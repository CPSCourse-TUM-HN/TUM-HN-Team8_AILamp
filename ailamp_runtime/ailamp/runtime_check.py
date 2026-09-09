from __future__ import annotations

from importlib.util import find_spec
import os
from pathlib import Path
import tempfile

from ailamp.config import HardwareConfig
from ailamp.hardware_check import CheckResult
from ailamp.paths import resolve_project_path
from ailamp.services.motor import RecordingStore


REQUIRED_RECORDINGS = ("idle", "nod", "scanning", "shy", "wake_up")


def run_runtime_checks(
    config: HardwareConfig,
    *,
    include_voice: bool = False,
    include_motor_runtime: bool = False,
    offline: bool = False,
    env: dict[str, str] | None = None,
) -> list[CheckResult]:
    environ = os.environ if env is None else env
    recordings_dir = resolve_project_path(config.simulation.recordings_dir)
    recording_names = set(RecordingStore(recordings_dir).list_names())
    missing_recordings = [name for name in REQUIRED_RECORDINGS if name not in recording_names]

    results = [
        CheckResult("runtime.profile", bool(config.system.platform), config.system.platform),
        CheckResult("runtime.target_os", bool(config.software.target_os), config.software.target_os),
        CheckResult("runtime.python", bool(config.software.python), config.software.python),
        CheckResult("runtime.install_extra", bool(config.software.install_extra), config.software.install_extra),
        CheckResult("runtime.package", find_spec("ailamp.cli") is not None, "ailamp.cli"),
        CheckResult(
            "runtime.recordings",
            not missing_recordings,
            ",".join(REQUIRED_RECORDINGS) if not missing_recordings else "missing " + ",".join(missing_recordings),
        ),
        CheckResult(
            "runtime.firmware.pico",
            resolve_project_path("firmware/pico_led_controller/code.py").exists(),
            str(resolve_project_path("firmware/pico_led_controller/code.py")),
        ),
        _outputs_writable_check(config),
    ]

    if not offline and (config.vision.backend == "api_hybrid" and config.vision.api_enabled or config.brain.enabled):
        has_key = bool(environ.get("OPENAI_API_KEY"))
        results.append(
            CheckResult(
                "runtime.openai_api_key",
                has_key,
                "set" if has_key else "missing OPENAI_API_KEY",
            )
        )
    elif offline:
        results.append(CheckResult("runtime.openai_api_key", True, "offline mode; OPENAI_API_KEY not required"))

    if include_voice:
        if not config.voice.enabled or not (config.audio.input_enabled and config.audio.output_enabled):
            results.append(CheckResult("runtime.voice.disabled", True, "voice/audio deliberately disabled in this profile"))
        else:
            results.extend(
                [
                    _module_check("runtime.voice.livekit", "livekit.agents"),
                    _module_check("runtime.voice.openai", "openai"),
                    _module_check("runtime.voice.dotenv", "dotenv"),
                    _module_check("runtime.voice.sounddevice", "sounddevice"),
                ]
            )

    if include_motor_runtime:
        results.append(
            _first_available_module_check(
                "runtime.motor_runtime.lelamp_follower",
                (
                    "lelamp.robots.lelamp_follower",
                    "lelamp.robot.lelamp_follower",
                    "lelamp.service.motors.lelamp_follower",
                ),
            )
        )

    return results


def _module_check(name: str, module_name: str) -> CheckResult:
    try:
        available = find_spec(module_name) is not None
    except ModuleNotFoundError:
        available = False
    return CheckResult(name, available, module_name)


def _first_available_module_check(name: str, module_names: tuple[str, ...]) -> CheckResult:
    for module_name in module_names:
        try:
            if find_spec(module_name) is not None:
                return CheckResult(name, True, module_name)
        except ModuleNotFoundError:
            continue
    return CheckResult(name, False, ",".join(module_names))


def _outputs_writable_check(config: HardwareConfig) -> CheckResult:
    output_dir = resolve_project_path(config.runtime.vision_state_file).parent
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=output_dir, prefix=".runtime-check.", delete=False) as handle:
            handle.write("ok\n")
            temp_path = Path(handle.name)
        temp_path.unlink(missing_ok=True)
        return CheckResult("runtime.outputs_writable", True, str(output_dir))
    except OSError as exc:
        return CheckResult("runtime.outputs_writable", False, f"{output_dir}: {exc}")
