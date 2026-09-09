from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import importlib.util
import math
import os
import threading
import time
import uuid
from typing import Any

from ailamp.config import HardwareConfig
from ailamp.services.controller import ExecutionOutcome, LampController


MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_USER_TEXT_CHARS = 700


@dataclass(frozen=True)
class _UnavailablePlan:
    reply: str
    actions: tuple = ()
    source: str = "local"
    available: bool = False
    error: str | None = None
    model: str = ""


@dataclass(frozen=True)
class _RequestContext:
    request_id: str
    user_text: str | None
    include_vision: bool
    started_at: float
    deadline: float
    generation: int
    state: dict[str, Any]
    brain: object


class BrainRuntime:
    def __init__(
        self,
        config: HardwareConfig,
        *,
        controller: LampController | None = None,
        brain_service: object | None = None,
        camera: object | None = None,
        brain_enabled: bool | None = None,
        vision_enabled: bool = False,
        max_calls: int | None = None,
        clock=time.monotonic,
    ):
        self.config = config
        self.controller = controller or LampController(config, clock=clock)
        self.brain_enabled = config.brain.enabled if brain_enabled is None else brain_enabled
        self.vision_enabled = vision_enabled
        self.provider = config.brain.provider
        self.model = config.brain.model
        self.max_calls = config.brain.max_calls if max_calls is None else max_calls
        self.plan_ttl_s = config.brain.plan_ttl_s
        self.interval_s = config.brain.interval_s
        self.image_max_px = config.brain.image_max_px
        self.request_spacing_s = max(float(config.brain.interval_s), float(config.runtime.action_cooldown_s))
        self.clock = clock
        self.brain = brain_service
        self.camera = camera
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._apply_close_lock = threading.RLock()
        self._inflight = False
        self._api_call_count = 0
        self._last_error: str | None = None
        self._camera_error: str | None = None
        self._last_capture_at: float | None = None
        self._last_admitted_at = -1_000_000.0
        self._last_finished_at = -1_000_000.0
        self._recent_user_text: deque[dict[str, Any]] = deque(maxlen=3)
        self._closed = False
        self._cleanup_complete = False
        self._controller_stop_called = False
        self._scheduler_thread: threading.Thread | None = None
        self._scheduler_stop = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._inflight_complete = threading.Event()
        self._inflight_complete.set()

    def open(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._closed:
                    raise RuntimeError("AI brain runtime is closed")
                if self._cleanup_complete:
                    raise RuntimeError("AI brain runtime is closed")
            controller_opened = False
            try:
                self.controller.open()
                controller_opened = True
                if self.vision_enabled:
                    self._ensure_camera()
                    assert self.camera is not None
                    self.camera.open()
            except Exception as exc:  # noqa: BLE001 - never expose raw hardware/provider errors.
                self._safe_cleanup_after_open_failure(controller_opened=controller_opened)
                raise RuntimeError("AI brain runtime failed to open") from None

    def start_scheduler(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("AI brain runtime is closed")
            if self._scheduler_thread is not None:
                return
            self._scheduler_stop.clear()
            self._scheduler_thread = threading.Thread(target=self._scheduler_loop, name="AILampBrainScheduler", daemon=True)
            self._scheduler_thread.start()

    def close(self) -> list[str]:
        errors: list[str] = []
        current = threading.current_thread()
        with self._lifecycle_lock:
            with self._lock:
                first_close = not self._closed
                self._closed = True
                self._scheduler_stop.set()
                scheduler_thread = self._scheduler_thread
                worker_thread = self._worker_thread
                inflight_complete = self._inflight_complete
            if first_close and not self._controller_stop_called:
                try:
                    self.controller.stop()
                except Exception:  # noqa: BLE001 - stable visible lifecycle error only.
                    errors.append("controller stop failed during brain runtime close")
                finally:
                    self._controller_stop_called = True

            threads: list[threading.Thread] = []
            for thread in (scheduler_thread, worker_thread):
                if thread is not None and thread is not current and thread not in threads:
                    threads.append(thread)

            deadline = time.monotonic() + 2.0
            if not inflight_complete.is_set():
                inflight_complete.wait(timeout=max(0.0, deadline - time.monotonic()))
            for thread in threads:
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                thread.join(timeout=remaining)

            live_threads = [thread for thread in threads if thread.is_alive()]
            if live_threads or not inflight_complete.is_set():
                errors.append("brain request did not stop")
                return errors

            with self._lock:
                self._scheduler_thread = None
                self._worker_thread = None
                if self._cleanup_complete:
                    return errors

            with self._apply_close_lock:
                if self.camera is not None and hasattr(self.camera, "close"):
                    try:
                        self.camera.close()
                    except Exception:  # noqa: BLE001 - stable visible cleanup error only.
                        errors.append("camera close failed")
                try:
                    if self.controller.close():
                        errors.append("controller close failed")
                except Exception:  # noqa: BLE001 - stable visible cleanup error only.
                    errors.append("controller close failed")
                with self._lock:
                    self._cleanup_complete = True
            return errors

    def submit_text(self, text: str, *, text_only: bool = False) -> ExecutionOutcome:
        if not isinstance(text, str) or not text.strip():
            return self._unavailable("request", "text request cannot be empty")
        user_text = self._bounded_text(text.strip())
        admission = self._reserve_request(user_text=user_text, include_vision=self.vision_enabled and not text_only)
        if isinstance(admission, ExecutionOutcome):
            return admission
        self._remember_user_text(user_text)
        return self._run_reserved_request(admission, register_current_thread=True)

    def observe_once(self, instruction: str | None = None) -> ExecutionOutcome:
        if not self.vision_enabled:
            return self._unavailable("observe", "vision is disabled")
        user_text = None if instruction is None else self._bounded_text(str(instruction))
        admission = self._reserve_request(user_text=user_text, include_vision=True)
        if isinstance(admission, ExecutionOutcome):
            return admission
        if user_text:
            self._remember_user_text(user_text)
        return self._run_reserved_request(admission, register_current_thread=True)

    def set_auto_enabled(self, enabled: bool) -> dict[str, Any]:
        if not enabled:
            try:
                self.controller.set_auto_enabled(False)
            finally:
                with self._lock:
                    self._last_error = None
            return self.snapshot()

        with self._lock:
            if self._closed:
                self._last_error = "AI brain runtime is closed"
                try:
                    self.controller.set_auto_enabled(False)
                except Exception:  # noqa: BLE001
                    pass
                return self.snapshot()
            if not self.brain_enabled:
                self._last_error = "AI brain is disabled; manual controls are commissioning fallback only"
                self.controller.set_auto_enabled(False)
                return self.snapshot()
            if self._get_available_brain_locked() is None:
                self._last_error = "AI brain is unavailable; set OPENAI_API_KEY and install the brain extra"
                self.controller.set_auto_enabled(False)
                return self.snapshot()
            if not self.vision_enabled:
                self._last_error = "vision is disabled"
                self.controller.set_auto_enabled(False)
                return self.snapshot()
            try:
                self._ensure_camera()
            except Exception:  # noqa: BLE001
                self._last_error = "AI vision camera is unavailable"
                self.controller.set_auto_enabled(False)
                return self.snapshot()
            self._last_error = None
            self.controller.set_auto_enabled(True)
            return self.snapshot()

    def scheduler_tick(self) -> None:
        with self._lock:
            if self._closed:
                return
        self.controller.tick_timer()
        self.controller.poll_completion()
        state = self.controller.snapshot()
        if not bool(state.get("auto_enabled")):
            return
        admission = self._reserve_request(user_text=None, include_vision=True, state=state)
        if isinstance(admission, ExecutionOutcome):
            return
        worker = threading.Thread(
            target=self._run_scheduler_worker,
            args=(admission,),
            name="AILampBrainWorker",
            daemon=True,
        )
        with self._lock:
            if self._closed or self._worker_thread is not None or not self._inflight:
                self._release_reserved_request(admission)
                return
            self._worker_thread = worker
            try:
                worker.start()
            except Exception:  # noqa: BLE001 - worker startup failure leaves no reserved request behind.
                self._worker_thread = None
                self._release_reserved_request(admission)
                self._last_error = "AI brain worker failed to start"

    def stop(self) -> ExecutionOutcome:
        with self._lock:
            self._last_error = None
        outcome = self.controller.stop()
        with self._lock:
            self._last_error = outcome.error
        return outcome

    def snapshot(self) -> dict[str, Any]:
        controller_state = self.controller.snapshot()
        with self._lock:
            return {
                "brain_enabled": self.brain_enabled,
                "provider": self.provider,
                "model": self.model,
                "api_call_count": self._visible_api_call_count_locked(),
                "max_calls": self.max_calls,
                "inflight": self._inflight,
                "last_error": self._last_error,
                "vision_enabled": self.vision_enabled,
                "auto_enabled": bool(controller_state.get("auto_enabled")),
                "camera_error": self._camera_error,
                "last_capture_at": self._last_capture_at,
                "request_cooldown_s": self._remaining_cooldown_locked(),
            }

    def _reserve_request(
        self,
        *,
        user_text: str | None,
        include_vision: bool,
        state: dict[str, Any] | None = None,
    ) -> _RequestContext | ExecutionOutcome:
        request_id = uuid.uuid4().hex
        started_at = float(self.clock())
        with self._lock:
            if self._closed:
                return self._unavailable_locked(request_id, "AI brain runtime is closed")
            if not self.brain_enabled:
                return self._unavailable_locked(
                    request_id,
                    "AI brain is disabled; manual controls are commissioning fallback only",
                )
            if self._inflight:
                return self._unavailable_locked(request_id, "AI brain request already in flight")
            remaining = self._remaining_cooldown_locked(now=started_at)
            if remaining > 0:
                return self._unavailable_locked(request_id, f"AI brain request cooldown active ({remaining:.2f}s remaining)")
            if self._visible_api_call_count_locked() >= self.max_calls:
                return self._unavailable_locked(request_id, "AI brain API call budget exhausted")
            brain = self._get_available_brain_locked()
            if brain is None:
                return self._unavailable_locked(
                    request_id,
                    "AI brain is unavailable; set OPENAI_API_KEY and install the brain extra",
                )
            if include_vision and not self.vision_enabled:
                return self._unavailable_locked(request_id, "vision is disabled")
            request_state = dict(state if state is not None else self.controller.snapshot())
            generation = int(request_state["generation"])
            self._inflight = True
            self._inflight_complete.clear()
            self._last_admitted_at = started_at
            self._last_error = None
            return _RequestContext(
                request_id=request_id,
                user_text=user_text,
                include_vision=include_vision,
                started_at=started_at,
                deadline=started_at + self.plan_ttl_s,
                generation=generation,
                state=request_state,
                brain=brain,
            )

    def _run_scheduler_worker(self, context: _RequestContext) -> None:
        try:
            self._run_reserved_request(context, register_current_thread=True)
        finally:
            with self._lock:
                if self._worker_thread is threading.current_thread():
                    self._worker_thread = None

    def _run_reserved_request(self, context: _RequestContext, *, register_current_thread: bool) -> ExecutionOutcome:
        try:
            return self._execute_reserved_request(context)
        finally:
            finished_at = float(self.clock())
            with self._lock:
                self._inflight = False
                self._last_finished_at = finished_at
                self._inflight_complete.set()

    def _execute_reserved_request(self, context: _RequestContext) -> ExecutionOutcome:
        frame_jpeg = None
        captured_at = None
        state = dict(context.state)
        unavailable = self._closed_or_stale_request(context)
        if unavailable is not None:
            return unavailable
        if context.include_vision:
            try:
                frame_jpeg, captured_at = self._capture_jpeg(request_start=context.started_at)
                state = {
                    **state,
                    "camera": {"captured_at": captured_at, "timestamp_source": "host_acquisition"},
                }
            except Exception as exc:  # noqa: BLE001 - camera failure must not call the model.
                message = self._camera_error_message(exc)
                with self._lock:
                    self._camera_error = message
                return self._unavailable(context.request_id, message)

        unavailable = self._closed_or_stale_request(context)
        if unavailable is not None:
            return unavailable
        with self._lock:
            if self._visible_api_call_count_locked() >= self.max_calls:
                return self._unavailable_locked(context.request_id, "AI brain API call budget exhausted")
            self._api_call_count += 1

        history = self._request_history()
        try:
            plan = context.brain.decide(user_text=context.user_text, frame_jpeg=frame_jpeg, state=state, history=history)
        except Exception:  # noqa: BLE001 - no fallback motion on provider failure.
            plan = _UnavailablePlan(reply="", error="AI brain provider request failed")
        return self._apply_returned_plan(
            plan,
            context.generation,
            context.started_at,
            context.deadline,
            request_id=context.request_id,
            captured_at=captured_at,
        )

    def _closed_or_stale_request(self, context: _RequestContext) -> ExecutionOutcome | None:
        with self._lock:
            if self._closed:
                return self._unavailable_locked(context.request_id, "AI brain runtime is closed")
        current_generation = int(self.controller.snapshot()["generation"])
        with self._lock:
            if self._closed:
                return self._unavailable_locked(context.request_id, "AI brain runtime is closed")
            if current_generation != context.generation:
                return self._unavailable_locked(
                    context.request_id,
                    "stale generation; AI plan dropped after stop/manual/disarm",
                )
        return None

    def _apply_returned_plan(
        self,
        plan: object,
        generation: int,
        started_at: float,
        deadline: float,
        *,
        request_id: str,
        captured_at: float | None = None,
    ) -> ExecutionOutcome:
        with self._apply_close_lock:
            with self._lock:
                if self._closed:
                    return self._unavailable_locked(request_id, "AI brain runtime is closed")
            outcome = self.controller.apply_brain_plan(
                plan,
                request_id=request_id,
                expected_generation=generation,
                deadline=deadline,
                captured_at=captured_at,
            )
            with self._lock:
                self._last_error = outcome.error
            return outcome

    def _capture_jpeg(self, *, request_start: float) -> tuple[bytes, float]:
        self._ensure_camera()
        assert self.camera is not None
        max_px = min(int(self.image_max_px), 512)
        if hasattr(self.camera, "capture_jpeg"):
            frame, captured_at = self.camera.capture_jpeg(max_px=max_px)
            if not isinstance(frame, bytes):
                frame = bytes(frame)
        else:
            frame = self.camera.read()
            captured_at = self.clock()
            if frame is None:
                raise RuntimeError("no camera frame")
            if not isinstance(frame, bytes):
                frame = self._encode_frame(frame, max_px=max_px)
        frame = self._validate_frame_jpeg(frame)
        captured_at = self._validate_capture_time(captured_at, request_start=request_start)
        with self._lock:
            if self._last_capture_at is not None and captured_at <= self._last_capture_at:
                raise RuntimeError("camera frame is not newer than the previous brain frame")
            self._last_capture_at = captured_at
            self._camera_error = None
        return frame, captured_at

    def _encode_frame(self, frame: object, *, max_px: int | None = None) -> bytes:
        import cv2  # type: ignore

        image_max_px = min(int(max_px if max_px is not None else self.image_max_px), 512)
        height, width = frame.shape[:2]
        largest = max(width, height)
        if largest > image_max_px:
            scale = image_max_px / largest
            frame = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))))
        ok, encoded = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("failed to encode camera frame as JPEG")
        return bytes(encoded)

    def _validate_capture_time(self, captured_at: object, *, request_start: float) -> float:
        if isinstance(captured_at, bool) or not isinstance(captured_at, int | float):
            raise RuntimeError("camera timestamp is not a real number")
        value = float(captured_at)
        now = float(self.clock())
        if not math.isfinite(value):
            raise RuntimeError("camera timestamp is not finite")
        if value > now:
            raise RuntimeError("camera timestamp is in the future")
        if value < request_start:
            raise RuntimeError("camera timestamp predates this brain request")
        if now - value > self.plan_ttl_s:
            raise RuntimeError("camera frame is stale")
        return value

    def _validate_frame_jpeg(self, frame: object) -> bytes:
        if not isinstance(frame, bytes):
            raise RuntimeError("camera frame is invalid")
        if not frame:
            raise RuntimeError("no camera frame")
        if len(frame) > MAX_FRAME_BYTES:
            raise RuntimeError("camera frame is too large")
        if not frame.startswith(b"\xff\xd8") or not frame.endswith(b"\xff\xd9"):
            raise RuntimeError("camera frame is not a JPEG")
        return frame

    def _ensure_camera(self) -> None:
        if self.camera is not None:
            return
        from ailamp.services.camera import CameraService

        self.camera = CameraService(
            self.config.camera.device_path,
            self.config.camera.width,
            self.config.camera.height,
            self.config.camera.fps,
            self.config.camera.pixel_format,
        )

    def _get_available_brain_locked(self) -> object | None:
        if self.brain is not None:
            return self.brain
        if not os.environ.get("OPENAI_API_KEY"):
            return None
        if importlib.util.find_spec("openai") is None:
            return None
        try:
            self.brain = self._make_default_brain()
        except Exception:  # noqa: BLE001 - provider configuration errors are sanitized by caller.
            self.brain = None
        return self.brain

    def _make_default_brain(self) -> object | None:
        try:
            from ailamp.services.brain import BrainService
        except Exception:
            return None
        return BrainService(
            provider=self.provider,
            model=self.model,
            recording_names=self.controller.recording_names,
            api_key=None,
            max_calls=self.max_calls,
            timeout_s=self.config.brain.timeout_s,
        )

    def _request_history(self) -> tuple[dict[str, Any], ...]:
        controller_history = []
        for item in self.controller.history(limit=9):
            if isinstance(item, dict):
                record = dict(item)
                record.setdefault("source", "controller")
                record.setdefault("type", "execution_outcome")
                controller_history.append(record)
        with self._lock:
            user_history = tuple(self._recent_user_text)
        return tuple((*user_history, *controller_history)[-12:])

    def _remember_user_text(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._recent_user_text.append(
                {
                    "source": "runtime",
                    "type": "user_text",
                    "text": self._bounded_text(text),
                }
            )

    def _bounded_text(self, text: str) -> str:
        return text[:MAX_USER_TEXT_CHARS]

    def _remaining_cooldown_locked(self, *, now: float | None = None) -> float:
        current = float(self.clock()) if now is None else float(now)
        latest_request_at = max(self._last_admitted_at, self._last_finished_at)
        remaining = self.request_spacing_s - (current - latest_request_at)
        return max(0.0, remaining)

    def _visible_api_call_count_locked(self) -> int:
        calls_used = getattr(self.brain, "calls_used", None)
        if isinstance(calls_used, int) and not isinstance(calls_used, bool):
            return max(self._api_call_count, calls_used)
        return self._api_call_count

    def _release_reserved_request(self, context: _RequestContext) -> None:
        with self._lock:
            if self._inflight:
                self._inflight = False
                self._inflight_complete.set()
                if self._last_admitted_at == context.started_at:
                    self._last_admitted_at = -1_000_000.0

    def _safe_cleanup_after_open_failure(self, *, controller_opened: bool) -> None:
        with self._lock:
            self._closed = True
            self._scheduler_stop.set()
            self._last_error = "AI brain runtime failed to open"
        if self.camera is not None and hasattr(self.camera, "close"):
            try:
                self.camera.close()
            except Exception:
                pass
        if controller_opened and not self._controller_stop_called:
            try:
                self.controller.stop()
            except Exception:
                pass
            finally:
                self._controller_stop_called = True
        if controller_opened:
            try:
                self.controller.close()
            except Exception:
                pass
        with self._lock:
            self._cleanup_complete = True

    def _camera_error_message(self, exc: BaseException) -> str:
        message = str(exc)
        stable_fragments = (
            "camera timestamp is not a real number",
            "camera timestamp is not finite",
            "camera timestamp is in the future",
            "camera timestamp predates this brain request",
            "camera frame is stale",
            "camera frame is not newer than the previous brain frame",
            "camera frame is too large",
            "camera frame is not a JPEG",
            "no camera frame",
        )
        for fragment in stable_fragments:
            if fragment in message:
                return fragment
        return "camera error"

    def _unavailable(self, request_id: str, message: str) -> ExecutionOutcome:
        with self._lock:
            return self._unavailable_locked(request_id, message)

    def _unavailable_locked(self, request_id: str, message: str) -> ExecutionOutcome:
        self._last_error = message
        return ExecutionOutcome(
            request_id=request_id,
            accepted=False,
            sent=False,
            completed=False,
            dry_run=not self.controller.with_outputs,
            error=message,
        )

    def _scheduler_loop(self) -> None:
        while not self._scheduler_stop.wait(0.25):
            self.scheduler_tick()
