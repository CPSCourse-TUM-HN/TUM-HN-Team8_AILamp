# 6. Common Issues

## Device Paths Changed

Run:

```bash
ls /dev/ttyACM* /dev/ttyUSB* /dev/video*
```

Update `config/hardware.toml`, which is the default Jetson Nano profile, and rerun:

```bash
ailamp runtime-check --include-devices
```

## Runtime Check Fails

- Missing `OPENAI_API_KEY`: export it before Nano API-hybrid vision.
- Missing LiveKit/OpenAI packages: reinstall with `pip install -e ".[nano]"`.
- Missing LeLamp motor runtime: install the sibling `lelamp_runtime` package before real ST3215 playback.
- `outputs/` not writable: fix project permissions before running `vision-loop`.

## LED Does Not Light

- Check `GST60A05-P1J` output.
- Check Pico WH serial port.
- Check TXS0108E direction and ground.
- Check the 330 ohm data resistor.
- Run `ailamp led-test`.

## Servo Does Not Move

- Check `GST120A12-P1J` output.
- Check Waveshare driver serial port.
- Check ST3215 IDs.
- Run `ailamp motor-test`.

## Camera Does Not Open

- Check `/dev/video0`.
- Reduce resolution to 640x480.
- Check Arducam UB0234 USB cable.

## Simulation Check Fails

- Run `ailamp sim-check` before opening the viewer.
- If actuator mapping fails, inspect `simulation/robot.xml` before running recordings.
- If render fails, install the simulation extra with `pip install -e ".[simulation]"`.
