# 2026-09-05 Audio-Free Control Implementation Notes

Scope implemented in this pass:

- OpenAI brain is the documented default provider with model `gpt-4.1-mini`.
- `web-control` is the primary audio-free console. Default startup is dry-run, loopback-only, no camera, no serial, no audio, no cloud.
- Non-loopback web access requires a strong token from `AILAMP_CONTROL_TOKEN` or the selected `--token-env`.
- Birthday reminder active runtime files, config blocks, CLI command, systemd units, and live docs were removed.
- Nano audio is configured off by default. Voice/LiveKit remains an optional future profile path.
- The Jetson Nano diagnostic script is read-only and reports the active Python/LeLamp environment instead of rebuilding it.

Verification run in this environment:

```bash
python3 -m pytest tests/test_web.py tests/test_cli.py tests/test_config.py tests/test_birthday_removed.py tests/test_environment_script.py tests/test_led_serial.py tests/test_docs.py -q
# 47 passed, 1 skipped

PYTHONPATH=ailamp_runtime python3 -m ailamp.cli web-control --help
# exited 0

PYTHONPATH=ailamp_runtime python3 -m ailamp.cli runtime-check --offline
# exited 0

PYTHONPATH=ailamp_runtime python3 -m ailamp.cli hardware-check --failures-only
# exited 0

./scripts/check_jetson_nano_environment.sh
# exited 0 on this non-Jetson environment
```

Sandbox limitation:

- Real local HTTP socket bind was denied with `PermissionError: [Errno 1] Operation not permitted`, so the real-socket web test is skipped here. Handler security and lifecycle tests run through an unbound server; run the real HTTP QA on a normal dev shell or the Jetson.

Physical limitations:

- Normalized joint deltas are not collision avoidance.
- Software stop cancels pending work but is not a physical power cutoff.
- Camera-enabled brain mode sends JPEG frames to OpenAI and is not a hard real-time tracking loop.
