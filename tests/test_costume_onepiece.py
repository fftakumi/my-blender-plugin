"""ワンピース(bodice + skirt_body の sewn 縫合)のテスト。

新 builder は無い。「新しい衣装 = 既存パーツの組み合わせ + hem_style 拡張」の検証。
定義は docs/garments.md のワンピースの節。
"""

import pytest

from my_blender_plugin.costume import parse_text, parts, spec as spec_module, validate


def build_onepiece():
    normalized = spec_module.load_preset("onepiece")
    return normalized, parts.build_all(normalized)


def test_onepiece_passes_every_hard_gate():
    normalized, built = build_onepiece()
    report = validate.costume_report(built, normalized)
    assert report["failed"] == [], report["failed"]


def test_bodice_and_skirt_are_sewn_at_the_waist():
    normalized, built = build_onepiece()
    report = validate.costume_report(built, normalized)
    seam = next(
        joint
        for joint in report["joints"]
        if joint.get("b") == "Dress_Skirt" or joint.get("a") == "Dress_Skirt"
    )
    assert seam["ok"], seam


def test_flat_hem_is_actually_level():
    normalized, built = build_onepiece()
    bodice = next(mesh for mesh in built["parts"] if mesh.name == "Dress_Bodice")
    hem = [bodice.verts[index][2] for index in bodice.rings["bottom"]]
    assert max(hem) - min(hem) < 1e-9


def test_flat_hem_drops_the_shirttail_keys_but_shirttail_keeps_them():
    """ゲートのスイッチは design のキーの有無。flat では消え、既定では残る"""
    normalized, built = build_onepiece()
    bodice = next(mesh for mesh in built["parts"] if mesh.name == "Dress_Bodice")
    assert "shirttail_drop" not in bodice.design

    blouse = spec_module.load_preset("blouse")
    blouse_built = parts.build_all(blouse)
    blouse_bodice = next(
        mesh for mesh in blouse_built["parts"] if mesh.name == "Blouse_Bodice"
    )
    assert "shirttail_drop" in blouse_bodice.design


def test_blouse_still_passes_after_the_hem_style_change():
    """回帰ガード: hem_style の追加でブラウスの挙動が変わっていないこと"""
    normalized = spec_module.load_preset("blouse")
    built = parts.build_all(normalized)
    report = validate.costume_report(built, normalized)
    assert report["failed"] == [], report["failed"]


def test_hem_style_rejects_unknown_values():
    spec = spec_module.load_preset("onepiece")
    bodice = next(part for part in spec["parts"] if part["type"] == "bodice")
    bodice["params"]["hem_style"] = "wavy"
    with pytest.raises(spec_module.SpecError):
        spec_module.normalize_spec(spec)


def test_parse_reaches_the_onepiece_preset():
    assert parse_text.parse("白いワンピース")["base_preset"] == "onepiece"
    assert parse_text.parse("紺のドレス")["base_preset"] == "onepiece"
