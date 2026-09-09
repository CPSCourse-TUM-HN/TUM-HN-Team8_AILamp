from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any

import pytest

from ailamp.config import load_hardware_config
from ailamp.services.brain_runtime import BrainRuntime
from ailamp.services.controller import ExecutionOutcome, LampController


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config/hardware.toml"
JPEG_A = b"\xff\xd8frame-a\xff\xd9"
JPEG_B = b"\xff\xd8frame-b\xff\xd9"


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


class FakeClock:
    def __init__(self, start: float = 0.0):
        self._lock = threading.Lock()
        self._value = float(start)

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def set(self, value: float) -> None:
        with self._lock:
            self._value = float(value)

    def advance(self, delta: float) -> None:
        with self._lock:
            self._value += float(delta)


class FakeBrain:
    def __init__(self, plan: Plan | None = None):
        self.calls: list[dict[str, Any]] = []
        self.plan = plan or Plan("好的", (Action("set_light", {"red": 7, "green": 8, "blue": 9}),))
        self.entered = threading.Event()
        self.release: threading.Event | None = None
        self.raise_error: BaseException | None = None

    def decide(self, *, user_text=None, frame_jpeg=None, state=None, history=()):
        self.calls.append({"user_text": user_text, "frame_jpeg": frame_jpeg, "state": state, "history": tuple(history)})
        self.entered.set()
        if self.release is not None:
            self.release.wait(5)
        if self.raise_error is not None:
            raise self.raise_error
        return self.plan


class FakeCamera:
    def __init__(self, *, captured_at: float = 10.1, frames: list[bytes] | None = None):
        self.opened = False
        self.closed = False
        self.close_count = 0
        self.frames = list(frames if frames is not None else [JPEG_A])
        self.captured_at_values = [captured_at]
        self.captures = 0
        self.max_px_values: list[int] = []
        self.entered = threading.Event()
        self.release: threading.Event | None = None
        self.open_error: BaseException | None = None

    def open(self):
        if self.open_error is not None:
            raise self.open_error
        self.opened = True

    def capture_jpeg(self, max_px=512):
        self.captures += 1
        self.max_px_values.append(max_px)
        self.entered.set()
        if self.release is not None:
            self.release.wait(5)
        frame = self.frames.pop(0) if self.frames else None
        if frame is None:
            raise RuntimeError("no camera frame")
        captured_at = self.captured_at_values.pop(0) if self.captured_at_values else 10.1
        return frame, captured_at

    def close(self):
        self.closed = True
        self.close_count += 1


class FakeController:
    def __init__(self, config, *, clock: FakeClock):
        self.config = config
        self.with_outputs = False
        self.recording_names = ("nod",)
        self.clock = clock
        self._lock = threading.RLock()
        self.generation = 0
        self.auto_enabled = False
        self.open_count = 0
        self.close_count = 0
        self.stop_count = 0
        self.tick_count = 0
        self.poll_count = 0
        self.applied: list[dict[str, Any]] = []
        self._history: list[dict[str, Any]] = []
        self.apply_entered = threading.Event()
        self.apply_release: threading.Event | None = None
        self.auto_enable_entered = threading.Event()
        self.auto_enable_release: threading.Event | None = None
        self.open_error: BaseException | None = None
        self.close_errors: list[str] = []
        self.stop_entered = threading.Event()

    def open(self):
        if self.open_error is not None:
            raise self.open_error
        self.open_count += 1

    def close(self) -> list[str]:
        self.close_count += 1
        return list(self.close_errors)

    def arm(self, enabled: bool):
        with self._lock:
            self.generation += 1
            if not enabled:
                self.auto_enabled = False
            return self.snapshot()

    def set_auto_enabled(self, enabled: bool):
        if enabled:
            self.auto_enable_entered.set()
            if self.auto_enable_release is not None:
                self.auto_enable_release.wait(5)
        with self._lock:
            self.generation += 1
            self.auto_enabled = bool(enabled)
            return self.snapshot()

    def manual_override(self):
        with self._lock:
            self.generation += 1
            self.auto_enabled = False

    def stop(self, request_id: str = "stop") -> ExecutionOutcome:
        self.stop_entered.set()
        with self._lock:
            self.generation += 1
            self.auto_enabled = False
            self.stop_count += 1
            outcome = ExecutionOutcome(request_id, True, False, True, True)
            self._record(outcome)
            return outcome

    def tick_timer(self):
        self.tick_count += 1
        return None

    def poll_completion(self):
        self.poll_count += 1
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "with_outputs": False,
            "armed": True,
            "auto_enabled": self.auto_enabled,
            "mode": "manual",
            "generation": self.generation,
            "recordings": list(self.recording_names),
            "position_source": "virtual",
            "positions": {},
            "light": {"red": 0, "green": 0, "blue": 0, "brightness": self.config.led.brightness},
            "focus_remaining_s": None,
            "motor_busy": False,
            "motor_error": None,
            "last_plan": None,
            "last_outcome": None if not self._history else self._history[-1],
        }

    def history(self, limit: int = 12):
        return tuple(self._history[-limit:])

    def apply_brain_plan(
        self,
        plan: object,
        *,
        request_id: str,
        expected_generation: int | None = None,
        deadline: float | None = None,
        captured_at: float | None = None,
    ) -> ExecutionOutcome:
        self.apply_entered.set()
        if self.apply_release is not None:
            self.apply_release.wait(5)
        with self._lock:
            actions = tuple({"name": action.name, "arguments": dict(action.arguments)} for action in getattr(plan, "actions", ()) or ())
            if not bool(getattr(plan, "available", True)):
                outcome = ExecutionOutcome(
                    request_id,
                    False,
                    False,
                    False,
                    True,
                    reply=str(getattr(plan, "reply", "") or ""),
                    error=str(getattr(plan, "error", None) or "AI brain unavailable"),
                    actions=actions,
                )
            elif expected_generation is not None and expected_generation != self.generation:
                outcome = ExecutionOutcome(
                    request_id,
                    False,
                    False,
                    False,
                    True,
                    reply=str(getattr(plan, "reply", "") or ""),
                    error="stale generation; AI plan dropped after stop/manual/disarm",
                    actions=actions,
                )
            elif deadline is not None and self.clock() > deadline:
                outcome = ExecutionOutcome(
                    request_id,
                    True,
                    False,
                    False,
                    True,
                    reply=str(getattr(plan, "reply", "") or ""),
                    error="AI plan expired before execution",
                    actions=actions,
                )
            else:
                outcome = ExecutionOutcome(
                    request_id,
                    True,
                    False,
                    True,
                    True,
                    reply=str(getattr(plan, "reply", "") or ""),
                    actions=actions,
                    action_results=tuple({"name": action["name"], "sent": False, "completed": True} for action in actions),
                )
                self.applied.append({"request_id": request_id, "actions": actions, "captured_at": captured_at})
            self._record(outcome)
            return outcome

    def _record(self, outcome: ExecutionOutcome) -> None:
        self._history.append(outcome.to_dict())
        self._history = self._history[-24:]


def config():
    return load_hardware_config(CONFIG_PATH)


def test_text_request_reaches_brain_service_and_executes_returned_tools_dry_run():
    cfg = config()
    clock = FakeClock(10.0)
    brain = FakeBrain()
    controller = LampController(cfg, clock=clock)
    runtime = BrainRuntime(cfg, controller=controller, brain_service=brain, brain_enabled=True, clock=clock)

    outcome = runtime.submit_text("用动作表示开心")

    assert outcome.completed is True
    assert brain.calls[0]["user_text"] == "用动作表示开心"
    assert brain.calls[0]["frame_jpeg"] is None
    assert brain.calls[0]["state"]["position_source"] in {"initial_observation", "sent_targets", "unknown", "virtual"}
    assert controller.snapshot()["last_plan"]["reply"] == "好的"


def test_text_request_with_vision_enabled_includes_camera_unless_text_only():
    cfg = config()
    clock = FakeClock(5.0)
    brain = FakeBrain()
    camera = FakeCamera(captured_at=5.0, frames=[JPEG_A, JPEG_B])
    runtime = BrainRuntime(
        cfg,
        controller=FakeController(cfg, clock=clock),
        brain_service=brain,
        camera=camera,
        brain_enabled=True,
        vision_enabled=True,
        clock=clock,
    )

    with_camera = runtime.submit_text("请看看我")
    clock.advance(max(cfg.brain.interval_s, cfg.runtime.action_cooldown_s))
    text_only = runtime.submit_text("只听这句", text_only=True)

    assert with_camera.completed is True
    assert text_only.completed is True
    assert brain.calls[0]["frame_jpeg"] == JPEG_A
    assert brain.calls[0]["state"]["camera"]["timestamp_source"] == "host_acquisition"
    assert brain.calls[1]["frame_jpeg"] is None
    assert brain.calls[1]["user_text"] == "只听这句"
    assert camera.captures == 1
    assert camera.max_px_values == [min(cfg.brain.image_max_px, 512)]


@pytest.mark.parametrize(
    ("bad_timestamp", "expected_error"),
    [
        (float("nan"), "camera timestamp is not finite"),
        (11.0, "camera timestamp is in the future"),
    ],
)
def test_future_or_nan_camera_capture_does_not_poison_watermark_and_valid_later_works(bad_timestamp, expected_error):
    cfg = config()
    clock = FakeClock(10.0)
    brain = FakeBrain()
    spacing = max(cfg.brain.interval_s, cfg.runtime.action_cooldown_s)
    camera = FakeCamera(captured_at=bad_timestamp, frames=[JPEG_A, JPEG_B])
    camera.captured_at_values = [bad_timestamp, 10.0 + spacing]
    runtime = BrainRuntime(
        cfg,
        controller=FakeController(cfg, clock=clock),
        brain_service=brain,
        camera=camera,
        brain_enabled=True,
        vision_enabled=True,
        clock=clock,
    )

    bad = runtime.observe_once()
    clock.advance(spacing)
    valid = runtime.observe_once()

    assert bad.accepted is False
    assert bad.error == expected_error
    assert valid.completed is True
    assert brain.calls[0]["frame_jpeg"] == JPEG_B
    assert runtime.snapshot()["last_capture_at"] == 10.0 + spacing


def test_disabled_inflight_budget_and_cooldown_rejections_do_not_touch_camera():
    cfg = config()
    clock = FakeClock(20.0)
    disabled_camera = FakeCamera()
    disabled = BrainRuntime(
        cfg,
        controller=FakeController(cfg, clock=clock),
        brain_service=FakeBrain(),
        camera=disabled_camera,
        brain_enabled=False,
        vision_enabled=True,
        clock=clock,
    )
    assert disabled.submit_text("ignored").accepted is False
    assert disabled_camera.captures == 0

    budget_camera = FakeCamera()
    budgeted = BrainRuntime(
        cfg,
        controller=FakeController(cfg, clock=clock),
        brain_service=FakeBrain(),
        camera=budget_camera,
        brain_enabled=True,
        max_calls=0,
        vision_enabled=True,
        clock=clock,
    )
    assert budgeted.submit_text("ignored").accepted is False
    assert budget_camera.captures == 0

    cooldown_camera = FakeCamera(captured_at=20.0, frames=[JPEG_A, JPEG_B])
    cooldown = BrainRuntime(
        cfg,
        controller=FakeController(cfg, clock=clock),
        brain_service=FakeBrain(),
        camera=cooldown_camera,
        brain_enabled=True,
        vision_enabled=True,
        clock=clock,
    )
    assert cooldown.submit_text("first").completed is True
    assert cooldown.submit_text("too soon").accepted is False
    assert cooldown_camera.captures == 1

    release = threading.Event()
    inflight_camera = FakeCamera(captured_at=30.0, frames=[JPEG_A, JPEG_B])
    inflight_camera.release = release
    inflight_clock = FakeClock(30.0)
    inflight = BrainRuntime(
        cfg,
        controller=FakeController(cfg, clock=inflight_clock),
        brain_service=FakeBrain(),
        camera=inflight_camera,
        brain_enabled=True,
        vision_enabled=True,
        clock=inflight_clock,
    )
    result: dict[str, ExecutionOutcome] = {}
    thread = threading.Thread(target=lambda: result.setdefault("outcome", inflight.submit_text("first")))
    thread.start()
    assert inflight_camera.entered.wait(1)

    rejected = inflight.submit_text("second")
    release.set()
    thread.join(1)

    assert rejected.accepted is False
    assert "in flight" in (rejected.error or "")
    assert inflight_camera.captures == 1


def test_stop_while_brain_call_is_blocked_drops_returned_plan():
    cfg = config()
    clock = FakeClock(1.0)
    brain = FakeBrain()
    brain.release = threading.Event()
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(cfg, controller=controller, brain_service=brain, brain_enabled=True, clock=clock)
    result: dict[str, ExecutionOutcome] = {}
    thread = threading.Thread(target=lambda: result.setdefault("outcome", runtime.submit_text("慢一点")))
    thread.start()
    assert brain.entered.wait(1)

    runtime.stop()
    brain.release.set()
    thread.join(1)

    assert result["outcome"].sent is False
    assert "stale generation" in (result["outcome"].error or "")
    assert controller.applied == []


def test_stop_and_rearm_while_camera_is_blocked_prevents_api_call_and_execution():
    cfg = config()
    clock = FakeClock(2.0)
    brain = FakeBrain()
    camera = FakeCamera(captured_at=2.0)
    camera.release = threading.Event()
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(cfg, controller=controller, brain_service=brain, camera=camera, brain_enabled=True, vision_enabled=True, clock=clock)
    result: dict[str, ExecutionOutcome] = {}
    thread = threading.Thread(target=lambda: result.setdefault("outcome", runtime.observe_once()))
    thread.start()
    assert camera.entered.wait(1)

    runtime.stop()
    controller.arm(True)
    camera.release.set()
    thread.join(1)

    assert result["outcome"].accepted is False
    assert "stale generation" in (result["outcome"].error or "")
    assert brain.calls == []
    assert controller.applied == []
    assert runtime.snapshot()["api_call_count"] == 0


def test_close_after_reservation_before_capture_waits_and_prevents_camera_or_api():
    cfg = config()
    clock = FakeClock(2.5)
    brain = FakeBrain()
    camera = FakeCamera(captured_at=2.5)
    controller = FakeController(cfg, clock=clock)
    entry_entered = threading.Event()
    entry_release = threading.Event()

    class EntryBlockedRuntime(BrainRuntime):
        def _execute_reserved_request(self, context):
            entry_entered.set()
            entry_release.wait(5)
            return super()._execute_reserved_request(context)

    runtime = EntryBlockedRuntime(
        cfg,
        controller=controller,
        brain_service=brain,
        camera=camera,
        brain_enabled=True,
        vision_enabled=True,
        clock=clock,
    )
    result: dict[str, ExecutionOutcome] = {}
    worker = threading.Thread(target=lambda: result.setdefault("outcome", runtime.submit_text("look later")))
    worker.start()
    assert entry_entered.wait(1)

    closer_result: dict[str, list[str]] = {}
    closer = threading.Thread(target=lambda: closer_result.setdefault("errors", runtime.close()))
    closer.start()
    assert controller.stop_entered.wait(1)

    assert camera.close_count == 0
    assert controller.close_count == 0
    entry_release.set()
    worker.join(1)
    closer.join(1)

    assert closer_result["errors"] == []
    assert result["outcome"].accepted is False
    assert "closed" in (result["outcome"].error or "")
    assert camera.captures == 0
    assert brain.calls == []
    assert controller.applied == []
    assert camera.close_count == 1
    assert controller.close_count == 1


def test_scheduler_tick_dispatches_async_and_continues_timer_poll_while_ai_blocked():
    cfg = config()
    clock = FakeClock(3.0)
    brain = FakeBrain()
    brain.release = threading.Event()
    camera = FakeCamera(captured_at=3.0, frames=[JPEG_A, JPEG_B])
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(cfg, controller=controller, brain_service=brain, camera=camera, brain_enabled=True, vision_enabled=True, clock=clock)
    runtime.set_auto_enabled(True)

    runtime.scheduler_tick()
    assert brain.entered.wait(1)
    clock.advance(max(cfg.brain.interval_s, cfg.runtime.action_cooldown_s) + 1)
    runtime.scheduler_tick()

    assert controller.tick_count == 2
    assert controller.poll_count == 2
    assert camera.captures == 1
    assert len(brain.calls) == 1
    brain.release.set()
    runtime.close()


def test_cooldown_is_measured_from_slow_request_finish():
    cfg = config()
    clock = FakeClock(0.0)
    spacing = max(cfg.brain.interval_s, cfg.runtime.action_cooldown_s)
    finish_at = spacing + 1.0
    brain = FakeBrain()
    brain.release = threading.Event()
    camera = FakeCamera(captured_at=0.0, frames=[JPEG_A, JPEG_B])
    camera.captured_at_values = [0.0, finish_at + spacing]
    runtime = BrainRuntime(
        cfg,
        controller=FakeController(cfg, clock=clock),
        brain_service=brain,
        camera=camera,
        brain_enabled=True,
        vision_enabled=True,
        clock=clock,
    )
    result: dict[str, ExecutionOutcome] = {}
    worker = threading.Thread(target=lambda: result.setdefault("outcome", runtime.submit_text("slow")))
    worker.start()
    assert brain.entered.wait(1)

    clock.advance(finish_at)
    brain.release.set()
    worker.join(1)
    rejected = runtime.submit_text("too soon after finish")
    clock.advance(spacing)
    accepted = runtime.submit_text("now ok")

    assert result["outcome"].completed is True
    assert rejected.accepted is False
    assert "cooldown" in (rejected.error or "")
    assert accepted.completed is True
    assert len(brain.calls) == 2


def test_inflight_remains_claimed_until_controller_apply_finishes():
    cfg = config()
    clock = FakeClock(4.0)
    brain = FakeBrain()
    camera = FakeCamera(captured_at=4.0, frames=[JPEG_A, JPEG_B])
    controller = FakeController(cfg, clock=clock)
    controller.apply_release = threading.Event()
    runtime = BrainRuntime(cfg, controller=controller, brain_service=brain, camera=camera, brain_enabled=True, vision_enabled=True, clock=clock)
    runtime.set_auto_enabled(True)

    runtime.scheduler_tick()
    assert controller.apply_entered.wait(1)
    clock.advance(max(cfg.brain.interval_s, cfg.runtime.action_cooldown_s) + 1)
    runtime.scheduler_tick()

    assert runtime.snapshot()["inflight"] is True
    assert camera.captures == 1
    assert len(brain.calls) == 1
    controller.apply_release.set()
    runtime.close()


def test_close_does_not_wait_for_synchronous_caller_thread_after_request_completes():
    cfg = config()
    clock = FakeClock(4.5)
    brain = FakeBrain()
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(cfg, controller=controller, brain_service=brain, brain_enabled=True, clock=clock)
    caller_done = threading.Event()
    caller_release = threading.Event()
    result: dict[str, ExecutionOutcome] = {}

    def caller():
        result["outcome"] = runtime.submit_text("finish then keep caller alive")
        caller_done.set()
        caller_release.wait(5)

    thread = threading.Thread(target=caller)
    thread.start()
    assert caller_done.wait(1)
    assert thread.is_alive()

    errors = runtime.close()

    caller_release.set()
    thread.join(1)
    assert errors == []
    assert result["outcome"].completed is True
    assert controller.close_count == 1


def test_controller_auto_state_is_runtime_source_before_and_after_rearm():
    cfg = config()
    clock = FakeClock(5.0)
    brain = FakeBrain()
    camera = FakeCamera(captured_at=5.0, frames=[JPEG_A, JPEG_B])
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(cfg, controller=controller, brain_service=brain, camera=camera, brain_enabled=True, vision_enabled=True, clock=clock)

    assert runtime.set_auto_enabled(True)["auto_enabled"] is True
    runtime.scheduler_tick()
    assert brain.entered.wait(1)
    controller.manual_override()
    controller.arm(True)
    clock.advance(max(cfg.brain.interval_s, cfg.runtime.action_cooldown_s) + 1)
    runtime.scheduler_tick()

    assert len(brain.calls) == 1
    assert runtime.snapshot()["auto_enabled"] is False


def test_set_auto_enable_race_with_close_leaves_controller_auto_disabled():
    cfg = config()
    clock = FakeClock(5.5)
    brain = FakeBrain()
    camera = FakeCamera(captured_at=5.5)
    controller = FakeController(cfg, clock=clock)
    controller.auto_enable_release = threading.Event()
    runtime = BrainRuntime(
        cfg,
        controller=controller,
        brain_service=brain,
        camera=camera,
        brain_enabled=True,
        vision_enabled=True,
        clock=clock,
    )
    result: dict[str, dict[str, Any]] = {}
    enabler = threading.Thread(target=lambda: result.setdefault("snapshot", runtime.set_auto_enabled(True)))
    enabler.start()
    assert controller.auto_enable_entered.wait(1)

    closer_result: dict[str, list[str]] = {}
    closer = threading.Thread(target=lambda: closer_result.setdefault("errors", runtime.close()))
    closer.start()
    controller.auto_enable_release.set()
    enabler.join(1)
    closer.join(1)

    assert closer_result["errors"] == []
    assert controller.auto_enabled is False
    assert runtime.snapshot()["auto_enabled"] is False


def test_scheduler_tick_after_close_does_not_poll_controller():
    cfg = config()
    clock = FakeClock(5.75)
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(cfg, controller=controller, brain_service=FakeBrain(), brain_enabled=True, clock=clock)

    assert runtime.close() == []
    runtime.scheduler_tick()

    assert controller.tick_count == 0
    assert controller.poll_count == 0


def test_close_while_ai_blocked_invalidates_request_and_repeated_close_cleans_once():
    cfg = config()
    clock = FakeClock(6.0)
    brain = FakeBrain()
    brain.release = threading.Event()
    camera = FakeCamera()
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(cfg, controller=controller, brain_service=brain, camera=camera, brain_enabled=True, clock=clock)
    result: dict[str, ExecutionOutcome] = {}
    worker = threading.Thread(target=lambda: result.setdefault("outcome", runtime.submit_text("blocked")))
    worker.start()
    assert brain.entered.wait(1)

    closer_result: dict[str, list[str]] = {}
    closer = threading.Thread(target=lambda: closer_result.setdefault("errors", runtime.close()))
    closer.start()
    brain.release.set()
    closer.join(1)
    worker.join(1)
    second_errors = runtime.close()

    assert closer_result["errors"] == []
    assert second_errors == []
    assert result["outcome"].accepted is False
    assert "closed" in (result["outcome"].error or "")
    assert controller.applied == []
    assert controller.stop_count == 1
    assert controller.close_count == 1
    assert camera.close_count == 1


def test_close_timeout_is_visible_and_does_not_disconnect_active_capture():
    cfg = config()
    clock = FakeClock(7.0)
    camera = FakeCamera(captured_at=7.0)
    camera.release = threading.Event()
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(
        cfg,
        controller=controller,
        brain_service=FakeBrain(),
        camera=camera,
        brain_enabled=True,
        vision_enabled=True,
        clock=clock,
    )
    result: dict[str, ExecutionOutcome] = {}
    thread = threading.Thread(target=lambda: result.setdefault("outcome", runtime.submit_text("capture blocks")))
    thread.start()
    assert camera.entered.wait(1)

    errors = runtime.close()

    assert any("brain request did not stop" in error for error in errors)
    assert controller.close_count == 0
    assert camera.close_count == 0
    camera.release.set()
    thread.join(1)
    assert runtime.close() == []
    assert controller.close_count == 1
    assert camera.close_count == 1
    assert result["outcome"].accepted is False


def test_open_failure_cleans_opened_services_and_sanitizes_error():
    cfg = config()
    clock = FakeClock(8.0)
    camera = FakeCamera()
    camera.open_error = RuntimeError("raw OPENAI_API_KEY=secret-token")
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(cfg, controller=controller, camera=camera, vision_enabled=True, clock=clock)

    try:
        runtime.open()
    except RuntimeError as exc:
        message = str(exc)
        cause = exc.__cause__
    else:
        raise AssertionError("open should fail")

    assert message == "AI brain runtime failed to open"
    assert cause is None
    assert "secret-token" not in message
    assert controller.close_count == 1
    assert camera.close_count == 1
    assert runtime.close() == []


def test_close_sanitizes_controller_close_error_strings():
    cfg = config()
    clock = FakeClock(8.5)
    controller = FakeController(cfg, clock=clock)
    controller.close_errors = ["raw token sk-secret"]
    runtime = BrainRuntime(cfg, controller=controller, brain_service=FakeBrain(), brain_enabled=True, clock=clock)

    errors = runtime.close()

    assert errors == ["controller close failed"]


def test_missing_default_brain_key_rejects_auto_enable_without_camera(monkeypatch):
    cfg = config()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    clock = FakeClock(9.0)
    camera = FakeCamera()
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(
        cfg,
        controller=controller,
        brain_service=None,
        camera=camera,
        brain_enabled=True,
        vision_enabled=True,
        clock=clock,
    )

    snapshot = runtime.set_auto_enabled(True)
    runtime.scheduler_tick()

    assert snapshot["auto_enabled"] is False
    assert "unavailable" in (snapshot["last_error"] or "")
    assert controller.auto_enabled is False
    assert camera.captures == 0


def test_rejected_text_is_not_remembered_as_future_history():
    cfg = config()
    clock = FakeClock(10.5)
    brain = FakeBrain()
    camera = FakeCamera(captured_at=10.5, frames=[JPEG_A, JPEG_B])
    spacing = max(cfg.brain.interval_s, cfg.runtime.action_cooldown_s)
    camera.captured_at_values = [10.5, 10.5 + spacing]
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(
        cfg,
        controller=controller,
        brain_service=brain,
        camera=camera,
        brain_enabled=True,
        vision_enabled=True,
        clock=clock,
    )

    first = runtime.submit_text("accepted text")
    rejected = runtime.submit_text("rejected cooldown text")
    clock.advance(spacing)
    runtime.set_auto_enabled(True)
    brain.entered.clear()
    runtime.scheduler_tick()
    assert brain.entered.wait(1)

    auto_history = brain.calls[-1]["history"]
    assert first.completed is True
    assert rejected.accepted is False
    assert any(item.get("text") == "accepted text" for item in auto_history)
    assert not any(item.get("text") == "rejected cooldown text" for item in auto_history)


def test_free_text_and_controller_outcomes_are_bounded_in_next_auto_history():
    cfg = config()
    clock = FakeClock(11.0)
    brain = FakeBrain()
    spacing = max(cfg.brain.interval_s, cfg.runtime.action_cooldown_s)
    camera = FakeCamera(captured_at=11.0, frames=[JPEG_A, JPEG_B])
    camera.captured_at_values = [11.0, 11.0 + spacing]
    controller = FakeController(cfg, clock=clock)
    runtime = BrainRuntime(cfg, controller=controller, brain_service=brain, camera=camera, brain_enabled=True, vision_enabled=True, clock=clock)

    first = runtime.submit_text("请保持非常安静但看起来专注")
    controller.stop(request_id="manual-stop")
    clock.advance(spacing)
    runtime.set_auto_enabled(True)
    brain.entered.clear()
    runtime.scheduler_tick()
    assert brain.entered.wait(1)

    assert first.completed is True
    auto_history = brain.calls[-1]["history"]
    assert any(item.get("type") == "user_text" and item.get("text") == "请保持非常安静但看起来专注" for item in auto_history)
    assert any(item.get("request_id") == first.request_id and item.get("completed") is True for item in auto_history)
    assert any(item.get("request_id") == "manual-stop" and item.get("completed") is True for item in auto_history)
    assert len(auto_history) <= 12
