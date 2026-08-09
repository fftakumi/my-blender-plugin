from my_blender_plugin.operators import grid_positions


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
