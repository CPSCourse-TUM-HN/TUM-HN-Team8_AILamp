# AILamp Current 3D Printing Guide

Version: 2026-05-18

This guide reflects the current AILamp Jetson Nano base revision. The original LeLamp arm, wrist, head, and diffuser parts are retained. The original LeLamp base and base cover are not final AILamp print parts; they are replaced by the AILamp electronics shell, cover, and moving arm-link boot.

## Final Print Set

| File | Qty | Use | Status |
| --- | ---: | --- | --- |
| `LampArm (Base-Elbow).3mf` | 1 | Original LeLamp lower arm | Print |
| `LampArm (Elbow-Wrist).3mf` | 1 | Original LeLamp upper arm | Print |
| `LampArm (Pitch).3mf` | 1 | Original LeLamp pitch bracket | Print |
| `LampHead.3mf` | 1 | Original lamp head | Print |
| `LampHead - Diffuser.3mf` | 1 | Original diffuser | Print |
| `AILamp_LampBase_Electronics_Shell.3mf` | 1 | Replacement base shell for Jetson Nano, Pico WH, ST3215 driver, wiring, and airflow | Print |
| `AILamp_LampBase_Electronics_Cover.3mf` | 1 | Replacement base cover with raised arm-mount collar | Print |
| `AILamp_Base_Arm_Link_Boot.3mf` | 1 | Moving transition boot between fixed cover and arm root | Print |
| `AILamp_Cable_Clip_6mm.3mf` | 2-4 | USB and signal cable routing | Print as needed |
| `AILamp_Cable_Clip_10mm.3mf` | 2-4 | Power and servo cable routing | Print as needed |
| `AILamp_Jetson_Nano_Base_Tray.3mf` | optional | Bench-fit Jetson tray for testing outside the lamp | Optional |
| `AILamp_Electronics_Side_Deck.3mf` | optional | Bench-fit Pico WH and ST3215 driver deck | Optional |
| `LampBase.3mf` | 0 | Original LeLamp base | Reference only |
| `LampBase - Cover.3mf` | 0 | Original LeLamp base cover | Reference only |

Each AILamp adapter `.3mf` has a matching `.stl` file in `3D/AILamp_Adapters/`.

## Geometry Reference

| Part | Main Dimensions | Critical Fit Features |
| --- | --- | --- |
| Replacement shell | 190 x 230 x 48 mm | Scaled from original LeLamp base proportions. Keeps the arm-origin relationship and provides internal electronics volume. |
| Replacement cover plate | 190 x 230 x 8 mm | Total height is 22 mm including the raised arm-mount collar. |
| Raised arm-mount collar | 92 x 98 x 18 mm | Center clearance is 58 x 64 mm around the base servo and arm root. Screw positions are x +/-36 mm, y +/-40 mm. |
| Moving arm-link boot | 74 x 74 x 42 mm | Center clearance is 42 x 48 mm. In MuJoCo it follows the original LeLamp base-cover transform so the arm root is not floating through a fixed opening. |
| Cover-to-shell screws | x +/-82 mm, y -88 mm and y +118 mm | Asymmetric Y positions preserve the original LeLamp base-to-arm origin relationship. |
| Jetson Nano standoffs | 84 x 64 mm | 3.4 mm loose screw clearance. |
| Waveshare ST3215 driver standoffs | 58 x 23 mm | Matches driver board mounting-hole spacing. |

## Recommended Print Settings

| Part Type | Material | Layer Height | Walls | Infill | Support |
| --- | --- | ---: | ---: | ---: | --- |
| Fit-check prototype | PLA | 0.20 mm | 3 | 15-20% | Only if slicer flags unsupported bridges |
| Final base shell and cover | PETG or PLA+ | 0.20 mm | 4-5 | 25-35% gyroid/grid | Minimal support, verify preview |
| Moving arm-link boot | PETG or PLA+ | 0.16-0.20 mm | 4 | 30-40% | Enable support inside roof if slicer preview shows sagging |
| Cable clips | PLA/PETG | 0.20 mm | 3 | 20% | Usually no support |

Fit rule: keep the first print slightly loose. The current adapter generator uses 1.5 mm PCB clearance per side and at least 2.5 mm extra width at cable exits. Do not force-fit boards or connectors.

## Orientation

| Part | Orientation |
| --- | --- |
| `AILamp_LampBase_Electronics_Shell.3mf` | Bottom face on print bed. Keep internal standoffs facing upward. |
| `AILamp_LampBase_Electronics_Cover.3mf` | Cover underside on print bed, raised collar upward. |
| `AILamp_Base_Arm_Link_Boot.3mf` | Largest flat base on print bed. Check the inner roof in slicer preview. |
| Cable clips | Flat base on print bed. |
| Original LeLamp arm/head parts | Use the original LeLamp orientation or the orientation recommended by your slicer preview. |

## Mechanical Fit Check

1. Assemble original LeLamp arm, wrist, head, and diffuser.
2. Do not print `LampBase.3mf` or `LampBase - Cover.3mf` as final AILamp base parts.
3. Fit `AILamp_LampBase_Electronics_Shell.3mf` and confirm Jetson Nano height, USB plugs, barrel power, camera/audio cables, and cable bend radius.
4. Fit `AILamp_LampBase_Electronics_Cover.3mf` and confirm the fixed raised collar does not rub the moving arm root.
5. Fit `AILamp_Base_Arm_Link_Boot.3mf` between the fixed cover collar and moving arm root.
6. Confirm the boot can move with the arm root and does not bind against the fixed cover collar.
7. Install Jetson Nano on the 84 x 64 mm standoffs.
8. Install Waveshare ST3215 driver on the 58 x 23 mm standoffs.
9. Install Pico WH in the electronics bay and confirm Micro-USB clearance.
10. Route USB, signal, servo, 12V, and 5V wires with the printed cable clips.

## Pre-Power Checklist

| Check | Requirement |
| --- | --- |
| 12V servo domain | Servo PSU only powers ST3215 driver and servo bus. |
| 5V LED domain | LED PSU only powers NeoMatrix and level shifter side that requires 5V. |
| GND | Share ground only where required by signal interfaces. |
| Emergency switch | Inline with 12V servo supply before first motor test. |
| NeoMatrix protection | 330 ohm data resistor and 1000 uF capacitor installed. |
| Connectors | No USB, barrel, or servo connectors are pressed against printed walls. |
| Motion clearance | Arm root moves without rubbing fixed cover collar. |

## Files

Generated print files are located in:

`<project-root>/AILamp/3D/AILamp_Adapters/`

Updated project docs are located in:

`<project-root>/AILamp/docs/en/1-3d-print.md`

`<project-root>/AILamp/docs/zh/1-3D打印.md`

