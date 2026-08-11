import json
import os

import pytest

from my_blender_plugin.costume import parts, spec as spec_module, validate

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def build(preset):
    normalized = spec_module.load_preset(preset)
    return normalized, parts.build_all(normalized)


def test_every_spec_part_type_has_a_builder():
    assert set(parts.BUILDERS) == set(spec_module.PART_SCHEMAS)


def test_skirt_body_top_matches_the_sizing_table():
    _normalized, built = build("skirt_flare")
    table = built["sizing"]
    body = next(mesh for mesh in built["parts"] if mesh.name == "Skirt_Body")
    # 上端はゆとり込みのウエスト寸法そのもの(周長で扱う)
    assert body.design["top_perimeter"] == pytest.approx(table["waist"])


def test_skirt_body_hem_reflects_flare():
    normalized, built = build("skirt_flare")
    body = next(mesh for mesh in built["parts"] if mesh.name == "Skirt_Body")
    flare = next(
        part["params"]["flare"]
        for part in normalized["parts"]
        if part["name"] == "Skirt_Body"
    )
    # 丈がヒップまでの距離より長いので、体に沿った周長はヒップ寸法で飽和する
    assert body.design["bottom_fit_perimeter"] == pytest.approx(
        built["sizing"]["hip"] * flare
    )


def test_drape_disables_the_independent_hem_perimeter_check():
    """周方向の変調が入ると実周長に布が乗るので、裾周長は独立に検算できない"""
    _normalized, built = build("skirt_flare")
    body = next(mesh for mesh in built["parts"] if mesh.name == "Skirt_Body")
    assert body.design["bottom_perimeter"] is None
    assert "drape" in body.design["bottom_perimeter_note"]


def test_plain_skirt_keeps_the_independent_hem_perimeter_check():
    normalized = spec_module.load_preset("skirt_flare")
    for part in normalized["parts"]:
        part["params"].pop("drape_folds", None)
        if part["type"] == "skirt_body":
            part["params"]["drape_folds"] = 0
            part["params"]["drape_depth"] = 0.0
    built = parts.build_all(normalized)
    body = next(mesh for mesh in built["parts"] if mesh.name == "Skirt_Body")
    assert body.design["bottom_perimeter"] is not None
    report = validate.costume_report(built, normalized)
    assert report["failed"] == []


def test_waistband_bottom_equals_skirt_top():
    _normalized, built = build("skirt_flare")
    band = next(mesh for mesh in built["parts"] if mesh.name == "Skirt_Waistband")
    body = next(mesh for mesh in built["parts"] if mesh.name == "Skirt_Body")
    assert band.design["bottom_perimeter"] == pytest.approx(body.design["top_perimeter"])
    assert band.design["bottom_z"] == pytest.approx(body.design["top_z"])


def test_cross_section_is_elliptical_at_the_waist():
    """胴の断面は円ではない。円だとランプシェードに見えて衣装にならない"""
    _normalized, built = build("skirt_flare")
    body = next(mesh for mesh in built["parts"] if mesh.name == "Skirt_Body")
    top = validate.ring_metrics(body.verts, body.rings["top"])
    assert top["depth"] / top["breadth"] == pytest.approx(
        built["sizing"]["depth_ratio"], rel=0.02
    )


def test_cross_section_rounds_out_toward_the_hem():
    _normalized, built = build("skirt_flare")
    body = next(mesh for mesh in built["parts"] if mesh.name == "Skirt_Body")
    assert body.design["depth_ratio_bottom"] > body.design["depth_ratio_top"]


def test_waistband_sits_above_the_waistline():
    _normalized, built = build("skirt_flare")
    band = next(mesh for mesh in built["parts"] if mesh.name == "Skirt_Waistband")
    assert band.design["top_z"] > band.design["bottom_z"]


def test_meters_per_unit_scales_everything():
    normalized = spec_module.load_preset("skirt_flare")
    metres = parts.build_all(normalized)
    normalized["meters_per_unit"] = 0.01  # 1 unit = 1cm のシーン
    centimetres = parts.build_all(normalized)
    for a, b in zip(metres["parts"], centimetres["parts"]):
        assert b.design["length"] == pytest.approx(a.design["length"] * 100.0)
        for pa, pb in zip(a.verts, b.verts):
            assert pb == pytest.approx(tuple(value * 100.0 for value in pa))


def test_joint_with_mismatched_segments_is_rejected():
    normalized = spec_module.load_preset("skirt_flare")
    for part in normalized["parts"]:
        if part["name"] == "Skirt_Waistband":
            part["params"]["segments"] = 16
    with pytest.raises(parts.PartError):
        parts.build_all(normalized)


# ------------------------------------------------------- プリセットの合否


@pytest.mark.parametrize("preset", ["skirt_a0", "skirt_flare", "skirt_pleated"])
def test_presets_have_no_hard_failures(preset):
    normalized, built = build(preset)
    report = validate.costume_report(built, normalized)
    assert report["failed"] == []


def test_flare_preset_is_fully_green():
    """変種A(フレア)は warn も含めて全部通す基準形"""
    normalized, built = build("skirt_flare")
    report = validate.costume_report(built, normalized)
    assert report["verdict"] == "PASS", report["warned"]


def test_pleated_preset_only_warns_about_pleat_induced_density():
    """変種B(プリーツ)は折り目に頂点が寄るぶんだけ warn を許す"""
    normalized, built = build("skirt_pleated")
    report = validate.costume_report(built, normalized)
    assert report["failed"] == []
    assert report["warned"] == ["Skirt_Body.edge_length_cv_ring"]


def test_pleated_preset_actually_has_pleats():
    normalized, built = build("skirt_pleated")
    report = validate.costume_report(built, normalized)
    body = next(part for part in report["parts"] if part["part"] == "Skirt_Body")
    assert body["pleat_frequency"] == 24
    # 折り込みがあるぶん、裾の布の長さは設計周長より長い
    assert body["fabric_ratio"] > 1.2


def test_flare_preset_has_drape_not_pleats():
    normalized, built = build("skirt_flare")
    report = validate.costume_report(built, normalized)
    body = next(part for part in report["parts"] if part["part"] == "Skirt_Body")
    kinds = {item["kind"] for item in body["design"]["radial_modulations"]}
    assert kinds == {"drape"}
    assert body["pleat_frequency"] == 14  # プリセットの drape_folds


def test_a0_smoke_preset_warns_that_it_is_too_coarse():
    """A0 は配管を通すための粗いspec。実測レンジから外れることを warn で明示する"""
    normalized, built = build("skirt_a0")
    report = validate.costume_report(built, normalized)
    assert report["failed"] == []
    assert "edge_length_over_h" in report["warned"]
    assert report["edge_length_over_h"] > validate.EDGE_LENGTH_OVER_H_RANGE[1]


def test_garment_level_edge_length_pools_every_part():
    normalized, built = build("skirt_flare")
    report = validate.costume_report(built, normalized)
    edges = sum(part["edge_length"]["all"]["n"] for part in report["parts"])
    assert report["edge_length"]["n"] == edges
    low, high = validate.EDGE_LENGTH_OVER_H_RANGE
    assert low <= report["edge_length_over_h"] <= high


def test_parts_do_not_intersect_each_other():
    normalized, built = build("skirt_flare")
    report = validate.costume_report(built, normalized)
    assert all(pair["intersections"] == 0 for pair in report["part_pairs"])


def test_report_records_reproducibility_fields():
    normalized, built = build("skirt_flare")
    report = validate.costume_report(built, normalized)
    assert report["assumed_height_m"] == pytest.approx(1.53)
    assert report["height_units"] == pytest.approx(1.53)
    assert report["sizing"]["ease"] == pytest.approx(0.03)


# ------------------------------------------------------- 再現性(ゴールデン)


def test_a0_geometry_matches_the_golden_file():
    """A0 スモークspec の頂点座標が変わったら気づけるようにする回帰テスト"""
    with open(os.path.join(GOLDEN_DIR, "golden_skirt_a0.json"), "r", encoding="utf-8") as handle:
        golden = json.load(handle)

    normalized, built = build("skirt_a0")
    assert spec_module.spec_hash(normalized) == golden["spec_hash"]

    mesh = built["parts"][0]
    assert mesh.name == golden["name"]
    assert [list(quad) for quad in mesh.quads] == golden["quads"]
    assert len(mesh.verts) == len(golden["verts"])
    for produced, expected in zip(mesh.verts, golden["verts"]):
        assert produced == pytest.approx(tuple(expected), abs=1e-9)
    for produced, expected in zip(mesh.uv_loops, golden["uv_loops"]):
        for (u, v), (eu, ev) in zip(produced, expected):
            assert (u, v) == pytest.approx((eu, ev), abs=1e-9)


def test_generation_is_deterministic():
    normalized = spec_module.load_preset("skirt_pleated")
    first = parts.build_all(normalized)
    second = parts.build_all(normalized)
    for a, b in zip(first["parts"], second["parts"]):
        assert a.verts == b.verts
        assert a.quads == b.quads
        assert a.uv_loops == b.uv_loops


def test_a0_is_a_closed_ring_of_eight():
    _normalized, built = build("skirt_a0")
    mesh = built["parts"][0]
    assert mesh.ring_size == 8
    assert mesh.ring_count == 2
    assert len(mesh.quads) == 8
    top = validate.ring_metrics(mesh.verts, mesh.rings["top"])
    bottom = validate.ring_metrics(mesh.verts, mesh.rings["bottom"])
    assert bottom["mean_radius"] > top["mean_radius"]
    assert top["z"] > bottom["z"]
    # 8分割の多角形なので実周長はウエスト寸法より数%短い
    assert top["perimeter"] == pytest.approx(built["sizing"]["waist"], rel=0.06)
    assert top["perimeter"] < built["sizing"]["waist"]
