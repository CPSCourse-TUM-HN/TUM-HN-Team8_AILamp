from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import threading
import time

import pytest

from ailamp.config import load_hardware_config
from ailamp.services.controller import LampController


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config/hardware.toml"


@dataclass(frozen=True)
class Action:
    name: str
    arguments: dict


@dataclass(frozen=True)
class Plan:
    reply: str
    actions: tuple[Action, ...] = ()
    source: str = "openai"
    available: bool = True
    error: str | None = None
    model: str = "gpt-4.1-mini"


class FakeMotor:
    def __init__(self):
        self.connected = False
        self.closed = False
        self.recordings: list[str] = []
        self.deltas: list[tuple] = []
        self.stopped = 0
        self.busy = False
        self.error = None
        self.connects = 0
        self.positions = {}
        self.position_source = "initial_observation"

    def connect(self):
        self.connected = True
        self.connects += 1

    def close(self):
        self.closed = True

    def play_recording(self, name):
        self.recordings.append(name)

    def play(self, name):
        self.recordings.append(name)

    def apply_joint_deltas(self, deltas):
        self.deltas.append(tuple(deltas))
        self.busy = True
        self.positions = {f"{command.joint}.pos": command.delta_units for command in deltas}
        self.position_source = "sent_targets"
        return dict(self.positions)

    def stop_motion(self):
        self.stopped += 1

    def wait_until_idle(self, timeout=None):
        return not self.busy

    def get_positions(self):
        return dict(self.positions)

    @property
    def is_busy(self):
        return self.busy

    @property
    def last_error(self):
        return self.error


class FakeLed:
    def __init__(self):
        self.connected = False
        self.closed = False
        self.colors: list[tuple[int, int, int]] = []
        self.brightnesses: list[int] = []
        self.cleared = 0
        self.connects = 0

    def connect(self):
        self.connected = True
        self.connects += 1

    def close(self):
        self.closed = True

    def solid(self, red, green, blue):
        self.colors.append((red, green, blue))
        return "OK"

    def brightness(self, value):
        self.brightnesses.append(value)
        return "OK"

    def clear(self):
        self.cleared += 1
        return "OK"


def controller(*, with_outputs=False, motor=None, led=None):
    return LampController(
        load_hardware_config(CONFIG_PATH),
        with_outputs=with_outputs,
        motor_service=motor or FakeMotor(),
        led_service=led or FakeLed(),
    )


class ManualClock:
    def __init__(self, now=0.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class SpyPhysicalMotor(FakeMotor):
    def __init__(self):
        super().__init__()
        self.calls: list[str] = []

    def connect(self):
        self.calls.append("connect")
        super().connect()

    def close(self):
        self.calls.append("close")
        super().close()

    def play(self, name):
        self.calls.append(f"play:{name}")
        super().play(name)

    def play_recording(self, name):
        self.calls.append(f"play_recording:{name}")
        super().play_recording(name)

    def apply_joint_deltas(self, deltas):
        self.calls.append("apply_joint_deltas")
        return super().apply_joint_deltas(deltas)

    def stop_motion(self):
        self.calls.append("stop_motion")
        super().stop_motion()


class SpyPhysicalLed(FakeLed):
    def __init__(self):
        super().__init__()
        self.calls: list[str] = []

    def connect(self):
        self.calls.append("connect")
        super().connect()

    def close(self):
        self.calls.append("close")
        super().close()

    def solid(self, red, green, blue):
        self.calls.append(f"solid:{red},{green},{blue}")
        return super().solid(red, green, blue)

    def brightness(self, value):
        self.calls.append(f"brightness:{value}")
        return super().brightness(value)

    def clear(self):
        self.calls.append("clear")
        return super().clear()


def test_default_dry_run_executes_ai_plan_without_physical_send():
    motor = FakeMotor()
    led = FakeLed()
    lamp = controller(motor=motor, led=led)

    result = lamp.apply_brain_plan(
        Plan(
            "我会安静陪着你。",
            (
                Action("move_joints", {"deltas": {"base_yaw": 2.5}}),
                Action("set_light", {"red": 20, "green": 30, "blue": 40, "brightness": 90}),
            ),
        ),
        request_id="r1",
    )

    assert result.accepted is True
    assert result.sent is False
    assert result.completed is True
    assert result.dry_run is True
    assert result.reply == "我会安静陪着你。"
    assert motor.deltas == []
    assert led.colors == []
    assert lamp.snapshot()["last_outcome"]["completed"] is True
    assert lamp.snapshot()["light"] == {"red": 20, "green": 30, "blue": 40, "brightness": 90}
    assert motor.connects == 0
    assert led.connects == 0


def test_real_outputs_require_explicit_arm_before_sending():
    motor = FakeMotor()
    led = FakeLed()
    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()

    blocked = lamp.apply_brain_plan(Plan("ready", (Action("play_recording", {"name": "nod"}),)), request_id="r1")
    lamp.arm(True)
    sent = lamp.apply_brain_plan(Plan("now", (Action("play_recording", {"name": "nod"}),)), request_id="r2")

    assert blocked.accepted is True
    assert blocked.sent is False
    assert "not armed" in (blocked.error or "")
    assert sent.sent is True
    assert sent.completed is True
    assert motor.recordings == ["nod"]


def test_real_motor_action_reports_in_progress_until_motor_idle():
    motor = FakeMotor()
    led = FakeLed()
    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()
    lamp.arm(True)

    outcome = lamp.apply_brain_plan(Plan("move", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="r1")
    state = lamp.snapshot()

    assert outcome.sent is True
    assert outcome.completed is False
    assert outcome.error is None
    assert state["position_source"] == "sent_targets"
    assert state["positions"] == {"base_yaw.pos": 1.0}


def test_physical_set_mode_and_light_same_plan_is_atomic_for_generation():
    motor = FakeMotor()
    led = FakeLed()
    clock = ManualClock()
    lamp = LampController(load_hardware_config(CONFIG_PATH), with_outputs=True, motor_service=motor, led_service=led, clock=clock)
    lamp.open()
    lamp.arm(True)
    generation = lamp.snapshot()["generation"]

    outcome = lamp.apply_brain_plan(
        Plan(
            "focus",
            (
                Action("set_mode", {"mode": "focus"}),
                Action("set_light", {"red": 10, "green": 20, "blue": 30}),
            ),
        ),
        request_id="r1",
        expected_generation=generation,
        deadline=60.0,
        captured_at=0.0,
    )
    stale = lamp.apply_brain_plan(
        Plan("stale", (Action("set_light", {"red": 1, "green": 2, "blue": 3}),)),
        request_id="stale",
        expected_generation=generation,
    )

    assert outcome.accepted is True
    assert outcome.error is None
    assert outcome.completed is True
    assert lamp.snapshot()["mode"] == "focus"
    assert led.colors == [(10, 20, 30)]
    assert stale.accepted is False
    assert "stale generation" in (stale.error or "")


def test_nonfinite_ai_time_metadata_is_rejected_without_sending():
    motor = FakeMotor()
    led = FakeLed()
    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()
    lamp.arm(True)

    nan_deadline = lamp.apply_brain_plan(
        Plan("bad", (Action("set_light", {"red": 1, "green": 2, "blue": 3}),)),
        request_id="nan",
        deadline=float("nan"),
    )
    inf_deadline = lamp.apply_brain_plan(
        Plan("bad", (Action("set_light", {"red": 1, "green": 2, "blue": 3}),)),
        request_id="inf",
        deadline=float("inf"),
    )

    assert nan_deadline.sent is False
    assert inf_deadline.sent is False
    assert "finite" in (nan_deadline.error or "")
    assert "finite" in (inf_deadline.error or "")
    assert led.colors == []


def test_pending_motor_completion_survives_led_noop_and_busy_rejections():
    motor = FakeMotor()
    led = FakeLed()
    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()
    lamp.arm(True)

    move = lamp.apply_brain_plan(Plan("move", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="move")
    light = lamp.apply_brain_plan(Plan("light", (Action("set_light", {"red": 4, "green": 5, "blue": 6}),)), request_id="light")
    noop = lamp.apply_brain_plan(Plan("noop", (Action("do_nothing", {"reason": "watch"}),)), request_id="noop")
    busy = lamp.apply_brain_plan(Plan("busy", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="busy")
    motor.busy = False
    completed = lamp.poll_completion()

    assert move.sent is True
    assert move.completed is False
    assert move.error is None
    assert light.completed is True
    assert noop.completed is True
    assert busy.sent is False
    assert "motor busy" in (busy.error or "")
    assert completed is not None
    assert completed.request_id == "move"
    assert completed.completed is True
    assert completed.error is None


def test_new_motor_submission_settles_idle_pending_result_before_replacement():
    motor = FakeMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()
    lamp.arm(True)

    first = lamp.apply_brain_plan(Plan("first", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="first")
    motor.busy = False
    second = lamp.apply_brain_plan(Plan("second", (Action("move_joints", {"deltas": {"base_yaw": 2}}),)), request_id="second")
    motor.busy = False
    completed_second = lamp.poll_completion()

    assert first.completed is False
    assert second.completed is False
    assert completed_second is not None
    history = lamp.history(limit=24)
    first_terminal = _terminal_motor_history(history, "first")
    second_terminal = _terminal_motor_history(history, "second")
    assert len(first_terminal) == 1
    assert len(second_terminal) == 1
    assert first_terminal[0]["completed"] is True
    assert first_terminal[0]["action_results"] == [{"name": "move_joints", "sent": True, "completed": True}]
    assert second_terminal[0]["completed"] is True
    assert second_terminal[0]["action_results"] == [{"name": "move_joints", "sent": True, "completed": True}]


def test_new_motor_submission_preserves_previous_async_error_before_motor_error_reset():
    class ErrorResettingMotor(FakeMotor):
        def apply_joint_deltas(self, deltas):
            self.error = None
            return super().apply_joint_deltas(deltas)

    motor = ErrorResettingMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()
    lamp.arm(True)

    first = lamp.apply_brain_plan(Plan("first", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="first")
    motor.busy = False
    motor.error = RuntimeError("first motor jam")
    second = lamp.apply_brain_plan(Plan("second", (Action("move_joints", {"deltas": {"base_yaw": 2}}),)), request_id="second")
    motor.busy = False
    completed_second = lamp.poll_completion()

    assert first.error is None
    assert second.error is None
    assert completed_second is not None
    history = lamp.history(limit=24)
    first_terminal = _terminal_motor_history(history, "first")
    second_terminal = _terminal_motor_history(history, "second")
    assert len(first_terminal) == 1
    assert len(second_terminal) == 1
    assert first_terminal[0]["completed"] is False
    assert "first motor jam" in (first_terminal[0]["error"] or "")
    assert first_terminal[0]["action_results"] == [
        {"name": "move_joints", "sent": True, "completed": False, "error": "first motor jam"}
    ]
    assert second_terminal[0]["completed"] is True
    assert second_terminal[0]["error"] is None


def test_pending_motor_replacement_still_rejects_when_previous_motor_is_busy():
    motor = FakeMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()
    lamp.arm(True)

    first = lamp.apply_brain_plan(Plan("first", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="first")
    rejected = lamp.apply_brain_plan(Plan("second", (Action("move_joints", {"deltas": {"base_yaw": 2}}),)), request_id="second")
    motor.busy = False
    completed_first = lamp.poll_completion()

    assert first.completed is False
    assert rejected.sent is False
    assert "motor busy" in (rejected.error or "")
    assert completed_first is not None
    assert completed_first.request_id == "first"
    assert len(_terminal_motor_history(lamp.history(limit=24), "first")) == 1
    assert _terminal_motor_history(lamp.history(limit=24), "second") == []


def test_motor_submission_rejects_when_pending_finishes_after_settlement_busy_read():
    class FinishAfterBusyReadMotor(FakeMotor):
        def __init__(self):
            super().__init__()
            self.flip_after_read = False

        @property
        def is_busy(self):
            captured = self.busy
            if self.flip_after_read:
                self.flip_after_read = False
                self.busy = False
            return captured

    motor = FinishAfterBusyReadMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()
    lamp.arm(True)

    first = lamp.apply_brain_plan(Plan("first", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="first")
    motor.flip_after_read = True
    rejected = lamp.apply_brain_plan(Plan("second", (Action("move_joints", {"deltas": {"base_yaw": 2}}),)), request_id="second")
    motor.busy = False
    completed_first = lamp.poll_completion()

    assert first.completed is False
    assert rejected.sent is False
    assert "motor busy" in (rejected.error or "")
    assert completed_first is not None
    assert completed_first.request_id == "first"
    assert completed_first.completed is True
    assert len(_terminal_motor_history(lamp.history(limit=24), "first")) == 1
    assert _terminal_motor_history(lamp.history(limit=24), "second") == []


def test_motor_submission_rejects_when_pending_errors_after_settlement_status_read():
    class FaultAfterStatusReadMotor(FakeMotor):
        def __init__(self):
            super().__init__()
            self.flip_after_read = False
            self._fault_after_error_read = False

        @property
        def is_busy(self):
            captured = self.busy
            if self.flip_after_read:
                self.flip_after_read = False
                self.busy = False
                self._fault_after_error_read = True
            return captured

        @property
        def last_error(self):
            captured = self.error
            if self._fault_after_error_read:
                self._fault_after_error_read = False
                self.error = RuntimeError("first motor fault after busy read")
            return captured

        def apply_joint_deltas(self, deltas):
            self.error = None
            return super().apply_joint_deltas(deltas)

    motor = FaultAfterStatusReadMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()
    lamp.arm(True)

    first = lamp.apply_brain_plan(Plan("first", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="first")
    motor.flip_after_read = True
    rejected = lamp.apply_brain_plan(Plan("second", (Action("move_joints", {"deltas": {"base_yaw": 2}}),)), request_id="second")
    completed_first = lamp.poll_completion()

    assert first.completed is False
    assert rejected.sent is False
    assert "motor busy" in (rejected.error or "")
    assert completed_first is not None
    assert completed_first.request_id == "first"
    assert completed_first.completed is False
    assert "first motor fault after busy read" in (completed_first.error or "")
    assert len(_terminal_motor_history(lamp.history(limit=24), "first")) == 1
    assert _terminal_motor_history(lamp.history(limit=24), "second") == []


def test_manual_set_mode_preserves_pending_motor_tracking_when_motion_not_stopped():
    motor = FakeMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()
    lamp.arm(True)

    move = lamp.apply_brain_plan(Plan("move", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="move")
    mode = lamp.set_mode("focus")
    motor.busy = False
    completed = lamp.poll_completion()

    assert move.completed is False
    assert mode.completed is True
    assert completed is not None
    assert completed.request_id == "move"
    assert completed.completed is True


def test_deadline_expiry_after_motor_send_keeps_pending_outcome_until_idle():
    class AdvancingMotor(FakeMotor):
        def __init__(self, clock):
            super().__init__()
            self.clock = clock

        def apply_joint_deltas(self, deltas):
            result = super().apply_joint_deltas(deltas)
            self.clock.advance(2)
            return result

    clock = ManualClock(10)
    motor = AdvancingMotor(clock)
    led = FakeLed()
    lamp = LampController(load_hardware_config(CONFIG_PATH), with_outputs=True, motor_service=motor, led_service=led, clock=clock)
    lamp.open()
    lamp.arm(True)

    outcome = lamp.apply_brain_plan(
        Plan(
            "move then light",
            (
                Action("move_joints", {"deltas": {"base_yaw": 1}}),
                Action("set_light", {"red": 1, "green": 2, "blue": 3}),
            ),
        ),
        request_id="mixed",
        deadline=11,
    )
    motor.busy = False
    completed = lamp.poll_completion()

    assert outcome.sent is True
    assert outcome.completed is False
    assert "expired" in (outcome.error or "")
    assert led.colors == []
    assert completed is not None
    assert completed.request_id == "mixed"
    assert completed.completed is False
    assert "expired" in (completed.error or "")


def test_immediate_motor_error_marks_motor_action_incomplete_before_return():
    class ImmediateErrorMotor(FakeMotor):
        def apply_joint_deltas(self, deltas):
            result = super().apply_joint_deltas(deltas)
            self.busy = False
            self.error = RuntimeError("limit fault")
            return result

    lamp = controller(with_outputs=True, motor=ImmediateErrorMotor())
    lamp.open()
    lamp.arm(True)

    outcome = lamp.apply_brain_plan(Plan("move", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="move")

    assert outcome.completed is False
    assert "limit fault" in (outcome.error or "")
    assert outcome.action_results == (
        {"name": "move_joints", "sent": True, "completed": False, "error": "limit fault"},
    )


def test_motor_status_reads_busy_before_error_to_observe_terminal_fault():
    class CompletingWithFaultMotor(FakeMotor):
        def __init__(self):
            super().__init__()
            self.busy = True
            self.read_order: list[str] = []

        @property
        def is_busy(self):
            self.read_order.append("busy")
            self.busy = False
            self.error = RuntimeError("late limit fault")
            return self.busy

        @property
        def last_error(self):
            self.read_order.append("error")
            return self.error

    motor = CompletingWithFaultMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()

    state = lamp.snapshot()

    assert motor.read_order == ["busy", "error"]
    assert state["motor_busy"] is False
    assert state["motor_error"] == "late limit fault"


def test_motor_completion_poll_survives_finish_between_outcome_and_pending_registration():
    class FinishBeforePendingRegistrationMotor(FakeMotor):
        def __init__(self):
            super().__init__()
            self.busy_reads = 0

        @property
        def is_busy(self):
            if not self.deltas:
                return self.busy
            self.busy_reads += 1
            if self.busy_reads >= 3:
                self.busy = False
            return self.busy

    motor = FinishBeforePendingRegistrationMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()
    lamp.arm(True)

    outcome = lamp.apply_brain_plan(Plan("move", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="move")
    completed = lamp.poll_completion()
    repeated = lamp.poll_completion()

    assert outcome.sent is True
    assert outcome.completed is False
    assert outcome.action_results == ({"name": "move_joints", "sent": True, "completed": False},)
    assert completed is not None
    assert completed.request_id == "move"
    assert completed.completed is True
    assert completed.action_results == ({"name": "move_joints", "sent": True, "completed": True},)
    assert repeated is None


def test_motor_completion_poll_survives_early_authorization_return_after_motor_send():
    class FinishBeforeEarlyReturnRegistrationMotor(FakeMotor):
        def __init__(self, clock):
            super().__init__()
            self.clock = clock
            self.busy_reads = 0

        def apply_joint_deltas(self, deltas):
            result = super().apply_joint_deltas(deltas)
            self.clock.advance(2)
            return result

        @property
        def is_busy(self):
            if not self.deltas:
                return self.busy
            self.busy_reads += 1
            if self.busy_reads >= 2:
                self.busy = False
            return self.busy

    clock = ManualClock(10)
    motor = FinishBeforeEarlyReturnRegistrationMotor(clock)
    led = FakeLed()
    lamp = LampController(load_hardware_config(CONFIG_PATH), with_outputs=True, motor_service=motor, led_service=led, clock=clock)
    lamp.open()
    lamp.arm(True)

    outcome = lamp.apply_brain_plan(
        Plan(
            "move then light",
            (
                Action("move_joints", {"deltas": {"base_yaw": 1}}),
                Action("set_light", {"red": 1, "green": 2, "blue": 3}),
            ),
        ),
        request_id="mixed",
        deadline=11,
    )
    completed = lamp.poll_completion()

    assert outcome.sent is True
    assert outcome.completed is False
    assert "expired" in (outcome.error or "")
    assert completed is not None
    assert completed.request_id == "mixed"
    assert completed.completed is False
    assert "expired" in (completed.error or "")
    assert completed.action_results == ({"name": "move_joints", "sent": True, "completed": True},)


def test_async_motor_error_is_reported_once_and_terminal():
    motor = FakeMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()
    lamp.arm(True)

    move = lamp.apply_brain_plan(Plan("move", (Action("move_joints", {"deltas": {"base_yaw": 1}}),)), request_id="move")
    motor.error = RuntimeError("motor jam")
    errored = lamp.poll_completion()
    repeated = lamp.poll_completion()

    assert move.error is None
    assert errored is not None
    assert errored.request_id == "move"
    assert errored.completed is False
    assert "motor jam" in (errored.error or "")
    assert repeated is None


def test_mixed_motor_and_failed_led_stays_failed_after_motor_idle():
    class BadLed(FakeLed):
        def solid(self, red, green, blue):
            super().solid(red, green, blue)
            return "ERR strip offline"

    motor = FakeMotor()
    lamp = controller(with_outputs=True, motor=motor, led=BadLed())
    lamp.open()
    lamp.arm(True)

    outcome = lamp.apply_brain_plan(
        Plan(
            "mixed",
            (
                Action("move_joints", {"deltas": {"base_yaw": 1}}),
                Action("set_light", {"red": 9, "green": 8, "blue": 7}),
            ),
        ),
        request_id="mixed",
    )
    motor.busy = False
    completed = lamp.poll_completion()

    assert outcome.sent is True
    assert outcome.completed is False
    assert "ERR strip offline" in (outcome.error or "")
    assert completed is not None
    assert completed.request_id == "mixed"
    assert completed.completed is False
    assert "ERR strip offline" in (completed.error or "")


def test_poll_completion_retains_led_failure_when_motor_later_errors():
    class BadLed(FakeLed):
        def solid(self, red, green, blue):
            super().solid(red, green, blue)
            return "ERR strip offline"

    motor = FakeMotor()
    lamp = controller(with_outputs=True, motor=motor, led=BadLed())
    lamp.open()
    lamp.arm(True)

    outcome = lamp.apply_brain_plan(
        Plan(
            "mixed",
            (
                Action("move_joints", {"deltas": {"base_yaw": 1}}),
                Action("set_light", {"red": 9, "green": 8, "blue": 7}),
            ),
        ),
        request_id="mixed",
    )
    motor.error = RuntimeError("motor jam")
    completed = lamp.poll_completion()

    assert "ERR strip offline" in (outcome.error or "")
    assert completed is not None
    assert completed.completed is False
    assert "ERR strip offline" in (completed.error or "")
    assert "motor jam" in (completed.error or "")
    assert {"name": "set_light.solid", "sent": False, "completed": False, "error": "ERR strip offline"} in completed.action_results


def test_invalid_plan_is_rejected_without_partial_execution():
    motor = FakeMotor()
    led = FakeLed()
    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()
    lamp.arm(True)

    result = lamp.apply_brain_plan(
        Plan(
            "bad",
            (
                Action("set_light", {"red": 1, "green": 2, "blue": 3}),
                Action("play_recording", {"name": "../escape"}),
            ),
        ),
        request_id="r1",
    )

    assert result.accepted is False
    assert result.sent is False
    assert "recording" in (result.error or "")
    assert led.colors == []
    assert motor.recordings == []


def test_manual_override_stops_motion_disables_auto_and_invalidates_generation():
    motor = FakeMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()
    before = lamp.snapshot()["generation"]

    result = lamp.manual_play_recording("idle")

    assert result.completed is False
    assert result.sent is False
    assert "not armed" in (result.error or "")
    assert motor.stopped == 1
    state = lamp.snapshot()
    assert state["auto_enabled"] is False
    assert state["generation"] == before + 1


def test_invalid_manual_input_does_not_override_state():
    motor = FakeMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()
    before = lamp.snapshot()

    result = lamp.manual_play_recording("../bad")

    assert result.accepted is False
    assert motor.stopped == 0
    assert lamp.snapshot()["generation"] == before["generation"]
    assert lamp.snapshot()["auto_enabled"] == before["auto_enabled"]


def test_disarm_invalidates_and_stops_motion():
    motor = FakeMotor()
    lamp = controller(with_outputs=True, motor=motor)
    lamp.open()
    lamp.arm(True)

    lamp.arm(False)

    assert motor.stopped == 1
    assert lamp.snapshot()["armed"] is False


def test_led_only_action_can_run_when_motor_busy():
    motor = FakeMotor()
    led = FakeLed()
    motor.busy = True
    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()
    lamp.arm(True)

    outcome = lamp.apply_brain_plan(Plan("light", (Action("set_light", {"red": 5, "green": 6, "blue": 7}),)), request_id="r1")

    assert outcome.sent is True
    assert led.colors == [(5, 6, 7)]


def test_led_ack_errors_and_partial_brightness_failure_are_visible():
    class ScriptedLed(FakeLed):
        def __init__(self, solid_reply="OK", brightness_reply="OK"):
            super().__init__()
            self.solid_reply = solid_reply
            self.brightness_reply = brightness_reply

        def solid(self, red, green, blue):
            super().solid(red, green, blue)
            return self.solid_reply

        def brightness(self, value):
            super().brightness(value)
            return self.brightness_reply

    for reply in ("ERR bad", "", None):
        led = ScriptedLed(solid_reply=reply)
        lamp = controller(with_outputs=True, led=led)
        lamp.open()
        lamp.arm(True)

        outcome = lamp.apply_brain_plan(Plan("light", (Action("set_light", {"red": 1, "green": 2, "blue": 3}),)), request_id=str(reply))

        assert outcome.sent is False
        assert outcome.completed is False
        assert outcome.error
        assert lamp.snapshot()["light"]["red"] == 0

    led = ScriptedLed(brightness_reply="ERR dimmer")
    lamp = controller(with_outputs=True, led=led)
    lamp.open()
    lamp.arm(True)

    partial = lamp.apply_brain_plan(
        Plan("light", (Action("set_light", {"red": 7, "green": 8, "blue": 9, "brightness": 99}),)),
        request_id="partial",
    )

    assert partial.sent is True
    assert partial.completed is False
    assert "ERR dimmer" in (partial.error or "")
    assert partial.action_results == (
        {"name": "set_light.solid", "sent": True, "completed": True},
        {"name": "set_light.brightness", "sent": False, "completed": False, "error": "ERR dimmer"},
    )
    assert lamp.snapshot()["light"] == {"red": 7, "green": 8, "blue": 9, "brightness": lamp.config.led.brightness}

    led = ScriptedLed(solid_reply="OK color set", brightness_reply="OK brightness set")
    lamp = controller(with_outputs=True, led=led)
    lamp.open()
    lamp.arm(True)

    ok_message = lamp.apply_brain_plan(
        Plan("light", (Action("set_light", {"red": 3, "green": 4, "blue": 5, "brightness": 6}),)),
        request_id="ok-message",
    )

    assert ok_message.completed is True
    assert ok_message.error is None


def test_led_exceptions_preserve_successful_substeps_and_best_known_light_state():
    class BrightnessRaises(FakeLed):
        def brightness(self, value):
            super().brightness(value)
            raise RuntimeError("brightness exploded")

    class SolidRaises(FakeLed):
        def solid(self, red, green, blue):
            raise RuntimeError("solid exploded")

    class ClearRaises(FakeLed):
        def clear(self):
            raise RuntimeError("clear exploded")

    led = BrightnessRaises()
    lamp = controller(with_outputs=True, led=led)
    lamp.open()
    lamp.arm(True)

    partial = lamp.apply_brain_plan(
        Plan("light", (Action("set_light", {"red": 7, "green": 8, "blue": 9, "brightness": 99}),)),
        request_id="partial",
    )

    assert partial.sent is True
    assert partial.completed is False
    assert "brightness exploded" in (partial.error or "")
    assert partial.action_results == (
        {"name": "set_light.solid", "sent": True, "completed": True},
        {"name": "set_light.brightness", "sent": False, "completed": False, "error": "brightness exploded"},
    )
    assert lamp.snapshot()["light"] == {"red": 7, "green": 8, "blue": 9, "brightness": lamp.config.led.brightness}

    lamp = controller(with_outputs=True, led=SolidRaises())
    lamp.open()
    lamp.arm(True)
    failed_solid = lamp.apply_brain_plan(
        Plan("light", (Action("set_light", {"red": 1, "green": 2, "blue": 3}),)),
        request_id="solid",
    )

    assert failed_solid.sent is False
    assert failed_solid.completed is False
    assert failed_solid.action_results == (
        {"name": "set_light.solid", "sent": False, "completed": False, "error": "solid exploded"},
    )

    lamp = controller(with_outputs=True, led=ClearRaises())
    lamp.open()
    lamp.arm(True)
    failed_clear = lamp.clear_light()

    assert failed_clear.sent is False
    assert failed_clear.completed is False
    assert failed_clear.action_results == (
        {"name": "clear_light", "sent": False, "completed": False, "error": "clear exploded"},
    )


def test_injected_physical_objects_are_never_called_in_dry_run():
    motor = SpyPhysicalMotor()
    led = SpyPhysicalLed()
    clock = ManualClock()
    lamp = LampController(load_hardware_config(CONFIG_PATH), motor_service=motor, led_service=led, clock=clock)

    lamp.manual_play_recording("idle")
    lamp.manual_set_light(9, 8, 7, 6)
    lamp.stop()
    lamp.open()
    lamp.arm(True)
    lamp.set_auto_enabled(False)
    lamp.manual_play_recording("idle")
    lamp.manual_set_light(1, 2, 3, 4)
    lamp.clear_light()
    lamp.set_mode("focus", 1)
    clock.advance(60)
    lamp.tick_timer()
    lamp.stop()
    lamp.close()

    assert motor.calls == []
    assert led.calls == []


def test_repeated_dry_run_snapshots_keep_virtual_positions_and_clamp_units():
    lamp = controller()

    first = lamp.apply_brain_plan(Plan("move", (Action("move_joints", {"deltas": {"base_yaw": 4}}),)), request_id="r1")
    second = lamp.apply_brain_plan(Plan("move", (Action("move_joints", {"deltas": {"base_yaw": 4}}),)), request_id="r2")
    for _ in range(3):
        state = lamp.snapshot()

    assert first.completed is True
    assert second.completed is True
    assert state["position_source"] == "virtual"
    assert state["positions"]["base_yaw.pos"] == 8.0

    for _ in range(30):
        lamp.apply_brain_plan(Plan("move", (Action("move_joints", {"deltas": {"base_yaw": 4}}),)), request_id="clamp")

    assert lamp.snapshot()["positions"]["base_yaw.pos"] == 100.0


def test_timer_respects_dry_run_gate_and_updates_virtual_state():
    motor = FakeMotor()
    led = FakeLed()
    clock = ManualClock()
    lamp = LampController(load_hardware_config(CONFIG_PATH), motor_service=motor, led_service=led, clock=clock)

    start = lamp.set_mode("focus", 1)
    clock.advance(60)
    finish = lamp.tick_timer()

    assert start.completed is True
    assert finish is not None
    assert finish.sent is False
    assert lamp.snapshot()["mode"] == "rest"
    assert motor.deltas == []
    assert led.colors == []


def test_auto_toggle_preserves_explicit_focus_timer_and_dry_run_ai_mode_bumps_generation():
    clock = ManualClock()
    lamp = LampController(load_hardware_config(CONFIG_PATH), clock=clock)

    lamp.manual_set_light(1, 2, 3, 4)
    timer = lamp.set_mode("focus", 1)
    timer_generation = lamp.snapshot()["generation"]
    lamp.set_auto_enabled(False)
    lamp.set_auto_enabled(True)
    clock.advance(60)
    finish = lamp.tick_timer()

    assert timer.completed is True
    assert finish is not None
    assert finish.request_id == "focus_timer"
    assert lamp.snapshot()["mode"] == "rest"
    assert lamp.snapshot()["light"] == {"red": 40, "green": 30, "blue": 20, "brightness": lamp.config.led.brightness}

    stale = lamp.apply_brain_plan(
        Plan("stale", (Action("set_mode", {"mode": "manual"}),)),
        request_id="stale",
        expected_generation=timer_generation,
    )

    assert stale.accepted is False
    assert "stale generation" in (stale.error or "")


def test_ai_mode_without_explicit_timer_clears_old_focus_timer():
    clock = ManualClock()
    lamp = LampController(load_hardware_config(CONFIG_PATH), clock=clock)

    lamp.set_mode("focus", 1)
    generation = lamp.snapshot()["generation"]
    outcome = lamp.apply_brain_plan(
        Plan("focus", (Action("set_mode", {"mode": "focus"}),)),
        request_id="ai-focus",
        expected_generation=generation,
    )
    clock.advance(60)

    assert outcome.completed is True
    assert lamp.tick_timer() is None


def test_focus_timer_is_cancelled_by_manual_light_stop_and_disarm():
    clock = ManualClock()
    led = FakeLed()
    lamp = LampController(load_hardware_config(CONFIG_PATH), with_outputs=True, motor_service=FakeMotor(), led_service=led, clock=clock)
    lamp.open()
    lamp.arm(True)
    lamp.set_mode("focus", 1)

    lamp.manual_set_light(1, 2, 3)
    clock.advance(60)

    assert lamp.tick_timer() is None
    assert led.colors == [(1, 2, 3)]

    lamp.set_mode("focus", 1)
    lamp.stop()
    clock.advance(60)

    assert lamp.tick_timer() is None

    lamp.arm(True)
    lamp.set_mode("focus", 1)
    lamp.arm(False)
    clock.advance(60)

    assert lamp.tick_timer() is None


def test_focus_timer_never_sends_when_unarmed():
    clock = ManualClock()
    led = FakeLed()
    lamp = LampController(load_hardware_config(CONFIG_PATH), with_outputs=True, motor_service=FakeMotor(), led_service=led, clock=clock)
    lamp.open()
    lamp.arm(True)
    lamp.set_mode("focus", 1)
    lamp.arm(False)
    clock.advance(60)

    outcome = lamp.tick_timer()

    assert outcome is None
    assert led.colors == []


def test_expected_generation_and_deadline_checked_at_dispatch_boundary():
    motor = FakeMotor()
    led = FakeLed()
    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()
    lamp.arm(True)
    generation = lamp.snapshot()["generation"]
    lamp.stop()

    outcome = lamp.apply_brain_plan(
        Plan("late", (Action("set_light", {"red": 1, "green": 2, "blue": 3}),)),
        request_id="late",
        expected_generation=generation,
        deadline=999.0,
    )

    assert outcome.accepted is False
    assert "stale generation" in (outcome.error or "")
    assert led.colors == []


def test_concurrent_late_brain_response_after_stop_never_sends():
    class BlockingPlan:
        def __init__(self):
            self.reply = "late"
            self.actions = (Action("play_recording", {"name": "nod"}),)
            self.available = True
            self.source = "openai"
            self.model = "gpt-4.1-mini"
            self.error = None

    motor = FakeMotor()
    led = FakeLed()
    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()
    lamp.arm(True)
    generation = lamp.snapshot()["generation"]
    release = threading.Event()
    result = {}

    def worker():
        release.wait(1)
        result["outcome"] = lamp.apply_brain_plan(
            BlockingPlan(),
            request_id="r1",
            expected_generation=generation,
            deadline=999.0,
        )

    thread = threading.Thread(target=worker)
    thread.start()
    lamp.stop()
    release.set()
    thread.join(1)

    assert result["outcome"].sent is False
    assert motor.recordings == []


def test_stop_invalidates_generation_disarms_and_attempts_all_cleanup():
    class BadMotor(FakeMotor):
        def close(self):
            super().close()
            raise RuntimeError("motor close failed")

    class BadLed(FakeLed):
        def close(self):
            super().close()
            raise RuntimeError("led close failed")

    motor = BadMotor()
    led = BadLed()
    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()
    lamp.arm(True)

    stop = lamp.stop()
    errors = lamp.close()

    assert stop.completed is True
    assert lamp.snapshot()["armed"] is False
    assert motor.closed is True
    assert led.closed is True
    assert len(errors) == 2


def test_physical_outputs_require_successful_open_and_close_blocks_new_dispatch():
    class BlockingLed(FakeLed):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def close(self):
            self.entered.set()
            self.release.wait(1)
            super().close()

    motor = FakeMotor()
    led = BlockingLed()
    unopened = controller(with_outputs=True, motor=FakeMotor(), led=FakeLed())

    unopened_arm = unopened.arm(True)
    unopened_send = unopened.apply_brain_plan(Plan("light", (Action("set_light", {"red": 1, "green": 2, "blue": 3}),)), request_id="unopened")

    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()
    lamp.arm(True)
    close_result = {}
    thread = threading.Thread(target=lambda: close_result.setdefault("errors", lamp.close()))
    thread.start()
    assert led.entered.wait(1)

    arm_during_close = lamp.arm(True)
    send_during_close = lamp.manual_set_light(1, 2, 3)
    led.release.set()
    thread.join(1)

    assert unopened_arm["armed"] is False
    assert "open" in (unopened_arm.get("lifecycle_error") or "")
    assert unopened_send.sent is False
    assert "open" in (unopened_send.error or "")
    assert arm_during_close["armed"] is False
    assert "closing" in (arm_during_close.get("lifecycle_state") or "")
    assert send_during_close.sent is False
    assert "closing" in (send_during_close.error or "")
    assert close_result["errors"] == []


def test_close_latches_state_while_open_connect_is_blocked_and_final_state_stays_closed():
    class BlockingMotor(FakeMotor):
        def __init__(self, events):
            super().__init__()
            self.events = events
            self.entered = threading.Event()
            self.release = threading.Event()

        def connect(self):
            self.events.append("motor.connect.enter")
            self.entered.set()
            self.release.wait(1)
            self.events.append("motor.connect.exit")
            super().connect()

        def close(self):
            self.events.append("motor.close")
            super().close()

    events: list[str] = []
    motor = BlockingMotor(events)
    led = SpyPhysicalLed()
    lamp = controller(with_outputs=True, motor=motor, led=led)
    open_result = {}

    open_thread = threading.Thread(target=lambda: open_result.setdefault("error", _capture_error(lamp.open)))
    open_thread.start()
    assert motor.entered.wait(1)

    close_result = {}
    close_thread = threading.Thread(target=lambda: close_result.setdefault("errors", lamp.close()))
    close_thread.start()

    deadline = time.monotonic() + 1
    while lamp.snapshot()["lifecycle_state"] != "closing" and time.monotonic() < deadline:
        time.sleep(0.001)

    assert lamp.snapshot()["lifecycle_state"] == "closing"
    assert lamp.arm(True)["armed"] is False
    denied = lamp.manual_set_light(1, 2, 3)
    assert denied.sent is False
    assert "closing" in (denied.error or "")

    motor.release.set()
    open_thread.join(1)
    close_thread.join(1)

    assert not open_thread.is_alive()
    assert not close_thread.is_alive()
    assert close_result["errors"] == []
    assert lamp.snapshot()["lifecycle_state"] == "closed"
    assert lamp.arm(True)["armed"] is False
    assert "open" in (lamp.snapshot()["lifecycle_error"] or "")
    assert events.index("motor.connect.exit") < events.index("motor.close")

    lamp.open()
    assert lamp.arm(True)["armed"] is True


def test_concurrent_close_waits_without_duplicate_cleanup():
    class BlockingCloseLed(FakeLed):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()
            self.close_count = 0

        def close(self):
            self.close_count += 1
            self.entered.set()
            self.release.wait(1)
            super().close()

    led = BlockingCloseLed()
    motor = FakeMotor()
    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()

    first = {}
    second = {}
    first_thread = threading.Thread(target=lambda: first.setdefault("errors", lamp.close()))
    first_thread.start()
    assert led.entered.wait(1)
    second_thread = threading.Thread(target=lambda: second.setdefault("errors", lamp.close()))
    second_thread.start()
    led.release.set()
    first_thread.join(1)
    second_thread.join(1)

    assert first["errors"] == []
    assert second["errors"] == []
    assert led.close_count == 1
    assert motor.closed is True


def test_close_failure_retains_resources_allows_retry_then_reopen():
    class RetryLed(FakeLed):
        def __init__(self):
            super().__init__()
            self.fail = True

        def close(self):
            if self.fail:
                self.fail = False
                raise RuntimeError("led close failed")
            super().close()

    motor = FakeMotor()
    led = RetryLed()
    lamp = controller(with_outputs=True, motor=motor, led=led)
    lamp.open()
    lamp.arm(True)

    first_errors = lamp.close()
    blocked = lamp.arm(True)
    with pytest.raises(RuntimeError, match="cleanup incomplete"):
        lamp.open()
    second_errors = lamp.close()
    lamp.open()

    assert first_errors == ["led close failed"]
    assert blocked["armed"] is False
    assert blocked.get("lifecycle_error") == "controller_cleanup_failed"
    assert second_errors == []
    assert lamp.snapshot()["lifecycle_state"] == "open"
    assert motor.connects == 2
    assert led.connects == 2


def test_open_connect_failure_lifecycle_outputs_use_stable_code_without_exception_text():
    secret = "SECRET_TOKEN:/Users/example/private/device.env"

    class ConnectFailsLed(FakeLed):
        def connect(self):
            raise RuntimeError(f"connect boom {secret}")

    lamp = controller(with_outputs=True, led=ConnectFailsLed())

    with pytest.raises(RuntimeError, match="connect boom"):
        lamp.open()
    snapshot = lamp.snapshot()
    blocked_arm = lamp.arm(True)
    blocked_dispatch = lamp.manual_set_light(1, 2, 3)

    assert snapshot["lifecycle_error"] == "controller_open_failed"
    assert blocked_arm["lifecycle_error"] == "controller_not_open"
    assert blocked_dispatch.error == "controller_not_open"
    assert secret not in json.dumps(snapshot)
    assert secret not in json.dumps(blocked_arm)
    assert secret not in json.dumps(blocked_dispatch.to_dict())


def test_failed_current_service_connect_is_cleaned_and_cleanup_failure_blocks_reopen_until_retry():
    secret = "SECRET_TOKEN:/Users/example/private/device.env"

    class PartiallyFailingLed(FakeLed):
        def __init__(self):
            super().__init__()
            self.fail_connect = True
            self.fail_close = True
            self.close_count = 0

        def connect(self):
            self.connects += 1
            self.connected = True
            if self.fail_connect:
                self.fail_connect = False
                raise RuntimeError(f"connect boom {secret}")

        def close(self):
            self.close_count += 1
            if self.fail_close:
                self.fail_close = False
                raise RuntimeError(f"cleanup boom {secret}")
            super().close()

    led = PartiallyFailingLed()
    lamp = controller(with_outputs=True, led=led)

    with pytest.raises(RuntimeError, match="connect boom.*cleanup boom"):
        lamp.open()
    failed_snapshot = lamp.snapshot()
    blocked_arm = lamp.arm(True)
    blocked_dispatch = lamp.manual_set_light(1, 2, 3)
    with pytest.raises(RuntimeError, match="cleanup incomplete"):
        lamp.open()

    retry_errors = lamp.close()
    lamp.open()

    assert led.close_count == 2
    assert retry_errors == []
    assert failed_snapshot["lifecycle_error"] == "controller_cleanup_failed"
    assert blocked_arm["lifecycle_error"] == "controller_cleanup_failed"
    assert blocked_dispatch.error == "controller_cleanup_failed"
    assert secret not in json.dumps(failed_snapshot)
    assert secret not in json.dumps(blocked_arm)
    assert secret not in json.dumps(blocked_dispatch.to_dict())
    assert lamp.snapshot()["lifecycle_state"] == "open"


def test_open_refresh_failure_does_not_leave_controller_open_or_fabricate_position_source():
    class RefreshFailsMotor(FakeMotor):
        def __init__(self):
            super().__init__()
            self.position_source = None

        def get_positions(self):
            raise RuntimeError("refresh boom")

    bad = controller(with_outputs=True, motor=RefreshFailsMotor())

    with pytest.raises(RuntimeError, match="refresh boom"):
        bad.open()

    assert bad.snapshot()["lifecycle_state"] == "closed"
    assert bad.arm(True)["armed"] is False

    class NoSourceMotor(FakeMotor):
        def __init__(self):
            super().__init__()
            self.position_source = None

        def get_positions(self):
            return {}

    lamp = controller(with_outputs=True, motor=NoSourceMotor())
    lamp.open()

    assert lamp.snapshot()["position_source"] == "unknown"


def _capture_error(func):
    try:
        func()
    except Exception as exc:  # noqa: BLE001 - thread helper returns assertion-visible errors.
        return exc
    return None


def _terminal_motor_history(history, request_id):
    return [
        record
        for record in history
        if record["request_id"] == request_id
        and any(result.get("name") in {"play_recording", "move_joints"} for result in record.get("action_results", ()))
        and (record.get("completed") is True or record.get("error") is not None)
    ]
