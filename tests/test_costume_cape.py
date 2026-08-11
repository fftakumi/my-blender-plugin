"""ケープ(前の開いた広がるシート)の3層テスト。

層1: 純粋関数(ellipse_arc)/ 層2: 組み上がりのゲート /
層3: ゲートが本当に欠陥を捕まえるか(spec を壊す・メッシュだけ壊す)。
定義は docs/garments.md のケープの節。
"""

import copy
import math

import pytest

from my_blender_plugin.costume import (
    modulate,
    parse_text,
    parts,
    spec as spec_module,
    validate,
)


def build_cape(overrides=None):
    normalized = spec_module.load_preset("cape")
    if overrides:
        for part in normalized["parts"]:
            part["params"].update(overrides.get(part["name"], {}))
        normalized = spec_module.normalize_spec(normalized)
    return normalized, parts.build_all(normalized)


def gates(built, normalized, name):
    report = validate.costume_report(built, normalized)
    return next(entry for entry in report["parts"] if entry["part"] == name)


# ---------------------------------------- 層1: 純粋関数


def test_ellipse_arc_of_the_full_circle_matches_the_perimeter():
    length = modulate.ellipse_arc(1.0, 0.68, 0.0, 2.0 * math.pi)
    assert length == pytest.approx(modulate.ellipse_perimeter(1.0, 0.68), rel=1e-4)


def test_ellipse_arc_is_denser_at_the_front():
    """楕円は弧長が角度に比例しない。長半径側(前後軸の左右)の弧が濃い"""
    front = modulate.ellipse_arc(1.0, 0.5, -math.pi / 4, math.pi / 4)
    side = modulate.ellipse_arc(1.0, 0.5, math.pi / 4, 3.0 * math.pi / 4)
    assert front != pytest.approx(side, rel=0.05)


def test_ellipse_arc_rejects_a_reversed_range():
    with pytest.raises(ValueError):
        modulate.ellipse_arc(1.0, 0.68, 1.0, 0.0)


# ---------------------------------------- 層2: 組み上がり


def test_cape_passes_every_hard_gate():
    normalized, built = build_cape()
    report = validate.costume_report(built, normalized)
    assert report["failed"] == [], report["failed"]


def test_cape_top_arc_matches_the_neck_measure():
    normalized, built = build_cape()
    entry = gates(built, normalized, "Cape_Body")
    table = built["sizing"]
    # 上端の弧長は「首回り×(1+ゆとり)の周長」から前開きの楔を除いた量に一致する
    assert entry["measured_top_arc"] < table["neck"] * (1.0 + 0.35)
    assert entry["hard"]["cape_top_arc"]["ok"]


def test_a_wide_open_cape_still_passes_the_arc_gate():
    """開き角を大きくしても製図値(楕円弧の数値積分)が追従すること。
    「周長×角度割合」の近似だと開き90°で3%を超えて落ちていた"""
    normalized, built = build_cape({"Cape_Body": {"front_open_degrees": 90.0}})
    entry = gates(built, normalized, "Cape_Body")
    assert entry["failed"] == [], entry["failed"]


def test_cape_collar_sits_exactly_on_the_cape():
    """cape の top リングが collar の host としてそのまま使えること(継ぎ目ゼロ)"""
    normalized, built = build_cape()
    report = validate.costume_report(built, normalized)
    joint = report["joints"][0]
    assert joint["ok"], joint


def test_parse_reaches_the_cape_preset():
    assert parse_text.parse("赤いマント")["base_preset"] == "cape"
    assert parse_text.parse("cape")["base_preset"] == "cape"


# ---------------------------------------- 層3a: spec を壊すとゲートが落ちる


def test_a_cape_without_flare_is_not_a_cape():
    normalized, built = build_cape({"Cape_Body": {"flare": 1.0}})
    entry = gates(built, normalized, "Cape_Body")
    assert "cape_flares_from_the_shoulder" in entry["failed"]


# ---------------------------------------- 層3b: メッシュだけ壊すとゲートが落ちる


def test_shrinking_the_hem_in_the_mesh_breaks_the_flare_gate():
    """design はそのまま、裾のリングの頂点だけ縮める → 実測系ゲートが捕まえる"""
    normalized, built = build_cape()
    cape = next(mesh for mesh in built["parts"] if mesh.name == "Cape_Body")
    tampered = copy.deepcopy(cape)
    bottom = set(tampered.rings["bottom"])
    tampered.verts = [
        (x * 0.55, y * 0.55, z) if index in bottom else (x, y, z)
        for index, (x, y, z) in enumerate(tampered.verts)
    ]
    after = validate.part_report(tampered, 1.58)
    assert "cape_flare_matches_spec" in after["failed"]


def test_closing_the_front_in_the_mesh_breaks_the_open_gate():
    """前開きの端点をくっつける → cape_front_open が捕まえる"""
    normalized, built = build_cape()
    cape = next(mesh for mesh in built["parts"] if mesh.name == "Cape_Body")
    tampered = copy.deepcopy(cape)
    verts = list(tampered.verts)
    for ring in (tampered.rings["top"], tampered.rings["bottom"]):
        first, last = ring[0], ring[-1]
        # 端点同士を同じ場所へ寄せる(前を閉じる)
        middle = tuple((a + b) / 2.0 for a, b in zip(verts[first], verts[last]))
        verts[first] = middle
        verts[last] = middle
    tampered.verts = verts
    after = validate.part_report(tampered, 1.58)
    assert "cape_front_open" in after["failed"]
