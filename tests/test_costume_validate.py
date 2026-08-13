import math

import pytest

from my_blender_plugin.costume import (
    kernels,
    modulate,
    parts,
    spec as spec_module,
    validate,
)


def tube(segments=12, rings=3, radius=1.0, height=2.0):
    angles = modulate.circle_angles(segments)
    ring_points = [
        kernels.ring_from_polar([radius] * segments, angles, height * (1.0 - i / (rings - 1)))
        for i in range(rings)
    ]
    return kernels.loft_rings(ring_points, name="Tube")


# --------------------------------------------------------------- 基本の幾何


def test_face_normal_of_a_ccw_triangle_points_up():
    verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    assert validate.face_normal(verts, (0, 1, 2)) == pytest.approx((0.0, 0.0, 1.0))


def test_face_area_of_a_unit_square():
    verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    assert validate.face_area(verts, (0, 1, 2, 3)) == pytest.approx(1.0)


# --------------------------------------------------------------- トポロジ


def test_clean_tube_topology():
    mesh = tube()
    topo = validate.topology(mesh.verts, mesh.quads)
    assert topo["nonmanifold_edges"] == 0
    assert topo["loose_verts"] == 0
    assert topo["winding_flipped_edges"] == 0
    assert topo["tris"] == 0
    assert topo["quads"] == len(mesh.quads)
    # 上端と下端で 12+12 = 24 本の境界辺
    assert topo["boundary_edges"] == 24


def test_flipped_face_is_detected():
    mesh = tube()
    a, b, c, d = mesh.quads[0]
    mesh.quads[0] = (d, c, b, a)
    topo = validate.topology(mesh.verts, mesh.quads)
    assert topo["winding_flipped_edges"] > 0


def test_loose_vert_is_detected():
    mesh = tube()
    mesh.verts.append((10.0, 10.0, 10.0))
    topo = validate.topology(mesh.verts, mesh.quads)
    assert topo["loose_verts"] == 1


def test_nonmanifold_edge_is_detected():
    mesh = tube()
    # 既存の辺を3枚目の面で使い回す
    a, b, _c, _d = mesh.quads[0]
    mesh.verts.append((5.0, 5.0, 5.0))
    extra = len(mesh.verts) - 1
    mesh.quads.append((a, b, extra))
    topo = validate.topology(mesh.verts, mesh.quads)
    assert topo["nonmanifold_edges"] >= 1
    assert topo["tris"] == 1


def test_boundary_loops_of_a_tube_are_two_rings():
    mesh = tube(segments=12, rings=4)
    topo = validate.topology(mesh.verts, mesh.quads)
    loops = validate.boundary_loops(topo["_boundary_edges"])
    assert len(loops) == 2
    assert sorted(len(loop) for loop in loops) == [12, 12]


def test_duplicate_verts_are_detected():
    mesh = tube()
    assert validate.duplicate_verts(mesh.verts, 1e-6) == 0
    mesh.verts.append(mesh.verts[0])
    assert validate.duplicate_verts(mesh.verts, 1e-6) == 1


# --------------------------------------------------------------- 交差


def test_crossing_quads_are_detected():
    # XY 平面の四角と、それを貫く垂直な四角。頂点は共有しない
    verts = [
        (-1.0, -1.0, 0.0),
        (1.0, -1.0, 0.0),
        (1.0, 1.0, 0.0),
        (-1.0, 1.0, 0.0),
        (0.0, -0.5, -1.0),
        (0.0, 0.5, -1.0),
        (0.0, 0.5, 1.0),
        (0.0, -0.5, 1.0),
    ]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7)]
    assert validate.self_intersections(verts, faces, cell=1.0, eps=1e-9) == 1


def test_touching_but_not_crossing_quads_are_not_flagged():
    # 辺で接するだけの2枚(共有頂点なし・同一平面)は交差ではない
    verts = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (1.0, 1.0, 0.0),
    ]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7)]
    assert validate.self_intersections(verts, faces, cell=1.0, eps=1e-9) == 0


def test_clean_tube_has_no_self_intersections():
    mesh = tube(segments=16, rings=5)
    mean_edge = 2.0 * math.pi / 16
    assert (
        validate.self_intersections(mesh.verts, mesh.quads, cell=mean_edge * 2, eps=1e-9) == 0
    )


def test_cross_intersections_skip_shared_ring_verts():
    lower = tube(segments=12, rings=2, radius=1.0, height=1.0)
    # 下端(z=0)を共有する形で上にもう1本積む
    angles = modulate.circle_angles(12)
    upper = kernels.loft_rings(
        [
            kernels.ring_from_polar([1.0] * 12, angles, 1.0),
            kernels.ring_from_polar([1.0] * 12, angles, 0.0),
        ],
        name="Upper",
    )
    # 座標が一致するリングをそのまま検査すると必ず当たる
    naive = validate.cross_intersections(lower, upper, cell=1.0, eps=1e-9)
    skipped = validate.cross_intersections(
        lower,
        upper,
        cell=1.0,
        eps=1e-9,
        skip_a=lower.rings["top"],
        skip_b=upper.rings["bottom"],
    )
    assert skipped == 0
    assert naive >= 0  # 実装依存なので数は問わない。除外後にゼロになることが要件


# --------------------------------------------------------------- リングの寸法


def test_ring_metrics_of_a_polygon_inscribed_in_a_circle():
    angles = modulate.circle_angles(24)
    ring = kernels.ring_from_polar([2.0] * 24, angles, 0.0)
    mesh = kernels.loft_rings([ring, kernels.ring_from_polar([2.0] * 24, angles, -1.0)])
    metrics = validate.ring_metrics(mesh.verts, mesh.rings["top"])
    assert metrics["verts"] == 24
    assert metrics["mean_radius"] == pytest.approx(2.0)
    # 多角形の周長は円周よりわずかに短い(24分割で 0.3% ほど)
    assert metrics["perimeter"] == pytest.approx(2 * math.pi * 2.0, rel=0.004)
    assert metrics["perimeter"] < 2 * math.pi * 2.0
    assert metrics["breadth"] == pytest.approx(4.0)
    assert metrics["depth"] == pytest.approx(4.0)


def test_ring_metrics_reports_an_ellipse_as_flatter_than_wide():
    angles = modulate.circle_angles(32)
    ring = kernels.ring_from_polar([2.0] * 32, angles, 0.0, depth_ratio=0.5)
    mesh = kernels.loft_rings([ring, kernels.ring_from_polar([2.0] * 32, angles, -1.0, depth_ratio=0.5)])
    metrics = validate.ring_metrics(mesh.verts, mesh.rings["top"])
    assert metrics["depth"] / metrics["breadth"] == pytest.approx(0.5)
    assert metrics["perimeter"] == pytest.approx(modulate.ellipse_perimeter(2.0, 0.5), rel=0.005)


# ---------------------------------------------------- プリーツの周波数検出


def test_spectrum_finds_the_pleat_frequency():
    offsets = modulate.pleat_offsets(segments=48, pleats=24, depth=0.2, duty=0.5)
    radii = modulate.ring_radii(1.0, offsets)
    frequency, amplitude = validate.dominant_pleat_frequency(radii)
    assert frequency == 24
    assert amplitude == pytest.approx(0.2, rel=0.05)


def test_spectrum_finds_the_drape_frequency():
    offsets = modulate.drape_offsets(segments=48, folds=12, depth=0.06)
    radii = modulate.ring_radii(1.0, offsets)
    frequency, amplitude = validate.dominant_pleat_frequency(radii)
    assert frequency == 12
    assert amplitude == pytest.approx(0.06, rel=0.05)


@pytest.mark.parametrize("depth_ratio", [1.0, 0.8, 0.68, 0.5, 0.4])
def test_spectrum_ignores_the_elliptical_variation(depth_ratio):
    """楕円は k=2 だけでなく偶数高調波にも漏れる。参照を引いて消えること"""
    angles = modulate.circle_angles(32)
    ring = kernels.ring_from_polar([1.0] * 32, angles, 0.0, depth_ratio=depth_ratio)
    radii = [math.hypot(point[0], point[1]) for point in ring]
    _frequency, amplitude = validate.dominant_pleat_frequency(radii, depth_ratio)
    assert amplitude < validate.PLEAT_AMPLITUDE_NOISE


def test_spectrum_without_the_ellipse_reference_would_false_positive():
    """参照を引かないと薄い断面で誤検出することを固定する(回帰防止)"""
    angles = modulate.circle_angles(32)
    ring = kernels.ring_from_polar([1.0] * 32, angles, 0.0, depth_ratio=0.5)
    radii = [math.hypot(point[0], point[1]) for point in ring]
    _frequency, naive = validate.dominant_pleat_frequency(radii, depth_ratio=1.0)
    assert naive > validate.PLEAT_AMPLITUDE_NOISE


def test_spectrum_separates_pleats_from_the_ellipse():
    angles = modulate.circle_angles(48)
    offsets = modulate.pleat_offsets(48, 24, 0.2, 0.5)
    radii_scaled = modulate.ring_radii(1.0, offsets)
    ring = kernels.ring_from_polar(radii_scaled, angles, 0.0, depth_ratio=0.6)
    radii = [math.hypot(point[0], point[1]) for point in ring]
    frequency, _amplitude = validate.dominant_pleat_frequency(radii)
    assert frequency == 24


def test_radial_spectrum_of_a_perfect_circle_is_flat():
    spectrum = validate.radial_spectrum([1.0] * 24, [2, 3, 6, 12])
    assert all(value < 1e-12 for value in spectrum.values())


def test_joint_gap_is_zero_for_coincident_rings():
    angles = modulate.circle_angles(8)
    upper = kernels.loft_rings(
        [
            kernels.ring_from_polar([1.0] * 8, angles, 1.0),
            kernels.ring_from_polar([1.0] * 8, angles, 0.0),
        ],
        name="Upper",
    )
    lower = kernels.loft_rings(
        [
            kernels.ring_from_polar([1.0] * 8, angles, 0.0),
            kernels.ring_from_polar([1.5] * 8, angles, -1.0),
        ],
        name="Lower",
    )
    assert validate.joint_gap(upper, "bottom", lower, "top") == pytest.approx(0.0)


def test_joint_gap_reports_a_real_gap():
    angles = modulate.circle_angles(8)
    upper = kernels.loft_rings(
        [
            kernels.ring_from_polar([1.0] * 8, angles, 1.0),
            kernels.ring_from_polar([1.0] * 8, angles, 0.1),
        ],
        name="Upper",
    )
    lower = kernels.loft_rings(
        [
            kernels.ring_from_polar([1.0] * 8, angles, 0.0),
            kernels.ring_from_polar([1.5] * 8, angles, -1.0),
        ],
        name="Lower",
    )
    assert validate.joint_gap(upper, "bottom", lower, "top") == pytest.approx(0.1)


# --------------------------------------------------------------- パーツ単体の判定


def _tube_with_design(segments=24, rings=5, radius=0.15, height=0.4):
    mesh = tube(segments=segments, rings=rings, radius=radius, height=height)
    # 多角形の実周長を設計値にする(円周ではなく、実際に辿る長さと比べたいので)
    perimeter = validate.ring_metrics(mesh.verts, mesh.rings["top"])["perimeter"]
    mesh.design = {
        "top_perimeter": perimeter,
        "bottom_perimeter": perimeter,
        "bottom_fit_perimeter": perimeter,
        "length": height,
        "pleats": 0,
        "pleat_depth": 0.0,
        "radial_modulations": [],
        "boundary_loops": 2,
        "boundary_verts": segments,
    }
    return mesh


def test_part_report_passes_on_a_clean_tube():
    report = validate.part_report(_tube_with_design(), height_units=1.53)
    assert report["failed"] == []
    assert report["verdict"] in ("PASS", "WARN")
    assert report["radial_outward_ratio"] == pytest.approx(1.0)
    assert report["boundary_loops"] == 2


def test_part_report_fails_when_dimensions_do_not_match_the_design():
    mesh = _tube_with_design()
    mesh.design["length"] *= 2.0
    report = validate.part_report(mesh, height_units=1.53)
    assert "length" in report["failed"]
    assert report["verdict"] == "FAIL"


def test_part_report_fails_when_a_declared_modulation_is_missing():
    mesh = _tube_with_design()
    # プリーツを作っていないのに「12山ある」と宣言させる
    mesh.design["radial_modulations"] = [
        {"kind": "pleat", "frequency": 12, "depth": 0.2}
    ]
    report = validate.part_report(mesh, height_units=1.53)
    assert "modulation_pleat_12" in report["failed"]


def test_part_report_fails_on_an_undeclared_modulation():
    """宣言していない周期成分が出ていたら不合格(意図しない波打ち)"""
    angles = modulate.circle_angles(48)
    offsets = modulate.drape_offsets(48, 8, 0.05)
    ring_points = [
        kernels.ring_from_polar(modulate.ring_radii(0.15, offsets), angles, 0.4 * (1 - i))
        for i in range(2)
    ]
    mesh = kernels.loft_rings(ring_points, name="Wobbly")
    mesh.design = {"pleats": 0, "pleat_depth": 0.0, "radial_modulations": []}
    report = validate.part_report(mesh, height_units=1.53)
    assert "no_unexpected_modulation" in report["failed"]


def test_part_report_accepts_a_declared_modulation():
    angles = modulate.circle_angles(48)
    offsets = modulate.drape_offsets(48, 8, 0.05)
    ring_points = [
        kernels.ring_from_polar(modulate.ring_radii(0.15, offsets), angles, 0.4 * (1 - i))
        for i in range(2)
    ]
    mesh = kernels.loft_rings(ring_points, name="Draped")
    mesh.design = {
        "pleats": 0,
        "pleat_depth": 0.0,
        "radial_modulations": [{"kind": "drape", "frequency": 8, "depth": 0.05}],
    }
    report = validate.part_report(mesh, height_units=1.53)
    assert report["failed"] == []


def test_part_report_fails_on_a_flipped_face():
    mesh = _tube_with_design()
    a, b, c, d = mesh.quads[0]
    mesh.quads[0] = (d, c, b, a)
    report = validate.part_report(mesh, height_units=1.53)
    assert "winding" in report["failed"]


def test_part_report_reports_edge_length_but_does_not_gate_it():
    """エッジ長 /H の実測値は「衣装1着ぶんの平均」なので、判定は衣装全体で行う。

    パーツ単体では内訳として数値だけ出す(接合のため本体と分割数を共有する
    ウエストバンドは単体では必ず細かく出るので、ここで落としてはいけない)。
    """
    mesh = _tube_with_design(segments=4, rings=2, radius=0.5, height=0.5)
    mesh.design["boundary_verts"] = 4
    report = validate.part_report(mesh, height_units=1.53)
    assert report["edge_length_over_h"]["all"] > 0.05  # 明らかに粗い
    assert "edge_length_over_h" not in report["warn"]


def test_part_report_warns_on_uneven_ring_edges():
    mesh = _tube_with_design(segments=24, rings=3)
    # 1リングだけ大きく膨らませて周方向のエッジ長をばらつかせる
    for index in mesh.rings["bottom"]:
        x, y, z = mesh.verts[index]
        mesh.verts[index] = (x * 4.0, y * 4.0, z)
    mesh.design["bottom_perimeter"] *= 4.0
    report = validate.part_report(mesh, height_units=1.53)
    assert "edge_length_cv_ring" in report["warned"]

# ------------------------------------------------- 胸の稜線(とがりすぎの検出)
#
# 「ふくらみが在るか」は bust_projection の下限が見ていたが、**在りすぎて
# 円錐にとがっていないか**は誰も見ていなかった。単一ドーム・襟ぐり突き抜け・
# バスト周の二重計上の3つとも、レンダーの目視でしか見つけられなかった。


def ridge_wedge(height, depth, steps=40):
    """高さ height の三角形の前面。上りの傾きは depth/(height/2) と分かっている"""
    return [
        (0.0, -depth * (1.0 - abs(2.0 * index / steps - 1.0)), index / steps * height)
        for index in range(steps + 1)
    ]


def test_bust_ridge_matches_a_known_slope():
    """理論値と一致すること(区間で代表点を取るので、ここがずれると意味が無い)"""
    for depth, expected in ((0.10, 45.0), (0.06, 31.0), (0.02, 11.3)):
        measured = validate.bust_ridge_degrees(ridge_wedge(0.20, depth))["degrees"]
        assert measured == pytest.approx(expected, abs=0.5), depth


def test_bust_ridge_grows_with_the_bulge():
    angles = [validate.bust_ridge_degrees(ridge_wedge(0.20, d))["degrees"]
              for d in (0.02, 0.06, 0.10)]
    assert angles[0] < angles[1] < angles[2]


def test_bust_ridge_ignores_everything_above_the_neckline():
    """襟ぐりより上(肩紐・襟ぐりの縁)を混ぜると稜線を取り違える
    (実際スク水で 37 度が 55 度に化けた)。

    実態は「襟ぐりでも布はまだ前に出ていて、その上の肩紐が急に後退する」形。
    その段差を胸の稜線と読むと、なだらかな胸でも急に見える"""
    chest = []
    for index in range(21):
        z = index / 20.0 * 0.20
        # 頂点 z=0.10 で y=-0.06、襟ぐり z=0.20 では -0.02 までしか戻らない
        y = -0.06 * (z / 0.10) if z <= 0.10 else -0.06 + 0.04 * (z - 0.10) / 0.10
        chest.append((0.0, y, z))
    straps = [(0.0, -0.002, 0.205), (0.0, -0.001, 0.21)]
    verts = chest + straps

    gentle = validate.bust_ridge_degrees(verts, z_max=0.20)["degrees"]
    contaminated = validate.bust_ridge_degrees(verts)["degrees"]
    assert gentle == pytest.approx(21.8, abs=1.0)  # 0.04/0.10 = 21.8度
    assert contaminated > 60.0  # 段差を稜線と読むと急に化ける


def test_bust_ridge_handles_degenerate_input():
    assert validate.bust_ridge_degrees([])["degrees"] is None
    assert validate.bust_ridge_degrees([(0.0, -1.0, 0.0)])["degrees"] is None
    # 前(y<0)の点が無い
    assert validate.bust_ridge_degrees([(0.0, 1.0, 0.0), (0.0, 2.0, 1.0)])["degrees"] is None


def test_the_swimsuit_bust_is_not_a_cone_but_the_default_still_warns():
    """回帰ガード: スク水(0.012)は通り、既定 0.022 のままの3つは警告が出る。
    この差が「バスト周の二重計上」の実測(docs のスク水の節)"""
    verdicts = {}
    for name in ("blouse", "vest", "onepiece", "swimsuit"):
        normalized = spec_module.load_preset(name)
        report = validate.costume_report(parts.build_all(normalized), normalized)
        entry = next(e for e in report["parts"] if "measured_bust_ridge_degrees" in e)
        verdicts[name] = entry["warn"]["bust_is_not_a_cone"]["ok"]
    assert verdicts["swimsuit"] is True
    assert verdicts["blouse"] is False
    assert verdicts["vest"] is False
    assert verdicts["onepiece"] is False
