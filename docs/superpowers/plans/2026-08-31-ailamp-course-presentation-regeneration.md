# AILamp Course Presentation Regeneration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate an editable 16:9 AILamp course-presentation deck and a PDF whose cover adapts L02 page 1 and whose body slides blend the three provided TUM course-deck visual systems.

**Architecture:** Keep the user-approved project narrative in the existing presentation-design specification. Build an original PPTX from a JavaScript ES module using `@oai/artifact-tool`; source text, image provenance, and QA findings live in a temporary build directory. Export the final PPTX to `output/presentation/` and its PDF to `output/pdf/`.

**Tech Stack:** Node.js with `@oai/artifact-tool`, LibreOffice headless conversion, Poppler rendering, AILamp-local renders and diagrams.

---

## File Structure

- Modify: `docs/superpowers/specs/2026-08-30-ailamp-course-presentation-repository-design.md` — record the PPTX/PDF output and visual-reference rules.
- Create: `docs/superpowers/plans/2026-08-31-ailamp-course-presentation-regeneration.md` — this execution plan.
- Create: `presentation/AILamp_Presentation_Content_EN.md` — concise per-slide audience-facing copy and image mapping.
- Create: `presentation/AILamp_Speaker_Script_EN.md` — presenter talk track and source note mapping.
- Create: `presentation/AILamp_Speaker_Script_ZH.md` — Chinese rehearsal aid aligned to the English script.
- Create: `presentation/AILamp_Demo_Runbook.md` — safe live demonstration and fallback-video sequence.
- Create: `output/presentation/AILamp_Group8_Course_Presentation.pptx` — editable final deck.
- Create: `output/pdf/AILamp_Group8_Course_Presentation.pdf` — exported final PDF.
- Create temporarily: `tmp/presentation-ailamp-course-*/build_deck.mjs` — artifact-tool deck builder.
- Create temporarily: `tmp/presentation-ailamp-course-*/source-notes.txt` and `qa-ledger.txt` — provenance and QA records.

### Task 1: Lock the Content and Reference Mapping

**Files:**
- Modify: `docs/superpowers/specs/2026-08-30-ailamp-course-presentation-repository-design.md`
- Create: `presentation/AILamp_Presentation_Content_EN.md`
- Create: `presentation/AILamp_Speaker_Script_EN.md`
- Create: `presentation/AILamp_Speaker_Script_ZH.md`
- Create: `presentation/AILamp_Demo_Runbook.md`

- [ ] **Step 1: Record the visual-reference constraints in the design specification**

Add the 16:9 format, the L02-inspired white title slide, the blended TUM-course body-slide system, and the output PPTX/PDF paths. State that these are original adaptations rather than copied slide content.

- [ ] **Step 2: Write exact English slide copy for slides 1–10**

Use the approved narrative: objective, CPS overview, Jetson Nano integration, five-axis mechanics, runtime, feature set, engineering workflow, validation/demo, and conclusion. Restrict claims to the LeLamp-level capability set and Nano-compatible hardware.

- [ ] **Step 3: Write matching English and Chinese talk tracks**

Use speaker labels `Team Leader` and `Member 2` through `Member 7`, preserve the 14:30 overall target, and explain which content is narrated during the physical demonstration.

- [ ] **Step 4: Write a demo runbook with a no-network fallback**

Include pre-power safety check, neutral pose, movement replay, light response, camera and audio device checks, safe shutdown, and locally stored video fallback.

- [ ] **Step 5: Check content boundaries**

Run:

```bash
rg -n -i '(openai vision|decisionservice|posture tracking|autonomous behavior)' presentation
```

Expected: no audience-facing claim of excluded extensions. Review every intentional reference manually before continuing.

### Task 2: Build the Editable Deck

**Files:**
- Create: `tmp/presentation-ailamp-course-*/build_deck.mjs`
- Create: `output/presentation/AILamp_Group8_Course_Presentation.pptx`

- [ ] **Step 1: Load the designated presentation runtime**

Call the workspace-dependency loader and use only the returned Node executable, module path, and binary directory. Set the command-scoped variables `RUNTIME_NODE`, `RUNTIME_NODE_MODULES`, and `RUNTIME_BIN_DIR`.

- [ ] **Step 2: Create the temporary build directory and source provenance file**

Use `mktemp -d` under `AILamp/tmp/`. Record the three PDF reference files, the local AILamp images, and their intended slide usage in `source-notes.txt`.

- [ ] **Step 3: Read the artifact-tool quick-start and API documentation**

Read `artifact_tool_docs/API_QUICK_START.md` and `artifact_tool_docs/api/API_DOCS.md` from the Presentation skill before authoring `build_deck.mjs`.

- [ ] **Step 4: Mark the presentation creation operation**

Run this as a standalone command from the Presentation skill directory:

```bash
node container_tools/mark_artifact_operation_started.mjs --operation-kind create --expected-output-count 1 --output-format pptx
```

- [ ] **Step 5: Implement the deck builder in JavaScript**

Create a 16:9 deck with ten slides. Use this visual contract:

```javascript
const COLORS = {
  tumBlue: '0065BD',
  darkNavy: '0D1B2A',
  ink: '111111',
  warmAmber: 'F59E0B',
  mist: 'F5F7FA',
  muted: '708090'
};
```

Slide 1 uses a white L02-style cover with left-aligned title text, a restrained TUM-blue wordmark treatment at upper right, and an original AILamp visual at lower right. Slides 2–10 alternate white analytic layouts and deep-navy section/impact layouts, retain a thin blue top rule or blue highlight, and include subtle lower-left course text and lower-right page numbers. Add [Sources] speaker-note blocks for local images and course-PDF visual references.

- [ ] **Step 6: Export the final PPTX**

Run the builder with the designated Node runtime and export exactly:

```text
/Users/yugu/Documents/New project 4/AILamp/output/presentation/AILamp_Group8_Course_Presentation.pptx
```

### Task 3: Export and Inspect the PDF

**Files:**
- Create: `output/pdf/AILamp_Group8_Course_Presentation.pdf`
- Create: `tmp/presentation-ailamp-course-*/qa-ledger.txt`

- [ ] **Step 1: Export the PPTX to PDF**

Use LibreOffice headless conversion and create exactly:

```text
/Users/yugu/Documents/New project 4/AILamp/output/pdf/AILamp_Group8_Course_Presentation.pdf
```

- [ ] **Step 2: Run automated PPTX canvas checks**

Run:

```bash
python3 /Users/yugu/.codex/plugins/cache/openai-primary-runtime/presentations/26.826.12353/skills/presentations/container_tools/slides_test.py /Users/yugu/Documents/New\ project\ 4/AILamp/output/presentation/AILamp_Group8_Course_Presentation.pptx
```

Expected: no unreviewed overflow warnings.

- [ ] **Step 3: Render every PPTX and PDF slide/page**

Run the Presentation skill renderer for the PPTX and `pdftoppm -png` for the PDF. Create a contact sheet only for deck-level flow; inspect every slide/page individually at full size.

- [ ] **Step 4: Correct visual defects in the builder and regenerate**

If any title wraps, body text clips, reference-style treatment is inconsistent, images are blurry, or elements overlap, revise `build_deck.mjs`, re-export the PPTX and PDF, and rerun Steps 2–3.

- [ ] **Step 5: Record the validation evidence**

Write the exact commands, exit codes, page count, and any resolved defects to `qa-ledger.txt`.

### Task 4: Package the Course Materials

**Files:**
- Modify: `presentation/README.md`
- Modify: `report/README.md`
- Modify: `video/README.md`
- Create or Modify: `README.md` in the clean course-submission repository after its local review

- [ ] **Step 1: Link the deck and scripts from `presentation/README.md`**

List the PPTX, PDF, English content, English script, Chinese rehearsal script, and demo runbook. Mark the PPTX as the editable source and the PDF as the submission version.

- [ ] **Step 2: Preserve explicit report and video status**

Keep report and video checklists factual: they list required files without saying that missing artifacts have been submitted.

- [ ] **Step 3: Verify package integrity**

Run:

```bash
find presentation output/presentation output/pdf -maxdepth 2 -type f | sort
```

Expected: all six presentation materials plus the PPTX and PDF are listed at their documented paths.

- [ ] **Step 4: Commit only new course-presentation source documentation**

Before committing, inspect the staging area. Do not stage the user’s unrelated dirty worktree changes. Use a commit message that names the presentation materials only.

## Plan Self-Review

- The design specification's content, attribution, 16:9 visual system, and output-format requirements each map to one or more tasks above.
- The plan names every source and output path, all required runtime commands, the original visual contract, and the inspection loop.
- The plan contains no unspecified source assets, invented measurements, or vague visual-acceptance rule.
