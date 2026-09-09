from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import Any, Iterable

from ailamp.config import HardwareConfig
from ailamp.paths import resolve_project_path
from ailamp.services.led_serial import LEDSerialService
from ailamp.services.motor import JOINT_NAMES, JointDeltaCommand, MotorService, RecordingStore


VALID_MODES = {"focus", "rest", "manual"}
MOTOR_ACTIONS = {"play_recording", "move_joints"}
LIFECYCLE_NOT_OPEN = "controller_not_open"
LIFECYCLE_CLOSING = "controller_closing"
LIFECYCLE_OPEN_FAILED = "controller_open_failed"
LIFECYCLE_CLEANUP_FAILED = "controller_cleanup_failed"


class _ActionDispatchError(RuntimeError):
    def __init__(self, message: str, *, sent: bool = False, results: Iterable[dict[str, Any]] = ()):
        super().__init__(message)
        self.sent = sent
        self.results = tuple(results)


@dataclass(frozen=True)
class ExecutionOutcome:
    request_id: str
    accepted: bool
    sent: bool
    completed: bool
    dry_run: bool
    reply: str = ""
    error: str | None = None
    actions: tuple[dict[str, Any], ...] = ()
    action_results: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "accepted": self.accepted,
            "sent": self.sent,
            "completed": self.completed,
            "dry_run": self.dry_run,
            "reply": self.reply,
            "error": self.error,
            "actions": list(self.actions),
            "action_results": list(self.action_results),
        }


class DryRunMotorService:
    def __init__(self):
        self.recordings: list[str] = []
        self.joint_deltas: list[tuple[JointDeltaCommand, ...]] = []
        self._busy = False
        self._last_error: BaseException | None = None
        self.position_source = "virtual"
        self.positions: dict[str, float] = {}

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def play_recording(self, name: str) -> None:
        self.recordings.append(name)

    def play(self, name: str) -> None:
        self.play_recording(name)

    def apply_joint_deltas(self, deltas: Iterable[JointDeltaCommand]) -> dict[str, float]:
        commands = tuple(deltas)
        self.joint_deltas.append(commands)
        for command in commands:
            self.positions[f"{command.joint}.pos"] = command.delta_units
        return dict(self.positions)

    def stop_motion(self) -> None:
        self._busy = False

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        return not self._busy

    def get_positions(self) -> dict[str, float]:
        return dict(self.positions)

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def last_error(self) -> BaseException | None:
        return self._last_error


class DryRunLEDService:
    def __init__(self):
        self.colors: list[tuple[int, int, int]] = []
        self.brightness_values: list[int] = []
        self.clear_count = 0

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def solid(self, red: int, green: int, blue: int) -> str:
        self.colors.append((red, green, blue))
        return "OK"

    def brightness(self, value: int) -> str:
        self.brightness_values.append(value)
        return "OK"

    def set_brightness(self, value: int) -> str:
        return self.brightness(value)

    def clear(self) -> str:
        self.clear_count += 1
        return "OK"


@dataclass(frozen=True)
class _ActionLike:
    name: str
    arguments: dict[str, Any]


class LampController:
    def __init__(
        self,
        config: HardwareConfig,
        *,
        with_outputs: bool = False,
        motor_service: object | None = None,
        led_service: object | None = None,
        clock=time.monotonic,
    ):
        self.config = config
        self.with_outputs = with_outputs
        self.clock = clock
        self.recordings = RecordingStore(resolve_project_path(config.simulation.recordings_dir))
        self.recording_names = tuple(self.recordings.list_names())
        self.motor = motor_service if with_outputs else DryRunMotorService()
        self.led = led_service if with_outputs else DryRunLEDService()
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._opened = False
        self._lifecycle_state = "closed"
        self._lifecycle_error: str | None = None
        self._armed = False
        self._auto_enabled = False
        self._mode = "manual"
        self._generation = 0
        self._last_plan: dict[str, Any] | None = None
        self._last_outcome: ExecutionOutcome | None = None
        self._pending_motor_outcome: ExecutionOutcome | None = None
        self._history: list[dict[str, Any]] = []
        self._position_source = "unknown"
        self._positions: dict[str, float] = {}
        self._light = {"red": 0, "green": 0, "blue": 0, "brightness": config.led.brightness}
        self._focus_deadline: float | None = None
        self._focus_generation: int | None = None

    def open(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._opened:
                    return
                if not self.with_outputs:
                    self._opened = True
                    self._lifecycle_state = "open"
                    self._lifecycle_error = None
                    self._refresh_positions_locked(raise_errors=True)
                    return
                if self._lifecycle_state == "closing":
                    raise RuntimeError("controller is closing")
                if self._lifecycle_state == "cleanup_failed":
                    raise RuntimeError("controller cleanup incomplete; close must succeed before open")
                try:
                    if self.motor is None:
                        self.motor = self._build_real_motor()
                    if self.led is None:
                        self.led = LEDSerialService(self.config.led.port, self.config.led.count, self.config.led.baudrate)
                except Exception as exc:
                    self._opened = False
                    self._lifecycle_state = "closed"
                    self._lifecycle_error = LIFECYCLE_OPEN_FAILED
                    raise
                self._lifecycle_state = "opening"
                self._lifecycle_error = None
                services = tuple(svc for svc in (self.led, self.motor) if svc is not None)
            attempted: list[object] = []
            try:
                for service in services:
                    if hasattr(service, "connect"):
                        attempted.append(service)
                        service.connect()
                with self._lock:
                    if self._lifecycle_state == "closing":
                        raise RuntimeError("controller close requested during open")
                    self._refresh_positions_locked(raise_errors=True)
                    if self._lifecycle_state == "closing":
                        raise RuntimeError("controller close requested during open")
                    self._opened = True
                    self._lifecycle_state = "open"
                    self._lifecycle_error = None
            except Exception as exc:
                cleanup_errors: list[str] = []
                with self._lock:
                    close_requested = self._lifecycle_state == "closing"
                if not close_requested:
                    for service in reversed(attempted):
                        if not hasattr(service, "close"):
                            continue
                        try:
                            service.close()
                        except Exception as cleanup_exc:  # noqa: BLE001 - preserve cleanup context.
                            cleanup_errors.append(str(cleanup_exc))
                message = str(exc)
                if cleanup_errors:
                    message = f"{message}; connect cleanup failed: {', '.join(cleanup_errors)}"
                with self._lock:
                    self._opened = False
                    if close_requested:
                        self._lifecycle_state = "closing"
                        self._lifecycle_error = LIFECYCLE_CLOSING
                    else:
                        self._lifecycle_state = "cleanup_failed" if cleanup_errors else "closed"
                        self._lifecycle_error = LIFECYCLE_CLEANUP_FAILED if cleanup_errors else LIFECYCLE_OPEN_FAILED
                raise RuntimeError(message) from exc

    def close(self) -> list[str]:
        errors: list[str] = []
        with self._lock:
            if self._lifecycle_state == "closing":
                wait_only = True
                motor = None
                led = None
            else:
                wait_only = False
                self._generation += 1
                self._armed = False
                self._auto_enabled = False
                self._focus_deadline = None
                self._focus_generation = None
                self._cancel_pending_motor_locked("cancelled by close")
                previous_state = self._lifecycle_state
                self._opened = False
                self._lifecycle_state = "closing"
                motor = self.motor
                led = self.led
                if self.with_outputs and previous_state == "open" and motor is not None and hasattr(motor, "stop_motion"):
                    try:
                        motor.stop_motion()
                    except Exception as exc:  # noqa: BLE001
                        errors.append(str(exc))
        with self._lifecycle_lock:
            if wait_only:
                return []
            if self.with_outputs:
                for service in (motor, led):
                    if service is None or not hasattr(service, "close"):
                        continue
                    try:
                        service.close()
                    except Exception as exc:  # noqa: BLE001 - cleanup should collect all failures.
                        errors.append(str(exc))
            with self._lock:
                self._lifecycle_state = "cleanup_failed" if errors else "closed"
                self._lifecycle_error = LIFECYCLE_CLEANUP_FAILED if errors else None
            return errors

    def arm(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            if self.with_outputs and enabled and not self._outputs_ready_locked():
                self._armed = False
                self._lifecycle_error = self._lifecycle_block_error_locked("arm")
                return self.snapshot()
            self._generation += 1
            self._armed = bool(enabled)
            if not enabled:
                self._auto_enabled = False
                self._focus_deadline = None
                self._focus_generation = None
                self._cancel_pending_motor_locked("cancelled by disarm")
                if self.with_outputs and self._outputs_ready_locked() and self.motor is not None and hasattr(self.motor, "stop_motion"):
                    self.motor.stop_motion()
            return self.snapshot()

    def set_auto_enabled(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            self._generation += 1
            self._auto_enabled = bool(enabled)
            if not enabled:
                self._cancel_pending_motor_locked("cancelled by auto disable")
            if not enabled and self.with_outputs and self._outputs_ready_locked() and self.motor is not None and hasattr(self.motor, "stop_motion"):
                self.motor.stop_motion()
            return self.snapshot()

    def stop(self, request_id: str = "stop") -> ExecutionOutcome:
        error = None
        with self._lock:
            self._generation += 1
            self._armed = False
            self._auto_enabled = False
            self._focus_deadline = None
            self._focus_generation = None
            self._cancel_pending_motor_locked("cancelled by stop")
            if self.with_outputs and self._outputs_ready_locked() and self.motor is not None and hasattr(self.motor, "stop_motion"):
                try:
                    self.motor.stop_motion()
                except Exception as exc:  # noqa: BLE001 - report software stop failures.
                    error = str(exc)
            outcome = ExecutionOutcome(
                request_id=request_id,
                accepted=error is None,
                sent=False,
                completed=error is None,
                dry_run=not self.with_outputs,
                error=error,
            )
            self._record_outcome_locked(outcome)
            return outcome

    def apply_brain_plan(
        self,
        plan: object,
        *,
        request_id: str,
        expected_generation: int | None = None,
        deadline: float | None = None,
        captured_at: float | None = None,
    ) -> ExecutionOutcome:
        reply = str(getattr(plan, "reply", "") or "")
        if not bool(getattr(plan, "available", True)):
            return self._recorded(
                ExecutionOutcome(
                    request_id,
                    False,
                    False,
                    False,
                    not self.with_outputs,
                    reply=reply,
                    error=str(getattr(plan, "error", None) or "AI brain unavailable"),
                )
            )
        try:
            actions = self._validate_plan_actions(tuple(getattr(plan, "actions", ()) or ()))
        except ValueError as exc:
            return self._recorded(ExecutionOutcome(request_id, False, False, False, not self.with_outputs, reply=reply, error=str(exc)))
        with self._lock:
            self._record_plan_locked(plan, actions)
            return self._dispatch_locked(
                actions,
                request_id=request_id,
                reply=reply,
                expected_generation=expected_generation,
                deadline=deadline,
                captured_at=captured_at,
                manual=False,
            )

    def manual_play_recording(self, name: str) -> ExecutionOutcome:
        try:
            actions = self._validate_plan_actions((_ActionLike("play_recording", {"name": name}),))
        except ValueError as exc:
            return self._recorded(ExecutionOutcome("manual", False, False, False, not self.with_outputs, error=str(exc)))
        with self._lock:
            self._manual_override_locked()
            return self._dispatch_locked(actions, request_id="manual", reply="", manual=True)

    def manual_set_light(self, red: int, green: int, blue: int, brightness: int | None = None) -> ExecutionOutcome:
        args: dict[str, Any] = {"red": red, "green": green, "blue": blue}
        if brightness is not None:
            args["brightness"] = brightness
        try:
            actions = self._validate_plan_actions((_ActionLike("set_light", args),))
        except ValueError as exc:
            return self._recorded(ExecutionOutcome("manual", False, False, False, not self.with_outputs, error=str(exc)))
        with self._lock:
            self._manual_override_locked()
            return self._dispatch_locked(actions, request_id="manual", reply="", manual=True)

    def clear_light(self) -> ExecutionOutcome:
        try:
            actions = ({"name": "clear_light", "arguments": {}},)
        except ValueError as exc:
            return self._recorded(ExecutionOutcome("manual", False, False, False, not self.with_outputs, error=str(exc)))
        with self._lock:
            self._manual_override_locked()
            return self._dispatch_locked(actions, request_id="manual", reply="", manual=True)

    def set_mode(self, mode: str, timer_minutes: int | None = None) -> ExecutionOutcome:
        try:
            actions = self._validate_plan_actions(
                (_ActionLike("set_mode", {"mode": mode, **({} if timer_minutes is None else {"timer_minutes": timer_minutes})}),)
            )
        except ValueError as exc:
            return self._recorded(ExecutionOutcome("manual", False, False, False, not self.with_outputs, error=str(exc)))
        with self._lock:
            self._manual_override_locked(stop_motor=False)
            return self._dispatch_locked(actions, request_id="manual", reply="", manual=True)

    def tick_timer(self) -> ExecutionOutcome | None:
        with self._lock:
            if self._focus_deadline is None or self.clock() < self._focus_deadline:
                return None
            if self.with_outputs and not self._outputs_ready_locked():
                self._focus_deadline = None
                self._focus_generation = None
                return None
            self._focus_deadline = None
            self._focus_generation = None
            self._generation += 1
            actions = (
                {"name": "set_mode", "arguments": {"mode": "rest"}},
                {"name": "set_light", "arguments": {"red": 40, "green": 30, "blue": 20, "brightness": self.config.led.brightness}},
            )
            return self._dispatch_locked(actions, request_id="focus_timer", reply="", manual=False)

    def poll_completion(self) -> ExecutionOutcome | None:
        with self._lock:
            return self._settle_pending_motor_if_terminal_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_positions_locked()
            motor_status = self._motor_status_locked()
            countdown = None
            if self._focus_deadline is not None:
                countdown = max(0.0, self._focus_deadline - self.clock())
            return {
                "with_outputs": self.with_outputs,
                "armed": self._armed,
                "auto_enabled": self._auto_enabled,
                "mode": self._mode,
                "generation": self._generation,
                "recordings": list(self.recording_names),
                "position_source": self._position_source,
                "positions": dict(self._positions),
                "light": dict(self._light),
                "focus_remaining_s": countdown,
                "motor_busy": motor_status["busy"],
                "motor_error": motor_status["error"],
                "lifecycle_state": self._lifecycle_state,
                "lifecycle_error": self._lifecycle_error,
                "last_plan": self._last_plan,
                "last_outcome": None if self._last_outcome is None else self._last_outcome.to_dict(),
            }

    def history(self, limit: int = 12) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(self._history[-limit:])

    def _build_real_motor(self) -> MotorService:
        return MotorService(
            self.config.motors.port,
            self.config.motors.lamp_id,
            resolve_project_path(self.config.simulation.recordings_dir),
            fps=self.config.motors.fps,
        )

    def _dispatch_locked(
        self,
        actions: tuple[dict[str, Any], ...],
        *,
        request_id: str,
        reply: str,
        expected_generation: int | None = None,
        deadline: float | None = None,
        captured_at: float | None = None,
        manual: bool,
    ) -> ExecutionOutcome:
        has_motor_action = any(action["name"] in MOTOR_ACTIONS for action in actions)
        check_motor_busy = not (self.with_outputs and has_motor_action)
        auth_error = self._authorization_error_locked(
            actions,
            expected_generation,
            deadline,
            captured_at,
            manual,
            check_motor_busy=check_motor_busy,
        )
        if auth_error:
            outcome = ExecutionOutcome(request_id, accepted=not auth_error.startswith("stale"), sent=False, completed=False, dry_run=not self.with_outputs, reply=reply, error=auth_error, actions=actions)
            self._record_outcome_locked(outcome)
            return outcome
        if self.with_outputs and has_motor_action:
            self._settle_pending_motor_if_terminal_locked()
            if self._pending_motor_outcome is not None:
                outcome = ExecutionOutcome(
                    request_id,
                    True,
                    False,
                    False,
                    False,
                    reply=reply,
                    error="motor busy; motor action not sent",
                    actions=actions,
                )
                self._record_outcome_locked(outcome)
                return outcome
        auth_error = self._authorization_error_locked(actions, expected_generation, deadline, captured_at, manual)
        if auth_error:
            outcome = ExecutionOutcome(request_id, accepted=not auth_error.startswith("stale"), sent=False, completed=False, dry_run=not self.with_outputs, reply=reply, error=auth_error, actions=actions)
            self._record_outcome_locked(outcome)
            return outcome
        if not self.with_outputs:
            results = tuple(self._apply_virtual_locked(action) for action in actions)
            for action in actions:
                if action["name"] == "set_mode" and not manual:
                    self._generation += 1
                    break
            outcome = ExecutionOutcome(request_id, True, False, True, True, reply=reply, actions=actions, action_results=results)
            self._record_outcome_locked(outcome)
            return outcome

        sent = False
        results: list[dict[str, Any]] = []
        mode_args: dict[str, Any] | None = None
        try:
            for action in actions:
                auth_error = self._authorization_error_locked((action,), expected_generation, deadline, captured_at, manual)
                if auth_error:
                    outcome = ExecutionOutcome(request_id, True, sent, False, False, reply=reply, error=auth_error, actions=actions, action_results=tuple(results))
                    if mode_args is not None:
                        self._apply_mode_locked(mode_args, bump_generation=not manual)
                    if self._has_pending_motor_result(tuple(results)):
                        self._pending_motor_outcome = outcome
                    self._record_outcome_locked(outcome)
                    return outcome
                if action["name"] == "set_mode":
                    mode_args = action["arguments"]
                    results.append({"name": "set_mode", "sent": False, "completed": True})
                    continue
                action_results = self._send_action_locked(action)
                sent = sent or any(result["sent"] for result in action_results)
                results.extend(action_results)
            status = self._motor_status_locked()
            if status["error"]:
                results = list(self._mark_motor_results(tuple(results), completed=False, error=status["error"]))
                outcome = ExecutionOutcome(request_id, True, sent, False, False, reply=reply, error=status["error"], actions=actions, action_results=tuple(results))
            elif status["busy"] and has_motor_action:
                outcome = ExecutionOutcome(request_id, True, sent, False, False, reply=reply, actions=actions, action_results=tuple(results))
            else:
                outcome = ExecutionOutcome(request_id, True, sent, True, False, reply=reply, actions=actions, action_results=tuple(results))
        except _ActionDispatchError as exc:
            sent = sent or exc.sent
            results.extend(exc.results)
            outcome = ExecutionOutcome(request_id, True, sent, False, False, reply=reply, error=str(exc), actions=actions, action_results=tuple(results))
        except Exception as exc:  # noqa: BLE001 - turn actuator failures into visible state.
            outcome = ExecutionOutcome(request_id, True, sent, False, False, reply=reply, error=str(exc), actions=actions, action_results=tuple(results))
        if mode_args is not None:
            self._apply_mode_locked(mode_args, bump_generation=not manual)
        if self._has_pending_motor_result(tuple(results)):
            self._pending_motor_outcome = outcome
        self._record_outcome_locked(outcome)
        return outcome

    def _authorization_error_locked(
        self,
        actions: tuple[dict[str, Any], ...],
        expected_generation: int | None,
        deadline: float | None,
        captured_at: float | None,
        manual: bool,
        *,
        check_motor_busy: bool = True,
    ) -> str | None:
        now = self.clock()
        if deadline is not None and not math.isfinite(deadline):
            return "AI plan deadline must be finite"
        if expected_generation is not None and self._generation != expected_generation:
            return "stale generation; AI plan dropped after stop/manual/disarm"
        if deadline is not None and now > deadline:
            return "AI plan expired before execution"
        if captured_at is not None and (not math.isfinite(captured_at) or captured_at > now or now - captured_at > self.config.brain.plan_ttl_s):
            return "camera frame is stale or from the future"
        if self.with_outputs and not self._outputs_ready_locked():
            return self._lifecycle_block_error_locked("dispatch")
        needs_output = any(action["name"] in {"play_recording", "move_joints", "set_light", "clear_light"} for action in actions)
        if self.with_outputs and needs_output and not self._armed:
            return "physical outputs not armed"
        has_motor_action = any(action["name"] in MOTOR_ACTIONS for action in actions)
        if check_motor_busy and has_motor_action and self._motor_status_locked()["busy"]:
            return "motor busy; motor action not sent"
        return None

    def _send_action_locked(self, action: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        name = action["name"]
        args = action["arguments"]
        if name == "play_recording":
            self._call_play_recording(str(args["name"]))
            return ({"name": name, "sent": True, "completed": not self._motor_status_locked()["busy"]},)
        if name == "move_joints":
            commands = tuple(JointDeltaCommand(joint, float(delta)) for joint, delta in args["deltas"].items())
            assert self.motor is not None
            self.motor.apply_joint_deltas(commands)
            self._refresh_positions_locked()
            return ({"name": name, "sent": True, "completed": not self._motor_status_locked()["busy"]},)
        if name == "set_light":
            assert self.led is not None
            try:
                solid_ack = self.led.solid(args["red"], args["green"], args["blue"])
            except Exception as exc:  # noqa: BLE001 - report per-substep LED failures.
                solid_error = str(exc)
                raise _ActionDispatchError(
                    solid_error,
                    sent=False,
                    results=({"name": "set_light.solid", "sent": False, "completed": False, "error": solid_error},),
                ) from exc
            solid_error = self._ack_error(solid_ack)
            if solid_error is not None:
                raise _ActionDispatchError(solid_error, sent=False, results=({"name": "set_light.solid", "sent": False, "completed": False, "error": solid_error},))
            self._light.update({key: args[key] for key in ("red", "green", "blue")})
            results: list[dict[str, Any]] = [{"name": "set_light.solid", "sent": True, "completed": True}]
            if "brightness" in args:
                try:
                    brightness_ack = self._set_led_brightness(args["brightness"])
                except Exception as exc:  # noqa: BLE001 - preserve successful solid result.
                    brightness_error = str(exc)
                    results.append({"name": "set_light.brightness", "sent": False, "completed": False, "error": brightness_error})
                    raise _ActionDispatchError(brightness_error, sent=True, results=tuple(results)) from exc
                brightness_error = self._ack_error(brightness_ack)
                if brightness_error is not None:
                    results.append({"name": "set_light.brightness", "sent": False, "completed": False, "error": brightness_error})
                    raise _ActionDispatchError(brightness_error, sent=True, results=tuple(results))
                self._light["brightness"] = args["brightness"]
                results.append({"name": "set_light.brightness", "sent": True, "completed": True})
            return tuple(results)
        if name == "clear_light":
            assert self.led is not None
            try:
                if hasattr(self.led, "clear"):
                    ack = self.led.clear()
                else:
                    ack = self.led.solid(0, 0, 0)
            except Exception as exc:  # noqa: BLE001 - report per-action LED cleanup failures.
                clear_error = str(exc)
                raise _ActionDispatchError(
                    clear_error,
                    sent=False,
                    results=({"name": name, "sent": False, "completed": False, "error": clear_error},),
                ) from exc
            ack_error = self._ack_error(ack)
            if ack_error is not None:
                raise _ActionDispatchError(ack_error, sent=False, results=({"name": name, "sent": False, "completed": False, "error": ack_error},))
            self._light.update({"red": 0, "green": 0, "blue": 0})
            return ({"name": name, "sent": True, "completed": True},)
        if name == "do_nothing":
            return ({"name": name, "sent": False, "completed": True, "reason": args["reason"]},)
        raise ValueError(f"unknown action: {name}")

    def _apply_virtual_locked(self, action: dict[str, Any]) -> dict[str, Any]:
        name = action["name"]
        args = action["arguments"]
        if name == "move_joints":
            for joint, delta in args["deltas"].items():
                key = f"{joint}.pos"
                self._positions[key] = max(-100.0, min(100.0, self._positions.get(key, 0.0) + float(delta)))
            if isinstance(self.motor, DryRunMotorService):
                self.motor.positions = dict(self._positions)
            self._position_source = "virtual"
        elif name == "set_light":
            self._light.update({key: args[key] for key in ("red", "green", "blue")})
            if "brightness" in args:
                self._light["brightness"] = args["brightness"]
        elif name == "clear_light":
            self._light.update({"red": 0, "green": 0, "blue": 0})
        elif name == "set_mode":
            self._apply_mode_locked(args, bump_generation=False)
        return {"name": name, "sent": False, "completed": True, "virtual": True}

    def _manual_override_locked(self, *, stop_motor: bool = True) -> None:
        self._generation += 1
        self._auto_enabled = False
        self._focus_deadline = None
        self._focus_generation = None
        if stop_motor:
            self._cancel_pending_motor_locked("cancelled by manual override")
        if stop_motor and self.with_outputs and self._outputs_ready_locked() and self.motor is not None and hasattr(self.motor, "stop_motion"):
            self.motor.stop_motion()

    def _call_play_recording(self, name: str) -> None:
        assert self.motor is not None
        if hasattr(self.motor, "play"):
            self.motor.play(name)
        else:
            self.motor.play_recording(name)

    def _set_led_brightness(self, value: int) -> object:
        assert self.led is not None
        if hasattr(self.led, "set_brightness"):
            return self.led.set_brightness(value)
        return self.led.brightness(value)

    def _apply_mode_locked(self, args: dict[str, Any], *, bump_generation: bool) -> None:
        if bump_generation:
            self._generation += 1
        self._mode = str(args["mode"])
        if self._mode == "manual":
            self._auto_enabled = False
            self._focus_deadline = None
            self._focus_generation = None
        elif self._mode == "focus":
            self._focus_deadline = self.clock() + int(args["timer_minutes"]) * 60 if "timer_minutes" in args else None
            self._focus_generation = self._generation if "timer_minutes" in args else None
        else:
            self._focus_deadline = None
            self._focus_generation = None

    def _record_plan_locked(self, plan: object, actions: tuple[dict[str, Any], ...]) -> None:
        self._last_plan = {
            "reply": str(getattr(plan, "reply", "") or ""),
            "source": str(getattr(plan, "source", "") or ""),
            "model": str(getattr(plan, "model", "") or ""),
            "actions": list(actions),
        }

    def _recorded(self, outcome: ExecutionOutcome) -> ExecutionOutcome:
        with self._lock:
            self._record_outcome_locked(outcome)
            return outcome

    def _record_outcome_locked(self, outcome: ExecutionOutcome) -> None:
        self._last_outcome = outcome
        self._history.append(outcome.to_dict())
        self._history = self._history[-24:]

    def _refresh_positions_locked(self, *, raise_errors: bool = False) -> None:
        if self.motor is None:
            return
        if not self.with_outputs and self._position_source == "virtual":
            return
        positions = None
        try:
            if hasattr(self.motor, "get_positions"):
                positions = self.motor.get_positions()
            elif hasattr(self.motor, "positions"):
                positions = getattr(self.motor, "positions")
            elif hasattr(self.motor, "current_state"):
                positions = getattr(self.motor, "current_state")
        except Exception:
            if raise_errors:
                raise
            return
        if isinstance(positions, dict):
            self._positions = {str(key): float(value) for key, value in positions.items() if isinstance(value, (int, float)) and math.isfinite(float(value))}
        source = getattr(self.motor, "position_source", None)
        if isinstance(source, str):
            self._position_source = source
        elif self.with_outputs and self._positions:
            self._position_source = "sent_targets"

    def _motor_status_locked(self) -> dict[str, Any]:
        if self.motor is None:
            return {"busy": False, "error": None}
        busy = bool(getattr(self.motor, "is_busy", False))
        err = getattr(self.motor, "last_error", None)
        return {"busy": busy, "error": None if err is None else str(err)}

    def _outputs_ready_locked(self) -> bool:
        return self._opened and self._lifecycle_state == "open"

    def _lifecycle_block_error_locked(self, operation: str) -> str:
        if self._lifecycle_state == "closing":
            return LIFECYCLE_CLOSING
        if self._lifecycle_state == "cleanup_failed":
            return LIFECYCLE_CLEANUP_FAILED
        return LIFECYCLE_NOT_OPEN

    def _cancel_pending_motor_locked(self, reason: str) -> None:
        if self._pending_motor_outcome is None:
            return
        pending = self._pending_motor_outcome
        self._pending_motor_outcome = None
        results = self._mark_motor_results(pending.action_results, completed=False, error=reason)
        self._history.append(
            ExecutionOutcome(
                pending.request_id,
                True,
                pending.sent,
                False,
                pending.dry_run,
                reply=pending.reply,
                error=reason,
                actions=pending.actions,
                action_results=results,
            ).to_dict()
        )
        self._history = self._history[-24:]

    def _settle_pending_motor_if_terminal_locked(self) -> ExecutionOutcome | None:
        if self._pending_motor_outcome is None:
            return None
        pending = self._pending_motor_outcome
        motor_status = self._motor_status_locked()
        if motor_status["error"]:
            results = self._mark_motor_results(pending.action_results, completed=False, error=motor_status["error"])
            error = self._combine_errors(pending.error, motor_status["error"])
            outcome = ExecutionOutcome(
                pending.request_id,
                True,
                True,
                False,
                False,
                reply=pending.reply,
                error=error,
                actions=pending.actions,
                action_results=results,
            )
            self._pending_motor_outcome = None
            self._record_outcome_locked(outcome)
            return outcome
        if not motor_status["busy"]:
            results = self._mark_motor_results(pending.action_results, completed=True)
            completed = pending.error is None
            outcome = ExecutionOutcome(
                pending.request_id,
                True,
                True,
                completed,
                False,
                reply=pending.reply,
                error=pending.error,
                actions=pending.actions,
                action_results=results,
            )
            self._pending_motor_outcome = None
            self._record_outcome_locked(outcome)
            return outcome
        return None

    @staticmethod
    def _combine_errors(first: str | None, second: str | None) -> str | None:
        if first and second and first != second:
            return f"{first}; {second}"
        return first or second

    @staticmethod
    def _has_pending_motor_result(results: tuple[dict[str, Any], ...]) -> bool:
        return any(
            result.get("name") in MOTOR_ACTIONS
            and result.get("sent") is True
            and result.get("completed") is False
            and result.get("error") is None
            for result in results
        )

    @staticmethod
    def _mark_motor_results(
        results: tuple[dict[str, Any], ...],
        *,
        completed: bool,
        error: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        updated: list[dict[str, Any]] = []
        for result in results:
            if result.get("name") in MOTOR_ACTIONS:
                changed = dict(result)
                changed["completed"] = completed
                if error is not None:
                    changed["error"] = error
                updated.append(changed)
            else:
                updated.append(result)
        return tuple(updated)

    @staticmethod
    def _ack_error(ack: object) -> str | None:
        if not isinstance(ack, str):
            return f"invalid LED ACK: {ack!r}"
        text = ack.strip()
        if text == "OK" or text.startswith("OK "):
            return None
        if text.startswith("ERR"):
            return text
        return f"invalid LED ACK: {ack!r}"

    def _validate_plan_actions(self, raw_actions: tuple[object, ...]) -> tuple[dict[str, Any], ...]:
        external = self._external_validate(raw_actions)
        if external is not None:
            raw_actions = external
        if len(raw_actions) > 2:
            raise ValueError("AI plan may contain at most 2 actions")
        motor_count = sum(1 for action in raw_actions if getattr(action, "name", "") in MOTOR_ACTIONS)
        if motor_count > 1:
            raise ValueError("AI plan may contain at most 1 motor action")
        return tuple(self._validate_action(action) for action in raw_actions)

    def _external_validate(self, raw_actions: tuple[object, ...]) -> tuple[object, ...] | None:
        try:
            from ailamp.services.brain import BrainAction, validate_actions
        except Exception:
            return None
        actions = tuple(BrainAction(str(getattr(action, "name", "")), dict(getattr(action, "arguments", {}) or {})) for action in raw_actions)
        return tuple(validate_actions(actions, self.recording_names))

    def _validate_action(self, action: object) -> dict[str, Any]:
        name = str(getattr(action, "name", ""))
        args = getattr(action, "arguments", None)
        if not isinstance(args, dict):
            raise ValueError(f"action {name or '<missing>'} arguments must be an object")
        if name == "play_recording":
            recording = args.get("name")
            if not isinstance(recording, str) or recording not in self.recording_names:
                raise ValueError(f"unknown recording: {recording!r}")
            return {"name": name, "arguments": {"name": recording}}
        if name == "move_joints":
            deltas = args.get("deltas")
            if not isinstance(deltas, dict) or not deltas:
                raise ValueError("move_joints requires non-empty deltas object")
            checked: dict[str, float] = {}
            for joint, value in deltas.items():
                if joint not in JOINT_NAMES:
                    raise ValueError(f"unknown joint: {joint!r}")
                delta = _finite_float(value, f"{joint} delta")
                if abs(delta) > 4:
                    raise ValueError(f"{joint} delta exceeds +/-4 normalized units")
                checked[str(joint)] = delta
            return {"name": name, "arguments": {"deltas": checked}}
        if name == "set_light":
            checked_light = {
                "red": _uint8(args.get("red"), "red"),
                "green": _uint8(args.get("green"), "green"),
                "blue": _uint8(args.get("blue"), "blue"),
            }
            if "brightness" in args:
                checked_light["brightness"] = _uint8(args.get("brightness"), "brightness")
            return {"name": name, "arguments": checked_light}
        if name == "set_mode":
            mode = args.get("mode")
            if mode not in VALID_MODES:
                raise ValueError(f"unknown mode: {mode!r}")
            checked_mode: dict[str, Any] = {"mode": mode}
            if "timer_minutes" in args:
                timer = args["timer_minutes"]
                if not isinstance(timer, int) or isinstance(timer, bool) or timer < 1 or timer > 180:
                    raise ValueError("timer_minutes must be an integer from 1 to 180")
                checked_mode["timer_minutes"] = timer
            return {"name": name, "arguments": checked_mode}
        if name == "do_nothing":
            reason = args.get("reason")
            if not isinstance(reason, str):
                raise ValueError("do_nothing requires a reason string")
            return {"name": name, "arguments": {"reason": reason}}
        raise ValueError(f"unknown action: {name!r}")


def _finite_float(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _uint8(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 255:
        raise ValueError(f"{label} must be an integer from 0 to 255")
    return value
