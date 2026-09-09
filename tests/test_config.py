from pathlib import Path
import re
import tomllib

from ailamp.config import load_hardware_config


_EXTRA_MARKER_RE = re.compile(r"extra == ['\"]([^'\"]+)['\"]")
_REQUIREMENT_RE = re.compile(r"\s*([A-Za-z0-9_.-]+)(?:\[([^\]]+)\])?")


def _dependency_name(name):
    return name.lower().replace("_", "-")


def _requirement_key(requirement):
    match = _REQUIREMENT_RE.match(requirement)
    assert match is not None, f"Could not parse requirement: {requirement}"
    extras = match.group(2) or ""
    return (
        _dependency_name(match.group(1)),
        tuple(sorted(_dependency_name(extra.strip()) for extra in extras.split(",") if extra.strip())),
    )


def _lock_dependency_key(dependency):
    extras = dependency.get("extras", dependency.get("extra", []))
    return (
        _dependency_name(dependency["name"]),
        tuple(sorted(_dependency_name(extra) for extra in extras)),
    )


def _editable_ailamp_lock_package():
    lock = tomllib.loads(Path("uv.lock").read_text())
    packages = [
        package
        for package in lock["package"]
        if package["name"] == "ailamp" and package.get("source") == {"editable": "."}
    ]
    assert len(packages) == 1
    return packages[0]


def _pyproject_optional_dependency_keys():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    return {
        extra: tuple(sorted(_requirement_key(requirement) for requirement in requirements))
        for extra, requirements in pyproject["project"]["optional-dependencies"].items()
    }


def _locked_optional_dependency_keys(package):
    return {
        extra: tuple(sorted(_lock_dependency_key(dependency) for dependency in dependencies))
        for extra, dependencies in package["optional-dependencies"].items()
    }


def _locked_metadata_optional_dependency_keys(package):
    dependencies_by_extra = {}
    for dependency in package["metadata"]["requires-dist"]:
        marker = dependency.get("marker")
        if marker is None:
            continue
        match = _EXTRA_MARKER_RE.fullmatch(marker)
        assert match is not None, f"Unexpected editable ailamp extra marker: {marker}"
        dependencies_by_extra.setdefault(match.group(1), []).append(_lock_dependency_key(dependency))

    return {
        extra: tuple(sorted(dependencies))
        for extra, dependencies in dependencies_by_extra.items()
    }


def test_loads_exact_hardware_bom_and_ports():
    config = load_hardware_config(Path("config/hardware.toml"))

    assert config.system.project_name == "AILamp"
    assert config.controller.model == "NVIDIA Jetson Nano Developer Kit 4GB"
    assert config.controller.mpn == "945-13450-0000-100"
    assert "JetPack 4.6" in config.software.target_os
    assert "Ubuntu 18.04" in config.software.target_os
    assert config.software.install_extra == "nano"
    assert config.software.vision_runtime.startswith("OpenAI API-hybrid")
    assert "Mac/PC" in config.software.simulation_runtime
    assert config.motors.port == "/dev/ttyACM0"
    assert config.led.port == "/dev/ttyACM1"
    assert config.camera.device_path == "/dev/video0"
    assert config.motors.lamp_id == "ailamp"
    assert config.motors.ids == {
        "base_yaw": 1,
        "base_pitch": 2,
        "elbow_pitch": 3,
        "wrist_roll": 4,
        "wrist_pitch": 5,
    }
    assert config.led.count == 64
    assert config.power.servo_supply == "MEAN WELL GST120A12-P1J, 12V 10A 120W"
    assert config.power.jetson_supply.startswith("Jetson Nano 5V 4A")
    assert config.led.logic_level_shifter == "TXS0108E 8-Channel Logic Level Converter Module"
    assert config.hardware_bom["power_connector"].quantity == "10"
    assert config.hardware_bom["jetson_power"].quantity == "1"
    assert config.hardware_bom["camera"].part.startswith("Arducam UB0234")
    assert not hasattr(config, "birthday")
    assert config.audio.input_enabled is False
    assert config.audio.output_enabled is False
    assert config.voice.enabled is False
    assert config.brain.enabled is False
    assert config.brain.provider == "openai"
    assert config.brain.model == "gpt-4.1-mini"
    assert config.brain.interval_s == 3.0
    assert config.brain.plan_ttl_s == 8.0
    assert config.runtime.vision_state_file == "outputs/vision_state.json"
    assert config.runtime.vision_interval_s == 0.2
    assert config.runtime.action_cooldown_s == 1.5
    assert config.vision.backend == "api_hybrid"
    assert config.vision.pose_enabled is False
    assert config.vision.pose_model == "disabled-on-jetson-nano"
    assert config.vision.api_enabled is True
    assert config.vision.far_area_ratio == 0.02


def test_loads_camera_path_and_pixel_format():
    config = load_hardware_config(Path("config/hardware.toml"))

    assert config.camera.device_path == "/dev/video0"
    assert config.camera.pixel_format == "MJPG"


def test_loads_jetson_nano_api_hybrid_profile():
    config = load_hardware_config(Path("config/hardware.jetson-nano.toml"))

    assert config.controller.model == "NVIDIA Jetson Nano Developer Kit 4GB"
    assert config.controller.mpn == "945-13450-0000-100"
    assert config.camera.width == 640
    assert config.camera.height == 480
    assert config.camera.fps == 15
    assert config.vision.backend == "api_hybrid"
    assert config.vision.pose_enabled is False
    assert config.vision.api_enabled is True
    assert config.vision.api_model == "gpt-4.1-mini"
    assert config.vision.api_interval_s == 1.0
    assert config.vision.api_image_max_px == 512
    assert config.runtime.vision_interval_s == 0.2


def test_loads_orin_reference_profile():
    config = load_hardware_config(Path("config/hardware.orin.toml"))

    assert config.controller.model == "NVIDIA Jetson Orin Nano Super Developer Kit"
    assert config.controller.mpn == "945-13766-0000-000"
    assert "JetPack 6" in config.software.target_os
    assert config.software.install_extra == "hardware,voice"
    assert config.vision.backend == "local_yolo"
    assert config.vision.pose_enabled is True
    assert config.power.jetson_supply.startswith("Jetson Orin Nano Super 19V")


def test_hardware_bom_quantities_are_complete():
    from ailamp.hardware_check import NANO_EXPECTED_BOM_QUANTITIES

    config = load_hardware_config(Path("config/hardware.toml"))

    assert set(config.hardware_bom) == set(NANO_EXPECTED_BOM_QUANTITIES)
    assert {key: item.quantity for key, item in config.hardware_bom.items()} == NANO_EXPECTED_BOM_QUANTITIES


def test_nano_extra_includes_voice_and_responses_api_dependencies():
    raw = tomllib.loads(Path("pyproject.toml").read_text())
    nano_deps = raw["project"]["optional-dependencies"]["nano"]
    voice_deps = raw["project"]["optional-dependencies"]["voice"]

    assert "openai>=2.35.0" in nano_deps
    assert "sounddevice>=0.5" not in nano_deps
    assert "livekit-agents[openai]>=1.2" not in nano_deps
    assert "livekit-plugins-noise-cancellation>=0.2" not in nano_deps
    assert "sounddevice>=0.5" in voice_deps


def test_editable_lock_optional_dependencies_match_pyproject():
    pyproject_optional_deps = _pyproject_optional_dependency_keys()
    lock_package = _editable_ailamp_lock_package()
    locked_optional_deps = _locked_optional_dependency_keys(lock_package)
    locked_metadata_optional_deps = _locked_metadata_optional_dependency_keys(lock_package)

    assert set(lock_package["metadata"]["provides-extras"]) == set(pyproject_optional_deps)
    assert locked_optional_deps == pyproject_optional_deps
    assert locked_metadata_optional_deps == pyproject_optional_deps

    audio_dependency_names = {
        ("livekit-agents", ("openai",)),
        ("livekit-plugins-noise-cancellation", ()),
        ("sounddevice", ()),
    }
    assert audio_dependency_names.isdisjoint(dict(locked_optional_deps)["nano"])
    assert audio_dependency_names.isdisjoint(dict(locked_metadata_optional_deps)["nano"])
    assert ("sounddevice", ()) not in dict(locked_optional_deps)["hardware"]
    assert ("sounddevice", ()) not in dict(locked_metadata_optional_deps)["hardware"]
    assert ("sounddevice", ()) in dict(locked_optional_deps)["voice"]
    assert ("sounddevice", ()) in dict(locked_metadata_optional_deps)["voice"]


def test_legacy_birthday_block_is_ignored(tmp_path):
    raw = Path("config/hardware.toml").read_text()
    legacy = raw + '\n[birthday]\nenabled = true\nmonth = 5\nday = 8\nmessage = "old"\nmotion = "nod"\nrgb = [1, 2, 3]\nstate_file = "old.json"\nspeech_command = "auto"\n'
    path = tmp_path / "legacy.toml"
    path.write_text(legacy)

    config = load_hardware_config(path)

    assert not hasattr(config, "birthday")
    assert config.brain.provider == "openai"
