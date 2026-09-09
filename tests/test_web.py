from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
from pathlib import Path
import threading

import pytest

from ailamp.config import load_hardware_config
from ailamp.web import create_server


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config/hardware.toml"


@dataclass(frozen=True)
class Outcome:
    accepted: bool = True
    sent: bool = False
    completed: bool = True
    error: str | None = None

    def to_dict(self):
        return {
            "accepted": self.accepted,
            "sent": self.sent,
            "completed": self.completed,
            "error": self.error,
        }


class FakeRuntime:
    def __init__(self):
        self.texts = []
        self.stopped = 0
        self.auto = []

    def snapshot(self):
        return {"brain_enabled": False, "provider": "openai", "model": "gpt-4.1-mini", "api_call_count": 0, "auto_enabled": bool(self.auto[-1]) if self.auto else False}

    def submit_text(self, text):
        self.texts.append(text)
        return Outcome()

    def stop(self):
        self.stopped += 1
        return Outcome(completed=True)

    def set_auto_enabled(self, enabled):
        self.auto.append(enabled)
        return self.snapshot()

    def close(self):
        pass


class FakeController:
    def snapshot(self):
        return {"armed": False, "mode": "manual", "with_outputs": False, "recordings": ["idle", "nod"]}

    def arm(self, enabled):
        return self.snapshot()


def close_server(server):
    server.server_close()


def request(server, method, path, body=None, headers=None, host="127.0.0.1:8765", include_origin=True):
    handler_cls = server.RequestHandlerClass
    token = server.csrf_token
    runtime = server.runtime

    class DummyHandler(handler_cls):
        def __init__(self):
            self.command = method
            self.path = path
            payload = b"" if body is None else body
            base_headers = {"Host": host, "Content-Length": str(len(payload))}
            if include_origin:
                base_headers["Origin"] = f"http://{host}"
            self.headers = {**base_headers, **(headers or {})}
            self.rfile = type("R", (), {"read": lambda _self, n=-1: payload})()
            self.status = None
            self.body = b""
            self.response_headers = {}
            self.server = server

        def send_response(self, code, message=None):
            self.status = code

        def send_header(self, key, value):
            self.response_headers[key] = value

        def end_headers(self):
            pass

        def log_message(self, format, *args):
            pass

        @property
        def wfile(self):
            class W:
                def __init__(inner_self, outer):
                    inner_self.outer = outer

                def write(inner_self, data):
                    inner_self.outer.body += data

            return W(self)

    handler = DummyHandler()
    if method == "GET":
        handler.do_GET()
    elif method == "POST":
        handler.do_POST()
    else:
        raise AssertionError(method)
    return handler, runtime, token


def test_get_state_is_side_effect_free_and_issues_csrf_token():
    runtime = FakeRuntime()
    server = create_server(("127.0.0.1", 8765), load_hardware_config(CONFIG_PATH), runtime=runtime, controller=FakeController(), bind_and_activate=False)

    handler, _, _ = request(server, "GET", "/api/state")
    payload = json.loads(handler.body)

    assert handler.status == 200
    assert "csrf_token" in payload
    assert runtime.texts == []
    close_server(server)


def test_mutation_requires_json_same_origin_and_csrf():
    runtime = FakeRuntime()
    server = create_server(("127.0.0.1", 8765), load_hardware_config(CONFIG_PATH), runtime=runtime, controller=FakeController(), bind_and_activate=False)
    _, _, token = request(server, "GET", "/api/state")
    body = json.dumps({"text": "你好"}).encode()

    no_token, _, _ = request(server, "POST", "/api/chat", body, {"Content-Type": "application/json"})
    wrong_origin, _, _ = request(
        server,
        "POST",
        "/api/chat",
        body,
        {"Content-Type": "application/json", "X-CSRF-Token": token},
        host="evil.local",
    )
    ok, _, _ = request(server, "POST", "/api/chat", body, {"Content-Type": "application/json", "X-CSRF-Token": token})

    assert no_token.status == 403
    assert wrong_origin.status == 403
    assert ok.status == 200
    assert runtime.texts == ["你好"]
    close_server(server)


def test_rejects_malformed_or_unknown_mutation_shapes():
    runtime = FakeRuntime()
    server = create_server(("127.0.0.1", 8765), load_hardware_config(CONFIG_PATH), runtime=runtime, controller=FakeController(), bind_and_activate=False)
    _, _, token = request(server, "GET", "/api/state")

    too_large = request(
        server,
        "POST",
        "/api/chat",
        b"x" * (server.max_body_bytes + 1),
        {"Content-Type": "application/json", "X-CSRF-Token": token},
    )[0]
    unknown = request(
        server,
        "POST",
        "/api/unknown",
        b"{}",
        {"Content-Type": "application/json", "X-CSRF-Token": token},
    )[0]

    assert too_large.status == 413
    assert unknown.status == 404
    assert runtime.texts == []
    close_server(server)


def test_wildcard_bind_is_rejected():
    config = load_hardware_config(CONFIG_PATH)

    for host in ("0.0.0.0", "", "::"):
        with pytest.raises(ValueError, match="concrete host"):
            create_server((host, 0), config, runtime=FakeRuntime(), controller=FakeController(), external_token="x" * 32, bind_and_activate=False)


def test_nonloopback_factory_requires_strong_token_and_loopback_auth_for_read_write():
    config = load_hardware_config(CONFIG_PATH)

    try:
        create_server(("192.168.1.50", 0), config, runtime=FakeRuntime(), controller=FakeController(), external_token="short", bind_and_activate=False)
    except ValueError as exc:
        assert "strong token" in str(exc)
    else:
        raise AssertionError("short token accepted")

    token = "x" * 32
    wrong_token = "y" * 32
    runtime = FakeRuntime()
    server = create_server(("127.0.0.1", 0), config, runtime=runtime, controller=FakeController(), external_token=token)
    port = server.server_address[1]
    host = f"127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def live_request(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            conn.request(method, path, body=body, headers={"Host": host, **(headers or {})})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    try:
        missing_get_status, missing_get_body = live_request("GET", "/api/state")
        wrong_get_status, wrong_get_body = live_request("GET", "/api/state", headers={"X-AILamp-Token": wrong_token})
        ok_get_status, ok_get_body = live_request("GET", "/api/state", headers={"X-AILamp-Token": token})
        csrf_token = json.loads(ok_get_body)["csrf_token"]
        body = json.dumps({"text": "localhost auth"}).encode()
        post_headers = {
            "Content-Type": "application/json",
            "Origin": f"http://{host}",
            "X-CSRF-Token": csrf_token,
        }

        missing_post_status, missing_post_body = live_request("POST", "/api/chat", body, post_headers)
        wrong_post_status, wrong_post_body = live_request("POST", "/api/chat", body, {**post_headers, "X-AILamp-Token": wrong_token})
        assert runtime.texts == []
        ok_post_status, ok_post_body = live_request("POST", "/api/chat", body, {**post_headers, "X-AILamp-Token": token})

        assert missing_get_status == 403
        assert wrong_get_status == 403
        assert ok_get_status == 200
        assert missing_post_status == 403
        assert wrong_post_status == 403
        assert ok_post_status == 200
        assert runtime.texts == ["localhost auth"]
        assert b"csrf_token" not in missing_get_body
        assert b"armed" not in missing_get_body
        assert b"recordings" not in missing_get_body
        for payload in (
            missing_get_body,
            wrong_get_body,
            ok_get_body,
            missing_post_body,
            wrong_post_body,
            ok_post_body,
        ):
            assert token.encode() not in payload
            assert wrong_token.encode() not in payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)


def test_nonloopback_unbound_handler_requires_token_without_leaking_it():
    token = "x" * 32
    server = create_server(("192.168.1.50", 8765), load_hardware_config(CONFIG_PATH), runtime=FakeRuntime(), controller=FakeController(), external_token=token, bind_and_activate=False)
    host = "192.168.1.50:8765"

    denied = request(server, "GET", "/api/state", host=host)[0]
    ok = request(server, "GET", "/api/state", headers={"X-AILamp-Token": token}, host=host)[0]

    assert denied.status == 403
    assert ok.status == 200
    assert token.encode() not in ok.body
    close_server(server)


def test_host_origin_and_port_are_strictly_validated():
    server = create_server(("127.0.0.1", 8765), load_hardware_config(CONFIG_PATH), runtime=FakeRuntime(), controller=FakeController(), bind_and_activate=False)
    port = server.server_address[1]
    host = f"127.0.0.1:{port}"
    _, _, token = request(server, "GET", "/api/state", host=host)
    body = json.dumps({"text": "你好"}).encode()

    wrong_port = request(server, "POST", "/api/chat", body, {"Content-Type": "application/json", "X-CSRF-Token": token}, host="127.0.0.1:1")[0]
    wrong_origin = request(server, "POST", "/api/chat", body, {"Content-Type": "application/json", "X-CSRF-Token": token, "Origin": "https://127.0.0.1:0"}, host=host)[0]
    missing_origin = request(server, "POST", "/api/chat", body, {"Content-Type": "application/json", "X-CSRF-Token": token}, host=host, include_origin=False)[0]

    assert wrong_port.status == 403
    assert wrong_origin.status == 403
    assert missing_origin.status == 200
    close_server(server)


def test_auto_route_is_wired():
    runtime = FakeRuntime()
    server = create_server(("127.0.0.1", 8765), load_hardware_config(CONFIG_PATH), runtime=runtime, controller=FakeController(), bind_and_activate=False)
    host = f"127.0.0.1:{server.server_address[1]}"
    _, _, token = request(server, "GET", "/api/state", host=host)

    handler = request(
        server,
        "POST",
        "/api/auto",
        json.dumps({"enabled": True}).encode(),
        {"Content-Type": "application/json", "X-CSRF-Token": token},
        host=host,
    )[0]

    assert handler.status == 200
    assert runtime.auto == [True]
    close_server(server)


def test_rejects_duplicate_content_length_and_unknown_fields():
    server = create_server(("127.0.0.1", 8765), load_hardware_config(CONFIG_PATH), runtime=FakeRuntime(), controller=FakeController(), bind_and_activate=False)
    host = f"127.0.0.1:{server.server_address[1]}"
    _, _, token = request(server, "GET", "/api/state", host=host)

    unknown = request(
        server,
        "POST",
        "/api/chat",
        json.dumps({"text": "hi", "extra": True}).encode(),
        {"Content-Type": "application/json", "X-CSRF-Token": token},
        host=host,
    )[0]

    assert unknown.status == 400
    close_server(server)


def test_index_html_uses_safe_dom_updates_and_wires_controls():
    html = Path("ailamp_runtime/ailamp/web/index.html").read_text()

    assert ".innerHTML" not in html
    assert "X-AILamp-Token" in html
    assert "/api/auto" in html
    assert "clear_light" in html
    assert "软件停止" in html
    assert "物理断电急停" in html
