from pathlib import Path


def test_nano_environment_script_is_read_only_and_routes_to_web_control():
    script = Path("scripts/check_jetson_nano_environment.sh").read_text()

    assert "read-only" in script
    assert "python3.11 -m venv" not in script
    assert "pip install -e '.[nano]'" in script
    assert "ailamp web-control --brain" in script
    assert "Do not blindly rebuild Python or overwrite servo calibration" in script
