"""曲率エッジのテッセレーションと等弧長リサンプル(bpy 非依存)。

`mathutils.Vector` は使わない。2D 点はすべて素の `(x, y)` タプルで扱う
(フェイク bpy のテスト環境に mathutils が無いため)。

GarmentCode の `properties.curvature_coords == "relative"` のとき、制御点は
**エッジ座標系** で与えられる:

    p = start + u * e + v * perp(e),   perp(e) = (-e_y, e_x)

u はエッジ方向の正規化パラメータ、v は左手側への法線方向。
`e` は正規化していないので、u も v も **|e| 倍された長さ** になる。
"""

import math

# 曲率エッジの下見サンプル数。この後で等弧長リサンプルするので粗くてよい
CURVE_SAMPLES = 256


def perp(vector):
    """2D ベクトルを反時計回りに 90 度回す(エッジ座標系の v 軸)"""
    return (-vector[1], vector[0])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def length(vector):
    return math.hypot(vector[0], vector[1])


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def edge_frame_point(p0, e, u, v):
    """エッジ座標系 (u, v) の点をパネル座標へ写す"""
    pe = perp(e)
    return (p0[0] + u * e[0] + v * pe[0], p0[1] + u * e[1] + v * pe[1])


def de_casteljau(control, t):
    """ベジエ曲線の de Casteljau 評価。control は 2D 点のリスト"""
    points = list(control)
    while len(points) > 1:
        points = [lerp(points[i], points[i + 1], t) for i in range(len(points) - 1)]
    return points[0]


def circle_control_y(rel_rad, large_arc, right):
    """SVG 風の circle 曲率パラメータから、エッジ座標系での中点高さ cy を復元する。

    GarmentCode は「エッジの中点を (0.5, cy) に置いた円弧」で曲率を表す。
    そのとき  rel_rad = (0.25 + cy^2) / (2*|cy|)  が成り立つので、これを逆に解く。

        cy^2 - 2*rel_rad*|cy| + 0.25 = 0  ->  |cy| = rel_rad ± sqrt(rel_rad^2 - 0.25)

    large_arc=1 のとき大きい方の根、right=1 のとき符号が負。
    """
    disc = rel_rad * rel_rad - 0.25
    if disc < 0.0:
        disc = 0.0  # 数値誤差で僅かに負になることがある(半径 < 弦長/2 は本来不能)
    root = math.sqrt(disc)
    low, high = rel_rad - root, rel_rad + root
    control_y = high if large_arc else low
    if control_y <= 1e-12:
        control_y = 1e-12
    return -control_y if right else control_y


def circle_arc(p0, p1, params):
    """circle 曲率の弧を {"center", "radius", "start_angle", "sweep"} で返す純粋関数

    center はパネル座標、radius はパネル座標での半径(= spec の radius に一致する)。
    角度はエッジ座標系のもので、`arc_point` と組で使う。
    """
    e = sub(p1, p0)
    chord = length(e)
    if chord < 1e-12:
        raise ValueError("長さ 0 のエッジに円弧の曲率は付けられません")
    radius, large_arc, right = params[0], int(params[1]), int(params[2])
    control_y = circle_control_y(radius / chord, large_arc, right)

    # エッジ座標系の円: (0,0)・(1,0)・(0.5, control_y) を通る
    center_v = (control_y * control_y - 0.25) / (2.0 * control_y)
    rel_radius = (0.25 + control_y * control_y) / (2.0 * abs(control_y))
    a0 = math.atan2(-center_v, -0.5)
    a1 = math.atan2(-center_v, 0.5)
    am = math.atan2(control_y - center_v, 0.0)

    sweep = _wrap_angle(a1 - a0)
    mid = _wrap_angle(am - a0)
    # 中点を通らない向きになっていたら反対回りにする(large_arc の判定込み)
    if (mid > 0.0) != (sweep > 0.0) or abs(mid) > abs(sweep):
        sweep -= math.copysign(2.0 * math.pi, sweep)

    return {
        "center": edge_frame_point(p0, e, 0.5, center_v),
        "radius": rel_radius * chord,
        "start_angle": a0,
        "sweep": sweep,
        # arc_point 用にエッジ座標系そのものを持ち回る
        "origin": tuple(p0),
        "edge": e,
        "center_v": center_v,
        "rel_radius": rel_radius,
    }


def arc_point(arc, t):
    """弧を [0, 1] のパラメータで(弧の角度について等間隔に)サンプルする"""
    angle = arc["start_angle"] + arc["sweep"] * t
    u = 0.5 + arc["rel_radius"] * math.cos(angle)
    v = arc["center_v"] + arc["rel_radius"] * math.sin(angle)
    return edge_frame_point(arc["origin"], arc["edge"], u, v)


def _wrap_angle(angle):
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    while angle > math.pi:
        angle -= 2.0 * math.pi
    return angle


def bezier_control_points(p0, p1, params):
    """quadratic / cubic の制御点(始点・終点を含む)をパネル座標で返す純粋関数"""
    e = sub(p1, p0)
    return [tuple(p0)] + [edge_frame_point(p0, e, u, v) for (u, v) in params] + [tuple(p1)]


def sample_curved_edge(p0, p1, curvature, samples=CURVE_SAMPLES):
    """エッジを密にサンプリングした 2D 点列(始点・終点を含む)を返す純粋関数

    curvature は `spec.parse_spec` が正規化した {"type", "params"}(無曲率は None)。
    """
    p0 = tuple(p0)
    p1 = tuple(p1)
    if not curvature:
        return [p0, p1]
    if samples < 2:
        raise ValueError("samples は 2 以上で指定してください")
    if distance(p0, p1) < 1e-12:
        return [p0, p1]

    ctype = curvature["type"]
    params = curvature["params"]

    if ctype in ("quadratic", "cubic"):
        control = bezier_control_points(p0, p1, params)
        return [de_casteljau(control, i / (samples - 1)) for i in range(samples)]

    if ctype == "circle":
        arc = circle_arc(p0, p1, params)
        return [arc_point(arc, i / (samples - 1)) for i in range(samples)]

    raise ValueError("未対応の曲率タイプです: %r" % (ctype,))


def polyline_length(points):
    """点列の総弧長を返す純粋関数"""
    return sum(distance(points[i], points[i + 1]) for i in range(len(points) - 1))


def resample_polyline(points, target_len):
    """点列を等弧長で target_len 刻みに貼り直す。始点・終点は必ず残す。

    返り値: (点列, 元の総弧長)。刻み数は round(総弧長 / target_len) で決めるので、
    実際の刻みは target_len ちょうどではなく総弧長を割り切る値になる
    (刻みは全区間で等しい = 均一)。
    """
    if target_len <= 0.0:
        raise ValueError("target_len は正の数で指定してください")
    if len(points) < 2:
        raise ValueError("点が2つ以上必要です")

    segments = [distance(points[i], points[i + 1]) for i in range(len(points) - 1)]
    total = sum(segments)
    if total < 1e-9:
        return [tuple(points[0]), tuple(points[-1])], total

    count = max(1, int(round(total / target_len)))
    step = total / count
    out = [tuple(points[0])]
    index = 0
    carried = 0.0
    for k in range(1, count):
        want = k * step
        while index < len(segments) and carried + segments[index] < want:
            carried += segments[index]
            index += 1
        if index >= len(segments):
            break
        t = (want - carried) / segments[index] if segments[index] > 1e-12 else 0.0
        out.append(lerp(points[index], points[index + 1], t))
    out.append(tuple(points[-1]))
    return out, total
