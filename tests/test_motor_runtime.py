import threading
import time

import pytest

from ailamp.services.motor import JointDeltaCommand, MotorService


def test_connect_reads_true_observed_state_without_initial_motion():
    robot = FakeRobot(observation=observed_state(base_yaw=22.0))
    service = MotorService("/dev/null", "ailamp", "ailamp_runtime/ailamp/recordings", robot=robot)

    service.connect()

    assert service.current_state == observed_state(base_yaw=22.0)
    assert service.get_positions() == observed_state(base_yaw=22.0)
    assert service.positions == observed_state(base_yaw=22.0)
    assert service.position_source == "initial_observation"
    positions = service.positions
    positions["base_yaw.pos"] = -99.0
    assert service.positions == observed_state(base_yaw=22.0)
    assert robot.sent_actions == []
    service.close()


def test_connect_rejects_invalid_observation():
    observation = observed_state()
    observation.pop("wrist_pitch.pos")
    service = MotorService(
        "/dev/null",
        "ailamp",
        "ailamp_runtime/ailamp/recordings",
        robot=FakeRobot(observation=observation),
    )

    with pytest.raises(RuntimeError, match="observation"):
        service.connect()


def test_delta_commands_are_sent_by_worker_and_are_bounded():
    robot = FakeRobot(observation=observed_state(base_yaw=0.0))
    service = MotorService(
        "/dev/null",
        "ailamp",
        "ailamp_runtime/ailamp/recordings",
        robot=robot,
        fps=1000,
        max_step_units=4,
    )

    service.connect()
    target = service.apply_joint_deltas([JointDeltaCommand("base_yaw", 10.0)])

    assert target["base_yaw.pos"] == 10.0
    assert service.wait_until_idle(1.0)
    sent_yaw = [action["base_yaw.pos"] for action in robot.sent_actions]
    assert sent_yaw[-1] == 10.0
    assert all(abs(next_value - value) <= 4.0 for value, next_value in zip([0.0] + sent_yaw, sent_yaw))
    assert {thread_id for _, thread_id in robot.send_threads} == {service.worker_thread_id}
    service.close()


def test_play_replaces_active_playback(tmp_path):
    write_recording(tmp_path, "slow", [0.0, 20.0, 40.0, 60.0])
    write_recording(tmp_path, "replacement", [-5.0])
    robot = BlockingRobot(observation=observed_state(base_yaw=0.0), block_after=1)
    service = MotorService(
        "/dev/null",
        "ailamp",
        tmp_path,
        robot=robot,
        fps=1000,
        max_step_units=100,
    )

    service.connect()
    service.play("slow")
    robot.wait_for_sends(1)
    service.play("replacement")
    robot.release()

    assert service.wait_until_idle(1.0)
    assert robot.sent_actions[-1]["base_yaw.pos"] == -5.0
    assert 60.0 not in [action["base_yaw.pos"] for action in robot.sent_actions]
    service.close()


def test_stop_motion_cancels_future_commands_and_waits_for_in_flight_send(tmp_path):
    write_recording(tmp_path, "move", [0.0, 20.0, 40.0])
    robot = BlockingRobot(observation=observed_state(base_yaw=0.0), block_after=1)
    service = MotorService(
        "/dev/null",
        "ailamp",
        tmp_path,
        robot=robot,
        fps=1000,
        max_step_units=100,
    )

    service.connect()
    service.play("move")
    robot.wait_for_sends(1)
    stop_thread = threading.Thread(target=service.stop_motion)
    stop_thread.start()
    time.sleep(0.02)
    assert stop_thread.is_alive()

    robot.release()
    stop_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    sent_at_stop = len(robot.sent_actions)
    time.sleep(0.05)
    assert len(robot.sent_actions) == sent_at_stop
    assert service.wait_until_idle(0.1)
    service.close()


def test_stop_motion_waits_for_clipped_state_before_late_delta(tmp_path):
    robot = BlockingClippingRobot(
        observation=observed_state(base_yaw=0.0),
        clipped_returns=[observed_state(base_yaw=2.0)],
        block_after=1,
    )
    service = MotorService(
        "/dev/null",
        "ailamp",
        tmp_path,
        robot=robot,
        fps=1000,
        max_step_units=4,
    )

    service.connect()
    service.apply_joint_deltas([JointDeltaCommand("base_yaw", 10.0)])
    robot.wait_for_sends(1)
    stop_thread = threading.Thread(target=service.stop_motion)
    stop_thread.start()
    time.sleep(0.02)
    assert stop_thread.is_alive()

    robot.release()
    stop_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert service.positions["base_yaw.pos"] == 2.0
    assert service.position_source == "sent_targets"

    target = service.apply_joint_deltas([JointDeltaCommand("base_yaw", 1.0)])
    assert target["base_yaw.pos"] == 3.0
    assert service.wait_until_idle(1.0)
    assert robot.sent_actions[-1]["base_yaw.pos"] == 3.0
    service.close()


def test_replacement_recomputes_bounded_steps_from_returned_clipped_state(tmp_path):
    robot = BlockingClippingRobot(
        observation=observed_state(base_yaw=0.0),
        clipped_returns=[observed_state(base_yaw=2.0)],
        block_after=1,
    )
    service = MotorService(
        "/dev/null",
        "ailamp",
        tmp_path,
        robot=robot,
        fps=1000,
        max_step_units=4,
    )

    service.connect()
    service.apply_joint_deltas([JointDeltaCommand("base_yaw", 10.0)])
    robot.wait_for_sends(1)
    service.apply_joint_deltas([JointDeltaCommand("base_yaw", 20.0)])
    robot.release()

    assert service.wait_until_idle(1.0)
    sent_yaw = [action["base_yaw.pos"] for action in robot.sent_actions]
    assert sent_yaw[0] == 4.0
    assert sent_yaw[1] <= 6.0
    assert service.positions["base_yaw.pos"] == 22.0
    service.close()


def test_send_exception_stays_visible_and_blocks_resume():
    robot = FailingRobot(observation=observed_state(base_yaw=0.0))
    service = MotorService("/dev/null", "ailamp", "ailamp_runtime/ailamp/recordings", robot=robot)

    service.connect()
    service.apply_joint_deltas([JointDeltaCommand("base_yaw", 1.0)])

    assert service.wait_until_idle(1.0)
    assert isinstance(service.last_error, RuntimeError)
    with pytest.raises(RuntimeError, match="blocked by previous motor send error"):
        service.apply_joint_deltas([JointDeltaCommand("base_yaw", 1.0)])
    service.close()


def test_send_exception_latches_even_when_generation_was_replaced(tmp_path):
    robot = BlockingFailingRobot(observation=observed_state(base_yaw=0.0), block_after=1)
    service = MotorService(
        "/dev/null",
        "ailamp",
        tmp_path,
        robot=robot,
        fps=1000,
        max_step_units=100,
    )

    service.connect()
    service.apply_joint_deltas([JointDeltaCommand("base_yaw", 1.0)])
    robot.wait_for_sends(1)
    service.apply_joint_deltas([JointDeltaCommand("base_yaw", 2.0)])
    robot.release()

    assert service.wait_until_idle(1.0)
    assert isinstance(service.last_error, RuntimeError)
    assert len(robot.send_attempts) == 1
    with pytest.raises(RuntimeError, match="blocked by previous motor send error"):
        service.play("idle")
    service.close()


def test_stop_motion_observes_send_failure_before_returning(tmp_path):
    robot = BlockingFailingRobot(observation=observed_state(base_yaw=0.0), block_after=1)
    service = MotorService(
        "/dev/null",
        "ailamp",
        tmp_path,
        robot=robot,
        fps=1000,
        max_step_units=100,
    )

    service.connect()
    service.apply_joint_deltas([JointDeltaCommand("base_yaw", 1.0)])
    robot.wait_for_sends(1)
    stop_thread = threading.Thread(target=service.stop_motion)
    stop_thread.start()
    time.sleep(0.02)

    robot.release()
    stop_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert isinstance(service.last_error, RuntimeError)
    assert "serial write failed" in str(service.last_error)
    with pytest.raises(RuntimeError, match="blocked by previous motor send error"):
        service.apply_joint_deltas([JointDeltaCommand("base_yaw", 1.0)])
    service.close()


def test_unchanged_clipped_return_surfaces_error_instead_of_replay_loop():
    robot = StuckRobot(observation=observed_state(base_yaw=0.0))
    service = MotorService(
        "/dev/null",
        "ailamp",
        "ailamp_runtime/ailamp/recordings",
        robot=robot,
        fps=1000,
        max_step_units=4,
    )

    service.connect()
    service.apply_joint_deltas([JointDeltaCommand("base_yaw", 10.0)])

    assert service.wait_until_idle(1.0)
    assert isinstance(service.last_error, RuntimeError)
    assert "did not change" in str(service.last_error)
    assert len(robot.sent_actions) == 1
    service.close()


def test_recording_repeat_frames_preserve_hold_duration_without_extra_sends(tmp_path):
    write_recording(tmp_path, "hold", [0.0, 0.0, 0.0])
    robot = FakeRobot(observation=observed_state(base_yaw=0.0))
    service = MotorService(
        "/dev/null",
        "ailamp",
        tmp_path,
        robot=robot,
        fps=10,
        max_step_units=100,
    )

    service.connect()
    started = time.monotonic()
    service.play("hold")
    time.sleep(0.12)

    assert service.is_busy
    assert robot.sent_actions == []
    assert service.wait_until_idle(1.0)
    assert time.monotonic() - started >= 0.28
    assert robot.sent_actions == []
    service.close()


def test_paused_recording_load_is_not_published_after_stop(tmp_path):
    robot = FakeRobot(observation=observed_state(base_yaw=0.0))
    store = BlockingRecordingStore([observed_state(base_yaw=10.0)])
    service = MotorService(
        "/dev/null",
        "ailamp",
        tmp_path,
        robot=robot,
        fps=1000,
        max_step_units=100,
    )

    service.connect()
    service.recordings = store
    play_thread = threading.Thread(target=service.play, args=("move",))
    play_thread.start()
    assert store.wait_for_load()

    service.stop_motion()
    store.release()
    play_thread.join(timeout=1.0)

    assert not play_thread.is_alive()
    time.sleep(0.05)
    assert robot.sent_actions == []
    assert service.wait_until_idle(0.1)
    service.close()


def test_reset_error_requires_close_and_reconnect_after_send_failure():
    robot = FailingRobot(observation=observed_state(base_yaw=0.0))
    service = MotorService("/dev/null", "ailamp", "ailamp_runtime/ailamp/recordings", robot=robot)

    service.connect()
    service.apply_joint_deltas([JointDeltaCommand("base_yaw", 1.0)])
    assert service.wait_until_idle(1.0)

    with pytest.raises(RuntimeError, match="close and reconnect"):
        service.reset_error()
    service.close()


def test_close_rejects_late_commands_and_disconnects_after_worker_exits(tmp_path):
    write_recording(tmp_path, "move", [10.0])
    robot = BlockingRobot(observation=observed_state(base_yaw=0.0), block_after=1)
    service = MotorService(
        "/dev/null",
        "ailamp",
        tmp_path,
        robot=robot,
        fps=1000,
        max_step_units=100,
    )

    service.connect()
    service.play("move")
    robot.wait_for_sends(1)
    close_thread = threading.Thread(target=service.close)
    close_thread.start()
    time.sleep(0.02)

    assert close_thread.is_alive()
    assert robot.disconnected is False
    with pytest.raises(RuntimeError, match="closing"):
        service.play("move")
    with pytest.raises(RuntimeError, match="closing"):
        service.apply_joint_deltas([JointDeltaCommand("base_yaw", 1.0)])

    robot.release()
    close_thread.join(timeout=1.0)
    assert not close_thread.is_alive()
    assert robot.disconnected is True


def test_concurrent_close_disconnects_injected_robot_once(tmp_path):
    robot = BlockingDisconnectRobot(observation=observed_state(base_yaw=0.0))
    service = MotorService(
        "/dev/null",
        "ailamp",
        tmp_path,
        robot=robot,
        fps=1000,
        max_step_units=100,
    )

    service.connect()
    first_close = threading.Thread(target=service.close)
    second_close = threading.Thread(target=service.close)
    first_close.start()
    second_close.start()
    assert robot.wait_for_disconnects(1)

    robot.release_disconnect()
    first_close.join(timeout=1.0)
    second_close.join(timeout=1.0)
    service.close()

    assert not first_close.is_alive()
    assert not second_close.is_alive()
    assert robot.disconnect_calls == 1


class FakeRobot:
    def __init__(self, observation):
        self.observation = observation
        self.is_calibrated = True
        self.sent_actions = []
        self.send_threads = []
        self.disconnected = False

    def connect(self, calibrate=False):
        assert calibrate is False

    def get_observation(self):
        return self.observation.copy()

    def send_action(self, action):
        clipped = action.copy()
        self.sent_actions.append(clipped)
        self.send_threads.append((clipped, threading.get_ident()))
        return clipped

    def disconnect(self):
        self.disconnected = True


class BlockingRobot(FakeRobot):
    def __init__(self, observation, *, block_after):
        super().__init__(observation)
        self.block_after = block_after
        self._send_count = 0
        self._sent = threading.Condition()
        self._release = threading.Event()

    def send_action(self, action):
        with self._sent:
            self._send_count += 1
            self._sent.notify_all()
        if self._send_count >= self.block_after:
            self._release.wait(timeout=1.0)
        return super().send_action(action)

    def wait_for_sends(self, count):
        deadline = time.monotonic() + 1.0
        with self._sent:
            while self._send_count < count:
                remaining = deadline - time.monotonic()
                assert remaining > 0
                self._sent.wait(remaining)

    def release(self):
        self._release.set()


class BlockingDisconnectRobot(FakeRobot):
    def __init__(self, observation):
        super().__init__(observation)
        self.disconnect_calls = 0
        self._disconnects = threading.Condition()
        self._release_disconnect = threading.Event()

    def disconnect(self):
        with self._disconnects:
            self.disconnect_calls += 1
            self._disconnects.notify_all()
        self._release_disconnect.wait(timeout=1.0)
        self.disconnected = True

    def wait_for_disconnects(self, count):
        deadline = time.monotonic() + 1.0
        with self._disconnects:
            while self.disconnect_calls < count:
                remaining = deadline - time.monotonic()
                assert remaining > 0
                self._disconnects.wait(remaining)
        return True

    def release_disconnect(self):
        self._release_disconnect.set()


class BlockingRecordingStore:
    def __init__(self, rows):
        self.rows = rows
        self._started = threading.Event()
        self._release = threading.Event()

    def load(self, name):
        assert name == "move"
        self._started.set()
        self._release.wait(timeout=1.0)
        return [row.copy() for row in self.rows]

    def wait_for_load(self):
        return self._started.wait(timeout=1.0)

    def release(self):
        self._release.set()


class BlockingClippingRobot(BlockingRobot):
    def __init__(self, observation, *, clipped_returns, block_after):
        super().__init__(observation, block_after=block_after)
        self.clipped_returns = list(clipped_returns)

    def send_action(self, action):
        with self._sent:
            self._send_count += 1
            self._sent.notify_all()
        if self._send_count >= self.block_after:
            self._release.wait(timeout=1.0)
        clipped = self.clipped_returns.pop(0) if self.clipped_returns else action.copy()
        self.sent_actions.append(action.copy())
        self.send_threads.append((action.copy(), threading.get_ident()))
        return clipped.copy()


class FailingRobot(FakeRobot):
    def send_action(self, action):
        raise RuntimeError("serial write failed")


class BlockingFailingRobot(BlockingRobot):
    def __init__(self, observation, *, block_after):
        super().__init__(observation, block_after=block_after)
        self.send_attempts = []

    def send_action(self, action):
        with self._sent:
            self._send_count += 1
            self.send_attempts.append(action.copy())
            self._sent.notify_all()
        if self._send_count >= self.block_after:
            self._release.wait(timeout=1.0)
        raise RuntimeError("serial write failed")


class StuckRobot(FakeRobot):
    def send_action(self, action):
        self.sent_actions.append(action.copy())
        return self.observation.copy()


def observed_state(**overrides):
    state = {
        "base_yaw.pos": 0.0,
        "base_pitch.pos": 0.0,
        "elbow_pitch.pos": 0.0,
        "wrist_roll.pos": 0.0,
        "wrist_pitch.pos": 0.0,
    }
    for joint, value in overrides.items():
        state[f"{joint}.pos"] = value
    return state


def write_recording(directory, name, base_yaw_values):
    rows = [
        "timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos\n"
    ]
    for index, value in enumerate(base_yaw_values):
        rows.append(f"{index},{value},0,0,0,0\n")
    (directory / f"{name}.csv").write_text("".join(rows))
