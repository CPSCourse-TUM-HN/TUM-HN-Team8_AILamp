import json
import threading
import time
from types import SimpleNamespace

import pytest

from ailamp.services.motor import JOINT_NAMES, JOINT_POSITION_KEYS, MotorService
from ailamp.services.motor_backend import PositionSample
from ailamp.services.motor_runtime import MotorDemoRuntime


CALIBRATION_SHA256 = "8" * 64


class FakeMotorRobot:
    is_calibrated = True

    def __init__(
        self,
        actual=None,
        *,
        auto_follow=True,
        block_send_number=None,
        fail_send_number=None,
    ):
        self.actual = actual or state(12.0)
        self.auto_follow = auto_follow
        self.block_send_number = block_send_number
        self.fail_send_number = fail_send_number
        self.sent_actions = []
        self.operations = []
        self.armed = False
        self.disconnected = False
        self._send_started = threading.Condition()
        self._release_send = threading.Event()

    def connect(self, calibrate=False):
        assert calibrate is False
        self.operations.append(("connect", threading.get_ident()))

    def get_observation(self):
        self.operations.append(("read", threading.get_ident()))
        return self.actual.copy()

    def read_position_sample(self):
        self.operations.append(("sample", threading.get_ident()))
        return PositionSample(
            raw_positions={
                joint: int(round(self.actual[f"{joint}.pos"]))
                for joint in JOINT_NAMES
            },
            normalized_positions=self.actual.copy(),
        )

    def arm(self, *, goal_velocity, acceleration, torque_limit):
        self.operations.append(
            (
                "arm",
                threading.get_ident(),
                goal_velocity,
                acceleration,
                torque_limit,
            )
        )
        self.armed = True
        return self.read_position_sample()

    def send_action(self, action):
        assert self.armed
        action = action.copy()
        with self._send_started:
            send_number = len(self.sent_actions) + 1
            self.sent_actions.append(action)
            self.operations.append(("send", threading.get_ident(), action))
            self._send_started.notify_all()
        if send_number == self.block_send_number:
            self._release_send.wait(timeout=1.0)
        if send_number == self.fail_send_number:
            raise RuntimeError("fake serial write failed")
        if self.auto_follow:
            self.actual = action.copy()
        return action

    def release(self):
        self.operations.append(("release", threading.get_ident()))
        self.armed = False

    def disconnect(self):
        self.operations.append(("disconnect", threading.get_ident()))
        self.disconnected = True

    def wait_for_sends(self, count):
        deadline = time.monotonic() + 1.0
        with self._send_started:
            while len(self.sent_actions) < count:
                remaining = deadline - time.monotonic()
                assert remaining > 0
                self._send_started.wait(remaining)

    def release_blocked_send(self):
        self._release_send.set()


class FakeConnectionDiagnostics:
    def __init__(self, torque_enable):
        self.torque_enable = torque_enable


class DiagnosticFakeMotorRobot(FakeMotorRobot):
    def __init__(self, *args, torque_enable, **kwargs):
        super().__init__(*args, **kwargs)
        self.backend_fault = None
        self.diagnostics = FakeConnectionDiagnostics(torque_enable)

    @property
    def is_armed(self):
        return self.armed

    @property
    def fault(self):
        return self.backend_fault


class FailingArmAfterEnableRobot(DiagnosticFakeMotorRobot):
    def arm(self, *, goal_velocity, acceleration, torque_limit):
        self.operations.append(
            (
                "arm",
                threading.get_ident(),
                goal_velocity,
                acceleration,
                torque_limit,
            )
        )
        self.armed = True
        error = RuntimeError("Goal_Position failed after automatic torque enable")
        self.backend_fault = error
        raise error


def make_unarmed_runtime(
    tmp_path,
    robot,
    *,
    feedback_timeout=0.1,
    max_step_units=100,
):
    recordings = tmp_path / "recordings"
    recordings.mkdir(parents=True, exist_ok=True)
    service = MotorService(
        "/dev/fake-demo",
        "lelamp",
        recordings,
        robot=robot,
        fps=1000,
        max_step_units=max_step_units,
        lock_dir=tmp_path / "locks",
    )
    runtime = MotorDemoRuntime(
        service,
        calibration_sha256=CALIBRATION_SHA256,
        home_path=tmp_path / "home.json",
        feedback_tolerance=0.01,
        feedback_timeout=feedback_timeout,
        feedback_poll_interval=0.001,
    )
    runtime.connect()
    return runtime, service


def make_runtime(tmp_path, robot, *, feedback_timeout=0.1):
    runtime, service = make_unarmed_runtime(
        tmp_path,
        robot,
        feedback_timeout=feedback_timeout,
    )
    runtime.arm(
        goal_velocity=300,
        acceleration=20,
        torque_limit=250,
        operator_confirmed=True,
    )
    return runtime, service


def test_arm_syncs_fresh_hold_target_before_step_limited_playback(tmp_path):
    robot = FakeMotorRobot(actual=state(0.0))
    write_recording(tmp_path / "recordings", "nearby", [state(64.0)])
    runtime, service = make_unarmed_runtime(
        tmp_path,
        robot,
        max_step_units=4,
    )
    assert service.positions["base_yaw.pos"] == 0.0

    robot.actual = state(60.0)
    sample = runtime.arm(
        goal_velocity=300,
        acceleration=20,
        torque_limit=250,
        operator_confirmed=True,
    )
    assert isinstance(sample, PositionSample)
    assert service.positions["base_yaw.pos"] == 60.0

    runtime.play("nearby")
    assert runtime.wait_until_idle(1.0)
    assert robot.sent_actions[0]["base_yaw.pos"] == 64.0
    runtime.release(operator_confirmed=True)
    runtime.close()


def test_stop_waits_for_inflight_send_then_fresh_reads_and_holds_12(tmp_path):
    robot = FakeMotorRobot(
        actual=state(12.0), auto_follow=False, block_send_number=1
    )
    write_recording(tmp_path / "recordings", "move", [state(70.0), state(80.0)])
    runtime, service = make_runtime(tmp_path, robot)

    runtime.play("move")
    robot.wait_for_sends(1)
    stop_thread = threading.Thread(target=runtime.stop)
    stop_thread.start()
    time.sleep(0.02)
    assert stop_thread.is_alive()

    robot.release_blocked_send()
    stop_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert [action["base_yaw.pos"] for action in robot.sent_actions] == [70.0, 12.0]
    assert service.positions["base_yaw.pos"] == 12.0
    assert service.position_source == "fresh_measured_hold"
    worker = service.worker_thread_id
    assert {entry[1] for entry in robot.operations if entry[0] in {"send", "read"}} >= {
        worker
    }
    assert all(
        entry[1] == worker
        for entry in robot.operations[2:]
        if entry[0] in {"send", "read", "sample", "arm", "release"}
    )
    runtime.release(operator_confirmed=True)
    runtime.close()


def test_play_preserves_complete_repeated_source_frames_and_feedback_events(tmp_path):
    robot = FakeMotorRobot(actual=state(0.0))
    rows = [state(10.0), state(10.0), state(20.0)]
    write_recording(tmp_path / "recordings", "repeat", rows)
    runtime, _ = make_runtime(tmp_path, robot)

    runtime.play("repeat")

    assert runtime.wait_until_idle(1.0)
    assert robot.sent_actions == rows
    assert [event.kind for event in runtime.events if event.task == "repeat"] == [
        "sent",
        "feedback_reached",
    ]
    runtime.release(operator_confirmed=True)
    runtime.close()


def test_busy_runtime_rejects_new_play_and_stop_cancels_next_script_scene(tmp_path):
    robot = FakeMotorRobot(actual=state(0.0), block_send_number=1)
    write_recording(tmp_path / "recordings", "first", [state(10.0)])
    write_recording(tmp_path / "recordings", "second", [state(20.0)])
    runtime, _ = make_runtime(tmp_path, robot)

    runtime.play_script(["first", "second"], include_home=False)
    robot.wait_for_sends(1)
    with pytest.raises(RuntimeError, match="busy"):
        runtime.play("second")

    stop_thread = threading.Thread(target=runtime.stop)
    stop_thread.start()
    robot.release_blocked_send()
    stop_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert 20.0 not in [action["base_yaw.pos"] for action in robot.sent_actions]
    runtime.release(operator_confirmed=True)
    runtime.close()


def test_stop_cannot_finish_between_generation_check_and_segment_submission(
    tmp_path,
    monkeypatch,
):
    robot = FakeMotorRobot(actual=state(0.0))
    write_recording(tmp_path / "recordings", "move", [state(20.0)])
    runtime, service = make_runtime(tmp_path, robot)
    submit_boundary_reached = threading.Event()
    allow_submission = threading.Event()
    stop_started = threading.Event()
    stop_finished = threading.Event()
    late_old_send = threading.Event()
    stop_errors = []
    original_play_frames = service.play_frames
    original_send_action = robot.send_action

    def blocked_play_frames(*args, **kwargs):
        submit_boundary_reached.set()
        assert allow_submission.wait(timeout=1.0)
        return original_play_frames(*args, **kwargs)

    def tracked_send_action(action):
        if stop_finished.is_set() and action["base_yaw.pos"] == 20.0:
            late_old_send.set()
        return original_send_action(action)

    monkeypatch.setattr(service, "play_frames", blocked_play_frames)
    monkeypatch.setattr(robot, "send_action", tracked_send_action)
    runtime.play("move")
    assert submit_boundary_reached.wait(timeout=1.0)

    def stop_runtime():
        stop_started.set()
        try:
            runtime.stop()
        except BaseException as exc:
            stop_errors.append(exc)
        finally:
            stop_finished.set()

    stop_thread = threading.Thread(target=stop_runtime)
    stop_thread.start()
    assert stop_started.wait(timeout=1.0)
    stop_finished_before_submission = stop_finished.wait(timeout=0.2)
    allow_submission.set()
    stop_thread.join(timeout=1.0)
    assert service.wait_until_idle(1.0)

    assert not stop_finished_before_submission
    assert not stop_thread.is_alive()
    assert stop_errors == []
    assert not late_old_send.is_set()
    runtime.release(operator_confirmed=True)
    runtime.close()


def test_feedback_timeout_latches_fault_and_never_reports_reached(tmp_path):
    robot = FakeMotorRobot(actual=state(0.0), auto_follow=False)
    write_recording(tmp_path / "recordings", "unreached", [state(30.0)])
    runtime, _ = make_runtime(tmp_path, robot, feedback_timeout=0.01)

    runtime.play("unreached")

    assert runtime.wait_until_idle(1.0)
    assert runtime.fault is not None
    assert not [
        event
        for event in runtime.events
        if event.task == "unreached" and event.kind == "feedback_reached"
    ]
    with pytest.raises(RuntimeError, match="latched fault"):
        runtime.play("unreached")
    runtime.close(leave_holding=True)


def test_send_failure_is_latched_without_retry(tmp_path):
    robot = FakeMotorRobot(actual=state(0.0), fail_send_number=1)
    write_recording(tmp_path / "recordings", "broken", [state(40.0)])
    runtime, _ = make_runtime(tmp_path, robot)

    runtime.play("broken")

    assert runtime.wait_until_idle(1.0)
    assert "fake serial write failed" in str(runtime.fault)
    assert len(robot.sent_actions) == 1
    with pytest.raises(RuntimeError, match="latched fault"):
        runtime.home()
    runtime.close(leave_holding=True)


def test_capture_home_uses_fresh_sample_and_binds_calibration(tmp_path):
    robot = FakeMotorRobot(actual=state(0.0))
    runtime, service = make_runtime(tmp_path, robot)
    service.play_frames([state(70.0)], preserve_source_frames=True)
    assert service.wait_until_idle(1.0)
    robot.actual = state(12.0)

    with pytest.raises(ValueError, match="operator confirmation"):
        runtime.capture_home(operator_confirmed=False)
    home = runtime.capture_home(operator_confirmed=True)

    stored = json.loads((tmp_path / "home.json").read_text())
    assert service.positions["base_yaw.pos"] == 70.0
    assert home["normalized_positions"]["base_yaw.pos"] == 12.0
    assert home["raw_positions"]["base_yaw"] == 12
    assert stored["calibration_sha256"] == CALIBRATION_SHA256
    assert stored["operator_confirmed"] is True
    assert any(event.kind == "operator_confirmed" for event in runtime.events)

    stored["calibration_sha256"] = "0" * 64
    (tmp_path / "home.json").write_text(json.dumps(stored))
    before = len(robot.sent_actions)
    with pytest.raises(RuntimeError, match="calibration"):
        runtime.home()
    assert len(robot.sent_actions) == before
    runtime.release(operator_confirmed=True)
    runtime.close()


def test_arm_capture_release_are_worker_serialized_and_quit_does_not_auto_release(tmp_path):
    robot = FakeMotorRobot(actual=state(0.0))
    runtime, service = make_runtime(tmp_path, robot)
    runtime.capture_home(operator_confirmed=True)

    with pytest.raises(RuntimeError, match="release or explicitly leave holding"):
        runtime.close()

    runtime.release(operator_confirmed=True)
    worker = service.worker_thread_id
    assert all(
        entry[1] == worker
        for entry in robot.operations
        if entry[0] in {"arm", "sample", "release"}
    )
    runtime.close()

    second_robot = FakeMotorRobot(actual=state(0.0))
    second, _ = make_runtime(tmp_path / "second", second_robot)
    warning = second.close(leave_holding=True)
    assert "不再监测" in warning
    assert not [entry for entry in second_robot.operations if entry[0] == "release"]


@pytest.mark.parametrize(
    ("torque_enable", "warning_text"),
    [
        ({joint: 1 for joint in JOINT_NAMES}, "仍为使能"),
        (None, "状态未知"),
    ],
)
def test_diagnostic_torque_risk_requires_explicit_leave_holding(
    tmp_path,
    torque_enable,
    warning_text,
):
    robot = DiagnosticFakeMotorRobot(
        actual=state(0.0),
        torque_enable=torque_enable,
    )
    runtime, _ = make_unarmed_runtime(tmp_path, robot)

    assert runtime.is_armed is False
    with pytest.raises(RuntimeError, match="explicitly leave holding"):
        runtime.close()
    warning = runtime.close(leave_holding=True)

    assert warning_text in warning
    assert "不再监测" in warning
    assert robot.disconnected is True


def test_verified_all_off_and_legacy_fake_can_quietly_close(tmp_path):
    all_off = {joint: 0 for joint in JOINT_NAMES}
    verified_robot = DiagnosticFakeMotorRobot(
        actual=state(0.0),
        torque_enable=all_off,
    )
    verified_runtime, _ = make_unarmed_runtime(tmp_path / "verified", verified_robot)
    assert verified_runtime.close() == ""

    legacy_robot = FakeMotorRobot(actual=state(0.0))
    legacy_runtime, _ = make_unarmed_runtime(tmp_path / "legacy", legacy_robot)
    assert legacy_runtime.close() == ""


def test_failed_arm_after_goal_enable_marks_torque_unknown(tmp_path):
    robot = FailingArmAfterEnableRobot(
        actual=state(0.0),
        torque_enable={joint: 0 for joint in JOINT_NAMES},
    )
    runtime, _ = make_unarmed_runtime(tmp_path, robot)

    with pytest.raises(RuntimeError, match="automatic torque enable"):
        runtime.arm(
            goal_velocity=300,
            acceleration=20,
            torque_limit=250,
            operator_confirmed=True,
        )

    assert runtime.is_armed is False
    with pytest.raises(RuntimeError, match="explicitly leave holding"):
        runtime.close()
    warning = runtime.close(leave_holding=True)
    assert "状态未知" in warning
    assert "不再监测" in warning


def test_interrupted_arm_wait_marks_torque_unknown(tmp_path, monkeypatch):
    robot = DiagnosticFakeMotorRobot(
        actual=state(0.0),
        torque_enable={joint: 0 for joint in JOINT_NAMES},
    )
    runtime, service = make_unarmed_runtime(tmp_path, robot)

    def interrupt_arm_wait(_callback):
        raise KeyboardInterrupt("interrupted while waiting for arm")

    with monkeypatch.context() as patch:
        patch.setattr(service, "execute_serialized", interrupt_arm_wait)
        with pytest.raises(KeyboardInterrupt, match="waiting for arm"):
            runtime.arm(
                goal_velocity=300,
                acceleration=20,
                torque_limit=250,
                operator_confirmed=True,
            )

    assert runtime.status()["torque_state"] == "unknown"
    assert not [entry for entry in robot.operations if entry[0] == "arm"]
    with pytest.raises(RuntimeError, match="explicitly leave holding"):
        runtime.close()
    warning = runtime.close(leave_holding=True)
    assert "状态未知" in warning
    assert "不再监测" in warning


def test_eof_shutdown_closes_unknown_torque_without_sending(tmp_path):
    robot = DiagnosticFakeMotorRobot(actual=state(0.0), torque_enable=None)
    runtime, _ = make_unarmed_runtime(tmp_path, robot)

    message = runtime.shutdown_for_eof_or_signal("EOF")

    assert runtime.is_connected is False
    assert robot.disconnected is True
    assert "状态未知" in message
    assert "不再监测" in message
    assert robot.sent_actions == []


def test_invalid_arm_parameters_are_rejected_before_worker_without_latching(tmp_path):
    robot = FakeMotorRobot(actual=state(0.0))
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    service = MotorService(
        "/dev/fake-invalid-arm",
        "lelamp",
        recordings,
        robot=robot,
        lock_dir=tmp_path / "locks",
    )
    runtime = MotorDemoRuntime(
        service,
        calibration_sha256=CALIBRATION_SHA256,
        home_path=tmp_path / "home.json",
    )
    runtime.connect()

    with pytest.raises(ValueError, match="goal_velocity"):
        runtime.arm(
            goal_velocity=0,
            acceleration=20,
            torque_limit=250,
            operator_confirmed=True,
        )

    assert runtime.fault is None
    assert not [entry for entry in robot.operations if entry[0] == "arm"]
    runtime.arm(
        goal_velocity=300,
        acceleration=20,
        torque_limit=250,
        operator_confirmed=True,
    )
    runtime.release(operator_confirmed=True)
    runtime.close()


def test_connect_exposes_read_only_safety_diagnostics(tmp_path):
    robot = FakeMotorRobot(actual=state(0.0))
    robot.diagnostics = SimpleNamespace(
        raw_positions={joint: 1000 for joint in JOINT_NAMES},
        normalized_positions=state(0.0),
        torque_enable={joint: int(joint == "base_yaw") for joint in JOINT_NAMES},
        operating_mode={joint: 0 for joint in JOINT_NAMES},
        issues=(),
        can_arm=False,
    )
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    service = MotorService(
        "/dev/fake-diagnostics",
        "lelamp",
        recordings,
        robot=robot,
        lock_dir=tmp_path / "locks",
    )
    runtime = MotorDemoRuntime(
        service,
        calibration_sha256=CALIBRATION_SHA256,
        home_path=tmp_path / "home.json",
    )

    diagnostics = runtime.connect()

    assert diagnostics["can_arm"] is False
    assert diagnostics["torque_enable"]["base_yaw"] == 1
    assert runtime.status()["connection_diagnostics"] == diagnostics
    assert robot.sent_actions == []
    warning = runtime.close(leave_holding=True)
    assert "仍为使能" in warning


def state(base_yaw):
    result = {key: 0.0 for key in JOINT_POSITION_KEYS}
    result["base_yaw.pos"] = float(base_yaw)
    return result


def write_recording(directory, name, rows):
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["timestamp," + ",".join(JOINT_POSITION_KEYS) + "\n"]
    for index, row in enumerate(rows):
        lines.append(
            str(index)
            + ","
            + ",".join(str(row[key]) for key in JOINT_POSITION_KEYS)
            + "\n"
        )
    (directory / f"{name}.csv").write_text("".join(lines))
