# CPS INHN0018 — Group 8 submission checklist

Presentation: **09.09.2026, from 13:00**, Etzelstr. 38 / online. Duration: **15 min per team**
(as announced for the July session; the September slot order was still being arranged on 21.08).

## Required items (Moodle: "Projects: Choosing your presentation date + Submission")

| # | Item | Where | Status |
|---|---|---|---|
| 1 | Video demonstrating the project in action | [Combined demo](docs/video/TEAM8_AiLamp_Combined_Demo.mp4) / [all clips](docs/video/) | ☑ published 09.09.2026 |
| 2 | Technical report — project, methodologies, findings | [PDF](docs/report/TEAM8_AiLamp_Technical_Report.pdf) / [Word and source](docs/report/) | ☑ report published |
| 3 | Presentation slides, **PDF format** | [PDF](docs/slides/Group8_CPS_Presentation.pdf) / [PowerPoint source](docs/slides/Group8_CPS_Presentation.pptx) | ☑ published 09.09.2026 |
| 4 | All code used in the project | repository root | ☑ pushed |
| 5 | Link to the GitHub repository holding all of the above | <https://github.com/CPSCourse-TUM-HN/TUM-HN-Team8_AILamp> | ☑ pushed 30.08.2026 |

> All materials (presentation, report, code and video) must be uploaded to the team's GitHub
> repository, and the code must additionally live in the course organisation
> <https://github.com/CPSCourse-TUM-HN>. Access is granted by Moaaz Eid (moaaz.eid@tum.de).

## Hardware handover

After the presentation the hardware must be handed to the CPS team (Moaaz). Teams that do not
attend in person are expected to arrange the handover before leaving. *Group 8 has asked Hadi
whether the handover may take place by 22.09.2026 — pending confirmation.*

## Push to the course organisation

```bash
cd TUM-HN-Team8_AILamp   # or your existing local clone
git remote -v   # origin -> git@github.com:CPSCourse-TUM-HN/TUM-HN-Team8_AILamp.git
git push
```

Repository (public, GPL-3.0): <https://github.com/CPSCourse-TUM-HN/TUM-HN-Team8_AILamp>

## Before the presentation

- [x] Slides exported as PDF and committed
- [x] Demo video recorded, committed or linked from `docs/video/README.md`
- [x] Report finished and exported to PDF
- [ ] `scripts/verify_local.sh` passes on a clean checkout
- [x] `README.md` links resolve on GitHub
- [x] `NOTICE.md` provenance up to date
- [x] Repository pushed to `CPSCourse-TUM-HN` and public
- [ ] Handover of hardware agreed with Moaaz
