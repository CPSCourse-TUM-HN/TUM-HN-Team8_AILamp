from __future__ import annotations

import csv
import errno
import fcntl
import hashlib
import inspect
import math
import re
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional


JOINT_NAMES: tuple[str, ...] = (
    "base_yaw",
    "base_pitch",
    "elbow_pitch",
    "wrist_roll",
    "wrist_pitch",
)
JOINT_POSITION_KEYS: tuple[str, ...] = tuple(f"{joint}.pos" for joint in JOINT_NAMES)
DEFAULT_JOINT_LIMITS_UNITS: dict[str, tuple[float, float]] = {
    joint: (-100.0, 100.0) for joint in JOINT_NAMES
}
DEFAULT_JOINT_LIMITS_DEG = DEFAULT_JOINT_LIMITS_UNITS
_RECORDING_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_FOLLOWER_MODULE = "lelamp.follower"
_MOTION_EPSILON = 1e-9
_PROCESS_LOCK_GUARD = threading.Lock()
_PROCESS_LOCKS: set[str] = set()


@dataclass(frozen=True)
class JointDeltaCommand:
    """Legacy `delta_deg` API whose values are normalized LeLamp `.pos` units."""

    joint: str
    delta_deg: float

    @property
    def delta_units(self) -> float:
        return self.delta_deg


class JointSafetyLimiter:
    def __init__(self, limits: dict[str, tuple[float, float]] | None = None):
        self.limits = limits or DEFAULT_JOINT_LIMITS_UNITS

    def apply(
        self,
        current_state: dict[str, float],
        deltas: list[JointDeltaCommand] | tuple[JointDeltaCommand, ...],
    ) -> dict[str, float]:
        target = dict(current_state)
        for command in deltas:
            if command.joint not in self.limits:
                raise ValueError(f"Unknown joint: {command.joint}")
            delta = _finite_float(command.delta_units, f"{command.joint} delta")
            low, high = self.limits[command.joint]
            _validate_limit(command.joint, low, high)
            key = f"{command.joint}.pos"
            if key not in target:
                raise ValueError(f"Missing current state for {key}")
            current = _finite_float(target[key], key)
            target[key] = max(low, min(high, current + delta))
        return target


class RecordingStore:
    def __init__(self, recordings_dir: str | Path):
        self.recordings_dir = Path(recordings_dir)

    def list_names(self) -> list[str]:
        if not self.recordings_dir.exists():
            return []
        names: list[str] = []
        for path in self.recordings_dir.glob("*.csv"):
            if _RECORDING_NAME_RE.fullmatch(path.stem) and self._is_inside_recordings_dir(path):
                names.append(path.stem)
        return sorted(names)

    def load(self, name: str) -> list[dict[str, float]]:
        if not isinstance(name, str):
            raise ValueError("Recording name must be a string")
        if not _RECORDING_NAME_RE.fullmatch(name):
            raise ValueError(f"Invalid recording name: {name}")
        path = self.recordings_dir / f"{name}.csv"
        if path.exists() and not self._is_inside_recordings_dir(path):
            raise ValueError(f"Recording path escapes recordings directory: {name}")
        if name not in self.list_names():
            raise FileNotFoundError(path)
        if not self._is_inside_recordings_dir(path):
            raise ValueError(f"Recording path escapes recordings directory: {name}")

        rows: list[dict[str, float]] = []
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            self._validate_fieldnames(name, reader.fieldnames)
            for index, row in enumerate(reader, start=1):
                rows.append(_validate_state(row, f"{name} row {index}", allow_timestamp=True))

        if not rows:
            raise ValueError(f"Recording {name} has empty data")
        return rows

    def _is_inside_recordings_dir(self, path: Path) -> bool:
        try:
            root = self.recordings_dir.resolve(strict=True)
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            return False
        return resolved == root or root in resolved.parents

    @staticmethod
    def _validate_fieldnames(name: str, fieldnames: Sequence[str] | None) -> None:
        if fieldnames is None:
            raise ValueError(f"Recording {name} is missing CSV header")
        if len(fieldnames) != len(set(fieldnames)):
            raise ValueError(f"Recording {name} has duplicate columns")
        fields = set(fieldnames)
        missing = set(JOINT_POSITION_KEYS) - fields
        if missing:
            raise ValueError(f"Recording {name} missing joint columns: {sorted(missing)}")
        allowed = set(JOINT_POSITION_KEYS) | {"timestamp"}
        unknown = fields - allowed
        if unknown:
            raise ValueError(f"Recording {name} has unknown columns: {sorted(unknown)}")


class MotorService:
    """Asynchronous ST3215 motor playback for the LeLamp follower arm.

    A process-level advisory ``fcntl.flock`` lock coordinates serial ownership
    between MotorService users. That kernel advisory lock cannot protect against
    original upstream LeLamp processes or other software that does not take the
    same lock.
    """

    def __init__(
        self,
        port: str,
        lamp_id: str,
        recordings_dir: str | Path,
        *,
        fps: int = 30,
        max_step_units: float = 4,
        robot_factory: Optional[Callable[[object], object]] = None,
        robot: Optional[object] = None,
        lock_dir: str | Path | None = None,
    ):
        self.fps = _finite_float(fps, "fps")
        if self.fps <= 0:
            raise ValueError("fps must be greater than zero")
        self.max_step_units = _finite_float(max_step_units, "max_step_units")
        if self.max_step_units <= 0:
            raise ValueError("max_step_units must be greater than zero")

        self.port = port
        self.lamp_id = lamp_id
        self.recordings = RecordingStore(recordings_dir)
        self.limiter = JointSafetyLimiter()
        self._robot_factory = robot_factory
        self._robot = robot
        self._owns_robot = robot is None
        self._lock_dir = Path(lock_dir) if lock_dir is not None else Path(tempfile.gettempdir())
        self._port_lock_file: TextIOWrapper | None = None
        self._port_lock_path: Path | None = None

        self._lifecycle_lock = threading.Lock()
        self._lock = threading.Condition(threading.RLock())
        self._worker: threading.Thread | None = None
        self._stop_worker = False
        self._closing = False
        self._command: _MotionCommand | _WorkerCall | None = None
        self._generation = 0
        self._active_generation: int | None = None
        self._sending = False
        self._last_error: BaseException | None = None
        self._error_blocked = False
        self._observed_state: dict[str, float] | None = None
        self._commanded_state: dict[str, float] | None = None
        self._position_source: str | None = None

    def connect(self, calibrate: bool = False) -> None:
        if calibrate:
            raise ValueError("MotorService requires an existing calibration; use calibrate=False")
        with self._lifecycle_lock:
            with self._lock:
                if self._closing:
                    raise RuntimeError("MotorService is closing")
                if self._worker is not None:
                    raise RuntimeError("MotorService is already connected")
                robot: object | None = None
                self._acquire_port_lock_locked()
                try:
                    robot = self._robot or self._build_robot()
                    _call_connect(robot)
                    if not _robot_has_existing_calibration(robot):
                        raise RuntimeError("MotorService requires an existing matching calibration")

                    observed = _read_observation(robot)
                    self._robot = robot
                    self._observed_state = observed.copy()
                    self._commanded_state = observed.copy()
                    self._position_source = "initial_observation"
                    self._last_error = None
                    self._error_blocked = False
                    self._closing = False
                    self._stop_worker = False
                    self._command = None
                    self._active_generation = None
                    self._sending = False
                    self._worker = threading.Thread(
                        target=self._run_worker,
                        name="AILampMotorWorker",
                        daemon=True,
                    )
                    self._worker.start()
                except BaseException as exc:
                    self._cleanup_failed_connect_locked(robot, exc)
                    raise

    def close(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                worker = self._worker
                if worker is None and self._port_lock_file is None:
                    return
                self._generation += 1
                self._cancel_pending_command_locked("MotorService is closing")
                self._active_generation = None
                self._closing = True
                self._stop_worker = worker is not None
                self._lock.notify_all()

            if worker is not None:
                worker.join(timeout=2.0)

            with self._lock:
                if worker is not None and worker.is_alive():
                    error = RuntimeError("MotorService worker did not stop; refusing to disconnect")
                    self._last_error = error
                    raise error
                self._worker = None
                robot = self._robot

            try:
                if robot is not None:
                    _call_optional(robot, "disconnect")
            except BaseException as exc:
                with self._lock:
                    self._last_error = exc
                    self._error_blocked = True
                    self._closing = True
                    self._stop_worker = False
                    self._lock.notify_all()
                raise

            with self._lock:
                self._command = None
                self._active_generation = None
                self._sending = False
                self._observed_state = None
                self._commanded_state = None
                self._position_source = None
                self._stop_worker = False
                self._closing = False
                self._last_error = None
                self._error_blocked = False
                if self._owns_robot:
                    self._robot = None
                self._release_port_lock_locked()
                self._lock.notify_all()

    def play(self, recording_name: str) -> None:
        with self._lock:
            self._require_ready_locked()
            self._generation += 1
            self._cancel_pending_command_locked("Motor command was replaced")
            generation = self._generation
        rows = self.recordings.load(recording_name)
        with self._lock:
            if (
                self._stop_worker
                or self._closing
                or self._generation != generation
                or self._worker is None
                or self._robot is None
            ):
                return
            self._require_ready_locked()
            self._command = _MotionCommand(frames=rows, deltas=None, expected_target=None)
            self._active_generation = None
            self._lock.notify_all()

    def play_frames(
        self,
        frames: Sequence[dict[str, Any]],
        *,
        preserve_source_frames: bool = True,
    ) -> None:
        """Schedule a fully validated frame sequence without replacing active work."""
        validated = [
            _validate_state(frame, f"motion frame {index}")
            for index, frame in enumerate(frames, start=1)
        ]
        if not validated:
            raise ValueError("Motion frame sequence must not be empty")
        with self._lock:
            self._require_ready_locked()
            if self._is_busy_locked():
                raise RuntimeError("MotorService is busy")
            self._generation += 1
            self._command = _MotionCommand(
                frames=validated,
                deltas=None,
                expected_target=validated[-1].copy(),
                preserve_source_frames=preserve_source_frames,
            )
            self._lock.notify_all()

    def execute_serialized(
        self,
        operation: Callable[[object], Any],
        *,
        cancel_motion: bool = False,
    ) -> Any:
        """Run one robot/bus operation on the sole motor worker and wait for it."""
        if not callable(operation):
            raise TypeError("operation must be callable")
        call = _WorkerCall(operation)
        with self._lock:
            self._require_ready_locked()
            if self._is_busy_locked() and not cancel_motion:
                raise RuntimeError("MotorService is busy")
            self._generation += 1
            self._cancel_pending_command_locked("Motor operation was cancelled")
            self._command = call
            self._lock.notify_all()

        call.done.wait()
        if call.error is not None:
            raise call.error
        return call.result

    def read_fresh_positions(self) -> dict[str, float]:
        """Read measured positions on the motor worker; never return the target cache."""
        return self.execute_serialized(lambda robot: _read_observation(robot))

    def stop_and_hold(self) -> dict[str, float]:
        """Cancel motion, freshly measure all joints, then hold that sample on the worker."""

        def hold(robot: object) -> _WorkerStateUpdate:
            hold_current = getattr(robot, "hold_current", None)
            if callable(hold_current):
                sample = hold_current()
                normalized = getattr(sample, "normalized_positions", None)
                measured = _validate_state(
                    normalized,
                    "fresh measured hold",
                )
                accepted = measured.copy()
            else:
                measured = _read_observation(robot)
                returned = getattr(robot, "send_action")(measured.copy())
                accepted = _validate_state(
                    returned,
                    "returned fresh hold target",
                    allow_extra=True,
                )
            return _WorkerStateUpdate(
                result=measured.copy(),
                commanded_state=accepted.copy(),
                position_source="fresh_measured_hold",
            )

        return self.execute_serialized(hold, cancel_motion=True)

    def apply_joint_deltas(
        self,
        deltas: list[JointDeltaCommand] | tuple[JointDeltaCommand, ...],
    ) -> dict[str, float]:
        if not deltas:
            return {}
        with self._lock:
            self._require_ready_locked()
            assert self._commanded_state is not None
            target = self.limiter.apply(self._commanded_state, deltas)
            self._generation += 1
            self._cancel_pending_command_locked("Motor command was replaced")
            self._command = _MotionCommand(frames=None, deltas=tuple(deltas), expected_target=None)
            self._active_generation = None
            self._lock.notify_all()
        return target

    def stop_motion(self) -> None:
        with self._lock:
            self._require_connected_locked()
            self._generation += 1
            self._cancel_pending_command_locked("Motor motion was stopped")
            self._active_generation = None
            self._lock.notify_all()
            while self._sending or self._command is not None or self._active_generation is not None:
                self._lock.wait()

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            self._require_connected_locked()
            while self._is_busy_locked():
                if deadline is None:
                    self._lock.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._lock.wait(remaining)
            return True

    def reset_error(self) -> None:
        with self._lock:
            self._require_connected_locked()
            raise RuntimeError(
                "MotorService send errors require close and reconnect before accepting commands"
            )

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._is_busy_locked()

    @property
    def last_error(self) -> BaseException | None:
        with self._lock:
            return self._last_error

    @property
    def current_state(self) -> dict[str, float]:
        """Return the initial observation, then the latest sent targets.

        This cache is not a real-time measured motor position stream.
        """
        return self.get_positions()

    def get_positions(self) -> dict[str, float]:
        """Return cached startup observation or latest sent targets, not live feedback."""
        with self._lock:
            if self._commanded_state is None:
                return {}
            return self._commanded_state.copy()

    @property
    def positions(self) -> dict[str, float]:
        """Alias for cached commanded positions; not live measured positions."""
        return self.get_positions()

    @property
    def position_source(self) -> str | None:
        with self._lock:
            return self._position_source

    @property
    def worker_thread_id(self) -> int | None:
        with self._lock:
            if self._worker is None:
                return None
            return self._worker.ident

    def _replace_command(self, command: "_MotionCommand") -> None:
        with self._lock:
            self._require_ready_locked()
            self._generation += 1
            self._cancel_pending_command_locked("Motor command was replaced")
            self._command = command
            self._active_generation = None
            self._lock.notify_all()

    def _require_connected_locked(self) -> None:
        if self._closing:
            raise RuntimeError("MotorService is closing")
        if self._worker is None or self._robot is None:
            raise RuntimeError("MotorService is not connected")

    def _require_ready_locked(self) -> None:
        self._require_connected_locked()
        if self._error_blocked:
            raise RuntimeError("MotorService is blocked by previous motor send error")

    def _is_busy_locked(self) -> bool:
        return self._command is not None or self._active_generation is not None or self._sending

    def _cancel_pending_command_locked(self, message: str) -> None:
        pending = self._command
        self._command = None
        if isinstance(pending, _WorkerCall):
            pending.error = RuntimeError(message)
            pending.done.set()

    def _build_robot(self) -> object:
        config_cls: type | None = None
        follower_cls: type | None = None
        if self._robot_factory is not None and hasattr(self._robot_factory, "Config"):
            config_cls = getattr(self._robot_factory, "Config")
        if self._robot_factory is None:
            follower_cls, config_cls = _import_public_follower()
        elif inspect.isclass(self._robot_factory):
            follower_cls = self._robot_factory
            config_cls = config_cls or getattr(follower_cls, "Config", None)

        config = _build_follower_config(
            config_cls,
            port=self.port,
            lamp_id=self.lamp_id,
            max_step_units=self.max_step_units,
        )
        if self._robot_factory is not None:
            return self._robot_factory(config)
        assert follower_cls is not None
        return follower_cls(config)

    def _port_lock_file_path(self) -> Path:
        canonical_port = str(Path(self.port).expanduser().resolve(strict=False))
        digest = hashlib.sha256(canonical_port.encode("utf-8")).hexdigest()
        lock_dir = self._lock_dir / "ailamp_motor_locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        return lock_dir / f"{digest}.lock"

    def _acquire_port_lock_locked(self) -> None:
        if self._port_lock_file is not None:
            return
        lock_path = self._port_lock_file_path()
        lock_key = str(lock_path)
        with _PROCESS_LOCK_GUARD:
            if lock_key in _PROCESS_LOCKS:
                raise RuntimeError(f"Serial port {self.port} is already owned by this process")
            handle = lock_path.open("a+")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise RuntimeError(
                        f"Serial port {self.port} is already owned by another MotorService process"
                    ) from exc
                raise
            _PROCESS_LOCKS.add(lock_key)
            self._port_lock_file = handle
            self._port_lock_path = lock_path

    def _release_port_lock_locked(self) -> None:
        handle = self._port_lock_file
        lock_path = self._port_lock_path
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            if lock_path is not None:
                with _PROCESS_LOCK_GUARD:
                    _PROCESS_LOCKS.discard(str(lock_path))
            self._port_lock_file = None
            self._port_lock_path = None

    def _cleanup_failed_connect_locked(
        self,
        robot: object | None,
        original_error: BaseException,
    ) -> None:
        cleanup_failed = False
        if robot is not None:
            try:
                _call_optional(robot, "disconnect")
            except BaseException as cleanup_error:
                original_error.add_note(
                    f"disconnect cleanup failed: {cleanup_error!r}"
                )
                cleanup_failed = True
        self._worker = None
        self._command = None
        self._active_generation = None
        self._sending = False
        self._observed_state = None
        self._commanded_state = None
        self._position_source = None
        self._stop_worker = False
        self._closing = cleanup_failed
        if cleanup_failed:
            self._robot = robot
        elif self._owns_robot:
            self._robot = None
        self._last_error = original_error
        self._error_blocked = True
        if not cleanup_failed:
            self._release_port_lock_locked()
        self._lock.notify_all()

    def _run_worker(self) -> None:
        while True:
            with self._lock:
                while not self._stop_worker and self._command is None:
                    self._lock.wait()
                if self._stop_worker:
                    self._active_generation = None
                    self._lock.notify_all()
                    return
                command = self._command
                generation = self._generation
                self._command = None
                self._active_generation = generation
                robot = self._robot
                current = self._commanded_state.copy() if self._commanded_state is not None else None

            assert command is not None
            assert robot is not None
            assert current is not None
            if isinstance(command, _WorkerCall):
                try:
                    result = command.operation(robot)
                    with self._lock:
                        if isinstance(result, _WorkerStateUpdate):
                            self._commanded_state = result.commanded_state.copy()
                            self._position_source = result.position_source
                            command.result = result.result
                        else:
                            command.result = result
                except BaseException as exc:
                    with self._lock:
                        self._last_error = exc
                        self._error_blocked = True
                        self._cancel_pending_command_locked(
                            "Motor operation was cancelled by a worker fault"
                        )
                        if self._active_generation == generation:
                            self._active_generation = None
                        command.error = exc
                        command.done.set()
                        self._lock.notify_all()
                else:
                    with self._lock:
                        if self._active_generation == generation:
                            self._active_generation = None
                        command.done.set()
                        self._lock.notify_all()
                continue
            try:
                current = self._execute_command(robot, current, command, generation)
            except BaseException as exc:
                with self._lock:
                    self._last_error = exc
                    self._error_blocked = True
                    self._cancel_pending_command_locked(
                        "Motor operation was cancelled by a motion fault"
                    )
                    if self._active_generation == generation:
                        self._active_generation = None
                    self._lock.notify_all()
            else:
                with self._lock:
                    if self._active_generation == generation:
                        self._active_generation = None
                    self._lock.notify_all()

    def _execute_command(
        self,
        robot: object,
        current: dict[str, float],
        command: "_MotionCommand",
        generation: int,
    ) -> dict[str, float]:
        frames = command.frames
        expected_target = command.expected_target
        if command.deltas is not None:
            target = self.limiter.apply(current, list(command.deltas))
            frames = [target]
            expected_target = target.copy()
        assert frames is not None

        for target in frames:
            target = _validate_state(target, "motion target")
            sent_for_frame = False
            while not _state_close(current, target):
                action = self._next_bounded_step(current, target)
                with self._lock:
                    if (
                        self._stop_worker
                        or self._generation != generation
                        or self._active_generation != generation
                    ):
                        return current
                    self._sending = True
                try:
                    returned = robot.send_action(action)
                    clipped = _validate_state(returned, "returned clipped action", allow_extra=True)
                except BaseException as exc:
                    with self._lock:
                        self._last_error = exc
                        self._error_blocked = True
                        self._cancel_pending_command_locked(
                            "Motor operation was cancelled by a send fault"
                        )
                        if self._active_generation == generation:
                            self._active_generation = None
                        self._sending = False
                        self._lock.notify_all()
                    raise

                previous = current
                sent_for_frame = True
                with self._lock:
                    self._commanded_state = clipped.copy()
                    self._position_source = "sent_targets"
                    current = clipped.copy()
                    self._sending = False
                    self._lock.notify_all()

                if not _state_close(current, target) and _state_close(current, previous):
                    raise RuntimeError(
                        "MotorService returned clipped action did not change toward target"
                    )

                with self._lock:
                    if (
                        self._stop_worker
                        or self._generation != generation
                        or self._active_generation != generation
                    ):
                        return current
                    if not self._wait_motion_period_locked(generation, 1 / self.fps):
                        return current
            if (
                not sent_for_frame
                and command.frames is not None
                and command.preserve_source_frames
            ):
                with self._lock:
                    if (
                        self._stop_worker
                        or self._generation != generation
                        or self._active_generation != generation
                    ):
                        return current
                    self._sending = True
                try:
                    returned = robot.send_action(target.copy())
                    clipped = _validate_state(
                        returned,
                        "returned repeated source frame",
                        allow_extra=True,
                    )
                except BaseException as exc:
                    with self._lock:
                        self._last_error = exc
                        self._error_blocked = True
                        self._cancel_pending_command_locked(
                            "Motor operation was cancelled by a send fault"
                        )
                        if self._active_generation == generation:
                            self._active_generation = None
                        self._sending = False
                        self._lock.notify_all()
                    raise
                with self._lock:
                    self._commanded_state = clipped.copy()
                    self._position_source = "sent_targets"
                    current = clipped.copy()
                    self._sending = False
                    self._lock.notify_all()
                sent_for_frame = True
                with self._lock:
                    if not self._wait_motion_period_locked(generation, 1 / self.fps):
                        return current
            if not sent_for_frame and command.frames is not None:
                with self._lock:
                    if not self._wait_motion_period_locked(generation, 1 / self.fps):
                        return current
        if expected_target is not None:
            _validate_state(current, "final commanded state")
        return current

    def _wait_motion_period_locked(self, generation: int, period: float) -> bool:
        deadline = time.monotonic() + period
        while True:
            if (
                self._stop_worker
                or self._generation != generation
                or self._active_generation != generation
            ):
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            self._lock.wait(remaining)

    def _next_bounded_step(
        self,
        current: dict[str, float],
        target: dict[str, float],
    ) -> dict[str, float]:
        action: dict[str, float] = {}
        for key in JOINT_POSITION_KEYS:
            delta = target[key] - current[key]
            if abs(delta) <= self.max_step_units + _MOTION_EPSILON:
                action[key] = _clamp_tiny(target[key])
                continue
            action[key] = _clamp_tiny(
                current[key] + math.copysign(self.max_step_units, delta)
            )
        return action


@dataclass(frozen=True)
class _MotionCommand:
    frames: list[dict[str, float]] | None
    deltas: tuple[JointDeltaCommand, ...] | None
    expected_target: dict[str, float] | None
    preserve_source_frames: bool = False


@dataclass(frozen=True)
class _WorkerStateUpdate:
    result: Any
    commanded_state: dict[str, float]
    position_source: str


class _WorkerCall:
    def __init__(self, operation: Callable[[object], Any]) -> None:
        self.operation = operation
        self.done = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None


def _import_public_follower() -> tuple[type, type]:
    try:
        module = __import__(_FOLLOWER_MODULE, fromlist=["LeLampFollower", "LeLampFollowerConfig"])
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Physical ST3215 playback needs the upstream LeLamp runtime with public "
            "`from lelamp.follower import LeLampFollower, LeLampFollowerConfig`. Clone "
            "https://github.com/humancomputerlab/lelamp_runtime next to AILamp and "
            "install it with `python3 -m pip install -e ../lelamp_runtime` on the Jetson."
        ) from exc
    follower_cls = getattr(module, "LeLampFollower", None)
    config_cls = getattr(module, "LeLampFollowerConfig", None)
    if follower_cls is None or config_cls is None:
        raise RuntimeError("lelamp.follower lacks LeLampFollower/LeLampFollowerConfig")
    return follower_cls, config_cls


def _build_follower_config(
    config_cls: type | None,
    *,
    port: str,
    lamp_id: str,
    max_step_units: float,
) -> object:
    kwargs = {
        "port": port,
        "id": lamp_id,
        "disable_torque_on_disconnect": False,
        "max_relative_target": max_step_units,
        "cameras": {},
        "use_degrees": False,
    }
    if config_cls is None:
        return SimpleNamespace(**kwargs)
    try:
        signature = inspect.signature(config_cls)
    except (TypeError, ValueError):
        return config_cls(**kwargs)
    accepted = {
        key: value
        for key, value in kwargs.items()
        if key in signature.parameters
        or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
    }
    return config_cls(**accepted)


def _call_connect(robot: object) -> None:
    connect = getattr(robot, "connect", None)
    if connect is None:
        return
    try:
        signature = inspect.signature(connect)
    except (TypeError, ValueError):
        connect(calibrate=False)
        return
    if "calibrate" in signature.parameters:
        connect(calibrate=False)
    else:
        connect()


def _call_optional(robot: object, method_name: str) -> None:
    method = getattr(robot, method_name, None)
    if callable(method):
        method()


def _robot_has_existing_calibration(robot: object) -> bool:
    if not hasattr(robot, "is_calibrated"):
        return False
    value = getattr(robot, "is_calibrated")
    return (value() if callable(value) else value) is True


def _read_observation(robot: object) -> dict[str, float]:
    for method_name in ("get_observation", "capture_observation", "read_observation", "observe"):
        method = getattr(robot, method_name, None)
        if callable(method):
            try:
                return _validate_state(method(), "initial observation", allow_extra=True)
            except ValueError as exc:
                raise RuntimeError(f"Invalid initial motor observation: {exc}") from exc
    raise RuntimeError("MotorService robot does not expose a public observation method")


def _validate_state(
    raw: dict[str, Any] | None,
    context: str,
    *,
    allow_timestamp: bool = False,
    allow_extra: bool = False,
) -> dict[str, float]:
    if raw is None:
        raise ValueError(f"{context} is missing")
    keys = set(raw)
    allowed = set(JOINT_POSITION_KEYS)
    if allow_timestamp:
        allowed.add("timestamp")
    missing = set(JOINT_POSITION_KEYS) - keys
    if missing:
        raise ValueError(f"{context} missing joint columns: {sorted(missing)}")
    unknown = keys - allowed
    if unknown and not allow_extra:
        raise ValueError(f"{context} has unknown columns: {sorted(unknown)}")
    return {key: _normalized_position(raw[key], f"{context} {key}") for key in JOINT_POSITION_KEYS}


def _normalized_position(value: Any, label: str) -> float:
    number = _finite_float(value, label)
    if number < -100.0 or number > 100.0:
        raise ValueError(f"{label} out of normalized range [-100, 100]: {number}")
    return number


def _finite_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _validate_limit(joint: str, low: float, high: float) -> None:
    low = _finite_float(low, f"{joint} low limit")
    high = _finite_float(high, f"{joint} high limit")
    if low > high:
        raise ValueError(f"{joint} low limit must be <= high limit")


def _state_close(
    left: dict[str, float],
    right: dict[str, float],
    *,
    epsilon: float = _MOTION_EPSILON,
) -> bool:
    return all(abs(left[key] - right[key]) <= epsilon for key in JOINT_POSITION_KEYS)


def _clamp_tiny(value: float) -> float:
    return 0.0 if abs(value) <= _MOTION_EPSILON else value
