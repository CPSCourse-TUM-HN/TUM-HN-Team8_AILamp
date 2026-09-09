# Five-axis motor demo implementation plan

> Execution: the user approved Codex CLI implementation and requested acceleration. Use the approved specification directly; do not generate another detailed plan or ask for execution-mode selection.

**Goal:** Deliver the first-phase offline-validated motor-only player; no Nano access, actuation, deployment, or web page in this phase.

**Architecture:** A read-only bus adapter is injected into the existing MotorService. Extend its single worker minimally to serialize fresh reads, arm, play, stop-and-hold, feedback, and release. A small runtime owns job state and a standalone CLI exposes the approved commands.

**Tech stack:** Existing Python 3.11+ standard-library runtime, pytest, fake bus; hardware-only imports remain lazy.

## Source of truth

- `docs/superpowers/specs/2026-09-08-five-axis-physical-demo-design.md` contains the approved complete behavioral contract.
- `ailamp_runtime/ailamp/services/motor.py` is the shared sender. Preserve existing APIs and regression behavior.
- New calibration and all 13 source CSVs are immutable inputs under `output/hardware_backups/LeLamp-demo-calibration-20260908-7QE2U0/`.
- Baseline: all 270 repository tests passed before implementation; focused motor tests are 31 of these.

## Step 1 — Adapter, red then green

- [ ] Add `tests/test_motor_backend.py` using a fake bus whose operation log records register, values, and thread ID.
- [ ] Observe failing tests before creating `services/motor_backend.py`.
- [ ] Cover explicit calibration hash and five-axis map, read-only bus connect, raw bounds before normalization, torque/mode refusal, bounded SRAM setup before any goal, fresh measured hold, and disconnect without torque release.
- [ ] Follower connect/configure/calibrate must never be called. Use its explicitly configured bus only. No new dependency installs.

## Step 2 — Single-worker runtime, red then green

- [ ] Add `tests/test_motor_demo_runtime.py`, then minimal compatible MotorService extensions and `services/motor_runtime.py`.
- [ ] Serialize bus operations and goals on the existing worker; no second serial sender. Use cancellation generations so stop preempts frame dispatch, feedback waits, and scene-boundary submissions.
- [ ] Prevalidate entire jobs before any write. Preserve all five-axis source frames, including repeats, with bounded interpolation.
- [ ] Capture home from fresh actual values with calibration identity and confirmation, never the commanded cache.
- [ ] Test target acceptance versus actual feedback, timeout/failure latching, stop reading actual position and holding it, and no automatic release or retries.

## Step 3 — CLI and offline evidence

- [ ] Add `tests/test_motor_cli.py`, observe red, then implement `ailamp/motor_cli.py` without importing AI, audio, camera, LED, or MuJoCo.
- [ ] Default to fake simulation. Support list, preflight, connect/arm separation, capture-home, play, home, script, stop, status, release, and explicit exit policy.
- [ ] Add concise Chinese usage instructions. Do not implement the later web phase.
- [ ] Preflight all 13 Nano snapshot recordings and report frame counts, hashes, interpolation-aware duration, and offline-only status.
- [ ] Re-run focused and complete repository tests with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q`.
- [ ] Verify calibration and source CSV hashes remain unchanged. No Git commits or unrelated edits.

## Independent completion checks

The main operator will read the resulting source, run tests independently, check dry CLI startup and snapshot preflight, and distinguish offline validation from later supported physical experiments. No automatic hardware operation is authorized here.
