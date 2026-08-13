import math

import pytest

from my_blender_plugin.costume import modulate


def test_smoothstep_endpoints_and_clamping():
    assert modulate.smoothstep(-1.0) == 0.0
    assert modulate.smoothstep(0.0) == 0.0
    assert modulate.smoothstep(0.5) == pytest.approx(0.5)
    assert modulate.smoothstep(1.0) == 1.0
    assert modulate.smoothstep(2.0) == 1.0


def test_axis_fractions_span_zero_to_one():
    assert modulate.axis_fractions(2) == [0.0, 1.0]
    assert modulate.axis_fractions(5) == [0.0, 0.25, 0.5, 0.75, 1.0]
    with pytest.raises(ValueError):
        modulate.axis_fractions(1)


# ------------------------------------------------------------------ 楕円断面


def _numeric_ellipse_perimeter(semi_major, depth_ratio, steps=200000):
    """弧長を数値積分して求める(ラマヌジャン近似の答え合わせ用の別経路)"""
    a = semi_major
    b = semi_major * depth_ratio
    total = 0.0
    previous = (a, 0.0)
    for index in range(1, steps + 1):
        angle = 2.0 * math.pi * index / steps
        point = (a * math.cos(angle), b * math.sin(angle))
        total += math.hypot(point[0] - previous[0], point[1] - previous[1])
        previous = point
    return total


def test_ellipse_perimeter_of_a_circle_is_two_pi_r():
    assert modulate.ellipse_perimeter(2.0, 1.0) == pytest.approx(2 * math.pi * 2.0)


@pytest.mark.parametrize("depth_ratio", [1.0, 0.8, 0.68, 0.5])
def test_ellipse_perimeter_matches_numeric_integration(depth_ratio):
    approximate = modulate.ellipse_perimeter(1.0, depth_ratio)
    exact = _numeric_ellipse_perimeter(1.0, depth_ratio)
    assert approximate == pytest.approx(exact, rel=1e-4)


def test_ellipse_semi_major_inverts_the_perimeter():
    for depth_ratio in (1.0, 0.68, 0.4):
        semi_major = modulate.ellipse_semi_major(1.25, depth_ratio)
        assert modulate.ellipse_perimeter(semi_major, depth_ratio) == pytest.approx(1.25)


def test_ellipse_semi_major_rejects_non_positive_depth():
    with pytest.raises(ValueError):
        modulate.ellipse_semi_major(1.0, 0.0)


def test_flatter_cross_section_needs_a_wider_semi_major():
    """同じ周長でも前後に潰れるほど左右に広がる"""
    circle = modulate.ellipse_semi_major(1.0, 1.0)
    flat = modulate.ellipse_semi_major(1.0, 0.5)
    assert flat > circle


def test_depth_ratio_rounds_out_as_the_skirt_flares():
    ts = [0.0, 0.5, 1.0]
    profile = modulate.depth_ratio_profile(ts, 0.68, flare=2.0, flare_curve=1.0)
    assert profile[0] == pytest.approx(0.68)  # 腰は体の断面
    assert profile[-1] == pytest.approx(1.0)  # 裾は自由に落ちて円に近づく
    assert profile[0] < profile[1] < profile[2]


def test_depth_ratio_stays_elliptical_without_flare():
    profile = modulate.depth_ratio_profile([0.0, 0.5, 1.0], 0.68, flare=1.0, flare_curve=1.0)
    assert profile == pytest.approx([0.68, 0.68, 0.68])


# ------------------------------------------------------------------ 軸方向の寸法


def test_follow_measure_reaches_hip_and_stays():
    assert modulate.follow_measure(0.0, 0.3, 0.60, 0.90, 0.12, 1.0) == pytest.approx(0.60)
    assert modulate.follow_measure(0.4, 0.3, 0.60, 0.90, 0.12, 1.0) == pytest.approx(0.90)
    assert modulate.follow_measure(1.0, 0.3, 0.60, 0.90, 0.12, 1.0) == pytest.approx(0.90)


def test_hip_hug_zero_keeps_the_waist_measure():
    for t in (0.0, 0.5, 1.0):
        assert modulate.follow_measure(t, 0.3, 0.60, 0.90, 0.12, 0.0) == pytest.approx(0.60)


def test_follow_measure_stops_short_on_a_very_short_skirt():
    # 丈 0.06 < ヒップまで 0.12 なので、下端でもヒップ寸法には届かない
    hem = modulate.follow_measure(1.0, 0.06, 0.60, 0.90, 0.12, 1.0)
    assert 0.60 < hem < 0.90


def test_flare_multiplier_endpoints():
    assert modulate.flare_multiplier(0.0, 2.0, 1.6) == pytest.approx(1.0)
    assert modulate.flare_multiplier(1.0, 2.0, 1.6) == pytest.approx(2.0)


def test_flare_curve_above_one_delays_the_spread():
    assert modulate.flare_multiplier(0.5, 2.0, 2.0) < modulate.flare_multiplier(0.5, 2.0, 1.0)


def test_skirt_profile_is_monotonic_and_reports_ends():
    profile = modulate.skirt_profile(
        rings=10,
        length=0.41,
        waist_perimeter=0.60,
        hip_perimeter=0.90,
        hip_drop=0.18,
        hip_hug=1.0,
        flare=2.0,
        flare_curve=1.6,
        depth_ratio=0.68,
    )
    assert len(profile["perimeter"]) == 10
    assert profile["perimeter"] == sorted(profile["perimeter"])
    assert profile["top_perimeter"] == pytest.approx(0.60)
    assert profile["bottom_perimeter"] == pytest.approx(0.90 * 2.0)
    # 長半径は周長と厚み比から逆算されている
    for perimeter, ratio, semi_major in zip(
        profile["perimeter"], profile["depth_ratio"], profile["semi_major"]
    ):
        assert modulate.ellipse_perimeter(semi_major, ratio) == pytest.approx(perimeter)


def test_band_profile_bottom_hits_the_requested_perimeter():
    profile = modulate.band_profile(rings=3, bottom_perimeter=0.60, flare=1.2, depth_ratio=0.68)
    assert profile["bottom_perimeter"] == pytest.approx(0.60)
    assert profile["top_perimeter"] == pytest.approx(0.50)


# ------------------------------------------------------------------ プリーツ


def test_pleat_offsets_are_zero_mean():
    offsets = modulate.pleat_offsets(segments=48, pleats=24, depth=0.22, duty=0.5)
    assert len(offsets) == 48
    assert sum(offsets) == pytest.approx(0.0)
    assert max(offsets) > 0.0 > min(offsets)


def test_pleat_offsets_have_one_fold_per_pleat():
    offsets = modulate.pleat_offsets(segments=48, pleats=24, depth=0.2, duty=0.5)
    descents = sum(1 for i in range(48) if offsets[i - 1] > offsets[i])
    assert descents == 24


def test_pleat_offsets_respect_duty():
    offsets = modulate.pleat_offsets(segments=48, pleats=12, depth=0.2, duty=0.75)
    period = 48 // 12
    assert sum(1 for index in range(period) if offsets[index] > 0) == 3


def test_pleat_offsets_zero_when_disabled():
    assert modulate.pleat_offsets(24, 0, 0.2, 0.5) == [0.0] * 24
    assert modulate.pleat_offsets(24, 12, 0.0, 0.5) == [0.0] * 24


def test_pleat_offsets_reject_non_divisible_segments():
    with pytest.raises(ValueError):
        modulate.pleat_offsets(segments=25, pleats=12, depth=0.2, duty=0.5)


def test_pleat_offsets_reject_period_of_one():
    with pytest.raises(ValueError) as error:
        modulate.pleat_offsets(segments=24, pleats=24, depth=0.2, duty=0.5)
    assert "2倍以上" in str(error.value)


def test_pleat_taper_only_the_top_ring_is_a_plain_circle():
    """上端リングだけ 0(ウエストバンドとの接合のため)"""
    assert modulate.pleat_taper(0.0, 0.22, 0.35) == 0.0


def test_pleat_taper_keeps_creases_just_below_the_waistband():
    """実物は腰でも折り目線が見えている。上部を 0 にすると滑らかな筒になる"""
    for t in (0.02, 0.1, 0.22):
        assert modulate.pleat_taper(t, 0.22, 0.35) == pytest.approx(0.35)


def test_pleat_taper_opens_fully_at_the_hem():
    assert modulate.pleat_taper(1.0, 0.22, 0.35) == pytest.approx(1.0)


def test_pleat_taper_increases_monotonically_below_the_stitching():
    values = [modulate.pleat_taper(index / 20.0, 0.22, 0.35) for index in range(21)]
    below = values[5:]  # t >= 0.25
    assert below == sorted(below)
    assert values[1] < values[-1]  # 腰より裾のほうが開いている


def test_pleat_taper_with_full_stitch_down_stays_closed():
    assert modulate.pleat_taper(0.9, 1.0, 0.35) == pytest.approx(0.35)


# ------------------------------------------------------------------ ドレープ


def _max_step(offsets):
    count = len(offsets)
    return max(abs(offsets[index] - offsets[index - 1]) for index in range(count))


def test_drape_offsets_are_zero_mean():
    offsets = modulate.drape_offsets(segments=32, folds=8, depth=0.05)
    assert len(offsets) == 32
    assert sum(offsets) == pytest.approx(0.0, abs=1e-12)
    assert max(offsets) == pytest.approx(0.05)


def test_drape_gets_smoother_with_more_segments_but_pleats_do_not():
    """ドレープは分割を増やすと折り目が消える。プリーツは何分割でも段差が残る"""
    coarse = _max_step(modulate.drape_offsets(segments=32, folds=8, depth=0.05))
    fine = _max_step(modulate.drape_offsets(segments=256, folds=8, depth=0.05))
    assert fine < coarse / 5.0

    pleat_coarse = _max_step(modulate.pleat_offsets(32, 8, 0.05, 0.5))
    pleat_fine = _max_step(modulate.pleat_offsets(256, 8, 0.05, 0.5))
    assert pleat_fine == pytest.approx(pleat_coarse)


def test_drape_folds_need_not_divide_the_segment_count():
    """余弦なので山数が分割数を割り切らなくても連続する"""
    offsets = modulate.drape_offsets(segments=32, folds=14, depth=0.05)
    assert sum(offsets) == pytest.approx(0.0, abs=1e-12)


def test_drape_offsets_zero_when_disabled():
    assert modulate.drape_offsets(24, 0, 0.05) == [0.0] * 24
    assert modulate.drape_offsets(24, 8, 0.0) == [0.0] * 24


def test_drape_taper_is_closed_at_the_waist():
    assert modulate.drape_taper(0.0) == 0.0
    assert modulate.drape_taper(1.0) == pytest.approx(1.0)


# ------------------------------------------------------------------ 合成


def test_combine_offsets_sums_each_modulation_with_its_own_taper():
    modulations = [
        {"offsets": [1.0, -1.0], "tapers": [0.0, 1.0]},
        {"offsets": [0.5, 0.5], "tapers": [1.0, 0.5]},
    ]
    assert modulate.combine_offsets(modulations, 0, 2) == pytest.approx([0.5, 0.5])
    assert modulate.combine_offsets(modulations, 1, 2) == pytest.approx([1.25, -0.75])


def test_combine_offsets_with_no_modulations_is_flat():
    assert modulate.combine_offsets([], 0, 4) == [0.0] * 4


def test_ring_radii_applies_the_combined_offsets():
    assert modulate.ring_radii(2.0, [0.0, 0.1, -0.1]) == pytest.approx([2.0, 2.2, 1.8])


def test_circle_angles():
    angles = modulate.circle_angles(4)
    assert angles == pytest.approx([0.0, math.pi / 2, math.pi, 3 * math.pi / 2])
    with pytest.raises(ValueError):
        modulate.circle_angles(2)


def test_pleat_fold_segments_marks_every_pleat_edge():
    """48分割24山(1山2分割)なら、内→外と外→内で全48本が折り目"""
    assert len(modulate.pleat_fold_segments(48, 24, 0.22, 0.5)) == 48


def test_pleat_fold_segments_respects_duty():
    # 1周期(4分割)につき「内→外」と「外→内」の2本
    folds = modulate.pleat_fold_segments(48, 12, 0.22, 0.75)
    assert len(folds) == 24
    assert folds[:4] == [0, 3, 4, 7]


def test_pleat_fold_segments_are_where_the_offsets_step():
    """オフセット列の実際の段差位置と一致すること"""
    offsets = modulate.pleat_offsets(48, 12, 0.22, 0.75)
    steps = [i for i in range(48) if abs(offsets[i] - offsets[i - 1]) > 1e-9]
    assert modulate.pleat_fold_segments(48, 12, 0.22, 0.75) == steps


def test_pleat_fold_segments_none_without_pleats():
    """ドレープはなだらかなので折り目として陰影を割ってはいけない"""
    assert modulate.pleat_fold_segments(48, 0, 0.0, 0.5) == []
    assert modulate.pleat_fold_segments(48, 24, 0.0, 0.5) == []
    assert modulate.pleat_fold_segments(24, 24, 0.22, 0.5) == []


# ------------------------------------------------- 胸のふくらみ(左右2つの山)
#
# apex=0 だと山が前中心で1つに重なり、胸骨のところがいちばん前へ出た
# 1枚のドームになる。実物はそこが窪むので不自然に見える。


FRONT = -math.pi / 2


def bust(delta_degrees, apex_degrees, amount=1.0, angular_degrees=62.0):
    """前中心から delta 度の位置でのふくらみ(バストの高さ t=bust_t で測る)"""
    return modulate.bust_projection(
        FRONT + math.radians(delta_degrees),
        0.3,
        amount,
        math.radians(angular_degrees),
        0.3,
        0.26,
        FRONT,
        math.radians(apex_degrees),
    )


def test_bust_apex_zero_keeps_the_single_lobe():
    """既定の 0 は従来と同じ形(前中心が最大)。回帰ガード"""
    assert bust(0.0, 0.0) == pytest.approx(1.0)
    for delta in (10.0, 30.0, 50.0):
        assert bust(delta, 0.0) < bust(delta - 10.0, 0.0)


def test_bust_apex_puts_the_peak_off_centre():
    apex = 22.0
    assert bust(apex, apex) == pytest.approx(1.0)
    assert bust(0.0, apex) < bust(apex, apex)
    assert bust(-apex, apex) == pytest.approx(bust(apex, apex))


def test_bust_apex_makes_a_valley_at_the_sternum():
    """前中心が谷になっていること(これが無いと1枚のドームに見える)"""
    apex = 22.0
    assert bust(0.0, apex) < 0.6
    # 谷から山へ単調に増える
    assert bust(0.0, apex) < bust(11.0, apex) < bust(apex, apex)


def test_bust_apex_does_not_widen_the_angular_reach():
    """総半幅は apex によらず angular_width のまま(袖ぐりに掛かる範囲を変えない)"""
    for apex in (0.0, 22.0, 40.0):
        assert bust(62.0, apex) == 0.0
        assert bust(61.0, apex) > 0.0


def test_bust_apex_must_stay_inside_the_angular_width():
    with pytest.raises(ValueError):
        bust(0.0, 62.0)
    with pytest.raises(ValueError):
        bust(0.0, -1.0)


def test_bust_upper_width_can_be_clipped_independently():
    """上側だけ狭めても頂点は値1・傾き0のまま(折れ目にならない)"""
    kwargs = dict(
        amount=1.0,
        angular_width=math.radians(62.0),
        bust_t=0.4,
        axial_width=0.26,
        front_angle=FRONT,
        apex_angle=math.radians(22.0),
        axial_width_up=0.10,
    )
    at = lambda t: modulate.bust_projection(FRONT + kwargs["apex_angle"], t, **kwargs)
    assert at(0.4) == pytest.approx(1.0)          # 頂点は変わらない
    assert at(0.30) == pytest.approx(0.0, abs=1e-12)  # 上は 0.10 で終わる
    assert at(0.29) == 0.0
    assert at(0.50) > 0.0                          # 下は 0.26 のまま生きている
    assert at(0.65) > 0.0                          # 下端は 0.4 + 0.26 = 0.66
    assert at(0.66) == 0.0
    # 上側のほうが急に落ちる(同じ距離での値が小さい)
    assert at(0.4 - 0.08) < at(0.4 + 0.08)


def test_bust_upper_width_defaults_to_the_lower_one():
    common = (1.0, math.radians(62.0), 0.4, 0.26, FRONT, math.radians(22.0))
    angle = FRONT + math.radians(22.0)
    for t in (0.30, 0.35, 0.45, 0.55):
        assert modulate.bust_projection(angle, t, *common) == pytest.approx(
            modulate.bust_projection(angle, t, *common, axial_width_up=0.26)
        )


def test_bust_rejects_a_non_positive_upper_width():
    with pytest.raises(ValueError):
        modulate.bust_projection(
            FRONT, 0.4, 1.0, math.radians(62.0), 0.4, 0.26, FRONT, 0.0, 0.0
        )
