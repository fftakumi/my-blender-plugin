"""ジオメトリカーネル K1: リング列 → 四角メッシュ(ロフト)。bpy 非依存。

自前で書くのはこれだけ。掃引(リボン・紐)は Blender の Curve + Bevel Object、
回転体(ボタン)は Screw モディファイア、シートの曲げは SimpleDeform に投げる
(それらへ渡す制御点・断面は paths.py が純粋関数で出す)。

UV はここで**解析的に**決める。パラメトリック曲面なので u は周方向の実弧長、
v は軸方向の実弧長で決まり、決定的で pytest で検算できる。
`bpy.ops.uv.smart_project` は EDIT モードを要求し headless で poll が落ちうるうえ、
バージョン間で結果が変わるので既定では使わない。
"""

import math
from dataclasses import dataclass, field

#: UV を [0,1] の内側に収めるための余白
UV_MARGIN = 0.02


@dataclass
class PartMesh:
    """1パーツぶんのメッシュ。bpy を通さずにここまで作り切る。"""

    name: str
    verts: list  # [(x, y, z), ...]
    quads: list  # [(a, b, c, d), ...] 反時計回り = 外向き
    uv_loops: list  # quads と同じ長さ。各要素は [(u, v)] × 4
    rings: dict = field(default_factory=dict)  # {"top": [頂点index...], "bottom": [...]}
    ring_size: int = 0  # 1リングあたりの頂点数(周方向の分割数)
    ring_count: int = 0  # リング本数
    tubular: bool = True  # 筒状(法線の外向き判定を掛けてよい)か
    axis_center: tuple = (0.0, 0.0)  # 筒の中心軸の xy
    #: 設計値(検証がこれと実測を突き合わせる)。単位はシーンの unit
    design: dict = field(default_factory=dict)
    material: str = "main"
    #: 折り目として陰影を割るべき分割位置(プリーツの折り線)。
    #: スムーズシェーディングだけだと浅い折り目がぼやけて「プレスした折り目」に見えない
    sharp_segments: list = field(default_factory=list)
    #: 面を抜いて穴(袖ぐり)を開けたか。開けると頂点を詰め直すので
    #: 「リング番号 × 分割数」で頂点を引く計算が使えなくなる
    holed: bool = False

    def edge_kinds(self):
        """辺を用途で分類して {("ring"|"axial"): [(a, b), ...]} を返す。

        フレアスカートは裾の周方向エッジが腰の2.5倍になるのが正当なので、
        エッジ長は周方向と軸方向を分けて報告する必要がある。
        """
        ring, axial = set(), set()
        for a, b, c, d in self.quads:
            # 四角は (上[j], 下[j], 下[j+1], 上[j+1]) の順に作ってある
            axial.add((min(a, b), max(a, b)))
            axial.add((min(c, d), max(c, d)))
            ring.add((min(b, c), max(b, c)))
            ring.add((min(d, a), max(d, a)))
        return {"ring": sorted(ring), "axial": sorted(axial)}


def _distance(p, q):
    return math.sqrt(
        (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
    )


def _ring_arc_lengths(points, closed):
    """リング上の累積弧長。closed なら最後に一周ぶんを足した len+1 個を返す"""
    lengths = [0.0]
    count = len(points)
    steps = count if closed else count - 1
    for index in range(steps):
        lengths.append(lengths[-1] + _distance(points[index], points[(index + 1) % count]))
    return lengths


def orthonormal_frame(direction):
    """筒を伸ばす向きから、リングを張る平面の2軸 (right, up) を返す。

    `cross(right, up) == -direction` になるように選ぶ。loft_rings は
    「反時計回りのリングを積む向きの逆」に面法線が向くので、この関係を守れば
    どの向きの筒でも法線が外を向く。
    """
    length = math.sqrt(sum(value * value for value in direction))
    if length == 0.0:
        raise ValueError("direction が零ベクトルです")
    forward = tuple(value / length for value in direction)
    # direction と平行でない軸を選んで右手系を作る
    helper = (0.0, 0.0, 1.0) if abs(forward[2]) < 0.9 else (1.0, 0.0, 0.0)
    right = (
        helper[1] * forward[2] - helper[2] * forward[1],
        helper[2] * forward[0] - helper[0] * forward[2],
        helper[0] * forward[1] - helper[1] * forward[0],
    )
    scale = math.sqrt(sum(value * value for value in right))
    right = tuple(value / scale for value in right)
    backward = tuple(-value for value in forward)
    up = (
        backward[1] * right[2] - backward[2] * right[1],
        backward[2] * right[0] - backward[0] * right[2],
        backward[0] * right[1] - backward[1] * right[0],
    )
    return right, up


def ring_on_frame(radii, angles, origin, right, up, depth_ratio=1.0):
    """任意の平面(origin, right, up)上にリング1本を張る。袖のような斜めの筒に使う"""
    if len(radii) != len(angles):
        raise ValueError("radii(%d) と angles(%d) の個数が違います" % (len(radii), len(angles)))
    if depth_ratio <= 0.0:
        raise ValueError("depth_ratio は正の数にしてください: %r" % (depth_ratio,))
    points = []
    for radius, angle in zip(radii, angles):
        u = radius * math.cos(angle)
        v = radius * depth_ratio * math.sin(angle)
        points.append(
            tuple(origin[axis] + right[axis] * u + up[axis] * v for axis in range(3))
        )
    return points


def loft_rings(
    ring_points,
    name="Part",
    closed=True,
    tubular=True,
    axis_center=(0.0, 0.0),
    skip_faces=(),
):
    """リング列を積んで四角メッシュにする。

    ring_points: 上から下へ並べたリングのリスト。各リングは同じ頂点数の
                 [(x, y, z), ...]。頂点は +Z から見て反時計回りに並べる。
    closed:      リングが周方向に閉じるか(筒なら True、シートなら False)。
    skip_faces:  作らない面の (リング番号, 分割番号) の集合。**袖ぐりのような穴**を
                 これで開ける。格子から矩形の面群を抜くと、境界がきれいな四角の輪になる。

    面の巻き方向は (上[j], 下[j], 下[j+1], 上[j+1])。反時計回りに並べたリングを
    上から下へ積むと、この順で面法線が軸から外を向く。
    """
    if len(ring_points) < 2:
        raise ValueError("リングは2本以上必要です: %d" % len(ring_points))
    ring_size = len(ring_points[0])
    if ring_size < (3 if closed else 2):
        raise ValueError("1リングの頂点数が足りません: %d" % ring_size)
    for index, ring in enumerate(ring_points):
        if len(ring) != ring_size:
            raise ValueError(
                "リングごとに頂点数が違います(ring[0]=%d, ring[%d]=%d)"
                % (ring_size, index, len(ring))
            )

    verts = [tuple(float(value) for value in point) for ring in ring_points for point in ring]

    ring_count = len(ring_points)
    span = ring_size if closed else ring_size - 1
    skip = set(tuple(item) for item in skip_faces)
    quads = []
    uv_all = _analytic_uv(ring_points, closed, span, ring_size)
    uv_loops = []
    for upper in range(ring_count - 1):
        top_base = upper * ring_size
        bottom_base = (upper + 1) * ring_size
        for j in range(span):
            if (upper, j) in skip:
                continue
            j2 = (j + 1) % ring_size
            quads.append((top_base + j, bottom_base + j, bottom_base + j2, top_base + j2))
            uv_loops.append(uv_all[upper * span + j])

    # 穴を開けると参照されない頂点が残る。孤立頂点は検証で不合格になるので詰め直す
    if skip:
        used = sorted({index for quad in quads for index in quad})
        remap = {old: new for new, old in enumerate(used)}
        verts = [verts[index] for index in used]
        quads = [tuple(remap[index] for index in quad) for quad in quads]
    else:
        remap = None

    def ring_slice(index):
        raw = range(index * ring_size, (index + 1) * ring_size)
        if remap is None:
            return list(raw)
        return [remap[value] for value in raw if value in remap]

    return PartMesh(
        name=name,
        verts=verts,
        quads=quads,
        uv_loops=uv_loops,
        rings={"top": ring_slice(0), "bottom": ring_slice(ring_count - 1)},
        ring_size=ring_size,
        ring_count=ring_count,
        tubular=tubular,
        axis_center=(float(axis_center[0]), float(axis_center[1])),
        holed=bool(skip),
    )


def _analytic_uv(ring_points, closed, span, ring_size):
    """実弧長にもとづく UV。u と v を同じ倍率で縮めるのでテクセル密度が揃う。

    各リングは自分の実長で展開し、全体の最大長で正規化して中央揃えにする
    (行ごとに 0-1 へ引き伸ばすとプリーツで密度が壊れる)。
    """
    arc = [_ring_arc_lengths(ring, closed) for ring in ring_points]
    max_ring_length = max(lengths[-1] for lengths in arc)

    # 軸方向は「対応する頂点同士の距離の平均」を段ごとに積む
    axial = [0.0]
    for upper in range(len(ring_points) - 1):
        step = sum(
            _distance(ring_points[upper][j], ring_points[upper + 1][j]) for j in range(ring_size)
        ) / ring_size
        axial.append(axial[-1] + step)
    axial_total = axial[-1]

    scale_base = max(max_ring_length, axial_total)
    scale = (1.0 - 2.0 * UV_MARGIN) / scale_base if scale_base > 0 else 0.0

    def uv(ring_index, j):
        lengths = arc[ring_index]
        half = lengths[-1] * 0.5
        return (
            0.5 + (lengths[j] - half) * scale,
            UV_MARGIN + (axial_total - axial[ring_index]) * scale,
        )

    uv_loops = []
    for upper in range(len(ring_points) - 1):
        for j in range(span):
            # 継ぎ目の四角では j+1 を「一周ぶん」として扱う(0 に戻さない)
            j2 = j + 1
            uv_loops.append(
                [uv(upper, j), uv(upper + 1, j), uv(upper + 1, j2), uv(upper, j2)]
            )
    return uv_loops


def ring_from_polar(radii, angles, z, center=(0.0, 0.0), depth_ratio=1.0):
    """リング1本を (x, y, z) のリストにする。

    depth_ratio は「前後の厚み / 左右の幅」。1.0 なら円、それ未満なら y 方向に
    潰れた楕円になる。人体の胴の断面は円ではないので、既定の衣装はここを 1 未満にする。

    z は数値でも、**分割ごとの列**でもよい。列を渡すと水平でないリングになる
    (襟ぐりの前下がりがこれ。水平な輪にすると襟が煙突に見える)。
    """
    if len(radii) != len(angles):
        raise ValueError("radii(%d) と angles(%d) の個数が違います" % (len(radii), len(angles)))
    if depth_ratio <= 0.0:
        raise ValueError("depth_ratio は正の数にしてください: %r" % (depth_ratio,))
    if isinstance(z, (list, tuple)):
        if len(z) != len(angles):
            raise ValueError("z(%d) と angles(%d) の個数が違います" % (len(z), len(angles)))
        zs = list(z)
    else:
        zs = [z] * len(angles)
    cx, cy = center
    return [
        (
            cx + radius * math.cos(angle),
            cy + radius * depth_ratio * math.sin(angle),
            height,
        )
        for radius, angle, height in zip(radii, angles, zs)
    ]
