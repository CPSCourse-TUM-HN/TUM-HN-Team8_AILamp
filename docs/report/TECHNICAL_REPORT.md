# Interactive Robot Lamp — Technical Report

**Course:** Embedded Systems, Cyber-Physical Systems and Robotics (INHN0018)
**Team:** Group 8, TUM Campus Heilbronn, Summer Semester 2026
**Date:** 09.09.2026
**Repository:** <https://github.com/CPSCourse-TUM-HN/TUM-HN-Team8_AILamp>

> Historical planning outline, superseded by the completed TEAM8 AiLamp technical report:
> [PDF](TEAM8_AiLamp_Technical_Report.pdf), [Word](TEAM8_AiLamp_Technical_Report.docx),
> [full Markdown](TEAM8_AiLamp_Technical_Report.md). The TODOs below belong to this earlier outline.

---

## Abstract
*(TODO — 150–200 words: what the lamp does, what we added on top of LeLamp, what we measured.)*

## 1. Introduction
### 1.1 Motivation
A desk lamp is a device everyone already has on the desk, which makes it a good carrier for an
embedded cyber-physical system: it must sense its environment, decide, and act in the physical world
under real-time and safety constraints, while staying small, quiet and cheap.

### 1.2 Goal
Build a lamp that (a) perceives whether a person is present, where they are and roughly what they are
doing, (b) reacts with expressive motion and light, and (c) can be addressed by voice — running
entirely on an embedded Jetson platform.

### 1.3 Relation to the course
The system exercises the core CPS topics of the lecture: continuous and discrete dynamics (servo
control vs. event-driven behaviour), composition of state machines (perception, behaviour, voice),
sensors and actuators, input/output, and safety/limit reasoning on the actuation path.

## 2. Related work and starting point
The mechanical platform and base motion runtime come from the open-source **LeLamp** project by
Human Computer Lab (GPL-3.0), itself inspired by Apple's *ELEGNT* work on expressive robot motion.
LeLamp provides a 5-DOF arm (Feetech ST3215 servos), the printable structure, an MJCF/URDF model and
a Python runtime for motor control, LED and recorded motion playback.
See `NOTICE.md` for the exact upstream files and commits we reuse.

**What was missing for our scenario** — and therefore what this project adds — is summarised in
Section 4.

## 3. System overview
### 3.1 Architecture
*(TODO — insert the architecture figure; `docs/media/` has renders that can be reused.)*

```
Camera ──▶ Vision layer ──▶ vision_state.json ──▶ Behaviour map ──▶ Motion service ──▶ ST3215 servos
                                   │                                 └──▶ LED service ──▶ Pico WH ──▶ NeoMatrix
Microphone ──▶ Voice agent (LiveKit / OpenAI Realtime) ──▶ Agent tool layer ──┘
```

### 3.2 Hardware
Two controller profiles, selected purely by configuration file:

| | Profile A | Profile B |
|---|---|---|
| Config | `config/hardware.toml` | `config/hardware.jetson-nano.toml` |
| Compute | Jetson Orin Nano Super | Jetson Nano 4 GB |
| Perception | local YOLO person + pose | low-rate frames to a cloud vision API, cached events |
| Simulation on device | yes | no |

Common hardware: 5× ST3215 servos + Waveshare servo driver, two MEAN WELL supplies, Raspberry Pi
Pico WH + TXS0108E level shifter + NeoMatrix, Arducam UB0234 camera, Seeed ReSpeaker XVF3800 mic
array, 4 Ω / 5 W speaker, emergency switch. Full BOM: `docs/hardware/AILamp_Hardware_BOM_Jetson_Nano.pdf`
and the `[hardware_bom]` section of the active config.

### 3.3 Mechanical design
The upstream lamp base has no room for a Jetson. We generated a replacement base shell and cover that
keep the original outer proportions and the arm-origin relationship, with a hidden electronics cavity,
ventilation and an I/O window, plus a Jetson tray, a side deck and cable clips
(`3D/AILamp_Adapters/`, print-ready in `3D/print_ready/`). The parts are produced by
`scripts/generate_ailamp_adapters.py`; tests assert that the checked-in files match a fresh generation
and that the meshes are closed 2-manifold solids.
*(TODO — photo of the printed base, print settings, material, print time.)*

### 3.4 Software
Python package `ailamp_runtime/ailamp/` with services for motors, LED, camera, audio, vision and
behaviour, a CLI (`ailamp …`) for every subsystem, and a LiveKit agent that exposes the decision layer
as callable tools. Firmware for the Pico WH LED controller is in `firmware/pico_led_controller/`.

## 4. Our contribution over upstream LeLamp
1. Jetson platform bring-up with two interchangeable hardware profiles.
2. Redesigned, electronics-hiding lamp base (generated, testable, print-ready).
3. Perception → behaviour mapping driven by configuration rather than code.
4. Voice + vision fusion in a single decision layer with dry-run and hardware modes.
5. Pico WH LED controller firmware and serial protocol.
6. Peripheral integration (Arducam UB0234, ReSpeaker XVF3800).
7. MuJoCo scene for the modified base, enabling motion development without hardware.
8. Test suite, local verification script and CI workflow.
9. Bilingual build documentation.

## 5. Methodology
### 5.1 Perception
*(TODO — model, input resolution, frame rate, which events are derived and how.)*

### 5.2 From events to behaviour
Events are mapped to a motion recording and an LED colour through `[behavior_map]` in the config; a
cooldown prevents the same event from re-triggering continuously.
*(TODO — table of event → motion → colour.)*

### 5.3 Motion and safety
Recorded joint trajectories are replayed on the ST3215 servos; the digital twin in `simulation/` is
used to validate a motion before it touches hardware. Joint limits and per-step deltas bound the
commanded motion.
*(TODO — state the actual limits used and how they were determined.)*

### 5.4 Voice interaction
*(TODO — LiveKit/OpenAI Realtime pipeline, wake behaviour, latency observed.)*

### 5.5 Simulation-first workflow
MuJoCo MJCF scene `simulation/ailamp_scene.xml`; renders in `docs/media/`.

## 6. Evaluation and results
*(TODO — the section the grade hangs on. Suggested measurements:*
- *detection rate / false positives over N minutes in the lab,*
- *end-to-end latency: frame captured → servo motion start,*
- *voice command → action latency,*
- *CPU/GPU load and power draw per profile,*
- *print/assembly effort, cost against the budget,*
- *unit-test count and pass rate.)*

## 7. Discussion, limitations and future work
*(TODO — what did not work, what we would change: e.g. cloud dependency in the Nano profile,
motion smoothness, thermal behaviour inside the closed base, safety envelope.)*

## 8. Conclusion
*(TODO)*

## References
1. Human Computer Lab, *LeLamp* — https://github.com/humancomputerlab/LeLamp (GPL-3.0)
2. Human Computer Lab, *lelamp_runtime* — https://github.com/humancomputerlab/lelamp_runtime
3. Apple Machine Learning Research, *ELEGNT: Expressive and Functional Movement Design for
   Non-anthropomorphic Robot* (2025)
4. E. A. Lee, S. A. Seshia, *Introduction to Embedded Systems — A Cyber-Physical Systems Approach*
5. *(add: YOLO / pose model, MuJoCo, LiveKit, Feetech ST3215 datasheet)*

## Appendix A — Reproducing our results
```bash
pip install -e ".[test]"
scripts/verify_local.sh      # unit tests, lockfile, static hardware checks, MuJoCo smoke test
ailamp sim-check --render outputs/sim_check.png
```

## Appendix B — Division of work
See `TEAM.md`. *(TODO — one line per member.)*
