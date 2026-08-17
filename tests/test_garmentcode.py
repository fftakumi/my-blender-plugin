"""GarmentCode 取り込みサブパッケージのテスト。

bpy 依存モジュール(garmentcode.build / garmentcode.weld)はここでは触らない
(フェイク bpy には mathutils / bmesh が無い)。テストするのは
「Blender が無くても答えが決まる部分」= 曲率・座標変換・リサンプル・
ステッチ対応付け・手足クラスタ・spec 検証。
"""

import math
from pathlib import Path

import pytest

from my_blender_plugin import garmentcode, operators, panels
from my_blender_plugin.garmentcode import (
    curves,
    limbs,
    panel_mesh,
    spec,
    stitches,
    topology,
    transform,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "data" / "garmentcode_right_half_specification.json"


@pytest.fixture(scope="module")
def parsed():
    return spec.load_spec(FIXTURE)


# --------------------------------------------------------------------------
# 曲率のテッセレーション
# --------------------------------------------------------------------------


def _convex_hull(points):
    """モノトーンチェーンによる 2D 凸包(反時計回り)"""

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    ordered = sorted(set(points))
    if len(ordered) <= 2:
        return ordered
    lower = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _inside_hull(point, hull, tolerance=1e-9):
    count = len(hull)
    for index in range(count):
        ax, ay = hull[index]
        bx, by = hull[(index + 1) % count]
        cross = (bx - ax) * (point[1] - ay) - (by - ay) * (point[0] - ax)
        if cross < -tolerance:
            return False
    return True


def test_circle_arc_reproduces_the_stated_radius():
    """circle 曲率は半径を復元できる(rel_rad の逆解きが厳密に戻る)"""
    p0, p1 = (0.0, 0.0), (10.0, 0.0)
    arc = curves.circle_arc(p0, p1, (8.0, 0, 0))
    assert abs(arc["radius"] - 8.0) < 1e-9


def test_circle_samples_lie_on_the_circle():
    p0, p1 = (3.0, -2.0), (11.0, 5.0)
    radius = 9.0
    arc = curves.circle_arc(p0, p1, (radius, 0, 0))
    points = curves.sample_curved_edge(p0, p1, {"type": "circle", "params": (radius, 0, 0)}, 64)
    for point in points:
        assert abs(curves.distance(point, arc["center"]) - radius) < 1e-9


def test_circle_keeps_the_endpoints():
    p0, p1 = (0.0, 0.0), (10.0, 0.0)
    points = curves.sample_curved_edge(p0, p1, {"type": "circle", "params": (8.0, 0, 0)}, 32)
    assert curves.distance(points[0], p0) < 1e-9
    assert curves.distance(points[-1], p1) < 1e-9


def test_circle_large_arc_takes_the_longer_way():
    p0, p1 = (0.0, 0.0), (10.0, 0.0)
    short = curves.circle_arc(p0, p1, (8.0, 0, 0))
    long = curves.circle_arc(p0, p1, (8.0, 1, 0))
    assert abs(long["sweep"]) > abs(short["sweep"])
    # 半径はどちらでも spec の値に戻る
    assert abs(long["radius"] - 8.0) < 1e-9


def test_circle_right_flips_the_bulge_side():
    p0, p1 = (0.0, 0.0), (10.0, 0.0)
    left = curves.sample_curved_edge(p0, p1, {"type": "circle", "params": (8.0, 0, 0)}, 33)
    right = curves.sample_curved_edge(p0, p1, {"type": "circle", "params": (8.0, 0, 1)}, 33)
    assert left[16][1] * right[16][1] < 0.0


def test_circle_arc_rejects_a_zero_length_edge():
    with pytest.raises(ValueError):
        curves.circle_arc((1.0, 1.0), (1.0, 1.0), (5.0, 0, 0))


def test_real_spec_circle_edge_keeps_its_radius(parsed):
    """フィクスチャの実データ(右前身頃の襟ぐり)でも半径が戻ること"""
    panel = parsed["panels"]["right_ftorso"]
    edge = panel["edges"][1]
    assert edge["curvature"]["type"] == "circle"
    a, b = edge["endpoints"]
    arc = curves.circle_arc(panel["vertices"][a], panel["vertices"][b], edge["curvature"]["params"])
    assert abs(arc["radius"] - edge["curvature"]["params"][0]) < 1e-9


@pytest.mark.parametrize(
    "curvature",
    [
        {"type": "quadratic", "params": ((0.5, 0.4),)},
        {"type": "cubic", "params": ((0.3, 0.5), (0.7, -0.2))},
    ],
)
def test_bezier_hits_the_endpoints(curvature):
    p0, p1 = (1.0, 2.0), (9.0, 4.0)
    points = curves.sample_curved_edge(p0, p1, curvature, 40)
    assert curves.distance(points[0], p0) < 1e-12
    assert curves.distance(points[-1], p1) < 1e-12


@pytest.mark.parametrize(
    "curvature",
    [
        {"type": "quadratic", "params": ((0.5, 0.4),)},
        {"type": "cubic", "params": ((0.3, 0.5), (0.7, -0.2))},
    ],
)
def test_bezier_stays_within_the_control_hull(curvature):
    p0, p1 = (1.0, 2.0), (9.0, 4.0)
    hull = _convex_hull(curves.bezier_control_points(p0, p1, curvature["params"]))
    for point in curves.sample_curved_edge(p0, p1, curvature, 64):
        assert _inside_hull(point, hull)


def test_curvature_control_points_use_the_edge_frame():
    """(u, v) は「エッジ方向」と「その左手側」。エッジが回れば制御点も回る"""
    control = curves.bezier_control_points((0.0, 0.0), (0.0, 10.0), ((0.5, 0.1),))
    # エッジは +Y 方向なので perp は -X 方向
    assert control[1] == pytest.approx((-1.0, 5.0))


def test_no_curvature_is_a_straight_segment():
    assert curves.sample_curved_edge((0.0, 0.0), (3.0, 4.0), None) == [(0.0, 0.0), (3.0, 4.0)]


def test_unknown_curvature_type_raises():
    with pytest.raises(ValueError):
        curves.sample_curved_edge((0.0, 0.0), (1.0, 0.0), {"type": "spiral", "params": ()})


# --------------------------------------------------------------------------
# 等弧長リサンプル
# --------------------------------------------------------------------------


def _spacings(points):
    return [curves.distance(points[i], points[i + 1]) for i in range(len(points) - 1)]


def test_resample_gives_uniform_spacing_on_a_straight_line():
    points, total = curves.resample_polyline([(0.0, 0.0), (10.0, 0.0)], 1.0)
    assert total == pytest.approx(10.0)
    assert len(points) == 11
    for gap in _spacings(points):
        assert gap == pytest.approx(1.0, abs=1e-9)


def test_resample_spacing_is_uniform_even_when_it_does_not_divide():
    points, total = curves.resample_polyline([(0.0, 0.0), (10.0, 0.0)], 1.4)
    gaps = _spacings(points)
    step = total / max(1, round(total / 1.4))
    for gap in gaps:
        assert gap == pytest.approx(step, abs=1e-9)
    # 目標からの外れは丸めぶん(半刻み)以内
    assert abs(step - 1.4) < 0.7


def test_resample_follows_a_curve_uniformly():
    dense = [
        (10.0 * math.cos(t / 200.0 * math.pi / 2), 10.0 * math.sin(t / 200.0 * math.pi / 2))
        for t in range(201)
    ]
    points, total = curves.resample_polyline(dense, 1.5)
    gaps = _spacings(points)
    step = total / (len(points) - 1)
    # 弦長は弧長よりわずかに短いので厳密には一致しない。1% 以内なら十分均一
    for gap in gaps:
        assert abs(gap - step) < step * 0.01
    assert abs(step - 1.5) < 0.75


def test_resample_keeps_both_endpoints():
    points, _total = curves.resample_polyline([(0.0, 0.0), (2.0, 0.0), (2.0, 3.0)], 0.7)
    assert points[0] == (0.0, 0.0)
    assert points[-1] == (2.0, 3.0)


def test_resample_rejects_a_non_positive_target():
    with pytest.raises(ValueError):
        curves.resample_polyline([(0.0, 0.0), (1.0, 0.0)], 0.0)


# --------------------------------------------------------------------------
# 座標変換
# --------------------------------------------------------------------------


def _axis_matrix(axis, angle):
    c, s = math.cos(angle), math.sin(angle)
    if axis == 0:
        return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    if axis == 1:
        return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def test_euler_is_extrinsic_xyz():
    """外因性 XYZ = 行列としては Rz @ Ry @ Rx(Blender の Euler('XYZ') と同じ)"""
    rx, ry, rz = 0.3, -0.7, 1.1
    expected = transform.matmul(
        _axis_matrix(2, rz), transform.matmul(_axis_matrix(1, ry), _axis_matrix(0, rx))
    )
    got = transform.euler_xyz_matrix(rx, ry, rz)
    for row in range(3):
        for col in range(3):
            assert got[row][col] == pytest.approx(expected[row][col], abs=1e-12)


def test_euler_matrix_is_orthonormal():
    matrix = transform.euler_xyz_matrix(0.4, 1.2, -0.9)
    for i in range(3):
        for j in range(3):
            dot = sum(matrix[k][i] * matrix[k][j] for k in range(3))
            assert dot == pytest.approx(1.0 if i == j else 0.0, abs=1e-12)


def test_identity_panel_is_just_the_y_up_to_z_up_flip():
    panel = {"rotation": (0.0, 0.0, 0.0), "translation": (1.0, 2.0, 3.0)}
    matrix, location = transform.panel_transform(panel)
    assert location == (1.0, -3.0, 2.0)  # (tx, -tz, ty)
    for row in range(3):
        for col in range(3):
            assert matrix[row][col] == pytest.approx(transform.RX90[row][col], abs=1e-12)


def test_identity_panel_maps_panel_y_to_world_z():
    panel = {"rotation": (0.0, 0.0, 0.0), "translation": (0.0, 0.0, 0.0)}
    matrix, location = transform.panel_transform(panel)
    assert transform.apply(matrix, location, (1.0, 0.0)) == pytest.approx((1.0, 0.0, 0.0))
    assert transform.apply(matrix, location, (0.0, 1.0)) == pytest.approx((0.0, 0.0, 1.0))


def test_cuff_rotation_matches_the_hand_computed_matrix():
    """実データのカフ(rotation = [0, 0, -90])。M = Rx(90) @ Rz(-90)"""
    panel = {"rotation": (0.0, 0.0, -90.0), "translation": (-55.0, 130.0, 17.5)}
    matrix, location = transform.panel_transform(panel)
    expected = ((0.0, 1.0, 0.0), (0.0, 0.0, -1.0), (-1.0, 0.0, 0.0))
    for row in range(3):
        for col in range(3):
            assert matrix[row][col] == pytest.approx(expected[row][col], abs=1e-12)
    assert location == pytest.approx((-55.0, -17.5, 130.0))
    # パネルの +X は world の -Z へ、パネルの +Y は world の +X へ
    origin = transform.apply(matrix, location, (0.0, 0.0))
    assert transform.apply(matrix, location, (1.0, 0.0)) == pytest.approx(
        (origin[0], origin[1], origin[2] - 1.0)
    )
    assert transform.apply(matrix, location, (0.0, 1.0)) == pytest.approx(
        (origin[0] + 1.0, origin[1], origin[2])
    )


def test_place_panel_transforms_every_point(parsed):
    panel = parsed["panels"]["sl_right_cuff_f"]
    placed = transform.place_panel(panel, panel["vertices"])
    assert len(placed) == len(panel["vertices"])
    assert all(len(point) == 3 for point in placed)


# --------------------------------------------------------------------------
# パネルの外形とメッシュ入力
# --------------------------------------------------------------------------


_SQUARE = {
    "vertices": [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
    "edges": [
        {"endpoints": (0, 1), "curvature": None},
        {"endpoints": (1, 2), "curvature": None},
        {"endpoints": (2, 3), "curvature": None},
        {"endpoints": (3, 0), "curvature": None},
    ],
    "translation": (0.0, 0.0, 0.0),
    "rotation": (0.0, 0.0, 0.0),
    "label": None,
}


def test_order_edge_loop_handles_a_shuffled_edge_list():
    edges = [
        {"endpoints": (2, 3)},
        {"endpoints": (0, 1)},
        {"endpoints": (3, 0)},
        {"endpoints": (1, 2)},
    ]
    order = panel_mesh.order_edge_loop(edges)
    assert sorted(index for index, _rev in order) == [0, 1, 2, 3]
    # 端点を辿って輪が閉じること
    node = edges[order[0][0]]["endpoints"][1 if not order[0][1] else 0]
    for index, reversed_flag in order[1:]:
        a, b = edges[index]["endpoints"]
        start, end = (b, a) if reversed_flag else (a, b)
        assert start == node
        node = end
    assert node == edges[order[0][0]]["endpoints"][0]


def test_panel_boundary_chains_follow_the_spec_edge_direction():
    boundary, chains = panel_mesh.panel_boundary(_SQUARE, 1.0)
    assert set(chains) == {0, 1, 2, 3}
    for index, edge in enumerate(_SQUARE["edges"]):
        a, b = edge["endpoints"]
        chain = chains[index]
        assert boundary[chain[0]] == pytest.approx(_SQUARE["vertices"][a])
        assert boundary[chain[-1]] == pytest.approx(_SQUARE["vertices"][b])


def test_panel_boundary_does_not_duplicate_the_corner_points():
    boundary, _chains = panel_mesh.panel_boundary(_SQUARE, 1.0)
    assert len(boundary) == 40  # 4辺 x 10刻み、角は1回ずつ
    assert len(set(boundary)) == len(boundary)


def test_panel_boundary_rejects_a_non_positive_target():
    with pytest.raises(ValueError):
        panel_mesh.panel_boundary(_SQUARE, 0.0)


def test_hex_interior_points_stay_inside_with_a_margin():
    boundary, _chains = panel_mesh.panel_boundary(_SQUARE, 1.0)
    interior = panel_mesh.hex_interior_points(boundary, 1.0)
    assert interior
    margin = 1.0 * panel_mesh.INTERIOR_MARGIN
    for x, y in interior:
        assert panel_mesh.point_in_polygon(x, y, boundary)
        assert panel_mesh.distance_to_polygon(x, y, boundary) > margin


def test_hex_interior_rows_use_the_sqrt3_over_2_ratio():
    boundary, _chains = panel_mesh.panel_boundary(_SQUARE, 1.0)
    rows = sorted({round(y, 6) for _x, y in panel_mesh.hex_interior_points(boundary, 1.0)})
    gaps = [rows[i + 1] - rows[i] for i in range(len(rows) - 1)]
    for gap in gaps:
        assert gap == pytest.approx(panel_mesh.HEX_ROW_RATIO, abs=1e-6)


def test_polygon_area_of_the_square():
    boundary, _chains = panel_mesh.panel_boundary(_SQUARE, 1.0)
    assert panel_mesh.polygon_area(boundary) == pytest.approx(100.0)


def test_panel_inputs_constraints_close_the_outline():
    inputs = panel_mesh.panel_inputs(_SQUARE, 1.0)
    count = inputs["boundary_count"]
    assert len(inputs["constraints"]) == count
    assert inputs["constraints"][-1] == (count - 1, 0)
    assert len(inputs["points"]) > count  # 内部点が足されている
    assert inputs["area"] == pytest.approx(100.0)


def test_panel_inputs_works_on_the_real_curved_panel(parsed):
    inputs = panel_mesh.panel_inputs(parsed["panels"]["right_ftorso"], 1.5)
    assert inputs["boundary_count"] > 50
    assert inputs["area"] > 0.0
    assert set(inputs["chains"]) == set(range(len(parsed["panels"]["right_ftorso"]["edges"])))


def test_merge_close_points_is_a_noop_when_nothing_overlaps():
    points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    kept, faces, chains, removed = panel_mesh.merge_close_points(
        points, [(0, 1, 2)], {0: [0, 1]}, 0.001
    )
    assert removed == 0
    assert kept == points
    assert faces == [(0, 1, 2)]
    assert chains == {0: [0, 1]}


def test_merge_close_points_drops_duplicates_and_degenerate_faces():
    points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.0, 1e-9)]
    kept, faces, chains, removed = panel_mesh.merge_close_points(
        points, [(0, 1, 2), (0, 1, 3)], {0: [0, 3]}, 0.001
    )
    assert removed == 1
    assert len(kept) == 3
    assert faces == [(0, 1, 2)]  # (0,1,3) は 0 と 3 が同一になって潰れる
    assert chains == {0: [0, 0]}


def test_triangle_area_sum_matches_the_outline():
    assert panel_mesh.triangle_area_sum(
        [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)], [(0, 1, 2), (0, 2, 3)]
    ) == pytest.approx(4.0)


# --------------------------------------------------------------------------
# ステッチの対応付け
# --------------------------------------------------------------------------


def test_pair_stitch_prefers_the_shorter_direction():
    """相手の chain が逆向きのときは反転して繋ぐ(参照データでは 40/40 が反転だった)"""
    verts = [
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 1.0),
        (0.0, 0.0, 2.0),
        (1.0, 0.0, 2.0),
        (1.0, 0.0, 1.0),
        (1.0, 0.0, 0.0),
    ]
    pairs, was_reversed = stitches.pair_stitch([0, 1, 2], [3, 4, 5], verts)
    assert was_reversed is True
    assert pairs == {(0, 5), (1, 4), (2, 3)}


def test_pair_stitch_keeps_the_forward_direction_when_it_is_shorter():
    verts = [
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 1.0),
        (0.0, 0.0, 2.0),
        (1.0, 0.0, 0.0),
        (1.0, 0.0, 1.0),
        (1.0, 0.0, 2.0),
    ]
    pairs, was_reversed = stitches.pair_stitch([0, 1, 2], [3, 4, 5], verts)
    assert was_reversed is False
    assert pairs == {(0, 3), (1, 4), (2, 5)}


def test_match_chains_gives_every_vertex_at_least_one_spring():
    """点数が違っても、どちらの側の頂点にも必ず1本は付く(両方向マッチの和)"""
    params_a = [0.0, 0.5, 1.0]
    params_b = [0.0, 0.25, 0.5, 0.75, 1.0]
    pairs = stitches.match_chains([0, 1, 2], params_a, [3, 4, 5, 6, 7], params_b)
    assert {a for a, _b in pairs} == {0, 1, 2}
    assert {b for _a, b in pairs} == {3, 4, 5, 6, 7}


def test_build_sewing_edges_skips_dart_apex_self_loops():
    """ダーツの先端は2本のエッジが同じ頂点を共有する。自己ループは捨てる"""
    verts = [(0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 2.0)]
    chains = {("p", 0): [0, 1, 2], ("p", 1): [2, 1, 0]}
    stitch = [({"panel": "p", "edge": 0}, {"panel": "p", "edge": 1})]
    sewing, stats = stitches.build_sewing_edges(stitch, chains, verts)
    assert sewing == []
    assert stats["self_loops"] == 3
    assert stats["reversed"] == 1
    assert stats["stitches"] == 1


def test_build_sewing_edges_normalises_the_pair_order():
    verts = [(0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (1.0, 0.0, 1.0)]
    chains = {("a", 0): [2, 3], ("b", 0): [0, 1]}
    stitch = [({"panel": "a", "edge": 0}, {"panel": "b", "edge": 0})]
    sewing, _stats = stitches.build_sewing_edges(stitch, chains, verts)
    assert sewing == [(0, 2), (1, 3)]  # 常に小さい添字が先で昇順


def test_build_sewing_edges_reports_missing_edges():
    stitch = [({"panel": "a", "edge": 0}, {"panel": "ghost", "edge": 3})]
    sewing, stats = stitches.build_sewing_edges(stitch, {("a", 0): [0, 1]}, [(0.0, 0.0, 0.0)] * 2)
    assert sewing == []
    assert stats["missing"] == [("ghost", 3)]
    assert stats["stitches"] == 0


def test_gap_stats():
    verts = [(0.0, 0.0, 0.0), (0.0, 0.0, 2.0), (0.0, 0.0, 0.0), (0.0, 0.0, 4.0)]
    assert stitches.gap_stats([(0, 1), (2, 3)], verts) == {"mean": 3.0, "max": 4.0, "count": 2}
    assert stitches.gap_stats([], verts) is None


# --------------------------------------------------------------------------
# 手足の自動センタリング
# --------------------------------------------------------------------------


def _box(x_lo, x_hi, half=5.0):
    return [
        (x, y, z)
        for x in (x_lo, x_hi)
        for y in (-half, half)
        for z in (-half, half)
    ]


def _arm_points(x_lo, x_hi, center_for_x):
    """腕の断面サンプル。各 x で中心に9点、上下左右に1点ずつ(中央値が中心に来る)"""
    points = []
    for step in range(int(x_lo), int(x_hi) + 1):
        x = float(step)
        cy, cz = center_for_x(x)
        points.extend([(x, cy, cz)] * 7)
        points.extend(
            [(x, cy - 4.0, cz), (x, cy + 4.0, cz), (x, cy, cz - 4.0), (x, cy, cz + 4.0)]
        )
    return points


@pytest.fixture
def limb_scene():
    """袖(x 20..44)+ カフ(x 46..56)+ 胴(x -20..0)の合成データ"""
    verts = _box(-20.0, 0.0) + _box(20.0, 44.0) + _box(46.0, 56.0)
    panel_ranges = {"torso": (0, 8), "sleeve": (8, 16), "cuff": (16, 24)}
    panels = {
        "torso": {"label": "body"},
        "sleeve": {"label": "arm"},
        "cuff": {"label": "arm"},
    }
    stitch_list = [({"panel": "sleeve", "edge": 0}, {"panel": "cuff", "edge": 0})]
    body = _arm_points(20, 57, lambda x: (3.0, 7.0) if x < 47.0 else (3.0, 2.0))
    return {
        "verts": verts,
        "panel_ranges": panel_ranges,
        "panels": panels,
        "stitches": stitch_list,
        "body": body,
    }


def test_is_limb_panel_by_label_and_by_name():
    assert limbs.is_limb_panel("whatever", {"label": "arm"})
    assert limbs.is_limb_panel("sl_right_cuff_f", {"label": None})
    assert limbs.is_limb_panel("left_sleeve_b", {})
    assert not limbs.is_limb_panel("right_ftorso", {"label": "body"})


def test_clusters_separate_the_sleeve_span_from_the_cuff_span(limb_scene):
    clusters = limbs.detect_limb_clusters(
        limb_scene["panels"],
        limb_scene["stitches"],
        limb_scene["panel_ranges"],
        limb_scene["verts"],
    )
    assert [cluster["panels"] for cluster in clusters] == [["sleeve"], ["cuff"]]
    assert all(cluster["axis"] == 0 for cluster in clusters)  # 支配軸は X
    assert clusters[0]["a_lo"] == 20.0 and clusters[0]["a_hi"] == 44.0
    assert clusters[1]["a_lo"] == 46.0 and clusters[1]["a_hi"] == 56.0


def test_clusters_ignore_non_limb_panels(limb_scene):
    clusters = limbs.detect_limb_clusters(
        limb_scene["panels"],
        limb_scene["stitches"],
        limb_scene["panel_ranges"],
        limb_scene["verts"],
    )
    assert all("torso" not in cluster["panels"] for cluster in clusters)


def test_clusters_skip_components_that_are_not_tube_like():
    verts = _box(0.0, 10.0, half=5.0)  # 10 x 10 x 10 の立方体
    clusters = limbs.detect_limb_clusters(
        {"blob": {"label": "arm"}}, [], {"blob": (0, 8)}, verts
    )
    assert clusters == []


def test_limb_offsets_centre_the_tube_on_the_limb_axis(limb_scene):
    clusters = limbs.detect_limb_clusters(
        limb_scene["panels"],
        limb_scene["stitches"],
        limb_scene["panel_ranges"],
        limb_scene["verts"],
    )
    report = limbs.limb_offsets(
        clusters,
        limb_scene["panel_ranges"],
        limb_scene["verts"],
        limb_scene["body"],
        limbs.garment_centroid(limb_scene["verts"]),
    )
    assert [entry["panels"] for entry in report] == [["sleeve"], ["cuff"]]
    assert all(entry["applied"] for entry in report)
    # 袖とカフで別々のオフセットになる(実データでも袖 Z+13.11 / カフ Z+3.20 だった)
    assert report[0]["delta"] == pytest.approx((0.0, 3.0, 7.0))
    assert report[1]["delta"] == pytest.approx((0.0, 3.0, 2.0))
    assert report[0]["axis"] == "X"
    assert report[0]["offset"] == {"Y": pytest.approx(3.0), "Z": pytest.approx(7.0)}


def test_centring_moves_the_tube_in_both_perpendicular_axes(limb_scene):
    """持ち上げる(片方向)だけでは駄目、という失敗の再演防止"""
    moved, report = limbs.center_limbs(
        limb_scene["panels"],
        limb_scene["stitches"],
        limb_scene["panel_ranges"],
        limb_scene["verts"],
        limb_scene["body"],
    )
    for entry in report:
        assert sum(1 for value in entry["delta"] if abs(value) > 1e-9) == 2
    sleeve = moved[8:16]
    assert 0.5 * (min(p[1] for p in sleeve) + max(p[1] for p in sleeve)) == pytest.approx(3.0)
    assert 0.5 * (min(p[2] for p in sleeve) + max(p[2] for p in sleeve)) == pytest.approx(7.0)


def test_centring_leaves_non_limb_panels_alone(limb_scene):
    moved, _report = limbs.center_limbs(
        limb_scene["panels"],
        limb_scene["stitches"],
        limb_scene["panel_ranges"],
        limb_scene["verts"],
        limb_scene["body"],
    )
    assert moved[0:8] == limb_scene["verts"][0:8]


def test_centring_without_body_points_does_nothing(limb_scene):
    moved, report = limbs.center_limbs(
        limb_scene["panels"],
        limb_scene["stitches"],
        limb_scene["panel_ranges"],
        limb_scene["verts"],
        [],
    )
    assert moved == limb_scene["verts"]
    assert all(not entry["applied"] for entry in report)
    assert all("note" in entry for entry in report)


def test_absurd_offsets_are_refused(limb_scene):
    far = _arm_points(20, 57, lambda x: (300.0, 300.0))
    _moved, report = limbs.center_limbs(
        limb_scene["panels"],
        limb_scene["stitches"],
        limb_scene["panel_ranges"],
        limb_scene["verts"],
        far,
    )
    assert all(not entry["applied"] for entry in report)
    assert all("%.1f" % limbs.MAX_OFFSET in entry["note"] for entry in report)


def test_measure_axis_center_uses_the_outer_half_of_the_span():
    """体に近い側は胴が混ざるので、遠い側の半分だけで測る"""
    # 内側(x<10)は中心 (0,0)、外側(x>10)は中心 (5,5)。境界の x=10 は
    # どちらの窓にも入るので station を置かない(測定窓の切れ目の検証ではないため)
    points = _arm_points(0, 9, lambda x: (0.0, 0.0)) + _arm_points(
        11, 20, lambda x: (5.0, 5.0)
    )
    outer = limbs.measure_axis_center(points, 0, 0.0, 20.0, outward_sign=1.0)
    assert outer["center1"] == pytest.approx(5.0)
    assert outer["center2"] == pytest.approx(5.0)
    inner = limbs.measure_axis_center(points, 0, 0.0, 20.0, outward_sign=-1.0)
    assert inner["center1"] == pytest.approx(0.0)


def test_measure_axis_center_returns_none_without_samples():
    assert limbs.measure_axis_center([], 0, 0.0, 10.0, 1.0) is None
    assert limbs.measure_axis_center([(0.0, 0.0, 0.0)] * 20, 0, 5.0, 5.0, 1.0) is None


def test_garment_centroid():
    assert limbs.garment_centroid([(0.0, 0.0, 0.0), (2.0, 4.0, 6.0)]) == (1.0, 2.0, 3.0)
    assert limbs.garment_centroid([]) == (0.0, 0.0, 0.0)


def test_real_spec_detects_the_sleeve_and_cuff_cluster(parsed):
    """実データの右腕: 袖2枚 + カフ2枚 が軸方向に2クラスタへ割れる"""
    panel_ranges = {}
    verts = []
    for name in parsed["order"]:
        panel = parsed["panels"][name]
        placed = transform.place_panel(panel, panel["vertices"])
        panel_ranges[name] = (len(verts), len(verts) + len(placed))
        verts.extend(placed)
    clusters = limbs.detect_limb_clusters(
        parsed["panels"], parsed["stitches"], panel_ranges, verts
    )
    grouped = [cluster["panels"] for cluster in clusters]
    assert grouped == [
        ["sl_right_cuff_b", "sl_right_cuff_f"],
        ["right_sleeve_b", "right_sleeve_f"],
    ]
    assert all(cluster["axis"] == 0 for cluster in clusters)  # 腕は X 方向


# --------------------------------------------------------------------------
# 連結成分
# --------------------------------------------------------------------------


def test_count_face_components_counts_islands():
    faces = [(0, 1, 2), (1, 2, 3), (4, 5, 6)]
    assert topology.count_face_components(7, faces) == 2


def test_pair_groups_merges_chains_of_pairs():
    assert topology.pair_groups([(0, 1), (1, 2), (5, 6)]) == [[0, 1, 2], [5, 6]]


def test_pair_groups_is_deterministic():
    assert topology.pair_groups([(6, 5), (2, 1), (1, 0)]) == [[0, 1, 2], [5, 6]]


# --------------------------------------------------------------------------
# spec の読み込みと検証
# --------------------------------------------------------------------------


def test_fixture_parses(parsed):
    assert len(parsed["panels"]) == 5
    assert len(parsed["stitches"]) == 9
    assert parsed["order"] == [
        "sl_right_cuff_b",
        "sl_right_cuff_f",
        "right_sleeve_b",
        "right_sleeve_f",
        "right_ftorso",
    ]
    assert parsed["properties"]["units_in_meter"] == 100


def test_panel_order_never_drops_a_panel():
    data = _minimal_spec()
    data["pattern"]["panel_order"] = []
    assert spec.parse_spec(data)["order"] == ["square"]


def _minimal_spec():
    return {
        "pattern": {
            "panels": {
                "square": {
                    "translation": [0, 0, 0],
                    "rotation": [0, 0, 0],
                    "vertices": [[0, 0], [1, 0], [1, 1], [0, 1]],
                    "edges": [
                        {"endpoints": [0, 1]},
                        {"endpoints": [1, 2]},
                        {"endpoints": [2, 3]},
                        {"endpoints": [3, 0]},
                    ],
                }
            },
            "stitches": [],
        },
        "properties": {"curvature_coords": "relative", "units_in_meter": 100},
    }


def test_minimal_spec_round_trips():
    parsed_minimal = spec.parse_spec(_minimal_spec())
    assert parsed_minimal["panels"]["square"]["edges"][0]["curvature"] is None
    assert parsed_minimal["panels"]["square"]["translation"] == (0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: d.pop("pattern"), "pattern"),
        (lambda d: d["pattern"].pop("panels"), "panels"),
        (lambda d: d["pattern"].__setitem__("panels", {}), "パネルが1枚もありません"),
        (lambda d: d["pattern"]["panels"]["square"].__setitem__("vertices", [[0, 0]]), "頂点が"),
        (
            lambda d: d["pattern"]["panels"]["square"]["edges"][0].__setitem__(
                "endpoints", [0, 99]
            ),
            "範囲外",
        ),
        (
            lambda d: d["pattern"]["panels"]["square"]["edges"][0].__setitem__(
                "endpoints", [2, 2]
            ),
            "同じ頂点",
        ),
        (
            lambda d: d["pattern"]["panels"]["square"]["edges"][0].__setitem__(
                "curvature", {"type": "spiral", "params": []}
            ),
            "未対応",
        ),
        (
            lambda d: d["pattern"]["panels"]["square"]["edges"][0].__setitem__(
                "curvature", {"type": "cubic", "params": [[0.5, 0.5]]}
            ),
            "制御点が",
        ),
        (
            lambda d: d["pattern"]["panels"]["square"].__setitem__("translation", [0, 0]),
            "要素数",
        ),
        (
            lambda d: d["pattern"].__setitem__(
                "stitches",
                [[{"panel": "ghost", "edge": 0}, {"panel": "square", "edge": 0}]],
            ),
            "知らないパネル",
        ),
        (
            lambda d: d["pattern"].__setitem__(
                "stitches",
                [[{"panel": "square", "edge": 9}, {"panel": "square", "edge": 0}]],
            ),
            "範囲外",
        ),
        (
            lambda d: d["pattern"].__setitem__(
                "stitches", [[{"panel": "square", "edge": 0}]]
            ),
            "2要素",
        ),
    ],
)
def test_malformed_spec_is_rejected_with_a_clear_message(mutate, fragment):
    data = _minimal_spec()
    mutate(data)
    with pytest.raises(spec.SpecError) as raised:
        spec.parse_spec(data)
    assert fragment in str(raised.value)


def test_load_spec_reports_a_missing_file(tmp_path):
    with pytest.raises(spec.SpecError) as raised:
        spec.load_spec(tmp_path / "nope.json")
    assert "開けません" in str(raised.value)


def test_load_spec_reports_broken_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(spec.SpecError) as raised:
        spec.load_spec(path)
    assert "JSON" in str(raised.value)


def test_spec_warnings_are_quiet_on_good_input(parsed):
    assert spec.spec_warnings(parsed) == []


def test_spec_warnings_flag_unexpected_units_and_coords():
    data = _minimal_spec()
    data["properties"] = {"curvature_coords": "absolute", "units_in_meter": 1}
    notes = spec.spec_warnings(spec.parse_spec(data))
    assert any("curvature_coords" in note for note in notes)
    assert any("units_in_meter" in note for note in notes)


def test_spec_warnings_flag_a_panel_nobody_sews():
    notes = spec.spec_warnings(spec.parse_spec(_minimal_spec()))
    assert any("square" in note for note in notes)


def test_scale_length_warning_only_fires_off_a_cm_scene():
    assert spec.scale_length_warning(0.01) is None
    message = spec.scale_length_warning(1.0)
    assert message and "1 BU = 1 cm" in message


# --------------------------------------------------------------------------
# パッケージの配線(costume と同じ約束を守っているか)
# --------------------------------------------------------------------------


def test_garmentcode_is_not_in_modules():
    """garmentcode は register を持たないので _modules に入れると AttributeError になる"""
    import my_blender_plugin

    assert garmentcode not in my_blender_plugin._modules
    assert not hasattr(garmentcode, "register")


def test_garmentcode_reload_all_is_safe_before_import():
    garmentcode.reload_all()


def test_garmentcode_reload_order_covers_every_submodule():
    listed = set(garmentcode._MODULE_NAMES)
    on_disk = {
        path.stem
        for path in (REPO / "my_blender_plugin" / "garmentcode").glob("*.py")
        if path.stem != "__init__"
    }
    assert listed == on_disk


def test_garmentcode_reload_is_wired_into_the_addon_init():
    source = (REPO / "my_blender_plugin" / "__init__.py").read_text(encoding="utf-8")
    assert "garmentcode.reload_all()" in source


def test_garmentcode_package_does_not_import_bpy_dependent_modules():
    """__init__.py が build/weld を再エクスポートすると純粋関数のテストが落ちる"""
    import sys

    assert "my_blender_plugin.garmentcode.build" not in sys.modules
    assert "my_blender_plugin.garmentcode.weld" not in sys.modules


def test_garmentcode_operators_are_registered():
    for cls in (
        operators.MYPLUGIN_OT_import_garmentcode,
        operators.MYPLUGIN_OT_weld_garmentcode_seams,
    ):
        assert cls in operators._classes
        assert cls.bl_idname.startswith("myplugin.")
        assert cls.bl_description


def test_panel_exposes_the_garmentcode_operators():
    source = (REPO / "my_blender_plugin" / "panels.py").read_text(encoding="utf-8")
    assert "MYPLUGIN_OT_import_garmentcode" in source
    assert "MYPLUGIN_OT_weld_garmentcode_seams" in source
    assert panels.MYPLUGIN_PT_main.bl_category == "My Plugin"


def test_no_cloth_settings_operator_exists():
    """クロスの設定・ベイクは Blender 標準の 物理演算 > クロス でやる(実装しない)"""
    names = [cls.bl_idname for cls in operators._classes]
    assert not any("cloth" in name or "bake" in name for name in names)


# --------------------------------------------------------------------------
# 取り込み結果の報告(純粋関数)
# --------------------------------------------------------------------------


def _fake_assembled(components, panels_count=2):
    return {
        "verts": [(0.0, 0.0, 0.0)] * 10,
        "faces": [(0, 1, 2)] * 4,
        "sewing": [(0, 1)],
        "sew_stats": {"stitches": 1, "self_loops": 2, "reversed": 1, "missing": []},
        "gaps": {"mean": 0.5, "max": 0.9, "count": 1},
        "limb_report": [
            {"panels": ["sleeve"], "applied": True, "offset": {"Y": 6.35, "Z": 13.11}}
        ],
        "components": components,
    }


def test_import_report_mentions_the_limb_offsets():
    parsed_stub = {"panels": {"a": {}, "b": {}}, "stitches": [], "properties": {}}
    info, warnings = operators.garmentcode_import_report(_fake_assembled(2), parsed_stub)
    assert any("Y+6.35" in line and "Z+13.11" in line for line in info)
    assert not any("面コンポーネント" in line for line in warnings)


def test_import_report_warns_when_panels_got_welded_together():
    parsed_stub = {"panels": {"a": {}, "b": {}}, "stitches": [], "properties": {}}
    _info, warnings = operators.garmentcode_import_report(_fake_assembled(1), parsed_stub)
    assert any("面コンポーネント" in line for line in warnings)


def test_import_report_warns_about_missing_stitch_edges():
    parsed_stub = {"panels": {"a": {}, "b": {}}, "stitches": [], "properties": {}}
    assembled = _fake_assembled(2)
    assembled["sew_stats"]["missing"] = [("ghost", 3)]
    _info, warnings = operators.garmentcode_import_report(assembled, parsed_stub)
    assert any("ghost[3]" in line for line in warnings)
