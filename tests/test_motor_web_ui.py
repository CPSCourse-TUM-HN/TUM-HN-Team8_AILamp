from __future__ import annotations

import re

from ailamp.motor_web_ui import (
    FAULT_NOTICE,
    HARDWARE_BANNER,
    INDEX_HTML,
    SIMULATION_BANNER,
    STOP_LABEL,
)


REQUIRED_IDS = (
    "mode-banner",
    "fault-panel",
    "btn-connect",
    "btn-arm",
    "btn-capture-home",
    "btn-release",
    "btn-script",
    "btn-home",
    "btn-stop",
    "recordings",
    "joints",
    "events",
    "message",
    "confirm-panel",
    "confirm-text",
    "confirm-supported",
    "arm-goal-velocity",
    "arm-acceleration",
    "arm-torque-limit",
    "token",
)


def test_ui_exposes_every_control_id():
    for element_id in REQUIRED_IDS:
        assert f'id="{element_id}"' in INDEX_HTML, element_id


def test_ui_uses_honest_labels_and_fault_notice():
    assert STOP_LABEL == "停止（取消后续帧并保持，不断电）"
    assert STOP_LABEL in INDEX_HTML
    assert FAULT_NOTICE == "故障已锁定，程序不会自动重试；请先人工确认机构与供电状态，然后重启进程。"
    assert FAULT_NOTICE in INDEX_HTML
    assert SIMULATION_BANNER in INDEX_HTML
    assert HARDWARE_BANNER in INDEX_HTML
    assert "我确认机构已被支撑" in INDEX_HTML


def test_ui_loads_nothing_from_the_network():
    assert "http://" not in INDEX_HTML
    assert "https://" not in INDEX_HTML
    assert "<link" not in INDEX_HTML
    assert re.search(r'src\s*=', INDEX_HTML) is None
    assert "@import" not in INDEX_HTML


def test_ui_does_not_prefill_arm_registers():
    for field in ("arm-goal-velocity", "arm-acceleration", "arm-torque-limit"):
        tag = re.search(rf'<input[^>]*id="{field}"[^>]*>', INDEX_HTML)
        assert tag is not None, field
        assert "value=" not in tag.group(0), field


def test_ui_polls_state_without_hardcoding_recordings():
    assert "/api/state" in INDEX_HTML
    assert "POLL_INTERVAL_MS" in INDEX_HTML
    assert "btn-play-" in INDEX_HTML
    for name in ("wake_up", "happy_wiggle", "headshake"):
        assert f'id="btn-play-{name}"' not in INDEX_HTML
