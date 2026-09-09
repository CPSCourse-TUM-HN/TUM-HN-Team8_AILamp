# LeLamp No-Text MuJoCo Preview Implementation Plan

> For agentic workers: implement this approved, bounded video task; keep code work isolated from the lamp runtime. The user approved generating a trial video and explicitly removed all on-screen text. No further design approval is needed for this trial.

**Goal:** Produce an approximately 40–60 second, silent, no-text MP4 showing the original LeLamp model moving, for the user to inspect as a simulation backup.

**Architecture:** Read the original MJCF and CSV recordings without changing them. An independent export script performs deterministic, smooth kinematic pose replay using MuJoCo forward kinematics and offscreen rendering, anchoring the actual lamp base in world coordinates. Encode RGB frames through the already-installed FFmpeg; deliver the MP4 plus separate provenance/QA metadata, never labels inside the picture.

**Tech Stack:** Existing `mujoco_mcp/.venv/bin/python` (Python 3.11, MuJoCo 3.8, NumPy, Pillow), FFmpeg/FFprobe, standard-library unittest. No package installation, real devices, network services, API calls, or modifications to existing runtime/config/model/mesh/recording files.

## Approved visual specification

- Original model: `/Users/yugu/Documents/New project 4/LeLamp/simulation/scene.xml`.
- Output: `/Users/yugu/Documents/New project 4/AILamp/output/video/LeLamp_MuJoCo_Preview_NoText.mp4`.
- Target 1920x1080, 30 fps, H.264, yuv420p, faststart, no audio/subtitle/data streams.
- Clean studio-style ground/background and soft scene lighting; no LED emission, UI panels, title, captions, labels, watermarks, timestamps, logos, or narration.
- Keep original geometry, joints and recognizable original base/head/arms. Do not use the modified AILamp base.
- One stable three-quarter camera with enough margin for the whole motion; choose a visually useful side after checking preview frames. Smooth transitions between a subset of wake_up, scanning, nod, headshake, curious, and idle; avoid unsafe-looking folds or clipping. Exact trial length may vary with source recordings.
- This is MuJoCo-rendered kinematic animation, NOT validated dynamics, physical motor execution, vision recognition, or real OpenAI decision-making. Put this distinction in the separate README/metadata, not in the picture.

## Spatial and source ledger

- Preserve SI units and the original MJCF local frames, axes, geometry offsets and mesh scale. No model or mesh source edits.
- Named servo mapping: base_yaw=1, base_pitch=2, elbow_pitch=3, wrist_roll=4, wrist_pitch=5. Actual actuator array order is 2,1,3,4,5; resolve by name, not array assumptions.
- CSV positions are assumed LeRobot RANGE_M100_100 based on runtime `use_degrees=False`. The existing AILamp runner's degree conversion conflicts with that evidence and must not be silently reused. Metadata must state that mapping to MJCF limits is an unverified simulation convention, not real calibration.
- Original root body is `lamparm__base_elbow`, with a freejoint. The physical base mesh `lamp_base` belongs to downstream `scs215_v5`. Locking the root arm pose would let the base move.
- For each frame, compute the base world transform with the freejoint at identity, then solve the freejoint transform so the base remains at a fixed reference world pose. This is a rigid world transform only; preserve all joint-relative transforms. Test both translation and orientation invariance.
- Choose the reference base pose from the original zero-pose orientation, translate its center to an aesthetic scene center and its actual lowest mesh point to the ground plane. Do not assume the body frame itself is at the base bottom.
- Use kinematic forward replay (`mj_forward`), explicitly avoiding any assertion of torque/contact/dynamics correctness. Smooth source recordings in time and transitions; do not introduce motor commands or imports of AnimationService.

## Task 1: Tests before exporter implementation

- [x] Create `mujoco_mcp/tests/test_render_lelamp_video.py` with standard unittest.
- [x] First run must fail because the exporter is absent, then implement. Test finite CSV values/strict timestamps, named joint mapping, normalized endpoint conversion, smooth transition endpoints, actual-base world-transform invariance at multiple poses, no out-of-range joint targets, and source files unchanged.
- [x] Run with `PYTHONDONTWRITEBYTECODE=1 mujoco_mcp/.venv/bin/python -m unittest discover -s mujoco_mcp/tests -p test_render_lelamp_video.py -v`.

## Task 2: Isolated exporter

- [x] Create `mujoco_mcp/render_lelamp_video.py` with a reusable CLI. Support explicit output path/resolution, a lightweight preview-only/contact-sheet option, and a full export. Do not overwrite pre-existing deliverables without an explicit overwrite flag.
- [x] Load/validate inputs and resolve joint addresses by name; deterministic interpolation with bounded targets and smooth pose transitions.
- [x] Anchor actual base and configure in-memory render appearance only. Hide duplicate collision geoms (group 3); no text render calls, no emission effects.
- [x] Stream frames to FFmpeg instead of retaining a whole HD movie in RAM. Properly close renderer/FFmpeg and raise clear errors on failure. Set simulation time for metadata consistently even though replay is kinematic.
- [x] Before long rendering, output a small montage of selected poses with no text so the root agent can inspect composition, lamp identity, anchoring, intersections, and lighting. Provide a manifest of sample times/poses separately.

## Task 3: Render and verify

- [x] Pass the focused tests, then render 1080p30 MP4 using reviewed composition. Emit progress at reasonable intervals.
- [x] Create a separate `README.md` and `preview_metadata.json` under `AILamp/output/video/` recording source paths/hashes, exact command, duration/frame count/codec, recorded motions, normalization and anchoring conventions, maximum measured base drift, and honest limitations. Existing files there must be preserved.
- [x] Verify with FFprobe that the final file is H.264/yuv420p, 1920x1080, 30 fps, expected duration/frame count, video-only. Decode the whole MP4 with `ffmpeg -v error -i OUTPUT -f null -`.
- [x] Root agent reviews multiple actual encoded frames before delivery. Unit tests/encoding checks do not establish correct physical motion.

## Change boundary

Only this plan, the new standalone exporter/tests, and their new output/video artifacts may be created or edited. No git commits, resets, broad cleanup, updates to dependencies, external API access, credentials, Nano/SSH, cameras, serial ports, or unrelated workspace edits.
