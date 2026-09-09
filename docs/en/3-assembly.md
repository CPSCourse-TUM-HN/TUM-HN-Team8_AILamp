# 3. Assembly

AILamp v1 keeps the original LeLamp printed parts intact. Do not cut `LampHead.3mf`, and do not cut, drill, or permanently modify the original LeLamp base, arm, head, or diffuser for the first prototype.

This assembly flow is organized as mechanical fit, wiring, power checks, first boot, and software validation.

## Mechanical Fit

1. Assemble the original LeLamp arm, wrist, head, and diffuser first. Keep `LampBase.3mf` and `LampBase - Cover.3mf` as references, not final AILamp base parts.
2. Fit `AILamp_LampBase_Electronics_Shell.3mf` as the replacement base body. Confirm the complete center motor chamber is clear before installing electronics; the base servo and arm root must not rub the shell, cover, or cable openings.
3. Confirm the forward electronics bay clears the Jetson Nano developer-kit envelope, USB plugs, barrel power plug, camera cable, optional future audio cable, and cable bend radius.
4. Confirm the 96 x 68 mm bottom Orin intake is not fully blocked by the desk; use feet or spacers in the final assembly so air can enter below the shell.
5. Fit `AILamp_LampBase_Electronics_Cover.3mf` as the replacement base top cover. Confirm the raised arm-mount collar surrounds the base servo/arm root without rubbing and gives a visible mechanical transition from the cover into the arm.
6. Confirm the cover service slots face the right direction: the front I/O/debug slot exposes Jetson USB/HDMI/power access, the rear slots carry 12V servo power, 5V LED power, and the ST3215 bus, and the left/right slots carry camera USB plus optional future audio USB.
7. Fit `AILamp_Base_Arm_Link_Boot.3mf` between the fixed cover collar and the moving arm root. Confirm the boot follows the arm-root transform in simulation and does not bind against the fixed cover collar.
8. Fasten the cover to the shell using the four corner screw positions at x +/-96 mm, y -106 mm and y +166 mm. Do not tighten fully until cable routing is confirmed.
9. Install the Orin using the 116 x 104 mm loose retainer pattern around the board envelope. The retainers are edge/strap posts, not hard claims about NVIDIA carrier-board through-holes.
10. Install the Pico WH and Waveshare ST3215 driver in the rear electronics bay. The Waveshare driver standoffs use the 58 x 23 mm mounting-hole spacing.
11. Confirm the boards can be removed without bending headers or blocking USB-C, Micro-USB, servo bus, 12V input, 5V LED wiring, or air intake.
12. Use `AILamp_Cable_Clip_6mm.3mf` for USB/signal cables and `AILamp_Cable_Clip_10mm.3mf` for power or servo bundles around the base.
13. `AILamp_Jetson_Orin_Base_Tray.3mf` and `AILamp_Electronics_Side_Deck.3mf` are optional bench-fit parts for debugging outside the lamp.

Fit rule: print the first adapter pass slightly loose. Tighten only after board fit is confirmed, using screws, zip ties, or removable straps.

Do not treat the unchanged LeLamp base as a closed internal enclosure for the Jetson Orin. The AILamp shell and cover replace the original base for the hidden-electronics build; print them at low infill and check connector clearance before a final print.

## Wiring

```text
Jetson USB -> Waveshare Servo Driver with ESP32 -> 5x Waveshare ST3215
Jetson USB -> Raspberry Pi Pico WH -> TXS0108E -> NeoMatrix DIN
Jetson USB -> Arducam UB0234
Optional future audio USB -> ReSpeaker XVF3800
GST120A12-P1J -> emergency switch -> barrel terminal -> WAGO 221-413 -> servo driver power
GST60A05-P1J -> barrel terminal -> WAGO 221-413 -> NeoMatrix 5V/GND
All signal-linked devices share GND where required by the device interface.
```

Keep the 12V servo power domain and 5V LED power domain physically separated. Route camera USB and any optional future audio USB away from servo power wiring where possible.

## LED Protection

```text
Pico GP0 -> TXS0108E A-side input
Pico 3V3 -> TXS0108E VCCA
NeoMatrix 5V -> TXS0108E VCCB
Common GND -> TXS0108E GND
TXS0108E B-side output -> 330 ohm resistor -> NeoMatrix DIN
TXS0108E OE -> VCCA
GST60A05-P1J 5V/GND -> 1000 uF capacitor -> NeoMatrix 5V/GND
```

## Power Checks

Before any software drives hardware:

1. Confirm the Jetson Nano is powered only by its 5V 4A barrel jack supply, with the J48 jumper installed.
2. Confirm the emergency switch disconnects the 12V servo supply.
3. Confirm `GST120A12-P1J` only powers the servo driver and ST3215 chain.
4. Confirm `GST60A05-P1J` only powers the NeoMatrix LED system.
5. Confirm the Pico, TXS0108E, and NeoMatrix share ground.
6. Confirm every ST3215 has the expected ID before installing the lamp in a constrained pose.

Do not run `--with-outputs` before `led-test` and `motor-test` pass.

## First Boot

Jetson Nano 4GB main flow:

```bash
ailamp runtime-check
ailamp hardware-check --include-devices
ailamp camera-test
ailamp led-test
ailamp motor-test
ailamp vision-demo
ailamp web-control --brain --vision
```

The same sequence can be run explicitly with `--config config/hardware.jetson-nano.toml`, but `config/hardware.toml` already targets the selected Jetson Nano hardware. Use `--config config/hardware.orin.toml` only if the project later returns to Orin hardware.

## Software Validation

Run dry-run software checks before real motion:

```bash
ailamp agent-tools-test --event person_right --offset 0.6 --request "follow me"
ailamp vision-loop --frames 30
```

After the lamp clears the dry-run path and has enough physical clearance:

```bash
ailamp vision-loop --with-outputs
ailamp web-control --brain --vision --with-outputs
```
