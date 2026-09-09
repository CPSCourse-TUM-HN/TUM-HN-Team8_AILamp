from pathlib import Path

import pytest

from ailamp.agent.livekit_agent import AILampToolbox
from ailamp.config import load_hardware_config
from ailamp.models import BoundingBox, VisionEvent, VisionEventType
from ailamp.services.behavior import BehaviorService
from ailamp.services.pose_gesture import PoseKeypoints
from ailamp.services.vision_runtime import VisionRuntime, VisionSnapshot, VisionStateStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config/hardware.orin.toml"
NANO_CONFIG_PATH = PROJECT_ROOT / "config/hardware.jetson-nano.toml"


class FakeCamera:
    def __init__(self, frames):
        self.frames = list(frames)
        self.closed = False

    def open(self):
        pass

    def read(self):
        if not self.frames:
            return None
        return self.frames.pop(0)

    def close(self):
        self.closed = True


class FakeDetector:
    def __init__(self, boxes):
        self.boxes = list(boxes)
        self.loaded = False

    def load(self):
        self.loaded = True

    def detect_person(self, frame):
        if not self.boxes:
            return None
        return self.boxes.pop(0)


class FakePoseDetector:
    def __init__(self, poses):
        self.poses = list(poses)
        self.loaded = False

    def load(self):
        self.loaded = True

    def detect_pose(self, frame):
        if not self.poses:
            return None
        return self.poses.pop(0)


class FakeAPIVision:
    def __init__(self, events):
        self.events = list(events)
        self.loaded = False

    def load(self):
        self.loaded = True

    def detect_event(self, frame):
        if not self.events:
            return VisionEvent(VisionEventType.NO_PERSON)
        return self.events.pop(0)


class FakeLed:
    def __init__(self):
        self.colors = []
        self.closed = False

    def connect(self):
        pass

    def solid(self, red, green, blue):
        self.colors.append((red, green, blue))
        return "OK"

    def close(self):
        self.closed = True


class FakeMotor:
    def __init__(self):
        self.recordings = []
        self.joint_deltas = []
        self.closed = False

    def connect(self):
        pass

    def play(self, recording_name):
        self.recordings.append(recording_name)

    def apply_joint_deltas(self, deltas):
        self.joint_deltas.append(tuple(deltas))
        return {}

    def close(self):
        self.closed = True


class FailingCloseMotor(FakeMotor):
    def close(self):
        self.closed = True
        raise RuntimeError("motor close failed")


def config():
    return load_hardware_config(CONFIG_PATH)


def nano_config():
    return load_hardware_config(NANO_CONFIG_PATH)


def test_vision_runtime_writes_state_and_maps_action(tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))
    runtime = VisionRuntime(
        config(),
        camera=FakeCamera([object()]),
        detector=FakeDetector([BoundingBox(270, 120, 100, 220, 0.91)]),
    )

    result = runtime.step()
    snapshot = VisionStateStore("outputs/vision_state.json").read()

    assert result.snapshot.event.event_type == VisionEventType.PERSON_CENTER
    assert result.snapshot.action.motion == "nod"
    assert not result.applied
    assert snapshot is not None
    assert snapshot.event.event_type == VisionEventType.PERSON_CENTER
    assert snapshot.action.rgb == (255, 180, 80)
    assert snapshot.decision is not None
    assert snapshot.decision.reason == "behavior_map:person_center"


def test_vision_runtime_applies_outputs_with_event_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))
    led = FakeLed()
    motor = FakeMotor()
    times = iter([0.0, 0.1, 2.0])
    runtime = VisionRuntime(
        config(),
        camera=FakeCamera([object(), object(), object()]),
        detector=FakeDetector(
            [
                BoundingBox(20, 120, 100, 220, 0.9),
                BoundingBox(20, 120, 100, 220, 0.9),
                BoundingBox(20, 120, 100, 220, 0.9),
            ]
        ),
        led_service=led,
        motor_service=motor,
        clock=lambda: next(times),
    )

    assert runtime.step(apply_outputs=True).applied
    assert not runtime.step(apply_outputs=True).applied
    assert runtime.step(apply_outputs=True).applied
    assert motor.recordings == []
    assert len(motor.joint_deltas) == 2
    assert motor.joint_deltas[0][0].joint == "base_yaw"
    assert motor.joint_deltas[0][0].delta_deg < 0
    assert led.colors == [(90, 150, 255), (90, 150, 255)]


def test_vision_runtime_applies_global_output_cooldown_for_alternating_events(tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))
    led = FakeLed()
    motor = FakeMotor()
    times = iter([0.0, 0.1])
    runtime = VisionRuntime(
        config(),
        camera=FakeCamera([object(), object()]),
        detector=FakeDetector(
            [
                BoundingBox(20, 120, 100, 220, 0.9),
                BoundingBox(520, 120, 100, 220, 0.9),
            ]
        ),
        led_service=led,
        motor_service=motor,
        clock=lambda: next(times),
    )

    assert runtime.step(apply_outputs=True).applied
    assert runtime.step(apply_outputs=True).applied is False
    assert len(motor.joint_deltas) == 1
    assert len(led.colors) == 1


def test_vision_runtime_prefers_pose_gesture_over_center_position(tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))
    runtime = VisionRuntime(
        config(),
        camera=FakeCamera([object()]),
        detector=FakeDetector([BoundingBox(270, 120, 100, 220, 0.91)]),
        pose_detector=FakePoseDetector(
            [
                PoseKeypoints.from_named(
                    (640, 480),
                    nose=(320, 150),
                    left_shoulder=(270, 240),
                    right_shoulder=(370, 240),
                    right_wrist=(520, 170),
                )
            ]
        ),
    )

    result = runtime.step()

    assert result.snapshot.event.event_type == VisionEventType.GESTURE_RIGHT
    assert result.snapshot.action.motion == "track"
    assert result.snapshot.decision.joint_deltas[0].joint == "base_yaw"


def test_vision_runtime_turns_departure_into_idle(tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))
    runtime = VisionRuntime(
        config(),
        camera=FakeCamera([object(), object()]),
        detector=FakeDetector([BoundingBox(270, 120, 100, 220, 0.91), None]),
    )

    assert runtime.step().snapshot.event.event_type == VisionEventType.PERSON_CENTER
    result = runtime.step()

    assert result.snapshot.event.event_type == VisionEventType.PERSON_LEFT_SEAT
    assert result.snapshot.action.motion == "idle"


def test_vision_runtime_uses_api_hybrid_backend_without_local_yolo(tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))
    api = FakeAPIVision([VisionEvent(VisionEventType.POSTURE_STUDYING, confidence=0.86)])
    detector = FakeDetector([BoundingBox(20, 120, 100, 220, 0.9)])
    runtime = VisionRuntime(
        nano_config(),
        camera=FakeCamera([b"jpeg"]),
        detector=detector,
        api_vision=api,
    )

    runtime.open()
    result = runtime.step()

    assert api.loaded
    assert detector.loaded is False
    assert result.snapshot.event.event_type == VisionEventType.POSTURE_STUDYING
    assert result.snapshot.action.motion == "idle"
    assert result.snapshot.action.rgb == (255, 235, 190)


def test_vision_runtime_does_not_convert_api_error_to_left_seat(tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))
    api = FakeAPIVision(
        [
            VisionEvent(VisionEventType.PERSON_CENTER, confidence=0.86),
            VisionEvent(VisionEventType.NO_PERSON, semantic_reason="api_error:network down"),
        ]
    )
    runtime = VisionRuntime(
        nano_config(),
        camera=FakeCamera([b"jpeg", b"jpeg"]),
        api_vision=api,
    )

    assert runtime.step().snapshot.event.event_type == VisionEventType.PERSON_CENTER
    result = runtime.step()

    assert result.snapshot.event.event_type == VisionEventType.NO_PERSON
    assert result.snapshot.event.semantic_reason == "api_error:network down"


def test_vision_runtime_does_not_apply_outputs_on_api_error_or_no_frame(tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))
    led = FakeLed()
    motor = FakeMotor()
    api = FakeAPIVision(
        [
            VisionEvent(VisionEventType.NO_PERSON, semantic_reason="api_error:timeout"),
            VisionEvent(VisionEventType.NO_PERSON, semantic_reason="no_frame"),
        ]
    )
    runtime = VisionRuntime(
        nano_config(),
        camera=FakeCamera([b"jpeg", None]),
        api_vision=api,
        led_service=led,
        motor_service=motor,
    )

    first = runtime.step(apply_outputs=True)
    second = runtime.step(apply_outputs=True)

    assert first.applied is False
    assert second.applied is False
    assert led.colors == []
    assert motor.recordings == []
    assert motor.joint_deltas == []


def test_local_vision_runtime_no_frame_has_semantic_reason_and_no_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))
    led = FakeLed()
    motor = FakeMotor()
    runtime = VisionRuntime(
        config(),
        camera=FakeCamera([None]),
        detector=FakeDetector([]),
        led_service=led,
        motor_service=motor,
    )

    result = runtime.step(apply_outputs=True)

    assert result.snapshot.event.event_type == VisionEventType.NO_PERSON
    assert result.snapshot.event.semantic_reason == "no_frame"
    assert result.applied is False
    assert led.colors == []
    assert motor.recordings == []
    assert motor.joint_deltas == []


def test_vision_runtime_close_attempts_all_resources_before_reporting_failure():
    camera = FakeCamera([])
    led = FakeLed()
    motor = FailingCloseMotor()
    runtime = VisionRuntime(
        config(),
        camera=camera,
        detector=FakeDetector([]),
        led_service=led,
        motor_service=motor,
    )

    with pytest.raises(RuntimeError, match="motor close failed"):
        runtime.close()

    assert motor.closed
    assert led.closed
    assert camera.closed


def test_agent_toolbox_reads_shared_vision_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AILAMP_PROJECT_ROOT", str(tmp_path))
    event = VisionEvent(VisionEventType.PERSON_CLOSE, confidence=0.88)
    action = BehaviorService().decide(event)
    store = VisionStateStore("outputs/vision_state.json")
    store.write(VisionSnapshot(event=event, action=action, updated_at="2026-05-08T09:00:00+00:00", frame_index=7))

    toolbox = AILampToolbox(str(CONFIG_PATH))
    toolbox.led = FakeLed()
    toolbox.motors = FakeMotor()
    toolbox._outputs_connected = True

    assert "apply_behavior_for_current_vision" in toolbox.describe_capabilities()
    assert "nod" in toolbox.list_recordings()
    assert "event=person_close" in toolbox.current_vision_state()
    assert toolbox.motion_for_current_vision() == ("track", (255, 120, 150))
    assert "applied event=person_close motion=track" in toolbox.apply_behavior_for_current_vision()
    assert toolbox.motors.recordings == []
    assert len(toolbox.motors.joint_deltas) == 1
    assert toolbox.motors.joint_deltas[0][0].joint == "wrist_pitch"
    assert toolbox.led.colors == [(255, 120, 150)]
