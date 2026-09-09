# TEAM8 AiLamp — Technical Report

English technical report for INHN0018, Group 8, TUM Campus Heilbronn.

- [Read the report (PDF, 15 pages)](TEAM8_AiLamp_Technical_Report.pdf)
- [Download the editable Word document](TEAM8_AiLamp_Technical_Report.docx)
- [Read the full Markdown source](TEAM8_AiLamp_Technical_Report.md)

The report covers the original full-system design: an OpenAI decision layer, camera perception,
voice interaction, five-axis expressive motion and lighting, using Jetson Nano as the embedded
platform. It distinguishes design targets, software evidence, the motion demonstration and
verified physical results, and records the current integration limitations.

The figures used by the Markdown report are included in [`assets/`](assets/). The earlier
[`TECHNICAL_REPORT.md`](TECHNICAL_REPORT.md) is retained as a historical planning outline;
use the files linked above for the completed report.

## Addendum — 9 September 2026

Section 11 and the abstract report a CI run of **136 passing and five failing tests** at commit
`3bfe93c`. That figure is correct for the run it cites and is left unaltered, but it now understates
the work: the suite has since grown to **388 tests**, mostly covering the five-axis motor runtime,
the control console and the offline motor CLI added after that run.

At the submitted revision the suite is **388 passing, 0 failing**.

The five failures described in section 11 have been resolved. Three were the 3D adapter-generation
tests, which import `manifold3d`, `mapbox-earcut` and `trimesh`; those were never declared in the
`[test]` extra of `pyproject.toml`, so CI installed without them. They are now declared and pinned in
`uv.lock`. The remaining two were documentation assertions that had drifted from the files they
check. No test was weakened or skipped to reach this result.
