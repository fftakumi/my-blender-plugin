import pytest

from my_blender_plugin.operators import (
    MYPLUGIN_OT_fit_body_to_corset,
    MYPLUGIN_OT_generate_costume,
    bounding_dimensions,
    coverage_weight,
    fit_name_token,
    fit_names,
    grid_positions,
    modifier_insert_index,
    resolve_body_and_corset,
    resolve_fit_ranges,
)


def test_generate_costume_uses_the_ai_by_default():
    """解釈の主役は AI(計画どおり既定 ON)。フェイク bpy ではアノテーション値が
    default そのものになる(conftest の _fake_property)"""
    assert MYPLUGIN_OT_generate_costume.__annotations__["use_ai"] is True


def test_grid_positions_count():
    positions = grid_positions(3, 2.0)
    assert len(positions) == 9


def test_grid_positions_single():
    assert grid_positions(1, 5.0) == [(0.0, 0.0, 0.0)]


def test_grid_positions_centered():
    positions = grid_positions(4, 1.5)
    assert abs(sum(p[0] for p in positions)) < 1e-9
    assert abs(sum(p[1] for p in positions)) < 1e-9


def test_grid_positions_spacing():
    positions = grid_positions(2, 2.0)
    assert sorted(positions) == [
        (-1.0, -1.0, 0.0),
        (-1.0, 1.0, 0.0),
        (1.0, -1.0, 0.0),
        (1.0, 1.0, 0.0),
    ]


def test_grid_positions_on_ground_plane():
    assert all(p[2] == 0.0 for p in grid_positions(3, 1.0))


# --- resolve_body_and_corset ---


def test_resolve_body_is_non_active_side():
    body, corset, error = resolve_body_and_corset(
        [("Body", "MESH"), ("Corset", "MESH")], "Corset"
    )
    assert (body, corset, error) == ("Body", "Corset", None)


def test_resolve_body_order_independent():
    body, corset, error = resolve_body_and_corset(
        [("Corset", "MESH"), ("Body", "MESH")], "Corset"
    )
    assert (body, corset, error) == ("Body", "Corset", None)


def test_resolve_requires_two_selected():
    body, corset, error = resolve_body_and_corset([("Corset", "MESH")], "Corset")
    assert body is None and corset is None
    assert error is not None

    _, _, error3 = resolve_body_and_corset(
        [("A", "MESH"), ("B", "MESH"), ("C", "MESH")], "C"
    )
    assert error3 is not None


def test_resolve_rejects_non_mesh():
    _, _, error = resolve_body_and_corset(
        [("Body", "MESH"), ("Rig", "ARMATURE")], "Rig"
    )
    assert error is not None
    assert "Rig" in error


def test_resolve_requires_active():
    _, _, error = resolve_body_and_corset([("Body", "MESH"), ("Corset", "MESH")], None)
    assert error is not None


def test_resolve_active_must_be_selected():
    _, _, error = resolve_body_and_corset(
        [("Body", "MESH"), ("Corset", "MESH")], "Other"
    )
    assert error is not None


# --- bounding_dimensions ---


def test_bounding_dimensions_basic():
    points = [(0.0, 0.0, 0.0), (2.0, 5.0, 1.0), (-1.0, 1.0, 3.0)]
    assert bounding_dimensions(points) == (3.0, 5.0, 3.0)


def test_bounding_dimensions_single_point_is_zero_sized():
    assert bounding_dimensions([(1.0, 2.0, 3.0)]) == (0.0, 0.0, 0.0)


def test_bounding_dimensions_rejects_empty():
    with pytest.raises(ValueError):
        bounding_dimensions([])


# --- resolve_fit_ranges ---


def test_resolve_fit_ranges_keeps_explicit_values():
    assert resolve_fit_ranges(2.5, 0.4, (10.0, 8.0, 6.0)) == (2.5, 0.4)


def test_resolve_fit_ranges_auto_scales_with_corset_size():
    # 0 指定はコルセットの最大辺から決める。シーンのスケールが変わっても比率は同じ
    small = resolve_fit_ranges(0.0, 0.0, (0.2, 0.16, 0.12))
    large = resolve_fit_ranges(0.0, 0.0, (20.0, 16.0, 12.0))
    assert large[0] == pytest.approx(small[0] * 100)
    assert large[1] == pytest.approx(small[1] * 100)
    assert large[1] < large[0]


def test_resolve_fit_ranges_auto_only_for_the_zero_side():
    assert resolve_fit_ranges(3.0, 0.0, (20.0, 16.0, 12.0)) == (3.0, 2.0)
    assert resolve_fit_ranges(0.0, 1.0, (20.0, 16.0, 12.0)) == (10.0, 1.0)


def test_resolve_fit_ranges_rejects_invalid_values():
    with pytest.raises(ValueError):
        resolve_fit_ranges(-1.0, 0.0, (10.0, 10.0, 10.0))
    with pytest.raises(ValueError):
        resolve_fit_ranges(0.0, -1.0, (10.0, 10.0, 10.0))
    with pytest.raises(ValueError):
        resolve_fit_ranges(0.0, 0.0, (0.0, 0.0, 0.0))


# --- coverage_weight ---


def test_coverage_weight_ignores_depth_within_range():
    # 深いはみ出しでもウェイトが落ちない(これが「距離に関係なく押し込む」の要)
    shallow = coverage_weight(0.01, 5.0, 10.0, 1.0)
    deep = coverage_weight(9.9, 5.0, 10.0, 1.0)
    assert shallow == 1.0
    assert deep == 1.0


def test_coverage_weight_drops_outside_max_range():
    assert coverage_weight(10.0, 5.0, 10.0, 1.0) == 1.0
    assert coverage_weight(10.001, 5.0, 10.0, 1.0) == 0.0


def test_coverage_weight_feathers_toward_the_rim():
    # 縁に近いほど 0 へ。裾や胸元で引きつれないようになだらかに落ちる
    assert coverage_weight(0.5, 0.0, 10.0, 2.0) == 0.0
    assert coverage_weight(0.5, 2.0, 10.0, 2.0) == 1.0
    middle = coverage_weight(0.5, 1.0, 10.0, 2.0)
    assert middle == pytest.approx(0.5)
    quarter = coverage_weight(0.5, 0.5, 10.0, 2.0)
    assert 0.0 < quarter < middle


def test_coverage_weight_without_rim_band_is_binary():
    # 閉じたコルセット(縁なし)は距離が無限大扱いになり、常に全力
    assert coverage_weight(0.5, float("inf"), 10.0, 0.0) == 1.0
    assert coverage_weight(0.5, 0.0, 10.0, 0.0) == 1.0


# --- fit_names ---


def test_fit_names_keys_and_uniqueness():
    names = fit_names("Corset")
    assert set(names) == {"vertex_group", "vwp", "shrinkwrap", "smooth", "shapekey"}
    assert len(set(names.values())) == 5
    assert all("Corset" in value for value in names.values())


def test_fit_names_deterministic_and_distinct_per_corset():
    assert fit_names("Corset") == fit_names("Corset")
    a = set(fit_names("CorsetA").values())
    b = set(fit_names("CorsetB").values())
    assert a.isdisjoint(b)


def test_fit_name_token_short_name_unchanged():
    assert fit_name_token("Corset") == "Corset"


def test_fit_names_respect_blender_63_byte_limit():
    # 日本語の長い名前(3バイト/文字)でも全名前が63バイト以内に収まる
    long_name = "コルセット_ドレス用_きつめ調整済み_最終版"
    names = fit_names(long_name)
    for value in names.values():
        assert len(value.encode("utf-8")) <= 63


def test_fit_name_token_long_names_deterministic_and_distinct():
    long_a = "コ" * 30 + "A"
    long_b = "コ" * 30 + "B"
    assert fit_name_token(long_a) == fit_name_token(long_a)
    # 切り詰め部分が同じでもハッシュで区別される
    assert fit_name_token(long_a) != fit_name_token(long_b)


# --- modifier_insert_index ---


def test_modifier_insert_index_empty_stack():
    assert modifier_insert_index([]) == 0


def test_modifier_insert_index_no_armature_appends():
    assert modifier_insert_index(["SUBSURF"]) == 1


def test_modifier_insert_index_before_armature():
    assert modifier_insert_index(["ARMATURE"]) == 0
    assert modifier_insert_index(["SUBSURF", "ARMATURE", "SUBSURF"]) == 1


# --- オペレーターのメタ情報(フェイク bpy で import できることの確認も兼ねる) ---


def test_fit_operator_meta():
    assert MYPLUGIN_OT_fit_body_to_corset.bl_idname == "myplugin.fit_body_to_corset"
    assert "UNDO" in MYPLUGIN_OT_fit_body_to_corset.bl_options
    assert MYPLUGIN_OT_fit_body_to_corset.bl_description
