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

# ---- ブラウスの「形」の定義(docs/garments.md #9〜#14)のしきい値 ----
#: 肩傾斜の許容差(度)。新文化式は 前22°/後18° なので設計値20°の左右幅ぶん
SHOULDER_SLOPE_TOL_DEG = 8.0
#: 袖の垂れ角の許容差(度)。角度そのものは設計値で、これは回帰の網
SLEEVE_DROOP_TOL_DEG = 3.0
#: 袖山の下から肘までのリング周長 / 二の腕周長 の下限。
#: これを外すと「全長を滑らかに細めた針のような円錐」が通ってしまう
SLEEVE_HOLD_MIN_RATIO = 0.92
#: カフスの帯の中でリング周長がばらついてよい割合(帯は一定半径のはず)
CUFF_BAND_STEP_MAX = 0.01
#: シャツと言えるボタンの最少個数
BUTTON_MIN_COUNT = 4
#: ボタンの間隔の変動係数の上限
BUTTON_GAP_CV_MAX = 0.15
#: 前立ての幅 / 前開きの隙間の幅 の下限。同じ幅だと縁が合って筋が見えるので余裕を持たせる
PLACKET_COVER_MARGIN = 1.5
#: ボタンの 厚み / 直径。実物のシャツ用貝ボタンは 2.3mm / 11.5mm = 0.20。
#: ここを見ていなかったので、迫り出す円錐(0.26・裏面も穴も無し)が通っていた
BUTTON_THICKNESS_RATIO = 0.20
#: その許容差(比)。1.5〜5mm 厚の実物があるので広めに取る
BUTTON_THICKNESS_TOL = 0.40
#: 穴の最少数。シャツは4つ穴が標準、2つ穴もある。0 は「ボタンではない何か」
BUTTON_MIN_HOLES = 2
#: 裾のシャツテールの最小の落差(m)。これを下回ると「水平に切った裾」と区別できない
SHIRTTAIL_MIN_DROP = 0.01
#: カフスの直前の周長 / カフスの周長 の下限。1.0 だと段差が無く、
#: 帯が「一定である」ことは測れても「見えるか」は測れていない
CUFF_GATHER_MIN = 1.10
#: 胸のふくらみが裾の前面より前へ出るべき最小量(m)。**設計値**。
#: これを下回ると楕円断面だけの胴(= メンズシャツ)と区別できない
BUST_PROJECTION_MIN = 0.015

# ---- ケープの「形」の定義(docs/garments.md)のしきい値 ----
#: 裾の弧長 / 上端の弧長 の下限。これ未満は肩から広がっておらず、
#: ただの開いた筒(=ケープではない)。**設計値**
CAPE_MIN_FLARE = 1.3
#: 裾の開き幅の許容差(比)。寸法ではなく「前が開いている」ことの確認なので広め
CAPE_GAP_TOL = 0.10

# ---- フードの「形」の定義(docs/garments.md)のしきい値 ----
#: フードの高さの許容差(比)
HOOD_DEPTH_TOL = 0.05

# ---- パンツの「形」の定義(docs/garments.md)のしきい値 ----
#: 股上・わたり・裾周の許容差(比)。すべて設計値由来の寸法なので少し広め
PANTS_DIM_TOL = 0.05
#: 股下の許容差(比)
PANTS_INSEAM_TOL = 0.03
#: 左右対称の許容: |左右の裾重心 x の和| / |差| の上限
PANTS_SYMMETRY_TOL = 0.10


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


def armhole_loops(mesh):
    """袖ぐりの境界ループを左右に分けて返す。

    胴は「前が開いた筒 + 袖ぐり2つ」なので境界ループは3本。そのうち**一番長い1本が
    外周**(襟ぐり + 前開き + 裾がつながったもの)で、残りが袖ぐり。
    左右は重心の x 符号で決める(左が +X)。

    戻り値: {"l": {...}, "r": {...}}(見つかったものだけ)
    """
    topo = topology(mesh.verts, mesh.quads)
    loops = boundary_loops(topo["_boundary_edges"])
    if len(loops) < 2:
        return {}
    measured = [ring_metrics(mesh.verts, loop) for loop in loops]
    # 外周を除いた残りを袖ぐりとみなす
    outer = max(range(len(measured)), key=lambda index: measured[index]["perimeter"])
    result = {}
    for index, metrics in enumerate(measured):
        if index == outer:
            continue
        label = "l" if metrics["center"][0] >= 0.0 else "r"
        if label in result and result[label]["perimeter"] >= metrics["perimeter"]:
            continue
        result[label] = {
            key: value for key, value in metrics.items() if not key.startswith("_")
        }
        result[label]["indices"] = list(loops[index])
    return result


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
        # 水平に投影した周長。裾がカーブしている(シャツテール)と実周長は
        # 上下動のぶん伸びるが、「胴まわりの太さ」として比べたいのはこちら。
        # 水平なリングでは両者は一致するので、平らな裾のパーツには影響しない
        "perimeter_xy": sum(
            math.hypot(
                points[index][0] - points[index - 1][0],
                points[index][1] - points[index - 1][1],
            )
            for index in range(count)
        ),
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


def open_arc_length(verts, ring_indices):
    """開いたリング(弧)の実長。ring_metrics と違い最初と最後を繋がない"""
    points = [verts[index] for index in ring_indices]
    return sum(_distance(points[index], points[index - 1]) for index in range(1, len(points)))


def cape_metrics(mesh, shoulder_t=None):
    """ケープの実測(メッシュだけから出す。生成側の申告は使わない)"""
    top = mesh.rings.get("top") or []
    bottom = mesh.rings.get("bottom") or []
    top_arc = open_arc_length(mesh.verts, top)
    hem_arc = open_arc_length(mesh.verts, bottom)
    hem_gap = _distance(mesh.verts[bottom[0]], mesh.verts[bottom[-1]]) if bottom else 0.0
    result = {
        "measured_top_arc": top_arc,
        "measured_hem_arc": hem_arc,
        "measured_hem_gap": hem_gap,
        "measured_cape_flare": (hem_arc / top_arc) if top_arc > 0.0 else 0.0,
    }
    # 肩の張り: **肩線に最も近いリング行だけ**の x 差し渡し。
    # 「肩線の1.7倍までの窓の最大」で測ると、flare で広がり始めた行が窓に入り、
    # リングが密・flare が急・丈が短いだけで設計値を超える誤検知になる
    # (合法な spec の AI 出力が _prebuild_check で捨てられていた。PR #8 レビュー)。
    # 行は ring_size × ring_count の添字構造から復元する
    if shoulder_t is not None and mesh.ring_count > 1:
        size, count = mesh.ring_size, mesh.ring_count
        nearest = min(range(count), key=lambda row: abs(row / (count - 1) - shoulder_t))
        result["shoulder_row_t"] = nearest / (count - 1)
        xs = [
            mesh.verts[index][0]
            for index in range(nearest * size, (nearest + 1) * size)
        ]
        result["measured_shoulder_span"] = max(xs) - min(xs)
    return result


def hood_metrics(mesh):
    """フードの実測(メッシュだけから出す)。

    リング行は ring_size × ring_count の添字構造から復元する(holed でない前提)。
    """
    size, count = mesh.ring_size, mesh.ring_count
    rows = [list(range(row * size, (row + 1) * size)) for row in range(count)]
    arcs = [open_arc_length(mesh.verts, row) for row in rows]
    gaps = [
        _distance(mesh.verts[row[0]], mesh.verts[row[-1]]) for row in rows
    ]
    # リングは上(先端)から下(縫い目)へ並ぶ。縫い目 = 最終行
    seam_top_z = max(mesh.verts[index][2] for index in rows[-1])
    top_z = max(point[2] for point in mesh.verts)
    return {
        "measured_hood_max_arc": max(arcs),
        "measured_hood_depth": top_z - seam_top_z,
        "measured_hood_face_gap": max(gaps),
    }


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
    warn = {}
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
    # 裾はプリーツが入ると布の長さが増えるので、独立に検算できるときだけ比べる。
    # 裾がカーブしているパーツ(シャツテール)は水平に投影した周長で比べる
    if bottom is not None and design.get("bottom_perimeter"):
        key = "perimeter_xy" if design.get("bottom_perimeter_projected") else "perimeter"
        hard["bottom_perimeter"] = _within(
            bottom[key], design["bottom_perimeter"], DIM_TOL
        )
    if design.get("length"):
        hard["length"] = _within(extent, design["length"], DIM_TOL)
    if mesh.tubular and bottom is not None and design.get("radial_modulations") is not None:
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
    # ---- ケープの定義(docs/garments.md)を検証項目にしたもの ----
    if design.get("cape_top_arc"):
        report.update(cape_metrics(mesh, design.get("cape_shoulder_t")))
        # 肩の張り(ハンガー型)。ただの円錐はスカートに見える(ブラインド識別で実証)
        if "cape_shoulder_span" in design:
            if design["cape_shoulder_span"] and "measured_shoulder_span" in report:
                hard["cape_sits_on_the_shoulders"] = _within(
                    report["measured_shoulder_span"], design["cape_shoulder_span"], DIM_TOL
                )
            else:
                # リング割りが粗くて肩線の近くに行が無い(builder が設計値を
                # None にしている)。黙ってゲートを消さず warn に出す
                warn["cape_shoulder_row_missing"] = _check(
                    False, report.get("shoulder_row_t"), "肩線の近くにリング行が要る"
                )
        # 上端の弧長 = 首回り×(1+ゆとり)から前開きの楔を除いた製図値
        hard["cape_top_arc"] = _within(
            report["measured_top_arc"], design["cape_top_arc"], DIM_TOL
        )
        # 肩から裾へ広がっていること(下限は定義、値は spec との一致)
        hard["cape_flares_from_the_shoulder"] = _check(
            report["measured_cape_flare"] >= CAPE_MIN_FLARE,
            report["measured_cape_flare"],
            ">= %.2f" % CAPE_MIN_FLARE,
        )
        hard["cape_flare_matches_spec"] = _within(
            report["measured_cape_flare"], design["cape_flare"], DIM_TOL
        )
        # 前が開いていること(裾の開き幅が設計どおり)
        hard["cape_front_open"] = _within(
            report["measured_hem_gap"], design["cape_hem_gap"], CAPE_GAP_TOL
        )
    # ---- パンツの定義(docs/garments.md)を検証項目にしたもの ----
    if design.get("pants_rise"):
        crotch_ring = mesh.rings.get("crotch") or []
        crotch_z = (
            sum(verts[index][2] for index in crotch_ring) / len(crotch_ring)
            if crotch_ring
            else None
        )
        report["measured_crotch_z"] = crotch_z
        if top is not None and crotch_z is not None:
            report["measured_rise"] = top["z"] - crotch_z
            hard["pants_rise"] = _within(
                report["measured_rise"], design["pants_rise"], PANTS_DIM_TOL
            )
        hems = {}
        for side in ("l", "r"):
            ring = mesh.rings.get("hem_" + side)
            if not ring:
                hard["pants_has_two_legs"] = _check(False, "hem_%s 無し" % side, "裾リング2本")
                continue
            hems[side] = ring_metrics(verts, ring)
            report["measured_hem_perimeter_" + side] = hems[side]["perimeter"]
            hard["pants_hem_" + side] = _within(
                hems[side]["perimeter"], design["pants_hem"], PANTS_DIM_TOL
            )
            if crotch_z is not None:
                report["measured_inseam_" + side] = crotch_z - hems[side]["z"]
                hard["pants_inseam_" + side] = _within(
                    report["measured_inseam_" + side],
                    design["pants_inseam"],
                    PANTS_INSEAM_TOL,
                )
            thigh_ring = mesh.rings.get("thigh_" + side)
            if design.get("pants_thigh") and thigh_ring:
                thigh = ring_metrics(verts, thigh_ring)
                report["measured_thigh_perimeter_" + side] = thigh["perimeter"]
                hard["pants_thigh_" + side] = _within(
                    thigh["perimeter"], design["pants_thigh"], PANTS_DIM_TOL
                )
        if len(hems) == 2:
            left_x = hems["l"]["center"][0]
            right_x = hems["r"]["center"][0]
            spread = abs(left_x - right_x)
            hard["pants_legs_symmetric"] = _check(
                spread > 0.0 and abs(left_x + right_x) <= PANTS_SYMMETRY_TOL * spread,
                (left_x, right_x),
                "裾の重心 x が左右対称",
            )
    # ---- フードの定義(docs/garments.md)を検証項目にしたもの ----
    if design.get("hood_arc_floor"):
        report.update(hood_metrics(mesh))
        # 頭を包んでいること(一番太いリングが頭囲ゆとり込みの3/4以上)
        hard["hood_wraps_the_head"] = _check(
            report["measured_hood_max_arc"] >= design["hood_arc_floor"],
            report["measured_hood_max_arc"],
            ">= %.4f" % design["hood_arc_floor"],
        )
        # 頭が入る高さがあること(縫い目の上端から頂まで)
        hard["hood_depth"] = _within(
            report["measured_hood_depth"], design["hood_depth"], HOOD_DEPTH_TOL
        )
        # 顔の開口が開いていること
        hard["hood_face_open"] = _check(
            report["measured_hood_face_gap"] >= design["hood_face_gap_floor"],
            report["measured_hood_face_gap"],
            ">= %.4f" % design["hood_face_gap_floor"],
        )
    # ---- ブラウスの定義(docs/garments.md)を検証項目にしたもの ----
    if design.get("shoulder_width"):
        report.update(bodice_metrics(mesh, design))
        hard["shoulder_width"] = _within(
            report["measured_shoulder_width"], design["shoulder_width"], DIM_TOL
        )
        hard["neck_opening"] = _within(
            report["measured_neck_perimeter"], design["neck_perimeter"], 0.05
        )
        if report["measured_armhole_depth"] is not None:
            hard["armhole_depth"] = _within(
                report["measured_armhole_depth"], design["armhole_depth"], 0.15
            )
        if report["measured_shoulder_span"] is not None:
            hard["shoulder_exists"] = _within(
                report["measured_shoulder_span"], design["shoulder_span"], 0.25
            )
        if report["armhole_on_side"] is not None:
            hard["armhole_on_side"] = _check(
                report["armhole_on_side"], report["armhole_centres"], "体側にある"
            )
        # ---- 形の定義 #7〜#9 ----
        if design.get("garment_length") and report["measured_garment_length"] is not None:
            hard["garment_length"] = _within(
                report["measured_garment_length"], design["garment_length"], 0.08
            )
        # 「前下がりが無い」は前下がりを測っても検出できない。設計値の有無で
        # 条件分岐すると、前下がりを 0 にした瞬間にゲートごと消える。
        # 胴には必ず襟ぐりがあるので、ここは**常に**判定する
        if "front_neck_drop" in design and report["measured_front_neck_drop"] is not None:
            hard["neck_front_drop"] = _within(
                report["measured_front_neck_drop"], design["front_neck_drop"], 0.30
            )
            # 前が後ろより深いこと。左右対称の水平な輪ではないことの担保
            hard["neck_drops_forward"] = _check(
                report["measured_front_neck_drop"]
                > report["measured_back_neck_drop"] + 1e-6,
                (report["measured_front_neck_drop"], report["measured_back_neck_drop"]),
                "前 > 後ろ",
            )
        # 定義 #16。「裾が水平」は裾を測っても検出できないので、
        # 前下がりと同じく**常に**判定する(設計値の有無で分岐しない)
        if "shirttail_drop" in design and report["measured_shirttail_drop"] is not None:
            hard["shirttail"] = _within(
                report["measured_shirttail_drop"], design["shirttail_drop"], 0.25
            )
            hard["shirttail_is_curved"] = _check(
                report["measured_shirttail_drop"] > SHIRTTAIL_MIN_DROP
                and report["measured_shirttail_front_drop"] > SHIRTTAIL_MIN_DROP,
                (
                    report["measured_shirttail_drop"],
                    report["measured_shirttail_front_drop"],
                ),
                "前後とも脇より %.3f 以上下がる" % SHIRTTAIL_MIN_DROP,
            )
        # 定義 #18。設計値が 0 でもゲートが消えないよう "in design" で常に判定し、
        # かつ**下限**を見る(0 にすると「設計 0 = 実測 0」で通ってしまう)
        if (
            "bust_projection_over_hem" in design
            and report["measured_bust_projection"] is not None
        ):
            hard["bust_projection"] = _check(
                report["measured_bust_projection"] >= BUST_PROJECTION_MIN,
                report["measured_bust_projection"],
                ">= %.3f" % BUST_PROJECTION_MIN,
            )
            hard["bust_is_above_the_waist"] = _check(
                (report["measured_bust_z_fraction"] or 0.0) >= 0.5,
                report["measured_bust_z_fraction"],
                ">= 0.5(丈の上半分)",
            )
        if (
            design.get("shoulder_slope_degrees")
            and report["measured_shoulder_slope_degrees"] is not None
        ):
            target = design["shoulder_slope_degrees"]
            measured = report["measured_shoulder_slope_degrees"]
            hard["shoulder_slope"] = _check(
                abs(measured - target) <= SHOULDER_SLOPE_TOL_DEG,
                measured,
                "%.1f ± %.1f 度" % (target, SHOULDER_SLOPE_TOL_DEG),
            )

    if design.get("sleeve_length_target") and bottom is not None:
        shoulder = design["shoulder_point"]
        reach = _distance(shoulder, bottom["center"])
        report["measured_sleeve_reach"] = reach
        hard["sleeve_length"] = _within(reach, design["sleeve_length_target"], 0.08)
        # ---- 形の定義 #10〜#12 ----
        report.update(sleeve_metrics(mesh, design))
        if report["measured_droop_degrees"] is not None:
            target = design.get("droop_degrees", 0.0)
            hard["sleeve_droops"] = _check(
                abs(report["measured_droop_degrees"] - target) <= SLEEVE_DROOP_TOL_DEG,
                report["measured_droop_degrees"],
                "%.1f ± %.1f 度" % (target, SLEEVE_DROOP_TOL_DEG),
            )
        if report["measured_hold_ratio"] is not None:
            hard["sleeve_not_cone"] = _check(
                report["measured_hold_ratio"] >= SLEEVE_HOLD_MIN_RATIO,
                report["measured_hold_ratio"],
                ">= %.2f" % SLEEVE_HOLD_MIN_RATIO,
            )
        if report["measured_cuff_perimeter"] is not None:
            hard["cuff_perimeter"] = _within(
                report["measured_cuff_perimeter"], design["cuff_perimeter"], 0.08
            )
            hard["cuff_band_is_flat"] = _check(
                report["measured_cuff_band_step"] <= CUFF_BAND_STEP_MAX,
                report["measured_cuff_band_step"],
                "<= %.3f" % CUFF_BAND_STEP_MAX,
            )
            # 定義 #17: 帯が一定なだけでは silhouette に段差が出ず、
            # 「幾何としては在るのに絵では見えない」ままになる
            if report["measured_cuff_gather"] is not None:
                hard["cuff_is_visible"] = _check(
                    report["measured_cuff_gather"] >= CUFF_GATHER_MIN,
                    report["measured_cuff_gather"],
                    ">= %.2f" % CUFF_GATHER_MIN,
                )
        else:
            hard["cuff_band_exists"] = _check(False, len(design.get("cuff_rings", [])), ">= 2")

    # ---- 定義 #13: 前立て ----
    if design.get("placket_width") and not design.get("button_count"):
        report.update(placket_metrics(mesh, design))
        hard["placket_width"] = _within(
            report["measured_placket_width"], design["placket_width"], 0.05
        )
        hard["placket_centred"] = _check(
            report["measured_placket_centred"], report["measured_placket_width"], "前中心"
        )
        # 前立ては前開きの隙間より広くないと、隙間が黒い筋として見える。
        # 「幅が設計どおり」だけでは覆えているかを見ていない
        gap = design.get("front_gap_width")
        if gap is not None:
            hard["placket_covers_the_opening"] = _check(
                report["measured_placket_width"] >= gap * PLACKET_COVER_MARGIN,
                (report["measured_placket_width"], gap),
                ">= 隙間 × %.1f" % PLACKET_COVER_MARGIN,
            )

    # ---- 定義 #14: ボタン ----
    if design.get("button_count"):
        report.update(button_metrics(mesh, design))
        hard["button_count"] = _check(
            report["measured_button_count"] == design["button_count"]
            and design["button_count"] >= BUTTON_MIN_COUNT,
            report["measured_button_count"],
            "%d (>= %d)" % (design["button_count"], BUTTON_MIN_COUNT),
        )
        hard["button_spacing"] = _check(
            report["measured_button_gap_cv"] <= BUTTON_GAP_CV_MAX,
            report["measured_button_gap_cv"],
            "<= %.2f" % BUTTON_GAP_CV_MAX,
        )
        hard["buttons_on_placket"] = _check(
            report["buttons_inside_placket"] and report["buttons_within_placket_z"],
            (report["buttons_inside_placket"], report["buttons_within_placket_z"]),
            "前立ての内側",
        )
        # ---- 定義 #19: ボタンが前立ての表面に**沿っている** ----
        # #14 の「内側にある」は x と z しか見ておらず、y(前後)の浮きを
        # 検出できなかった(ワンピースで実証)。裏面と表面の離隔は
        # standoff(意図した浮かせ)以上・ボタンの厚み以下であること
        if report.get("measured_button_float_gap") is not None:
            low, high = report["measured_button_float_gap"]
            slack = max(
                report["measured_button_thickness"] or 0.0,
                design.get("button_standoff") or 0.0,
            )
            hard["buttons_touch_the_placket"] = _check(
                low >= -1e-9 and high <= slack + 1e-9,
                report["measured_button_float_gap"],
                "0 〜 %.4f(表面から浮かず、沈まず)" % slack,
            )
        # ---- 定義 #15: ボタン**そのものの形**。#14 は個数・間隔・位置しか見ていない ----
        if design.get("button_diameter"):
            hard["button_diameter"] = _within(
                report["measured_button_diameter"], design["button_diameter"], 0.20
            )
        if report["measured_button_thickness_ratio"] is not None:
            hard["button_is_a_flat_disc"] = _within(
                report["measured_button_thickness_ratio"],
                BUTTON_THICKNESS_RATIO,
                BUTTON_THICKNESS_TOL,
            )
        if report["measured_button_holes"] is not None:
            # 申告と一致するだけでは駄目。**穴が無いボタンはシャツのボタンではない**
            # ので下限も見る(holes=0 にすると申告0と実測0が一致して通ってしまう)
            hard["button_holes"] = _check(
                report["measured_button_holes"] == design.get("button_holes")
                and report["measured_button_holes"] >= BUTTON_MIN_HOLES
                and report["measured_button_hole_spacing_cv"] <= BUTTON_GAP_CV_MAX,
                (
                    report["measured_button_holes"],
                    report["measured_button_hole_spacing_cv"],
                ),
                "%d個以上・等間隔(申告 %s)" % (BUTTON_MIN_HOLES, design.get("button_holes")),
            )

    # 折り目がウエストバンドの直下から裾まで続いているか(docs/garments.md 定義 #1・#2)。
    # 「腰では畳まれている」を「腰では折り目が無い」と実装すると上半分が滑らかな筒になり、
    # 数値も目視も通らないのにプリーツスカートに見えない、という状態になる。
    if design.get("pleats") and mesh.ring_count >= 3 and not mesh.holed:
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


def seam_distance(mesh_a, ring_a, mesh_b, ring_b):
    """縫い合わせる境界どうしの隔たり。分割数が違ってよい版。

    片方の各頂点から相手の最も近い頂点までの距離の最大値。座標の一致は求めず
    「縫える距離にあるか」だけを見る(袖ぐりと袖山は分割数もいせ込み量も違う)。
    """
    points_a = [mesh_a.verts[index] for index in mesh_a.rings.get(ring_a, [])]
    points_b = [mesh_b.verts[index] for index in mesh_b.rings.get(ring_b, [])]
    if not points_a or not points_b:
        return float("inf")
    worst = 0.0
    for point in points_a:
        worst = max(worst, min(_distance(point, other) for other in points_b))
    return worst


def bodice_metrics(mesh, design):
    """ブラウスの胴について、定義 #1〜#5 と #7〜#9 を測る。

    設計値との突き合わせは part_report がやる。ここは**実物から測るだけ**。
    """
    loops = armhole_loops(mesh)
    result = {
        "measured_shoulder_width": None,
        "measured_neck_perimeter": None,
        "measured_neck_seam": None,
        "measured_armhole_depth": None,
        "measured_shoulder_span": None,
        "armhole_on_side": None,
        "armhole_centres": {},
        "measured_front_neck_drop": None,
        "measured_back_neck_drop": None,
        "measured_shoulder_slope_degrees": None,
        "measured_garment_length": None,
        "measured_shirttail_drop": None,
        "measured_shirttail_front_drop": None,
        "measured_bust_projection": None,
        "measured_bust_z_fraction": None,
    }

    # 定義 #16: 裾は脇がいちばん高く、前後の中心が下がる
    hem = mesh.rings.get("bottom")
    if hem:
        points = [mesh.verts[index] for index in hem]
        side = max(points, key=lambda point: abs(point[0]))
        front = min(points, key=lambda point: point[1])
        back = max(points, key=lambda point: point[1])
        result["measured_shirttail_drop"] = side[2] - back[2]
        result["measured_shirttail_front_drop"] = side[2] - front[2]

        # 定義 #18: 胸のふくらみ。メッシュ全体でいちばん前に出ている点が、
        # 裾の前面よりどれだけ前か。楕円断面だけの胴だとほぼ 0 になる。
        # 高さも見る(いちばん前が裾だったら、それはふくらみではない)
        nose = min(mesh.verts, key=lambda point: point[1])
        result["measured_bust_projection"] = front[1] - nose[1]
        zs = [point[2] for point in mesh.verts]
        span = max(zs) - min(zs)
        if span > 0:
            result["measured_bust_z_fraction"] = (nose[2] - min(zs)) / span

    shoulder = mesh.rings.get("shoulder")
    if shoulder:
        xs = [mesh.verts[index][0] for index in shoulder]
        result["measured_shoulder_width"] = max(xs) - min(xs)

    # 襟ぐり = 外周のうち一番高いところにある部分…ではなく、上端リングそのもの
    top = mesh.rings.get("top")
    if top:
        points = [mesh.verts[index] for index in top]
        # 前下がりのぶん立体的な縫い目長は首回りより長くなる(実物の襟も首より長い)。
        # 「首に合うか」は **水平に投影した周長** で見る。縫い目長は襟の設計値として別に出す
        flat = [(point[0], point[1], 0.0) for point in points]
        result["measured_neck_perimeter"] = sum(
            _distance(flat[index], flat[index - 1]) for index in range(1, len(flat))
        ) + _distance(flat[0], flat[-1])
        result["measured_neck_seam"] = sum(
            _distance(points[index], points[index - 1]) for index in range(1, len(points))
        )

        # 定義 #8: 側頸点(左右の端)を基準に、前中心と後ろ中心がどれだけ下がっているか
        snp = max(points, key=lambda point: abs(point[0]))
        front = min(points, key=lambda point: point[1])
        back = max(points, key=lambda point: point[1])
        result["measured_front_neck_drop"] = snp[2] - front[2]
        result["measured_back_neck_drop"] = snp[2] - back[2]
        result["measured_garment_length"] = snp[2] - min(
            point[2] for point in mesh.verts
        )

        # 定義 #9: 側頸点から肩先への傾き
        if shoulder:
            tip = max((mesh.verts[index] for index in shoulder), key=lambda p: abs(p[0]))
            run = abs(tip[0]) - abs(snp[0])
            if run > 1e-9:
                result["measured_shoulder_slope_degrees"] = math.degrees(
                    math.atan2(snp[2] - tip[2], run)
                )

    if loops and shoulder:
        shoulder_z = max(mesh.verts[index][2] for index in shoulder)
        depths, spans, on_side = [], [], []
        neck_points = [mesh.verts[index] for index in (top or [])]
        for label, loop in loops.items():
            indices = loop["indices"]
            lowest = min(mesh.verts[index][2] for index in indices)
            depths.append(shoulder_z - lowest)
            centre = loop["center"]
            result["armhole_centres"][label] = [round(value, 5) for value in centre]
            half_width = (result["measured_shoulder_width"] or 0.0) / 2.0
            on_side.append(abs(centre[0]) > half_width * 0.5 and abs(centre[1]) < abs(centre[0]))
            if neck_points:
                # 肩の渡り幅 = 襟ぐりの輪と袖ぐりの輪の最短距離(定義 #1)
                spans.append(
                    min(
                        _distance(mesh.verts[index], point)
                        for index in indices
                        for point in neck_points
                    )
                )
        if depths:
            result["measured_armhole_depth"] = sum(depths) / len(depths)
        if spans:
            result["measured_shoulder_span"] = sum(spans) / len(spans)
        if on_side:
            result["armhole_on_side"] = all(on_side)
    return result


def sleeve_metrics(mesh, design):
    """袖について定義 #10〜#12 を測る(形の検証)。

    ランドマーク間の距離だけでは「水平に突き出した針のような円錐」でも合格してしまう。
    ここで測るのは**軸の向き**と**周長のプロファイル**。
    """
    result = {
        "measured_droop_degrees": None,
        "ring_perimeters": [],
        "measured_hold_ratio": None,
        "measured_cuff_perimeter": None,
        "measured_cuff_band_step": None,
        "measured_cuff_gather": None,
    }
    if not mesh.ring_size or mesh.ring_count < 2:
        return result

    rings = [
        [mesh.verts[index] for index in loft_ring_indices(mesh.ring_size, mesh.ring_count, r)]
        for r in range(mesh.ring_count)
    ]
    result["ring_perimeters"] = [
        sum(_distance(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))
        for ring in rings
    ]

    def centre(ring):
        return tuple(sum(point[axis] for point in ring) / len(ring) for axis in range(3))

    # 定義 #10: 軸(上端中心 → 下端中心)が水平から何度下がっているか
    top, bottom = centre(rings[0]), centre(rings[-1])
    axis = tuple(bottom[a] - top[a] for a in range(3))
    horizontal = math.hypot(axis[0], axis[1])
    if horizontal > 1e-9 or abs(axis[2]) > 1e-9:
        result["measured_droop_degrees"] = math.degrees(math.atan2(-axis[2], horizontal))

    # 定義 #11: 袖山の下から肘までの各リングが、二の腕の太さを保っているか
    hold = [index for index in design.get("hold_rings", []) if index < len(rings)]
    bicep = design.get("bicep_perimeter")
    if hold and bicep:
        result["measured_hold_ratio"] = (
            min(result["ring_perimeters"][index] for index in hold) / bicep
        )

    # 定義 #12: カフスは一定半径の帯。最後の2リングの周長差が無いこと
    cuff = [index for index in design.get("cuff_rings", []) if index < len(rings)]
    if len(cuff) >= 2:
        lengths = [result["ring_perimeters"][index] for index in cuff]
        result["measured_cuff_perimeter"] = lengths[-1]
        widest = max(lengths)
        result["measured_cuff_band_step"] = (
            (widest - min(lengths)) / widest if widest > 0 else 0.0
        )
        # 定義 #17: カフスが**見える**こと。帯が一定なだけでは silhouette に
        # 段差が出ず、幾何としては在るのに絵では見えない。
        # 実物は袖のほうが太く、カフスに絞り込まれるので段差ができる
        above = cuff[0] - 1
        if above >= 0 and lengths[0] > 0:
            result["measured_cuff_gather"] = (
                result["ring_perimeters"][above] / lengths[0]
            )
    return result


def placket_metrics(mesh, design):
    """前立てについて定義 #13 を測る"""
    xs = [point[0] for point in mesh.verts]
    zs = [point[2] for point in mesh.verts]
    return {
        "measured_placket_width": (max(xs) - min(xs)) if xs else None,
        "measured_placket_length": (max(zs) - min(zs)) if zs else None,
        "measured_placket_centred": (abs(max(xs) + min(xs)) < 1e-6) if xs else None,
    }


def _loop_centre(mesh, loop):
    return tuple(
        sum(mesh.verts[index][axis] for index in loop) / len(loop) for axis in range(3)
    )


def _angular_spread(points, cx, cz):
    """点を (x, z) 平面の角度順に並べたときの、間隔の変動係数"""
    if len(points) < 2:
        return 0.0
    order = sorted(math.atan2(point[2] - cz, point[0] - cx) for point in points)
    gaps = [order[index] - order[index - 1] for index in range(1, len(order))]
    gaps.append(order[0] + 2.0 * math.pi - order[-1])
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return 0.0
    variance = sum((gap - mean) ** 2 for gap in gaps) / len(gaps)
    return math.sqrt(variance) / mean


def _button_shells(mesh, design, loops):
    """ボタンを**境界ループから**1個ずつ復元して、個数・高さ・穴を実測する。

    ボタン1個の境界は「外周(分割数)」「中心の小穴(分割数)」「穴 × holes(各4頂点)」。
    大きい輪2本の重心 z がそのボタンの高さで、小さい輪をいちばん近いボタンに
    割り当てれば **1個ごとの穴の数**が出る。

    **高さを design["button_zs"] から取ってはいけない。** それは生成側の申告で、
    メッシュを動かしても気づけない(実際、頂点を間隔の45%ずらしても間隔ゲートが
    通ることを指摘で実証された)。docs/garments.md の罠表4行目と同じ形。

    戻り値: (個数, 高さのリスト, 1個あたりの穴数の最小, 穴間隔CVの最大)
    """
    segments = design.get("button_segments")
    if not segments or segments == 4:
        # 分割数4だと穴の輪(4頂点)と外周の輪が区別できない
        return None, None, None, None
    big = [loop for loop in loops if len(loop) == segments]
    small = [loop for loop in loops if len(loop) == 4]
    if not big:
        return 0, [], None, None

    radius = design.get("button_radius") or 0.0
    tolerance = max(radius * 1.5, 1e-9)

    # 大きい輪の重心 z をボタンごとにまとめる(ボタン間は穴の数十倍離れている)
    clusters = []
    for centre in sorted((_loop_centre(mesh, loop) for loop in big), key=lambda p: -p[2]):
        if clusters and abs(clusters[-1][-1][2] - centre[2]) <= tolerance:
            clusters[-1].append(centre)
        else:
            clusters.append([centre])
    heights = [sum(point[2] for point in group) / len(group) for group in clusters]
    count = len(clusters)
    if not small:
        return count, heights, 0, 0.0

    # 穴をいちばん近いボタンへ割り当てる。**個数の平均で見てはいけない** —
    # 1個に8穴・別の1個に0穴でも平均4で通ってしまう
    buckets = [[] for _ in heights]
    for loop in small:
        centre = _loop_centre(mesh, loop)
        nearest = min(range(len(heights)), key=lambda i: abs(heights[i] - centre[2]))
        buckets[nearest].append(centre)

    spreads = []
    for group, height in zip(buckets, heights):
        if len(group) >= 2:
            cx = sum(point[0] for point in group) / len(group)
            spreads.append(_angular_spread(group, cx, height))
    return (
        count,
        heights,
        min(len(group) for group in buckets),
        max(spreads) if spreads else 0.0,
    )


def _button_y_extents(mesh, design, heights):
    """ボタンごとの y の範囲(裏面 = 最大 y、前面 = 最小 y)をメッシュから復元する。

    各頂点をいちばん近いボタン(高さ)に割り当てる。設計値の申告(button_zs 等)は
    使わない。厚みを**部品全体の y 幅**で測ると、ボタンが表面に沿って前後に
    段差を持つ(ワンピース)だけで「厚い円盤」に化けるので、シェルごとに測る。
    """
    if not heights:
        return None, None
    radius = design.get("button_radius") or 0.0
    tolerance = max(radius * 1.5, 1e-9)
    backs = [None] * len(heights)
    fronts = [None] * len(heights)
    for _x, y, z in mesh.verts:
        nearest = min(range(len(heights)), key=lambda i: abs(heights[i] - z))
        if abs(heights[nearest] - z) > tolerance:
            continue
        if backs[nearest] is None or y > backs[nearest]:
            backs[nearest] = y
        if fronts[nearest] is None or y < fronts[nearest]:
            fronts[nearest] = y
    if any(back is None for back in backs):
        return None, None
    return backs, fronts


def button_metrics(mesh, design):
    """ボタン列について定義 #14 を測る。

    個数は「メッシュの独立したシェルの数」ではなく設計値の申告を使うと恒真になるので、
    **境界ループの本数から数え直す**(ボタン1個につき外周+中心の2本)。
    """
    topo = topology(mesh.verts, mesh.quads)
    loops = boundary_loops(topo["_boundary_edges"])
    xs = [point[0] for point in mesh.verts]
    ys = [point[1] for point in mesh.verts]
    half = design.get("placket_width", 0.0) / 2.0

    # 定義 #15: ボタン**そのものの形**。個数・間隔・位置は形を1つも測っていない。
    # 直径は x の差し渡し(ボタンは x 中心に揃っている)
    diameter = (max(xs) - min(xs)) if xs else 0.0
    count, heights, holes, hole_spread = _button_shells(mesh, design, loops)

    backs, fronts = _button_y_extents(mesh, design, heights)
    # 厚みは**シェルごと**の y 幅の最大。部品全体の y 幅だと、表面に沿って
    # ボタンの前後位置が段差を持つだけで「厚い円盤」に化ける
    if backs and fronts:
        thickness = max(back - front for back, front in zip(backs, fronts))
    else:
        thickness = (max(ys) - min(ys)) if ys else 0.0

    # 高さは**メッシュから**復元したものを使う(設計値の申告ではない)
    zs = sorted(heights or [], reverse=True)
    gaps = [zs[index - 1] - zs[index] for index in range(1, len(zs))]
    mean_gap = sum(gaps) / len(gaps) if gaps else 0.0
    spread = 0.0
    if gaps and mean_gap > 0:
        variance = sum((gap - mean_gap) ** 2 for gap in gaps) / len(gaps)
        spread = math.sqrt(variance) / mean_gap
    # 定義 #19: ボタンが前立ての**表面に沿っている**か。
    # 各シェルの頂点ごとに「その z での表面 y − 頂点 y」(クリアランス)を測り、
    # ボタン1個の**最小クリアランス**を見る。最小が負なら布に沈み、最小が
    # 大きければボタン全体が布から浮いている(全体の最前点から一定 y に置く
    # 旧実装は、前面が後退する胴 = ワンピースで浮いた)。中心1点の離隔で
    # 見ないのは、表面が傾く区間では平らな円盤の中心が正当に離れるため
    float_gap = None
    surface_profile = design.get("placket_front_profile")
    if surface_profile and heights:
        from . import modulate as modulate_module

        radius_tol = max((design.get("button_radius") or 0.0) * 1.5, 1e-9)
        minimums = [None] * len(heights)
        for _x, y, z in mesh.verts:
            nearest = min(range(len(heights)), key=lambda i: abs(heights[i] - z))
            if abs(heights[nearest] - z) > radius_tol:
                continue
            clearance = modulate_module.interp_profile(surface_profile, z) - y
            if minimums[nearest] is None or clearance < minimums[nearest]:
                minimums[nearest] = clearance
        if all(value is not None for value in minimums):
            float_gap = (min(minimums), max(minimums))

    return {
        "measured_button_count": count,
        "measured_button_zs": zs,
        "measured_button_float_gap": float_gap,
        "measured_button_gap_cv": spread,
        "measured_button_gap": mean_gap,
        "measured_button_diameter": diameter,
        "measured_button_thickness": thickness,
        "measured_button_thickness_ratio": (thickness / diameter) if diameter else None,
        "measured_button_holes": holes,
        "measured_button_hole_spacing_cv": hole_spread,
        "buttons_inside_placket": (
            bool(xs) and half > 0 and max(xs) <= half and min(xs) >= -half
        ),
        # z も**メッシュから**復元した値で見る。設計値同士の比較は原理的に落ちない
        "buttons_within_placket_z": (
            bool(zs)
            and max(zs) <= design.get("placket_top_z", float("inf"))
            and min(zs) >= design.get("placket_bottom_z", float("-inf"))
        ),
    }


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
        kind = joint.get("kind", "shared")
        if kind == "sewn":
            # 縫い合わせなので座標の一致は求めない。近くにあることだけ確かめる
            gap = seam_distance(mesh_a, joint["a_ring"], mesh_b, joint["b_ring"])
            tolerance = max(mean_edges[mesh_a.name], mean_edges[mesh_b.name]) * 2.0
        else:
            gap = joint_gap(mesh_a, joint["a_ring"], mesh_b, joint["b_ring"])
            tolerance = max(mean_edges[mesh_a.name], mean_edges[mesh_b.name]) * 1e-3
        joints.append(
            {
                "a": joint["a"],
                "a_ring": joint["a_ring"],
                "b": joint["b"],
                "b_ring": joint["b_ring"],
                "kind": kind,
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

    # 実測の「エッジ長 / 身長 0.0206-0.0311」は**布のパーツ1着ぶんの平均**の値なので、
    # 布のパーツの辺をプールして測る。接合のために本体と周方向分割を共有する
    # ウエストバンドは単体では細かく出るが、それはパーツ単体で判定すべき値ではない。
    #
    # **硬い部品(ボタン)は別勘定。** 参照レンジは Bottoms の布メッシュの実測で、
    # ボタンを含まない。直径11.5mmの部品を混ぜると平均を押し下げて、
    # 布の粗さを見るという指標の意味が消える(実際 0.0199 → 0.0143 まで落ちた)
    trim = {mesh.name for mesh in meshes if mesh.flat_shaded}
    lengths_by_part = {report["part"]: report.pop("_edge_lengths") for report in parts}
    pooled = [
        length
        for name, lengths in lengths_by_part.items()
        if name not in trim
        for length in lengths
    ]
    trim_pooled = [
        length for name, lengths in lengths_by_part.items() if name in trim for length in lengths
    ]
    pooled_stats = _stats(pooled)
    edge_over_h = (pooled_stats["mean"] / height_units) if height_units and pooled else None
    trim_over_h = (
        (_stats(trim_pooled)["mean"] / height_units) if height_units and trim_pooled else None
    )

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
        # 硬い部品(ボタン)の内訳。布の指標とは混ぜない
        "trim_edge_length_over_h": trim_over_h,
        "trim_parts": sorted(trim),
        "parts": parts,
        "joints": joints,
        "part_pairs": crossings,
        "hard": hard,
        "warn": warn,
        "failed": sorted(failed),
        "warned": sorted(warned),
        "verdict": "FAIL" if failed else ("WARN" if warned else "PASS"),
    }
