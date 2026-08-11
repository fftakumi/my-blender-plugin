"""フード(首の縫い目から頭を包むシート)の3層テスト。

定義は docs/garments.md のフードの節。
"""

import copy

import pytest

from my_blender_plugin.costume import (
    parse_text,
    parts,
    sizing,
    spec as spec_module,
    validate,
)


def build_hooded_cape(overrides=None):
    normalized = spec_module.load_preset("hooded_cape")
    if overrides:
        for part in normalized["parts"]:
            part["params"].update(overrides.get(part["name"], {}))
        normalized = spec_module.normalize_spec(normalized)
    return normalized, parts.build_all(normalized)


def hood_entry(built, normalized):
    report = validate.costume_report(built, normalized)
    return report, next(e for e in report["parts"] if e["part"] == "Cape_Hood")


# ---------------------------------------- 層1: サイズ表


def test_sizing_carries_the_head_measures():
    table = sizing.resolve(1.58)
    # 頭囲 55cm / 全頭高 22cm 前後(公表値を丸めた設計値)
    assert table["head_circumference"] == pytest.approx(0.553, abs=0.01)
    assert table["head_height"] == pytest.approx(0.221, abs=0.01)


def test_head_measures_do_not_take_global_ease():
    """ゆとりは周長4項目(bust/waist/hip/neck)だけ。頭囲には掛からない"""
    plain = sizing.resolve(1.58, ease=0.0)
    eased = sizing.resolve(1.58, ease=0.5)
    assert plain["head_circumference"] == eased["head_circumference"]


# ---------------------------------------- 層2: 組み上がり


def test_hooded_cape_passes_every_hard_gate():
    normalized, built = build_hooded_cape()
    report, entry = hood_entry(built, normalized)
    assert report["failed"] == [], report["failed"]


def test_hood_seam_sits_exactly_on_the_cape():
    """リング0が host の頂点そのもの = shared joint のずれゼロ"""
    normalized, built = build_hooded_cape()
    report, _entry = hood_entry(built, normalized)
    joint = report["joints"][0]
    assert joint["ok"], joint


def test_hood_wraps_and_covers_the_head():
    normalized, built = build_hooded_cape()
    _report, entry = hood_entry(built, normalized)
    table = built["sizing"]
    assert entry["measured_hood_max_arc"] >= table["head_circumference"] * 1.15 * 0.75
    assert entry["measured_hood_depth"] == pytest.approx(
        table["head_height"] * 1.25, rel=0.05
    )


def test_parse_reaches_the_hooded_preset():
    assert parse_text.parse("黒いパーカー")["base_preset"] == "hooded_cape"
    assert parse_text.parse("フード付きのマント")["base_preset"] == "hooded_cape"


# ---------------------------------------- 層3a: spec を壊すとゲートが落ちる


def test_the_face_gap_floor_scales_with_the_requested_opening():
    """開口の下限(設計弦長の半分)が spec の開口角に追従すること。
    高さ側の 3a は design と実装が同じ倍率を共有するので作れない —
    メッシュだけ潰す 3b(下のテスト)が高さの網"""
    normalized, built = build_hooded_cape({"Cape_Hood": {"face_open_degrees": 200.0}})
    _report, wide = hood_entry(built, normalized)
    normalized, built = build_hooded_cape({"Cape_Hood": {"face_open_degrees": 60.0}})
    _report, narrow = hood_entry(built, normalized)
    assert wide["design"]["hood_face_gap_floor"] > narrow["design"]["hood_face_gap_floor"]
    assert wide["failed"] == [] and narrow["failed"] == []


# ---------------------------------------- 層3b: メッシュだけ壊すとゲートが落ちる


def test_squashing_the_hood_in_the_mesh_breaks_the_depth_gate():
    """design はそのまま、頂点の z だけ縫い目へ潰す → hood_depth が捕まえる"""
    normalized, built = build_hooded_cape()
    hood = next(mesh for mesh in built["parts"] if mesh.name == "Cape_Hood")
    tampered = copy.deepcopy(hood)
    seam_top = max(
        tampered.verts[index][2] for index in tampered.rings["bottom"]
    )
    tampered.verts = [
        (x, y, seam_top + (z - seam_top) * 0.4) if z > seam_top else (x, y, z)
        for x, y, z in tampered.verts
    ]
    after = validate.part_report(tampered, 1.58)
    assert "hood_depth" in after["failed"]


def test_shrinking_the_hood_in_the_mesh_breaks_the_wrap_gate():
    """半径を縮めた「頭の入らないフード」を hood_wraps_the_head が捕まえる"""
    normalized, built = build_hooded_cape()
    hood = next(mesh for mesh in built["parts"] if mesh.name == "Cape_Hood")
    tampered = copy.deepcopy(hood)
    seam = set(tampered.rings["bottom"])
    cx = sum(tampered.verts[i][0] for i in seam) / len(seam)
    cy = sum(tampered.verts[i][1] for i in seam) / len(seam)
    tampered.verts = [
        (cx + (x - cx) * 0.6, cy + (y - cy) * 0.6, z) if index not in seam else (x, y, z)
        for index, (x, y, z) in enumerate(tampered.verts)
    ]
    after = validate.part_report(tampered, 1.58)
    assert "hood_wraps_the_head" in after["failed"]
