# TEAM8 AiLamp — demonstration videos

Two clips are provided. The combined demo is the one shown during the presentation.

## 1. Combined demo — physical lamp and simulation

[Watch or download the video](TEAM8_AiLamp_Combined_Demo.mp4)

- Title: **AILamp | Combined Demo**
- Duration: **2 min 18 s** (138.1 seconds)
- Format: **MP4 / H.264, 1280 × 720, 30 fps**
- Audio: **AAC**
- Content: the assembled physical lamp first, then the simulation animation.

This clip stages a voice-triggered interaction scenario. The actual motor actions were
triggered from the control console, not by speech recognition. Vision and voice prototypes
exist in the wider repository but are not connected to this execution path.

## 2. Expressive motion sequence

[Watch or download the video](TEAM8_AiLamp_Expressive_Motion_EN.mp4)

[Download the editable English subtitles (SRT)](TEAM8_AiLamp_Expressive_Motion_EN.srt)

- Title: **TEAM8 AiLamp | Expressive Motion**
- Duration: **45.9 seconds**
- Format: **MP4 / H.264, 1920 × 1080, 30 fps**
- Captions: **English, burned into the video**
- Audio: **none**
- View: a fixed main camera, with the complete lamp and base kept in frame.

Motion sequence: Wake-up → Curiosity → Nod → Scanning → Headshake → Idle.

The captions explain the expressive meaning of each gesture. This video covers
the motion sequence; live sensor input and AI-triggered control are not demonstrated.

## How the animation was produced

The simulation animation is a MuJoCo forward-kinematic render, not a physics simulation and not a
recording of the physical lamp. Its provenance is recorded here:

- [`preview_metadata.json`](preview_metadata.json) — render parameters, source digests, joint mapping
  and the truthful-scope statement. Absolute paths from the authoring machine were replaced with
  `<project-root>/`; nothing else was changed.
- [`scripts/render_lelamp_video.py`](../../scripts/render_lelamp_video.py) — the renderer that
  produced it.
