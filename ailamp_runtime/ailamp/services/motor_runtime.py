from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ailamp.services.motor import (
    JOINT_NAMES,
    JOINT_POSITION_KEYS,
    MotorService,
    _WorkerStateUpdate,
)
from ailamp.services.motor_backend import (
    DEFAULT_ACCELERATION_LIMITS,
    DEFAULT_GOAL_VELOCITY_LIMITS,
    DEFAULT_TORQUE_LIMITS,
    EXPECTED_JOINT_IDS,
    PositionSample,
)


DEFAULT_SCRIPT: tuple[str, ...] = (
    "wake_up",
    "nod",
    "curious",
    "shy",
    "headshake",
    "scanning",
    "happy_wiggle",
    "home",
)

_TORQUE_VERIFIED_ALL_OFF = "verified_all_off"
_TORQUE_HOLDING = "holding"
_TORQUE_ENABLED = "enabled"
_TORQUE_UNKNOWN = "unknown"
_TORQUE_UNREPORTED = "unreported"
_MISSING = object()


@dataclass(frozen=True)
class MotorRuntimeEvent:
    kind: str
    task: str
    timestamp: str
    details: dict[str, Any]


class MotorDemoRuntime:
    """Owns one MotorService and runs complete demo jobs without a second sender."""

    def __init__(
        self,
        service: MotorService,
        *,
        calibration_sha256: str,
        home_path: str | Path,
        feedback_tolerance: float = 2.0,
        feedback_timeout: float = 3.0,
        feedback_poll_interval: float = 0.05,
        clock=time.monotonic,
        sleeper=time.sleep,
    ) -> None:
        self.service = service
        self.calibration_sha256 = _validate_sha256(calibration_sha256)
        self.home_path = Path(home_path)
        self.feedback_tolerance = _positive_finite(
            feedback_tolerance, "feedback_tolerance", allow_zero=True
        )
        self.feedback_timeout = _positive_finite(
            feedback_timeout, "feedback_timeout"
        )
        self.feedback_poll_interval = _positive_finite(
            feedback_poll_interval, "feedback_poll_interval"
        )
        self._clock = clock
        self._sleeper = sleeper

        self._condition = threading.Condition(threading.RLock())
        self._connected = False
        self._armed = False
        self._busy = False
        self._task: str | None = None
        self._generation = 0
        self._job_thread: threading.Thread | None = None
        self._fault: BaseException | None = None
        self._events: list[MotorRuntimeEvent] = []
        self._last_feedback: dict[str, float] | None = None
        self._connection_diagnostics: dict[str, Any] | None = None
        self._torque_state = _TORQUE_UNREPORTED

    def connect(self) -> dict[str, Any] | None:
        with self._condition:
            if self._connected:
                raise RuntimeError("Motor demo runtime is already connected")
        self.service.connect(calibrate=False)
        diagnostics, torque_state = self.service.execute_serialized(
            _inspect_connection_safety
        )
        with self._condition:
            self._connected = True
            self._armed = False
            self._torque_state = torque_state
            self._fault = None
            self._last_feedback = None
            self._connection_diagnostics = diagnostics
            self._condition.notify_all()
        return None if diagnostics is None else diagnostics.copy()

    def arm(
        self,
        *,
        goal_velocity: int,
        acceleration: int,
        torque_limit: int,
        operator_confirmed: bool,
    ) -> PositionSample:
        if operator_confirmed is not True:
            raise ValueError("arm requires explicit operator confirmation of physical support")
        velocity = _bounded_int(
            goal_velocity,
            "goal_velocity",
            DEFAULT_GOAL_VELOCITY_LIMITS,
        )
        accel = _bounded_int(
            acceleration,
            "acceleration",
            DEFAULT_ACCELERATION_LIMITS,
        )
        torque = _bounded_int(
            torque_limit,
            "torque_limit",
            DEFAULT_TORQUE_LIMITS,
        )
        with self._condition:
            self._require_available_locked(require_armed=False)
            if self._armed:
                raise RuntimeError("Motor demo runtime is already armed")
            self._record_event_locked(
                "operator_confirmed",
                "arm",
                {"physical_support": True},
            )
        failed_torque_state: list[str] = []

        def arm_on_worker(robot: object) -> _WorkerStateUpdate:
            try:
                return _call_arm_and_sync_commanded_state(
                    robot,
                    goal_velocity=velocity,
                    acceleration=accel,
                    torque_limit=torque,
                )
            except BaseException:
                failed_torque_state.append(_inspect_failed_arm_safety(robot))
                raise

        try:
            sample = self.service.execute_serialized(arm_on_worker)
        except BaseException as exc:
            with self._condition:
                self._torque_state = failed_torque_state[-1] if failed_torque_state else _TORQUE_UNKNOWN
            self._latch_fault(exc, "arm")
            raise
        if not isinstance(sample, PositionSample):
            raise RuntimeError("Motor backend arm did not return a fresh position sample")
        with self._condition:
            self._armed = True
            self._torque_state = _TORQUE_HOLDING
            self._last_feedback = sample.normalized_positions.copy()
            self._record_event_locked(
                "sent",
                "arm",
                {
                    "goal_velocity": velocity,
                    "acceleration": accel,
                    "torque_limit": torque,
                    "hold_target": sample.normalized_positions.copy(),
                },
            )
            self._condition.notify_all()
        return sample

    def play(self, recording_name: str) -> None:
        with self._condition:
            self._require_available_locked()
            rows = self.service.recordings.load(recording_name)
            self._start_job_locked([(recording_name, rows)], recording_name)

    def play_script(
        self,
        sequence: Sequence[str] | None = None,
        *,
        include_home: bool = True,
    ) -> None:
        with self._condition:
            self._require_available_locked()
            if sequence is None:
                steps = list(DEFAULT_SCRIPT)
            else:
                steps = list(sequence)
                if not steps:
                    raise ValueError("script must contain at least one step")
                if include_home and (not steps or steps[-1] != "home"):
                    steps.append("home")

            segments: list[tuple[str, list[dict[str, float]]]] = []
            for step in steps:
                if step == "home":
                    home = self._load_home()
                    segments.append(("home", [home["normalized_positions"]]))
                else:
                    # Every recording is loaded before _start_job_locked.  A bad
                    # late scene can therefore never permit an earlier write.
                    segments.append((step, self.service.recordings.load(step)))
            self._start_job_locked(segments, "script")

    def home(self) -> None:
        with self._condition:
            self._require_available_locked()
            home = self._load_home()
            self._start_job_locked(
                [("home", [home["normalized_positions"]])],
                "home",
            )

    def stop(self) -> dict[str, float]:
        with self._condition:
            self._require_connected_locked()
            self._require_no_fault_locked()
            if not self._armed:
                raise RuntimeError("Motor demo runtime is not armed")
            self._generation += 1
            generation = self._generation
            self._busy = True
            self._task = "stop"
            self._condition.notify_all()
        try:
            measured = self.service.stop_and_hold()
            self._record_event(
                "sent",
                "stop",
                {"fresh_hold_target": measured.copy()},
            )
            feedback = self._wait_for_feedback(measured, generation)
            if feedback is None:
                raise RuntimeError("stop was superseded before hold confirmation")
            self._record_event(
                "feedback_reached",
                "stop",
                {"actual": feedback.copy()},
            )
        except BaseException as exc:
            self._latch_fault(exc, "stop", generation=generation)
            raise
        finally:
            with self._condition:
                if self._generation == generation:
                    self._busy = False
                    self._task = None
                self._condition.notify_all()
        return measured

    def capture_home(self, *, operator_confirmed: bool) -> dict[str, Any]:
        if operator_confirmed is not True:
            raise ValueError("capture-home requires explicit operator confirmation")
        with self._condition:
            self._require_available_locked()
            self._record_event_locked(
                "operator_confirmed",
                "capture-home",
                {"pose_is_home": True},
            )
        try:
            sample = self.service.execute_serialized(_read_position_sample)
        except BaseException as exc:
            self._latch_fault(exc, "capture-home")
            raise
        if not isinstance(sample, PositionSample):
            raise RuntimeError("Motor backend did not return raw and normalized positions")
        normalized = _validate_positions(
            sample.normalized_positions,
            "captured home normalized_positions",
        )
        raw = _validate_raw_positions(sample.raw_positions)
        payload: dict[str, Any] = {
            "schema_version": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "robot_id": self.service.lamp_id,
            "calibration_sha256": self.calibration_sha256,
            "joint_mapping": EXPECTED_JOINT_IDS.copy(),
            "units": {
                "raw_positions": "servo_raw",
                "normalized_positions": "LeLamp [-100,100]",
            },
            "raw_positions": raw,
            "normalized_positions": normalized,
            "operator_confirmed": True,
        }
        _atomic_write_json(self.home_path, payload)
        with self._condition:
            self._last_feedback = normalized.copy()
        return payload

    def release(self, *, operator_confirmed: bool) -> None:
        if operator_confirmed is not True:
            raise ValueError("release requires explicit operator confirmation of physical support")
        with self._condition:
            self._require_available_locked()
            if not self._armed:
                raise RuntimeError("Motor demo runtime is not armed")
            self._record_event_locked(
                "operator_confirmed",
                "release",
                {"physical_support": True},
            )
        try:
            self.service.execute_serialized(_call_release)
        except BaseException as exc:
            self._latch_fault(exc, "release")
            raise
        with self._condition:
            self._armed = False
            self._torque_state = _TORQUE_VERIFIED_ALL_OFF
            self._record_event_locked("released", "release", {})
            self._condition.notify_all()

    def confirm_visible(self, task: str, note: str = "") -> None:
        with self._condition:
            self._require_connected_locked()
            self._record_event_locked(
                "operator_confirmed",
                task,
                {"visible": True, "note": note},
            )

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else self._clock() + timeout
        with self._condition:
            while self._busy:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def status(self) -> dict[str, Any]:
        with self._condition:
            return {
                "connected": self._connected,
                "armed": self._armed,
                "torque_state": self._torque_state,
                "busy": self._busy,
                "task": self._task,
                "fault": None if self._fault is None else str(self._fault),
                "position_source": self.service.position_source,
                "commanded_positions": self.service.positions,
                "last_feedback": None
                if self._last_feedback is None
                else self._last_feedback.copy(),
                "connection_diagnostics": None
                if self._connection_diagnostics is None
                else self._connection_diagnostics.copy(),
            }

    def close(self, *, leave_holding: bool = False) -> str:
        with self._condition:
            if not self._connected:
                return ""
            if self._busy:
                raise RuntimeError("Motor demo runtime is busy; stop it before quit")
            torque_risk = self._torque_may_remain_enabled_locked()
            if torque_risk and not leave_holding:
                if self._armed:
                    raise RuntimeError(
                        "Motor demo runtime is armed; release or explicitly leave holding"
                    )
                raise RuntimeError(
                    "Motor torque may still be enabled; explicitly leave holding"
                )
            warning = self._close_warning_locked() if torque_risk else ""
        self.service.close()
        with self._condition:
            self._connected = False
            self._connection_diagnostics = None
            self._condition.notify_all()
        return warning

    def shutdown_for_eof_or_signal(self, reason: str) -> str:
        messages: list[str] = [f"{reason}: cancelling motion"]
        with self._condition:
            connected = self._connected
            armed = self._armed
            healthy = self._fault is None and self.service.last_error is None
        if connected and armed and healthy:
            try:
                self.stop()
                messages.append("fresh stop-and-hold confirmed")
            except BaseException as exc:
                messages.append(f"stop-and-hold failed: {exc}")
        if connected:
            try:
                with self._condition:
                    leave_holding = self._torque_may_remain_enabled_locked()
                warning = self.close(leave_holding=leave_holding)
                if warning:
                    messages.append(warning)
            except BaseException as exc:
                messages.append(f"close failed: {exc}")
        return "; ".join(messages)

    @property
    def events(self) -> tuple[MotorRuntimeEvent, ...]:
        with self._condition:
            return tuple(self._events)

    @property
    def fault(self) -> BaseException | None:
        with self._condition:
            return self._fault

    @property
    def is_busy(self) -> bool:
        with self._condition:
            return self._busy

    @property
    def is_armed(self) -> bool:
        with self._condition:
            return self._armed

    @property
    def is_connected(self) -> bool:
        with self._condition:
            return self._connected

    def _start_job_locked(
        self,
        segments: list[tuple[str, list[dict[str, float]]]],
        task: str,
    ) -> None:
        if not segments:
            raise ValueError("motor job must contain at least one segment")
        # Copy complete validated frames before publishing the job.
        prepared = [
            (
                name,
                [
                    _validate_positions(frame, f"{name} frame {index}")
                    for index, frame in enumerate(frames, start=1)
                ],
            )
            for name, frames in segments
        ]
        if any(not frames for _, frames in prepared):
            raise ValueError("motor job contains an empty segment")
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._task = task
        worker = threading.Thread(
            target=self._run_segments,
            args=(prepared, generation),
            name="AILampMotorDemoJob",
            daemon=True,
        )
        self._job_thread = worker
        worker.start()
        self._condition.notify_all()

    def _run_segments(
        self,
        segments: list[tuple[str, list[dict[str, float]]]],
        generation: int,
    ) -> None:
        try:
            for name, frames in segments:
                with self._condition:
                    if self._generation != generation:
                        return
                    self.service.play_frames(frames, preserve_source_frames=True)
                self.service.wait_until_idle()
                if not self._generation_is_current(generation):
                    return
                if self.service.last_error is not None:
                    raise self.service.last_error
                self._record_event(
                    "sent",
                    name,
                    {
                        "source_frames": len(frames),
                        "target": frames[-1].copy(),
                        "trigger": "simulated" if name != "home" else "home",
                    },
                )
                feedback = self._wait_for_feedback(frames[-1], generation)
                if feedback is None:
                    return
                self._record_event(
                    "feedback_reached",
                    name,
                    {"actual": feedback.copy()},
                )
        except BaseException as exc:
            if self._generation_is_current(generation):
                self._latch_fault(exc, self._task or "motor-job", generation=generation)
        finally:
            with self._condition:
                if self._generation == generation:
                    self._busy = False
                    self._task = None
                    self._job_thread = None
                self._condition.notify_all()

    def _wait_for_feedback(
        self,
        target: dict[str, float],
        generation: int,
    ) -> dict[str, float] | None:
        deadline = self._clock() + self.feedback_timeout
        while self._generation_is_current(generation):
            actual = self.service.read_fresh_positions()
            with self._condition:
                self._last_feedback = actual.copy()
            if _positions_reached(actual, target, self.feedback_tolerance):
                return actual
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise TimeoutError(
                    f"fresh motor feedback did not reach target within "
                    f"{self.feedback_timeout:g}s"
                )
            self._sleeper(min(self.feedback_poll_interval, remaining))
        return None

    def _load_home(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.home_path.read_text())
        except FileNotFoundError as exc:
            raise RuntimeError("home pose has not been captured") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("home pose file is unreadable") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("home pose file must contain an object")
        if payload.get("calibration_sha256") != self.calibration_sha256:
            raise RuntimeError("home pose calibration SHA-256 does not match")
        if payload.get("robot_id") != self.service.lamp_id:
            raise RuntimeError("home pose robot ID does not match")
        if payload.get("joint_mapping") != EXPECTED_JOINT_IDS:
            raise RuntimeError("home pose joint mapping does not match")
        if payload.get("operator_confirmed") is not True:
            raise RuntimeError("home pose lacks operator confirmation")
        payload["normalized_positions"] = _validate_positions(
            payload.get("normalized_positions"),
            "home normalized_positions",
        )
        payload["raw_positions"] = _validate_raw_positions(
            payload.get("raw_positions")
        )
        return payload

    def _generation_is_current(self, generation: int) -> bool:
        with self._condition:
            return self._generation == generation

    def _require_available_locked(self, *, require_armed: bool = True) -> None:
        self._require_connected_locked()
        self._require_no_fault_locked()
        if self._busy:
            raise RuntimeError("Motor demo runtime is busy")
        if require_armed and not self._armed:
            raise RuntimeError("Motor demo runtime is not armed")

    def _require_connected_locked(self) -> None:
        if not self._connected:
            raise RuntimeError("Motor demo runtime is not connected")

    def _require_no_fault_locked(self) -> None:
        if self._fault is not None or self.service.last_error is not None:
            raise RuntimeError("Motor demo runtime is blocked by a latched fault")

    def _torque_may_remain_enabled_locked(self) -> bool:
        return self._armed or self._torque_state in {
            _TORQUE_HOLDING,
            _TORQUE_ENABLED,
            _TORQUE_UNKNOWN,
        }

    def _close_warning_locked(self) -> str:
        if self._torque_state == _TORQUE_UNKNOWN:
            return "警告：电机扭矩状态未知；退出后程序不再监测。"
        if self._torque_state == _TORQUE_ENABLED and not self._armed:
            return "警告：诊断显示电机扭矩仍为使能；退出后程序不再监测。"
        return "警告：退出后保持使能，但程序不再监测。"

    def _latch_fault(
        self,
        exc: BaseException,
        task: str,
        *,
        generation: int | None = None,
    ) -> None:
        with self._condition:
            if generation is not None and self._generation != generation:
                return
            if self._fault is None:
                self._fault = exc
                self._record_event_locked("fault", task, {"error": str(exc)})
            self._condition.notify_all()

    def _record_event(
        self,
        kind: str,
        task: str,
        details: dict[str, Any],
    ) -> None:
        with self._condition:
            self._record_event_locked(kind, task, details)

    def _record_event_locked(
        self,
        kind: str,
        task: str,
        details: dict[str, Any],
    ) -> None:
        self._events.append(
            MotorRuntimeEvent(
                kind=kind,
                task=task,
                timestamp=datetime.now(timezone.utc).isoformat(),
                details=details,
            )
        )


def _call_arm(
    robot: object,
    *,
    goal_velocity: int,
    acceleration: int,
    torque_limit: int,
) -> PositionSample:
    method = getattr(robot, "arm", None)
    if not callable(method):
        raise RuntimeError("Motor backend does not implement arm")
    return method(
        goal_velocity=goal_velocity,
        acceleration=acceleration,
        torque_limit=torque_limit,
    )


def _call_arm_and_sync_commanded_state(
    robot: object,
    *,
    goal_velocity: int,
    acceleration: int,
    torque_limit: int,
) -> _WorkerStateUpdate:
    sample = _call_arm(
        robot,
        goal_velocity=goal_velocity,
        acceleration=acceleration,
        torque_limit=torque_limit,
    )
    if not isinstance(sample, PositionSample):
        raise RuntimeError("Motor backend arm did not return a fresh position sample")
    commanded = _validate_positions(
        sample.normalized_positions,
        "arm position sample",
    )
    return _WorkerStateUpdate(
        result=sample,
        commanded_state=commanded,
        position_source="armed_position_sample",
    )


def _call_release(robot: object) -> None:
    method = getattr(robot, "release", None)
    if not callable(method):
        raise RuntimeError("Motor backend does not implement release")
    method()


def _read_position_sample(robot: object) -> PositionSample:
    method = getattr(robot, "read_position_sample", None)
    if not callable(method):
        raise RuntimeError("Motor backend does not expose raw position samples")
    return method()


def _validate_positions(raw: Any, context: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError(f"{context} must be a mapping")
    missing = set(JOINT_POSITION_KEYS) - set(raw)
    extra = set(raw) - set(JOINT_POSITION_KEYS)
    if missing or extra:
        raise ValueError(
            f"{context} must contain exactly five joints; "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    result: dict[str, float] = {}
    for key in JOINT_POSITION_KEYS:
        try:
            value = float(raw[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{context} {key} must be finite") from exc
        if not math.isfinite(value):
            raise ValueError(f"{context} {key} must be finite")
        if value < -100.0 or value > 100.0:
            raise ValueError(f"{context} {key} is outside [-100, 100]")
        result[key] = value
    return result


def _validate_raw_positions(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict) or set(raw) != set(JOINT_NAMES):
        raise RuntimeError("home raw_positions must contain exactly five joints")
    result: dict[str, int] = {}
    for joint in JOINT_NAMES:
        value = raw[joint]
        if isinstance(value, bool) or not isinstance(value, int):
            raise RuntimeError(f"home raw position for {joint} must be an integer")
        result[joint] = value
    return result


def _positions_reached(
    actual: dict[str, float],
    target: dict[str, float],
    tolerance: float,
) -> bool:
    return all(
        abs(actual[key] - target[key]) <= tolerance for key in JOINT_POSITION_KEYS
    )


def _inspect_connection_safety(
    robot: object,
) -> tuple[dict[str, Any] | None, str]:
    try:
        value = getattr(robot, "diagnostics", _MISSING)
    except BaseException:
        return None, _TORQUE_UNKNOWN
    if value is _MISSING:
        return None, _TORQUE_UNREPORTED
    try:
        diagnostics = _serialize_connection_diagnostics(value)
        torque_state = _torque_state_from_diagnostics(value)
        backend_fault = getattr(robot, "fault", None)
        backend_armed = getattr(robot, "is_armed", False)
    except BaseException:
        return None, _TORQUE_UNKNOWN
    if backend_fault is not None:
        torque_state = _TORQUE_UNKNOWN
    elif backend_armed is True:
        torque_state = (
            _TORQUE_ENABLED
            if torque_state == _TORQUE_ENABLED
            else _TORQUE_UNKNOWN
        )
    return diagnostics, torque_state


def _inspect_failed_arm_safety(robot: object) -> str:
    try:
        value = getattr(robot, "diagnostics", _MISSING)
    except BaseException:
        return _TORQUE_UNKNOWN
    if value is _MISSING:
        return _TORQUE_UNREPORTED
    try:
        torque_state = _torque_state_from_diagnostics(value)
        backend_fault = getattr(robot, "fault", None)
        backend_armed = getattr(robot, "is_armed", False)
    except BaseException:
        return _TORQUE_UNKNOWN
    if backend_fault is not None or backend_armed is True:
        return _TORQUE_UNKNOWN
    return torque_state


def _torque_state_from_diagnostics(value: Any) -> str:
    if value is None:
        return _TORQUE_UNKNOWN
    if isinstance(value, dict):
        torque_enable = value.get("torque_enable", _MISSING)
    else:
        torque_enable = getattr(value, "torque_enable", _MISSING)
    if not isinstance(torque_enable, dict):
        return _TORQUE_UNKNOWN
    if set(torque_enable) != set(JOINT_NAMES):
        return _TORQUE_UNKNOWN
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in torque_enable.values()
    ):
        return _TORQUE_UNKNOWN
    if all(value == 0 for value in torque_enable.values()):
        return _TORQUE_VERIFIED_ALL_OFF
    return _TORQUE_ENABLED


def _serialize_connection_diagnostics(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.copy()
    fields = (
        "raw_positions",
        "normalized_positions",
        "torque_enable",
        "operating_mode",
        "issues",
        "can_arm",
    )
    result: dict[str, Any] = {}
    for field in fields:
        if not hasattr(value, field):
            continue
        item = getattr(value, field)
        if isinstance(item, dict):
            item = item.copy()
        elif isinstance(item, tuple):
            item = list(item)
        result[field] = item
    return result


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass
        raise


def _positive_finite(value: Any, label: str, *, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if number < 0 or (number == 0 and not allow_zero):
        relation = "non-negative" if allow_zero else "greater than zero"
        raise ValueError(f"{label} must be {relation}")
    return number


def _validate_sha256(value: str) -> str:
    normalized = str(value).lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("calibration_sha256 must be a 64-character hexadecimal digest")
    return normalized


def _bounded_int(value: Any, label: str, limits: tuple[int, int]) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if number != value:
        raise ValueError(f"{label} must be an integer")
    low, high = limits
    if number < low or number > high:
        raise ValueError(f"{label} must be in [{low}, {high}]")
    return number
