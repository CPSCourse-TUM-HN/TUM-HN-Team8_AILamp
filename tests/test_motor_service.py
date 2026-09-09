import builtins
import os
import subprocess
import sys
import textwrap
import types
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from ailamp.services.motor import (
    DEFAULT_JOINT_LIMITS_DEG,
    DEFAULT_JOINT_LIMITS_UNITS,
    JointDeltaCommand,
    JointSafetyLimiter,
    MotorService,
    RecordingStore,
)


def test_motor_service_reports_missing_upstream_runtime(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("lelamp"):
            raise ModuleNotFoundError(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    service = MotorService("/dev/null", "ailamp", "ailamp_runtime/ailamp/recordings")

    with pytest.raises(RuntimeError, match="upstream LeLamp runtime"):
        service.connect()


def test_motor_service_uses_public_follower_config_flags_without_injection(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    @dataclass
    class BaseConfig:
        id: str | None = None

    @dataclass
    class SyntheticConfig(BaseConfig):
        port: str = ""
        disable_torque_on_disconnect: bool = True
        max_relative_target: float | None = None
        cameras: dict[str, object] | None = None
        use_degrees: bool = False

    class SyntheticFollower:
        def __init__(self, config):
            captured["config"] = config
            captured["robot"] = self
            self.is_calibrated = False
            self.observation = observed_state()
            self.disconnected = False

        def connect(self, calibrate=False):
            assert calibrate is False
            self.is_calibrated = True

        def get_observation(self):
            return self.observation.copy()

        def disconnect(self):
            self.disconnected = True

    lelamp_module = types.ModuleType("lelamp")
    follower_module = types.ModuleType("lelamp.follower")
    follower_module.LeLampFollower = SyntheticFollower
    follower_module.LeLampFollowerConfig = SyntheticConfig
    monkeypatch.setitem(sys.modules, "lelamp", lelamp_module)
    monkeypatch.setitem(sys.modules, "lelamp.follower", follower_module)

    service = MotorService(
        "/dev/ttyUSB0",
        "ailamp",
        "ailamp_runtime/ailamp/recordings",
        max_step_units=7,
        lock_dir=tmp_path,
    )

    service.connect()
    service.close()

    assert captured["config"].port == "/dev/ttyUSB0"
    assert captured["config"].id == "ailamp"
    assert captured["config"].use_degrees is False
    assert captured["config"].max_relative_target == 7
    assert captured["config"].disable_torque_on_disconnect is False
    assert captured["robot"].disconnected is True


def test_robot_factory_config_preserves_lamp_id(tmp_path):
    captured: dict[str, object] = {}

    class FactoryRobot(FakeRobot):
        def __init__(self, config):
            captured["config"] = config
            super().__init__(observed_state())

    service = MotorService(
        "/dev/null",
        "desk-lamp",
        "ailamp_runtime/ailamp/recordings",
        robot_factory=FactoryRobot,
        lock_dir=tmp_path,
    )

    service.connect()
    service.close()

    assert captured["config"].id == "desk-lamp"
    assert captured["config"].port == "/dev/null"
    assert captured["config"].use_degrees is False
    assert captured["config"].max_relative_target == 4
    assert captured["config"].disable_torque_on_disconnect is False


def test_motor_service_rejects_uncalibrated_robot():
    robot = FakeRobot(observation=observed_state(), calibrated=False)
    service = MotorService("/dev/null", "ailamp", "ailamp_runtime/ailamp/recordings", robot=robot)

    with pytest.raises(RuntimeError, match="calibration"):
        service.connect()


def test_motor_service_requires_explicit_public_calibration_after_connect(tmp_path):
    robot = FakeRobotWithoutCalibration(observation=observed_state())
    service = MotorService(
        "/dev/null",
        "ailamp",
        "ailamp_runtime/ailamp/recordings",
        robot=robot,
        lock_dir=tmp_path,
    )

    with pytest.raises(RuntimeError, match="calibration"):
        service.connect()

    assert robot.disconnected is True


def test_connect_failure_cleans_up_and_preserves_original_error(tmp_path):
    robot = RetryableDisconnectRobot(observation={"base_yaw.pos": 0.0})
    service = MotorService(
        "/dev/null",
        "ailamp",
        "ailamp_runtime/ailamp/recordings",
        robot=robot,
        lock_dir=tmp_path,
    )

    with pytest.raises(RuntimeError, match="Invalid initial motor observation") as exc_info:
        service.connect()

    assert exc_info.value is service.last_error
    assert "disconnect cleanup failed" in "\n".join(getattr(exc_info.value, "__notes__", []))

    replacement = MotorService(
        "/dev/null",
        "ailamp",
        "ailamp_runtime/ailamp/recordings",
        robot=FakeRobot(observation=observed_state()),
        lock_dir=tmp_path,
    )
    with pytest.raises(RuntimeError, match="already owned"):
        replacement.connect()

    robot.fail_disconnect = False
    service.close()
    replacement.connect()
    replacement.close()

    assert robot.disconnect_calls == 2


def test_motor_service_rejects_same_port_until_close_releases_lock(tmp_path):
    first = MotorService(
        "/tmp/fake-tty",
        "ailamp",
        "ailamp_runtime/ailamp/recordings",
        robot=FakeRobot(observation=observed_state()),
        lock_dir=tmp_path,
    )
    second = MotorService(
        "/tmp/fake-tty",
        "ailamp",
        "ailamp_runtime/ailamp/recordings",
        robot=FakeRobot(observation=observed_state()),
        lock_dir=tmp_path,
    )

    first.connect()
    with pytest.raises(RuntimeError, match="already owned"):
        second.connect()

    first.close()
    second.connect()
    second.close()


def test_motor_service_port_lock_excludes_other_process(tmp_path):
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    code = textwrap.dedent(
        """
        import pathlib
        import sys
        import time
        from ailamp.services.motor import MotorService

        def observed_state():
            return {
                "base_yaw.pos": 0.0,
                "base_pitch.pos": 0.0,
                "elbow_pitch.pos": 0.0,
                "wrist_roll.pos": 0.0,
                "wrist_pitch.pos": 0.0,
            }

        class FakeRobot:
            is_calibrated = True
            def connect(self, calibrate=False):
                pass
            def get_observation(self):
                return observed_state()
            def disconnect(self):
                pass

        lock_dir, ready, release, port = map(pathlib.Path, sys.argv[1:5])
        service = MotorService(str(port), "ailamp", "ailamp_runtime/ailamp/recordings", robot=FakeRobot(), lock_dir=lock_dir)
        service.connect()
        ready.write_text("ready")
        deadline = time.monotonic() + 5
        while not release.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        service.close()
        """
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = "ailamp_runtime"
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path), str(ready), str(release), "/tmp/fake-process-tty"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()

        service = MotorService(
            "/tmp/fake-process-tty",
            "ailamp",
            "ailamp_runtime/ailamp/recordings",
            robot=FakeRobot(observation=observed_state()),
            lock_dir=tmp_path,
        )
        with pytest.raises(RuntimeError, match="already owned"):
            service.connect()
    finally:
        release.write_text("release")
        child.wait(timeout=5)


def test_motor_service_validates_fps_is_finite_positive():
    with pytest.raises(ValueError, match="fps must be finite"):
        MotorService("/dev/null", "ailamp", "ailamp_runtime/ailamp/recordings", fps=float("nan"))

    with pytest.raises(ValueError, match="fps must be greater than zero"):
        MotorService("/dev/null", "ailamp", "ailamp_runtime/ailamp/recordings", fps=0)


def test_joint_safety_limiter_clips_delta_targets():
    limiter = JointSafetyLimiter({"base_yaw": (-10.0, 10.0), "wrist_pitch": (-5.0, 5.0)})

    target = limiter.apply(
        {"base_yaw.pos": 9.0, "wrist_pitch.pos": -4.0},
        [JointDeltaCommand("base_yaw", 5.0), JointDeltaCommand("wrist_pitch", -5.0)],
    )

    assert target["base_yaw.pos"] == 10.0
    assert target["wrist_pitch.pos"] == -5.0


def test_joint_delta_command_legacy_delta_deg_is_normalized_units():
    command = JointDeltaCommand("base_yaw", 3.5)

    assert command.delta_units == 3.5


def test_default_joint_limits_keep_legacy_alias():
    assert DEFAULT_JOINT_LIMITS_DEG is DEFAULT_JOINT_LIMITS_UNITS
    assert DEFAULT_JOINT_LIMITS_UNITS["base_yaw"] == (-100.0, 100.0)


def test_joint_safety_limiter_rejects_unknown_or_absent_values():
    limiter = JointSafetyLimiter({"base_yaw": (-10.0, 10.0)})

    with pytest.raises(ValueError, match="current state"):
        limiter.apply({}, [JointDeltaCommand("base_yaw", 1.0)])

    with pytest.raises(ValueError, match="Unknown joint"):
        limiter.apply({"base_yaw.pos": 0.0}, [JointDeltaCommand("wrist_pitch", 1.0)])

    with pytest.raises(ValueError, match="finite"):
        limiter.apply({"base_yaw.pos": 0.0}, [JointDeltaCommand("base_yaw", float("nan"))])


def test_recording_store_loads_all_local_recordings():
    store = RecordingStore("ailamp_runtime/ailamp/recordings")

    assert store.list_names() == [
        "curious",
        "excited",
        "happy_wiggle",
        "headshake",
        "idle",
        "nod",
        "sad",
        "scanning",
        "shock",
        "shy",
        "wake_up",
    ]

    for name in store.list_names():
        rows = store.load(name)
        assert rows
        assert set(rows[0]) == {
            "base_yaw.pos",
            "base_pitch.pos",
            "elbow_pitch.pos",
            "wrist_roll.pos",
            "wrist_pitch.pos",
        }


def test_recording_store_rejects_malformed_and_traversal_csv(tmp_path):
    (tmp_path / "duplicate.csv").write_text(
        "timestamp,base_yaw.pos,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos\n"
        "0,0,1,0,0,0,0\n"
    )
    (tmp_path / "missing.csv").write_text("timestamp,base_yaw.pos\n0,1\n")
    (tmp_path / "nan.csv").write_text(
        "timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos\n"
        "0,nan,0,0,0,0\n"
    )
    (tmp_path / "inf.csv").write_text(
        "timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos\n"
        "0,inf,0,0,0,0\n"
    )
    (tmp_path / "range.csv").write_text(
        "timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos\n"
        "0,101,0,0,0,0\n"
    )
    (tmp_path / "unknown.csv").write_text(
        "timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos,extra\n"
        "0,0,0,0,0,0,1\n"
    )
    (tmp_path / "empty.csv").write_text(
        "timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos\n"
    )

    store = RecordingStore(tmp_path)

    with pytest.raises(ValueError, match="duplicate"):
        store.load("duplicate")
    with pytest.raises(ValueError, match="missing"):
        store.load("missing")
    with pytest.raises(ValueError, match="finite"):
        store.load("nan")
    with pytest.raises(ValueError, match="finite"):
        store.load("inf")
    with pytest.raises(ValueError, match="out of normalized range"):
        store.load("range")
    with pytest.raises(ValueError, match="unknown columns"):
        store.load("unknown")
    with pytest.raises(ValueError, match="empty"):
        store.load("empty")
    with pytest.raises(ValueError, match="recording name"):
        store.load("../idle")
    with pytest.raises(ValueError, match="Recording name must be a string"):
        store.load(123)

    outside = tmp_path.parent / "outside.csv"
    outside.write_text(
        "timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos\n"
        "0,0,0,0,0,0\n"
    )
    try:
        (tmp_path / "escape.csv").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available on this filesystem")
    with pytest.raises(ValueError, match="escapes"):
        store.load("escape")


class FakeConfig:
    def __init__(
        self,
        *,
        port,
        id,
        use_degrees,
        max_relative_target,
        disable_torque_on_disconnect,
    ):
        self.port = port
        self.id = id
        self.use_degrees = use_degrees
        self.max_relative_target = max_relative_target
        self.disable_torque_on_disconnect = disable_torque_on_disconnect


class FakeRobot:
    Config = FakeConfig

    def __init__(self, observation, *, calibrated=True):
        self.observation = observation
        self.is_calibrated = calibrated
        self.connected = False
        self.disconnected = False
        self.sent_actions = []

    def connect(self, calibrate=False):
        assert calibrate is False
        self.connected = True

    def get_observation(self):
        return self.observation.copy()

    def send_action(self, action):
        clipped = action.copy()
        self.sent_actions.append(clipped)
        return clipped

    def disconnect(self):
        self.disconnected = True


class FakeRobotWithoutCalibration(FakeRobot):
    def __init__(self, observation):
        super().__init__(observation)
        del self.is_calibrated
        self.calibration = {"unrelated": True}


class RetryableDisconnectRobot(FakeRobot):
    def __init__(self, observation):
        super().__init__(observation)
        self.fail_disconnect = True
        self.disconnect_calls = 0

    def disconnect(self):
        self.disconnect_calls += 1
        if self.fail_disconnect:
            raise RuntimeError("serial disconnect failed")
        self.disconnected = True


def observed_state(**overrides):
    state = {
        "base_yaw.pos": 12.0,
        "base_pitch.pos": -8.0,
        "elbow_pitch.pos": 45.0,
        "wrist_roll.pos": 3.0,
        "wrist_pitch.pos": -2.0,
    }
    state.update(overrides)
    return state
