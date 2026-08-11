import pytest

from my_blender_plugin.costume import palette


def flat_rgba(colors, alpha=1.0):
    out = []
    for red, green, blue in colors:
        out.extend([red, green, blue, alpha])
    return out


def test_rgba_to_rgb_drops_transparent_pixels():
    flat = [1.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0]
    assert palette.rgba_to_rgb(flat) == [(1.0, 0.0, 0.0)]


def test_rgba_to_rgb_stride_samples_evenly():
    flat = flat_rgba([(index / 10.0, 0.0, 0.0) for index in range(10)])
    assert len(palette.rgba_to_rgb(flat, stride=2)) == 5


def test_rgba_to_rgb_rejects_ragged_input():
    with pytest.raises(ValueError):
        palette.rgba_to_rgb([1.0, 0.0, 0.0])
    with pytest.raises(ValueError):
        palette.rgba_to_rgb([1.0, 0.0, 0.0, 1.0], stride=0)


def test_median_cut_of_a_single_colour():
    result = palette.median_cut([(0.2, 0.3, 0.4)] * 50, 4)
    assert len(result) == 1
    assert result[0]["color"] == pytest.approx((0.2, 0.3, 0.4))
    assert result[0]["weight"] == pytest.approx(1.0)


def test_median_cut_separates_two_clusters():
    pixels = [(0.9, 0.1, 0.1)] * 60 + [(0.1, 0.1, 0.9)] * 20
    result = palette.median_cut(pixels, 2)
    assert len(result) == 2
    # 母数の多い赤が先
    assert result[0]["color"][0] > result[0]["color"][2]
    assert result[0]["weight"] == pytest.approx(0.75)
    assert result[1]["weight"] == pytest.approx(0.25)


def test_median_cut_is_deterministic_regardless_of_input_order():
    pixels = [(0.9, 0.1, 0.1)] * 30 + [(0.1, 0.8, 0.2)] * 30 + [(0.2, 0.2, 0.9)] * 30
    first = palette.median_cut(pixels, 3)
    second = palette.median_cut(list(reversed(pixels)), 3)
    assert [item["color"] for item in first] == pytest.approx(
        [item["color"] for item in second]
    )


def test_median_cut_never_returns_more_than_requested():
    pixels = [(index / 100.0, 0.5, 0.5) for index in range(100)]
    assert len(palette.median_cut(pixels, 5)) <= 5


def test_median_cut_handles_empty_and_bad_count():
    assert palette.median_cut([], 3) == []
    with pytest.raises(ValueError):
        palette.median_cut([(0.0, 0.0, 0.0)], 0)


def test_weights_sum_to_one():
    pixels = [(0.9, 0.1, 0.1)] * 7 + [(0.1, 0.1, 0.9)] * 13
    result = palette.median_cut(pixels, 4)
    assert sum(item["weight"] for item in result) == pytest.approx(1.0)


def test_drop_extremes_removes_the_white_background():
    pixels = [(1.0, 1.0, 1.0)] * 80 + [(0.05, 0.12, 0.42)] * 20
    kept = palette.dominant_colors(pixels, count=1, drop_extremes=True)
    assert kept[0]["color"][2] > kept[0]["color"][0]  # 青が残る


def test_drop_extremes_keeps_a_genuinely_white_garment():
    """真っ白しか無い画像で全部落として空を返さないこと"""
    kept = palette.dominant_colors([(1.0, 1.0, 1.0)] * 40, count=2, drop_extremes=True)
    assert kept
    assert kept[0]["color"] == pytest.approx((1.0, 1.0, 1.0))


def test_to_material_params_names_the_first_colour_main():
    colors = palette.median_cut([(0.9, 0.1, 0.1)] * 30 + [(0.1, 0.1, 0.9)] * 10, 2)
    materials = palette.to_material_params(colors)
    assert "main" in materials
    assert "accent1" in materials
    assert materials["main"]["base_color"][0] > materials["main"]["base_color"][2]


def test_to_material_params_gives_grey_less_sheen_than_a_saturated_colour():
    grey = palette.to_material_params([{"color": (0.5, 0.5, 0.5), "weight": 1.0}])
    vivid = palette.to_material_params([{"color": (0.9, 0.05, 0.05), "weight": 1.0}])
    assert grey["main"]["sheen"] < vivid["main"]["sheen"]
    assert grey["main"]["roughness"] > vivid["main"]["roughness"]


def test_material_params_pass_spec_validation():
    from my_blender_plugin.costume import spec as spec_module

    colors = palette.median_cut([(0.2, 0.3, 0.7)] * 20, 1)
    spec = spec_module.default_spec()
    spec["materials"] = palette.to_material_params(colors)
    for part in spec["parts"]:
        part["material"] = "main"
    normalized = spec_module.normalize_spec(spec)
    assert normalized["materials"]["main"]["base_color"] == pytest.approx([0.2, 0.3, 0.7])
