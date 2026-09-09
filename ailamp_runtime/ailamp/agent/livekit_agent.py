from __future__ import annotations

from pathlib import Path

from ailamp.config import load_hardware_config
from ailamp.paths import resolve_project_path
from ailamp.services.behavior import BehaviorService
from ailamp.services.decision import AIDecision, DecisionService
from ailamp.services.led_serial import LEDSerialService
from ailamp.services.motor import JointDeltaCommand
from ailamp.services.motor import MotorService
from ailamp.services.motor import RecordingStore
from ailamp.services.vision_runtime import VisionSnapshot, VisionStateStore
from ailamp.models import VisionEvent, VisionEventType


class DryRunLEDService:
    def __init__(self):
        self.commands: list[tuple[int, int, int]] = []

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def solid(self, red: int, green: int, blue: int) -> str:
        self.commands.append((red, green, blue))
        return f"dry-run led solid rgb=({red}, {green}, {blue})"


class DryRunMotorService:
    def __init__(self):
        self.recordings: list[str] = []
        self.joint_deltas: list[tuple[JointDeltaCommand, ...]] = []

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def play(self, recording_name: str) -> None:
        self.recordings.append(recording_name)

    def apply_joint_deltas(self, deltas):
        self.joint_deltas.append(tuple(deltas))
        return {}


class AILampToolbox:
    def __init__(self, config_path: str, *, led_service=None, motor_service=None):
        self.config = load_hardware_config(config_path)
        self.behavior = BehaviorService()
        self.decision = DecisionService(behavior=self.behavior)
        self.last_event = VisionEvent(VisionEventType.NO_PERSON)
        self.vision_state = VisionStateStore(self.config.runtime.vision_state_file)
        self.recordings = RecordingStore(self._resolve_recordings_dir(config_path))
        self.led = led_service or LEDSerialService(self.config.led.port, self.config.led.count, self.config.led.baudrate)
        self.motors = motor_service or MotorService(
            self.config.motors.port,
            self.config.motors.lamp_id,
            self.recordings.recordings_dir,
            fps=self.config.motors.fps,
        )
        self._outputs_connected = False

    def _resolve_recordings_dir(self, config_path: str) -> Path:
        recordings_dir = Path(self.config.simulation.recordings_dir)
        if recordings_dir.is_absolute():
            return recordings_dir
        config_file = Path(config_path)
        if config_file.is_absolute() and config_file.parent.name == "config":
            return config_file.parent.parent / recordings_dir
        return resolve_project_path(recordings_dir)

    def connect_outputs(self) -> None:
        if self._outputs_connected:
            return
        self.led.connect()
        self.motors.connect()
        self._outputs_connected = True

    def current_snapshot(self) -> VisionSnapshot | None:
        snapshot = self.vision_state.read()
        if snapshot is not None:
            self.last_event = snapshot.event
        return snapshot

    def current_vision_event(self) -> VisionEvent:
        snapshot = self.current_snapshot()
        if snapshot is not None:
            return snapshot.event
        return self.last_event

    def current_vision_state(self) -> str:
        snapshot = self.current_snapshot()
        if snapshot is None:
            return self.last_event.event_type.value
        action = snapshot.action
        event = snapshot.event
        return (
            f"event={event.event_type.value} confidence={event.confidence:.2f} "
            f"motion={action.motion} rgb={action.rgb} updated_at={snapshot.updated_at}"
        )

    def motion_for_current_vision(self) -> tuple[str, tuple[int, int, int]]:
        decision = self.decide_response()
        return decision.motion, decision.rgb

    def decide_response(self, user_text: str | None = None) -> AIDecision:
        snapshot = self.current_snapshot()
        if snapshot is not None and snapshot.decision is not None and not user_text:
            return snapshot.decision
        return self.decision.decide(self.current_vision_event(), user_text=user_text)

    def describe_capabilities(self) -> str:
        return (
            "tools=get_vision_state,decide_response,suggest_motion_for_vision,apply_behavior_for_current_vision,"
            "list_recordings,play_recording,set_light "
            f"recordings={','.join(self.recordings.list_names())}"
        )

    def list_recordings(self) -> str:
        return ",".join(self.recordings.list_names())

    def apply_behavior_for_current_vision(self, user_text: str | None = None) -> str:
        self.connect_outputs()
        decision = self.decide_response(user_text=user_text)
        if decision.joint_deltas:
            self.motors.apply_joint_deltas(decision.joint_deltas)
            self.led.solid(*decision.rgb)
        else:
            self.motors.play(decision.motion)
            self.led.solid(*decision.rgb)
        return (
            f"applied event={decision.event.event_type.value} motion={decision.motion} "
            f"rgb={decision.rgb} joint_deltas={_format_joint_deltas(decision.joint_deltas)}"
        )

    def play_recording(self, recording_name: str) -> str:
        self.connect_outputs()
        self.motors.play(recording_name)
        return f"playing {recording_name}"

    def set_light(self, red: int, green: int, blue: int) -> str:
        self.connect_outputs()
        return self.led.solid(red, green, blue)


def run_agent(config_path: str, *, with_outputs: bool = False) -> None:
    # Import lazily so non-voice commands do not require LiveKit/OpenAI deps.
    from dotenv import load_dotenv  # type: ignore
    from livekit import agents  # type: ignore

    load_dotenv()
    if with_outputs:
        toolbox = AILampToolbox(config_path)
    else:
        toolbox = AILampToolbox(
            config_path,
            led_service=DryRunLEDService(),
            motor_service=DryRunMotorService(),
        )

    async def entrypoint(ctx):  # pragma: no cover - requires LiveKit runtime
        from livekit.agents import Agent, AgentSession, RoomInputOptions, function_tool  # type: ignore
        from livekit.plugins import noise_cancellation, openai  # type: ignore

        class AILampAgent(Agent):
            def __init__(self):
                super().__init__(
                    instructions=(
                        "You are AILamp, an interactive robotic desk lamp. "
                        "Reply in the user's language, Chinese or English, and keep responses concise. "
                        "Inspect vision state when the user asks what you see. "
                        "Use motion and light tools for physical responses, and prefer the mapped "
                        "vision behavior when the user asks the lamp to react to a person. "
                        "If tools report dry-run output, say the action was simulated rather than physically applied."
                    )
                )

            @function_tool
            async def describe_capabilities(self) -> str:
                return toolbox.describe_capabilities()

            @function_tool
            async def get_vision_state(self) -> str:
                return toolbox.current_vision_state()

            @function_tool
            async def decide_response(self, user_request: str = "") -> str:
                decision = toolbox.decide_response(user_request)
                return (
                    f"motion={decision.motion} rgb={decision.rgb} "
                    f"joint_deltas={_format_joint_deltas(decision.joint_deltas)} reason={decision.reason}"
                )

            @function_tool
            async def suggest_motion_for_vision(self) -> str:
                motion, rgb = toolbox.motion_for_current_vision()
                return f"motion={motion} rgb={rgb}"

            @function_tool
            async def apply_behavior_for_current_vision(self, user_request: str = "") -> str:
                return toolbox.apply_behavior_for_current_vision(user_request)

            @function_tool
            async def list_recordings(self) -> str:
                return toolbox.list_recordings()

            @function_tool
            async def play_recording(self, recording_name: str) -> str:
                return toolbox.play_recording(recording_name)

            @function_tool
            async def set_light(self, red: int, green: int, blue: int) -> str:
                return toolbox.set_light(red, green, blue)

        session = AgentSession(llm=openai.realtime.RealtimeModel(voice="ballad"))
        await session.start(
            room=ctx.room,
            agent=AILampAgent(),
            room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVC()),
        )

    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint, num_idle_processes=1))


def _format_joint_deltas(deltas: tuple[JointDeltaCommand, ...]) -> str:
    if not deltas:
        return "none"
    return ",".join(f"{command.joint}:{command.delta_deg:+.2f}" for command in deltas)
