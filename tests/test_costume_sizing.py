import math

import pytest

from my_blender_plugin.costume import sizing


def test_defaults_scale_with_height():
    small = sizing.resolve(1.0)
    large = sizing.resolve(2.0)
    assert large["body"]["waist"] == pytest.approx(small["body"]["waist"] * 2.0)
    assert large["body"]["hip"] == pytest.approx(small["body"]["hip"] * 2.0)


def test_ratio_override():
    table = sizing.resolve(1.5, {"waist": 0.40})
    assert table["body"]["waist"] == pytest.approx(0.60)
    assert table["ratios"]["waist"] == pytest.approx(0.40)


def test_absolute_override_wins_over_ratio():
    table = sizing.resolve(1.5, {"waist": 0.40, "waist_m": 0.66})
    assert table["body"]["waist"] == pytest.approx(0.66)
    assert table["ratios"]["waist"] == pytest.approx(0.66 / 1.5)


def test_ease_applies_to_circumferences_only():
    table = sizing.resolve(1.5, ease=0.10)
    assert table["waist"] == pytest.approx(table["body"]["waist"] * 1.10)
    assert table["hip"] == pytest.approx(table["body"]["hip"] * 1.10)
    # 垂直距離にはゆとりを掛けない
    assert table["hip_drop"] == pytest.approx(table["body"]["hip_drop"])


def test_radius_matches_circumference():
    table = sizing.resolve(1.53, ease=0.03)
    assert table["waist_radius"] == pytest.approx(table["waist"] / (2 * math.pi))
    assert table["hip_radius"] == pytest.approx(table["hip"] / (2 * math.pi))


def test_circumference_radius_roundtrip():
    assert sizing.radius_to_circumference(sizing.circumference_to_radius(0.6)) == pytest.approx(0.6)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"assumed_height": 0.0},
        {"assumed_height": 1.5, "ease": -0.1},
        {"assumed_height": 1.5, "overrides": {"waist": -0.1}},
        {"assumed_height": 1.5, "overrides": {"waist_m": 0.0}},
        {"assumed_height": 1.5, "overrides": {"wing_span": 0.5}},
    ],
)
def test_bad_input_raises(kwargs):
    with pytest.raises(sizing.SizingError):
        sizing.resolve(**kwargs)


def test_hip_is_wider_than_waist_by_default():
    table = sizing.resolve(1.53)
    assert table["hip_radius"] > table["waist_radius"]
