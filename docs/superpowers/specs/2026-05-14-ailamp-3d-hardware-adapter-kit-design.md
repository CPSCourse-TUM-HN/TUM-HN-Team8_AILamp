# AILamp 3D Hardware Adapter Kit Design

Date: 2026-05-14
Status: written for user review

## Goal

Create a printable AILamp adapter kit for the Jetson Nano 4GB API-hybrid hardware profile while preserving the original LeLamp mechanical parts. The first implementation must avoid destructive edits to the seven upstream `.3mf` files and instead add separate printable mounting parts for the new controller, camera, microphone, LED matrix, servo driver, and cable routing.

## Fixed Toolchain

- Primary mechanical CAD workflow: OnShape, matching the LeLamp/AIlamp project documentation.
- Primary robot simulation workflow: MuJoCo MJCF.
- Blender, Fusion 360, SolidWorks, Gazebo, and Isaac Sim are not primary AILamp v1 workflows.
- The generated repository assets may include `.3mf`, `.stl`, and simplified MuJoCo visual geometry, but OnShape remains the source-of-truth CAD workflow.

## Existing Printable Baseline

The original seven LeLamp printable files remain unchanged:

- `3D/LampArm (Base-Elbow).3mf`
- `3D/LampArm (Elbow-Wrist).3mf`
- `3D/LampArm (Pitch).3mf`
- `3D/LampBase - Cover.3mf`
- `3D/LampBase.3mf`
- `3D/LampHead - Diffuser.3mf`
- `3D/LampHead.3mf`

Measured existing envelopes:

- Base: 160 x 190 x 40 mm
- Base cover: 154 x 184 x 6 mm
- Lamp head: 104 x 79 x 142 mm
- Diffuser: 104 x 79 x 5 mm

## Hardware Fit Targets

The adapter kit is designed around the selected Jetson Nano 4GB API-hybrid hardware:

- NVIDIA Jetson Nano Developer Kit 4GB, MPN `945-13450-0000-100`: 100 x 80 mm developer kit footprint.
- Raspberry Pi Pico WH: 21 x 51 mm board footprint.
- Waveshare Servo Driver with ESP32, SKU `21593`: 65 x 30 mm board footprint, 2.75 mm mounting holes.
- Adafruit NeoPixel NeoMatrix 8x8, Product ID `1487`: 71.17 x 71.17 x 3.28 mm LED matrix.
- Arducam `UB0234`: 32 x 32 mm camera board, 28 x 28 mm hole pitch, M12 lens.
- Seeed Studio ReSpeaker XVF3800 USB 4-Mic Array with Case, Product `6490`: 35 x 86 mm envelope.
- Waveshare ST3215 Servo: 45.22 x 35 x 24.72 mm, already represented by the LeLamp-style servo joint layout and not redesigned in this adapter pass.

## Clearance And Fit Rules

The adapter kit must prioritize easy assembly over tight snap-fit geometry. Printed parts should locate boards without pinching them, and all PCB pockets, wire exits, and board retainers must include practical clearance:

- PCB edge pockets: nominal hardware footprint plus 1.5 mm clearance per side.
- Board retaining lips: hold the board vertically or laterally without pressing on components.
- Mounting holes: use oversize clearance holes, at least 0.4 mm larger than the screw nominal diameter.
- Cable exits: at least 2.5 mm extra width beyond the cable or connector bundle envelope.
- Sliding or clip-on features: no hard press-fit requirement; use screws, zip ties, or removable straps for retention.
- Camera lens aperture: at least 1.0 mm radial clearance around the M12 lens barrel.
- NeoMatrix holder: leave an air gap behind the diffuser and avoid direct LED-to-diffuser pressure.

The first print pass should be slightly loose. If later physical testing shows movement or vibration, retention should be improved with screws, pads, or zip ties rather than shrinking the board pockets too aggressively.

## Printable Outputs

Add a new directory for AILamp-specific printable adapters:

- `3D/AILamp_Adapters/AILamp_Jetson_Nano_Base_Tray.3mf`
- `3D/AILamp_Adapters/AILamp_Electronics_Side_Deck.3mf`
- `3D/AILamp_Adapters/AILamp_Head_Camera_Mount.3mf`
- `3D/AILamp_Adapters/AILamp_NeoMatrix_Holder.3mf`
- `3D/AILamp_Adapters/AILamp_ReSpeaker_External_Mount.3mf`
- `3D/AILamp_Adapters/AILamp_Cable_Clip_6mm.3mf`
- `3D/AILamp_Adapters/AILamp_Cable_Clip_10mm.3mf`

Each adapter also gets an exported `.stl` for slicer compatibility and MuJoCo visual reference work.

## Adapter Design

### Jetson Nano Base Tray

The Jetson tray is a low-profile external tray sized for the 100 x 80 mm Nano developer kit. It mounts behind or beside the original base rather than inside the 40 mm base cavity. It includes standoff bosses, bottom airflow slots, side cable exits for USB and power, and zip-tie relief slots. The tray should use M2.5 or M3 hardware where possible, with oversized clearance holes to account for board revision and print tolerance.

### Electronics Side Deck

The side deck holds the Waveshare ST3215 Servo Driver with ESP32 and the Raspberry Pi Pico WH. The servo driver side must leave access to USB-C, the 5.5 x 2.1 mm DC input, the OLED, and servo bus connectors. The Pico side must leave access to Micro-USB, headers, and LED signal wiring. The side deck should include cable-tie slots for 12V servo wiring and 5V LED wiring so those power domains remain physically separated.

### Head Camera Mount

The camera mount holds the Arducam UB0234 at the lamp head front/top area without modifying the original lamp head mesh. It uses a 32 x 32 mm board pocket with 28 x 28 mm hole pitch, a central M12 lens clearance aperture, and strap slots so the final viewing direction can be adjusted with placement or a thin shim during physical calibration. The first generated adapter is a flat reversible bracket rather than an angled camera body, so it remains easy to print and does not assume the final lamp-head strap path.

### NeoMatrix Holder

The NeoMatrix holder positions the 71.17 mm square LED matrix behind the existing diffuser. It provides a thin rear frame, LED board retaining lips, wire exit relief, and enough air gap to avoid pressing LEDs against the diffuser. It must preserve diffuser serviceability so the LED matrix can be removed without disassembling the arm.

### ReSpeaker External Mount

The ReSpeaker mount is external and mechanically isolated from the speaker and servo driver. It presents the XVF3800 mic array upward or forward with minimal obstruction. It should not place the microphone directly above the speaker, because that increases acoustic echo and enclosure vibration risk. The mount should include a USB cable relief path back toward the Jetson Nano tray.

### Cable Clips

Two cable clip sizes are included:

- 6 mm internal channel for USB/signal cable groups.
- 10 mm internal channel for combined power or servo cable bundles.

Both clips use loose rectangular internal channels, small zip-tie windows, and flat mounting pads. Clips are separate parts so the final routing can be adjusted after the real lamp is assembled.

## Simulation Representation

MuJoCo simulation should not attempt to reproduce every adapter screw boss. Add simplified visual geometry only:

- A thin base-adjacent Jetson tray.
- A small electronics board group.
- A lamp-head camera block with a simplified board and lens-clearance volume.
- A square LED matrix plane behind the diffuser.
- A ReSpeaker block on or near the base.

These simplified bodies are visual and collision-light. They document physical placement, help screenshots, and keep the model loadable without changing the servo kinematic chain.

## Documentation Updates

Update both English and Chinese docs with:

- Adapter kit file list.
- Hardware placement diagram.
- Assembly order.
- Wire routing guidance.
- Power-domain separation: 12V servo system and 5V LED system remain separate.
- Note that the original seven LeLamp `.3mf` files are preserved.
- Note that final hole positions should be checked against the real boards before large print batches.

## Verification Plan

Design-level checks:

- All new adapter files are present under `3D/AILamp_Adapters/`.
- The original seven LeLamp `.3mf` files are byte-for-byte untouched.
- Hardware envelopes follow the clearance rules: at least 1.5 mm around PCB edges, oversize screw holes, and at least 2.5 mm extra width at cable exits.
- Camera mount exposes the M12 lens and does not block the diffuser.
- NeoMatrix holder accepts a 71.17 mm square board envelope.
- ReSpeaker mount does not share the speaker mounting surface.
- Cable clips route USB/signal and power wiring without crossing the lamp joint axes.

Software/simulation checks:

- MuJoCo scene still loads.
- New visual assets do not break the existing actuator mapping.
- A render shows the camera, LED matrix, Jetson tray, electronics deck, and microphone mount in plausible locations.

Physical prototype checks:

- Print first pass at draft quality.
- Fit boards before powering electronics.
- Confirm USB, DC barrel, servo bus, and LED wiring can be plugged and unplugged.
- Move all five joints manually through their safe range and verify no cable clip or adapter contacts moving parts.

## Non-Goals

- Do not redesign the full lamp shell in this pass.
- Do not replace the original LeLamp base, arm, head, or diffuser files.
- Do not move to a Fusion 360, SolidWorks, Blender, Gazebo, or Isaac Sim primary workflow.
- Do not optimize for injection molding or commercial enclosure manufacturing.
- Do not assume the Jetson Nano, power supplies, speaker, and all wiring fit inside the original base cavity.

## Source References

- NVIDIA Jetson Nano Developer Kit size: https://nvidianews.nvidia.com/news/nvidia-announces-jetson-nano-99-tiny-yet-mighty-nvidia-cuda-x-ai-computer-that-runs-all-ai-models
- Raspberry Pi Pico WH board form factor: https://www.raspberrypi.com/products/raspberry-pi-pico/
- Adafruit NeoPixel NeoMatrix 8x8 Product ID 1487 dimensions: https://www.adafruit.com/product/1487
- Arducam UB0234 datasheet: https://uctronics.com/download/Amazon/UB0234_2MP_Wide_Angle_USB2.0_Camera_Datasheet.pdf
- Waveshare ST3215 Servo dimensions: https://www.waveshare.com/wiki/ST3215_Servo
- Waveshare Servo Driver with ESP32 dimensions: https://www.waveshare.net/wiki/Servo_Driver_with_ESP32
- Seeed Studio ReSpeaker XVF3800 dimensions: https://www.seeed.cc/product/respeaker-mic-array-v2-0
