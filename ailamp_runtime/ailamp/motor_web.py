"""Motor-only web page: a thin HTTP adapter over :class:`MotorDemoRuntime`.

Every route maps one-to-one onto a runtime method.  The web layer adds no
motion semantics of its own: it never auto-connects, never auto-arms, never
retries, and never releases torque unless the operator confirms.  Runtime
refusals surface verbatim as HTTP 409.
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import signal
import sys
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from typing import Any, Callable, TextIO
from urllib.parse import urlparse

from ailamp import motor_cli
from ailamp.motor_web_http import (
    LOCAL_HOSTS,
    MAX_BODY_BYTES,
    WILDCARD_HOSTS,
    HardenedJSONHandler,
)
from ailamp.motor_web_ui import INDEX_HTML
from ailamp.services.motor import RecordingStore
from ailamp.services.motor_backend import (
    DEFAULT_ACCELERATION_LIMITS,
    DEFAULT_GOAL_VELOCITY_LIMITS,
    DEFAULT_TORQUE_LIMITS,
)
from ailamp.services.motor_runtime import DEFAULT_SCRIPT, MotorDemoRuntime, MotorRuntimeEvent


LOGGER = logging.getLogger(__name__)

MAX_EVENTS = 50
MIN_EXTERNAL_TOKEN_LENGTH = 32
MAX_TCP_PORT = 65535
DEFAULT_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8766
DEFAULT_TOKEN_ENV = "AILAMP_MOTOR_WEB_TOKEN"
MODE_SIMULATION = "simulation"
MODE_HARDWARE = "hardware"
MODES = frozenset({MODE_SIMULATION, MODE_HARDWARE})
CONFIRMATION_VALUE = "CONFIRM"
SIGNAL_EXIT_CODE = 130
ARM_FIELDS = frozenset({"goal_velocity", "acceleration", "torque_limit", "confirm"})
ARM_LIMITS: dict[str, tuple[int, int]] = {
    "goal_velocity": DEFAULT_GOAL_VELOCITY_LIMITS,
    "acceleration": DEFAULT_ACCELERATION_LIMITS,
    "torque_limit": DEFAULT_TORQUE_LIMITS,
}
MODE_LINES = {
    MODE_SIMULATION: "模式：离线模拟（不会打开硬件串口）。",
    MODE_HARDWARE: "模式：真实硬件（connect 只读；arm 才写入）。",
}

ServeFunction = Callable[["MotorWebServer", MotorDemoRuntime, TextIO], int]


class MotorWebServer(ThreadingHTTPServer):
    """One process, one runtime, one bus owner."""

    daemon_threads = True

    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        *,
        runtime: MotorDemoRuntime,
        recordings: RecordingStore,
        mode: str,
        external_token: str | None = None,
        max_body_bytes: int = MAX_BODY_BYTES,
        bind_and_activate: bool = True,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass, bind_and_activate=bind_and_activate)
        self.runtime = runtime
        self.recordings = recordings
        self.mode = mode
        self.external_token = external_token
        self.csrf_token = secrets.token_urlsafe(32)
        self.max_body_bytes = max_body_bytes

    def state_snapshot(self) -> dict[str, Any]:
        """Fresh, JSON-ready view of the page state; never shares runtime dicts."""
        return {
            "csrf_token": self.csrf_token,
            "mode": self.mode,
            "recordings": list(self.recordings.list_names()),
            "default_script": list(DEFAULT_SCRIPT),
            "arm_limits": {name: list(limits) for name, limits in ARM_LIMITS.items()},
            "status": self.runtime.status(),
            "events": _serialize_events(self.runtime.events),
        }


def create_server(
    address: tuple[str, int],
    runtime: MotorDemoRuntime,
    *,
    recordings_dir,
    mode: str,
    external_token: str | None = None,
    bind_and_activate: bool = True,
) -> MotorWebServer:
    """Validate bind rules, then build the server around one runtime."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}")
    host = address[0]
    if host in WILDCARD_HOSTS:
        raise ValueError("motor web page requires a concrete host; use 127.0.0.1 or the Nano LAN IPv4 address")
    if host not in LOCAL_HOSTS and (not external_token or len(external_token) < MIN_EXTERNAL_TOKEN_LENGTH):
        raise ValueError(
            f"non-loopback bind requires a strong token of at least {MIN_EXTERNAL_TOKEN_LENGTH} characters"
        )
    return MotorWebServer(
        address,
        _Handler,
        runtime=runtime,
        recordings=RecordingStore(recordings_dir),
        mode=mode,
        external_token=external_token,
        bind_and_activate=bind_and_activate,
    )


class _Handler(HardenedJSONHandler):
    server: MotorWebServer

    _POST_ROUTES = {
        "/api/connect": "_post_connect",
        "/api/arm": "_post_arm",
        "/api/play": "_post_play",
        "/api/script": "_post_script",
        "/api/home": "_post_home",
        "/api/stop": "_post_stop",
        "/api/release": "_post_release",
        "/api/capture-home": "_post_capture_home",
        "/api/confirm": "_post_confirm",
    }

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if not self._read_allowed():
            return
        if path == "/":
            self._send_html(INDEX_HTML)
            return
        if path == "/api/state":
            if self._token_allowed_if_required():
                self._send_json(self.server.state_snapshot())
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not_found", "unknown path")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        route_name = self._POST_ROUTES.get(path)
        if route_name is None:
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", "unknown path")
            return
        if not self._mutation_allowed():
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            extras = getattr(self, route_name)(payload)
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
        except (RuntimeError, TimeoutError) as exc:
            # Safety refusals from the runtime; relayed verbatim, never retried.
            self._send_error(HTTPStatus.CONFLICT, "runtime_refused", str(exc))
        except Exception:  # noqa: BLE001 - last resort: log, report, keep serving.
            LOGGER.exception("motor web request failed: %s", path)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "internal error")
        else:
            self._send_json({"ok": True, "state": self.server.state_snapshot(), **extras})

    def _post_connect(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_fields(payload, set())
        self.server.runtime.connect()
        return {}

    def _post_arm(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_fields(payload, ARM_FIELDS, required=ARM_FIELDS)
        _require_confirmation(payload)
        self.server.runtime.arm(
            goal_velocity=_int_field(payload, "goal_velocity"),
            acceleration=_int_field(payload, "acceleration"),
            torque_limit=_int_field(payload, "torque_limit"),
            operator_confirmed=True,
        )
        return {}

    def _post_play(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_fields(payload, {"name"}, required={"name"})
        name = _string_field(payload, "name")
        if name not in self.server.recordings.list_names():
            raise ValueError(f"unknown recording: {name}")
        self.server.runtime.play(name)
        return {}

    def _post_script(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_fields(payload, set())
        missing = _missing_script_recordings(self.server.recordings)
        if missing:
            raise ValueError(f"script recordings missing: {missing}")
        self.server.runtime.play_script()
        return {}

    def _post_home(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_fields(payload, set())
        self.server.runtime.home()
        return {}

    def _post_stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_fields(payload, set())
        held = self.server.runtime.stop()
        return {"held": dict(held)}

    def _post_release(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_fields(payload, {"confirm"}, required={"confirm"})
        _require_confirmation(payload)
        self.server.runtime.release(operator_confirmed=True)
        return {}

    def _post_capture_home(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_fields(payload, {"confirm"}, required={"confirm"})
        _require_confirmation(payload)
        home = self.server.runtime.capture_home(operator_confirmed=True)
        return {"home": dict(home["normalized_positions"])}

    def _post_confirm(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_fields(payload, {"task", "note"}, required={"task"})
        task = _string_field(payload, "task").strip()
        if not task:
            raise ValueError("task must be a non-empty string")
        note = payload.get("note", "")
        if not isinstance(note, str):
            raise ValueError("note must be a string")
        self.server.runtime.confirm_visible(task, note)
        return {}


def build_parser() -> argparse.ArgumentParser:
    """CLI options: the runtime options shared with motor_cli plus HTTP binding."""
    parser = argparse.ArgumentParser(
        prog="python3 -m ailamp.motor_web",
        description="AILamp 五轴电机操作页面（默认仅模拟）",
    )
    motor_cli.add_runtime_arguments(parser)
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址；非 loopback 需要控制令牌")
    parser.add_argument("--http-port", type=_port_type, default=DEFAULT_HTTP_PORT)
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help="非 loopback 绑定时，从该环境变量读取至少 32 字符的控制令牌",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    output: TextIO = sys.stdout,
    runtime_factory: Callable[[argparse.Namespace], MotorDemoRuntime] | None = None,
    serve: ServeFunction | None = None,
) -> int:
    """Build one runtime, bind the page, and serve until interrupted."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    mode = MODE_HARDWARE if args.hardware else MODE_SIMULATION
    try:
        runtime = (runtime_factory or motor_cli.build_runtime)(args)
        token = _external_token(args)
        server = create_server(
            (args.host, args.http_port),
            runtime,
            recordings_dir=args.recordings_dir,
            mode=mode,
            external_token=token,
        )
    except Exception as exc:  # noqa: BLE001 - report every startup refusal the same way as the CLI.
        print(f"错误：{exc}", file=output)
        return 2
    host, port = server.server_address[:2]
    print(MODE_LINES[mode], file=output)
    print(f"操作页面：http://{host}:{port}/ （Ctrl-C 或 SIGTERM 会先尝试 stop 再关闭）", file=output)
    return (serve or serve_forever)(server, runtime, output)


def serve_forever(server: MotorWebServer, runtime: MotorDemoRuntime, output: TextIO) -> int:
    """Serve until SIGINT/SIGTERM, then run the runtime's stop-then-close shutdown."""
    previous_term = signal.getsignal(signal.SIGTERM)

    def _raise_keyboard_interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    try:
        server.serve_forever()
        return 0
    except KeyboardInterrupt:
        print("", file=output)
        print(runtime.shutdown_for_eof_or_signal("signal"), file=output)
        return SIGNAL_EXIT_CODE
    finally:
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
        server.server_close()


def _external_token(args: argparse.Namespace) -> str | None:
    """Token from the environment; required (and strong) for non-loopback binds."""
    token = os.environ.get(args.token_env)
    if args.host in LOCAL_HOSTS:
        return token or None
    if not token or len(token) < MIN_EXTERNAL_TOKEN_LENGTH:
        raise ValueError(
            f"非 loopback 绑定需要在环境变量 {args.token_env} 中提供至少 "
            f"{MIN_EXTERNAL_TOKEN_LENGTH} 字符的控制令牌"
        )
    return token


def _serialize_events(events: tuple[MotorRuntimeEvent, ...]) -> list[dict[str, Any]]:
    return [
        {
            "kind": event.kind,
            "task": event.task,
            "timestamp": event.timestamp,
            "details": dict(event.details),
        }
        for event in events[-MAX_EVENTS:]
    ]


def _missing_script_recordings(recordings: RecordingStore) -> list[str]:
    available = set(recordings.list_names())
    return [step for step in DEFAULT_SCRIPT if step != "home" and step not in available]


def _require_confirmation(payload: dict[str, Any]) -> None:
    if payload.get("confirm") != CONFIRMATION_VALUE:
        raise ValueError(f"confirm must be {CONFIRMATION_VALUE}")


def _int_field(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _string_field(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _port_type(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("http-port 必须是整数") from exc
    if number < 0 or number > MAX_TCP_PORT:
        raise argparse.ArgumentTypeError(f"http-port 必须位于 [0, {MAX_TCP_PORT}]")
    return number


if __name__ == "__main__":
    raise SystemExit(main())
