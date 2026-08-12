"""パンツ(K2 = 分岐の溶接)の3層テスト。

定義は docs/garments.md のパンツの節。K1 で表現できない唯一の形なので、
カーネル(weld_meshes)のテストもここに置く。
"""

import copy
import math

import pytest

from my_blender_plugin.costume import (
    kernels,
    modulate,
    parse_text,
    parts,
    spec as spec_module,
    validate,
)


def build_pants(overrides=None):
    normalized = spec_module.load_preset("pants")
    if overrides:
        for part in normalized["parts"]:
            part["params"].update(overrides.get(part["name"], {}))
        normalized = spec_module.normalize_spec(normalized)
    return normalized, parts.build_all(normalized)


def pants_entry(built, normalized):
    report = validate.costume_report(built, normalized)
    return report, next(e for e in report["parts"] if e["part"] == "Pants_Body")


# ---------------------------------------- 層1: 純粋関数


def test_leg_profile_holds_the_thigh_then_tapers():
    thigh, knee, hem = 0.09, 0.06, 0.064
    assert modulate.leg_radius_profile(0.0, thigh, knee, hem) == thigh
    assert modulate.leg_radius_profile(modulate.THIGH_HOLD_T, thigh, knee, hem) == thigh
    assert modulate.leg_radius_profile(modulate.KNEE_T, thigh, knee, hem) == pytest.approx(knee)
    assert modulate.leg_radius_profile(1.0, thigh, knee, hem) == pytest.approx(hem)
    with pytest.raises(ValueError):
        modulate.leg_radius_profile(1.1, thigh, knee, hem)


def _tiny_loft(shift_x):
    ring = [(shift_x + x, y, 0.0) for x, y in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))]
    lower = [(x, y, -1.0) for x, y, _z in ring]
    return kernels.loft_rings([ring, lower], closed=True)


def test_weld_meshes_shares_identical_vertices():
    a = _tiny_loft(0.0)
    b = _tiny_loft(1.0)  # x=1.0 の2頂点×2リングが a と一致する
    welded, maps = kernels.weld_meshes([a, b], name="welded")
    assert len(welded.verts) == len(a.verts) + len(b.verts) - 4
    assert len(welded.quads) == len(a.quads) + len(b.quads)
    # maps で引いた頂点は元の座標そのもの
    for piece, mapping in ((a, maps[0]), (b, maps[1])):
        for local, merged in enumerate(mapping):
            assert welded.verts[merged] == piece.verts[local]


def test_weld_meshes_packs_uv_islands_side_by_side():
    a = _tiny_loft(0.0)
    b = _tiny_loft(5.0)
    welded, _maps = kernels.weld_meshes([a, b], name="welded")
    first = [uv for loop in welded.uv_loops[: len(a.quads)] for uv in loop]
    second = [uv for loop in welded.uv_loops[len(a.quads):] for uv in loop]
    assert max(u for u, _v in first) <= 0.5 + 1e-9
    assert min(u for u, _v in second) >= 0.5 - 1e-9


# ---------------------------------------- 層2: 組み上がり


def test_pants_pass_every_hard_gate():
    normalized, built = build_pants()
    report, entry = pants_entry(built, normalized)
    assert report["failed"] == [], report["failed"]


def test_pants_have_one_waist_and_two_hems():
    normalized, built = build_pants()
    _report, entry = pants_entry(built, normalized)
    assert entry["boundary_loops"] == 3
    left = entry["measured_hem_perimeter_l"]
    right = entry["measured_hem_perimeter_r"]
    assert left == pytest.approx(right, rel=1e-6)


def test_pants_measures_follow_the_sizing_table():
    normalized, built = build_pants()
    _report, entry = pants_entry(built, normalized)
    table = built["sizing"]
    assert entry["measured_rise"] == pytest.approx(table["rise"], rel=0.05)
    assert entry["measured_inseam_l"] == pytest.approx(table["inseam"], rel=0.03)
    assert entry["measured_thigh_perimeter_l"] == pytest.approx(
        table["thigh"] * 1.15, rel=0.05
    )


def test_shorts_are_just_a_scale_change():
    """ハーフパンツは inseam_scale だけで成立する(新コード不要)"""
    normalized, built = build_pants({"Pants_Body": {"inseam_scale": 0.35}})
    report, _entry = pants_entry(built, normalized)
    assert report["failed"] == [], report["failed"]


def test_waistband_shares_the_pants_waist_ring():
    normalized, built = build_pants()
    report, _entry = pants_entry(built, normalized)
    joint = report["joints"][0]
    assert joint["ok"], joint


def test_parse_reaches_the_pants_preset():
    assert parse_text.parse("黒いズボン")["base_preset"] == "pants"
    assert parse_text.parse("スラックス")["base_preset"] == "pants"


def test_the_thigh_gate_survives_a_coarse_leg_grid():
    """保持区間 [blend, THIGH_HOLD_T] に行が乗らない粗い割りでも thigh ゲートが
    消えないこと(消えると「針の脚」の網に穴が開く。PR #8 レビュー)。
    設計値はその行のプロファイル値に切り替わる"""
    for leg_rings in (3, 4, 6):
        normalized, built = build_pants({"Pants_Body": {"leg_rings": leg_rings}})
        report, entry = pants_entry(built, normalized)
        assert "pants_thigh_l" in entry["hard"], leg_rings
        assert report["failed"] == [], (leg_rings, report["failed"])


# ---------------------------------------- 層3a: spec を壊すとゲートが落ちる


def test_segments_must_be_a_multiple_of_four():
    spec = spec_module.load_preset("pants")
    body = next(part for part in spec["parts"] if part["type"] == "pants")
    body["params"]["segments"] = 30
    with pytest.raises(spec_module.SpecError) as error:
        spec_module.normalize_spec(spec)
    assert "4の倍数" in str(error.value)


# ---------------------------------------- 層3b: メッシュだけ壊すとゲートが落ちる


def test_lifting_one_hem_in_the_mesh_breaks_the_inseam_gate():
    normalized, built = build_pants()
    pants = next(mesh for mesh in built["parts"] if mesh.name == "Pants_Body")
    tampered = copy.deepcopy(pants)
    hem = set(tampered.rings["hem_l"])
    lift = tampered.design["pants_inseam"] * 0.2
    tampered.verts = [
        (x, y, z + lift) if index in hem else (x, y, z)
        for index, (x, y, z) in enumerate(tampered.verts)
    ]
    after = validate.part_report(tampered, 1.58)
    assert "pants_inseam_l" in after["failed"]


def test_shifting_a_leg_in_the_mesh_breaks_the_symmetry_gate():
    normalized, built = build_pants()
    pants = next(mesh for mesh in built["parts"] if mesh.name == "Pants_Body")
    tampered = copy.deepcopy(pants)
    hem = set(tampered.rings["hem_r"])
    shift = abs(tampered.verts[tampered.rings["hem_l"][0]][0])
    tampered.verts = [
        (x + shift, y, z) if index in hem else (x, y, z)
        for index, (x, y, z) in enumerate(tampered.verts)
    ]
    after = validate.part_report(tampered, 1.58)
    assert "pants_legs_symmetric" in after["failed"]


def test_shrinking_the_thigh_in_the_mesh_breaks_the_thigh_gate():
    """全長を細めた「針の脚」を thigh ゲートが捕まえる(袖の円錐ゲートと同じ狙い)"""
    normalized, built = build_pants()
    pants = next(mesh for mesh in built["parts"] if mesh.name == "Pants_Body")
    tampered = copy.deepcopy(pants)
    thigh = set(tampered.rings["thigh_l"])
    xs = [tampered.verts[index][0] for index in thigh]
    ys = [tampered.verts[index][1] for index in thigh]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    tampered.verts = [
        (cx + (x - cx) * 0.7, cy + (y - cy) * 0.7, z) if index in thigh else (x, y, z)
        for index, (x, y, z) in enumerate(tampered.verts)
    ]
    after = validate.part_report(tampered, 1.58)
    assert "pants_thigh_l" in after["failed"]
