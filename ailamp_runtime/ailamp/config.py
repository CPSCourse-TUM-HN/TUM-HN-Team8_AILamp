from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from ailamp.paths import project_root


@dataclass(frozen=True)
class SystemConfig:
    project_name: str
    platform: str


@dataclass(frozen=True)
class ControllerConfig:
    model: str
    mpn: str
    storage: str
    system_card: str


@dataclass(frozen=True)
class SoftwareConfig:
    target_os: str
    jetpack: str
    python: str
    install_extra: str
    vision_runtime: str
    simulation_runtime: str


@dataclass(frozen=True)
class MotorConfig:
    port: str
    lamp_id: str
    driver_model: str
    servo_model: str
    servo_quantity: int
    fps: int
    ids: dict[str, int]


@dataclass(frozen=True)
class LEDConfig:
    port: str
    controller: str
    panel: str
    count: int
    baudrate: int
    brightness: int
    power_supply: str
    logic_level_shifter: str


@dataclass(frozen=True)
class PowerConfig:
    jetson_supply: str
    servo_supply: str
    led_supply: str
    emergency_switch: str
    barrel_adapter: str
    power_connector: str
    power_wire_red: str
    power_wire_black: str
    signal_wire: str


@dataclass(frozen=True)
class BOMItem:
    part: str
    quantity: str


@dataclass(frozen=True)
class CameraConfig:
    model: str
    device: int
    device_path: str
    width: int
    height: int
    fps: int
    pixel_format: str


@dataclass(frozen=True)
class VisionConfig:
    backend: str
    model: str
    pose_model: str
    pose_enabled: bool
    api_enabled: bool
    api_model: str
    api_interval_s: float
    api_image_max_px: int
    api_timeout_s: float
    api_event_ttl_s: float
    confidence: float
    left_threshold: float
    right_threshold: float
    close_area_ratio: float
    far_area_ratio: float
    far_depth_m: float


@dataclass(frozen=True)
class AudioConfig:
    input_model: str
    speaker_model: str
    input_device: str
    output_device: str
    input_enabled: bool = True
    output_enabled: bool = True


@dataclass(frozen=True)
class VoiceConfig:
    provider: str
    transport: str
    enabled: bool


@dataclass(frozen=True)
class RuntimeConfig:
    vision_state_file: str
    vision_interval_s: float
    action_cooldown_s: float


@dataclass(frozen=True)
class BrainConfig:
    enabled: bool
    provider: str
    model: str
    interval_s: float
    timeout_s: float
    plan_ttl_s: float
    max_calls: int
    image_max_px: int


@dataclass(frozen=True)
class SimulationConfig:
    model_path: str
    recordings_dir: str
    lock_freejoint: bool


@dataclass(frozen=True)
class HardwareConfig:
    system: SystemConfig
    controller: ControllerConfig
    software: SoftwareConfig
    power: PowerConfig
    motors: MotorConfig
    led: LEDConfig
    camera: CameraConfig
    vision: VisionConfig
    audio: AudioConfig
    voice: VoiceConfig
    runtime: RuntimeConfig
    brain: BrainConfig
    simulation: SimulationConfig
    hardware_bom: dict[str, BOMItem]


def _resolve_config_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return project_root() / candidate


def load_hardware_config(path: str | Path) -> HardwareConfig:
    config_path = _resolve_config_path(path)
    with config_path.open("rb") as handle:
        raw: dict[str, Any] = tomllib.load(handle)

    motor_raw = {
        "lamp_id": raw.get("system", {}).get("project_name", "AILamp").lower(),
        **raw["motors"],
    }
    audio_raw = {
        "input_enabled": True,
        "output_enabled": True,
        **raw["audio"],
    }
    brain_raw = {
        "enabled": False,
        "provider": "openai",
        "model": raw.get("vision", {}).get("api_model", "gpt-4.1-mini"),
        "interval_s": 3.0,
        "timeout_s": 10.0,
        "plan_ttl_s": 8.0,
        "max_calls": 200,
        "image_max_px": 512,
    }
    brain_raw.update(raw.get("brain", {}))
    vision_raw = {
        "backend": "local_yolo",
        "api_enabled": False,
        "api_model": "gpt-4.1-mini",
        "api_interval_s": 1.0,
        "api_image_max_px": 512,
        "api_timeout_s": 10.0,
        "api_event_ttl_s": 2.0,
    }
    vision_raw.update(raw["vision"])

    return HardwareConfig(
        system=SystemConfig(**raw["system"]),
        controller=ControllerConfig(**raw["controller"]),
        software=SoftwareConfig(**raw["software"]),
        power=PowerConfig(**raw["power"]),
        motors=MotorConfig(**motor_raw),
        led=LEDConfig(**raw["led"]),
        camera=CameraConfig(**raw["camera"]),
        vision=VisionConfig(**vision_raw),
        audio=AudioConfig(**audio_raw),
        voice=VoiceConfig(**raw["voice"]),
        runtime=RuntimeConfig(**raw["runtime"]),
        brain=BrainConfig(**brain_raw),
        simulation=SimulationConfig(**raw["simulation"]),
        hardware_bom={key: BOMItem(**value) for key, value in raw["hardware_bom"].items()},
    )
