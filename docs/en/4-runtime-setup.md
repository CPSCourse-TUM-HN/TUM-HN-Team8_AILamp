# 4. Runtime Setup

## Jetson Nano OS

Use the NVIDIA Jetson Nano Developer Kit 4GB software stack, not the upstream LeLamp Raspberry Pi OS stack.

```text
Target board -> NVIDIA Jetson Nano Developer Kit 4GB, MPN 945-13450-0000-100
Target OS -> NVIDIA JetPack 4.6.x / Jetson Linux R32.x, Ubuntu 18.04 based
Recommended first flash -> JetPack 4.6.1 SD card image for Jetson Nano Developer Kit
Power -> 5V 4A barrel jack, 5.5mm x 2.1mm center-positive, J48 jumper installed
```

Do not install Raspberry Pi OS, Pi Camera packages, or ReSpeaker HAT overlays. The current AILamp profile has no microphone and no speaker.

## Install AILamp Runtime

Run the read-only environment diagnostic first:

```bash
scripts/check_jetson_nano_environment.sh
```

Use the already working LeLamp playback environment for physical output. AILamp supports Python >=3.11 for software preview, but the local upstream LeLamp runtime currently requires Python >=3.12, so do not blindly rebuild Python or overwrite calibration.

```bash
cd ~/projects/AILamp
source /path/to/working/lelamp-env/bin/activate
pip install -e ".[nano,test]"
```

The Nano extra installs OpenAI/API-hybrid support, USB camera support, and serial support. It does not install LiveKit, sounddevice, local YOLO pose, MuJoCo, or local large models.

## Configure

The active profile is `config/hardware.toml`. Confirm these fields before physical output:

```text
motors.lamp_id -> exact LeLamp follower ID that already plays motions successfully; default "ailamp" is only a placeholder
motors.port -> ST3215 controller serial port
led.port -> Pico WH serial port
camera.device_path -> Arducam USB UVC device
```

Use environment variables for model keys only:

```bash
export OPENAI_API_KEY=...
```

## Checks

Safe offline checks:

```bash
ailamp runtime-check --offline
ailamp hardware-check
```

After the Jetson is wired:

```bash
ailamp runtime-check --include-motor-runtime
ailamp hardware-check --include-devices
ailamp camera-test
ailamp led-test
ailamp motor-test
```

`runtime-check --include-voice` reports voice/audio as deliberately disabled for this profile. `audio-test` also fails early with an audio-disabled message instead of importing `sounddevice`.

`ailamp motor-test` lists configured ports and bundled recordings; it does not move hardware or prove actual motion.

## Pico WH Firmware

Copy `firmware/pico_led_controller/code.py` to the CircuitPython drive on the Pico WH.

```text
CircuitPython 9.x for Raspberry Pi Pico WH
adafruit-circuitpython-neopixel library in /lib
NeoMatrix data line on GP0
TXS0108E level shifter between Pico GP0 and NeoMatrix DIN
```

Serial protocol:

```text
PING
CLEAR
SOLID r g b
BRIGHTNESS value
PIXELS r,g,b;r,g,b
```

## Current Control Path

Start dry-run first:

```bash
ailamp web-control
```

Then enable the OpenAI brain without camera:

```bash
OPENAI_API_KEY=... ailamp web-control --brain
```

Then add camera context:

```bash
OPENAI_API_KEY=... ailamp web-control --brain --vision
```

Only after `led-test` and `motor-test` pass:

```bash
OPENAI_API_KEY=... ailamp web-control --brain --vision --with-outputs
```

Stop any original LeLamp playback process, `vision-loop`, or voice-agent process before launching physical outputs. The browser must explicitly unlock physical output before commands are sent. Prefer SSH tunneling to the loopback server. If binding to a LAN address, use a concrete Nano LAN IPv4 address, set a strong `AILAMP_CONTROL_TOKEN`, and use only a trusted network.
