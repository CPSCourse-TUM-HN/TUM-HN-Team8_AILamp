import builtins
import io
from pathlib import Path

import pytest

import ailamp.motor_cli as motor_cli
from ailamp.motor_cli import (
    DEFAULT_CALIBRATION_DIR,
    DEFAULT_CALIBRATION_SHA256,
    DEFAULT_RECORDINGS_DIR,
    MotorCliSession,
    build_parser,
    build_runtime,
    main,
    run_offline_preflight,
)


def test_real_snapshot_offline_preflight_reports_all_13_files_and_5488_frames():
    report = run_offline_preflight(
        recordings_dir=DEFAULT_RECORDINGS_DIR,
        calibration_dir=DEFAULT_CALIBRATION_DIR,
        expected_calibration_sha256=DEFAULT_CALIBRATION_SHA256,
        fps=30,
        max_step_units=4,
    )

    assert report.offline_only is True
    assert report.calibration_sha256 == DEFAULT_CALIBRATION_SHA256
    assert report.total_frames == 5488
    assert [recording.name for recording in report.recordings] == [
        "curious",
        "excited",
        "happy_wiggle",
        "headshake",
        "idle",
        "nod",
        "sad",
        "scanning",
        "shock",
        "shy",
        "test01",
        "test02",
        "wake_up",
    ]
    assert all(len(recording.sha256) == 64 for recording in report.recordings)
    assert all(recording.estimated_duration_seconds > 0 for recording in report.recordings)


def test_list_and_preflight_never_construct_a_motor_runtime():
    output = io.StringIO()

    def forbidden_factory(_args):
        raise AssertionError("offline commands must not construct a runtime")

    assert main(["list"], output=output, runtime_factory=forbidden_factory) == 0
    assert "test02" in output.getvalue()

    output = io.StringIO()
    assert main(["preflight"], output=output, runtime_factory=forbidden_factory) == 0
    rendered = output.getvalue()
    assert "13 段" in rendered
    assert "5488 帧" in rendered
    assert "仅离线" in rendered


def test_default_shell_uses_simulation_without_importing_lelamp(monkeypatch):
    real_import = builtins.__import__

    def reject_lelamp(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("lelamp"):
            raise AssertionError("default simulation must not import LeLamp hardware runtime")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", reject_lelamp)
    commands = iter(
        [
            "connect",
            "arm 300 20 250 CONFIRM",
            "release CONFIRM",
            "quit",
        ]
    )
    output = io.StringIO()

    result = main(
        ["shell"],
        input_fn=lambda _prompt: next(commands),
        output=output,
    )

    assert result == 0
    assert "模拟" in output.getvalue()


def test_home_path_defaults_to_simulation_and_hardware_requires_an_explicit_path(
    monkeypatch, tmp_path
):
    parser = build_parser()
    simulation_args = parser.parse_args(["shell"])
    simulation_runtime = build_runtime(simulation_args)
    hardware_home = tmp_path / "hardware-home.json"
    hardware_args = parser.parse_args(
        ["--hardware", "--home-path", str(hardware_home), "shell"]
    )

    assert simulation_runtime.home_path == motor_cli.DEFAULT_SIMULATION_HOME_PATH
    assert hardware_args.home_path == hardware_home
    assert hardware_args.home_path != simulation_runtime.home_path

    def forbidden_calibration_load(*_args, **_kwargs):
        raise AssertionError("missing hardware home must fail before backend setup")

    monkeypatch.setattr(motor_cli, "load_calibration_file", forbidden_calibration_load)
    missing_home_args = parser.parse_args(["--hardware", "shell"])
    with pytest.raises(ValueError, match="--home-path"):
        build_runtime(missing_home_args)


def test_parser_keeps_numeric_defaults_and_accepts_experiment_boundaries():
    defaults = build_parser().parse_args(["shell"])

    assert defaults.fps == 30.0
    assert defaults.max_step_units == 4.0
    assert defaults.feedback_tolerance == 2.0
    assert defaults.feedback_timeout == 3.0
    assert defaults.feedback_poll_interval == 0.05

    boundaries = build_parser().parse_args(
        [
            "--fps",
            "30",
            "--max-step-units",
            "4",
            "--feedback-tolerance",
            "0",
            "--feedback-timeout",
            "30",
            "--feedback-poll-interval",
            "1",
            "shell",
        ]
    )
    assert boundaries.feedback_tolerance == 0.0


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--fps", "nan"),
        ("--fps", "inf"),
        ("--fps", "30.01"),
        ("--fps", "0"),
        ("--max-step-units", "nan"),
        ("--max-step-units", "inf"),
        ("--max-step-units", "4.01"),
        ("--max-step-units", "0"),
        ("--feedback-tolerance", "nan"),
        ("--feedback-tolerance", "inf"),
        ("--feedback-tolerance", "5.01"),
        ("--feedback-tolerance", "-0.01"),
        ("--feedback-timeout", "nan"),
        ("--feedback-timeout", "inf"),
        ("--feedback-timeout", "30.01"),
        ("--feedback-timeout", "0"),
        ("--feedback-poll-interval", "nan"),
        ("--feedback-poll-interval", "inf"),
        ("--feedback-poll-interval", "1.01"),
        ("--feedback-poll-interval", "0"),
    ],
)
def test_parser_rejects_values_outside_experiment_boundaries(option, value):
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args([option, value, "shell"])

    assert exc_info.value.code == 2


def test_normal_quit_refuses_armed_runtime_but_leave_holding_warns(tmp_path):
    runtime = FakeRuntime(connected=True, armed=True)
    session = MotorCliSession(
        runtime,
        recordings_dir=tmp_path,
        calibration_dir=tmp_path,
        expected_calibration_sha256="8" * 64,
        fps=30,
        max_step_units=4,
        output=io.StringIO(),
    )

    assert session.execute("quit") is True
    assert runtime.close_calls == [(False,)]

    assert session.execute("quit leave-holding") is False
    assert runtime.close_calls[-1] == (True,)
    assert "不再监测" in session.output.getvalue()
    assert runtime.release_calls == 0


def test_eof_stops_then_closes_without_release(tmp_path):
    runtime = FakeRuntime(connected=True, armed=True)
    session = MotorCliSession(
        runtime,
        recordings_dir=tmp_path,
        calibration_dir=tmp_path,
        expected_calibration_sha256="8" * 64,
        fps=30,
        max_step_units=4,
        output=io.StringIO(),
    )

    result = session.run(input_fn=lambda _prompt: (_ for _ in ()).throw(EOFError()))

    assert result == 0
    assert runtime.shutdown_reasons == ["EOF"]
    assert runtime.release_calls == 0


def test_keyboard_interrupt_during_a_command_uses_signal_shutdown(tmp_path):
    runtime = InterruptingRuntime(connected=True, armed=True)
    session = MotorCliSession(
        runtime,
        recordings_dir=tmp_path,
        calibration_dir=tmp_path,
        expected_calibration_sha256="8" * 64,
        fps=30,
        max_step_units=4,
        output=io.StringIO(),
    )

    commands = iter(["connect"])
    result = session.run(input_fn=lambda _prompt: next(commands))

    assert result == 130
    assert runtime.shutdown_reasons == ["signal"]
    assert runtime.release_calls == 0


class FakeRuntime:
    def __init__(self, *, connected, armed):
        self.is_connected = connected
        self.is_armed = armed
        self.close_calls = []
        self.release_calls = 0
        self.shutdown_reasons = []

    def close(self, *, leave_holding=False):
        self.close_calls.append((leave_holding,))
        if self.is_armed and not leave_holding:
            raise RuntimeError("release or explicitly leave holding")
        if leave_holding:
            return "警告：退出后保持使能，但程序不再监测。"
        return ""

    def shutdown_for_eof_or_signal(self, reason):
        self.shutdown_reasons.append(reason)
        return f"{reason}: stop then close"


class InterruptingRuntime(FakeRuntime):
    def connect(self):
        raise KeyboardInterrupt
