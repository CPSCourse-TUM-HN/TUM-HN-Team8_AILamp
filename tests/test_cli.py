from pathlib import Path

from ailamp.cli import main


CONFIG_PATH = str(Path(__file__).resolve().parents[1] / "config/hardware.toml")
NANO_CONFIG_PATH = str(Path(__file__).resolve().parents[1] / "config/hardware.jetson-nano.toml")
ORIN_CONFIG_PATH = str(Path(__file__).resolve().parents[1] / "config/hardware.orin.toml")


def test_cli_static_hardware_check_passes(capsys):
    exit_code = main(["hardware-check"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "PASS controller.model: NVIDIA Jetson Nano Developer Kit 4GB" in output
    assert "PASS controller.mpn: 945-13450-0000-100" in output
    assert "PASS software.profile: NVIDIA JetPack 4.6.x / Jetson Linux R32.x, Ubuntu 18.04 based" in output
    assert "PASS power.jetson_supply: Jetson Nano 5V 4A DC barrel jack power supply" in output
    assert "PASS led.count: 64" in output
    assert "PASS bom.servo.quantity: 5" in output


def test_cli_hardware_check_failures_only_is_empty(capsys):
    exit_code = main(["hardware-check", "--failures-only"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output == ""


def test_cli_static_hardware_check_passes_for_jetson_nano_profile(capsys):
    exit_code = main(["--config", NANO_CONFIG_PATH, "hardware-check"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "PASS controller.model: NVIDIA Jetson Nano Developer Kit 4GB" in output
    assert "PASS controller.mpn: 945-13450-0000-100" in output
    assert "PASS camera.fps: 15" in output
    assert "PASS vision.backend: api_hybrid" in output


def test_cli_static_hardware_check_passes_for_orin_reference_profile(capsys):
    exit_code = main(["--config", ORIN_CONFIG_PATH, "hardware-check"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "PASS controller.model: NVIDIA Jetson Orin Nano Super Developer Kit" in output
    assert "PASS controller.mpn: 945-13766-0000-000" in output
    assert "PASS software.install_extra: hardware,voice" in output


def test_cli_runtime_check_reports_missing_openai_key_for_default_nano_profile(capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = main(["--config", CONFIG_PATH, "runtime-check"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "PASS runtime.profile: jetson-nano" in output
    assert "PASS runtime.target_os: NVIDIA JetPack 4.6.x / Jetson Linux R32.x, Ubuntu 18.04 based" in output
    assert "PASS runtime.recordings: idle,nod,scanning,shy,wake_up" in output
    assert "PASS runtime.firmware.pico" in output
    assert "PASS runtime.outputs_writable:" in output
    assert "FAIL runtime.openai_api_key: missing OPENAI_API_KEY" in output


def test_cli_runtime_check_offline_skips_openai_key(capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = main(["--config", CONFIG_PATH, "runtime-check", "--offline"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "PASS runtime.openai_api_key: offline mode; OPENAI_API_KEY not required" in output


def test_cli_runtime_check_reports_missing_openai_key_for_nano(capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = main(["--config", NANO_CONFIG_PATH, "runtime-check"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "PASS runtime.profile: jetson-nano" in output
    assert "FAIL runtime.openai_api_key: missing OPENAI_API_KEY" in output


def test_cli_runtime_check_include_voice_and_motor_runtime(capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    exit_code = main(
        [
            "--config",
            NANO_CONFIG_PATH,
            "runtime-check",
            "--include-voice",
            "--include-motor-runtime",
        ]
    )
    output = capsys.readouterr().out

    assert "PASS runtime.voice.disabled: voice/audio deliberately disabled in this profile" in output
    assert "runtime.motor_runtime.lelamp_follower" in output
    assert exit_code in {0, 1}


def test_cli_sim_demo_outputs_virtual_vision_events(capsys):
    exit_code = main(["sim-demo"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "event=no_person motion=scanning" in output
    assert "event=person_left motion=headshake" in output
    assert "event=person_close motion=shy" in output


def test_cli_sim_check_uses_runner_and_validates_scene(monkeypatch, capsys):
    calls = {}

    class FakeInfo:
        model_path = Path("simulation/ailamp_scene.xml")
        nq = 14
        nv = 13
        nu = 5
        joints = [
            "lamparm__base_elbow_freejoint",
            "2",
            "1",
            "3",
            "4",
            "5",
            "target_slide_x",
            "target_slide_y",
        ]
        actuators = ["2", "1", "3", "4", "5"]

    class FakeRunner:
        def __init__(self, model_path, lock_freejoint=True):
            calls["init"] = (str(model_path), lock_freejoint)

        def load(self):
            calls["load"] = True

        def info(self):
            calls["info"] = True
            return FakeInfo()

        def set_target_position(self, x_m, depth_m):
            calls["target"] = (x_m, depth_m)

        def replay_recording(self, name, recordings_dir, max_frames=None):
            calls.setdefault("recordings", []).append((name, max_frames))
            return 3

        def render(self, output_path, camera_name=None):
            calls["render"] = (str(output_path), camera_name)
            return Path(output_path)

    monkeypatch.setattr("ailamp.cli.MujocoRunner", FakeRunner)

    exit_code = main(["sim-check", "--render", "outputs/sim_check.png"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert calls["init"] == ("simulation/ailamp_scene.xml", True)
    assert calls["recordings"] == [
        ("wake_up", 5),
        ("idle", 5),
        ("nod", 5),
        ("scanning", 5),
        ("shy", 5),
        ("wake_up", 30),
    ]
    assert calls["load"] is True
    assert calls["target"] == (1.2, 3.8)
    assert calls["render"] == ("outputs/sim_check.png", "ailamp_overview_camera")
    assert "PASS sim.model:" in output
    assert "PASS sim.actuators: 2,1,3,4,5" in output
    assert "PASS sim.virtual_events: no_person,person_left,person_center,person_right,person_close,person_far" in output
    assert "PASS sim.adapter_visuals:" in output


def test_cli_sim_check_fails_on_wrong_actuator_mapping(monkeypatch, capsys):
    class FakeInfo:
        model_path = Path("simulation/ailamp_scene.xml")
        nq = 14
        nv = 13
        nu = 5
        joints = ["lamparm__base_elbow_freejoint", "target_slide_x", "target_slide_y"]
        actuators = ["1", "2", "3", "4", "5"]

    class FakeRunner:
        def __init__(self, model_path, lock_freejoint=True):
            pass

        def info(self):
            return FakeInfo()

        def replay_recording(self, name, recordings_dir, max_frames=None):
            return 5

    monkeypatch.setattr("ailamp.cli.MujocoRunner", FakeRunner)

    exit_code = main(["sim-check"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "FAIL sim.actuators: 1,2,3,4,5" in output


def test_cli_vision_loop_uses_runtime(monkeypatch, capsys):
    calls = {}

    class FakeResult:
        def format(self):
            return "frame=0 event=person_center motion=nod applied=True"

    class FakeRuntime:
        def __init__(self, config):
            calls["project"] = config.system.project_name

        def open(self, *, with_outputs):
            calls["open_with_outputs"] = with_outputs

        def run(self, *, max_frames, interval_s, apply_outputs):
            calls["run"] = (max_frames, interval_s, apply_outputs)
            yield FakeResult()

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr("ailamp.cli.VisionRuntime", FakeRuntime)

    exit_code = main(["vision-loop", "--frames", "2", "--interval", "0", "--with-outputs"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert calls == {
        "project": "AILamp",
        "open_with_outputs": True,
        "run": (2, 0.0, True),
        "closed": True,
    }
    assert "event=person_center" in output


def test_cli_vision_demo_uses_runtime_for_api_hybrid(monkeypatch, capsys):
    calls = {}

    class FakeResult:
        def format(self):
            return "frame=0 event=person_center motion=nod applied=False"

    class FakeRuntime:
        def __init__(self, config):
            calls["backend"] = config.vision.backend

        def open(self, *, with_outputs=False):
            calls["open_with_outputs"] = with_outputs

        def step(self):
            calls["stepped"] = True
            return FakeResult()

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr("ailamp.cli.VisionRuntime", FakeRuntime)

    exit_code = main(["--config", NANO_CONFIG_PATH, "vision-demo"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert calls == {
        "backend": "api_hybrid",
        "open_with_outputs": False,
        "stepped": True,
        "closed": True,
    }
    assert "event=person_center" in output


def test_cli_agent_tools_test_dry_run(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))

    exit_code = main(
        [
            "--config",
            CONFIG_PATH,
            "agent-tools-test",
            "--event",
            "person_close",
            "--apply",
            "--recording",
            "nod",
            "--color",
            "1",
            "2",
            "3",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "capabilities:" in output
    assert "vision: event=person_close" in output
    assert "decision: motion=track rgb=(255, 120, 150) joint_deltas=wrist_pitch:-2.50" in output
    assert "apply: applied event=person_close motion=track" in output
    assert "joint_deltas=wrist_pitch:-2.50" in output
    assert "recording: playing nod" in output
    assert "light: dry-run led solid rgb=(1, 2, 3)" in output


def test_cli_agent_tools_test_voice_focus_and_tracking(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))

    focus_code = main(["--config", CONFIG_PATH, "agent-tools-test", "--event", "person_right", "--offset", "0.6", "--request", "进入专注模式"])
    focus_output = capsys.readouterr().out
    track_code = main(["--config", CONFIG_PATH, "agent-tools-test", "--event", "person_right", "--offset", "0.6", "--request", "看着我并跟随我"])
    track_output = capsys.readouterr().out

    assert focus_code == 0
    assert "decision: motion=idle rgb=(255, 235, 190)" in focus_output
    assert "reason=voice:focus_mode" in focus_output
    assert track_code == 0
    assert "decision: motion=track" in track_output
    assert "joint_deltas=base_yaw:+2.40" in track_output
    assert "reason=voice:track" in track_output


def test_cli_agent_defaults_to_dry_run(monkeypatch):
    assert main(["--config", NANO_CONFIG_PATH, "agent"]) == 1


def test_cli_agent_with_outputs_uses_real_outputs(monkeypatch):
    assert main(["--config", NANO_CONFIG_PATH, "agent", "--with-outputs"]) == 1


def test_cli_web_control_help(capsys):
    try:
        main(["web-control", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out

    assert "--brain" in output
    assert "--vision" in output
    assert "--token-env" in output


def test_cli_web_control_requires_strong_token_for_lan(capsys, monkeypatch):
    monkeypatch.setenv("AILAMP_CONTROL_TOKEN", "short")

    exit_code = main(["web-control", "--host", "192.168.1.50"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "requires a strong token" in output


def test_cli_web_control_rejects_wildcard_host(capsys, monkeypatch):
    monkeypatch.setenv("AILAMP_CONTROL_TOKEN", "x" * 32)

    exit_code = main(["web-control", "--host", "0.0.0.0"])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "concrete host" in output


def test_cli_web_control_starts_runtime_scheduler_and_cleans_up(monkeypatch, capsys):
    calls = []

    class FakeRuntime:
        def open(self):
            calls.append("open")

        def start_scheduler(self):
            calls.append("start_scheduler")

        def close(self):
            calls.append("close")

    class FakeServer:
        runtime = FakeRuntime()
        server_address = ("127.0.0.1", 8766)

        def serve_forever(self):
            calls.append("serve_forever")
            raise KeyboardInterrupt

        def server_close(self):
            calls.append("server_close")

    def fake_create_server(address, config, **kwargs):
        calls.append(("create_server", address, kwargs["with_outputs"], kwargs["brain_enabled"], kwargs["vision_enabled"]))
        return FakeServer()

    monkeypatch.setattr("ailamp.web.create_server", fake_create_server)

    exit_code = main(["web-control", "--port", "8766", "--brain", "--vision"])
    output = capsys.readouterr().out

    assert exit_code == 130
    assert calls == [
        ("create_server", ("127.0.0.1", 8766), False, True, True),
        "open",
        "start_scheduler",
        "serve_forever",
        "close",
        "server_close",
    ]
    assert "http://127.0.0.1:8766" in output
