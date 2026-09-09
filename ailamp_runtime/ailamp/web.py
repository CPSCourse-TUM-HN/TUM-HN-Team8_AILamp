from __future__ import annotations

import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import secrets
from typing import Any
from urllib.parse import urlparse

from ailamp.config import HardwareConfig
from ailamp.services.brain_runtime import BrainRuntime
from ailamp.services.controller import LampController


MAX_BODY_BYTES = 16 * 1024
MAX_JSON_DEPTH = 8
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
WILDCARD_HOSTS = {"", "0.0.0.0", "::"}


class AILampHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        *,
        config: HardwareConfig,
        controller: LampController,
        runtime: BrainRuntime,
        external_token: str | None = None,
        max_body_bytes: int = MAX_BODY_BYTES,
        bind_and_activate: bool = True,
    ):
        if external_token is not None and len(external_token) < 32:
            raise ValueError("non-loopback web-control requires a strong token of at least 32 characters")
        super().__init__(server_address, RequestHandlerClass, bind_and_activate=bind_and_activate)
        self.config = config
        self.controller = controller
        self.runtime = runtime
        self.external_token = external_token
        self.csrf_token = secrets.token_urlsafe(32)
        self.max_body_bytes = max_body_bytes


def create_server(
    address: tuple[str, int],
    config: HardwareConfig,
    *,
    controller: LampController | None = None,
    runtime: BrainRuntime | None = None,
    with_outputs: bool = False,
    brain_enabled: bool | None = None,
    vision_enabled: bool = False,
    external_token: str | None = None,
    bind_and_activate: bool = True,
) -> AILampHTTPServer:
    host = address[0]
    if host in WILDCARD_HOSTS:
        raise ValueError("web-control requires a concrete host; use 127.0.0.1 or the Nano LAN IPv4 address")
    if host not in LOCAL_HOSTS and (not external_token or len(external_token) < 32):
        raise ValueError("non-loopback web-control bind requires a strong token from the environment")
    controller = controller or LampController(config, with_outputs=with_outputs)
    runtime = runtime or BrainRuntime(config, controller=controller, brain_enabled=brain_enabled, vision_enabled=vision_enabled)
    return AILampHTTPServer(
        address,
        _Handler,
        config=config,
        controller=controller,
        runtime=runtime,
        external_token=external_token,
        bind_and_activate=bind_and_activate,
    )


class _Handler(BaseHTTPRequestHandler):
    server: AILampHTTPServer

    def setup(self) -> None:
        super().setup()
        try:
            self.connection.settimeout(3)
        except Exception:
            pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if not self._host_allowed() or not self._origin_allowed(require_origin=False):
            return
        if path == "/":
            self._send_html(_index_html())
            return
        if path == "/api/state":
            if not self._token_allowed_if_required():
                return
            self._send_json(
                {
                    "csrf_token": self.server.csrf_token,
                    "controller": self.server.controller.snapshot(),
                    "brain": self.server.runtime.snapshot(),
                }
            )
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not_found", "unknown path")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/api/chat", "/api/observe", "/api/stop", "/api/arm", "/api/manual", "/api/auto"}:
            self._send_error(HTTPStatus.NOT_FOUND, "not_found", "unknown path")
            return
        if not self._mutation_allowed():
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            if path == "/api/chat":
                self._require_fields(payload, {"text"}, required={"text"})
                text = payload["text"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("text must be a non-empty string")
                outcome = self.server.runtime.submit_text(text)
                self._send_state(outcome)
                return
            if path == "/api/observe":
                self._require_fields(payload, {"instruction"})
                instruction = payload.get("instruction")
                if instruction is not None and not isinstance(instruction, str):
                    raise ValueError("instruction must be a string")
                outcome = self.server.runtime.observe_once(instruction)
                self._send_state(outcome)
                return
            if path == "/api/stop":
                self._require_fields(payload, set())
                self._send_state(self.server.runtime.stop())
                return
            if path == "/api/arm":
                self._require_fields(payload, {"enabled"}, required={"enabled"})
                enabled = payload["enabled"]
                if not isinstance(enabled, bool):
                    raise ValueError("enabled must be boolean")
                self._send_json({"state": self.server.controller.arm(enabled), "brain": self.server.runtime.snapshot()})
                return
            if path == "/api/auto":
                self._require_fields(payload, {"enabled"}, required={"enabled"})
                enabled = payload["enabled"]
                if not isinstance(enabled, bool):
                    raise ValueError("enabled must be boolean")
                self._send_json({"brain": self.server.runtime.set_auto_enabled(enabled), "state": self.server.controller.snapshot()})
                return
            if path == "/api/manual":
                outcome = self._manual(payload)
                self._send_state(outcome)
                return
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, "bad_request", str(exc))
        except Exception as exc:  # noqa: BLE001 - return bounded actuator/domain errors as JSON.
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "server_error", str(exc))

    def _manual(self, payload: dict[str, Any]):
        self._require_fields(payload, {"action", "arguments"}, required={"action"})
        action = payload.get("action")
        args = payload.get("arguments", {})
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        if action == "play_recording":
            self._require_fields(args, {"name"}, required={"name"})
            name = args.get("name")
            if not isinstance(name, str):
                raise ValueError("name must be a string")
            return self.server.controller.manual_play_recording(name)
        if action == "set_light":
            self._require_fields(args, {"red", "green", "blue", "brightness"}, required={"red", "green", "blue"})
            brightness = args.get("brightness")
            if brightness is not None and (not isinstance(brightness, int) or isinstance(brightness, bool)):
                raise ValueError("brightness must be an integer")
            return self.server.controller.manual_set_light(
                _int_arg(args, "red"),
                _int_arg(args, "green"),
                _int_arg(args, "blue"),
                brightness,
            )
        if action == "clear_light":
            self._require_fields(args, set())
            return self.server.controller.clear_light()
        if action == "set_mode":
            self._require_fields(args, {"mode", "timer_minutes"}, required={"mode"})
            mode = args.get("mode")
            if not isinstance(mode, str):
                raise ValueError("mode must be a string")
            timer = args.get("timer_minutes")
            if timer is not None and (not isinstance(timer, int) or isinstance(timer, bool)):
                raise ValueError("timer_minutes must be an integer")
            return self.server.controller.set_mode(mode, timer)
        raise ValueError("unknown manual action")

    def _mutation_allowed(self) -> bool:
        if not self._host_allowed() or not self._origin_allowed(require_origin=False):
            return False
        if self._header_values("Transfer-Encoding"):
            self._send_error(HTTPStatus.BAD_REQUEST, "bad_request", "Transfer-Encoding is not supported")
            return False
        content_lengths = self._header_values("Content-Length")
        if len(content_lengths) != 1:
            self._send_error(HTTPStatus.LENGTH_REQUIRED, "bad_length", "exactly one Content-Length header is required")
            return False
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self._send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type", "Content-Type must be application/json")
            return False
        if self.headers.get("X-CSRF-Token") != self.server.csrf_token:
            self._send_error(HTTPStatus.FORBIDDEN, "forbidden", "invalid CSRF token")
            return False
        return self._token_allowed_if_required()

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        parsed_host, parsed_port = _split_host_port(host)
        actual_port = int(self.server.server_address[1])
        if parsed_port != actual_port:
            self._send_error(HTTPStatus.FORBIDDEN, "forbidden", "invalid host port")
            return False
        bind_host = self.server.server_address[0]
        allowed = parsed_host in {bind_host, "localhost" if bind_host == "127.0.0.1" else bind_host}
        if not allowed:
            self._send_error(HTTPStatus.FORBIDDEN, "forbidden", "invalid host")
            return False
        return True

    def _origin_allowed(self, *, require_origin: bool) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            if require_origin:
                self._send_error(HTTPStatus.FORBIDDEN, "forbidden", "missing origin")
                return False
            return True
        parsed = urlparse(origin)
        if parsed.scheme != "http" or not parsed.netloc:
            self._send_error(HTTPStatus.FORBIDDEN, "forbidden", "invalid origin")
            return False
        if parsed.netloc != self.headers.get("Host", ""):
            self._send_error(HTTPStatus.FORBIDDEN, "forbidden", "origin does not match host")
            return False
        return True

    def _token_allowed_if_required(self) -> bool:
        token = self.server.external_token
        if token is None:
            return True
        supplied = self.headers.get("X-AILamp-Token", "")
        if not hmac.compare_digest(supplied, token):
            self._send_error(HTTPStatus.FORBIDDEN, "forbidden", "invalid control token")
            return False
        return True

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_error(HTTPStatus.LENGTH_REQUIRED, "bad_length", "invalid Content-Length")
            return None
        if length < 0:
            self._send_error(HTTPStatus.LENGTH_REQUIRED, "bad_length", "Content-Length cannot be negative")
            return None
        if length > self.server.max_body_bytes:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large", "request body too large")
            return None
        try:
            raw_bytes = self.rfile.read(length)
            if len(raw_bytes) != length:
                self._send_error(HTTPStatus.BAD_REQUEST, "bad_request", "request body ended early")
                return None
            raw = raw_bytes.decode("utf-8")
            payload = json.loads(raw or "{}")
        except UnicodeDecodeError:
            self._send_error(HTTPStatus.BAD_REQUEST, "bad_utf8", "request body must be UTF-8")
            return None
        except json.JSONDecodeError:
            self._send_error(HTTPStatus.BAD_REQUEST, "bad_json", "request body must be JSON")
            return None
        if not isinstance(payload, dict):
            self._send_error(HTTPStatus.BAD_REQUEST, "bad_json", "request body must be a JSON object")
            return None
        if _json_depth(payload) > MAX_JSON_DEPTH or _contains_nonfinite(payload):
            self._send_error(HTTPStatus.BAD_REQUEST, "bad_json", "request JSON is outside allowed bounds")
            return None
        return payload

    def _send_state(self, outcome) -> None:
        self._send_json({"outcome": outcome.to_dict(), "state": self.server.controller.snapshot(), "brain": self.server.runtime.snapshot()})

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._send_json({"error": {"code": code, "message": message}}, status=status)

    def _require_fields(self, payload: dict[str, Any], allowed: set[str], *, required: set[str] | None = None) -> None:
        required = required or set()
        unknown = set(payload) - allowed
        missing = required - set(payload)
        if unknown:
            raise ValueError(f"unknown field: {sorted(unknown)[0]}")
        if missing:
            raise ValueError(f"missing field: {sorted(missing)[0]}")

    def _header_values(self, name: str) -> list[str]:
        get_all = getattr(self.headers, "get_all", None)
        if callable(get_all):
            return list(get_all(name) or [])
        value = self.headers.get(name)
        return [] if value is None else [value]

    def log_message(self, format, *args) -> None:  # noqa: A003 - BaseHTTPRequestHandler API.
        return


def _split_host_port(value: str) -> tuple[str, int | None]:
    if value.startswith("["):
        host, _, tail = value[1:].partition("]")
        if not tail.startswith(":"):
            return host, None
        port = tail[1:]
    else:
        host, sep, port = value.rpartition(":")
        if not sep:
            return value, None
    try:
        return host, int(port)
    except ValueError:
        return host, None


def _int_arg(args: dict[str, Any], key: str) -> int:
    value = args.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _json_depth(value: Any, depth: int = 0) -> int:
    if isinstance(value, dict):
        return max([depth] + [_json_depth(child, depth + 1) for child in value.values()])
    if isinstance(value, list):
        return max([depth] + [_json_depth(child, depth + 1) for child in value])
    return depth


def _contains_nonfinite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_nonfinite(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_nonfinite(child) for child in value)
    return False


def _index_html() -> str:
    path = Path(__file__).with_name("web").joinpath("index.html")
    return path.read_text(encoding="utf-8")
