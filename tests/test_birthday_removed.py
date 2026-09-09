from pathlib import Path

from ailamp.cli import build_parser
from ailamp.config import load_hardware_config


def test_birthday_command_and_config_are_removed():
    parser = build_parser()
    help_text = parser.format_help()
    config = load_hardware_config("config/hardware.toml")

    assert "birthday-check" not in help_text
    assert not hasattr(config, "birthday")
    assert not Path("ailamp_runtime/ailamp/services/birthday.py").exists()
