# 1. 3D Print

AILamp uses the original LeLamp print files in `3D/`.

## CAD Source

Keep the upstream LeLamp mechanical workflow:

- Primary CAD software: OnShape.
- Upstream CAD reference: `https://cad.onshape.com/documents/16c9706360b5ad34f9c8db49/w/2edfa54c83253c120fbc9e58/e/a7196194821d9cfe2842a44a`
- Upstream assembly reference: `https://cad.onshape.com/documents/16c9706360b5ad34f9c8db49/w/2edfa54c83253c120fbc9e58/e/a35eec618cd78ea5d74bf01b`
- Export print files as `.3mf`.
- Export simulation meshes as `.stl` when MuJoCo assets need updating.

Do not switch the primary mechanical workflow to Blender, SolidWorks, Fusion 360, or FreeCAD for AILamp v1.

## Print Counts

| File | Qty |
| --- | ---: |
| `LampBase.3mf` | 1 |
| `LampBase - Cover.3mf` | 1 |
| `LampArm (Base-Elbow).3mf` | 1 |
| `LampArm (Elbow-Wrist).3mf` | 1 |
| `LampArm (Pitch).3mf` | 2 |
| `LampHead.3mf` | 1 |
| `LampHead - Diffuser.3mf` | 1 |

## AILamp Replacement Base Kit

AILamp v1 keeps the original seven LeLamp `.3mf` files unchanged for reference. For the selected Jetson Orin Nano Super build, do not print the original `LampBase.3mf` and `LampBase - Cover.3mf` as final parts. Print the AILamp replacement base shell and cover instead.

Fit check: the selected Jetson Orin Nano Super developer kit is treated as a 103 x 90.5 x 34.77 mm developer-kit envelope, with the carrier board itself at 100 x 79 mm. The Raspberry Pi Pico WH is treated as 51 x 21 mm, and the Waveshare Servo Driver with ESP32 as 65 x 30 mm. The original LeLamp `LampBase.3mf` outer envelope is about 160 x 190 x 40 mm, so the AILamp base replacement grows the base to 220 x 300 x 66 mm while preserving the complete central motor chamber and arm-root relationship.

Native rounded geometry: the replacement shell, cover, and arm-link boot are not a second external base. They are generated as replacement LampBase parts from rounded-rectangle polygons with proper walls, lips, screw bosses, vents, and service openings. The motor chamber remains centered and empty first; the Orin board is moved into the forward electronics bay, while the Pico WH and ST3215 driver sit in the rear electronics bay.

| Adapter file | Qty | Purpose |
| --- | ---: | --- |
| `AILamp_LampBase_Electronics_Shell.3mf` | 1 | Replacement LampBase shell for Jetson Orin Nano Super, Pico WH, ST3215 driver, wiring, and airflow |
| `AILamp_LampBase_Electronics_Cover.3mf` | 1 | Replacement LampBase cover with raised arm-mount collar, screw holes, and service vents |
| `AILamp_Base_Arm_Link_Boot.3mf` | 1 | Moving link boot between the fixed cover collar and the arm root |
| `AILamp_Cable_Clip_6mm.3mf` | 2-4 | USB and signal cable routing |
| `AILamp_Cable_Clip_10mm.3mf` | 2-4 | Power and servo cable routing |
| `AILamp_Jetson_Orin_Base_Tray.3mf` | optional | Bench-fit tray for testing the Orin outside the lamp |
| `AILamp_Electronics_Side_Deck.3mf` | optional | Bench-fit side deck for testing the Pico WH and ST3215 driver outside the lamp |

Each listed adapter `.3mf` has a matching `.stl` export with the same base name for slicer compatibility and visual checks.

Replacement base geometry:

- Replacement shell: 220 x 300 x 66 mm.
- Replacement cover plate: 220 x 300 x 5.5 mm, with a 22.5 mm total height including the raised base-arm mount collar.
- Orin thermal and cable height allowance: 34.77 mm developer-kit height + 18 mm connector bend allowance + 6 mm top air gap = 58.77 mm. The replacement shell internal height is 63 mm.
- Motor chamber keep-out: 104 x 118 mm centered around the base servo and arm root, preserved before placing electronics.
- Cooling and service openings: the shell has a 96 x 68 mm bottom intake below the Orin heat-sink zone and an 82 x 12 mm rear service opening. The cover has a 118 x 16 mm front I/O/debug slot, a 118 x 8 mm top service vent above the Orin zone, rear power and signal harness slots, and left/right USB camera/audio service slots.
- Lower service pass-through: 78 x 94 mm.
- Raised arm-mount collar: 112 x 124 x 20 mm, with a 72 x 86 mm loose center clearance around the base servo/arm root.
- Arm-mount collar screw positions: x +/-44 mm, y +/-50 mm.
- Moving base-arm link boot: 84 x 84 x 45 mm, with a 48 x 54 mm loose center clearance. In MuJoCo this boot is attached at the original LeLamp base-cover transform so it follows the arm root instead of the fixed electronics shell.
- Cover-to-shell screw positions: four corner holes at x +/-96 mm, y -106 mm and y +166 mm. The asymmetric Y positions keep the original LeLamp arm origin relationship while extending the electronics volume forward.
- Internal Orin retainers: 116 x 104 mm loose retainer pattern around the 103 x 90.5 mm developer-kit envelope. These are edge retainers for screws or straps, not through-board hole claims.
- Internal Waveshare driver standoffs: 58 x 23 mm pattern, matching the board mounting-hole spacing.

Do not print or install lamp-head adapter parts for the current replacement-base revision. Camera, LED, and audio hardware should be routed as wiring/devices first; the replacement cover already reserves cable service slots for them, while lamp-head/external holders need a separate fit pass after real cable bend radius is confirmed.

Fit rule: print the first adapter pass slightly loose. PCB pockets and board retainers include deliberate clearance, cable exits include at least 2.5 mm extra width, and retention should use screws, zip ties, or removable straps rather than hard snap-fit pressure.

For the hidden-electronics route, print `AILamp_LampBase_Electronics_Shell.3mf`, `AILamp_LampBase_Electronics_Cover.3mf`, and `AILamp_Base_Arm_Link_Boot.3mf` first at low infill for fit testing. These replace the original LeLamp base and cover in the AILamp build.
