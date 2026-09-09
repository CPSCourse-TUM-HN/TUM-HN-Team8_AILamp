from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from ailamp.config import HardwareConfig
from ailamp.paths import resolve_project_path
from ailamp.services.behavior import BehaviorService
from ailamp.services.motor import RecordingStore


EXPECTED_BOM_QUANTITIES = {
    "main_controller": "1",
    "storage": "1",
    "system_card": "1",
    "jetson_power": "1",
    "servo": "5",
    "servo_driver": "1",
    "servo_power": "1",
    "led_controller": "1",
    "led_panel": "1",
    "led_power": "1",
    "logic_level_shifter": "1",
    "led_resistor": "5",
    "led_capacitor": "2",
    "camera": "1",
    "audio_input": "1",
    "speaker": "1",
    "emergency_switch": "1",
    "usb_cable_servo": "1",
    "usb_cable_pico": "1",
    "usb_extension": "1",
    "servo_extension": "5",
    "power_terminal": "4",
    "power_connector": "10",
    "power_wire_red": "2m",
    "power_wire_black": "2m",
    "signal_wire": "2m",
}

NANO_EXPECTED_BOM_QUANTITIES = {
    **EXPECTED_BOM_QUANTITIES,
    "storage": "0",
    "audio_input": "0",
    "speaker": "0",
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str

    def format(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"{status} {self.name}: {self.detail}"


def path_exists(path: str | Path) -> bool:
    return Path(path).exists()


def _bom_part(config: HardwareConfig, key: str) -> str:
    item = config.hardware_bom.get(key)
    if item is None:
        return "<missing>"
    return item.part


def _expected_controller_mpn(config: HardwareConfig) -> str:
    if "Jetson Nano Developer Kit 4GB" in config.controller.model:
        return "945-13450-0000-100"
    return "945-13766-0000-000"


def _is_jetson_nano(config: HardwareConfig) -> bool:
    return "Jetson Nano Developer Kit 4GB" in config.controller.model


def _expected_bom_quantities(config: HardwareConfig) -> dict[str, str]:
    quantities = dict(NANO_EXPECTED_BOM_QUANTITIES if _is_jetson_nano(config) else EXPECTED_BOM_QUANTITIES)
    if not config.audio.input_enabled:
        quantities["audio_input"] = "0"
    if not config.audio.output_enabled:
        quantities["speaker"] = "0"
    return quantities


def run_static_hardware_checks(config: HardwareConfig) -> list[CheckResult]:
    model_path = resolve_project_path(config.simulation.model_path)
    recordings_dir = resolve_project_path(config.simulation.recordings_dir)
    recordings = RecordingStore(recordings_dir).list_names()
    behavior = BehaviorService()
    behavior_motions = {motion for motion, _rgb in behavior.behavior_map.values()}
    expected_bom_quantities = _expected_bom_quantities(config)
    bom_keys = set(config.hardware_bom)
    controller_supported = (
        "Jetson Orin Nano Super" in config.controller.model
        or _is_jetson_nano(config)
    )
    software_matches_controller = (
        (
            _is_jetson_nano(config)
            and "JetPack 4.6" in config.software.target_os
            and "Ubuntu 18.04" in config.software.target_os
            and config.software.install_extra == "nano"
            and config.software.vision_runtime.startswith("OpenAI API-hybrid")
            and "Mac/PC" in config.software.simulation_runtime
        )
        or (
            "Jetson Orin Nano Super" in config.controller.model
            and "JetPack 6" in config.software.target_os
            and config.software.install_extra == "hardware,voice"
        )
    )

    results = [
        CheckResult("config.project", config.system.project_name == "AILamp", config.system.project_name),
        CheckResult("controller.model", controller_supported, config.controller.model),
        CheckResult("controller.mpn", config.controller.mpn == _expected_controller_mpn(config), config.controller.mpn),
        CheckResult("software.profile", software_matches_controller, config.software.target_os),
        CheckResult("software.install_extra", bool(config.software.install_extra), config.software.install_extra),
        CheckResult("software.vision_runtime", bool(config.software.vision_runtime), config.software.vision_runtime),
        CheckResult("software.simulation_runtime", bool(config.software.simulation_runtime), config.software.simulation_runtime),
        CheckResult("power.jetson_supply", config.power.jetson_supply == _bom_part(config, "jetson_power"), config.power.jetson_supply),
        CheckResult("motor.count", config.motors.servo_quantity == 5, str(config.motors.servo_quantity)),
        CheckResult("motor.ids", set(config.motors.ids.values()) == {1, 2, 3, 4, 5}, str(config.motors.ids)),
        CheckResult("motor.driver", config.motors.driver_model == _bom_part(config, "servo_driver"), config.motors.driver_model),
        CheckResult("motor.servo", config.motors.servo_model == _bom_part(config, "servo"), config.motors.servo_model),
        CheckResult("led.count", config.led.count == 64, str(config.led.count)),
        CheckResult("led.controller", config.led.controller == _bom_part(config, "led_controller"), config.led.controller),
        CheckResult("led.panel", config.led.panel == _bom_part(config, "led_panel"), config.led.panel),
        CheckResult("led.level_shifter", config.led.logic_level_shifter == _bom_part(config, "logic_level_shifter"), config.led.logic_level_shifter),
        CheckResult("camera.model", config.camera.model == _bom_part(config, "camera"), config.camera.model),
        CheckResult("camera.fps", config.camera.fps in {15, 30}, str(config.camera.fps)),
        CheckResult("vision.backend", config.vision.backend in {"local_yolo", "api_hybrid"}, config.vision.backend),
        CheckResult(
            "audio.input",
            (not config.audio.input_enabled and config.hardware_bom["audio_input"].quantity == "0")
            or config.audio.input_model == _bom_part(config, "audio_input"),
            "disabled by config" if not config.audio.input_enabled else config.audio.input_model,
        ),
        CheckResult(
            "audio.speaker",
            (not config.audio.output_enabled and config.hardware_bom["speaker"].quantity == "0")
            or config.audio.speaker_model == _bom_part(config, "speaker"),
            "disabled by config" if not config.audio.output_enabled else config.audio.speaker_model,
        ),
        CheckResult("power.servo_supply", config.power.servo_supply == _bom_part(config, "servo_power"), config.power.servo_supply),
        CheckResult("power.led_supply", config.power.led_supply == _bom_part(config, "led_power"), config.power.led_supply),
        CheckResult("simulation.model", model_path.exists(), str(model_path)),
        CheckResult("recordings.dir", recordings_dir.exists(), str(recordings_dir)),
        CheckResult("recording.wake_up", (recordings_dir / "wake_up.csv").exists(), str(recordings_dir / "wake_up.csv")),
        CheckResult("recording.behavior_motions", behavior_motions.issubset(set(recordings)), ", ".join(sorted(behavior_motions))),
        CheckResult("bom.keys", bom_keys == set(expected_bom_quantities), ", ".join(sorted(bom_keys))),
    ]
    for key, quantity in expected_bom_quantities.items():
        actual = config.hardware_bom[key].quantity if key in config.hardware_bom else "<missing>"
        results.append(CheckResult(f"bom.{key}.quantity", actual == quantity, actual))
    return results


def run_device_presence_checks(config: HardwareConfig) -> list[CheckResult]:
    return [
        CheckResult("servo.port", os.path.exists(config.motors.port), config.motors.port),
        CheckResult("led.port", os.path.exists(config.led.port), config.led.port),
        CheckResult("camera.device", os.path.exists(config.camera.device_path), config.camera.device_path),
    ]
