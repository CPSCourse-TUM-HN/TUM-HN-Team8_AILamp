from __future__ import annotations

import argparse
from datetime import datetime
from datetime import timezone
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from ailamp.agent.livekit_agent import AILampToolbox, DryRunLEDService, DryRunMotorService
from ailamp.config import load_hardware_config
from ailamp.hardware_check import CheckResult
from ailamp.hardware_check import run_device_presence_checks, run_static_hardware_checks
from ailamp.paths import resolve_project_path
from ailamp.models import BehaviorAction, VisionEvent, VisionEventType
from ailamp.runtime_check import run_runtime_checks
from ailamp.services.behavior import BehaviorService
from ailamp.services.decision import DecisionService
from ailamp.services.led_serial import LEDSerialService
from ailamp.services.motor import MotorService
from ailamp.services.motor import RecordingStore
from ailamp.services.vision import classify_person_position
from ailamp.services.vision_runtime import VisionRuntime
from ailamp.services.vision_runtime import VisionSnapshot, VisionStateStore
from ailamp.simulation.mujoco_runner import MujocoRunner
from ailamp.simulation.sim_vision import classify_virtual_target_from_joints


DEFAULT_CONFIG = "config/hardware.toml"
EXPECTED_SIM_ACTUATORS = ["2", "1", "3", "4", "5"]
SIM_CHECK_RECORDINGS = ["wake_up", "idle", "nod", "scanning", "shy"]
SIM_CHECK_ADAPTER_VISUALS = [
    "ailamp_integrated_base_shell_visual",
    "ailamp_integrated_base_cover_visual",
    "ailamp_base_arm_link_boot_visual",
    "ailamp_cable_clip_6mm_visual",
    "ailamp_cable_clip_10mm_visual",
]


def _config(args):
    return load_hardware_config(args.config)


def hardware_check(args) -> int:
    config = _config(args)
    results = run_static_hardware_checks(config)
    if args.include_devices:
        results.extend(run_device_presence_checks(config))
    if args.failures_only:
        results = [result for result in results if not result.passed]
    for result in results:
        print(result.format())
    return 0 if all(result.passed for result in results) else 1


def runtime_check(args) -> int:
    config = _config(args)
    results = run_runtime_checks(
        config,
        include_voice=args.include_voice,
        include_motor_runtime=args.include_motor_runtime,
        offline=args.offline,
    )
    if args.include_devices:
        results.extend(run_device_presence_checks(config))
    for result in results:
        print(result.format())
    return 0 if all(result.passed for result in results) else 1


def led_test(args) -> int:
    config = _config(args)
    service = LEDSerialService(config.led.port, led_count=config.led.count, baudrate=config.led.baudrate)
    service.connect()
    try:
        print("PING =>", service.ping())
        print("BRIGHTNESS =>", service.brightness(config.led.brightness))
        print("SOLID =>", service.solid(*args.color))
    finally:
        service.close()
    return 0


def motor_test(args) -> int:
    config = _config(args)
    store = RecordingStore(resolve_project_path(config.simulation.recordings_dir))
    print("recordings:", ", ".join(store.list_names()))
    print("port:", config.motors.port)
    print("ids:", config.motors.ids)
    return 0


def sim_demo(args) -> int:
    config = _config(args)
    behavior = BehaviorService()
    target_joint_sets = [
        None,
        {"target_slide_x": -0.6, "target_slide_y": -1.5},
        {"target_slide_x": 0.0, "target_slide_y": -1.5},
        {"target_slide_x": 0.6, "target_slide_y": -1.5},
        {"target_slide_x": 0.0, "target_slide_y": -0.45},
        {"target_slide_x": 0.0, "target_slide_y": -3.8},
    ]
    for joints in target_joint_sets:
        event = classify_virtual_target_from_joints(joints)
        action = behavior.decide(event)
        print(f"target_joints={joints} event={event.event_type.value} motion={action.motion} rgb={action.rgb}")

    if args.render:
        runner = MujocoRunner(config.simulation.model_path, lock_freejoint=config.simulation.lock_freejoint)
        runner.load()
        runner.set_target_position(0.0, 1.5)
        runner.replay_recording("wake_up", config.simulation.recordings_dir, max_frames=30)
        output = runner.render("outputs/sim_demo.png")
        print("render:", output)
    return 0


def sim_viewer(args) -> int:
    config = _config(args)
    runner = MujocoRunner(config.simulation.model_path, lock_freejoint=config.simulation.lock_freejoint)
    info = runner.info()
    print(f"model={info.model_path}")
    print(f"nq={info.nq} nv={info.nv} nu={info.nu}")
    print("joints:", ", ".join(info.joints))
    print("actuators:", ", ".join(info.actuators))
    if args.render:
        output = runner.render(args.render, camera_name=args.camera)
        print("render:", output)
    return 0


def sim_check(args) -> int:
    config = _config(args)
    runner = MujocoRunner(config.simulation.model_path, lock_freejoint=config.simulation.lock_freejoint)
    results: list[CheckResult] = []

    try:
        info = runner.info()
        results.extend(_sim_info_checks(config, info))
        results.extend(_sim_recording_checks(runner, config))
        results.append(_sim_virtual_events_check())
        results.append(_sim_adapter_visuals_check(config))
        if args.render:
            _prepare_sim_render_state(runner, config, args.camera)
            output = runner.render(args.render, camera_name=args.camera)
            results.append(CheckResult("sim.render", True, str(output)))
    except Exception as exc:  # noqa: BLE001 - command should report a clear failed check.
        results.append(CheckResult("sim.load", False, str(exc)))

    for result in results:
        print(result.format())
    return 0 if all(result.passed for result in results) else 1


def _prepare_sim_render_state(runner: MujocoRunner, config, camera_name: str | None) -> None:
    runner.load()
    if camera_name == "ailamp_overview_camera":
        runner.set_target_position(1.2, 3.8)
        runner.replay_recording("wake_up", config.simulation.recordings_dir, max_frames=30)


def _sim_info_checks(config, info) -> list[CheckResult]:
    model_path = resolve_project_path(config.simulation.model_path)
    actuators = list(info.actuators)
    joints = set(info.joints)
    return [
        CheckResult("sim.model", model_path.exists(), str(model_path)),
        CheckResult("sim.nu", info.nu == 5, str(info.nu)),
        CheckResult("sim.actuators", actuators == EXPECTED_SIM_ACTUATORS, ",".join(actuators)),
        CheckResult(
            "sim.root_freejoint",
            config.simulation.lock_freejoint and "lamparm__base_elbow_freejoint" in joints,
            f"lock_freejoint={config.simulation.lock_freejoint}",
        ),
        CheckResult(
            "sim.target_joints",
            {"target_slide_x", "target_slide_y"}.issubset(joints),
            ",".join(sorted(joints)),
        ),
    ]


def _sim_recording_checks(runner: MujocoRunner, config) -> list[CheckResult]:
    results: list[CheckResult] = []
    for recording in SIM_CHECK_RECORDINGS:
        try:
            frames = runner.replay_recording(recording, config.simulation.recordings_dir, max_frames=5)
            results.append(CheckResult(f"sim.recording.{recording}", frames > 0, str(frames)))
        except Exception as exc:  # noqa: BLE001 - report individual recording failure.
            results.append(CheckResult(f"sim.recording.{recording}", False, str(exc)))
    return results


def _sim_virtual_events_check() -> CheckResult:
    target_joint_sets = [
        None,
        {"target_slide_x": -0.6, "target_slide_y": -1.5},
        {"target_slide_x": 0.0, "target_slide_y": -1.5},
        {"target_slide_x": 0.6, "target_slide_y": -1.5},
        {"target_slide_x": 0.0, "target_slide_y": -0.45},
        {"target_slide_x": 0.0, "target_slide_y": -3.8},
    ]
    events = [classify_virtual_target_from_joints(joints).event_type.value for joints in target_joint_sets]
    expected = ["no_person", "person_left", "person_center", "person_right", "person_close", "person_far"]
    return CheckResult("sim.virtual_events", events == expected, ",".join(events))


def _sim_adapter_visuals_check(config) -> CheckResult:
    scene_path = resolve_project_path(config.simulation.model_path)
    roots = [ET.parse(scene_path).getroot()]
    for include in roots[0].findall("include"):
        include_file = include.attrib.get("file")
        if include_file:
            roots.append(ET.parse(scene_path.parent / include_file).getroot())

    geoms = {
        geom.attrib.get("name"): geom.attrib
        for root in roots
        for geom in root.findall(".//geom")
        if geom.attrib.get("name")
    }
    meshes = {
        mesh.attrib.get("name"): mesh.attrib
        for root in roots
        for mesh in root.findall(".//mesh")
        if mesh.attrib.get("name")
    }
    valid = True
    for name in SIM_CHECK_ADAPTER_VISUALS:
        geom = geoms.get(name)
        mesh_name = geom.get("mesh") if geom is not None else None
        valid = (
            valid
            and geom is not None
            and geom.get("type") == "mesh"
            and mesh_name in meshes
            and geom.get("contype") == "0"
            and geom.get("conaffinity") == "0"
        )
    return CheckResult("sim.adapter_visuals", valid, ",".join(SIM_CHECK_ADAPTER_VISUALS))


def camera_test(args) -> int:
    config = _config(args)
    from ailamp.services.camera import CameraService

    camera = CameraService(
        config.camera.device_path,
        config.camera.width,
        config.camera.height,
        config.camera.fps,
        config.camera.pixel_format,
    )
    try:
        report = camera.probe()
        print(report)
    finally:
        camera.close()
    return 0


def audio_test(args) -> int:
    config = _config(args)
    if not (config.audio.input_enabled or config.audio.output_enabled or config.voice.enabled):
        print("audio disabled in this hardware profile; use the audio/voice profile before running audio-test")
        return 1
    from ailamp.services.audio import AudioService

    report = AudioService(config.audio.input_model, config.audio.speaker_model).probe()
    print("input:", report.input_model)
    print("speaker:", report.speaker_model)
    for device in report.devices:
        print(device)
    return 0


def vision_demo(args) -> int:
    config = _config(args)
    if config.vision.backend == "api_hybrid":
        runtime = VisionRuntime(config)
        try:
            runtime.open(with_outputs=False)
            print(runtime.step().format())
        finally:
            runtime.close()
        return 0

    from ailamp.services.camera import CameraService
    from ailamp.services.vision import DetectorService

    camera = CameraService(
        config.camera.device_path,
        config.camera.width,
        config.camera.height,
        config.camera.fps,
        config.camera.pixel_format,
    )
    detector = DetectorService(config.vision.model, config.vision.confidence)
    detector.load()
    try:
        camera.open()
        frame = camera.read()
        if frame is None:
            print("event=no_person confidence=0.00")
            return 1
        bbox = detector.detect_person(frame)
        event = classify_person_position(
            bbox,
            (config.camera.width, config.camera.height),
            left_threshold=config.vision.left_threshold,
            right_threshold=config.vision.right_threshold,
            close_area_ratio=config.vision.close_area_ratio,
            far_area_ratio=config.vision.far_area_ratio,
        )
        action = BehaviorService().decide(event)
        print(f"event={event.event_type.value} confidence={event.confidence:.2f} motion={action.motion} rgb={action.rgb}")
    finally:
        camera.close()
    return 0


def vision_loop(args) -> int:
    config = _config(args)
    runtime = VisionRuntime(config)
    try:
        runtime.open(with_outputs=args.with_outputs)
        try:
            for result in runtime.run(
                max_frames=args.frames,
                interval_s=args.interval,
                apply_outputs=args.with_outputs,
            ):
                print(result.format())
        except KeyboardInterrupt:
            return 130
    finally:
        runtime.close()
    return 0


def agent(args) -> int:
    config = _config(args)
    if not config.voice.enabled:
        print("voice agent is disabled in this no-audio profile; use `ailamp web-control --brain` for the primary console")
        return 1
    from ailamp.agent.livekit_agent import run_agent

    run_agent(args.config, with_outputs=args.with_outputs)
    return 0


def web_control(args) -> int:
    config = _config(args)
    token = os.environ.get(args.token_env) if args.token_env else None
    if args.host in {"", "0.0.0.0", "::"}:
        print("web-control requires a concrete host; use 127.0.0.1 or the Nano LAN IPv4 address")
        return 2
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        if not token or len(token) < 32:
            print(f"non-loopback bind requires a strong token in {args.token_env}")
            return 2
    from ailamp.web import create_server

    try:
        server = create_server(
            (args.host, args.port),
            config,
            with_outputs=args.with_outputs,
            brain_enabled=args.brain,
            vision_enabled=args.vision,
            external_token=token,
        )
    except Exception as exc:  # noqa: BLE001 - bind/auth setup failure should be a clean CLI error.
        print(f"web-control startup failed: {exc}")
        return 1
    try:
        server.runtime.open()
        server.runtime.start_scheduler()
    except Exception as exc:  # noqa: BLE001 - failed startup must release earlier resources.
        close_errors = server.runtime.close()
        server.server_close()
        print(f"web-control startup failed: {exc}")
        if close_errors:
            print("cleanup errors:", "; ".join(close_errors))
        return 1
    print(
        f"AILamp web-control listening on http://{args.host}:{server.server_address[1]} "
        f"outputs={'enabled' if args.with_outputs else 'dry-run'} "
        f"brain={'enabled' if args.brain else 'disabled'} "
        f"vision={'enabled' if args.vision else 'disabled'}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        close_errors = server.runtime.close()
        if close_errors:
            print("cleanup errors:", "; ".join(close_errors))
        server.server_close()
    return 0


def agent_tools_test(args) -> int:
    config = _config(args)
    event = VisionEvent(
        VisionEventType(args.event),
        confidence=args.confidence,
        normalized_offset=args.offset,
        area_ratio=args.area_ratio,
    )
    decision = DecisionService().decide(event, user_text=args.request)
    action = BehaviorAction(event=event, motion=decision.motion, rgb=decision.rgb)
    state = VisionSnapshot(
        event=event,
        action=action,
        updated_at=datetime.now(timezone.utc).isoformat(),
        frame_index=0,
        decision=decision,
    )
    VisionStateStore(config.runtime.vision_state_file).write(state)

    if args.with_outputs:
        toolbox = AILampToolbox(args.config)
    else:
        toolbox = AILampToolbox(
            args.config,
            led_service=DryRunLEDService(),
            motor_service=DryRunMotorService(),
        )
        toolbox._outputs_connected = True

    print("capabilities:", toolbox.describe_capabilities())
    print("vision:", toolbox.current_vision_state())
    decision = toolbox.decide_response(args.request)
    deltas = ",".join(f"{command.joint}:{command.delta_deg:+.2f}" for command in decision.joint_deltas) or "none"
    print(f"decision: motion={decision.motion} rgb={decision.rgb} joint_deltas={deltas} reason={decision.reason}")

    if args.apply:
        print("apply:", toolbox.apply_behavior_for_current_vision(args.request))
    if args.recording:
        print("recording:", toolbox.play_recording(args.recording))
    if args.color:
        print("light:", toolbox.set_light(*args.color))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ailamp")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)

    hardware = subparsers.add_parser("hardware-check")
    hardware.add_argument("--include-devices", action="store_true")
    hardware.add_argument("--failures-only", action="store_true")
    hardware.set_defaults(func=hardware_check)

    runtime = subparsers.add_parser("runtime-check")
    runtime.add_argument("--include-devices", action="store_true")
    runtime.add_argument("--include-voice", action="store_true")
    runtime.add_argument("--include-motor-runtime", action="store_true")
    runtime.add_argument("--offline", action="store_true", help="Do not require cloud API keys")
    runtime.set_defaults(func=runtime_check)

    led = subparsers.add_parser("led-test")
    led.add_argument("--color", nargs=3, type=int, default=(255, 180, 80))
    led.set_defaults(func=led_test)

    subparsers.add_parser("motor-test").set_defaults(func=motor_test)
    subparsers.add_parser("camera-test").set_defaults(func=camera_test)
    subparsers.add_parser("audio-test").set_defaults(func=audio_test)
    subparsers.add_parser("vision-demo").set_defaults(func=vision_demo)
    vision_loop_parser = subparsers.add_parser("vision-loop")
    vision_loop_parser.add_argument("--frames", type=int, default=None, help="Stop after N frames; omit to run forever")
    vision_loop_parser.add_argument("--interval", type=float, default=None, help="Seconds between frames")
    vision_loop_parser.add_argument("--with-outputs", action="store_true", help="Drive ST3215 motions and Pico LEDs")
    vision_loop_parser.set_defaults(func=vision_loop)
    agent_parser = subparsers.add_parser("agent")
    agent_parser.add_argument("--with-outputs", action="store_true", help="Allow agent tools to drive ST3215 motions and Pico LEDs")
    agent_parser.set_defaults(func=agent)
    web_parser = subparsers.add_parser("web-control")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)
    web_parser.add_argument("--with-outputs", action="store_true", help="Permit physical outputs after browser unlock")
    web_parser.add_argument("--brain", action="store_true", help="Enable real OpenAI brain calls")
    web_parser.add_argument("--vision", action="store_true", help="Send camera JPEG frames to the brain")
    web_parser.add_argument("--token-env", default="AILAMP_CONTROL_TOKEN")
    web_parser.set_defaults(func=web_control)
    agent_tools = subparsers.add_parser("agent-tools-test")
    agent_tools.add_argument(
        "--event",
        choices=[event.value for event in VisionEventType],
        default=VisionEventType.PERSON_CENTER.value,
    )
    agent_tools.add_argument("--confidence", type=float, default=0.9)
    agent_tools.add_argument("--offset", type=float, default=0.0, help="Normalized horizontal person offset, -1 left to +1 right")
    agent_tools.add_argument("--area-ratio", type=float, default=0.0, help="Detected person bbox area ratio")
    agent_tools.add_argument("--request", default=None, help="Optional voice/user request to feed the AI decision layer")
    agent_tools.add_argument("--apply", action="store_true", help="Apply mapped behavior through the toolbox")
    agent_tools.add_argument("--recording", default=None, help="Play a named recording through the toolbox")
    agent_tools.add_argument("--color", nargs=3, type=int, default=None, help="Set LED color through the toolbox")
    agent_tools.add_argument("--with-outputs", action="store_true", help="Use real ST3215 and Pico outputs")
    agent_tools.set_defaults(func=agent_tools_test)

    sim_demo_parser = subparsers.add_parser("sim-demo")
    sim_demo_parser.add_argument("--render", action="store_true")
    sim_demo_parser.set_defaults(func=sim_demo)

    sim_viewer_parser = subparsers.add_parser("sim-viewer")
    sim_viewer_parser.add_argument("--render", default=None)
    sim_viewer_parser.add_argument("--camera", default="ailamp_overview_camera")
    sim_viewer_parser.set_defaults(func=sim_viewer)

    sim_check_parser = subparsers.add_parser("sim-check")
    sim_check_parser.add_argument("--render", default=None)
    sim_check_parser.add_argument("--camera", default="ailamp_overview_camera")
    sim_check_parser.set_defaults(func=sim_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
