"""Hardened JSON request handler shared by the motor web page.

Mirrors the guards used by ``ailamp.web`` (same-origin host check, CSRF token,
optional external token, bounded JSON bodies) without importing that older
LED/brain console.  The owning server must expose ``csrf_token``,
``external_token`` and ``max_body_bytes``.
"""

from __future__ import annotations

import hmac
import json
import math
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse


MAX_BODY_BYTES = 16 * 1024
MAX_JSON_DEPTH = 8
SOCKET_TIMEOUT_SECONDS = 3
CSRF_HEADER = "X-CSRF-Token"
EXTERNAL_TOKEN_HEADER = "X-AILamp-Token"
JSON_CONTENT_TYPE = "application/json"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
WILDCARD_HOSTS = frozenset({"", "0.0.0.0", "::"})


class HardenedJSONHandler(BaseHTTPRequestHandler):
    """Base handler: reads are same-origin, mutations also need CSRF + JSON."""

    def setup(self) -> None:
        super().setup()
        try:
            self.connection.settimeout(SOCKET_TIMEOUT_SECONDS)
        except OSError:
            # A socket that refuses timeouts still serves; the guard is best effort.
            pass

    def _read_allowed(self) -> bool:
        """Host must match the bind address; Origin, when present, must match Host."""
        return self._host_allowed() and self._origin_allowed(require_origin=False)

    def _mutation_allowed(self) -> bool:
        """Everything a read needs, plus one JSON body and a valid CSRF token."""
        if not self._read_allowed():
            return False
        if self._header_values("Transfer-Encoding"):
            self._send_error(HTTPStatus.BAD_REQUEST, "bad_request", "Transfer-Encoding is not supported")
            return False
        if len(self._header_values("Content-Length")) != 1:
            self._send_error(
                HTTPStatus.LENGTH_REQUIRED, "bad_length", "exactly one Content-Length header is required"
            )
            return False
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != JSON_CONTENT_TYPE:
            self._send_error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type", "Content-Type must be application/json"
            )
            return False
        if self.headers.get(CSRF_HEADER) != self.server.csrf_token:
            self._send_error(HTTPStatus.FORBIDDEN, "forbidden", "invalid CSRF token")
            return False
        return self._token_allowed_if_required()

    def _host_allowed(self) -> bool:
        parsed_host, parsed_port = split_host_port(self.headers.get("Host", ""))
        if parsed_port != int(self.server.server_address[1]):
            self._send_error(HTTPStatus.FORBIDDEN, "forbidden", "invalid host port")
            return False
        bind_host = self.server.server_address[0]
        aliases = {bind_host, "localhost" if bind_host == "127.0.0.1" else bind_host}
        if parsed_host not in aliases:
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
        supplied = self.headers.get(EXTERNAL_TOKEN_HEADER, "")
        if not hmac.compare_digest(supplied, token):
            self._send_error(HTTPStatus.FORBIDDEN, "forbidden", "invalid control token")
            return False
        return True

    def _read_json(self) -> dict[str, Any] | None:
        """Return the JSON object body, or send the matching error and return None."""
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
            payload = json.loads(raw_bytes.decode("utf-8") or "{}")
        except UnicodeDecodeError:
            self._send_error(HTTPStatus.BAD_REQUEST, "bad_utf8", "request body must be UTF-8")
            return None
        except json.JSONDecodeError:
            self._send_error(HTTPStatus.BAD_REQUEST, "bad_json", "request body must be JSON")
            return None
        if not isinstance(payload, dict):
            self._send_error(HTTPStatus.BAD_REQUEST, "bad_json", "request body must be a JSON object")
            return None
        if json_depth(payload) > MAX_JSON_DEPTH or contains_nonfinite(payload):
            self._send_error(HTTPStatus.BAD_REQUEST, "bad_json", "request JSON is outside allowed bounds")
            return None
        return payload

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status)

    def _send_html(self, body: str) -> None:
        self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8", HTTPStatus.OK)

    def _send_error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._send_json({"error": {"code": code, "message": message}}, status=status)

    def _send_bytes(self, data: bytes, content_type: str, status: HTTPStatus) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _require_fields(
        self,
        payload: dict[str, Any],
        allowed: set[str] | frozenset[str],
        *,
        required: set[str] | frozenset[str] | None = None,
    ) -> None:
        """Reject unknown fields first, then missing required ones."""
        required = required or set()
        unknown = set(payload) - set(allowed)
        missing = set(required) - set(payload)
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


def split_host_port(value: str) -> tuple[str, int | None]:
    """Split a Host header into (host, port); port is None when absent or invalid."""
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


def json_depth(value: Any, depth: int = 0) -> int:
    """Nesting depth of a decoded JSON value (scalars are depth 0)."""
    if isinstance(value, dict):
        return max([depth] + [json_depth(child, depth + 1) for child in value.values()])
    if isinstance(value, list):
        return max([depth] + [json_depth(child, depth + 1) for child in value])
    return depth


def contains_nonfinite(value: Any) -> bool:
    """True when any float inside the decoded JSON is NaN or infinite."""
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(contains_nonfinite(child) for child in value.values())
    if isinstance(value, list):
        return any(contains_nonfinite(child) for child in value)
    return False
