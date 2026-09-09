import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

from ailamp.services.motor import JOINT_NAMES, JOINT_POSITION_KEYS
from ailamp.services.motor_backend import (
    DEFAULT_ACCELERATION_LIMITS,
    DEFAULT_GOAL_VELOCITY_LIMITS,
    DEFAULT_TORQUE_LIMITS,
    LeLampMotorBackend,
)


JOINT_IDS = {joint: index for index, joint in enumerate(JOINT_NAMES, start=1)}


class FakeBus:
    def __init__(
        self,
        calibration,
        *,
        positions=None,
        torque=None,
        modes=None,
        disconnect_failures=0,
        read_failure=None,
        fail_goal_after_auto_enable=False,
    ):
        self.is_calibrated = True
        self.positions = positions or {
            joint: values["range_min"] for joint, values in calibration.items()
        }
        self.torque = torque or {joint: 0 for joint in JOINT_NAMES}
        self.modes = modes or {joint: 0 for joint in JOINT_NAMES}
        self.disconnect_failures = disconnect_failures
        self.read_failure = read_failure
        self.fail_goal_after_auto_enable = fail_goal_after_auto_enable
        self.operations = []
        self.connected = False
        self.disconnect_disable_torque = None

    def connect(self):
        self.operations.append(("connect", None))
        self.connected = True

    def sync_read(self, register, *, normalize=False):
        assert normalize is False
        self.operations.append(("read", register))
        if register == self.read_failure:
            raise RuntimeError(f"{register} read failed")
        if register == "Present_Position":
            return self.positions.copy()
        if register == "Torque_Enable":
            return self.torque.copy()
        if register == "Operating_Mode":
            return self.modes.copy()
        raise AssertionError(register)

    def sync_write(self, register, values, *, normalize=False):
        assert normalize is False
        self.operations.append(("write", register, values.copy()))
        if register == "Goal_Position":
            self.positions = values.copy()
            self.torque = {joint: 1 for joint in JOINT_NAMES}
            if self.fail_goal_after_auto_enable:
                raise RuntimeError("Goal_Position failed after automatic torque enable")
        elif register == "Torque_Enable":
            self.torque = values.copy()

    def disconnect(self, *, disable_torque):
        self.operations.append(("disconnect", disable_torque))
        self.disconnect_disable_torque = disable_torque
        if self.disconnect_failures:
            self.disconnect_failures -= 1
            raise RuntimeError("disconnect failed")
        self.connected = False


@pytest.fixture
def calibration(tmp_path):
    values = {
        joint: {
            "id": JOINT_IDS[joint],
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": 1000 + index * 100,
            "range_max": 2000 + index * 100,
        }
        for index, joint in enumerate(JOINT_NAMES)
    }
    path = tmp_path / "lelamp.json"
    path.write_text(json.dumps(values))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return tmp_path, values, digest


def make_backend(calibration, bus):
    directory, _, digest = calibration
    return LeLampMotorBackend(
        port="/dev/fake-serial",
        calibration_dir=directory,
        expected_calibration_sha256=digest,
        bus=bus,
    )


def test_connect_is_read_only_and_disconnect_never_releases_torque(calibration):
    _, values, _ = calibration
    bus = FakeBus(values)
    backend = make_backend(calibration, bus)

    diagnostics = backend.connect()

    assert diagnostics.normalized_positions == {
        key: -100.0 for key in JOINT_POSITION_KEYS
    }
    assert not [entry for entry in bus.operations if entry[0] == "write"]
    backend.disconnect()
    assert bus.disconnect_disable_torque is False


def test_disconnect_failure_is_retried_on_the_next_disconnect(calibration):
    _, values, _ = calibration
    bus = FakeBus(values, disconnect_failures=1)
    backend = make_backend(calibration, bus)
    backend.connect()

    with pytest.raises(RuntimeError, match="disconnect failed"):
        backend.disconnect()
    assert bus.connected is True

    backend.disconnect()

    assert bus.connected is False
    assert [entry for entry in bus.operations if entry[0] == "disconnect"] == [
        ("disconnect", False),
        ("disconnect", False),
    ]


@pytest.mark.parametrize(
    ("failure_point", "message"),
    [
        ("calibration", "configured calibration"),
        ("observation", "Present_Position read failed"),
    ],
)
def test_failed_connect_keeps_cleanup_pending_when_disconnect_fails(
    calibration, failure_point, message
):
    _, values, _ = calibration
    bus = FakeBus(
        values,
        disconnect_failures=1,
        read_failure=("Present_Position" if failure_point == "observation" else None),
    )
    if failure_point == "calibration":
        bus.is_calibrated = False
    backend = make_backend(calibration, bus)

    with pytest.raises(RuntimeError, match=message) as raised:
        backend.connect()

    assert any(
        "disconnect cleanup failed" in note
        for note in getattr(raised.value, "__notes__", ())
    )
    assert bus.connected is True

    backend.disconnect()

    assert bus.connected is False
    assert len(
        [entry for entry in bus.operations if entry[0] == "disconnect"]
    ) == 2


def test_non_lelamp_identity_is_rejected_before_bus_connect(calibration):
    directory, values, digest = calibration
    bus = FakeBus(values)

    with pytest.raises(ValueError, match="lamp_id.*lelamp"):
        LeLampMotorBackend(
            port="/dev/fake-serial",
            lamp_id="other-lamp",
            calibration_dir=directory,
            expected_calibration_sha256=digest,
            bus=bus,
        )

    assert bus.operations == []


def test_upstream_follower_is_only_used_to_construct_the_bus(monkeypatch, calibration):
    directory, values, digest = calibration
    bus = FakeBus(values)
    captured = {}

    class Config:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class Follower:
        def __init__(self, config):
            self.bus = bus

        def connect(self, *args, **kwargs):
            raise AssertionError("follower.connect must never be called")

        configure = connect
        calibrate = connect

    package = types.ModuleType("lelamp")
    module = types.ModuleType("lelamp.follower")
    module.LeLampFollower = Follower
    module.LeLampFollowerConfig = Config
    monkeypatch.setitem(sys.modules, "lelamp", package)
    monkeypatch.setitem(sys.modules, "lelamp.follower", module)

    backend = LeLampMotorBackend(
        port="/dev/fake-serial",
        calibration_dir=directory,
        expected_calibration_sha256=digest,
    )
    backend.connect()

    assert captured == {
        "port": "/dev/fake-serial",
        "id": "lelamp",
        "calibration_dir": Path(directory),
        "cameras": {},
        "use_degrees": False,
        "disable_torque_on_disconnect": False,
        "max_relative_target": None,
    }


def test_hash_and_raw_range_are_checked_before_any_motor_write(calibration):
    directory, values, digest = calibration
    bus = FakeBus(values)
    bad_hash = "0" * len(digest)
    backend = LeLampMotorBackend(
        port="/dev/fake-serial",
        calibration_dir=directory,
        expected_calibration_sha256=bad_hash,
        bus=bus,
    )

    with pytest.raises(RuntimeError, match="SHA-256"):
        backend.connect()
    assert bus.operations == []

    bus.positions["base_yaw"] = values["base_yaw"]["range_max"] + 1
    backend = make_backend(calibration, bus)
    with pytest.raises(RuntimeError, match="calibrated raw range"):
        backend.connect()
    assert not [entry for entry in bus.operations if entry[0] == "write"]


@pytest.mark.parametrize(
    ("torque", "modes", "message"),
    [
        ({"base_yaw": 1}, {}, "already enabled"),
        ({}, {"wrist_pitch": 1}, "position mode"),
    ],
)
def test_arm_refuses_enabled_or_wrong_mode_without_writes(
    calibration, torque, modes, message
):
    _, values, _ = calibration
    bus = FakeBus(
        values,
        torque={joint: torque.get(joint, 0) for joint in JOINT_NAMES},
        modes={joint: modes.get(joint, 0) for joint in JOINT_NAMES},
    )
    backend = make_backend(calibration, bus)
    backend.connect()

    with pytest.raises(RuntimeError, match=message):
        backend.arm(goal_velocity=300, acceleration=20, torque_limit=250)

    assert not [entry for entry in bus.operations if entry[0] == "write"]


def test_arm_writes_bounded_sram_parameters_before_current_goal_and_enable(calibration):
    _, values, _ = calibration
    positions = {
        joint: (entry["range_min"] + entry["range_max"]) // 2
        for joint, entry in values.items()
    }
    bus = FakeBus(values, positions=positions)
    backend = make_backend(calibration, bus)
    backend.connect()

    sample = backend.arm(goal_velocity=300, acceleration=20, torque_limit=250)

    writes = [entry for entry in bus.operations if entry[0] == "write"]
    assert [entry[1] for entry in writes] == [
        "Goal_Velocity",
        "Acceleration",
        "Torque_Limit",
        "Goal_Position",
        "Torque_Enable",
    ]
    assert writes[3][2] == sample.raw_positions
    assert writes[4][2] == {joint: 1 for joint in JOINT_NAMES}

    for bad, limits, keyword in [
        (limits[1] + 1, limits, keyword)
        for limits, keyword in [
            (DEFAULT_GOAL_VELOCITY_LIMITS, "goal_velocity"),
            (DEFAULT_ACCELERATION_LIMITS, "acceleration"),
            (DEFAULT_TORQUE_LIMITS, "torque_limit"),
        ]
    ]:
        kwargs = {"goal_velocity": 300, "acceleration": 20, "torque_limit": 250}
        kwargs[keyword] = bad
        other_bus = FakeBus(values, positions=positions)
        other = make_backend(calibration, other_bus)
        other.connect()
        with pytest.raises(ValueError, match=keyword):
            other.arm(**kwargs)
        assert not [entry for entry in other_bus.operations if entry[0] == "write"]


def test_goal_failure_after_automatic_torque_enable_remains_armed(calibration):
    _, values, _ = calibration
    bus = FakeBus(values, fail_goal_after_auto_enable=True)
    backend = make_backend(calibration, bus)
    backend.connect()

    with pytest.raises(RuntimeError, match="automatic torque enable"):
        backend.arm(goal_velocity=300, acceleration=20, torque_limit=250)

    writes = [entry[1] for entry in bus.operations if entry[0] == "write"]
    assert writes == [
        "Goal_Velocity",
        "Acceleration",
        "Torque_Limit",
        "Goal_Position",
    ]
    assert bus.torque == {joint: 1 for joint in JOINT_NAMES}
    assert backend.is_armed is True


def test_send_action_requires_complete_in_range_five_axis_target(calibration):
    _, values, _ = calibration
    bus = FakeBus(values)
    backend = make_backend(calibration, bus)
    backend.connect()
    backend.arm(goal_velocity=300, acceleration=20, torque_limit=250)
    target = {key: float(index) for index, key in enumerate(JOINT_POSITION_KEYS)}

    returned = backend.send_action(target)

    assert returned == target
    assert set(returned) == set(JOINT_POSITION_KEYS)
    writes_before_invalid = len([entry for entry in bus.operations if entry[0] == "write"])
    with pytest.raises(ValueError, match="missing joint"):
        backend.send_action({"base_yaw.pos": 0.0})
    invalid = target.copy()
    invalid["base_yaw.pos"] = 101.0
    with pytest.raises(ValueError, match="normalized range"):
        backend.send_action(invalid)
    assert len([entry for entry in bus.operations if entry[0] == "write"]) == writes_before_invalid
