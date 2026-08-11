import math

import pytest

from my_blender_plugin.costume import kernels, modulate, validate


def cylinder_rings(segments=8, rings=3, radius=1.0, height=2.0):
    angles = modulate.circle_angles(segments)
    return [
        kernels.ring_from_polar([radius] * segments, angles, height * (1.0 - index / (rings - 1)))
        for index in range(rings)
    ]


def test_ring_from_polar_places_points_on_the_circle():
    ring = kernels.ring_from_polar([2.0, 2.0, 2.0, 2.0], modulate.circle_angles(4), 5.0)
    assert len(ring) == 4
    assert ring[0] == pytest.approx((2.0, 0.0, 5.0))
    assert ring[1] == pytest.approx((0.0, 2.0, 5.0))
    for point in ring:
        assert math.hypot(point[0], point[1]) == pytest.approx(2.0)
        assert point[2] == 5.0


def test_ring_from_polar_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        kernels.ring_from_polar([1.0, 2.0], modulate.circle_angles(3), 0.0)


def test_ring_from_polar_squashes_y_by_depth_ratio():
    ring = kernels.ring_from_polar(
        [2.0] * 4, modulate.circle_angles(4), 0.0, depth_ratio=0.5
    )
    assert ring[0] == pytest.approx((2.0, 0.0, 0.0))  # x は長半径そのまま
    assert ring[1] == pytest.approx((0.0, 1.0, 0.0))  # y は半分
    breadth = max(p[0] for p in ring) - min(p[0] for p in ring)
    depth = max(p[1] for p in ring) - min(p[1] for p in ring)
    assert depth / breadth == pytest.approx(0.5)


def test_ring_from_polar_rejects_non_positive_depth_ratio():
    with pytest.raises(ValueError):
        kernels.ring_from_polar([1.0] * 4, modulate.circle_angles(4), 0.0, depth_ratio=0.0)


def test_elliptical_tube_normals_still_point_outward():
    angles = modulate.circle_angles(16)
    rings = [
        kernels.ring_from_polar([1.0] * 16, angles, 1.0, depth_ratio=0.6),
        kernels.ring_from_polar([1.2] * 16, angles, 0.0, depth_ratio=0.8),
    ]
    mesh = kernels.loft_rings(rings)
    for face in mesh.quads:
        normal = validate.face_normal(mesh.verts, face)
        center = [sum(mesh.verts[i][axis] for i in face) / 4 for axis in range(3)]
        assert validate._dot(normal, (center[0], center[1], 0.0)) > 0.0


def test_loft_counts_for_a_closed_tube():
    mesh = kernels.loft_rings(cylinder_rings(segments=8, rings=3))
    assert len(mesh.verts) == 24
    assert len(mesh.quads) == 16  # 8分割 × 2段
    assert mesh.ring_size == 8
    assert mesh.ring_count == 3
    assert mesh.rings["top"] == list(range(8))
    assert mesh.rings["bottom"] == list(range(16, 24))


def test_loft_counts_for_an_open_sheet():
    mesh = kernels.loft_rings(cylinder_rings(segments=8, rings=3), closed=False)
    assert len(mesh.quads) == 14  # 7分割 × 2段


def test_face_normals_point_outward():
    mesh = kernels.loft_rings(cylinder_rings(segments=12, rings=4))
    for face in mesh.quads:
        normal = validate.face_normal(mesh.verts, face)
        center = [sum(mesh.verts[i][axis] for i in face) / 4 for axis in range(3)]
        radial = (center[0], center[1], 0.0)
        assert validate._dot(normal, radial) > 0.0


def test_uv_stays_inside_the_unit_square():
    mesh = kernels.loft_rings(cylinder_rings(segments=16, rings=6))
    assert len(mesh.uv_loops) == len(mesh.quads)
    for loop in mesh.uv_loops:
        assert len(loop) == 4
        for u, v in loop:
            assert 0.0 <= u <= 1.0
            assert 0.0 <= v <= 1.0


def test_uv_seam_does_not_wrap_back_to_zero():
    mesh = kernels.loft_rings(cylinder_rings(segments=8, rings=2))
    # 最後の四角は継ぎ目。u が 0 に戻ると帯が1周ぶん逆走してテクスチャが破綻する
    seam = mesh.uv_loops[-1]
    assert seam[2][0] > seam[1][0]
    assert seam[3][0] > seam[0][0]


def test_uv_is_deterministic():
    first = kernels.loft_rings(cylinder_rings())
    second = kernels.loft_rings(cylinder_rings())
    assert first.uv_loops == second.uv_loops


def test_uv_texel_density_is_uniform_on_a_cylinder():
    mesh = kernels.loft_rings(cylinder_rings(segments=16, rings=5, radius=1.0, height=2.0))
    densities = []
    for face, loop in zip(mesh.quads, mesh.uv_loops):
        area3d = validate.face_area(mesh.verts, face)
        # UV 四角形の面積(靴紐公式)
        area_uv = abs(
            sum(
                loop[i][0] * loop[(i + 1) % 4][1] - loop[(i + 1) % 4][0] * loop[i][1]
                for i in range(4)
            )
        ) / 2.0
        densities.append(area_uv / area3d)
    spread = (max(densities) - min(densities)) / (sum(densities) / len(densities))
    assert spread < 1e-6


def test_edge_kinds_splits_ring_and_axial_edges():
    mesh = kernels.loft_rings(cylinder_rings(segments=8, rings=3))
    kinds = mesh.edge_kinds()
    assert len(kinds["ring"]) == 8 * 3  # リング3本 × 8辺
    assert len(kinds["axial"]) == 8 * 2  # 段2つ × 8辺


def test_loft_rejects_inconsistent_ring_sizes():
    rings = cylinder_rings(segments=8, rings=2)
    rings[1] = rings[1][:-1]
    with pytest.raises(ValueError):
        kernels.loft_rings(rings)


def test_loft_rejects_single_ring():
    with pytest.raises(ValueError):
        kernels.loft_rings(cylinder_rings(rings=3)[:1])
