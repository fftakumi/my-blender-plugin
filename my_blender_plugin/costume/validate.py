"""幾何メトリクスと合否判定。**bpy 非依存**なので pytest から直接呼べる。

多様体性・境界ループ・退化面・巻き方向・自己交差・寸法は (頂点, 四角面) から
純 Python で計算できる。Blender の起動を挟まないぶん生成↔検証ループが速く回る。
Blender が要るのは UV とマテリアルとレンダーだけで、それは tools/verify.py が見る。

`mathutils` は使わない(テスト環境のフェイク bpy には無い)。
"""

import math

#: 寸法の許容誤差(設計値に対する比)
DIM_TOL = 0.03
#: 筒状パーツで面法線が外を向いていなければならない割合
RADIAL_OUTWARD_MIN = 0.95
#: プリーツの周期成分の振幅が、指定した折り込み深さの何割以上あれば「入っている」と見るか
PLEAT_AMPLITUDE_FRACTION = 0.3
#: 上から2本目のリングの折り目の振幅が、裾の振幅の何割以上あれば
#: 「折り目がウエストまで続いている」と見るか。
#: 実物のプリーツスカートは腰でも折り目線が見えている(閉じているだけ)。
#: docs/garments.md のプリーツスカートの定義 #1 に対応する。
PLEAT_TOP_CREASE_MIN_RATIO = 0.15
#: プリーツ無しのときに許す周期成分の振幅(楕円断面を多角形で近似したときの高調波ぶん)
PLEAT_AMPLITUDE_NOISE = 0.01
#: 参照実測(VRM 10体)の Bottoms の中央エッジ長 / 身長。warn 判定に使う
EDGE_LENGTH_OVER_H_RANGE = (0.0206, 0.0311)
#: エッジ長の変動係数の上限(warn)。**周方向と軸方向を混ぜて測ってはいけない** —
#: 短いバンドは軸方向の辺が周方向の4倍あり、混ぜた CV は「不均一」ではなく
#: 「縦横比が1でない」を測ってしまう。実測した値(フレアスカート 周0.27/軸0.02、
#: プリーツ 周0.34/軸0.02、バンド 周0.00/軸0.00)を見て 0.40 に置いた。
EDGE_CV_MAX = 0.40


# ------------------------------------------------------------------ 小物


def _sub(p, q):
    return (p[0] - q[0], p[1] - q[1], p[2] - q[2])


def _cross(u, v):
    return (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )


def _dot(u, v):
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def _length(u):
    return math.sqrt(_dot(u, u))


def _distance(p, q):
    return _length(_sub(p, q))


def _stats(values):
    """レポートに載せる基本統計。空なら None 埋め"""
    if not values:
        return {"n": 0, "min": None, "max": None, "mean": None, "cv": None}
    count = len(values)
    mean = sum(values) / count
    if count > 1 and mean != 0.0:
        variance = sum((value - mean) ** 2 for value in values) / count
        cv = math.sqrt(variance) / abs(mean)
    else:
        cv = 0.0
    return {
        "n": count,
        "min": min(values),
        "max": max(values),
        "mean": mean,
        "cv": cv,
    }


def face_normal(verts, face):
    """Newell 法の面法線(正規化)。Blender の面法線と同じ向きになる"""
    nx = ny = nz = 0.0
    count = len(face)
    for index in range(count):
        current = verts[face[index]]
        following = verts[face[(index + 1) % count]]
        nx += (current[1] - following[1]) * (current[2] + following[2])
        ny += (current[2] - following[2]) * (current[0] + following[0])
        nz += (current[0] - following[0]) * (current[1] + following[1])
    norm = _length((nx, ny, nz))
    if norm == 0.0:
        return (0.0, 0.0, 0.0)
    return (nx / norm, ny / norm, nz / norm)


def face_area(verts, face):
    """多角形の面積(Newell 法のベクトル長の半分)"""
    nx = ny = nz = 0.0
    count = len(face)
    for index in range(count):
        current = verts[face[index]]
        following = verts[face[(index + 1) % count]]
        nx += current[1] * following[2] - current[2] * following[1]
        ny += current[2] * following[0] - current[0] * following[2]
        nz += current[0] * following[1] - current[1] * following[0]
    return 0.5 * _length((nx, ny, nz))


# ------------------------------------------------------------ トポロジ


def edge_usage(faces):
    """辺 → その辺を使った (面index, 向き) のリスト"""
    usage = {}
    for face_index, face in enumerate(faces):
        count = len(face)
        for index in range(count):
            a = face[index]
            b = face[(index + 1) % count]
            usage.setdefault((min(a, b), max(a, b)), []).append((face_index, (a, b)))
    return usage


def topology(verts, faces):
    """非多様体・境界・孤立・巻き方向をまとめて数える"""
    usage = edge_usage(faces)
    nonmanifold = [edge for edge, users in usage.items() if len(users) > 2]
    boundary = [edge for edge, users in usage.items() if len(users) == 1]

    used_verts = set()
    for face in faces:
        used_verts.update(face)
    loose_verts = [index for index in range(len(verts)) if index not in used_verts]

    # 内部辺は2面から逆向きに使われていなければ巻き方向が揃っていない
    flipped = [
        edge
        for edge, users in usage.items()
        if len(users) == 2 and users[0][1] == users[1][1]
    ]

    return {
        "verts": len(verts),
        "faces": len(faces),
        "edges": len(usage),
        "quads": sum(1 for face in faces if len(face) == 4),
        "tris": sum(1 for face in faces if len(face) == 3),
        "nonmanifold_edges": len(nonmanifold),
        "boundary_edges": len(boundary),
        "loose_verts": len(loose_verts),
        # 面から辺を組み立てているので「面に属さない辺」は原理的に存在しない
        "loose_edges": 0,
        "winding_flipped_edges": len(flipped),
        "_boundary_edges": boundary,
    }


def boundary_loops(boundary_edge_list):
    """境界辺の集合を輪(ループ)に組み直して、各ループの頂点列を返す"""
    adjacency = {}
    for a, b in boundary_edge_list:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    loops = []
    visited = set()
    for start in sorted(adjacency):
        if start in visited:
            continue
        loop = [start]
        visited.add(start)
        previous, current = None, start
        while True:
            following = None
            for candidate in adjacency[current]:
                if candidate != previous and candidate not in visited:
                    following = candidate
                    break
            if following is None:
                break
            loop.append(following)
            visited.add(following)
            previous, current = current, following
        loops.append(loop)
    return loops


def duplicate_verts(verts, tolerance):
    """距離マージで潰れてしまう頂点の数(格子バケットで数える)"""
    if tolerance <= 0.0:
        return 0
    buckets = {}
    duplicates = 0
    for point in verts:
        key = (
            int(math.floor(point[0] / tolerance)),
            int(math.floor(point[1] / tolerance)),
            int(math.floor(point[2] / tolerance)),
        )
        merged = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other in buckets.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                        if _distance(point, other) <= tolerance:
                            merged = True
                            break
                    if merged:
                        break
                if merged:
                    break
            if merged:
                break
        if merged:
            duplicates += 1
        else:
            buckets.setdefault(key, []).append(point)
    return duplicates


# --------------------------------------------------------- 交差(自己 / パーツ間)


def _triangles(verts, faces, skip_verts=()):
    """四角を三角2枚に割る。skip_verts に触れる面は最初から除外する"""
    skip = set(skip_verts)
    result = []
    for face_index, face in enumerate(faces):
        if skip and skip.intersection(face):
            continue
        if len(face) == 3:
            result.append((face_index, tuple(face)))
        else:
            a, b, c, d = face
            result.append((face_index, (a, b, c)))
            result.append((face_index, (a, c, d)))
    return result


def _segment_hits_triangle(p0, p1, t0, t1, t2, eps):
    """線分 p0-p1 が三角形 t0t1t2 の内部を貫くか(Möller-Trumbore)"""
    direction = _sub(p1, p0)
    edge1 = _sub(t1, t0)
    edge2 = _sub(t2, t0)
    pvec = _cross(direction, edge2)
    determinant = _dot(edge1, pvec)
    if abs(determinant) < eps:
        return False  # 線分が三角形の面と平行(同一平面は別に扱う)
    inverse = 1.0 / determinant
    tvec = _sub(p0, t0)
    u = _dot(tvec, pvec) * inverse
    if u < eps or u > 1.0 - eps:
        return False
    qvec = _cross(tvec, edge1)
    v = _dot(direction, qvec) * inverse
    if v < eps or u + v > 1.0 - eps:
        return False
    t = _dot(edge2, qvec) * inverse
    return eps < t < 1.0 - eps


def triangles_intersect(verts_a, tri_a, verts_b, tri_b, eps):
    """非同一平面の三角形同士は「片方の辺が相手を貫く」かで判定できる"""
    a = [verts_a[i] for i in tri_a]
    b = [verts_b[i] for i in tri_b]
    for start, end in ((0, 1), (1, 2), (2, 0)):
        if _segment_hits_triangle(a[start], a[end], b[0], b[1], b[2], eps):
            return True
    for start, end in ((0, 1), (1, 2), (2, 0)):
        if _segment_hits_triangle(b[start], b[end], a[0], a[1], a[2], eps):
            return True
    return False


def _grid_buckets(verts, triangles, cell):
    """三角形の AABB を格子に登録して候補ペアを絞る"""
    buckets = {}
    for slot, (_face_index, tri) in enumerate(triangles):
        points = [verts[i] for i in tri]
        lows = [min(point[axis] for point in points) for axis in range(3)]
        highs = [max(point[axis] for point in points) for axis in range(3)]
        ranges = [
            range(int(math.floor(lows[axis] / cell)), int(math.floor(highs[axis] / cell)) + 1)
            for axis in range(3)
        ]
        for x in ranges[0]:
            for y in ranges[1]:
                for z in ranges[2]:
                    buckets.setdefault((x, y, z), []).append(slot)
    return buckets


def self_intersections(verts, faces, cell, eps, skip_verts=()):
    """同一メッシュ内で交差している面ペアの数。頂点を共有するペアは除外する"""
    triangles = _triangles(verts, faces, skip_verts)
    if not triangles:
        return 0
    buckets = _grid_buckets(verts, triangles, cell)
    checked = set()
    hits = set()
    for slots in buckets.values():
        for i, left in enumerate(slots):
            for right in slots[i + 1 :]:
                pair = (left, right) if left < right else (right, left)
                if pair in checked:
                    continue
                checked.add(pair)
                face_a, tri_a = triangles[pair[0]]
                face_b, tri_b = triangles[pair[1]]
                if face_a == face_b or set(tri_a) & set(tri_b):
                    continue
                if triangles_intersect(verts, tri_a, verts, tri_b, eps):
                    hits.add((min(face_a, face_b), max(face_a, face_b)))
    return len(hits)


def cross_intersections(mesh_a, mesh_b, cell, eps, skip_a=(), skip_b=()):
    """別パーツ同士で交差している面ペアの数。接合リングに触れる面は除外する。

    開いた面(筒)には「内部」が定義できないので、「相手の内側に何mm入ったか」ではなく
    **面が実際に交差しているか**で貫通を判定する。座標が一致する接合リングを
    そのまま検査すると必ず当たるので、そこに触れる面は先に落とす。
    """
    tris_a = _triangles(mesh_a.verts, mesh_a.quads, skip_a)
    tris_b = _triangles(mesh_b.verts, mesh_b.quads, skip_b)
    if not tris_a or not tris_b:
        return 0
    buckets = _grid_buckets(mesh_b.verts, tris_b, cell)
    hits = set()
    for slot_a, (face_a, tri_a) in enumerate(tris_a):
        points = [mesh_a.verts[i] for i in tri_a]
        lows = [min(point[axis] for point in points) for axis in range(3)]
        highs = [max(point[axis] for point in points) for axis in range(3)]
        candidates = set()
        for x in range(int(math.floor(lows[0] / cell)), int(math.floor(highs[0] / cell)) + 1):
            for y in range(int(math.floor(lows[1] / cell)), int(math.floor(highs[1] / cell)) + 1):
                for z in range(
                    int(math.floor(lows[2] / cell)), int(math.floor(highs[2] / cell)) + 1
                ):
                    candidates.update(buckets.get((x, y, z), ()))
        for slot_b in candidates:
            face_b, tri_b = tris_b[slot_b]
            if triangles_intersect(mesh_a.verts, tri_a, mesh_b.verts, tri_b, eps):
                hits.add((face_a, face_b))
        del slot_a
    return len(hits)


# ------------------------------------------------------------------ リングの寸法


def loft_ring_indices(ring_size, ring_count, index):
    """ロフトの index 番目のリングの頂点インデックス。

    kernels.loft_rings はリングを上から順に ring_size 個ずつ並べるので、
    上端・下端以外のリングもこれで取り出せる(折り目が途中で消えていないかを見るのに使う)。
    """
    if not 0 <= index < ring_count:
        raise IndexError("リング番号が範囲外です: %d (0..%d)" % (index, ring_count - 1))
    return list(range(index * ring_size, (index + 1) * ring_size))


def pleat_amplitude_at_ring(mesh, index):
    """指定リングでの「楕円ぶんを除いた」最大周期成分の振幅。

    折り目がウエストから裾まで続いているかを、リングごとに測るために使う。
    """
    metrics = ring_metrics(mesh.verts, loft_ring_indices(mesh.ring_size, mesh.ring_count, index))
    depth_ratio = metrics["depth"] / metrics["breadth"] if metrics["breadth"] > 0 else 1.0
    return dominant_pleat_frequency(metrics["_radii"], depth_ratio)


def ring_metrics(verts, ring_indices):
    """境界リングの重心・平均半径・フィット周長・実周長"""
    points = [verts[index] for index in ring_indices]
    count = len(points)
    cx = sum(point[0] for point in points) / count
    cy = sum(point[1] for point in points) / count
    cz = sum(point[2] for point in points) / count
    radii = [math.hypot(point[0] - cx, point[1] - cy) for point in points]
    mean_radius = sum(radii) / count
    loop_length = sum(
        _distance(points[index], points[(index + 1) % count]) for index in range(count)
    )
    return {
        "verts": count,
        "center": (cx, cy, cz),
        "z": cz,
        "mean_radius": mean_radius,
        "radius_min": min(radii),
        "radius_max": max(radii),
        # 実周長。断面が楕円でもプリーツが入っていてもそのまま辿った長さ
        "perimeter": loop_length,
        "breadth": max(point[0] for point in points) - min(point[0] for point in points),
        "depth": max(point[1] for point in points) - min(point[1] for point in points),
        "_radii": radii,
    }


def radial_spectrum(radii, frequencies):
    """リング半径列の周期成分の振幅(平均半径で正規化)を周波数ごとに返す。

    「半径が落ちる回数を数える」方式は使えない。断面が楕円だと半径が周方向に
    滑らかに増減するので、プリーツが無くても分割数の半分だけ「落ちる」ことになる。
    周波数で見れば楕円は k=2 に、プリーツは k=山数 に出るので分離できる。
    """
    count = len(radii)
    mean = sum(radii) / count
    if mean == 0.0:
        return {frequency: 0.0 for frequency in frequencies}
    spectrum = {}
    for frequency in frequencies:
        real = sum(
            radius * math.cos(2.0 * math.pi * frequency * index / count)
            for index, radius in enumerate(radii)
        )
        imaginary = sum(
            radius * math.sin(2.0 * math.pi * frequency * index / count)
            for index, radius in enumerate(radii)
        )
        spectrum[frequency] = 2.0 * math.sqrt(real * real + imaginary * imaginary) / count / mean
    return spectrum


def ellipse_reference_spectrum(count, depth_ratio, frequencies):
    """同じ分割数・同じ厚み比の「プリーツの無い楕円」の周期成分。

    楕円の半径は周方向に周期 π で増減するので k=2 だけでなく **k=4, 6, ... の
    偶数高調波にも漏れる**(実測: 厚み比 0.68 で k4=0.0089、0.40 で k4=0.0418。
    分割数には依存しない)。これを引かないと「意図しない波打ち」の判定が
    薄い断面のときに誤検出する。
    """
    reference = [
        math.sqrt(
            math.cos(2.0 * math.pi * index / count) ** 2
            + (depth_ratio * math.sin(2.0 * math.pi * index / count)) ** 2
        )
        for index in range(count)
    ]
    return radial_spectrum(reference, frequencies)


def residual_spectrum(radii, frequencies, depth_ratio=1.0):
    """楕円ぶんを差し引いた周期成分。プリーツ・ドレープだけが残る"""
    measured = radial_spectrum(radii, frequencies)
    reference = ellipse_reference_spectrum(len(radii), depth_ratio, frequencies)
    return {
        frequency: max(0.0, measured[frequency] - reference[frequency])
        for frequency in frequencies
    }


def dominant_pleat_frequency(radii, depth_ratio=1.0, min_frequency=3):
    """k=1(中心ずれ)と k=2(楕円)を除いた最大の周期成分 (山数, 正規化振幅)。

    プリーツもドレープも入っていなければ振幅はほぼ 0 になる。
    """
    count = len(radii)
    frequencies = list(range(min_frequency, count // 2 + 1))
    if not frequencies:
        return (0, 0.0)
    spectrum = residual_spectrum(radii, frequencies, depth_ratio)
    best = max(frequencies, key=lambda frequency: spectrum[frequency])
    return (best, spectrum[best])


# ------------------------------------------------------------------ パーツ1枚の検査


def part_report(mesh, height_units):
    """PartMesh 1枚を検査して数値と合否を返す"""
    verts, faces = mesh.verts, mesh.quads
    design = mesh.design or {}

    kinds = mesh.edge_kinds()
    ring_lengths = [_distance(verts[a], verts[b]) for a, b in kinds["ring"]]
    axial_lengths = [_distance(verts[a], verts[b]) for a, b in kinds["axial"]]
    all_lengths = ring_lengths + axial_lengths
    mean_edge = sum(all_lengths) / len(all_lengths) if all_lengths else 0.0

    topo = topology(verts, faces)
    loops = boundary_loops(topo.pop("_boundary_edges"))
    areas = [face_area(verts, face) for face in faces]
    area_eps = (mean_edge**2) * 1e-6
    degenerate = sum(1 for area in areas if area <= area_eps)

    duplicates = duplicate_verts(verts, mean_edge * 1e-4)
    crossings = self_intersections(
        verts, faces, cell=max(mean_edge * 2.0, 1e-9), eps=1e-9
    )

    outward = None
    if mesh.tubular:
        cx, cy = mesh.axis_center
        inside = 0
        for face in faces:
            normal = face_normal(verts, face)
            center = [
                sum(verts[index][axis] for index in face) / len(face) for axis in range(3)
            ]
            radial = (center[0] - cx, center[1] - cy, 0.0)
            if _length(radial) > 0.0 and _dot(normal, radial) > 0.0:
                inside += 1
        outward = inside / len(faces) if faces else 0.0

    top = ring_metrics(verts, mesh.rings["top"]) if mesh.rings.get("top") else None
    bottom = ring_metrics(verts, mesh.rings["bottom"]) if mesh.rings.get("bottom") else None

    # 断面の厚み比は実測から取る(楕円ぶんを周期成分から差し引くために必要)
    bottom_depth_ratio = (
        bottom["depth"] / bottom["breadth"] if bottom and bottom["breadth"] > 0 else 1.0
    )
    pleat_frequency, pleat_amplitude = (
        dominant_pleat_frequency(bottom["_radii"], bottom_depth_ratio) if bottom else (0, 0.0)
    )

    zs = [point[2] for point in verts]
    extent = max(zs) - min(zs) if zs else 0.0

    report = {
        "part": mesh.name,
        "topology": topo,
        "boundary_loops": len(loops),
        "boundary_loop_verts": sorted(len(loop) for loop in loops),
        "degenerate_faces": degenerate,
        "duplicate_verts": duplicates,
        "self_intersections": crossings,
        "radial_outward_ratio": outward,
        "edge_length": {
            "ring": _stats(ring_lengths),
            "axial": _stats(axial_lengths),
            "all": _stats(all_lengths),
        },
        "edge_length_over_h": {
            "ring": _stats(ring_lengths)["mean"] / height_units if height_units else None,
            "axial": _stats(axial_lengths)["mean"] / height_units if height_units else None,
            "all": mean_edge / height_units if height_units else None,
        },
        "z_extent": extent,
        "rings": {
            "top": {key: value for key, value in top.items() if not key.startswith("_")}
            if top
            else None,
            "bottom": {key: value for key, value in bottom.items() if not key.startswith("_")}
            if bottom
            else None,
        },
        "pleat_frequency": pleat_frequency,
        "pleat_amplitude": pleat_amplitude,
        "design": design,
    }

    hard = {}
    hard["nonmanifold"] = _check(topo["nonmanifold_edges"] == 0, topo["nonmanifold_edges"], 0)
    hard["loose_verts"] = _check(topo["loose_verts"] == 0, topo["loose_verts"], 0)
    hard["winding"] = _check(
        topo["winding_flipped_edges"] == 0, topo["winding_flipped_edges"], 0
    )
    hard["degenerate_faces"] = _check(degenerate == 0, degenerate, 0)
    hard["duplicate_verts"] = _check(duplicates == 0, duplicates, 0)
    hard["self_intersections"] = _check(crossings == 0, crossings, 0)
    hard["all_quads"] = _check(topo["tris"] == 0, topo["tris"], 0)
    if outward is not None:
        hard["radial_outward"] = _check(
            outward >= RADIAL_OUTWARD_MIN, outward, ">= %.2f" % RADIAL_OUTWARD_MIN
        )
    if design.get("boundary_loops") is not None:
        hard["boundary_loop_count"] = _check(
            len(loops) == design["boundary_loops"], len(loops), design["boundary_loops"]
        )
    if design.get("boundary_verts") is not None:
        expected = [design["boundary_verts"]] * design.get("boundary_loops", len(loops))
        hard["boundary_loop_verts"] = _check(
            sorted(len(loop) for loop in loops) == sorted(expected),
            sorted(len(loop) for loop in loops),
            sorted(expected),
        )
    # 上端はプリーツのテーパーが必ず 0 なので、実周長をサイズ表の寸法と直接比べられる
    if top is not None and design.get("top_perimeter"):
        hard["top_perimeter"] = _within(top["perimeter"], design["top_perimeter"], DIM_TOL)
    # 裾はプリーツが入ると布の長さが増えるので、独立に検算できるときだけ比べる
    if bottom is not None and design.get("bottom_perimeter"):
        hard["bottom_perimeter"] = _within(
            bottom["perimeter"], design["bottom_perimeter"], DIM_TOL
        )
    if design.get("length"):
        hard["length"] = _within(extent, design["length"], DIM_TOL)
    if bottom is not None and design.get("radial_modulations") is not None:
        expected = design["radial_modulations"]
        wanted = {item["frequency"]: item for item in expected}
        measured = (
            residual_spectrum(bottom["_radii"], sorted(wanted), bottom_depth_ratio)
            if wanted
            else {}
        )
        report["radial_spectrum"] = {
            "expected": expected,
            "measured": measured,
            "bottom_depth_ratio": bottom_depth_ratio,
            "strongest_unexpected": (pleat_frequency, pleat_amplitude),
        }
        for frequency, item in sorted(wanted.items()):
            floor = PLEAT_AMPLITUDE_FRACTION * item["depth"]
            hard["modulation_%s_%d" % (item["kind"], frequency)] = _check(
                measured[frequency] >= floor,
                measured[frequency],
                ">= %.4f" % floor,
            )
        # 指定していない周期成分が出ていないこと(k=1,2 は中心ずれと楕円なので除外済み)
        if pleat_frequency not in wanted:
            hard["no_unexpected_modulation"] = _check(
                pleat_amplitude < PLEAT_AMPLITUDE_NOISE,
                (pleat_frequency, pleat_amplitude),
                "< %.4f" % PLEAT_AMPLITUDE_NOISE,
            )
    # 折り目がウエストバンドの直下から裾まで続いているか(docs/garments.md 定義 #1・#2)。
    # 「腰では畳まれている」を「腰では折り目が無い」と実装すると上半分が滑らかな筒になり、
    # 数値も目視も通らないのにプリーツスカートに見えない、という状態になる。
    if design.get("pleats") and mesh.ring_count >= 3:
        top_frequency, top_amplitude = pleat_amplitude_at_ring(mesh, 1)
        report["pleat_amplitude_below_band"] = top_amplitude
        report["pleat_frequency_below_band"] = top_frequency
        if pleat_amplitude > 0.0:
            ratio = top_amplitude / pleat_amplitude
            report["pleat_crease_ratio"] = ratio
            hard["pleat_crease_reaches_top"] = _check(
                ratio >= PLEAT_TOP_CREASE_MIN_RATIO,
                ratio,
                ">= %.2f" % PLEAT_TOP_CREASE_MIN_RATIO,
            )
            hard["pleat_opens_downward"] = _check(
                pleat_amplitude > top_amplitude, (top_amplitude, pleat_amplitude), "裾 > 腰"
            )
    # プリーツを指定したなら、裾の布が設計上の周長より確かに長くなっていること。
    # 増える量は深さと duty と断面の形に依存して閉じた式にならないので、
    # ここは「予測値」ではなく**下限**として使う(山数の検算は上の周波数側が担う)。
    if design.get("pleats") and bottom is not None and design.get("bottom_fit_perimeter"):
        ratio = bottom["perimeter"] / design["bottom_fit_perimeter"]
        floor = 1.0 + 0.5 * design.get("pleat_depth", 0.0)
        report["fabric_ratio"] = ratio
        hard["pleat_fabric"] = _check(ratio >= floor, ratio, ">= %.3f" % floor)

    # エッジ長 /H は衣装1着ぶんの平均に対する実測値なので、判定は衣装全体
    # (costume_report)で行う。パーツ単位の値は内訳として報告するだけ。
    report["_edge_lengths"] = all_lengths

    warn = {}
    # CV は周方向と軸方向を分けて見る(混ぜると縦横比を不均一さと誤認する)
    for kind, lengths in (("ring", ring_lengths), ("axial", axial_lengths)):
        cv = _stats(lengths)["cv"]
        if cv is not None:
            warn["edge_length_cv_" + kind] = _check(
                cv <= EDGE_CV_MAX, cv, "<= %.2f" % EDGE_CV_MAX
            )

    report["hard"] = hard
    report["warn"] = warn
    report["failed"] = sorted(key for key, value in hard.items() if not value["ok"])
    report["warned"] = sorted(key for key, value in warn.items() if not value["ok"])
    report["verdict"] = "FAIL" if report["failed"] else ("WARN" if report["warned"] else "PASS")
    return report


def raw_mesh_report(name, verts, faces):
    """リング構造を持たない生のメッシュ(モディファイア評価後など)を検査する。

    設計値との突き合わせはできないので、トポロジと自己交差だけを見る。
    """
    edges = edge_usage(faces)
    lengths = [_distance(verts[a], verts[b]) for a, b in edges]
    mean_edge = sum(lengths) / len(lengths) if lengths else 0.0

    topo = topology(verts, faces)
    loops = boundary_loops(topo.pop("_boundary_edges"))
    areas = [face_area(verts, face) for face in faces]
    degenerate = sum(1 for area in areas if area <= (mean_edge**2) * 1e-6)
    duplicates = duplicate_verts(verts, mean_edge * 1e-4)
    crossings = self_intersections(verts, faces, cell=max(mean_edge * 2.0, 1e-9), eps=1e-9)

    hard = {
        "nonmanifold": _check(topo["nonmanifold_edges"] == 0, topo["nonmanifold_edges"], 0),
        "loose_verts": _check(topo["loose_verts"] == 0, topo["loose_verts"], 0),
        "winding": _check(
            topo["winding_flipped_edges"] == 0, topo["winding_flipped_edges"], 0
        ),
        "degenerate_faces": _check(degenerate == 0, degenerate, 0),
        "duplicate_verts": _check(duplicates == 0, duplicates, 0),
        "self_intersections": _check(crossings == 0, crossings, 0),
        "all_quads": _check(topo["tris"] == 0, topo["tris"], 0),
    }
    failed = sorted(key for key, value in hard.items() if not value["ok"])
    return {
        "part": name,
        "topology": topo,
        "boundary_loops": len(loops),
        "boundary_loop_verts": sorted(len(loop) for loop in loops),
        "degenerate_faces": degenerate,
        "duplicate_verts": duplicates,
        "self_intersections": crossings,
        "edge_length": _stats(lengths),
        "hard": hard,
        "failed": failed,
        "verdict": "FAIL" if failed else "PASS",
    }


def _check(ok, value, expected):
    return {"ok": bool(ok), "value": value, "expected": expected}


def _within(value, target, tolerance):
    ok = target != 0 and abs(value - target) / abs(target) <= tolerance
    return {
        "ok": bool(ok),
        "value": value,
        "expected": target,
        "error_ratio": (value - target) / target if target else None,
    }


# ------------------------------------------------------------------ 衣装一式の検査


def joint_gap(mesh_a, ring_a, mesh_b, ring_b):
    """接合するリング同士の対応頂点の最大ずれ"""
    indices_a = mesh_a.rings[ring_a]
    indices_b = mesh_b.rings[ring_b]
    if len(indices_a) != len(indices_b):
        return float("inf")
    return max(
        _distance(mesh_a.verts[a], mesh_b.verts[b]) for a, b in zip(indices_a, indices_b)
    )


def costume_report(built, normalized_spec):
    """パーツ一式(parts.build_all の戻り値)を検査する"""
    meshes = built["parts"]
    height_units = normalized_spec["assumed_height"] / normalized_spec["meters_per_unit"]

    parts = [part_report(mesh, height_units) for mesh in meshes]
    by_name = {mesh.name: mesh for mesh in meshes}

    mean_edges = {}
    for mesh, report in zip(meshes, parts):
        mean_edges[mesh.name] = report["edge_length"]["all"]["mean"] or 0.0

    # 接合リングの頂点は座標が一致しているのが正しいので、交差検査から外す
    joint_verts = {mesh.name: set() for mesh in meshes}
    joints = []
    for joint in normalized_spec["joints"]:
        mesh_a, mesh_b = by_name[joint["a"]], by_name[joint["b"]]
        gap = joint_gap(mesh_a, joint["a_ring"], mesh_b, joint["b_ring"])
        tolerance = max(mean_edges[mesh_a.name], mean_edges[mesh_b.name]) * 1e-3
        joints.append(
            {
                "a": joint["a"],
                "a_ring": joint["a_ring"],
                "b": joint["b"],
                "b_ring": joint["b_ring"],
                "gap": gap,
                "ok": gap <= tolerance,
                "tolerance": tolerance,
            }
        )
        joint_verts[mesh_a.name].update(mesh_a.rings[joint["a_ring"]])
        joint_verts[mesh_b.name].update(mesh_b.rings[joint["b_ring"]])

    crossings = []
    for left in range(len(meshes)):
        for right in range(left + 1, len(meshes)):
            mesh_a, mesh_b = meshes[left], meshes[right]
            cell = max(mean_edges[mesh_a.name], mean_edges[mesh_b.name], 1e-9) * 2.0
            count = cross_intersections(
                mesh_a,
                mesh_b,
                cell=cell,
                eps=1e-9,
                skip_a=joint_verts[mesh_a.name],
                skip_b=joint_verts[mesh_b.name],
            )
            crossings.append({"a": mesh_a.name, "b": mesh_b.name, "intersections": count})

    total_faces = sum(len(mesh.quads) for mesh in meshes)
    total_verts = sum(len(mesh.verts) for mesh in meshes)

    # 実測の「エッジ長 / 身長 0.0206-0.0311」は**衣装1着ぶんの平均**の値なので、
    # 全パーツの辺をプールして測る。接合のために本体と周方向分割を共有する
    # ウエストバンドは単体では細かく出るが、それはパーツ単体で判定すべき値ではない。
    pooled = [length for report in parts for length in report.pop("_edge_lengths")]
    pooled_stats = _stats(pooled)
    edge_over_h = (pooled_stats["mean"] / height_units) if height_units and pooled else None

    hard = {
        "poly_budget": _check(
            total_faces <= normalized_spec["poly_budget"],
            total_faces,
            "<= %d" % normalized_spec["poly_budget"],
        ),
        "joints_closed": _check(
            all(joint["ok"] for joint in joints),
            [joint["gap"] for joint in joints],
            "全て許容内",
        ),
        "part_intersections": _check(
            all(item["intersections"] == 0 for item in crossings),
            sum(item["intersections"] for item in crossings),
            0,
        ),
    }

    low, high = EDGE_LENGTH_OVER_H_RANGE
    warn = {}
    if edge_over_h is not None:
        warn["edge_length_over_h"] = _check(
            low <= edge_over_h <= high, edge_over_h, "%.4f-%.4f" % (low, high)
        )

    failed = sorted(key for key, value in hard.items() if not value["ok"])
    failed += ["%s.%s" % (report["part"], key) for report in parts for key in report["failed"]]
    warned = sorted(key for key, value in warn.items() if not value["ok"])
    warned += ["%s.%s" % (report["part"], key) for report in parts for key in report["warned"]]

    return {
        "name": normalized_spec["name"],
        "assumed_height_m": normalized_spec["assumed_height"],
        "meters_per_unit": normalized_spec["meters_per_unit"],
        "height_units": height_units,
        "sizing": {
            key: value for key, value in built["sizing"].items() if not key.startswith("_")
        },
        "totals": {"verts": total_verts, "faces": total_faces},
        "edge_length": pooled_stats,
        "edge_length_over_h": edge_over_h,
        "parts": parts,
        "joints": joints,
        "part_pairs": crossings,
        "hard": hard,
        "warn": warn,
        "failed": sorted(failed),
        "warned": sorted(warned),
        "verdict": "FAIL" if failed else ("WARN" if warned else "PASS"),
    }
