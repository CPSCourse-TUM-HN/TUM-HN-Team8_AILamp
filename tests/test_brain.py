import base64
import json
import math
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from ailamp.services.brain import (
    BrainAction,
    BrainPlan,
    BrainService,
    BrainValidationError,
    tool_schemas,
    validate_actions,
)
from ailamp.services.controller import ExecutionOutcome


class FakeResponses:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses):
        self.responses = FakeResponses(responses)


class FakeOptionsClient(FakeClient):
    def __init__(self, responses):
        super().__init__(responses)
        self.options_calls = []

    def with_options(self, **kwargs):
        self.options_calls.append(kwargs)
        return self


def function_call(name, arguments):
    return {"type": "function_call", "name": name, "arguments": json.dumps(arguments)}


def message_text(text):
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


def complete_response(*output, output_text=None):
    return SimpleNamespace(status="completed", output=list(output), output_text=output_text)


def test_tool_schemas_match_validator_contract():
    schemas = tool_schemas(["nod", "wave"])
    by_name = {schema["name"]: schema for schema in schemas}

    assert set(by_name) == {"play_recording", "move_joints", "set_light", "set_mode", "do_nothing"}
    assert all(schema["type"] == "function" for schema in schemas)
    assert all(schema["strict"] is False for schema in schemas)
    assert by_name["play_recording"]["parameters"]["properties"]["name"]["enum"] == ["nod", "wave"]
    assert by_name["move_joints"]["parameters"]["properties"]["deltas"]["additionalProperties"] is False
    assert by_name["set_light"]["parameters"]["required"] == ["red", "green", "blue"]
    assert "single-tick" in by_name["move_joints"]["description"]
    assert "degrees" not in by_name["move_joints"]["description"].lower()


def test_tool_schemas_omit_play_recording_when_whitelist_is_empty_and_reject_bad_names():
    schemas = tool_schemas([])

    assert {schema["name"] for schema in schemas} == {"move_joints", "set_light", "set_mode", "do_nothing"}

    with pytest.raises(BrainValidationError):
        tool_schemas(["nod", "../secret"])


def test_recording_names_are_deduped_and_constructor_rejects_invalid_configuration():
    service = BrainService(recording_names=["nod", "nod", "wave"], max_calls=0, api_key=None)

    assert service.recording_names == ("nod", "wave")

    with pytest.raises(ValueError):
        BrainService(recording_names=["nod.csv"])
    with pytest.raises(ValueError):
        BrainService(recording_names=(name for name in ["nod"]))
    with pytest.raises(ValueError):
        BrainService(max_calls=True)
    with pytest.raises(ValueError):
        BrainService(max_calls=-1)
    with pytest.raises(ValueError):
        BrainService(timeout_s=math.inf)
    with pytest.raises(ValueError):
        BrainService(model="")


def test_validate_actions_accepts_valid_two_tool_plan_and_copies_arguments():
    raw = [
        {"name": "move_joints", "arguments": {"deltas": {"base_yaw": 1.25, "wrist_pitch": -4}}},
        {"name": "set_light", "arguments": {"red": 1, "green": 2, "blue": 3}},
    ]

    actions = validate_actions(raw, recording_names=["nod"])
    raw[0]["arguments"]["deltas"]["base_yaw"] = 99

    assert actions == (
        BrainAction("move_joints", {"deltas": {"base_yaw": 1.25, "wrist_pitch": -4}}),
        BrainAction("set_light", {"red": 1, "green": 2, "blue": 3}),
    )


def test_validate_actions_accepts_brain_action_input_and_do_nothing_only_by_itself():
    action = BrainAction("do_nothing", {"reason": "waiting for real controller feedback"})

    assert validate_actions([action], recording_names=()) == (action,)

    with pytest.raises(BrainValidationError):
        validate_actions(
            [
                action,
                BrainAction("set_mode", {"mode": "rest"}),
            ],
            recording_names=(),
        )


@pytest.mark.parametrize(
    "raw_actions",
    [
        [{"name": "unknown", "arguments": {}}],
        [{"name": "set_light", "arguments": {"red": 1, "green": 2, "blue": 3, "brightness": 4, "x": 5}}],
        [{"name": "set_light", "arguments": {"red": True, "green": 2, "blue": 3, "brightness": 4}}],
        [{"name": "set_light", "arguments": {"red": 1.2, "green": 2, "blue": 3, "brightness": 4}}],
        [{"name": "set_light", "arguments": {"red": 1, "green": 2, "blue": 3, "brightness": 256}}],
        [{"name": "set_light", "arguments": {"red": 1, "green": 2, "blue": 3, "brightness": None}}],
        [{"name": "set_mode", "arguments": {"mode": "party"}}],
        [{"name": "set_mode", "arguments": {"mode": ["focus"]}}],
        [{"name": "set_mode", "arguments": {"mode": {"value": "focus"}}}],
        [{"name": "set_mode", "arguments": {"mode": "focus", "timer_minutes": False}}],
        [{"name": "set_mode", "arguments": {"mode": "focus", "timer_minutes": 181}}],
        [{"name": "do_nothing", "arguments": {"reason": ""}}],
        [{"name": "do_nothing", "arguments": {"reason": "x" * 401}}],
        [{"name": "move_joints", "arguments": {"deltas": {}}}],
        [{"name": "move_joints", "arguments": {"deltas": {"base_yaw": 4.1}}}],
        [{"name": "move_joints", "arguments": {"deltas": {"base_yaw": True}}}],
        [{"name": "move_joints", "arguments": {"deltas": {"base_yaw": "1"}}}],
        [{"name": "move_joints", "arguments": {"deltas": {"base_yaw": math.nan}}}],
        [{"name": "move_joints", "arguments": {"deltas": {"base_yaw": math.inf}}}],
        [{"name": "move_joints", "arguments": {"deltas": {"base_yaw": 10**500}}}],
        [{"name": "move_joints", "arguments": {"deltas": {"shoulder": 1}}}],
        [{"name": "move_joints", "arguments": {"deltas": {"base_yaw": 1, "base_pitch": 1, "elbow_pitch": 1, "wrist_roll": 1, "wrist_pitch": 1, "extra": 1}}}],
        [{"name": "play_recording", "arguments": {"name": "../nod"}}],
        [{"name": "play_recording", "arguments": {"name": "nod.csv"}}],
        [{"name": "play_recording", "arguments": {"name": "missing"}}],
        [{"name": "play_recording", "arguments": {"name": "nod"}}, {"name": "move_joints", "arguments": {"deltas": {"base_yaw": 1}}}],
        [
            {"name": "set_light", "arguments": {"red": 1, "green": 2, "blue": 3, "brightness": 4}},
            {"name": "set_mode", "arguments": {"mode": "focus"}},
            {"name": "move_joints", "arguments": {"deltas": {"base_yaw": 1}}},
        ],
    ],
)
def test_validate_actions_rejects_invalid_actions_atomically(raw_actions):
    with pytest.raises(BrainValidationError):
        validate_actions(raw_actions, recording_names=["nod"])


@pytest.mark.parametrize(
    "raw_actions",
    [
        [BrainAction(["set_mode"], {"mode": "focus"})],
        [BrainAction("set_mode", {"mode": ["focus"]})],
        [{"name": ["set_mode"], "arguments": {"mode": "focus"}}],
        [{"name": "set_mode", "arguments": []}],
        [{"name": "set_mode", "arguments": {"mode": "focus"}, "extra": True}],
    ],
)
def test_validate_actions_rejects_wrong_shapes_consistently(raw_actions):
    with pytest.raises(BrainValidationError):
        validate_actions(raw_actions, recording_names=["nod"])


def test_decide_sends_exact_multimodal_responses_request_and_preserves_chinese_text_and_history():
    frame = b"\xff\xd8" + (b"a" * 8) + b"\xff\xd9"
    response = complete_response(
        message_text("我会先建议转向并调暗灯光，等待控制器执行。"),
        function_call("move_joints", {"deltas": {"base_yaw": 2.0}}),
        function_call("set_light", {"red": 20, "green": 40, "blue": 80, "brightness": 120}),
    )
    client = FakeClient([response])
    service = BrainService(client=client, model="gpt-4.1-mini", recording_names=["nod"], api_key=None)

    plan = service.decide(
        user_text="请看向左边，不要用关键词规则",
        frame_jpeg=frame,
        state={"mode": "manual", "joints": {"base_yaw": 12}, "long": "x" * 2000},
        history=[
            {"status": "executed", "action": "set_light", "result": "ok"},
            {"status": "failed", "action": "move_joints", "error": "servo limit"},
        ],
    )

    call = client.responses.calls[0]
    content = call["input"][0]["content"]
    context = content[0]["text"]

    assert plan == BrainPlan(
        reply="我会先建议转向并调暗灯光，等待控制器执行。",
        actions=(
            BrainAction("move_joints", {"deltas": {"base_yaw": 2.0}}),
            BrainAction("set_light", {"red": 20, "green": 40, "blue": 80, "brightness": 120}),
        ),
        model="gpt-4.1-mini",
    )
    assert call["model"] == "gpt-4.1-mini"
    assert "temperature" not in call
    assert call["max_output_tokens"] == 1024
    assert call["store"] is False
    assert call["tools"] == tool_schemas(["nod"])
    assert call["input"] == [{"role": "user", "content": content}]
    assert content[1] == {
        "type": "input_image",
        "image_url": "data:image/jpeg;base64," + base64.b64encode(frame).decode("ascii"),
        "detail": "low",
    }
    assert "请看向左边，不要用关键词规则" in context
    assert "executed" in context
    assert "servo limit" in context
    assert "failed" in context
    assert "previous_response_id" not in call
    assert service.calls_used == 1


def test_decide_no_image_path_and_reply_only_output_text_fallback():
    client = FakeClient([complete_response(output_text="只回复，不建议动作。")])
    service = BrainService(client=client, api_key=None)

    plan = service.decide(user_text="只说话", frame_jpeg=None, state=None, history=())

    assert plan.available is True
    assert plan.reply == "只回复，不建议动作。"
    assert plan.actions == ()
    assert len(client.responses.calls[0]["input"][0]["content"]) == 1


def test_decide_supports_sdk_like_response_objects():
    response = SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="收到，建议点头。")]),
            SimpleNamespace(type="function_call", name="play_recording", arguments='{"name":"nod"}'),
        ],
        output_text=None,
    )
    client = FakeClient([response])
    service = BrainService(client=client, recording_names=["nod"], api_key=None)

    plan = service.decide(user_text="点头")

    assert plan.reply == "收到，建议点头。"
    assert plan.actions == (BrainAction("play_recording", {"name": "nod"}),)


def test_missing_key_does_not_import_openai_or_consume_budget(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delitem(sys.modules, "openai", raising=False)
    service = BrainService(client=None, api_key=None)

    plan = service.decide(user_text="hello")

    assert plan == BrainPlan(reply="", actions=(), available=False, error="missing_api_key", model="gpt-4.1-mini")
    assert "openai" not in sys.modules
    assert service.calls_used == 0


def test_lazy_import_factory_uses_explicit_key_timeout_and_no_retries(monkeypatch):
    created = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.responses = FakeResponses([complete_response(output_text="ok")])

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    service = BrainService(api_key="sk-test", timeout_s=3)

    plan = service.decide(user_text="hello")

    assert plan.reply == "ok"
    assert created == [{"api_key": "sk-test", "timeout": 3, "max_retries": 0}]
    assert service.calls_used == 1


def test_injected_client_with_options_receives_timeout_and_zero_retries():
    client = FakeOptionsClient([complete_response(output_text="ok")])
    service = BrainService(client=client, timeout_s=2.5, api_key=None)

    plan = service.decide(user_text="hello")

    assert plan.reply == "ok"
    assert client.options_calls == [{"timeout": 2.5, "max_retries": 0}]
    assert service.calls_used == 1


def test_timeout_and_api_error_are_sanitized_and_count_against_budget():
    client = FakeClient([TimeoutError("secret sk-test"), RuntimeError("credential leak")])
    service = BrainService(client=client, max_calls=2, api_key=None)

    first = service.decide(user_text="one")
    second = service.decide(user_text="two")
    third = service.decide(user_text="three")

    assert first.available is False
    assert first.error == "provider_timeout"
    assert second.available is False
    assert second.error == "provider_error"
    assert "secret" not in first.reply + (first.error or "")
    assert third.available is False
    assert third.error == "call_budget_exhausted"
    assert service.calls_used == 2
    assert len(client.responses.calls) == 2


def test_sdk_named_timeout_error_is_sanitized_as_timeout():
    class APITimeoutError(Exception):
        pass

    client = FakeClient([APITimeoutError("secret sk-test")])
    service = BrainService(client=client, max_calls=1, api_key=None)

    plan = service.decide(user_text="one")

    assert plan.available is False
    assert plan.error == "provider_timeout"
    assert plan.actions == ()
    assert service.calls_used == 1


def test_budget_reservation_is_thread_safe_and_counts_only_one_attempt(monkeypatch):
    import ailamp.services.brain as brain_module

    class BlockingResponses:
        def __init__(self):
            self.calls = []
            self.started = threading.Event()
            self.release = threading.Event()
            self.lock = threading.Lock()

        def create(self, **kwargs):
            with self.lock:
                self.calls.append(kwargs)
            self.started.set()
            self.release.wait(timeout=2)
            return complete_response(output_text="ok")

    class BlockingClient:
        def __init__(self):
            self.responses = BlockingResponses()

    client = BlockingClient()
    service = BrainService(client=client, max_calls=1, api_key=None)
    start = threading.Barrier(3)
    results = []
    context_barrier = threading.Barrier(2)
    original_build_context = brain_module._build_context

    def blocking_build_context(**kwargs):
        if service.calls_used == 0:
            try:
                context_barrier.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass
        return original_build_context(**kwargs)

    monkeypatch.setattr(brain_module, "_build_context", blocking_build_context)

    def worker(text):
        start.wait()
        results.append(service.decide(user_text=text))

    threads = [threading.Thread(target=worker, args=(f"msg {idx}",)) for idx in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    assert client.responses.started.wait(timeout=1)
    time.sleep(0.05)
    client.responses.release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert len(client.responses.calls) == 1
    assert service.calls_used == 1
    assert [plan.error for plan in results].count(None) == 1
    assert [plan.error for plan in results].count("call_budget_exhausted") == 1


def test_unknown_provider_returns_unavailable_without_calling_client():
    client = FakeClient([complete_response(output_text="unused")])
    service = BrainService(provider="anthropic", client=client, api_key=None)

    plan = service.decide(user_text="hello")

    assert plan.available is False
    assert plan.error == "unsupported_provider"
    assert plan.actions == ()
    assert service.calls_used == 0
    assert client.responses.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        '{"red":1,"red":2,"green":2,"blue":3,"brightness":4}',
        '{"red":NaN,"green":2,"blue":3,"brightness":4}',
        '{"red":Infinity,"green":2,"blue":3,"brightness":4}',
        '{"red":1,"green":2,"blue":3,"brightness":4,}',
        '{"red":1,"green":2,"blue":3,"nested":' + ("[" * 33) + ("0" + "]" * 33) + "}",
        '{"red":1,"green":2,"blue":3,"payload":"' + ("x" * 16385) + '"}',
    ],
)
def test_decide_rejects_non_strict_json_function_arguments(arguments):
    client = FakeClient([complete_response({"type": "function_call", "name": "set_light", "arguments": arguments})])
    service = BrainService(client=client, api_key=None)

    plan = service.decide(user_text="light")

    assert plan.available is False
    assert plan.error == "invalid_tool_arguments"
    assert plan.actions == ()
    assert service.calls_used == 1


def test_decide_rejects_strict_json_decode_failures_without_raising():
    deeply_nested = "[" * 2000 + "]" * 2000
    client = FakeClient([complete_response({"type": "function_call", "name": "set_light", "arguments": deeply_nested})])
    service = BrainService(client=client, api_key=None)

    plan = service.decide(user_text="light")

    assert plan.available is False
    assert plan.error == "invalid_tool_arguments"
    assert plan.actions == ()
    assert service.calls_used == 1


def test_invalid_second_tool_fails_closed_with_zero_actions():
    client = FakeClient(
        [
            complete_response(
                function_call("set_light", {"red": 1, "green": 2, "blue": 3, "brightness": 4}),
                function_call("move_joints", {"deltas": {"base_yaw": 9}}),
            )
        ]
    )
    service = BrainService(client=client, api_key=None)

    plan = service.decide(user_text="bad second")

    assert plan.available is False
    assert plan.error == "invalid_tool_arguments"
    assert plan.actions == ()


@pytest.mark.parametrize("response", [SimpleNamespace(status="failed", output=[]), SimpleNamespace(status="incomplete", output=[]), SimpleNamespace(status="refused", output=[])])
def test_failed_incomplete_and_refusal_responses_are_rejected(response):
    client = FakeClient([response])
    service = BrainService(client=client, api_key=None)

    plan = service.decide(user_text="hello")

    assert plan.available is False
    assert plan.error == "provider_response_unavailable"
    assert plan.actions == ()


def test_empty_or_malformed_response_is_unavailable():
    client = FakeClient([SimpleNamespace(status="completed", output=[]), SimpleNamespace(status="completed", output=[{"type": "unknown"}])])
    service = BrainService(client=client, api_key=None, max_calls=2)

    empty = service.decide(user_text="hello")
    malformed = service.decide(user_text="hello again")

    assert empty.available is False
    assert empty.error == "provider_response_unavailable"
    assert malformed.available is False
    assert malformed.error == "provider_response_unavailable"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"frame_jpeg": b"x" * (2 * 1024 * 1024 + 1)},
        {"frame_jpeg": b""},
        {"frame_jpeg": b"\xff\xd8not-closed"},
        {"frame_jpeg": b"not-opened\xff\xd9"},
        {"frame_jpeg": bytearray(b"\xff\xd8ok\xff\xd9")},
        {"frame_jpeg": memoryview(b"\xff\xd8ok\xff\xd9")},
        {"history": ({"status": "executed"} for _ in range(1))},
        {"history": {"status": "executed"}},
        {"history": "executed"},
        {"state": {"secret": object()}},
        {"state": {"values": {1, 2}}},
    ],
)
def test_invalid_inputs_do_not_consume_budget(kwargs):
    service = BrainService(client=FakeClient([complete_response(output_text="unused")]), api_key=None, max_calls=1)

    plan = service.decide(**kwargs)

    assert plan.available is False
    assert plan.error == "invalid_input"
    assert service.calls_used == 0


def test_history_is_bounded_to_newest_records_and_image_is_not_retained():
    client = FakeClient([complete_response(output_text="ok")])
    service = BrainService(client=client, api_key=None)
    history = [{"idx": idx, "payload": "x" * 500} for idx in range(20)]
    frame = b"\xff\xd8image\xff\xd9"

    service.decide(frame_jpeg=frame, history=history)

    context = client.responses.calls[0]["input"][0]["content"][0]["text"]
    assert '"idx": 0' not in context
    assert '"idx": 7' not in context
    assert '"idx": 8' in context
    assert '"idx": 19' in context
    assert len(context) < 6000
    assert not any(value is frame for value in service.__dict__.values())


def test_context_budget_preserves_newest_history_status_action_result_and_error():
    client = FakeClient([complete_response(output_text="ok")])
    service = BrainService(client=client, api_key=None)
    history = [
        {"idx": 0, "status": "old", "action": "old", "result": "old", "error": "old", "payload": "x" * 1000}
    ] + [
        {
            "idx": idx,
            "status": f"status_{idx}",
            "action": f"action_{idx}",
            "result": f"result_{idx}",
            "error": f"error_{idx}",
            "payload": "y" * 1000,
        }
        for idx in range(1, 13)
    ]

    service.decide(user_text="u" * 5000, state={"state": "s" * 5000}, history=history)

    context = client.responses.calls[0]["input"][0]["content"][0]["text"]
    assert '"idx": 0' not in context
    for idx in range(1, 13):
        assert f"status_{idx}" in context
        assert f"action_{idx}" in context
        assert f"result_{idx}" in context
        assert f"error_{idx}" in context
    assert len(context) <= 5500 + len("...[truncated]")


def test_context_summarizes_execution_outcomes_without_losing_partial_action_results():
    client = FakeClient([complete_response(output_text="ok")])
    service = BrainService(client=client, api_key=None)
    request_id = "0123456789abcdef0123456789abcdef"
    history = [
        ExecutionOutcome(
            request_id=f"old{idx:029d}",
            accepted=True,
            sent=True,
            completed=True,
            dry_run=False,
            reply="历史执行反馈很长" * 80,
            actions=(
                {
                    "name": "set_light",
                    "arguments": {"red": idx, "green": idx + 1, "blue": idx + 2, "brightness": idx + 3},
                },
            ),
            action_results=({"name": "set_light.solid", "sent": True, "completed": True},),
        ).to_dict()
        for idx in range(11)
    ]
    history.append(
        ExecutionOutcome(
            request_id=request_id,
            accepted=True,
            sent=True,
            completed=False,
            dry_run=False,
            reply="我已经尝试转动灯头并设置灯光，但亮度控制返回了失败，需要下次避免声称亮度完成。" * 80,
            error="ERR dimmer",
            actions=(
                {"name": "move_joints", "arguments": {"deltas": {"base_yaw": 1.5}}},
                {"name": "set_light", "arguments": {"red": 7, "green": 8, "blue": 9, "brightness": 99}},
            ),
            action_results=(
                {"name": "move_joints", "sent": True, "completed": True},
                {"name": "set_light.solid", "sent": True, "completed": True},
                {"name": "set_light.brightness", "sent": False, "completed": False, "error": "ERR dimmer"},
            ),
        ).to_dict()
    )

    service.decide(user_text="请根据真实反馈决定下一步" * 200, state={"mode": "manual", "note": "s" * 5000}, history=history)

    context = client.responses.calls[0]["input"][0]["content"][0]["text"]
    assert request_id in context
    assert '"accepted": true' in context
    assert '"sent": true' in context
    assert '"completed": false' in context
    assert '"dry_run": false' in context
    assert '"error": "ERR dimmer"' in context
    assert '{"name": "move_joints", "sent": true, "completed": true}' in context
    assert '{"name": "set_light.solid", "sent": true, "completed": true}' in context
    assert '{"name": "set_light.brightness", "sent": false, "completed": false, "error": "ERR dimmer"}' in context
    assert len(context) <= 5500 + len("...[truncated]")


def test_context_keeps_execution_outcome_summary_valid_when_brightness_error_is_long():
    client = FakeClient([complete_response(output_text="ok")])
    service = BrainService(client=client, api_key=None)
    request_id = "fedcba9876543210fedcba9876543210"
    brightness_error = "ERR serial read timeout while waiting for brightness acknowledgement from Pico LED controller; no response received"
    history = [
        ExecutionOutcome(
            request_id=f"older{idx:027d}",
            accepted=True,
            sent=True,
            completed=False,
            dry_run=False,
            reply="历史回复很长" * 120,
            error=brightness_error,
            actions=(
                {"name": "move_joints", "arguments": {"deltas": {"base_yaw": 1.0}}},
                {"name": "set_light", "arguments": {"red": 1, "green": 2, "blue": 3, "brightness": 4}},
            ),
            action_results=(
                {"name": "move_joints", "sent": True, "completed": True},
                {"name": "set_light.solid", "sent": True, "completed": True},
                {"name": "set_light.brightness", "sent": False, "completed": False, "error": brightness_error},
            ),
        ).to_dict()
        for idx in range(11)
    ]
    history.append(
        ExecutionOutcome(
            request_id=request_id,
            accepted=True,
            sent=True,
            completed=False,
            dry_run=False,
            reply="真实反馈说明亮度确认没有收到，下一轮只能说颜色可能已发送，不能声称亮度完成。" * 120,
            error=brightness_error,
            actions=(
                {"name": "move_joints", "arguments": {"deltas": {"base_yaw": 1.0}}},
                {"name": "set_light", "arguments": {"red": 10, "green": 20, "blue": 30, "brightness": 40}},
            ),
            action_results=(
                {"name": "move_joints", "sent": True, "completed": True},
                {"name": "set_light.solid", "sent": True, "completed": True},
                {"name": "set_light.brightness", "sent": False, "completed": False, "error": brightness_error},
            ),
        ).to_dict()
    )

    service.decide(user_text="请只根据最新真实执行反馈回答" * 200, state={"mode": "manual", "note": "s" * 5000}, history=history)

    context = client.responses.calls[0]["input"][0]["content"][0]["text"]
    history_text = context.split("recent_real_execution_history_untrusted_newest_12:\n", 1)[1]
    target_lines = [line for line in history_text.splitlines() if request_id in line]
    assert len(target_lines) == 1
    summary = json.loads(target_lines[0])
    assert summary["request_id"] == request_id
    assert summary["accepted"] is True
    assert summary["sent"] is True
    assert summary["completed"] is False
    assert summary["dry_run"] is False
    assert summary["error"] == brightness_error[:80] + "...[truncated]"
    assert summary["action_results"] == [
        {"name": "move_joints", "sent": True, "completed": True},
        {"name": "set_light.solid", "sent": True, "completed": True},
        {
            "name": "set_light.brightness",
            "sent": False,
            "completed": False,
            "error": brightness_error[:80] + "...[truncated]",
        },
    ]
    assert len(context) <= 5500


def test_output_text_is_bounded_and_too_many_output_blocks_are_rejected():
    client = FakeClient(
        [
            complete_response(message_text("x" * 2500)),
            complete_response(*(message_text(str(idx)) for idx in range(9))),
        ]
    )
    service = BrainService(client=client, api_key=None, max_calls=2)

    bounded = service.decide(user_text="long reply")
    too_many = service.decide(user_text="too many")

    assert bounded.available is True
    assert len(bounded.reply) == 2000
    assert too_many.available is False
    assert too_many.error == "provider_response_unavailable"


def test_brain_dataclasses_are_frozen():
    action = BrainAction("do_nothing", {"reason": "still"})
    plan = BrainPlan("reply", (action,))

    with pytest.raises(Exception):
        action.name = "set_mode"
    with pytest.raises(Exception):
        plan.reply = "changed"
