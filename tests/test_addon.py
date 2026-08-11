import tomllib
from pathlib import Path

import pytest

import my_blender_plugin
from my_blender_plugin import costume, operators, panels

REPO = Path(__file__).resolve().parent.parent


def test_bl_info():
    info = my_blender_plugin.bl_info
    assert info["name"]
    assert isinstance(info["version"], tuple)


def test_register_unregister():
    # フェイク bpy 上で register/unregister が例外なく通ることを確認する
    my_blender_plugin.register()
    my_blender_plugin.unregister()


def test_version_matches_the_extension_manifest():
    """上げ忘れると Blender 側で更新が検知されないので両方を突き合わせる"""
    with open(REPO / "my_blender_plugin" / "blender_manifest.toml", "rb") as handle:
        manifest = tomllib.load(handle)
    assert manifest["version"] == ".".join(
        str(part) for part in my_blender_plugin.bl_info["version"]
    )


def test_costume_is_not_in_modules():
    """costume は register を持たないので _modules に入れると AttributeError になる"""
    assert costume not in my_blender_plugin._modules
    assert not hasattr(costume, "register")


def test_costume_reload_all_is_safe_before_import():
    """まだ import されていないサブモジュールを触らないこと(bpy依存モジュール対策)"""
    costume.reload_all()


def test_costume_reload_order_covers_every_submodule():
    listed = set(costume._MODULE_NAMES)
    on_disk = {
        path.stem
        for path in (REPO / "my_blender_plugin" / "costume").glob("*.py")
        if path.stem != "__init__"
    }
    assert listed == on_disk


def test_costume_package_does_not_import_bpy_dependent_modules():
    """__init__.py が build/materials を再エクスポートすると純粋関数のテストが落ちる"""
    import sys

    assert "my_blender_plugin.costume.build" not in sys.modules
    assert "my_blender_plugin.costume.materials" not in sys.modules


def test_generate_costume_operator_is_registered():
    assert operators.MYPLUGIN_OT_generate_costume in operators._classes
    assert operators.MYPLUGIN_OT_generate_costume.bl_idname == "myplugin.generate_costume"
    assert operators.MYPLUGIN_OT_generate_costume.bl_description


@pytest.mark.parametrize("cls", operators._classes)
def test_every_operator_has_a_description_and_undo(cls):
    assert cls.bl_description, cls.__name__
    assert "REGISTER" in cls.bl_options, cls.__name__
    assert "UNDO" in cls.bl_options, cls.__name__


@pytest.mark.parametrize("cls", operators._classes)
def test_operator_naming_convention(cls):
    assert cls.__name__.startswith("MYPLUGIN_OT_"), cls.__name__
    assert cls.bl_idname.startswith("myplugin."), cls.bl_idname


def test_panel_exposes_the_costume_operator():
    source = (REPO / "my_blender_plugin" / "panels.py").read_text(encoding="utf-8")
    assert "MYPLUGIN_OT_generate_costume" in source
    assert panels.MYPLUGIN_PT_main.bl_category == "My Plugin"


def test_tools_are_not_inside_the_addon_package():
    """tools/ は配布 zip に入らない位置に置く(build.sh は my_blender_plugin/ だけを固める)"""
    assert (REPO / "tools" / "generate.py").is_file()
    assert not (REPO / "my_blender_plugin" / "tools").exists()


def test_presets_live_inside_the_addon_package():
    """プリセットは配布 zip に入る位置に置く"""
    presets = REPO / "my_blender_plugin" / "costume" / "presets"
    assert sorted(path.name for path in presets.glob("*.json"))
