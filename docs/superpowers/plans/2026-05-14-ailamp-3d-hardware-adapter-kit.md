# AILamp 3D Hardware Adapter Kit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a printable AILamp adapter kit for the Jetson Nano API-hybrid hardware while preserving the original seven LeLamp `.3mf` files.

**Architecture:** Add a small pure-Python geometry generator that writes simple printable `.3mf` and `.stl` adapter files from explicit hardware envelopes and clearance constants. Keep the generator separate from runtime code, add tests that enforce loose-fit clearances and original-file preservation, then add simplified MuJoCo visual geometry and bilingual docs.

**Tech Stack:** Python 3.11 standard library, pytest, 3MF XML-in-ZIP output, ASCII STL output, MuJoCo MJCF visual geoms, Markdown docs.

---

## File Structure

- Create `scripts/generate_ailamp_adapters.py`: pure-stdlib generator for adapter meshes, hardware dimensions, clearance rules, `.3mf`, and `.stl` exports.
- Create `tests/test_ailamp_adapters.py`: validates generator metadata, loose-fit dimensions, generated files, and original `.3mf` preservation.
- Modify `tests/test_assets.py`: asserts adapter outputs are present after generation.
- Modify `simulation/ailamp_scene.xml`: adds simplified visual geometry for Jetson tray, electronics deck, camera, LED matrix, and ReSpeaker mount.
- Modify `tests/test_simulation_runner.py`: validates new simulation visual names are present and model still loads when MuJoCo is installed.
- Modify `docs/en/1-3d-print.md` and `docs/zh/1-3D打印.md`: replace destructive LampHead modification wording with reversible adapter-kit assembly instructions.

## Task 1: Add Adapter Generator Tests

**Files:**
- Create: `tests/test_ailamp_adapters.py`

- [ ] **Step 1: Write failing generator metadata tests**

Create `tests/test_ailamp_adapters.py` with this content:

```python
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import zipfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "scripts/generate_ailamp_adapters.py"
ORIGINAL_PRINT_FILES = [
    ROOT / "3D/LampArm (Base-Elbow).3mf",
    ROOT / "3D/LampArm (Elbow-Wrist).3mf",
    ROOT / "3D/LampArm (Pitch).3mf",
    ROOT / "3D/LampBase - Cover.3mf",
    ROOT / "3D/LampBase.3mf",
    ROOT / "3D/LampHead - Diffuser.3mf",
    ROOT / "3D/LampHead.3mf",
]


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_ailamp_adapters", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_3mf_bounds(path: Path) -> tuple[float, float, float]:
    with zipfile.ZipFile(path) as archive:
        xml_text = archive.read("3D/3dmodel.model")
    root = ET.fromstring(xml_text)
    namespace = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    vertices = root.findall(".//m:vertex", namespace)
    xs = [float(vertex.attrib["x"]) for vertex in vertices]
    ys = [float(vertex.attrib["y"]) for vertex in vertices]
    zs = [float(vertex.attrib["z"]) for vertex in vertices]
    return max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)


def test_adapter_specs_use_loose_fit_clearances():
    generator = load_generator()

    assert generator.PCB_EDGE_CLEARANCE_MM == 1.5
    assert generator.CABLE_EXIT_EXTRA_WIDTH_MM == 2.5
    assert generator.SCREW_HOLE_EXTRA_DIAMETER_MM == 0.4
    assert generator.M12_LENS_RADIAL_CLEARANCE_MM == 1.0

    specs = {spec.name: spec for spec in generator.adapter_specs()}
    assert specs["AILamp_Jetson_Nano_Base_Tray"].board_pocket_mm == (103.0, 83.0)
    assert specs["AILamp_Electronics_Side_Deck"].board_pocket_mm == (145.0, 48.0)
    assert specs["AILamp_Head_Camera_Mount"].board_pocket_mm == (35.0, 35.0)
    assert specs["AILamp_NeoMatrix_Holder"].board_pocket_mm == (74.17, 74.17)
    assert specs["AILamp_ReSpeaker_External_Mount"].board_pocket_mm == (89.0, 38.0)


def test_generator_writes_all_adapter_files_without_touching_originals(tmp_path):
    generator = load_generator()
    before = {path: sha256(path) for path in ORIGINAL_PRINT_FILES}

    output_dir = tmp_path / "adapters"
    generator.generate_all(output_dir)

    expected_names = {
        "AILamp_Jetson_Nano_Base_Tray",
        "AILamp_Electronics_Side_Deck",
        "AILamp_Head_Camera_Mount",
        "AILamp_NeoMatrix_Holder",
        "AILamp_ReSpeaker_External_Mount",
        "AILamp_Cable_Clip_6mm",
        "AILamp_Cable_Clip_10mm",
    }
    for name in expected_names:
        path_3mf = output_dir / f"{name}.3mf"
        path_stl = output_dir / f"{name}.stl"
        assert path_3mf.exists()
        assert path_3mf.stat().st_size > 1000
        assert path_stl.exists()
        assert path_stl.stat().st_size > 1000

    jetson_bounds = parse_3mf_bounds(output_dir / "AILamp_Jetson_Nano_Base_Tray.3mf")
    assert jetson_bounds[0] >= 122.0
    assert jetson_bounds[1] >= 102.0
    assert jetson_bounds[2] >= 4.0

    neomatrix_bounds = parse_3mf_bounds(output_dir / "AILamp_NeoMatrix_Holder.3mf")
    assert neomatrix_bounds[0] >= 86.0
    assert neomatrix_bounds[1] >= 86.0

    after = {path: sha256(path) for path in ORIGINAL_PRINT_FILES}
    assert after == before
```

- [ ] **Step 2: Run the tests to verify they fail before implementation**

Run:

```bash
python3 -m pytest tests/test_ailamp_adapters.py -q
```

Expected: FAIL because `scripts/generate_ailamp_adapters.py` does not exist.

## Task 2: Implement The Pure-Python Adapter Generator

**Files:**
- Create: `scripts/generate_ailamp_adapters.py`

- [ ] **Step 1: Create generator constants, specs, and mesh writer**

Create `scripts/generate_ailamp_adapters.py` with these implementation rules:

```python
PCB_EDGE_CLEARANCE_MM = 1.5
CABLE_EXIT_EXTRA_WIDTH_MM = 2.5
SCREW_HOLE_EXTRA_DIAMETER_MM = 0.4
M12_LENS_RADIAL_CLEARANCE_MM = 1.0

JETSON_BOARD_MM = (100.0, 80.0)
PICO_BOARD_MM = (51.0, 21.0)
SERVO_DRIVER_BOARD_MM = (65.0, 30.0)
NEOMATRIX_BOARD_MM = (71.17, 71.17)
CAMERA_BOARD_MM = (32.0, 32.0)
RESPEAKER_CASE_MM = (86.0, 35.0)
```

Use a `dataclass(frozen=True)` named `AdapterSpec` with fields `name`, `outer_mm`, `board_pocket_mm`, and `description`. `adapter_specs()` must return exactly these values:

```python
[
    AdapterSpec("AILamp_Jetson_Nano_Base_Tray", (122.0, 102.0, 12.0), (103.0, 83.0), "Loose external Jetson Nano tray"),
    AdapterSpec("AILamp_Electronics_Side_Deck", (145.0, 48.0, 10.0), (145.0, 48.0), "Servo driver and Pico WH side deck"),
    AdapterSpec("AILamp_Head_Camera_Mount", (48.0, 42.0, 10.0), (35.0, 35.0), "Arducam UB0234 reversible head mount"),
    AdapterSpec("AILamp_NeoMatrix_Holder", (86.0, 86.0, 8.0), (74.17, 74.17), "Loose NeoMatrix holder behind diffuser"),
    AdapterSpec("AILamp_ReSpeaker_External_Mount", (101.0, 50.0, 9.0), (89.0, 38.0), "External ReSpeaker XVF3800 mount"),
    AdapterSpec("AILamp_Cable_Clip_6mm", (24.0, 18.0, 8.0), (8.5, 8.0), "Loose cable clip for USB and signal wires"),
    AdapterSpec("AILamp_Cable_Clip_10mm", (30.0, 22.0, 10.0), (12.5, 10.0), "Loose cable clip for power or servo bundles"),
]
```

Implement a `Mesh` class with `add_box(cx, cy, cz, sx, sy, sz)`, `write_stl(path)`, and `write_3mf(path)` methods. `write_3mf()` must create a valid 3MF ZIP with:

```text
[Content_Types].xml
_rels/.rels
3D/3dmodel.model
```

Set the 3MF model root to `unit="millimeter"`.

- [ ] **Step 2: Build loose-fit adapter geometry**

Implement one builder function per adapter. Each function returns a populated `Mesh` instance and uses only loose rails, open channels, and screw or zip-tie retention:

```python
def build_jetson_tray() -> Mesh:
    mesh = Mesh()
    return mesh


def build_electronics_side_deck() -> Mesh:
    mesh = Mesh()
    return mesh


def build_camera_mount() -> Mesh:
    mesh = Mesh()
    return mesh


def build_neomatrix_holder() -> Mesh:
    mesh = Mesh()
    return mesh


def build_respeaker_mount() -> Mesh:
    mesh = Mesh()
    return mesh


def build_cable_clip(channel_width_mm: float, outer_mm: tuple[float, float, float]) -> Mesh:
    mesh = Mesh()
    return mesh
```

Geometry rules:

- Use plates, rails, standoffs, and open cable slots built from rectangular solids.
- Do not make closed snap-fit pockets.
- Use retaining rails and zip-tie slots instead of tight friction fits.
- Keep board pockets at the dimensions in `adapter_specs()`.
- For the camera mount, use a 16 x 16 mm lens aperture area and four 3.4 x 3.4 mm square clearance holes on a 28 x 28 mm pitch.
- For cable clips, make a U-channel with internal width from `board_pocket_mm[0]`.

- [ ] **Step 3: Add generation entrypoint**

Implement:

```python
def generate_all(output_dir: Path | str = Path("3D/AILamp_Adapters")) -> list[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    builders = {
        "AILamp_Jetson_Nano_Base_Tray": build_jetson_tray,
        "AILamp_Electronics_Side_Deck": build_electronics_side_deck,
        "AILamp_Head_Camera_Mount": build_camera_mount,
        "AILamp_NeoMatrix_Holder": build_neomatrix_holder,
        "AILamp_ReSpeaker_External_Mount": build_respeaker_mount,
        "AILamp_Cable_Clip_6mm": lambda: build_cable_clip(8.5, (24.0, 18.0, 8.0)),
        "AILamp_Cable_Clip_10mm": lambda: build_cable_clip(12.5, (30.0, 22.0, 10.0)),
    }
    written = []
    for name, builder in builders.items():
        mesh = builder()
        path_3mf = output / f"{name}.3mf"
        path_stl = output / f"{name}.stl"
        mesh.write_3mf(path_3mf)
        mesh.write_stl(path_stl)
        written.extend([path_3mf, path_stl])
    return written
```

Add:

```python
if __name__ == "__main__":
    for path in generate_all():
        print(path)
```

- [ ] **Step 4: Run the adapter tests**

Run:

```bash
python3 -m pytest tests/test_ailamp_adapters.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the generator and tests**

Run:

```bash
git add scripts/generate_ailamp_adapters.py tests/test_ailamp_adapters.py
git commit -m "Add AILamp adapter generator"
```

## Task 3: Generate Adapter Files And Add Asset Checks

**Files:**
- Create: `3D/AILamp_Adapters/*.3mf`
- Create: `3D/AILamp_Adapters/*.stl`
- Modify: `tests/test_assets.py`

- [ ] **Step 1: Generate adapter files in the repository**

Run:

```bash
python3 scripts/generate_ailamp_adapters.py
```

Expected output includes:

```text
3D/AILamp_Adapters/AILamp_Jetson_Nano_Base_Tray.3mf
3D/AILamp_Adapters/AILamp_Jetson_Nano_Base_Tray.stl
3D/AILamp_Adapters/AILamp_Cable_Clip_10mm.3mf
3D/AILamp_Adapters/AILamp_Cable_Clip_10mm.stl
```

- [ ] **Step 2: Extend asset presence test**

Modify `tests/test_assets.py` by adding these assertions after the existing `3D/LampHead.3mf` assertion:

```python
    adapter_dir = root / "3D/AILamp_Adapters"
    assert (adapter_dir / "AILamp_Jetson_Nano_Base_Tray.3mf").exists()
    assert (adapter_dir / "AILamp_Electronics_Side_Deck.3mf").exists()
    assert (adapter_dir / "AILamp_Head_Camera_Mount.3mf").exists()
    assert (adapter_dir / "AILamp_NeoMatrix_Holder.3mf").exists()
    assert (adapter_dir / "AILamp_ReSpeaker_External_Mount.3mf").exists()
    assert (adapter_dir / "AILamp_Cable_Clip_6mm.3mf").exists()
    assert (adapter_dir / "AILamp_Cable_Clip_10mm.3mf").exists()
```

- [ ] **Step 3: Run asset and generator tests**

Run:

```bash
python3 -m pytest tests/test_assets.py tests/test_ailamp_adapters.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit generated printable files**

Run:

```bash
git add 3D/AILamp_Adapters tests/test_assets.py
git commit -m "Add AILamp printable adapter files"
```

## Task 4: Add MuJoCo Visual Placement Geometry

**Files:**
- Modify: `simulation/ailamp_scene.xml`
- Modify: `tests/test_simulation_runner.py`

- [ ] **Step 1: Add simulation XML presence test**

Append this test to `tests/test_simulation_runner.py`:

```python
from pathlib import Path


def test_ailamp_adapter_visuals_are_present_in_scene():
    root = Path(__file__).resolve().parents[1]
    scene = (root / "simulation/ailamp_scene.xml").read_text()

    for name in [
        "ailamp_jetson_tray_visual",
        "ailamp_electronics_deck_visual",
        "ailamp_camera_mount_visual",
        "ailamp_neomatrix_visual",
        "ailamp_respeaker_visual",
    ]:
        assert name in scene
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_simulation_runner.py::test_ailamp_adapter_visuals_are_present_in_scene -q
```

Expected: FAIL because the names are not in `simulation/ailamp_scene.xml`.

- [ ] **Step 3: Add visual-only geoms to the scene**

Modify `simulation/ailamp_scene.xml`:

Add these materials inside `<asset>`:

```xml
        <material name="ailamp_electronics_dark" rgba="0.05 0.07 0.08 1" />
        <material name="ailamp_pcb_green" rgba="0.02 0.35 0.16 1" />
        <material name="ailamp_camera_black" rgba="0.01 0.01 0.01 1" />
        <material name="ailamp_led_warm" rgba="1.0 0.72 0.32 1" />
```

Add these bodies inside `<worldbody>` after the floor geom:

```xml
        <body name="ailamp_adapter_visuals" pos="0 0 0">
            <geom name="ailamp_jetson_tray_visual" type="box" pos="-0.16 0.08 0.018" size="0.061 0.051 0.006" material="ailamp_electronics_dark" contype="0" conaffinity="0" />
            <geom name="ailamp_electronics_deck_visual" type="box" pos="0.13 0.08 0.024" size="0.0725 0.024 0.005" material="ailamp_pcb_green" contype="0" conaffinity="0" />
            <geom name="ailamp_respeaker_visual" type="box" pos="0.0 0.16 0.035" size="0.0505 0.025 0.0045" material="ailamp_electronics_dark" contype="0" conaffinity="0" />
        </body>
        <body name="ailamp_head_adapter_visuals" pos="0.02 -0.02 0.44">
            <geom name="ailamp_camera_mount_visual" type="box" pos="0.0 -0.03 0.0" size="0.024 0.005 0.021" material="ailamp_camera_black" contype="0" conaffinity="0" />
            <geom name="ailamp_neomatrix_visual" type="box" pos="0.0 -0.036 -0.035" size="0.036 0.002 0.036" material="ailamp_led_warm" contype="0" conaffinity="0" />
        </body>
```

- [ ] **Step 4: Run simulation tests**

Run:

```bash
python3 -m pytest tests/test_simulation_runner.py -q
```

Expected: PASS. If MuJoCo is installed locally, also run:

```bash
ailamp sim-viewer --render outputs/ailamp_adapters_check.png
```

Expected: writes `outputs/ailamp_adapters_check.png`.

- [ ] **Step 5: Commit simulation placement**

Run:

```bash
git add simulation/ailamp_scene.xml tests/test_simulation_runner.py
git commit -m "Add AILamp adapter visuals to simulation"
```

## Task 5: Update 3D Print Documentation

**Files:**
- Modify: `docs/en/1-3d-print.md`
- Modify: `docs/zh/1-3D打印.md`

- [ ] **Step 1: Update English 3D print guide**

In `docs/en/1-3d-print.md`, replace the `## AILamp Mechanical Changes` section with:

```markdown
## AILamp Adapter Kit

AILamp v1 preserves the original seven LeLamp `.3mf` files. The Jetson Nano 4GB hardware profile uses reversible adapter parts in `3D/AILamp_Adapters/` instead of cutting into the original base or lamp head.

| Adapter file | Qty | Purpose |
| --- | ---: | --- |
| `AILamp_Jetson_Nano_Base_Tray.3mf` | 1 | External loose-fit tray for the Jetson Nano 4GB developer kit |
| `AILamp_Electronics_Side_Deck.3mf` | 1 | Mounts the Waveshare ST3215 driver and Raspberry Pi Pico WH |
| `AILamp_Head_Camera_Mount.3mf` | 1 | Reversible Arducam UB0234 lamp-head camera mount |
| `AILamp_NeoMatrix_Holder.3mf` | 1 | Holds the Adafruit NeoMatrix behind the diffuser |
| `AILamp_ReSpeaker_External_Mount.3mf` | 1 | External ReSpeaker XVF3800 mount |
| `AILamp_Cable_Clip_6mm.3mf` | 2-4 | USB and signal cable routing |
| `AILamp_Cable_Clip_10mm.3mf` | 2-4 | Power and servo cable routing |

Fit rule: print the first adapter pass slightly loose. PCB pockets include 1.5 mm clearance per side, cable exits include at least 2.5 mm extra width, and retention should use screws, zip ties, or removable straps rather than hard snap-fit pressure.
```

- [ ] **Step 2: Update Chinese 3D print guide**

In `docs/zh/1-3D打印.md`, replace the final AILamp camera sentence with:

```markdown
## AILamp 适配件套件

AILamp v1 保留 LeLamp 原始 7 个 `.3mf` 文件，不直接切割原始底座或灯头。Jetson Nano 4GB 硬件配置使用 `3D/AILamp_Adapters/` 中的可逆适配件。

| 适配件文件 | 数量 | 用途 |
| --- | ---: | --- |
| `AILamp_Jetson_Nano_Base_Tray.3mf` | 1 | Jetson Nano 4GB 外置宽松托盘 |
| `AILamp_Electronics_Side_Deck.3mf` | 1 | 固定 Waveshare ST3215 驱动板和 Raspberry Pi Pico WH |
| `AILamp_Head_Camera_Mount.3mf` | 1 | 可逆安装 Arducam UB0234 摄像头 |
| `AILamp_NeoMatrix_Holder.3mf` | 1 | 将 Adafruit NeoMatrix 固定在扩散片后方 |
| `AILamp_ReSpeaker_External_Mount.3mf` | 1 | 外置固定 ReSpeaker XVF3800 |
| `AILamp_Cable_Clip_6mm.3mf` | 2-4 | USB 和信号线走线 |
| `AILamp_Cable_Clip_10mm.3mf` | 2-4 | 电源线和舵机线走线 |

尺寸规则：第一版适配件不要卡太死。PCB 口袋每边预留 1.5 mm，线缆出口至少额外预留 2.5 mm，固定优先使用螺丝、扎带或可拆绑带，不依赖硬卡扣挤压板子。
```

- [ ] **Step 3: Run docs tests**

Run:

```bash
python3 -m pytest tests/test_docs.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit docs update**

Run:

```bash
git add docs/en/1-3d-print.md docs/zh/1-3D打印.md
git commit -m "Document AILamp printable adapter kit"
```

## Task 6: Final Verification

**Files:**
- Read-only verification across the project

- [ ] **Step 1: Run focused tests**

Run:

```bash
python3 -m pytest tests/test_ailamp_adapters.py tests/test_assets.py tests/test_simulation_runner.py tests/test_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full tests**

Run:

```bash
python3 -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Check whitespace**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 4: Inspect original 3MF preservation**

Run:

```bash
git diff --name-only HEAD~4..HEAD -- 3D | grep '^3D/Lamp' || true
```

Expected: no output, confirming the original LeLamp print files were not changed by these commits.

- [ ] **Step 5: Summarize output files**

Run:

```bash
find 3D/AILamp_Adapters -maxdepth 1 -type f | sort
```

Expected: seven `.3mf` files and seven `.stl` files.
