"""ベスト(袖なし bodice)のプリセットが既存パーツだけで成立することの検証。

新しいコードは書いていない。「新しい衣装 = 新しいパーツの組み合わせ」の最小例。
"""

from my_blender_plugin.costume import parse_text, parts, spec as spec_module, validate


def build_vest():
    normalized = spec_module.load_preset("vest")
    return normalized, parts.build_all(normalized)


def test_vest_passes_every_hard_gate():
    normalized, built = build_vest()
    report = validate.costume_report(built, normalized)
    assert report["failed"] == [], report["failed"]


def test_vest_has_no_sleeves_and_keeps_open_armholes():
    normalized, built = build_vest()
    types = [part["type"] for part in normalized["parts"]]
    assert "sleeve" not in types
    bodice = next(mesh for mesh in built["parts"] if mesh.name == "Vest_Bodice")
    # 袖ぐりの穴は開いたまま残る(ベストとして正しい)。境界ループは外周+穴2つ
    assert "armhole_l" in bodice.rings and "armhole_r" in bodice.rings
    assert bodice.design["boundary_loops"] == 3


def test_sleeve_gates_do_not_fire_without_sleeves():
    """袖のゲートは design の sleeve_length_target で発火する。袖が無ければ発火しない"""
    normalized, built = build_vest()
    report = validate.costume_report(built, normalized)
    for entry in report["parts"]:
        assert not any("sleeve" in name for name in entry["failed"])


def test_vest_words_reach_the_vest_preset():
    result = parse_text.parse("黒いベスト")
    assert result["base_preset"] == "vest"
    normalized = spec_module.normalize_spec(result["spec"])
    report = validate.costume_report(parts.build_all(normalized), normalized)
    assert report["failed"] == []
    assert parse_text.parse("gilet風のジレ")["base_preset"] == "vest"
