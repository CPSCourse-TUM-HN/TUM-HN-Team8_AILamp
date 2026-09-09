from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ailamp.services.motor import JOINT_NAMES, JOINT_POSITION_KEYS


POSITION_MODE = 0
DEFAULT_GOAL_VELOCITY_LIMITS = (1, 3400)
DEFAULT_ACCELERATION_LIMITS = (1, 254)
DEFAULT_TORQUE_LIMITS = (1, 1000)
EXPECTED_JOINT_IDS = {
    joint: motor_id for motor_id, joint in enumerate(JOINT_NAMES, start=1)
}


@dataclass(frozen=True)
class JointCalibration:
    motor_id: int
    drive_mode: int
    homing_offset: int
    range_min: int
    range_max: int


@dataclass(frozen=True)
class PositionSample:
    raw_positions: dict[str, int]
    normalized_positions: dict[str, float]


@dataclass(frozen=True)
class ConnectionDiagnostics:
    raw_positions: dict[str, int]
    normalized_positions: dict[str, float]
    torque_enable: dict[str, int] | None
    operating_mode: dict[str, int] | None
    issues: tuple[str, ...]

    @property
    def can_arm(self) -> bool:
        return (
            not self.issues
            and self.torque_enable is not None
            and self.operating_mode is not None
            and all(value == 0 for value in self.torque_enable.values())
            and all(value == POSITION_MODE for value in self.operating_mode.values())
        )


class LeLampMotorBackend:
    """Narrow, motor-only adapter around the LeLamp follower bus.

    Construction may use the upstream follower to obtain its configured bus, but
    connection deliberately calls only ``bus.connect()``.  All positions sent to
    or returned from MotorService use LeLamp normalized ``[-100, 100]`` units;
    conversion to raw servo values is performed here without clipping.
    """

    def __init__(
        self,
        port: str,
        calibration_dir: str | Path,
        *,
        expected_calibration_sha256: str,
        lamp_id: str = "lelamp",
        bus: object | None = None,
        follower_factory: type | None = None,
        config_factory: type | None = None,
    ) -> None:
        if lamp_id != "lelamp":
            raise ValueError("LeLampMotorBackend requires lamp_id='lelamp'")
        self.port = str(port)
        self.lamp_id = lamp_id
        self.calibration_dir = Path(calibration_dir)
        self.expected_calibration_sha256 = expected_calibration_sha256.lower()
        self._bus = bus
        self._follower_factory = follower_factory
        self._config_factory = config_factory
        self._follower: object | None = None
        self._calibration: dict[str, JointCalibration] | None = None
        self._calibration_sha256: str | None = None
        self._connected = False
        self._disconnect_pending = False
        self._armed = False
        self._fault: BaseException | None = None
        self._diagnostics: ConnectionDiagnostics | None = None

    @property
    def is_calibrated(self) -> bool:
        return self._connected and self._calibration is not None

    @property
    def is_armed(self) -> bool:
        return self._armed

    @property
    def fault(self) -> BaseException | None:
        return self._fault

    @property
    def calibration_sha256(self) -> str:
        if self._calibration_sha256 is None:
            raise RuntimeError("Motor backend calibration has not been verified")
        return self._calibration_sha256

    @property
    def joint_ids(self) -> dict[str, int]:
        calibration = self._require_calibration()
        return {joint: entry.motor_id for joint, entry in calibration.items()}

    @property
    def diagnostics(self) -> ConnectionDiagnostics | None:
        return self._diagnostics

    def connect(self, calibrate: bool = False) -> ConnectionDiagnostics:
        if calibrate:
            raise ValueError("LeLampMotorBackend never calibrates during connect")
        if self._connected:
            raise RuntimeError("Motor backend is already connected")
        if self._disconnect_pending:
            raise RuntimeError("Motor backend disconnect cleanup is still pending")

        calibration, digest = _load_calibration(
            self.calibration_dir,
            self.expected_calibration_sha256,
        )
        self._calibration = calibration
        self._calibration_sha256 = digest
        bus = self._bus or self._construct_upstream_bus()
        self._bus = bus

        try:
            connect = getattr(bus, "connect")
            # A connect call may open the port before raising, so cleanup is
            # required from the moment the call begins.
            self._disconnect_pending = True
            connect()
            if getattr(bus, "is_calibrated", None) is not True:
                raise RuntimeError("LeLamp bus does not report the configured calibration")

            sample = self._read_position_sample_unchecked()
            issues: list[str] = []
            torque = self._diagnostic_read("Torque_Enable", issues)
            modes = self._diagnostic_read("Operating_Mode", issues)
            diagnostics = ConnectionDiagnostics(
                raw_positions=sample.raw_positions,
                normalized_positions=sample.normalized_positions,
                torque_enable=torque,
                operating_mode=modes,
                issues=tuple(issues),
            )
            self._diagnostics = diagnostics
            self._connected = True
            self._disconnect_pending = False
            self._fault = None
            return diagnostics
        except BaseException as exc:
            self._connected = False
            self._diagnostics = None
            self._fault = exc
            if self._disconnect_pending:
                try:
                    getattr(bus, "disconnect")(disable_torque=False)
                except BaseException as cleanup_error:
                    exc.add_note(
                        f"disconnect cleanup failed: {cleanup_error!r}"
                    )
                else:
                    self._disconnect_pending = False
            raise

    def disconnect(self) -> None:
        if not self._connected and not self._disconnect_pending:
            return
        assert self._bus is not None
        try:
            getattr(self._bus, "disconnect")(disable_torque=False)
        except BaseException as exc:
            self._connected = False
            self._disconnect_pending = True
            self._diagnostics = None
            self._latch_fault(exc)
            raise
        self._connected = False
        self._disconnect_pending = False
        self._diagnostics = None
        # Do not clear _armed: when disconnecting while holding, torque may
        # remain enabled and the process can no longer monitor that state.

    def get_observation(self) -> dict[str, float]:
        return self.read_position_sample().normalized_positions

    def read_position_sample(self) -> PositionSample:
        self._require_healthy_connection()
        try:
            return self._read_position_sample_unchecked()
        except BaseException as exc:
            self._latch_fault(exc)
            raise

    def arm(
        self,
        *,
        goal_velocity: int,
        acceleration: int,
        torque_limit: int,
    ) -> PositionSample:
        self._require_healthy_connection()
        velocity = _bounded_int(
            goal_velocity, "goal_velocity", DEFAULT_GOAL_VELOCITY_LIMITS
        )
        accel = _bounded_int(
            acceleration, "acceleration", DEFAULT_ACCELERATION_LIMITS
        )
        torque_limit_value = _bounded_int(
            torque_limit, "torque_limit", DEFAULT_TORQUE_LIMITS
        )

        torque = self._strict_read("Torque_Enable")
        if any(value != 0 for value in torque.values()):
            raise RuntimeError(
                "Refusing arm because one or more motors are already enabled"
            )
        modes = self._strict_read("Operating_Mode")
        if any(value != POSITION_MODE for value in modes.values()):
            raise RuntimeError("Refusing arm because all motors must be in position mode 0")
        sample = self.read_position_sample()

        try:
            self._write_all("Goal_Velocity", velocity)
            self._write_all("Acceleration", accel)
            self._write_all("Torque_Limit", torque_limit_value)
            # Goal_Position may enable torque before a transport error is
            # reported, so conservatively retain the armed state from here.
            self._armed = True
            self._write_raw("Goal_Position", sample.raw_positions)
            self._write_all("Torque_Enable", 1)
            enabled = self._strict_read("Torque_Enable")
            if any(value != 1 for value in enabled.values()):
                raise RuntimeError("Arm verification failed: torque enable state is unknown")
        except BaseException as exc:
            self._latch_fault(exc)
            raise

        self._armed = True
        return sample

    def send_action(self, action: dict[str, Any]) -> dict[str, float]:
        self._require_healthy_connection()
        if not self._armed:
            raise RuntimeError("Motor backend is not armed")
        target = _validate_normalized_target(action)
        raw = {
            joint: self.normalized_to_raw(joint, target[f"{joint}.pos"])
            for joint in JOINT_NAMES
        }
        try:
            self._write_raw("Goal_Position", raw)
        except BaseException as exc:
            self._latch_fault(exc)
            raise
        # MotorService needs the complete accepted target, never a raw-register
        # dictionary or transport acknowledgement.  Actual feedback is separate.
        return target.copy()

    def hold_current(self) -> PositionSample:
        sample = self.read_position_sample()
        self.send_action(sample.normalized_positions)
        return sample

    def release(self) -> None:
        self._require_healthy_connection()
        if not self._armed:
            raise RuntimeError("Motor backend is not armed")
        try:
            self._write_all("Torque_Enable", 0)
            released = self._strict_read("Torque_Enable")
            if any(value != 0 for value in released.values()):
                raise RuntimeError("Release verification failed: torque remains enabled")
        except BaseException as exc:
            self._latch_fault(exc)
            raise
        self._armed = False

    def raw_to_normalized(self, joint: str, raw_value: Any) -> float:
        entry = self._require_joint(joint)
        raw = _finite_number(raw_value, f"{joint} raw position")
        if raw < entry.range_min or raw > entry.range_max:
            raise RuntimeError(
                f"{joint} raw position {raw} is outside calibrated raw range "
                f"[{entry.range_min}, {entry.range_max}]"
            )
        return ((raw - entry.range_min) * 200.0 / (entry.range_max - entry.range_min)) - 100.0

    def normalized_to_raw(self, joint: str, normalized_value: Any) -> int:
        entry = self._require_joint(joint)
        normalized = _finite_number(
            normalized_value, f"{joint} normalized position"
        )
        if normalized < -100.0 or normalized > 100.0:
            raise ValueError(
                f"{joint} normalized position out of normalized range [-100, 100]: "
                f"{normalized}"
            )
        raw = entry.range_min + ((normalized + 100.0) / 200.0) * (
            entry.range_max - entry.range_min
        )
        rounded = int(round(raw))
        if rounded < entry.range_min or rounded > entry.range_max:
            raise ValueError(f"{joint} conversion produced an out-of-range raw target")
        return rounded

    def _construct_upstream_bus(self) -> object:
        follower_factory = self._follower_factory
        config_factory = self._config_factory
        if follower_factory is None or config_factory is None:
            try:
                module = __import__(
                    "lelamp.follower",
                    fromlist=["LeLampFollower", "LeLampFollowerConfig"],
                )
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "Physical motor access requires the upstream LeLamp runtime"
                ) from exc
            follower_factory = follower_factory or getattr(
                module, "LeLampFollower", None
            )
            config_factory = config_factory or getattr(
                module, "LeLampFollowerConfig", None
            )
        if follower_factory is None or config_factory is None:
            raise RuntimeError("lelamp.follower lacks its public follower classes")

        config = config_factory(
            port=self.port,
            id=self.lamp_id,
            calibration_dir=self.calibration_dir,
            cameras={},
            use_degrees=False,
            disable_torque_on_disconnect=False,
            max_relative_target=None,
        )
        follower = follower_factory(config)
        bus = getattr(follower, "bus", None)
        if bus is None:
            raise RuntimeError("LeLamp follower does not expose its configured bus")
        self._follower = follower
        return bus

    def _read_position_sample_unchecked(self) -> PositionSample:
        raw_values = self._strict_read("Present_Position")
        raw_positions: dict[str, int] = {}
        normalized: dict[str, float] = {}
        for joint in JOINT_NAMES:
            raw_number = _finite_number(raw_values[joint], f"{joint} raw position")
            if not raw_number.is_integer():
                raise RuntimeError(f"{joint} raw position must be an integer")
            raw = int(raw_number)
            raw_positions[joint] = raw
            normalized[f"{joint}.pos"] = self.raw_to_normalized(joint, raw)
        return PositionSample(raw_positions, normalized)

    def _diagnostic_read(
        self, register: str, issues: list[str]
    ) -> dict[str, int] | None:
        try:
            return self._strict_read(register)
        except BaseException as exc:
            issues.append(f"{register} unknown: {exc}")
            return None

    def _strict_read(self, register: str) -> dict[str, int]:
        if self._bus is None:
            raise RuntimeError("Motor bus has not been constructed")
        values = getattr(self._bus, "sync_read")(register, normalize=False)
        if not isinstance(values, dict):
            raise RuntimeError(f"{register} read did not return a joint mapping")
        missing = set(JOINT_NAMES) - set(values)
        extra = set(values) - set(JOINT_NAMES)
        if missing or extra:
            raise RuntimeError(
                f"{register} must contain exactly the five configured joints; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        result: dict[str, int] = {}
        for joint in JOINT_NAMES:
            number = _finite_number(values[joint], f"{register} {joint}")
            if not number.is_integer():
                raise RuntimeError(f"{register} {joint} must be an integer")
            result[joint] = int(number)
        return result

    def _write_all(self, register: str, value: int) -> None:
        self._write_raw(register, {joint: value for joint in JOINT_NAMES})

    def _write_raw(self, register: str, values: dict[str, int]) -> None:
        if self._bus is None:
            raise RuntimeError("Motor bus has not been constructed")
        if set(values) != set(JOINT_NAMES):
            raise ValueError(f"{register} write requires all five joints")
        getattr(self._bus, "sync_write")(register, values, normalize=False)

    def _require_joint(self, joint: str) -> JointCalibration:
        calibration = self._require_calibration()
        try:
            return calibration[joint]
        except KeyError as exc:
            raise ValueError(f"Unknown joint: {joint}") from exc

    def _require_calibration(self) -> dict[str, JointCalibration]:
        if self._calibration is None:
            raise RuntimeError("Motor backend calibration has not been verified")
        return self._calibration

    def _require_healthy_connection(self) -> None:
        if not self._connected or self._bus is None:
            raise RuntimeError("Motor backend is not connected")
        if self._fault is not None:
            raise RuntimeError("Motor backend is blocked by a latched fault") from self._fault

    def _latch_fault(self, exc: BaseException) -> None:
        if self._fault is None:
            self._fault = exc


def _load_calibration(
    calibration_dir: Path,
    expected_sha256: str,
) -> tuple[dict[str, JointCalibration], str]:
    path = calibration_dir / "lelamp.json"
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"Cannot read required calibration file: {path}") from exc
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256.lower():
        raise RuntimeError(
            f"Calibration SHA-256 mismatch: expected {expected_sha256.lower()}, got {digest}"
        )
    try:
        decoded = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Calibration file is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict) or set(decoded) != set(JOINT_NAMES):
        raise RuntimeError("Calibration must contain exactly the five LeLamp joints")

    calibration: dict[str, JointCalibration] = {}
    for joint in JOINT_NAMES:
        raw_entry = decoded[joint]
        if not isinstance(raw_entry, dict):
            raise RuntimeError(f"Calibration entry {joint} must be an object")
        try:
            entry = JointCalibration(
                motor_id=_json_int(raw_entry["id"], f"{joint}.id"),
                drive_mode=_json_int(raw_entry["drive_mode"], f"{joint}.drive_mode"),
                homing_offset=_json_int(
                    raw_entry["homing_offset"], f"{joint}.homing_offset"
                ),
                range_min=_json_int(raw_entry["range_min"], f"{joint}.range_min"),
                range_max=_json_int(raw_entry["range_max"], f"{joint}.range_max"),
            )
        except KeyError as exc:
            raise RuntimeError(f"Calibration entry {joint} is incomplete") from exc
        if entry.motor_id != EXPECTED_JOINT_IDS[joint]:
            raise RuntimeError(
                f"Calibration motor ID mismatch for {joint}: expected "
                f"{EXPECTED_JOINT_IDS[joint]}, got {entry.motor_id}"
            )
        if entry.range_min >= entry.range_max:
            raise RuntimeError(f"Calibration range for {joint} is invalid")
        calibration[joint] = entry
    return calibration, digest


def load_calibration_file(
    calibration_dir: str | Path,
    expected_sha256: str,
) -> tuple[dict[str, JointCalibration], str]:
    """Validate the immutable calibration file without opening a motor bus."""
    return _load_calibration(Path(calibration_dir), expected_sha256)


def _validate_normalized_target(raw: dict[str, Any]) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError("Motor action must be a joint mapping")
    keys = set(raw)
    expected = set(JOINT_POSITION_KEYS)
    missing = expected - keys
    extra = keys - expected
    if missing:
        raise ValueError(f"Motor action missing joint targets: {sorted(missing)}")
    if extra:
        raise ValueError(f"Motor action has unknown joint targets: {sorted(extra)}")
    result: dict[str, float] = {}
    for key in JOINT_POSITION_KEYS:
        value = _finite_number(raw[key], key)
        if value < -100.0 or value > 100.0:
            raise ValueError(
                f"{key} out of normalized range [-100, 100]: {value}"
            )
        result[key] = value
    return result


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _json_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"Calibration {label} must be an integer")
    return value


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
