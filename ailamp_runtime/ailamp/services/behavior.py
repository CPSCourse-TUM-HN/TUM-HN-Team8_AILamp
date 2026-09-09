from __future__ import annotations

from ailamp.models import BehaviorAction, VisionEvent, VisionEventType


DEFAULT_BEHAVIOR_MAP: dict[VisionEventType, tuple[str, tuple[int, int, int]]] = {
    VisionEventType.NO_PERSON: ("scanning", (30, 30, 80)),
    VisionEventType.PERSON_LEFT: ("headshake", (80, 120, 255)),
    VisionEventType.PERSON_CENTER: ("nod", (255, 180, 80)),
    VisionEventType.PERSON_RIGHT: ("scanning", (80, 120, 255)),
    VisionEventType.PERSON_CLOSE: ("shy", (255, 80, 120)),
    VisionEventType.PERSON_FAR: ("curious", (80, 255, 160)),
    VisionEventType.PERSON_LEFT_SEAT: ("idle", (30, 30, 80)),
    VisionEventType.GESTURE_LEFT: ("headshake", (90, 150, 255)),
    VisionEventType.GESTURE_RIGHT: ("scanning", (90, 150, 255)),
    VisionEventType.GESTURE_UP: ("curious", (180, 220, 255)),
    VisionEventType.GESTURE_DOWN: ("idle", (180, 220, 255)),
    VisionEventType.POSTURE_STUDYING: ("idle", (255, 235, 190)),
    VisionEventType.LOOKING_AT_LAMP: ("nod", (255, 210, 130)),
    VisionEventType.EXPRESSION_SMILE: ("happy_wiggle", (255, 210, 130)),
    VisionEventType.EXPRESSION_TIRED: ("idle", (255, 235, 190)),
    VisionEventType.EXPRESSION_NEUTRAL: ("idle", (180, 220, 255)),
}


class BehaviorService:
    def __init__(self, behavior_map: dict[VisionEventType, tuple[str, tuple[int, int, int]]] | None = None):
        self.behavior_map = behavior_map or DEFAULT_BEHAVIOR_MAP

    def decide(self, event: VisionEvent) -> BehaviorAction:
        motion, rgb = self.behavior_map[event.event_type]
        return BehaviorAction(event=event, motion=motion, rgb=rgb)

    def apply(self, event: VisionEvent, motor_service, led_service) -> BehaviorAction:
        action = self.decide(event)
        motor_service.play(action.motion)
        led_service.solid(*action.rgb)
        return action
