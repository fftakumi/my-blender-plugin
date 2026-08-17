"""パネル1枚を三角形分割に掛ける直前まで組み立てる(bpy 非依存)。

三角形分割そのもの(`mathutils.geometry.delaunay_2d_cdt`)は mathutils にあるので
bpy 側(`build.py`)の仕事。ここでやるのはその **入力** を作るところまで:

- エッジ列を端点でつないで外形の巡回順にする(spec のエッジ順は連結順とは限らない)
- 曲率をテッセレーションして目標エッジ長で等弧長リサンプルする
- 外形の内側に六方格子で内部点を撒く
- 各エッジ上の境界頂点列(chain)を **spec のエッジ向き (a -> b)** に揃えて返す

chain はステッチの相手合わせに使うので、向きが揃っていないと縫合がねじれる。
"""

import bisect
import math

from . import curves

# 六方格子の行間 = target_edge * sqrt(3)/2
HEX_ROW_RATIO = math.sqrt(3.0) / 2.0
# 内部点を境界から離す距離 = target_edge * INTERIOR_MARGIN
INTERIOR_MARGIN = 0.62
# パネル内でのみ使う重複頂点マージ距離 [cm]。全体マージは厳禁(下の注意書き)
MERGE_DIST = 0.001


def order_edge_loop(edges):
    """edges を端点でつないだ巡回順 [(エッジ添字, 逆走フラグ), ...] にする純粋関数

    辿り切れなかったら spec のファイル順をそのまま信じる(第2要素 False)。
    """
    count = len(edges)
    adjacency = {}
    for index, edge in enumerate(edges):
        a, b = edge["endpoints"]
        adjacency.setdefault(a, []).append(index)
        adjacency.setdefault(b, []).append(index)

    used = [False] * count
    order = [(0, False)]
    used[0] = True
    node = edges[0]["endpoints"][1]
    while len(order) < count:
        following = None
        for candidate in adjacency.get(node, ()):
            if not used[candidate]:
                following = candidate
                break
        if following is None:
            return [(index, False) for index in range(count)]
        a, b = edges[following]["endpoints"]
        reversed_flag = b == node
        order.append((following, reversed_flag))
        used[following] = True
        node = a if reversed_flag else b
    return order


def panel_boundary(panel, target_edge):
    """パネルの外形を巡回して (境界点列, {エッジ添字: 頂点添字列}) を返す純粋関数"""
    if target_edge <= 0.0:
        raise ValueError("target_edge は正の数で指定してください")
    vertices = panel["vertices"]
    edges = panel["edges"]

    boundary = []
    walk = []
    for edge_index, reversed_flag in order_edge_loop(edges):
        a, b = edges[edge_index]["endpoints"]
        # 曲率は spec に書かれた向き (a -> b) で定義されているので、必ず
        # その向きでサンプリングしてから、逆走なら点列を反転する。
        dense = curves.sample_curved_edge(
            vertices[a], vertices[b], edges[edge_index]["curvature"]
        )
        if reversed_flag:
            dense = list(reversed(dense))
        points, _total = curves.resample_polyline(dense, target_edge)
        start = len(boundary)
        boundary.extend(points[:-1])  # 終点は次エッジの始点なので重複させない
        walk.append((edge_index, reversed_flag, list(range(start, len(boundary)))))

    # 各エッジの chain に「次のエッジの始点」を終端として足し、
    # 逆走したエッジは反転して常に spec のエッジ向き (a -> b) に揃える。
    chains = {}
    for position, (edge_index, reversed_flag, indices) in enumerate(walk):
        next_first = walk[(position + 1) % len(walk)][2][0]
        chain = indices + [next_first]
        chains[edge_index] = list(reversed(chain)) if reversed_flag else chain
    return boundary, chains


def point_in_polygon(x, y, polygon):
    """交差数判定。polygon は閉じた 2D 点の巡回列(最後と最初は自動でつなぐ)"""
    inside = False
    count = len(polygon)
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            crossing = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < crossing:
                inside = not inside
        j = i
    return inside


def distance_to_polygon(x, y, polygon):
    """点から多角形の辺までの最短距離を返す純粋関数"""
    best = float("inf")
    count = len(polygon)
    for i in range(count):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % count]
        dx, dy = bx - ax, by - ay
        squared = dx * dx + dy * dy
        if squared < 1e-18:
            found = math.hypot(x - ax, y - ay)
        else:
            t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / squared))
            found = math.hypot(x - (ax + dx * t), y - (ay + dy * t))
        if found < best:
            best = found
    return best


def polygon_area(polygon):
    """多角形の面積(靴紐公式・符号なし)を返す純粋関数"""
    total = 0.0
    count = len(polygon)
    for i in range(count):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % count]
        total += ax * by - bx * ay
    return abs(total) * 0.5


def row_crossings(y, polygon):
    """走査線 y と外形の交点 x を昇順で返す純粋関数(`point_in_polygon` と同じ判定則)

    閉じた多角形なら交点数は必ず偶数なので、「x より大きい交点が奇数個 = 内側」は
    「x 以下の交点が奇数個」と同値。行ごとに1回だけ作れば全格子点で使い回せる。
    """
    crossings = []
    count = len(polygon)
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            crossings.append((xj - xi) * (y - yi) / (yj - yi) + xi)
        j = i
    crossings.sort()
    return crossings


def segment_grid(polygon, cell):
    """外形の辺を一様格子へ振り分ける純粋関数。{(cx, cy): [辺の添字, ...]}

    辺の bounding box が触るセルすべてに入れる(保守的なので取りこぼさない)。
    """
    grid = {}
    count = len(polygon)
    for i in range(count):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % count]
        x0 = int(math.floor(min(ax, bx) / cell))
        x1 = int(math.floor(max(ax, bx) / cell))
        y0 = int(math.floor(min(ay, by) / cell))
        y1 = int(math.floor(max(ay, by) / cell))
        for cx in range(x0, x1 + 1):
            for cy in range(y0, y1 + 1):
                grid.setdefault((cx, cy), []).append(i)
    return grid


def near_polygon(x, y, polygon, grid, cell, margin):
    """点が外形の辺から margin 以内かを返す純粋関数

    `distance_to_polygon(...) <= margin` と同値だが、全辺を見ずに済ませる。
    セル幅 >= margin なら、margin 以内の辺は必ず 3x3 近傍のどれかに登録されている。
    """
    count = len(polygon)
    base_x = int(math.floor(x / cell))
    base_y = int(math.floor(y / cell))
    for offset_x in (-1, 0, 1):
        for offset_y in (-1, 0, 1):
            for i in grid.get((base_x + offset_x, base_y + offset_y), ()):
                ax, ay = polygon[i]
                bx, by = polygon[(i + 1) % count]
                dx, dy = bx - ax, by - ay
                squared = dx * dx + dy * dy
                if squared < 1e-18:
                    found = math.hypot(x - ax, y - ay)
                else:
                    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / squared))
                    found = math.hypot(x - (ax + dx * t), y - (ay + dy * t))
                if found <= margin:
                    return True
    return False


def hex_interior_points(boundary, target_edge):
    """外形の内側に六方格子の内部点を撒いて返す純粋関数

    境界から `target_edge * INTERIOR_MARGIN` 以内には置かない
    (境界のリサンプル点と潰れた三角形を作らないため)。

    ★ 素朴に「全格子点 x 全辺」を回すと概ね O(1/target_edge^3) になり、
      target_edge をオペレーターの下限 0.2cm まで下げると同期の execute() の中で
      分単位に膨れて Blender が固まったように見える(実測: 5パネルで
      1.5cm→0.03秒 / 0.5cm→0.77秒 / 0.2cm→12.3秒)。内外判定は行ごとの走査線、
      境界からの距離は辺の格子で近傍だけに絞って、辺の本数から切り離す。
    """
    if target_edge <= 0.0:
        raise ValueError("target_edge は正の数で指定してください")
    xs = [point[0] for point in boundary]
    ys = [point[1] for point in boundary]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    dx = target_edge
    dy = target_edge * HEX_ROW_RATIO
    margin = target_edge * INTERIOR_MARGIN
    grid = segment_grid(boundary, margin)

    interior = []
    row = 0
    y = y0 + dy * 0.5
    while y < y1:
        crossings = row_crossings(y, boundary)
        x = x0 + (0.0 if row % 2 == 0 else dx * 0.5) + dx * 0.5
        while x < x1:
            if bisect.bisect_right(crossings, x) % 2 == 1 and not near_polygon(
                x, y, boundary, grid, margin, margin
            ):
                interior.append((x, y))
            x += dx
        y += dy
        row += 1
    return interior


def panel_inputs(panel, target_edge):
    """1パネルぶんの CDT 入力を返す純粋関数

    返り値 dict:
      points          : [(x, y), ...]  境界点 + 内部点(境界が先)
      boundary_count  : 境界点の数
      constraints     : [(i, j), ...]  外形の拘束エッジ
      chains          : {エッジ添字: [頂点添字, ...]}
      area            : 外形の面積(三角形分割の検算用)
    """
    boundary, chains = panel_boundary(panel, target_edge)
    interior = hex_interior_points(boundary, target_edge)
    count = len(boundary)
    return {
        "points": boundary + interior,
        "boundary_count": count,
        "constraints": [(i, (i + 1) % count) for i in range(count)],
        "chains": chains,
        "area": polygon_area(boundary),
    }


def merge_close_points(points, faces, chains, distance):
    """**パネル内だけ** の重複頂点マージ。(点列, 面, chains, 消した数) を返す純粋関数

    ★ 全体(結合後)でのマージは絶対にやってはいけない。
      前中心・後中心のパネル対は spec 上まったく同じ座標を共有しているため、
      0.001 cm の全体マージでも別パネル同士が溶接されてしまい、
      面コンポーネントが 16 -> 12 に減る(参照実装での実測)。
      なお、パネル単位で走らせた場合は重複 0 個だった(= 実質ノーオペ)。
    """
    cell = max(distance, 1e-9) * 2.0
    grid = {}
    remap = list(range(len(points)))
    keep = []
    new_index = {}
    for index, point in enumerate(points):
        key = (int(math.floor(point[0] / cell)), int(math.floor(point[1] / cell)))
        found = -1
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for other in grid.get((key[0] + offset_x, key[1] + offset_y), ()):
                    if curves.distance(points[other], point) <= distance:
                        found = other
                        break
                if found >= 0:
                    break
            if found >= 0:
                break
        if found >= 0:
            remap[index] = new_index[found]
        else:
            grid.setdefault(key, []).append(index)
            new_index[index] = len(keep)
            keep.append(point)
            remap[index] = new_index[index]

    removed = len(points) - len(keep)
    if removed == 0:
        return points, faces, chains, 0
    merged_faces = []
    for face in faces:
        remapped = [remap[vertex] for vertex in face]
        if len(set(remapped)) == 3:
            merged_faces.append(tuple(remapped))
    merged_chains = {
        key: [remap[vertex] for vertex in chain] for key, chain in chains.items()
    }
    return keep, merged_faces, merged_chains, removed


def triangle_area_sum(points, faces):
    """三角形の面積和を返す純粋関数(外形面積との突き合わせに使う)"""
    total = 0.0
    for i, j, k in faces:
        ax, ay = points[i]
        bx, by = points[j]
        cx, cy = points[k]
        total += abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) * 0.5
    return total
