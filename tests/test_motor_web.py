from __future__ import annotations

import io
import json

import pytest

from ailamp import motor_cli, motor_web
from ailamp.motor_web import create_server
from ailamp.motor_web_ui import INDEX_HTML
from ailamp.services.motor import MotorService
from ailamp.services.motor_backend import (
    DEFAULT_ACCELERATION_LIMITS,
    DEFAULT_GOAL_VELOCITY_LIMITS,
    DEFAULT_TORQUE_LIMITS,
)
from ailamp.services.motor_runtime import DEFAULT_SCRIPT, MotorDemoRuntime
from test_motor_demo_runtime import (
    CALIBRATION_SHA256,
    FakeMotorRobot,
    state,
    write_recording,
)


HOST = "127.0.0.1:8766"
ARM_BODY = {
    "goal_velocity": 300,
    "acceleration": 20,
    "torque_limit": 250,
    "confirm": "CONFIRM",
}
MUTATION_PATHS = (
    "/api/connect",
    "/api/arm",
    "/api/play",
    "/api/script",
    "/api/home",
    "/api/stop",
    "/api/release",
    "/api/capture-home",
    "/api/confirm",
)
SCRIPT_RECORDINGS = tuple(step for step in DEFAULT_SCRIPT if step != "home")


def request(server, method, path, body=None, headers=None, *, host=HOST, include_origin=True):
    handler_cls = server.RequestHandlerClass

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
    getattr(handler, f"do_{method}")()
    return handler


def get(server, path, headers=None, **kwargs):
    handler = request(server, "GET", path, headers=headers, **kwargs)
    return handler.status, handler.body


def get_json(server, path, headers=None, **kwargs):
    status, body = get(server, path, headers, **kwargs)
    return status, json.loads(body)


def post(server, path, body, token=None, headers=None, **kwargs):
    merged = {"Content-Type": "application/json"}
    if token is not None:
        merged["X-CSRF-Token"] = token
    merged.update(headers or {})
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    handler = request(server, "POST", path, raw, merged, **kwargs)
    return handler.status, json.loads(handler.body)


def csrf(server, headers=None, **kwargs):
    return get_json(server, "/api/state", headers, **kwargs)[1]["csrf_token"]


class WebFixture:
    def __init__(self, tmp_path, robot=None, *, mode="simulation", address=("127.0.0.1", 8766), external_token=None):
        self.robot = robot or FakeMotorRobot()
        self.recordings = tmp_path / "recordings"
        for name in SCRIPT_RECORDINGS:
            write_recording(self.recordings, name, [state(10.0), state(20.0)])
        service = MotorService(
            "/dev/fake-web",
            "lelamp",
            self.recordings,
            robot=self.robot,
            fps=1000,
            max_step_units=100,
            lock_dir=tmp_path / "locks",
        )
        self.home_path = tmp_path / "home.json"
        self.runtime = MotorDemoRuntime(
            service,
            calibration_sha256=CALIBRATION_SHA256,
            home_path=self.home_path,
            feedback_tolerance=0.01,
            feedback_timeout=0.5,
            feedback_poll_interval=0.001,
        )
        self.server = create_server(
            address,
            self.runtime,
            recordings_dir=self.recordings,
            mode=mode,
            external_token=external_token,
            bind_and_activate=False,
        )

    def token(self):
        return csrf(self.server)

    def connect(self):
        return post(self.server, "/api/connect", {}, self.token())

    def arm(self):
        return post(self.server, "/api/arm", ARM_BODY, self.token())

    def connect_and_arm(self):
        status, _ = self.connect()
        assert status == 200
        status, _ = self.arm()
        assert status == 200
        return self.token()

    def wait(self):
        assert self.runtime.wait_until_idle(timeout=2.0)

    def events(self):
        return get_json(self.server, "/api/state")[1]["events"]

    def close(self):
        self.server.server_close()


@pytest.fixture
def web_factory(tmp_path):
    created = []

    def make(robot=None, **kwargs):
        fixture = WebFixture(tmp_path, robot, **kwargs)
        created.append(fixture)
        return fixture

    yield make
    for fixture in created:
        fixture.close()


def test_index_serves_ui_listing_every_recording_and_stop_button(web_factory):
    web = web_factory()

    status, body = get(web.server, "/")
    html = body.decode("utf-8")

    assert status == 200
    assert html == INDEX_HTML
    assert 'id="btn-stop"' in html


def test_state_reports_mode_recordings_limits_script_and_csrf(web_factory):
    web = web_factory()

    status, payload = get_json(web.server, "/api/state")

    assert status == 200
    assert payload["mode"] == "simulation"
    assert payload["recordings"] == sorted(SCRIPT_RECORDINGS)
    assert payload["default_script"] == list(DEFAULT_SCRIPT)
    assert payload["arm_limits"] == {
        "goal_velocity": list(DEFAULT_GOAL_VELOCITY_LIMITS),
        "acceleration": list(DEFAULT_ACCELERATION_LIMITS),
        "torque_limit": list(DEFAULT_TORQUE_LIMITS),
    }
    assert payload["status"]["connected"] is False
    assert payload["status"]["armed"] is False
    assert payload["events"] == []
    assert len(payload["csrf_token"]) >= 32
    assert web.robot.operations == []


def test_state_is_read_only_and_never_connects(web_factory):
    web = web_factory()

    get_json(web.server, "/api/state")
    get_json(web.server, "/api/state")

    assert web.runtime.is_connected is False
    assert web.robot.operations == []


@pytest.mark.parametrize("path", MUTATION_PATHS)
def test_every_mutation_requires_csrf_token(web_factory, path):
    web = web_factory()

    status, payload = post(web.server, path, {})

    assert status == 403
    assert payload["error"]["code"] == "forbidden"
    assert web.robot.operations == []


def test_mutation_rejects_foreign_host_and_mismatched_origin(web_factory):
    web = web_factory()
    token = web.token()

    foreign_host, _ = post(web.server, "/api/connect", {}, token, host="evil.local:8766")
    bad_origin, _ = post(
        web.server,
        "/api/connect",
        {},
        token,
        {"Origin": "http://other.local:8766"},
    )

    assert foreign_host == 403
    assert bad_origin == 403
    assert web.robot.operations == []


def test_unknown_path_and_unknown_field_are_rejected(web_factory):
    web = web_factory()
    token = web.token()

    unknown_path, _ = post(web.server, "/api/dance", {}, token)
    unknown_field, payload = post(web.server, "/api/connect", {"force": True}, token)

    assert unknown_path == 404
    assert unknown_field == 400
    assert payload["error"]["code"] == "invalid_request"
    assert "force" in payload["error"]["message"]
    assert web.runtime.is_connected is False


def test_oversized_body_is_rejected(web_factory):
    web = web_factory()
    token = web.token()

    status, payload = post(
        web.server,
        "/api/connect",
        b"x" * (web.server.max_body_bytes + 1),
        token,
    )

    assert status == 413
    assert payload["error"]["code"] == "too_large"


def test_connect_reports_read_only_connection(web_factory):
    web = web_factory()

    status, payload = web.connect()

    assert status == 200
    assert payload["ok"] is True
    assert payload["state"]["status"]["connected"] is True
    assert payload["state"]["status"]["armed"] is False
    assert web.robot.armed is False


def test_arm_requires_exact_confirmation_and_never_touches_robot(web_factory):
    web = web_factory()
    web.connect()
    token = web.token()

    status, payload = post(web.server, "/api/arm", {**ARM_BODY, "confirm": "yes"}, token)

    assert status == 400
    assert "confirm" in payload["error"]["message"]
    assert web.robot.armed is False
    assert web.runtime.is_armed is False


@pytest.mark.parametrize(
    "field,value",
    [("goal_velocity", True), ("acceleration", 20.5), ("torque_limit", "250")],
)
def test_arm_rejects_non_integer_registers(web_factory, field, value):
    web = web_factory()
    web.connect()

    status, payload = post(web.server, "/api/arm", {**ARM_BODY, field: value}, web.token())

    assert status == 400
    assert field in payload["error"]["message"]
    assert web.robot.armed is False


def test_arm_out_of_range_is_refused_by_runtime_validation(web_factory):
    web = web_factory()
    web.connect()

    status, payload = post(
        web.server,
        "/api/arm",
        {**ARM_BODY, "torque_limit": DEFAULT_TORQUE_LIMITS[1] + 1},
        web.token(),
    )

    assert status == 400
    assert "torque_limit" in payload["error"]["message"]
    assert web.robot.armed is False


def test_arm_holds_current_pose_and_records_operator_confirmation(web_factory):
    web = web_factory()
    web.connect()

    status, payload = web.arm()

    assert status == 200
    assert payload["state"]["status"]["armed"] is True
    assert payload["state"]["status"]["torque_state"] == "holding"
    kinds = [(event["kind"], event["task"]) for event in payload["state"]["events"]]
    assert ("operator_confirmed", "arm") in kinds
    assert ("sent", "arm") in kinds
    assert web.robot.armed is True


def test_play_rejects_unknown_recording_without_touching_runtime(web_factory):
    web = web_factory()
    token = web.connect_and_arm()

    status, payload = post(web.server, "/api/play", {"name": "missing"}, token)

    assert status == 400
    assert "missing" in payload["error"]["message"]
    assert web.robot.sent_actions == []


def test_play_before_arm_is_refused_with_runtime_message(web_factory):
    web = web_factory()
    web.connect()

    status, payload = post(web.server, "/api/play", {"name": "nod"}, web.token())

    assert status == 409
    assert payload["error"]["code"] == "runtime_refused"
    assert "not armed" in payload["error"]["message"]
    assert web.robot.sent_actions == []


def test_play_runs_recording_and_records_sent_and_feedback(web_factory):
    web = web_factory(FakeMotorRobot(block_send_number=1))
    token = web.connect_and_arm()

    status, payload = post(web.server, "/api/play", {"name": "nod"}, token)
    assert payload["state"]["status"]["busy"] is True
    web.robot.release_blocked_send()
    web.wait()

    assert status == 200
    assert payload["state"]["status"]["task"] == "nod"
    kinds = [(event["kind"], event["task"]) for event in web.events()]
    assert ("sent", "nod") in kinds
    assert ("feedback_reached", "nod") in kinds
    assert web.robot.sent_actions[-1]["base_yaw.pos"] == 20.0


def test_stop_while_armed_returns_fresh_hold_target(web_factory):
    web = web_factory()
    token = web.connect_and_arm()

    status, payload = post(web.server, "/api/stop", {}, token)

    assert status == 200
    assert set(payload["held"]) == {f"{joint}.pos" for joint in (
        "base_yaw", "base_pitch", "elbow_pitch", "wrist_roll", "wrist_pitch"
    )}
    assert payload["state"]["status"]["armed"] is True
    assert payload["state"]["status"]["busy"] is False


def test_stop_before_arm_is_refused(web_factory):
    web = web_factory()
    web.connect()

    status, payload = post(web.server, "/api/stop", {}, web.token())

    assert status == 409
    assert "not armed" in payload["error"]["message"]


def test_home_requires_captured_home_then_succeeds(web_factory):
    web = web_factory()
    token = web.connect_and_arm()

    refused, refused_payload = post(web.server, "/api/home", {}, token)
    captured, captured_payload = post(
        web.server, "/api/capture-home", {"confirm": "CONFIRM"}, token
    )
    started, _ = post(web.server, "/api/home", {}, token)
    web.wait()

    assert refused == 409
    assert "home pose has not been captured" in refused_payload["error"]["message"]
    assert captured == 200
    assert set(captured_payload["home"]) == {
        f"{joint}.pos" for joint in ("base_yaw", "base_pitch", "elbow_pitch", "wrist_roll", "wrist_pitch")
    }
    assert web.home_path.exists()
    assert started == 200
    assert ("sent", "home") in [(event["kind"], event["task"]) for event in web.events()]


def test_capture_home_and_release_require_confirmation(web_factory):
    web = web_factory()
    token = web.connect_and_arm()

    capture, _ = post(web.server, "/api/capture-home", {"confirm": "ok"}, token)
    release, _ = post(web.server, "/api/release", {}, token)

    assert capture == 400
    assert release == 400
    assert not web.home_path.exists()
    assert web.robot.armed is True


def test_release_after_confirmation_verifies_torque_off(web_factory):
    web = web_factory()
    token = web.connect_and_arm()

    status, payload = post(web.server, "/api/release", {"confirm": "CONFIRM"}, token)

    assert status == 200
    assert payload["state"]["status"]["armed"] is False
    assert payload["state"]["status"]["torque_state"] == "verified_all_off"
    assert web.robot.armed is False


def test_script_refuses_when_home_missing_and_runs_after_capture(web_factory):
    web = web_factory(FakeMotorRobot(block_send_number=1))
    token = web.connect_and_arm()

    refused, refused_payload = post(web.server, "/api/script", {}, token)
    post(web.server, "/api/capture-home", {"confirm": "CONFIRM"}, token)
    started, started_payload = post(web.server, "/api/script", {}, token)
    web.robot.release_blocked_send()
    web.wait()

    assert refused == 409
    assert "home pose has not been captured" in refused_payload["error"]["message"]
    assert started == 200
    assert started_payload["state"]["status"]["task"] == "script"
    sent = [event["task"] for event in web.events() if event["kind"] == "sent"]
    for step in DEFAULT_SCRIPT:
        assert step in sent


def test_script_reports_missing_recordings_before_starting(tmp_path):
    web = WebFixture(tmp_path)
    try:
        (web.recordings / "curious.csv").unlink()
        token = web.connect_and_arm()

        status, payload = post(web.server, "/api/script", {}, token)

        assert status == 400
        assert "curious" in payload["error"]["message"]
        assert web.robot.sent_actions == []
    finally:
        web.close()


def test_latched_fault_blocks_every_later_motion(web_factory):
    web = web_factory(FakeMotorRobot(fail_send_number=1))
    token = web.connect_and_arm()

    post(web.server, "/api/play", {"name": "nod"}, token)
    web.wait()
    status, payload = post(web.server, "/api/play", {"name": "wake_up"}, token)
    _, state_payload = get_json(web.server, "/api/state")

    assert state_payload["status"]["fault"] is not None
    assert status == 409
    assert "latched fault" in payload["error"]["message"]
    assert len(web.robot.sent_actions) == 1


def test_confirm_records_visible_operator_event(web_factory):
    web = web_factory()
    web.connect()

    status, payload = post(
        web.server,
        "/api/confirm",
        {"task": "wake_up", "note": "现场可见"},
        web.token(),
    )

    assert status == 200
    event = payload["state"]["events"][-1]
    assert event["kind"] == "operator_confirmed"
    assert event["task"] == "wake_up"
    assert event["details"]["note"] == "现场可见"


def test_confirm_requires_non_empty_task(web_factory):
    web = web_factory()
    web.connect()

    status, payload = post(web.server, "/api/confirm", {"task": "  "}, web.token())

    assert status == 400
    assert "task" in payload["error"]["message"]


def test_events_are_capped(web_factory):
    web = web_factory()
    web.connect()
    token = web.token()
    for index in range(motor_web.MAX_EVENTS + 5):
        post(web.server, "/api/confirm", {"task": f"note{index}"}, token)

    events = web.events()

    assert len(events) == motor_web.MAX_EVENTS
    assert events[-1]["task"] == f"note{motor_web.MAX_EVENTS + 4}"


def test_hardware_mode_is_reported_in_state(web_factory):
    web = web_factory(mode="hardware")

    _, payload = get_json(web.server, "/api/state")

    assert payload["mode"] == "hardware"


def test_create_server_rejects_wildcard_hosts_and_unknown_mode(tmp_path):
    with pytest.raises(ValueError, match="host"):
        WebFixture(tmp_path, address=("0.0.0.0", 8766))
    with pytest.raises(ValueError, match="mode"):
        WebFixture(tmp_path, mode="sim")


def test_non_loopback_bind_requires_strong_token(tmp_path):
    with pytest.raises(ValueError, match="token"):
        WebFixture(tmp_path, address=("192.168.55.1", 8766))
    with pytest.raises(ValueError, match="token"):
        WebFixture(tmp_path, address=("192.168.55.1", 8766), external_token="short")


def test_non_loopback_requests_must_present_external_token(tmp_path):
    token_value = "t" * 40
    web = WebFixture(tmp_path, address=("192.168.55.1", 8766), external_token=token_value)
    try:
        host = "192.168.55.1:8766"
        page, _ = get(web.server, "/", host=host)
        denied, _ = get_json(web.server, "/api/state", host=host)
        allowed, payload = get_json(
            web.server, "/api/state", {"X-AILamp-Token": token_value}, host=host
        )
        mutation_denied, _ = post(
            web.server, "/api/connect", {}, payload["csrf_token"], host=host
        )
        mutation_allowed, _ = post(
            web.server,
            "/api/connect",
            {},
            payload["csrf_token"],
            {"X-AILamp-Token": token_value},
            host=host,
        )

        assert page == 200
        assert denied == 403
        assert allowed == 200
        assert mutation_denied == 403
        assert mutation_allowed == 200
    finally:
        web.close()


def test_parser_shares_runtime_options_with_cli_and_keeps_cli_intact():
    web_args = motor_web.build_parser().parse_args([])
    cli_args = motor_cli.build_parser().parse_args(["shell"])

    assert web_args.host == "127.0.0.1"
    assert web_args.http_port == 8766
    assert web_args.token_env == "AILAMP_MOTOR_WEB_TOKEN"
    for name in (
        "hardware",
        "port",
        "recordings_dir",
        "calibration_dir",
        "calibration_sha256",
        "home_path",
        "fps",
        "max_step_units",
        "feedback_tolerance",
        "feedback_timeout",
        "feedback_poll_interval",
    ):
        assert getattr(web_args, name) == getattr(cli_args, name)
    assert cli_args.command == "shell"


def test_parser_rejects_invalid_http_port():
    with pytest.raises(SystemExit):
        motor_web.build_parser().parse_args(["--http-port", "70000"])


def test_main_hardware_without_home_path_fails_before_binding(monkeypatch):
    output = io.StringIO()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("server must not be created")

    monkeypatch.setattr(motor_web, "create_server", forbidden)
    code = motor_web.main(["--hardware"], output=output, serve=forbidden)

    assert code == 2
    assert "--home-path" in output.getvalue()


def test_main_non_loopback_without_token_fails_before_binding(monkeypatch):
    output = io.StringIO()
    monkeypatch.delenv("AILAMP_MOTOR_WEB_TOKEN", raising=False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("must not serve")

    code = motor_web.main(["--host", "192.168.55.1"], output=output, serve=forbidden)

    assert code == 2
    assert "AILAMP_MOTOR_WEB_TOKEN" in output.getvalue()


def test_main_simulation_prints_mode_and_url_then_serves():
    output = io.StringIO()
    captured = {}

    def fake_serve(server, runtime, out):
        captured["server"] = server
        captured["runtime"] = runtime
        server.server_close()
        return 0

    code = motor_web.main(["--http-port", "0"], output=output, serve=fake_serve)
    text = output.getvalue()

    assert code == 0
    assert "模式：离线模拟" in text
    assert "操作页面" in text
    assert captured["server"].mode == "simulation"
    assert captured["runtime"].is_connected is False


# --- malformed-request branches of the hardened handler -----------------------


def test_mutation_rejects_transfer_encoding_and_bad_lengths(web_factory):
    web = web_factory()
    token = web.token()

    chunked, chunked_payload = post(
        web.server, "/api/connect", {}, token, {"Transfer-Encoding": "chunked"}
    )
    invalid, invalid_payload = post(
        web.server, "/api/connect", {}, token, {"Content-Length": "abc"}
    )
    negative, _ = post(web.server, "/api/connect", {}, token, {"Content-Length": "-1"})

    assert chunked == 400
    assert chunked_payload["error"]["code"] == "bad_request"
    assert invalid == 411
    assert invalid_payload["error"]["code"] == "bad_length"
    assert negative == 411
    assert web.runtime.is_connected is False


def test_mutation_rejects_wrong_content_type_and_malformed_json(web_factory):
    web = web_factory()
    token = web.token()

    text_type, text_payload = post(
        web.server, "/api/connect", {}, token, {"Content-Type": "text/plain"}
    )
    bad_utf8, utf8_payload = post(web.server, "/api/connect", b"\xff\xfe", token)
    not_object, object_payload = post(web.server, "/api/connect", b"[]", token)
    not_json, json_payload = post(web.server, "/api/connect", b"{", token)

    assert text_type == 415
    assert text_payload["error"]["code"] == "unsupported_media_type"
    assert bad_utf8 == 400
    assert utf8_payload["error"]["code"] == "bad_utf8"
    assert not_object == 400
    assert object_payload["error"]["code"] == "bad_json"
    assert not_json == 400
    assert json_payload["error"]["code"] == "bad_json"
    assert web.runtime.is_connected is False


def test_mutation_rejects_deep_or_non_finite_json(web_factory):
    web = web_factory()
    token = web.token()
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}}

    too_deep, deep_payload = post(web.server, "/api/connect", deep, token)
    nan, nan_payload = post(web.server, "/api/connect", b'{"x": NaN}', token)

    assert too_deep == 400
    assert deep_payload["error"]["code"] == "bad_json"
    assert nan == 400
    assert nan_payload["error"]["code"] == "bad_json"


def test_body_shorter_than_content_length_is_rejected(web_factory):
    web = web_factory()

    status, payload = post(
        web.server, "/api/connect", b"{}", web.token(), {"Content-Length": "10"}
    )

    assert status == 400
    assert "ended early" in payload["error"]["message"]


def test_reads_reject_bad_origin_scheme_wrong_port_and_unknown_path(web_factory):
    web = web_factory()

    https_origin, _ = get(web.server, "/api/state", {"Origin": "https://127.0.0.1:8766"})
    wrong_port, _ = get(web.server, "/api/state", host="127.0.0.1:9999")
    unknown, _ = get(web.server, "/api/nope")
    localhost_alias, _ = get(web.server, "/api/state", host="localhost:8766")

    assert https_origin == 403
    assert wrong_port == 403
    assert unknown == 404
    assert localhost_alias == 200


def test_split_host_port_handles_ipv6_and_missing_ports():
    from ailamp.motor_web_http import split_host_port

    assert split_host_port("[::1]:8766") == ("::1", 8766)
    assert split_host_port("[::1]") == ("::1", None)
    assert split_host_port("127.0.0.1") == ("127.0.0.1", None)
    assert split_host_port("127.0.0.1:x") == ("127.0.0.1", None)


def test_unexpected_backend_error_is_logged_and_not_leaked(web_factory, monkeypatch, caplog):
    web = web_factory()
    token = web.token()

    def explode():
        raise OSError("serial exploded at /dev/ttyACM0")

    monkeypatch.setattr(web.runtime, "connect", explode)
    with caplog.at_level("ERROR", logger="ailamp.motor_web"):
        status, payload = post(web.server, "/api/connect", {}, token)

    assert status == 500
    assert payload["error"] == {"code": "internal_error", "message": "internal error"}
    assert "/dev/ttyACM0" not in json.dumps(payload)
    assert "serial exploded" in caplog.text


# --- process entry points -----------------------------------------------------


def test_external_token_helper_only_demands_strength_off_loopback(monkeypatch):
    from argparse import Namespace

    monkeypatch.delenv("AILAMP_MOTOR_WEB_TOKEN", raising=False)
    loopback = Namespace(host="127.0.0.1", token_env="AILAMP_MOTOR_WEB_TOKEN")
    remote = Namespace(host="192.168.55.1", token_env="AILAMP_MOTOR_WEB_TOKEN")

    assert motor_web._external_token(loopback) is None
    with pytest.raises(ValueError, match="AILAMP_MOTOR_WEB_TOKEN"):
        motor_web._external_token(remote)
    monkeypatch.setenv("AILAMP_MOTOR_WEB_TOKEN", "k" * 40)
    assert motor_web._external_token(loopback) == "k" * 40
    assert motor_web._external_token(remote) == "k" * 40


def test_main_reads_token_from_named_env_and_passes_it_to_server(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "z" * 40)
    captured = {}

    class FakeServer:
        server_address = ("192.168.55.1", 8766)

    def fake_create(address, runtime, **kwargs):
        captured.update(kwargs, address=address)
        return FakeServer()

    monkeypatch.setattr(motor_web, "create_server", fake_create)
    output = io.StringIO()

    code = motor_web.main(
        ["--host", "192.168.55.1", "--token-env", "MY_TOKEN"],
        output=output,
        serve=lambda server, runtime, out: 0,
    )

    assert code == 0
    assert captured["external_token"] == "z" * 40
    assert captured["address"] == ("192.168.55.1", 8766)
    assert captured["mode"] == "simulation"
    assert "192.168.55.1:8766" in output.getvalue()


def test_serve_forever_returns_130_on_signal_and_runs_runtime_shutdown(tmp_path, monkeypatch):
    web = WebFixture(tmp_path)
    output = io.StringIO()

    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(web.server, "serve_forever", interrupted)
    code = motor_web.serve_forever(web.server, web.runtime, output)

    assert code == 130
    assert "signal: cancelling motion" in output.getvalue()


def test_serve_forever_returns_0_on_clean_exit(tmp_path, monkeypatch):
    web = WebFixture(tmp_path)

    monkeypatch.setattr(web.server, "serve_forever", lambda: None)

    assert motor_web.serve_forever(web.server, web.runtime, io.StringIO()) == 0
