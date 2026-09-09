# Review Feedback Fixes Implementation Plan

> **For agentic workers:** Use the repository's codex-delegation workflow and test-driven-development. The user approved implementation of the three confirmed review findings. No further feature work, Git commits, calibration changes, device access, or runtime model API calls are authorized by this plan.

**Goal:** Preserve truthful action feedback across requests and make the Nano dependency lock match the audio-free package metadata.

**Architecture:** Keep all public interfaces and existing actuator guards. Compress execution history semantically instead of cutting off per-action results. Resolve an idle pending motor result before allowing a new motor submission to overwrite it. Regenerate dependency metadata with the normal lock tool, without package upgrades or system installation.

**Tech Stack:** Existing Python runtime, pytest with fake hardware/provider clients, uv lock.

## Task 1: Preserve action outcomes in the model context

Files: `ailamp_runtime/ailamp/services/brain.py`, `tests/test_brain.py`.

- [x] Add a failing regression using actual `ExecutionOutcome.to_dict()` shape: 32-character request ID, a long Chinese reply, motor success, LED solid success, and LED brightness failure. Capture the exact text sent to a fake Responses client; require all three substep statuses to survive.
- [x] Run `python3 -m pytest tests/test_brain.py -q` and record the expected RED: initially 1 failed, 74 passed; long-error follow-up 1 failed, 75 passed with a JSONDecodeError proving the first patch still cut a record.
- [x] Replace blind truncation of standard outcome records with an explicit bounded summary. Preserve request identity, overall accepted/sent/completed/dry_run/error, and per-action name/sent/completed/error. Omit verbose reply/arguments before dropping execution results. Errors are bounded individually with a visible truncation marker; retained outcome JSON remains complete. Keep the complete context bounded and legacy synthetic history formats working.
- [x] Run the same test command to GREEN: 76 passed. Includes 12-record long-history, partial-success, and long-device-error cases. Root independently confirmed newest results survive the 5500-character context budget, including JSON-escaped error text.

## Task 2: Settle the previous motor outcome before replacement

Files: `ailamp_runtime/ailamp/services/controller.py`, `tests/test_controller.py`.

- [x] Add deterministic fake-only regressions: submit A, set fake motor idle without polling, submit B, finish/poll B. History must contain exactly one terminal result for A and B. Also cover A's asynchronous error before B resets the motor error, and preserve the existing busy rejection behavior.
- [x] Run `python3 -m pytest tests/test_controller.py -q` and record RED: 2 failed, 40 passed.
- [x] Finalize an existing completed/failed pending result while holding the controller lock, before admitting any new motor job that could overwrite its feedback. Keep busy outcomes pending and retain all stop/manual/disarm semantics.
- [x] Run `python3 -m pytest tests/test_controller.py tests/test_motor_service.py tests/test_motor_runtime.py -q` to GREEN: final 75 passed. An independent review found a busy-to-idle race between settlement and authorization; two additional deterministic regressions first produced 2 failed, 42 passed. Admission now rejects a new motor job whenever previous feedback remains pending after settlement. Final controller-only result: 44 passed. No unrelated lifecycle refactor.

## Task 3: Synchronize the audio-free lock

Files: `uv.lock`, `tests/test_config.py` (or a focused new dependency-lock test), current no-audio guide verification notes.

- [x] Add a test comparing the editable ailamp package's locked optional-dependency names/metadata markers with pyproject extras. Nano must exclude livekit-agents, livekit-plugins-noise-cancellation, and sounddevice; voice must include sounddevice. Preserve non-audio packages and other extras.
- [x] Confirm RED against the existing stale lock with `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider tests/test_config.py -q`: 1 failed, 7 passed.
- [x] Use `uv lock` without `--upgrade`, followed by `uv lock --check`. Following explicit user approval of a separate cache and subprocess PyPI access, `UV_DEFAULT_INDEX=https://pypi.org/simple uv --cache-dir /tmp/ailamp-uv-cache.mi8qvU lock` returned exit 0, `Resolved 130 packages in 5.26s`. Root independently ran `uv --cache-dir /tmp/ailamp-uv-cache.mi8qvU lock --check`: exit 0, `Resolved 130 packages in 11ms`. No handwritten metadata/hashes, uv sync, installation, or system-environment changes.
- [x] Confirm the regression is GREEN and record exact lock verification output. The previously failing dependency-extra test passes in the final 261-test software suite; the Chinese guide now records the completed lock update and remaining Nano validation limitations.

Task 3 blockers resolved: original-cache access and delegated-process DNS failures were handled only after the user separately approved a temporary cache and PyPI network access. The original cache permissions remain unchanged. The lock was generated normally, and its diff against `/tmp/ailamp-uv-cache.mi8qvU/before.lock` changes only editable `ailamp` optional-dependencies and the corresponding `requires-dist` markers. All resolved package versions, sources and artifact hashes remain unchanged. Audio dependencies remain in the global lock for the optional voice installation. Updated lock SHA256: `3197fa94d1202b42e9ec06ec1b9d83c30145349cb7c316f3f7347401a39a929e`.

## Final verification

- [x] Run `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q --ignore=tests/test_ailamp_adapters.py --ignore=tests/test_simulation_runner.py`: after lock regeneration, root verification returned **261 passed in 3.86s**, replacing the prior 260 passed / 1 stale-lock failure result.
- [x] Inspect scoped source/tests and run `git diff --check -- tests/test_config.py uv.lock`: exit 0, no output. The new runtime/test/guide/plan files are untracked, so each was also checked with `git diff --no-index --check -- /dev/null <file>`; these returned 1 for file differences with no whitespace diagnostics. Previously audited runtime files remain unchanged by the lock continuation; pyproject.toml and tests/test_config.py also match their pre-regeneration hashes. Pre-existing dirty CAD, simulation, exports, NOTICE and runtime configuration were left untouched.
- [x] Record exact RED/GREEN results and remaining Nano/API integration limitations in the current guide. No physical validation claimed.
