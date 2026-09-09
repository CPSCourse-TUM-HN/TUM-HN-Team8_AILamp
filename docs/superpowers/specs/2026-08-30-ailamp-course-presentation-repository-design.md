# AILamp Course Presentation and Repository Design

## Purpose

Prepare Group 8's INHN0018 course-presentation content, a clean course-submission repository, and a polished presentation deck for AILamp. The presentation is designed for a nominal 15-minute group slot on 2026-09-09. The task produces an editable PPTX and a course-submission PDF in addition to the content files.

## Confirmed Project Positioning

- The project name shown to the audience is **AILamp**.
- AILamp is presented as Group 8's working Jetson Nano robotic-lamp prototype.
- The final demonstrated capability set matches the public LeLamp-level system: five-axis servo movement, servo setup and calibration, movement recording and replay, programmable light, camera, microphone, and speaker.
- The Raspberry Pi route is replaced by a Jetson Nano route.
- Nano-compatible peripherals are allowed where direct Raspberry Pi peripherals are incompatible: a USB camera, USB microphone/speaker interface, and Pico-based light control.
- OpenAI vision, posture tracking, autonomous behavior selection, DecisionService, and other unverified AILamp extensions are excluded from the final-outcome narrative.
- The presentation does not call AILamp an imitation. It includes a concise, accurate attribution stating that open-source LeLamp mechanical assets and baseline runtime were adapted and integrated for Jetson Nano. The repository retains `LICENSE` and `NOTICE.md`.

## Audience and Communication Goal

The audience is the INHN0018 Embedded Systems, Cyber-Physical Systems and Robotics teaching team and other students. The presentation must show a coherent CPS implementation rather than a shopping list or build tutorial. It should connect sensors, computation, and physical actuators, explain the team's engineering method, and end with a real system demonstration and concrete validation evidence.

## Deliverables

The content-only presentation package contains:

1. `presentation/AILamp_Presentation_Content_EN.md` — exact audience-facing copy for each slide, with visual placement guidance.
2. `presentation/AILamp_Speaker_Script_EN.md` — timed English talk track by slide and speaker.
3. `presentation/AILamp_Speaker_Script_ZH.md` — Chinese understanding/rehearsal version aligned with the English script.
4. `presentation/AILamp_Demo_Runbook.md` — live-demo sequence, fallback video sequence, operator cues, and pre-demo safety checks.
5. `presentation/README.md` — assembly instructions and the final-file checklist.
6. `output/presentation/AILamp_Group8_Course_Presentation.pptx` — editable 16:9 presentation deck.
7. `output/pdf/AILamp_Group8_Course_Presentation.pdf` — exported course-submission PDF.

## Visual Design and Reference Use

The deck uses the three user-provided course PDFs only as visual reference material. It does not reproduce their course content, slides, or diagrams.

- **Title slide:** mirror the visual language of page 1 of `L02-2.pdf`: generous white canvas, large black title aligned on the left, restrained blue institutional accent in the upper-right area, and one original AILamp visual in the lower-right area. The title slide is adapted to a 16:9 canvas so it matches the main deck.
- **Body slides:** blend the reference decks' recurring TUM course conventions: high-contrast deep-navy section slides, white analysis slides, a thin TUM-blue top rule, large sans-serif headings, black body text, blue highlights, and occasional warm amber italic emphasis. The design uses flat editorial compositions instead of dense application-like cards.
- **Page furniture:** each body slide carries a small course line at lower left and a slide number at lower right. The footer remains deliberately subtle.
- **Visuals:** use existing AILamp renders, original wiring diagrams, and simple native diagrams. Photographic or generated imagery is never used to imply a hardware result that the team has not actually demonstrated.
- **Readability:** use 16:9 widescreen, slide titles at least 35 pt, body text at least 20 pt, and no visible planning notes, speaker cues, or timing text.

The design is intentionally an adaptation, not a pixel-level copy of any course deck.

## Presentation Structure

The deck uses ten slides and targets 14 minutes 30 seconds, leaving about 30 seconds of buffer.

| Slide | Purpose | Target time | Speaker |
| --- | --- | ---: | --- |
| 1 | Title and 30-second physical demonstration hook | 0:30 | Team Leader |
| 2 | Problem, objective, and user scenario | 1:00 | Team Leader |
| 3 | CPS system overview: sensing, computation, and actuation | 1:30 | Team Leader |
| 4 | Jetson Nano and peripheral integration | 1:30 | Member 2 |
| 5 | Five-axis mechanics and serial servo chain | 1:30 | Member 3 |
| 6 | Software architecture and runtime flow | 1:30 | Team Leader |
| 7 | Movement replay, lighting, camera, and audio capabilities | 1:30 | Member 4 |
| 8 | Engineering methodology and team workflow | 1:30 | Members 5 and 6 |
| 9 | Physical demonstration and validation evidence | 2:00 | Member 7 operates; Team Leader gives a short narration |
| 10 | Challenges, lessons, attribution, and conclusion | 1:30 | Member 7 presents challenges; Team Leader concludes |

The Team Leader speaks for approximately five minutes and fifteen seconds and owns the opening, CPS architecture, main software content, a short demonstration narration, and the closing statement. `Member 2` through `Member 7` are intentional role labels until the user supplies names.

## Content Rules

- Slides use short audience-facing English text. Detailed explanations stay in the speaker script.
- The English script is the authoritative spoken version. The Chinese script is a close rehearsal aid, not a second audience-facing deck.
- No numeric performance, reliability, latency, or accuracy claim is invented. If no measured value exists, the script describes the observable test rather than supplying a number.
- Hardware functions are described as completed only when the corresponding pre-presentation check has passed. The demo runbook identifies those checks.
- The first demonstration shows a safe, pre-recorded five-axis motion replay and a visible light response. Camera and audio are then shown as device-level functions unless a stronger behavior is actually verified.
- The attribution is brief and factual. It appears in the final slide and repository notice without dominating the presentation.
- Power-domain and safety details are summarized, not turned into a wiring tutorial.

## Demonstration Design

The primary demo sequence is:

1. Power and emergency-stop check before the talk.
2. Start from a known neutral pose.
3. Replay one short recorded movement.
4. Show a programmed light change.
5. Show that the camera device is available.
6. Show microphone/speaker input-output operation.
7. Return to a neutral pose and stop motor power safely.

The fallback is a locally stored demonstration video following the same sequence. The talk never depends on network access or an external AI API.

## Course Repository Design

A separate clean course repository is prepared as `Team-8-AILamp`, intended for the `CPSCourse-TUM-HN` organization. The existing development repository remains untouched so its uncommitted work is not overwritten.

```text
Team-8-AILamp/
├── README.md
├── LICENSE
├── NOTICE.md
├── presentation/
│   ├── AILamp_Presentation_Content_EN.md
│   ├── AILamp_Speaker_Script_EN.md
│   ├── AILamp_Speaker_Script_ZH.md
│   ├── AILamp_Demo_Runbook.md
│   └── README.md
├── report/
│   └── README.md
├── video/
│   └── README.md
├── media/
├── ailamp_runtime/
├── firmware/
├── config/
├── 3D/
├── simulation/
├── docs/
├── tests/
├── scripts/
├── .github/workflows/ci.yml
├── pyproject.toml
└── uv.lock
```

The clean repository excludes caches, local builds, temporary render folders, duplicated Claude/Manus handoff packages, secret files, and redundant generated previews. Required source, runtime code, firmware, Nano configuration, necessary print files, simulation assets, tests, license, and attribution are retained.

`report/README.md` and `video/README.md` provide explicit completion checklists until the actual report PDF and demonstration video are added. They must not imply that missing materials have already been submitted.

## Verification Criteria

Before delivery, verify:

- The ten-slide content totals no more than 14 minutes 30 seconds at rehearsal pace.
- Every slide has one clear message and corresponding English and Chinese script sections.
- The capability list contains only LeLamp-level functions implemented through the Nano-compatible route.
- No OpenAI-vision or autonomous-decision outcome appears in audience-facing content.
- Attribution exists in the repository and final slide.
- The course repository contains no credentials, `.env` files, caches, build directories, or handoff archives.
- All copied code and documentation paths resolve inside the clean repository.
- The repository landing page links to presentation, report, video, code, documentation, and verification instructions.
- The final course PDF remains a user-produced artifact and is not falsely listed as complete before it exists.

## Scope Exclusions

- Creating or exporting a PPTX or PDF.
- Inventing team-member names, measured results, or hardware-validation outcomes.
- Presenting upstream open-source work as wholly original.
- Adding new runtime features, modifying CAD, or changing robot behavior.
- Publishing or pushing to GitHub before the clean repository is reviewed locally.
