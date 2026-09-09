from __future__ import annotations

import base64
from dataclasses import replace
import json
import re
import time
from typing import Callable

from ailamp.models import VisionEvent, VisionEventType


SUPPORTED_API_EVENTS = {
    VisionEventType.PERSON_LEFT,
    VisionEventType.PERSON_CENTER,
    VisionEventType.PERSON_RIGHT,
    VisionEventType.PERSON_CLOSE,
    VisionEventType.PERSON_FAR,
    VisionEventType.PERSON_LEFT_SEAT,
    VisionEventType.GESTURE_LEFT,
    VisionEventType.GESTURE_RIGHT,
    VisionEventType.GESTURE_UP,
    VisionEventType.GESTURE_DOWN,
    VisionEventType.POSTURE_STUDYING,
    VisionEventType.LOOKING_AT_LAMP,
    VisionEventType.EXPRESSION_SMILE,
    VisionEventType.EXPRESSION_TIRED,
    VisionEventType.EXPRESSION_NEUTRAL,
    VisionEventType.NO_PERSON,
}


class APIVisionService:
    def __init__(
        self,
        *,
        client: object | None = None,
        model: str = "gpt-4.1-mini",
        interval_s: float = 1.0,
        confidence_threshold: float = 0.45,
        image_max_px: int = 512,
        timeout_s: float = 10.0,
        event_ttl_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.client = client
        self.model = model
        self.interval_s = interval_s
        self.confidence_threshold = confidence_threshold
        self.image_max_px = image_max_px
        self.timeout_s = timeout_s
        self.event_ttl_s = event_ttl_s
        self.clock = clock
        self._last_event: VisionEvent | None = None
        self._last_event_at = -1_000_000.0

    def load(self) -> None:
        if self.client is not None:
            return
        from openai import OpenAI  # type: ignore

        self.client = OpenAI()

    def detect_event(self, frame) -> VisionEvent:
        if frame is None:
            event = VisionEvent(VisionEventType.NO_PERSON, semantic_reason="no_frame")
            self._last_event = event
            self._last_event_at = self.clock()
            return event
        now = self.clock()
        if self._last_event is not None and now - self._last_event_at < self.interval_s:
            return replace(self._last_event, semantic_reason="cached_before_api_interval")
        try:
            event = self._request_event(frame)
        except Exception as exc:  # noqa: BLE001 - runtime should degrade safely on API/network errors.
            if self._last_event is not None and now - self._last_event_at <= self.event_ttl_s:
                return replace(self._last_event, semantic_reason=f"cached_after_api_error:{exc}")
            return VisionEvent(VisionEventType.NO_PERSON, semantic_reason=f"api_error:{exc}")
        self._last_event = event
        self._last_event_at = now
        return event

    def _request_event(self, frame) -> VisionEvent:
        if self.client is None:
            raise RuntimeError("APIVisionService client is not loaded")
        data_url = self._frame_to_data_url(frame)
        api_client = self.client.with_options(timeout=self.timeout_s) if hasattr(self.client, "with_options") else self.client
        response = api_client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": self._prompt()},
                        {"type": "input_image", "image_url": data_url, "detail": "low"},
                    ],
                }
            ],
        )
        return self._event_from_text(_response_text(response))

    def _event_from_text(self, text: str) -> VisionEvent:
        raw = json.loads(_json_object_text(text))
        raw_event_type = str(raw.get("event_type", VisionEventType.NO_PERSON.value))
        event_type = VisionEventType._value2member_map_.get(raw_event_type)
        if event_type is None or event_type not in SUPPORTED_API_EVENTS:
            return VisionEvent(VisionEventType.NO_PERSON, semantic_reason=f"unsupported_event_type:{raw_event_type}")
        confidence = float(raw.get("confidence", 0.0))
        if event_type != VisionEventType.NO_PERSON and confidence < self.confidence_threshold:
            return VisionEvent(VisionEventType.NO_PERSON, confidence=confidence, semantic_reason=f"low_confidence:{event_type.value}:{confidence:.2f}")
        return VisionEvent(
            event_type=event_type,
            confidence=confidence,
            normalized_offset=float(raw.get("normalized_offset", 0.0)),
            area_ratio=float(raw.get("area_ratio", 0.0)),
            semantic_reason=str(raw.get("reason", "")),
        )

    def _frame_to_data_url(self, frame) -> str:
        if isinstance(frame, bytes):
            jpeg = frame
        else:
            jpeg = _encode_frame_as_jpeg(frame, self.image_max_px)
        return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")

    def _prompt(self) -> str:
        events = ", ".join(event.value for event in sorted(SUPPORTED_API_EVENTS, key=lambda item: item.value))
        return (
            "Analyze this desk camera frame for AILamp. Return only JSON with keys "
            "event_type, confidence, normalized_offset, area_ratio, reason. "
            f"event_type must be one of: {events}. "
            "Use normalized_offset from -1 left to +1 right. Use area_ratio as the visible person bbox area divided by frame area."
        )


def _encode_frame_as_jpeg(frame, image_max_px: int) -> bytes:
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on hardware install.
        raise RuntimeError("opencv-python-headless is required for API vision frames") from exc

    image = frame
    height, width = image.shape[:2]
    max_side = max(width, height)
    if image_max_px > 0 and max_side > image_max_px:
        scale = image_max_px / max_side
        image = cv2.resize(image, (int(width * scale), int(height * scale)))
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    if not ok:
        raise RuntimeError("failed to encode camera frame as JPEG")
    return encoded.tobytes()


def _response_text(response: object) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text
    if isinstance(response, dict) and isinstance(response.get("output_text"), str):
        return response["output_text"]
    raise RuntimeError("OpenAI response did not include output_text")


def _json_object_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if match:
        return match.group(0)
    raise ValueError("OpenAI vision response was not JSON")
