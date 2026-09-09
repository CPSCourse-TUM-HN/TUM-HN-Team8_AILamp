from __future__ import annotations

import argparse
import hashlib
import json
import math
import shlex
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from ailamp.services.motor import JOINT_NAMES, JOINT_POSITION_KEYS, MotorService, RecordingStore
from ailamp.services.motor_backend import (
    JointCalibration,
    LeLampMotorBackend,
    load_calibration_file,
)
from ailamp.services.motor_runtime import DEFAULT_SCRIPT, MotorDemoRuntime


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_ROOT = (
    PROJECT_ROOT
    / "output"
    / "hardware_backups"
    / "LeLamp-demo-calibration-20260908-7QE2U0"
)
DEFAULT_CALIBRATION_DIR = DEFAULT_SNAPSHOT_ROOT
DEFAULT_RECORDINGS_DIR = DEFAULT_SNAPSHOT_ROOT / "nano_recordings"
DEFAULT_SIMULATION_HOME_PATH = (
    PROJECT_ROOT / "output" / "five_axis_demo_home.simulation.json"
)
# Backward-compatible name; the default now applies to simulation only.
DEFAULT_HOME_PATH = DEFAULT_SIMULATION_HOME_PATH
DEFAULT_CALIBRATION_SHA256 = (
    "8285b8c0d24239046dec3db33e305fecbec5a4be2f9c3a4c2e718ed068a943c1"
)
DEFAULT_HARDWARE_PORT = (
    "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B14033387-if00"
)


@dataclass(frozen=True)
class RecordingPreflight:
    name: str
    sha256: str
    source_frames: int
    estimated_schedule_frames: int
    source_duration_seconds: float
    estimated_duration_seconds: float


@dataclass(frozen=True)
class OfflinePreflightReport:
    calibration_sha256: str
    recordings: tuple[RecordingPreflight, ...]
    total_frames: int
    total_estimated_schedule_frames: int
    total_estimated_duration_seconds: float
    offline_only: bool = True


def run_offline_preflight(
    *,
    recordings_dir: str | Path,
    calibration_dir: str | Path,
    expected_calibration_sha256: str,
    fps: float,
    max_step_units: float,
) -> OfflinePreflightReport:
    """Validate calibration and every CSV without constructing or opening a bus."""
    fps_value = _positive_finite(fps, "fps")
    step_value = _positive_finite(max_step_units, "max_step_units")
    _, calibration_digest = load_calibration_file(
        calibration_dir,
        expected_calibration_sha256,
    )
    store = RecordingStore(recordings_dir)
    names = store.list_names()
    if not names:
        raise RuntimeError("No valid motor recordings were found")

    reports: list[RecordingPreflight] = []
    recordings_root = Path(recordings_dir)
    for name in names:
        rows = store.load(name)
        path = recordings_root / f"{name}.csv"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        estimated_frames = _estimated_schedule_frames(rows, step_value)
        reports.append(
            RecordingPreflight(
                name=name,
                sha256=digest,
                source_frames=len(rows),
                estimated_schedule_frames=estimated_frames,
                source_duration_seconds=len(rows) / fps_value,
                estimated_duration_seconds=estimated_frames / fps_value,
            )
        )

    total_frames = sum(item.source_frames for item in reports)
    total_schedule_frames = sum(item.estimated_schedule_frames for item in reports)
    return OfflinePreflightReport(
        calibration_sha256=calibration_digest,
        recordings=tuple(reports),
        total_frames=total_frames,
        total_estimated_schedule_frames=total_schedule_frames,
        total_estimated_duration_seconds=total_schedule_frames / fps_value,
    )


class MotorCliSession:
    def __init__(
        self,
        runtime: MotorDemoRuntime,
        *,
        recordings_dir: str | Path,
        calibration_dir: str | Path,
        expected_calibration_sha256: str,
        fps: float,
        max_step_units: float,
        output: TextIO = sys.stdout,
    ) -> None:
        self.runtime = runtime
        self.recordings_dir = Path(recordings_dir)
        self.calibration_dir = Path(calibration_dir)
        self.expected_calibration_sha256 = expected_calibration_sha256
        self.fps = fps
        self.max_step_units = max_step_units
        self.output = output

    def run(self, *, input_fn: Callable[[str], str] = input) -> int:
        self._print_help()
        while True:
            try:
                line = input_fn("ailamp-motor> ")
                if not self.execute(line):
                    return 0
            except (EOFError, StopIteration):
                self._print(self.runtime.shutdown_for_eof_or_signal("EOF"))
                return 0
            except KeyboardInterrupt:
                self._print("")
                self._print(self.runtime.shutdown_for_eof_or_signal("signal"))
                return 130

    def execute(self, line: str) -> bool:
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            self._print(f"错误：{exc}")
            return True
        if not parts:
            return True
        command, *args = parts
        try:
            if command == "help":
                self._print_help()
            elif command == "list":
                self._print("\n".join(RecordingStore(self.recordings_dir).list_names()))
            elif command == "preflight":
                self._print_preflight(
                    run_offline_preflight(
                        recordings_dir=self.recordings_dir,
                        calibration_dir=self.calibration_dir,
                        expected_calibration_sha256=self.expected_calibration_sha256,
                        fps=self.fps,
                        max_step_units=self.max_step_units,
                    )
                )
            elif command == "connect":
                _require_no_args(command, args)
                diagnostics = self.runtime.connect()
                self._print("已只读连接；尚未 arm。")
                if diagnostics is not None:
                    self._print(
                        "连接诊断："
                        + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)
                    )
            elif command == "arm":
                if len(args) != 4 or not _is_confirmation(args[3]):
                    raise ValueError(
                        "用法：arm <velocity> <acceleration> <torque_limit> CONFIRM"
                    )
                self.runtime.arm(
                    goal_velocity=_parse_int(args[0], "velocity"),
                    acceleration=_parse_int(args[1], "acceleration"),
                    torque_limit=_parse_int(args[2], "torque_limit"),
                    operator_confirmed=True,
                )
                self._print("已按当前实测姿态 arm 并保持；这不是承重验收。")
            elif command == "capture-home":
                if len(args) != 1 or not _is_confirmation(args[0]):
                    raise ValueError("用法：capture-home CONFIRM")
                home = self.runtime.capture_home(operator_confirmed=True)
                self._print(
                    "已保存 fresh home："
                    + json.dumps(home["normalized_positions"], ensure_ascii=False)
                )
            elif command == "play":
                if len(args) != 1:
                    raise ValueError("用法：play <recording>")
                self.runtime.play(args[0])
                self._print(f"已启动 {args[0]}；sent 与 feedback_reached 分开记录。")
            elif command == "home":
                _require_no_args(command, args)
                self.runtime.home()
                self._print("已启动 home。")
            elif command == "script":
                _require_no_args(command, args)
                self.runtime.play_script()
                self._print("已启动默认模拟触发剧本：" + " -> ".join(DEFAULT_SCRIPT))
            elif command == "stop":
                _require_no_args(command, args)
                held = self.runtime.stop()
                self._print(
                    "已 fresh stop-and-hold：" + json.dumps(held, ensure_ascii=False)
                )
            elif command == "status":
                _require_no_args(command, args)
                self._print(json.dumps(self.runtime.status(), ensure_ascii=False, sort_keys=True))
            elif command == "confirm":
                if not args:
                    raise ValueError("用法：confirm <task> [note]")
                self.runtime.confirm_visible(args[0], " ".join(args[1:]))
                self._print("已记录 operator_confirmed；不会改写反馈状态。")
            elif command == "wait":
                _require_no_args(command, args)
                self.runtime.wait_until_idle()
                self._print("当前任务已结束；请用 status 检查反馈或故障。")
            elif command == "release":
                if len(args) != 1 or not _is_confirmation(args[0]):
                    raise ValueError("用法：release CONFIRM")
                self.runtime.release(operator_confirmed=True)
                self._print("已确认支撑并 release。")
            elif command == "quit":
                if args not in ([], ["leave-holding"]):
                    raise ValueError("用法：quit [leave-holding]")
                warning = self.runtime.close(leave_holding=args == ["leave-holding"])
                if warning:
                    self._print(warning)
                return False
            else:
                raise ValueError(f"未知命令：{command}")
        except Exception as exc:
            self._print(f"错误：{exc}")
        return True

    def _print_preflight(self, report: OfflinePreflightReport) -> None:
        self._print(
            f"离线预检通过：{len(report.recordings)} 段，{report.total_frames} 帧；"
            "仅离线证据，不代表实体运动完成。"
        )
        self._print(f"calibration sha256={report.calibration_sha256}")
        for item in report.recordings:
            self._print(
                f"{item.name}: frames={item.source_frames}, sha256={item.sha256}, "
                f"source={item.source_duration_seconds:.3f}s, "
                f"interpolation_estimate={item.estimated_duration_seconds:.3f}s"
            )
        self._print(
            f"合计插值估算={report.total_estimated_duration_seconds:.3f}s；"
            "未计入连接时实测姿态到首帧的未知过渡。"
        )

    def _print_help(self) -> None:
        self._print(
            "命令：help/list/preflight/connect/arm/capture-home/play/home/script/"
            "stop/status/confirm/wait/release/quit"
        )

    def _print(self, message: str) -> None:
        print(message, file=self.output)


class _SimulatedBus:
    """In-memory bus used by default; it never opens a serial device."""

    def __init__(self, calibration: dict[str, JointCalibration]) -> None:
        self.is_calibrated = True
        self._connected = False
        self._positions = {
            joint: int(round((entry.range_min + entry.range_max) / 2))
            for joint, entry in calibration.items()
        }
        self._torque = {joint: 0 for joint in JOINT_NAMES}
        self._modes = {joint: 0 for joint in JOINT_NAMES}

    def connect(self) -> None:
        self._connected = True

    def sync_read(self, register: str, *, normalize: bool = False) -> dict[str, int]:
        if normalize:
            raise ValueError("simulation exposes raw register reads only")
        self._require_connected()
        if register == "Present_Position":
            return self._positions.copy()
        if register == "Torque_Enable":
            return self._torque.copy()
        if register == "Operating_Mode":
            return self._modes.copy()
        raise ValueError(f"unknown simulated register: {register}")

    def sync_write(
        self,
        register: str,
        values: dict[str, int],
        *,
        normalize: bool = False,
    ) -> None:
        if normalize:
            raise ValueError("simulation accepts raw register writes only")
        self._require_connected()
        if set(values) != set(JOINT_NAMES):
            raise ValueError("simulated writes require all five joints")
        if register == "Goal_Position":
            self._positions = values.copy()
        elif register == "Torque_Enable":
            self._torque = values.copy()
        elif register not in {"Goal_Velocity", "Acceleration", "Torque_Limit"}:
            raise ValueError(f"unknown simulated register: {register}")

    def disconnect(self, *, disable_torque: bool) -> None:
        if disable_torque:
            raise AssertionError("motor demo must never silently release torque")
        self._connected = False

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("simulated bus is not connected")


def add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the backend/runtime options shared by the CLI and the web page."""
    parser.add_argument("--hardware", action="store_true", help="显式选择真实硬件后端")
    parser.add_argument("--port", default=DEFAULT_HARDWARE_PORT)
    parser.add_argument("--recordings-dir", type=Path, default=DEFAULT_RECORDINGS_DIR)
    parser.add_argument("--calibration-dir", type=Path, default=DEFAULT_CALIBRATION_DIR)
    parser.add_argument(
        "--calibration-sha256", default=DEFAULT_CALIBRATION_SHA256
    )
    parser.add_argument("--home-path", type=Path, default=None)
    parser.add_argument(
        "--fps",
        type=_bounded_float_type("fps", upper=30.0),
        default=30.0,
    )
    parser.add_argument(
        "--max-step-units",
        type=_bounded_float_type("max-step-units", upper=4.0),
        default=4.0,
    )
    parser.add_argument(
        "--feedback-tolerance",
        type=_bounded_float_type(
            "feedback-tolerance", upper=5.0, allow_zero=True
        ),
        default=2.0,
    )
    parser.add_argument(
        "--feedback-timeout",
        type=_bounded_float_type("feedback-timeout", upper=30.0),
        default=3.0,
    )
    parser.add_argument(
        "--feedback-poll-interval",
        type=_bounded_float_type("feedback-poll-interval", upper=1.0),
        default=0.05,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m ailamp.motor_cli",
        description="AILamp 完整五轴电机实验 CLI（默认仅模拟）",
    )
    add_runtime_arguments(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="离线列出 CSV；不打开串口")
    commands.add_parser("preflight", help="离线校验校准与全部 CSV")
    commands.add_parser("shell", help="进入交互实验会话；默认模拟")
    return parser


def build_runtime(args: argparse.Namespace) -> MotorDemoRuntime:
    if args.hardware and args.home_path is None:
        raise ValueError("真实硬件模式必须显式提供 --home-path")
    home_path = args.home_path or DEFAULT_SIMULATION_HOME_PATH
    calibration, _ = load_calibration_file(
        args.calibration_dir,
        args.calibration_sha256,
    )
    bus = None if args.hardware else _SimulatedBus(calibration)
    port = args.port if args.hardware else "/tmp/ailamp-five-axis-simulation"
    backend = LeLampMotorBackend(
        port=port,
        lamp_id="lelamp",
        calibration_dir=args.calibration_dir,
        expected_calibration_sha256=args.calibration_sha256,
        bus=bus,
    )
    service = MotorService(
        port,
        "lelamp",
        args.recordings_dir,
        robot=backend,
        fps=args.fps,
        max_step_units=args.max_step_units,
    )
    return MotorDemoRuntime(
        service,
        calibration_sha256=args.calibration_sha256,
        home_path=home_path,
        feedback_tolerance=args.feedback_tolerance,
        feedback_timeout=args.feedback_timeout,
        feedback_poll_interval=args.feedback_poll_interval,
    )


def main(
    argv: list[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    runtime_factory: Callable[[argparse.Namespace], MotorDemoRuntime] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list":
            for name in RecordingStore(args.recordings_dir).list_names():
                print(name, file=output)
            return 0
        if args.command == "preflight":
            report = run_offline_preflight(
                recordings_dir=args.recordings_dir,
                calibration_dir=args.calibration_dir,
                expected_calibration_sha256=args.calibration_sha256,
                fps=args.fps,
                max_step_units=args.max_step_units,
            )
            session = MotorCliSession(
                _UnusedRuntime(),
                recordings_dir=args.recordings_dir,
                calibration_dir=args.calibration_dir,
                expected_calibration_sha256=args.calibration_sha256,
                fps=args.fps,
                max_step_units=args.max_step_units,
                output=output,
            )
            session._print_preflight(report)
            return 0

        factory = runtime_factory or build_runtime
        runtime = factory(args)
        print(
            "模式：真实硬件（connect 只读；arm 才写入）。"
            if args.hardware
            else "模式：离线模拟（不会打开硬件串口）。",
            file=output,
        )
        session = MotorCliSession(
            runtime,
            recordings_dir=args.recordings_dir,
            calibration_dir=args.calibration_dir,
            expected_calibration_sha256=args.calibration_sha256,
            fps=args.fps,
            max_step_units=args.max_step_units,
            output=output,
        )
        previous_term = None
        if signal.getsignal(signal.SIGTERM) is not None:
            previous_term = signal.getsignal(signal.SIGTERM)

            def _raise_keyboard_interrupt(_signum, _frame):
                raise KeyboardInterrupt

            signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
        try:
            return session.run(input_fn=input_fn)
        finally:
            if previous_term is not None:
                signal.signal(signal.SIGTERM, previous_term)
    except Exception as exc:
        print(f"错误：{exc}", file=output)
        return 2


class _UnusedRuntime:
    """Sentinel proving offline preflight has no runtime side effects."""


def _estimated_schedule_frames(
    rows: list[dict[str, float]],
    max_step_units: float,
) -> int:
    if not rows:
        return 0
    estimated = 1
    previous = rows[0]
    for current in rows[1:]:
        largest_delta = max(
            abs(current[key] - previous[key]) for key in JOINT_POSITION_KEYS
        )
        estimated += max(1, math.ceil(largest_delta / max_step_units))
        previous = current
    return estimated


def _positive_finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and greater than zero")
    return number


def _bounded_float_type(
    label: str,
    *,
    upper: float,
    allow_zero: bool = False,
) -> Callable[[str], float]:
    lower_bracket = "[" if allow_zero else "("

    def parse(value: str) -> float:
        try:
            number = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{label} 必须是有限数") from exc
        below_lower_bound = number < 0 if allow_zero else number <= 0
        if not math.isfinite(number) or below_lower_bound or number > upper:
            raise argparse.ArgumentTypeError(
                f"{label} 必须是有限数且位于 {lower_bracket}0, {upper:g}]"
            )
        return number

    return parse


def _parse_int(value: str, label: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{label} 必须是整数") from exc


def _is_confirmation(value: str) -> bool:
    return value in {"CONFIRM", "确认", "确认支撑"}


def _require_no_args(command: str, args: list[str]) -> None:
    if args:
        raise ValueError(f"{command} 不接受参数")


if __name__ == "__main__":
    raise SystemExit(main())
