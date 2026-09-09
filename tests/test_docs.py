from pathlib import Path

from ailamp.models import VisionEventType


ADAPTER_PRINT_FILES = {
    "AILamp_LampBase_Electronics_Shell.3mf",
    "AILamp_LampBase_Electronics_Cover.3mf",
    "AILamp_Base_Arm_Link_Boot.3mf",
    "AILamp_Jetson_Orin_Base_Tray.3mf",
    "AILamp_Electronics_Side_Deck.3mf",
    "AILamp_Cable_Clip_6mm.3mf",
    "AILamp_Cable_Clip_10mm.3mf",
}

ADAPTER_VISUAL_NAMES = {
    "ailamp_integrated_base_shell_visual",
    "ailamp_integrated_base_cover_visual",
    "ailamp_base_arm_link_boot_visual",
    "ailamp_cable_clip_6mm_visual",
    "ailamp_cable_clip_10mm_visual",
}


def test_docs_do_not_reference_missing_motion_names():
    root = Path(__file__).resolve().parents[1]
    docs_text = "\n".join(path.read_text() for path in (root / "docs").rglob("*.md"))

    assert "look_left" not in docs_text
    assert "look_right" not in docs_text


def test_docs_list_all_vision_event_types():
    root = Path(__file__).resolve().parents[1]
    docs_text = "\n".join(path.read_text() for path in (root / "docs").rglob("*.md"))

    for event_type in VisionEventType:
        assert event_type.value in docs_text


def test_assembly_docs_include_adapter_installation_and_fit_rules():
    root = Path(__file__).resolve().parents[1]
    english = (root / "docs/en/3-assembly.md").read_text()
    chinese = (root / "docs/zh/3-装配.md").read_text()

    for docs_text in (english, chinese):
        for filename in ADAPTER_PRINT_FILES:
            assert filename in docs_text

    assert "Do not cut `LampHead.3mf`" in english
    assert "不要切割 `LampHead.3mf`" in chinese
    assert "slightly loose" in english
    assert "不要卡死" in chinese
    assert "AILamp base replacement grows the base to 220 x 300 x 66 mm" in (
        root / "docs/en/1-3d-print.md"
    ).read_text()
    assert "AILamp 替换底座扩大到 220 x 300 x 66 mm" in (
        root / "docs/zh/1-3D打印.md"
    ).read_text()
    assert "replace the original base" in english
    assert "替代原底座" in chinese
    assert "x +/-96 mm, y -106 mm and y +166 mm" in english
    assert "x 方向 +/-96 mm、y 方向 -106 mm 和 +166 mm" in chinese
    assert "raised arm-mount collar" in english
    assert "凸起灯臂固定座" in chinese
    assert "112 x 124 x 20 mm" in (root / "docs/en/1-3d-print.md").read_text()
    assert "112 x 124 x 20 mm" in (root / "docs/zh/1-3D打印.md").read_text()
    assert "116 x 104 mm" in english
    assert "116 x 104 mm" in chinese
    assert "58 x 23 mm" in english
    assert "58 x 23 mm" in chinese
    assert "ailamp hardware-check" in english
    assert "ailamp hardware-check" in chinese
    assert "ailamp led-test" in english
    assert "ailamp led-test" in chinese
    assert "ailamp vision-demo" in english
    assert "ailamp vision-demo" in chinese
    assert "ailamp agent" in english
    assert "ailamp agent" in chinese


def test_simulation_docs_list_adapter_visuals():
    root = Path(__file__).resolve().parents[1]
    docs_text = "\n".join(
        [
            (root / "docs/en/5-control-vision-simulation.md").read_text(),
            (root / "docs/zh/5-控制视觉仿真.md").read_text(),
        ]
    )

    for visual_name in ADAPTER_VISUAL_NAMES:
        assert visual_name in docs_text


def test_top_level_docs_describe_adapter_kit():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text()
    notice = (root / "NOTICE.md").read_text()

    assert "3D/AILamp_Adapters/" in readme
    assert "2-manifold" in readme
    assert "Generated AILamp adapter kit" in notice
    assert "replacement-base STL mesh visuals for the selected Jetson electronics layout" in notice


def test_assembly_and_runtime_docs_include_nano_acceptance_flow():
    root = Path(__file__).resolve().parents[1]
    docs_text = "\n".join(
        [
            (root / "docs/en/3-assembly.md").read_text(),
            (root / "docs/zh/3-装配.md").read_text(),
            (root / "docs/en/4-runtime-setup.md").read_text(),
            (root / "docs/zh/4-运行环境.md").read_text(),
            (root / "docs/en/5-control-vision-simulation.md").read_text(),
            (root / "docs/zh/5-控制视觉仿真.md").read_text(),
        ]
    )

    required_phrases = [
        "runtime-check",
        "scripts/check_jetson_nano_environment.sh",
        "JetPack 4.6",
        "Ubuntu 18.04",
        "sim-check",
        "web-control --brain --vision --with-outputs",
        "Jetson Nano",
        "Do not run `--with-outputs` before `led-test` and `motor-test` pass",
        "不要在 `led-test` 和 `motor-test` 通过前运行 `--with-outputs`",
        "mechanical fit",
        "power checks",
        "软件验收",
    ]
    for phrase in required_phrases:
        assert phrase in docs_text


def test_adapter_design_docs_match_current_geometry_scope():
    root = Path(__file__).resolve().parents[1]
    design_spec = (
        root / "docs/superpowers/specs/2026-05-14-ailamp-3d-hardware-adapter-kit-design.md"
    ).read_text()
    english_print = (root / "docs/en/1-3d-print.md").read_text()
    chinese_print = (root / "docs/zh/1-3D打印.md").read_text()

    for stale_phrase in (
        "slight downward viewing angle",
        "lens cylinder",
        "rounded internal channels",
    ):
        assert stale_phrase not in design_spec

    assert "matching `.stl` export" in english_print
    assert "同名 `.stl` 导出文件" in chinese_print


def test_adapter_docs_do_not_reintroduce_destructive_original_part_edits():
    root = Path(__file__).resolve().parents[1]
    docs_text = "\n".join(
        path.read_text()
        for path in [
            root / "docs/en/1-3d-print.md",
            root / "docs/en/3-assembly.md",
            root / "docs/zh/1-3D打印.md",
            root / "docs/zh/3-装配.md",
        ]
    )

    forbidden_phrases = [
        "Add an Arducam UB0234 opening in `LampHead.3mf`",
        "cut `LampBase.3mf`",
        "cut `LampHead.3mf`",
        "drill `LampBase.3mf`",
        "drill `LampHead.3mf`",
        "modify original `.3mf`",
        "切割 `LampBase.3mf`",
        "切割 `LampHead.3mf`",
        "钻孔 `LampBase.3mf`",
        "钻孔 `LampHead.3mf`",
        "修改原始 `.3mf`",
    ]
    allowed_phrases = {
        "Do not cut `LampHead.3mf`",
        "不要切割 `LampHead.3mf`",
    }

    safe_text = docs_text
    for phrase in allowed_phrases:
        safe_text = safe_text.replace(phrase, "")

    for phrase in forbidden_phrases:
        assert phrase not in safe_text
