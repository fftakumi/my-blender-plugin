import pytest

from my_blender_plugin.operators import (
    MYPLUGIN_OT_fit_body_to_corset,
    fit_names,
    grid_positions,
    modifier_insert_index,
    proximity_distances,
    resolve_body_and_corset,
)


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


# --- proximity_distances ---


def test_proximity_distances_inverted_range():
    assert proximity_distances(0.05) == (0.05, 0.0)


def test_proximity_distances_rejects_zero_and_negative():
    with pytest.raises(ValueError):
        proximity_distances(0.0)
    with pytest.raises(ValueError):
        proximity_distances(-0.01)


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
