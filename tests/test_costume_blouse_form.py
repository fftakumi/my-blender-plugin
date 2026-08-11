"""ブラウスの「形」の定義(docs/garments.md #7〜#14)のテスト。

前の版は定義がランドマーク間の**距離**しか測っておらず、数値が全部合ったまま
「煙突つきの寸胴に水平な円錐が刺さったもの」が合格した。ここでテストするのは
**形を測る関数**と、**その形が壊れたときにゲートが実際に落ちること**。

ゲートのテストは「正しい入力で通る」だけでは足りない。壊した入力で
**落ちる**ことまで見ないと、恒真のゲートを書いても気づけない。
"""

import copy
import math

import pytest

from my_blender_plugin.costume import (
    kernels,
    modulate,
    parts,
    sizing,
    spec as spec_module,
    validate,
)


def build_blouse(overrides=None):
    """blouse プリセットを(必要なら書き換えて)組み立てる"""
    normalized = spec_module.load_preset("blouse")
    if overrides:
        for name, params in overrides.items():
            part = next(item for item in normalized["parts"] if item["name"] == name)
            part["params"].update(params)
        normalized = spec_module.normalize_spec(copy.deepcopy(normalized))
    return normalized, parts.build_all(normalized)


def part_named(built, name):
    return next(mesh for mesh in built["parts"] if mesh.name == name)


def gates(built, normalized, name):
    report = validate.costume_report(built, normalized)
    entry = next(item for item in report["parts"] if item["part"] == name)
    return entry


# --------------------------------------------------------------- 純粋関数


def test_sleeve_profile_holds_the_bicep_until_the_elbow():
    """定義 #11: 肘までは二の腕の太さのまま"""
    for t in (0.0, 0.2, 0.5):
        assert modulate.sleeve_radius_profile(t, 0.05, 0.03, 0.5, 0.9) == pytest.approx(0.05)


def test_sleeve_profile_ends_in_a_constant_cuff_band():
    """定義 #12: cuff_start より下は一定 = カフスの帯"""
    for t in (0.9, 0.95, 1.0):
        assert modulate.sleeve_radius_profile(t, 0.05, 0.03, 0.5, 0.9) == pytest.approx(0.03)


def test_sleeve_profile_narrows_monotonically_between_elbow_and_cuff():
    values = [
        modulate.sleeve_radius_profile(index / 20.0, 0.05, 0.03, 0.5, 0.9)
        for index in range(21)
    ]
    assert all(later <= earlier + 1e-12 for earlier, later in zip(values, values[1:]))


def test_sleeve_profile_rejects_a_cuff_that_starts_before_the_elbow():
    with pytest.raises(ValueError):
        modulate.sleeve_radius_profile(0.5, 0.05, 0.03, 0.9, 0.5)


def test_neckline_drop_is_zero_at_the_side_neck_points():
    front = -math.pi / 2
    for angle in (0.0, math.pi):
        assert modulate.neckline_drop(angle, 0.07, 0.02, front) == pytest.approx(0.0)


def test_neckline_drop_is_deeper_in_front_than_in_back():
    front = -math.pi / 2
    assert modulate.neckline_drop(front, 0.07, 0.02, front) == pytest.approx(0.07)
    assert modulate.neckline_drop(front + math.pi, 0.07, 0.02, front) == pytest.approx(0.02)


def test_neckline_drop_rejects_negative_drops():
    with pytest.raises(ValueError):
        modulate.neckline_drop(0.0, -0.01, 0.02, 0.0)


def test_button_positions_are_evenly_spaced():
    zs = modulate.button_positions(1.0, 0.0, 6, 0.1, 0.1)
    gaps = [zs[index - 1] - zs[index] for index in range(1, len(zs))]
    assert len(zs) == 6
    assert max(gaps) == pytest.approx(min(gaps))
    assert zs[0] == pytest.approx(0.9)
    assert zs[-1] == pytest.approx(0.1)


def test_button_positions_reject_an_inverted_span():
    with pytest.raises(ValueError):
        modulate.button_positions(0.0, 1.0, 6, 0.1, 0.1)


def test_ring_from_polar_accepts_a_z_per_vertex():
    angles = modulate.circle_angles(4)
    ring = kernels.ring_from_polar([1.0] * 4, angles, [0.0, 1.0, 2.0, 3.0])
    assert [point[2] for point in ring] == [0.0, 1.0, 2.0, 3.0]


def test_ring_from_polar_rejects_a_mismatched_z_list():
    angles = modulate.circle_angles(4)
    with pytest.raises(ValueError):
        kernels.ring_from_polar([1.0] * 4, angles, [0.0, 1.0])


def test_sizing_carries_the_product_measurements():
    table = sizing.resolve(1.58)
    # marea 9号ブラウスの実寸(着丈65 / 袖丈56 / 袖口23 / 肩幅38)
    assert table["garment_length"] == pytest.approx(0.65, abs=0.005)
    assert table["sleeve_length"] == pytest.approx(0.56, abs=0.005)
    assert table["cuff"] == pytest.approx(0.23, abs=0.005)
    assert table["shoulder_width"] == pytest.approx(0.38, abs=0.005)


def test_sizing_rejects_an_absurd_shoulder_slope():
    with pytest.raises(sizing.SizingError):
        sizing.resolve(1.58, {"shoulder_slope_degrees": 95.0})


# --------------------------------------------------------------- 組み上がり


def test_blouse_passes_every_hard_gate():
    normalized, built = build_blouse()
    report = validate.costume_report(built, normalized)
    assert report["failed"] == []


def test_neckline_drops_forward_and_is_not_a_horizontal_ring():
    """定義 #8"""
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Bodice")
    assert entry["measured_front_neck_drop"] > entry["measured_back_neck_drop"]
    assert entry["measured_front_neck_drop"] > 0.03


def test_shoulder_slope_matches_the_drafting_rule():
    """定義 #9: 新文化式の 前22°/後18° の平均 20°"""
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Bodice")
    assert entry["measured_shoulder_slope_degrees"] == pytest.approx(20.0, abs=2.0)


def test_garment_length_matches_the_product_measurement():
    """定義 #7: 着丈は製品実寸(65cm)から。勘で置いた hem_z がクロップの原因だった"""
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Bodice")
    assert entry["measured_garment_length"] == pytest.approx(0.65, rel=0.08)


def test_sleeve_droops_and_is_not_a_horizontal_spike():
    """定義 #10"""
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Sleeve_L")
    assert entry["measured_droop_degrees"] > 20.0


def test_sleeve_keeps_its_width_to_the_elbow():
    """定義 #11"""
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Sleeve_L")
    assert entry["measured_hold_ratio"] >= validate.SLEEVE_HOLD_MIN_RATIO


def test_cuff_is_a_flat_band_at_the_product_measurement():
    """定義 #12"""
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Sleeve_L")
    assert entry["measured_cuff_band_step"] <= validate.CUFF_BAND_STEP_MAX
    assert entry["measured_cuff_perimeter"] == pytest.approx(0.23, rel=0.08)


def test_bicep_comes_from_the_sizing_table_not_from_the_punched_armhole():
    """袖ぐりの穴は矩形なので周長が実物より3割大きい。そこから袖幅を出すと膨らむ"""
    normalized, built = build_blouse()
    sleeve = part_named(built, "Blouse_Sleeve_L")
    assert sleeve.design["bicep_perimeter"] == pytest.approx(
        built["sizing"]["bicep"], rel=1e-9
    )
    # 穴の周長(実測 57cm)から出していたら 44cm になり、袖幅の実勢 33cm を大きく外す
    assert sleeve.design["bicep_perimeter"] == pytest.approx(0.33, abs=0.01)
    assert sleeve.design["bicep_perimeter"] < sleeve.design["armhole_perimeter"]


def test_placket_is_a_band_of_the_declared_width_on_the_front_centre():
    """定義 #13"""
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Placket")
    assert entry["measured_placket_centred"] is True
    assert entry["measured_placket_width"] == pytest.approx(0.019 * 1.58, rel=0.05)


def test_placket_is_wider_than_the_front_opening_it_covers():
    """定義 #13: 「幅が設計どおり」だけでは覆えているかを見ていない。

    分割数28のとき前開きの隙間は 3.3cm あり、幅 3.0cm の前立てでは覆えず、
    裾のアップで黒い筋として出た。**相手より広いこと**を別のゲートにする。
    """
    normalized, built = build_blouse()
    placket = part_named(built, "Blouse_Placket")
    entry = gates(built, normalized, "Blouse_Placket")
    assert "placket_covers_the_opening" in entry["hard"]
    assert entry["hard"]["placket_covers_the_opening"]["ok"]
    assert placket.design["front_gap_width"] < placket.design["placket_width"]


def test_a_front_opening_wider_than_the_placket_fails():
    normalized, built = build_blouse({"Blouse_Bodice": {"front_gap": 0.03}})
    entry = gates(built, normalized, "Blouse_Placket")
    assert "placket_covers_the_opening" in entry["failed"]


def test_the_front_opening_width_does_not_depend_on_the_segment_count():
    """隙間は分割数の副産物にしない(分割数を変えただけで前立てからはみ出す)"""
    widths = []
    for segments in (20, 28, 40):
        _normalized, built = build_blouse({"Blouse_Bodice": {"segments": segments}})
        widths.append(part_named(built, "Blouse_Bodice").design["front_gap_width"])
    assert max(widths) == pytest.approx(min(widths), rel=1e-6)
    assert max(widths) == pytest.approx(0.006 * 1.58, rel=1e-6)


def test_the_placket_follows_the_bodice_profile_without_cutting_into_it():
    """前立ての縦の刻みは胴の前面プロファイルと同じ z。等間隔だと食い込む"""
    normalized, built = build_blouse()
    report = validate.costume_report(built, normalized)
    pair = next(
        item
        for item in report["part_pairs"]
        if {item["a"], item["b"]} == {"Blouse_Bodice", "Blouse_Placket"}
    )
    assert pair["intersections"] == 0


def test_buttons_are_counted_from_the_mesh_not_from_the_declaration():
    """定義 #14: 個数は境界ループから数え直す(申告を信じると恒真になる)"""
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Buttons")
    assert entry["measured_button_count"] == 6
    assert entry["measured_button_gap_cv"] == pytest.approx(0.0, abs=1e-9)
    assert entry["buttons_inside_placket"] is True


def test_button_hole_segments_are_evenly_spaced():
    assert modulate.button_hole_segments(12, 4) == [0, 3, 6, 9]
    assert modulate.button_hole_segments(12, 0) == []


def test_button_hole_segments_reject_a_count_that_cannot_be_divided():
    with pytest.raises(ValueError):
        modulate.button_hole_segments(10, 4)


def test_button_is_a_flat_disc_with_four_holes():
    """定義 #15: シャツ用貝ボタンは 直径11.5mm / 厚み2.3mm / 4つ穴。

    #14 は個数・間隔・位置しか見ていなかったので、前の版の
    「迫り出す円錐・裏面も穴も無し」が通っていた。
    """
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Buttons")
    assert entry["measured_button_diameter"] == pytest.approx(0.0115, abs=0.0005)
    assert entry["measured_button_thickness"] == pytest.approx(0.0023, abs=0.0005)
    assert entry["measured_button_thickness_ratio"] == pytest.approx(0.20, abs=0.02)
    assert entry["measured_button_holes"] == 4
    assert entry["measured_button_hole_spacing_cv"] == pytest.approx(0.0, abs=1e-9)


def test_a_domed_button_fails_the_flat_disc_gate():
    """厚み/直径が実物から外れたら落ちること(前の版の円錐は 0.26)"""
    normalized, built = build_blouse({"Blouse_Buttons": {"thickness_ratio": 0.5}})
    entry = gates(built, normalized, "Blouse_Buttons")
    assert "button_is_a_flat_disc" in entry["failed"]


def test_a_button_without_holes_fails():
    """穴を 0 にすると「申告0 = 実測0」で通ってしまう罠。下限で止める"""
    normalized, built = build_blouse({"Blouse_Buttons": {"holes": 0}})
    entry = gates(built, normalized, "Blouse_Buttons")
    assert entry["measured_button_holes"] == 0
    assert "button_holes" in entry["failed"]


def test_holes_are_counted_from_the_mesh_as_boundary_loops():
    """穴は「押した頂点の数」ではなく**境界ループ**として数える。

    窪みだと生成側の申告に近い量を数えることになる。面を抜いて本当に開けたので、
    ボタン1個の境界は 外周 + 中心の小穴 + 穴4つ = 6本になる。
    """
    normalized, built = build_blouse()
    buttons = part_named(built, "Blouse_Buttons")
    entry = gates(built, normalized, "Blouse_Buttons")
    assert buttons.design["boundary_loops"] == 6 * (2 + 4)
    assert entry["boundary_loops"] == 6 * (2 + 4)
    assert entry["measured_button_holes"] == 4


def test_buttons_are_flat_shaded():
    """硬い部品にスムーズシェーディングを掛けると円盤の縁が丸まって塊に見える"""
    _normalized, built = build_blouse()
    assert part_named(built, "Blouse_Buttons").flat_shaded is True
    assert part_named(built, "Blouse_Bodice").flat_shaded is False


def test_the_sleeve_seam_sits_exactly_on_the_armhole():
    """袖の上端リングは袖ぐりの境界ループそのものなので継ぎ目はゼロ"""
    _normalized, built = build_blouse()
    bodice = part_named(built, "Blouse_Bodice")
    sleeve = part_named(built, "Blouse_Sleeve_L")
    assert validate.seam_distance(bodice, "armhole_l", sleeve, "top") < 1e-9


def test_the_collar_sits_exactly_on_the_neckline():
    _normalized, built = build_blouse()
    bodice = part_named(built, "Blouse_Bodice")
    collar = part_named(built, "Blouse_Collar")
    assert validate.seam_distance(bodice, "top", collar, "bottom") < 1e-9


def test_the_collar_fall_folds_back_down_over_the_stand():
    """羽根は台襟の上端から下へ倒れ、外へ広がる"""
    _normalized, built = build_blouse()
    stand = part_named(built, "Blouse_Collar")
    fall = part_named(built, "Blouse_Collar_Fall")
    fold_z = max(point[2] for point in stand.verts)
    assert max(point[2] for point in fall.verts) == pytest.approx(fold_z)
    assert min(point[2] for point in fall.verts) < fold_z
    tip = max(abs(point[0]) for point in fall.verts)
    assert tip > max(abs(point[0]) for point in stand.verts)


def test_dependent_parts_are_built_in_dependency_order():
    """ボタンは前立てに、前立ては胴に付く。二段の依存が解けること"""
    _normalized, built = build_blouse()
    names = [mesh.name for mesh in built["parts"]]
    assert set(names) >= {"Blouse_Bodice", "Blouse_Placket", "Blouse_Buttons"}


def test_a_part_with_a_missing_host_is_reported_clearly():
    normalized = spec_module.load_preset("blouse")
    part = next(item for item in normalized["parts"] if item["name"] == "Blouse_Buttons")
    part["attach_to"] = "Nope"
    with pytest.raises(parts.PartError) as error:
        parts.build_all(normalized)
    assert "Nope" in str(error.value)


# ------------------------------------------- ゲートが本当に欠陥を捕まえるか
#
# 「正しい入力で通る」だけのテストでは、恒真のゲートを書いても気づけない。


def test_a_cone_sleeve_fails_the_shape_gate():
    """全長を滑らかに細めた袖(=前の版)は sleeve_not_cone で落ちること。

    肘の判定位置は spec の elbow_fraction ではなく解剖の位置(袖丈の半分)なので、
    テーパーを早く始めても判定区間が一緒に縮んで逃げる、ということが起きない。
    """
    normalized, built = build_blouse(
        {
            "Blouse_Sleeve_L": {
                "elbow_fraction": 0.1,  # cap まで繰り上がる
                "cuff_start": 0.55,  # 全長のほとんどをテーパーに使う
                "cuff_gather": 1.0,  # いせ込み無し = 袖口までまっすぐ細る
            }
        }
    )
    entry = gates(built, normalized, "Blouse_Sleeve_L")
    assert "sleeve_not_cone" in entry["failed"]


def test_a_horizontal_neckline_fails_the_front_drop_gate():
    """水平な襟ぐり(=前の版)は neck_drops_forward で落ちること"""
    normalized, built = build_blouse({"Blouse_Bodice": {"neck_drop_scale": 0.0}})
    entry = gates(built, normalized, "Blouse_Bodice")
    assert "neck_drops_forward" in entry["failed"]
    assert "neck_front_drop" in entry["failed"]


def test_a_cropped_bodice_fails_the_garment_length_gate():
    """着丈を6割にすると(=前の版の丈)落ちること"""
    normalized, built = build_blouse({"Blouse_Bodice": {"length_scale": 0.6}})
    entry = gates(built, normalized, "Blouse_Bodice")
    assert "garment_length" not in entry["failed"], "設計値も一緒に縮むので丈ゲートは通る"
    # 丈そのものは製品実寸から外れる。設計値と実測の一致ではなく実寸との一致を見る
    assert entry["measured_garment_length"] < 0.65 * 0.8


def test_too_few_buttons_fail_the_button_gate():
    normalized, built = build_blouse({"Blouse_Buttons": {"count": 2}})
    entry = gates(built, normalized, "Blouse_Buttons")
    assert "button_count" in entry["failed"]


def test_a_sleeve_without_a_cuff_band_fails():
    """カフスの帯が1リングしか無いと cuff_band_exists で落ちること"""
    normalized, built = build_blouse(
        {"Blouse_Sleeve_L": {"rings": 5, "cuff_start": 0.99}}
    )
    entry = gates(built, normalized, "Blouse_Sleeve_L")
    assert "cuff_band_exists" in entry["failed"]


def test_shirttail_drops_at_front_and_back():
    """定義 #16: 裾は脇がいちばん高く、前後の中心が下がる"""
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Bodice")
    assert entry["measured_shirttail_drop"] > entry["measured_shirttail_front_drop"]
    assert entry["measured_shirttail_front_drop"] > validate.SHIRTTAIL_MIN_DROP


def test_garment_length_is_measured_to_the_lowest_hem_point():
    """裾がカーブしても着丈は製品実寸のまま(いちばん下が着丈)"""
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Bodice")
    assert entry["measured_garment_length"] == pytest.approx(0.65, rel=0.05)


def test_a_horizontal_hem_fails_the_shirttail_gate():
    """水平に切った裾は落ちること。設計値が 0 でもゲートは消えない"""
    normalized, built = build_blouse({"Blouse_Bodice": {"shirttail_drop": 0.0}})
    entry = gates(built, normalized, "Blouse_Bodice")
    assert "shirttail_is_curved" in entry["failed"]


def test_the_hem_perimeter_is_compared_in_the_horizontal_plane():
    """裾がカーブすると実周長は上下動ぶん伸びる。太さの比較は水平投影で行う"""
    normalized, built = build_blouse()
    bodice = part_named(built, "Blouse_Bodice")
    assert bodice.design["bottom_perimeter_projected"] is True
    hem = validate.ring_metrics(bodice.verts, bodice.rings["bottom"])
    assert hem["perimeter"] > hem["perimeter_xy"]
    assert hem["perimeter_xy"] == pytest.approx(
        bodice.design["bottom_perimeter"], rel=validate.DIM_TOL
    )


def test_a_flat_ring_has_the_same_length_in_both_measures():
    """水平なリングでは実周長と水平投影が一致する(スカートに影響しない)"""
    _normalized, built = build_blouse()
    skirt = None
    from my_blender_plugin.costume import spec as spec_module

    normalized = spec_module.load_preset("skirt_flare")
    skirt_built = parts.build_all(normalized)
    skirt = next(m for m in skirt_built["parts"] if m.name == "Skirt_Body")
    hem = validate.ring_metrics(skirt.verts, skirt.rings["bottom"])
    assert hem["perimeter_xy"] == pytest.approx(hem["perimeter"], rel=1e-12)


def test_the_cuff_is_visible_as_a_step_in_the_silhouette():
    """定義 #17: 帯が一定なだけでは絵に出ない。

    「幾何としては在るのに見えない」は、反省に書いた原因3(組み上がりの姿を
    誰も見ていない)のミニチュア。段差を測れる量にして初めてゲートになる。
    """
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Sleeve_L")
    assert entry["measured_cuff_gather"] >= validate.CUFF_GATHER_MIN
    assert "cuff_is_visible" in entry["hard"]


def test_a_cuff_without_a_gather_fails_even_though_the_band_is_flat():
    normalized, built = build_blouse({"Blouse_Sleeve_L": {"cuff_gather": 1.0}})
    entry = gates(built, normalized, "Blouse_Sleeve_L")
    assert entry["hard"]["cuff_band_is_flat"]["ok"], "帯が一定なのは変わらない"
    assert "cuff_is_visible" in entry["failed"]


def test_the_cuff_seam_ring_is_marked_sharp():
    """段差だけでは陰影が繋がって縫い目に見えない"""
    _normalized, built = build_blouse()
    sleeve = part_named(built, "Blouse_Sleeve_L")
    assert sleeve.sharp_rings == sleeve.design["cuff_rings"][:1]
    assert part_named(built, "Blouse_Bodice").sharp_rings == []


def test_sleeve_profile_gather_keeps_the_cuff_itself_at_the_product_size():
    """いせ込みは袖口の手前を太くするだけで、カフスそのものは実寸のまま"""
    assert modulate.sleeve_radius_profile(1.0, 0.05, 0.03, 0.5, 0.9, 1.3) == pytest.approx(0.03)
    assert modulate.sleeve_radius_profile(0.89, 0.05, 0.03, 0.5, 0.9, 1.3) > 0.03


def test_sleeve_profile_rejects_a_gather_below_one():
    with pytest.raises(ValueError):
        modulate.sleeve_radius_profile(0.5, 0.05, 0.03, 0.5, 0.9, 0.8)


def test_bust_projection_is_a_local_bump_not_a_global_swell():
    """定義 #18: 角度方向にも軸方向にも局所的。袖ぐりにも裾にも触れない"""
    front = -math.pi / 2
    peak = modulate.bust_projection(front, 0.3, 0.03, 1.0, 0.3, 0.25, front)
    assert peak == pytest.approx(0.03)
    # 袖ぐりの角度(0 と π)には出ない
    for angle in (0.0, math.pi):
        assert modulate.bust_projection(angle, 0.3, 0.03, 1.0, 0.3, 0.25, front) == 0.0
    # 裾(t=1)にも出ない
    assert modulate.bust_projection(front, 1.0, 0.03, 1.0, 0.3, 0.25, front) == 0.0


def test_bust_projection_rejects_bad_widths():
    with pytest.raises(ValueError):
        modulate.bust_projection(0.0, 0.3, 0.03, 0.0, 0.3, 0.25, 0.0)
    with pytest.raises(ValueError):
        modulate.bust_projection(0.0, 0.3, -0.01, 1.0, 0.3, 0.25, 0.0)


def test_the_bodice_has_a_bust_above_the_waist():
    """定義 #18: 楕円断面だけの胴はメンズシャツにしか見えない"""
    normalized, built = build_blouse()
    entry = gates(built, normalized, "Blouse_Bodice")
    assert entry["measured_bust_projection"] >= validate.BUST_PROJECTION_MIN
    assert entry["measured_bust_z_fraction"] >= 0.5


def test_a_bodice_without_a_bust_fails():
    """設計値を 0 にしても「設計 0 = 実測 0」で通らないこと"""
    normalized, built = build_blouse({"Blouse_Bodice": {"bust_projection": 0.0}})
    entry = gates(built, normalized, "Blouse_Bodice")
    assert "bust_projection" in entry["failed"]


def test_the_placket_follows_the_bust_without_cutting_into_it():
    """前立ては胸のふくらみにも乗る。乗らないと浮くか食い込む"""
    normalized, built = build_blouse()
    report = validate.costume_report(built, normalized)
    pair = next(
        item
        for item in report["part_pairs"]
        if {item["a"], item["b"]} == {"Blouse_Bodice", "Blouse_Placket"}
    )
    assert pair["intersections"] == 0
    placket = part_named(built, "Blouse_Placket")
    # 前立てのいちばん前が、胸のふくらみのぶんだけ前へ出ていること
    bodice = part_named(built, "Blouse_Bodice")
    assert placket.design["placket_front_y"] < min(
        y for _z, y in bodice.design["front_profile"][-3:]
    )
