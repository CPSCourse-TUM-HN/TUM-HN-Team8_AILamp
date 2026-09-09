# 8. Audio-Free Control

Current AILamp control is audio-free. The primary path is typed text plus optional USB camera JPEGs into the OpenAI brain, then validated local tool admission through one serialized controller and the single motor worker.

Confirmed installed hardware: Jetson Nano 4GB, five ST3215 servos, Pico WH LED panel, and USB camera. Existing LeLamp servo IDs, calibration, and recording playback are reused. Microphone, speaker, and LiveKit voice are disabled in the default profile. The birthday reminder feature is removed from active runtime, config, CLI, systemd, and docs.

```text
text + optional JPEG + state/history
  -> real OpenAI BrainService model call
  -> validated tools
  -> guarded local motor/LED execution
  -> actual outcome fed back into history
```

Start dry-run first:

```bash
ailamp web-control
```

Enable text-only OpenAI brain:

```bash
OPENAI_API_KEY=... ailamp web-control --brain
```

Enable camera context:

```bash
OPENAI_API_KEY=... ailamp web-control --brain --vision
```

Enable physical outputs only after `led-test` and `motor-test` pass:

```bash
OPENAI_API_KEY=... ailamp web-control --brain --vision --with-outputs
```

Stop any original LeLamp playback process, `vision-loop`, or voice-agent process before launching physical outputs. The browser still requires an explicit unlock before any physical command is sent. Prefer SSH tunneling to the default `127.0.0.1:8765` server. LAN bind requires a concrete Nano LAN IPv4 address and a strong token:

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
export AILAMP_CONTROL_TOKEN='paste-generated-token-here'
OPENAI_API_KEY=... ailamp web-control --host 192.168.1.50 --brain --vision --token-env AILAMP_CONTROL_TOKEN
```

Do not expose the console to untrusted networks or the public internet.

AILamp supports Python >=3.11 for software preview, but the local upstream LeLamp runtime currently requires Python >=3.12. Reuse the Python environment where LeLamp playback already works. Set `motors.lamp_id` to the exact existing successful LeLamp follower ID; the default `ailamp` value is only a config default, not evidence of calibration.

```bash
scripts/check_jetson_nano_environment.sh
ailamp runtime-check --offline
ailamp runtime-check --include-motor-runtime
ailamp hardware-check --include-devices
```

`ailamp motor-test` lists configured ports and bundled recordings; it does not move hardware or prove actual motion.

The normalized joint limits are not collision avoidance. The OpenAI brain is a low-rate planning layer, not a hard real-time servo loop or instantaneous tracking system. OpenCV frame timestamps are host acquisition times, not USB hardware capture timestamps, and real USB buffering latency is unmeasured. Camera-enabled brain mode sends JPEGs to OpenAI; tests here use fakes and incur no cloud cost. Software stop cancels pending work but is not a physical power cutoff.
