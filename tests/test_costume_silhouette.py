import math

import pytest

from my_blender_plugin.costume import modulate, silhouette


def cone_mask(height=40, top=6, bottom=30, center=25, width=50):
    """上が細く下が広い台形のマスク(スカートの正面シルエットの模型)"""
    mask = []
    for y in range(height):
        t = y / (height - 1)
        half = (top + (bottom - top) * t) / 2.0
        mask.append([abs(x - center) <= half for x in range(width)])
    return mask


def test_linear_to_srgb_endpoints():
    assert silhouette.linear_to_srgb(0.0) == 0.0
    assert silhouette.linear_to_srgb(1.0) == pytest.approx(1.0)
    # リニアの中間は sRGB では明るくなる
    assert silhouette.linear_to_srgb(0.2140) == pytest.approx(0.5, abs=1e-3)


def test_runs_finds_contiguous_spans():
    assert silhouette.runs([False, True, True, False, True]) == [(1, 2), (4, 4)]
    assert silhouette.runs([True, True]) == [(0, 1)]
    assert silhouette.runs([False, False]) == []


def test_centered_run_prefers_the_span_containing_the_centre():
    spans = [(0, 3), (10, 20), (30, 31)]
    assert silhouette.centered_run(spans, 15) == (10, 20)
    assert silhouette.centered_run(spans, 1) == (0, 3)
    assert silhouette.centered_run([], 5) is None


def test_centered_run_falls_back_to_the_nearest_span():
    assert silhouette.centered_run([(0, 3), (30, 31)], 20) == (30, 31)


def test_centered_run_ignores_hair_like_side_spans():
    """髪はスカートの外側に別の区間として出る。中心線の区間だけ見れば巻き込まない"""
    row = [False] * 60
    for x in range(4, 7):  # 左に垂れた髪
        row[x] = True
    for x in range(20, 40):  # スカート本体
        row[x] = True
    for x in range(52, 55):  # 右に垂れた髪
        row[x] = True
    assert silhouette.centered_run(silhouette.runs(row), 30) == (20, 39)


def test_dominant_center_finds_the_middle_of_a_cone():
    assert silhouette.dominant_center(cone_mask(center=25)) == 25


def test_width_profile_of_a_cone_grows_downward():
    profile = silhouette.width_profile(cone_mask())
    widths = [row["width"] for row in profile["rows"]]
    assert widths[0] < widths[-1]
    assert widths == sorted(widths)


def test_width_profile_drops_hair_only_rows():
    mask = cone_mask()
    # 最下段の下に「髪だけ」の細い行を足す
    thin = [False] * 50
    thin[4] = thin[5] = True
    mask.append(thin)
    profile = silhouette.width_profile(mask, center=25, min_width=3)
    assert profile["rows"][-1]["y"] == len(mask) - 2


def test_normalized_widths_starts_at_one_and_spans_zero_to_one():
    normalized = silhouette.normalized_widths(silhouette.width_profile(cone_mask()))
    assert normalized[0] == (0.0, pytest.approx(1.0))
    assert normalized[-1][0] == pytest.approx(1.0)
    assert normalized[-1][1] > 1.0


def test_hem_ratio_is_resolution_independent():
    """実際に使う数値(裾/上端の幅比)が解像度で変わらないこと。

    各サンプル点の厳密一致は求めない。幅は必ず奇数画素に丸まるので、
    上端が細い低解像度では最初の数行が同じ幅で頭打ちになる(量子化)。
    """
    ratios = []
    for scale in (1, 4, 10):
        profile = silhouette.width_profile(
            cone_mask(
                height=40 * scale,
                top=6 * scale,
                bottom=30 * scale,
                center=25 * scale,
                width=50 * scale,
            )
        )
        ratios.append(silhouette.normalized_widths(profile)[-1][1])
    # 設計上の比は 30/6 = 5.0。解像度を上げるほどそこへ収束する
    assert ratios[-1] == pytest.approx(5.0, rel=0.03)
    assert max(ratios[1:]) - min(ratios[1:]) < 0.25


def test_normalized_widths_needs_at_least_two_rows():
    with pytest.raises(ValueError):
        silhouette.normalized_widths({"center": 0, "rows": [{"y": 0, "width": 4}]})


def test_compare_of_identical_profiles_is_zero():
    profile = silhouette.width_profile(cone_mask())
    result = silhouette.compare(profile, profile)
    assert result["rms"] == pytest.approx(0.0)
    assert result["hem_ratio_error"] == pytest.approx(0.0)


def test_compare_detects_a_narrower_produced_skirt():
    reference = silhouette.width_profile(cone_mask(top=6, bottom=36))
    produced = silhouette.width_profile(cone_mask(top=6, bottom=18))
    result = silhouette.compare(reference, produced)
    assert result["hem_ratio_produced"] < result["hem_ratio_reference"]
    assert result["hem_ratio_error"] < -0.3  # 明らかに狭い
    assert result["rms"] > 0.3


def test_fit_flare_recovers_a_known_shape():
    """幅が 1 + (f-1)t^c で作られていれば、その f と c が返ってくること"""
    for flare, curve in ((1.5, 1.0), (2.0, 1.6), (1.2, 2.5)):
        normalized = [
            (index / 23.0, modulate.flare_multiplier(index / 23.0, flare, curve))
            for index in range(24)
        ]
        fitted = silhouette.fit_flare(normalized)
        assert fitted["flare"] == pytest.approx(flare, abs=0.02)
        assert fitted["curve"] == pytest.approx(curve, abs=0.06)
        assert fitted["rms"] < 1e-3


def test_fit_flare_on_a_straight_cone_gives_curve_near_one():
    normalized = [(index / 23.0, 1.0 + 1.0 * (index / 23.0)) for index in range(24)]
    fitted = silhouette.fit_flare(normalized)
    assert fitted["curve"] == pytest.approx(1.0, abs=0.06)
    assert fitted["flare"] == pytest.approx(2.0, abs=0.02)


def test_mask_from_srgb_rows_uses_the_luminance_threshold():
    rows = [[(0.1, 0.1, 0.3), (0.9, 0.9, 0.9)]]
    mask = silhouette.mask_from_srgb_rows(rows, threshold=0.4)
    assert mask == [[True, False]]


def test_mask_from_alpha_rows():
    assert silhouette.mask_from_alpha_rows([[0.0, 0.6, 1.0]], threshold=0.5) == [
        [False, True, True]
    ]


def test_a_generated_skirt_profile_fits_its_own_flare_parameter():
    """生成したスカートの投影幅から当てはめた flare が、指定した flare と整合すること。

    投影の幅は長半径で決まるので、断面が裾で円に近づくぶん見かけの倍率は
    指定 flare(周長比)より**大幅に**小さく出る(1.55 → 見かけ 1.31)。
    参照画像の見かけの比と spec の flare を直接見比べてはいけない、という戒めでもある。
    """
    rings = 24
    ts = modulate.axis_fractions(rings)
    flare, curve = 1.55, 1.35
    depth = modulate.depth_ratio_profile(ts, 0.68, flare, curve)
    perimeters = [1.0 * modulate.flare_multiplier(t, flare, curve) for t in ts]
    widths = [
        2.0 * modulate.ellipse_semi_major(perimeter, ratio)
        for perimeter, ratio in zip(perimeters, depth)
    ]
    normalized = [(t, width / widths[0]) for t, width in zip(ts, widths)]
    fitted = silhouette.fit_flare(normalized)
    assert fitted["rms"] < 0.02
    assert fitted["flare"] < flare
    assert math.isclose(fitted["flare"], 1.31, abs_tol=0.04)


def test_trim_at_widest_cuts_below_the_hem():
    """切り出しが裾より下(タイツ・脚)まで届いていても、裾で打ち切れること"""
    profile = {
        "center": 25,
        "rows": [
            {"y": 0, "left": 20, "right": 30, "width": 11},
            {"y": 1, "left": 15, "right": 35, "width": 21},
            {"y": 2, "left": 10, "right": 40, "width": 31},  # 裾(最大)
            {"y": 3, "left": 22, "right": 28, "width": 7},  # タイツ
            {"y": 4, "left": 22, "right": 28, "width": 7},
        ],
    }
    trimmed = silhouette.trim_at_widest(profile)
    assert [row["y"] for row in trimmed["rows"]] == [0, 1, 2]
    assert silhouette.normalized_widths(trimmed)[-1][1] == pytest.approx(31 / 11)


def test_trim_at_widest_keeps_a_clean_profile_intact():
    profile = silhouette.width_profile(cone_mask())
    assert silhouette.trim_at_widest(profile)["rows"] == profile["rows"]


def test_trim_at_widest_handles_an_empty_profile():
    empty = {"center": 0, "rows": []}
    assert silhouette.trim_at_widest(empty)["rows"] == []


def test_untrimmed_profile_past_the_hem_gives_a_ratio_below_one():
    """打ち切らないと「裾/上端 < 1」という有り得ない値が出ることを固定する(回帰防止)"""
    profile = {
        "center": 25,
        "rows": [
            {"y": 0, "left": 20, "right": 30, "width": 11},
            {"y": 1, "left": 10, "right": 40, "width": 31},
            {"y": 2, "left": 23, "right": 27, "width": 5},
        ],
    }
    assert silhouette.normalized_widths(profile)[-1][1] < 1.0


def test_close_row_gaps_joins_nearby_spans():
    """服の内側の明るい模様(白パネル・飾り線)で割れた区間をつなぐ"""
    assert silhouette.close_row_gaps([(10, 20), (24, 40)], max_gap=4) == [(10, 40)]
    assert silhouette.close_row_gaps([(10, 20), (24, 40)], max_gap=2) == [(10, 20), (24, 40)]


def test_close_row_gaps_is_a_noop_without_a_gap_budget():
    spans = [(0, 3), (10, 20)]
    assert silhouette.close_row_gaps(spans, max_gap=0) == spans
    assert silhouette.close_row_gaps([], max_gap=5) == []


def test_close_row_gaps_can_over_merge_into_hair():
    """max_gap を大きくすると髪までつながる。実測で踏んだので上限の存在を固定する"""
    row_runs = [(4, 6), (20, 40), (52, 55)]  # 髪 / スカート / 髪
    assert silhouette.close_row_gaps(row_runs, max_gap=6) == [(4, 6), (20, 40), (52, 55)]
    assert silhouette.close_row_gaps(row_runs, max_gap=14) == [(4, 55)]


def test_width_profile_uses_the_gap_budget():
    mask = [[True] * 5 + [False] * 3 + [True] * 5]
    assert silhouette.width_profile(mask, center=6, min_width=1)["rows"][0]["width"] == 5
    wide = silhouette.width_profile(mask, center=6, min_width=1, max_gap=4)
    assert wide["rows"][0]["width"] == 13
