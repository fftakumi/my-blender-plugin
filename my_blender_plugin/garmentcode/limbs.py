"""袖・カフなど筒状パーツの腕軸への自動センタリング(bpy 非依存)。

★ この取り込み器でいちばん重要な処理。

失敗の歴史(参照実装が3回のシミュレーション失敗で突き止めたもの):

    袖パネルを「腕をよけるまで上に持ち上げる」だけでは駄目。縫合スプリングが
    筒を閉じるとき、筒は **自分自身の重心** に向かって収束するので、腕が筒の外に
    残り、袖がずり落ちる。カフの |x| が 55 -> 38 -> 21 と移動していったのがその証拠。

正解:

    袖・カフの各コンポーネントを、腕の軸に対して **軸と直交する2方向の両方で**
    中心を合わせる。参照データでの実測オフセットは 袖 Y+6.35 / Z+13.11、
    カフ Y+6.78 / Z+3.20。センタリング後は 453 本の縫い目がすべて閉じ(最大 0.99cm)、
    どの断面でも腕が筒の内側にあった。

数値はハードコードしない。毎回、素体メッシュの断面中心を測り直して決める
(`measure_axis_center` に渡す点列は bpy 側が素体の評価済みメッシュから取る。
このモジュール自体は素の 3D タプルしか知らない)。
"""

from . import topology

# spec の panel["label"] がこれなら手足候補
LIMB_PANEL_LABELS = ("arm",)
# 名前にこれを含んでも手足候補。パンツの脚も揃えたいなら "pant" / "leg" を足す(未検証)
LIMB_NAME_KEYWORDS = ("sleeve", "cuff", "arm")

# 軸方向スパンのうち「体から遠い側」何割で腕軸を測るか
MEASURE_OUTER_FRAC = 0.5
# 測定スラブ数の上限(短い区間では自動的に減る)
MEASURE_SLABS = 8
# 1スラブの最小長 [cm]
MEASURE_SLAB_MIN_LEN = 1.5
# スラブを採用する最小サンプル点数
MEASURE_SLAB_MIN_POINTS = 6
# これを超えるオフセットは異常とみなして適用しない [cm]
MAX_OFFSET = 40.0
# 最長辺 / 2番目の辺 がこれ未満なら筒とみなさない
MIN_ELONGATION = 1.15

AXIS_NAMES = "XYZ"


def _name_tokens(name):
    """パネル名を区切り文字で語に割る。部分一致だと garment / warm が "arm" に化ける"""
    lowered = name.lower()
    for separator in "-.":
        lowered = lowered.replace(separator, "_")
    return set(lowered.split("_"))


def is_limb_panel(name, panel, labels=LIMB_PANEL_LABELS, keywords=LIMB_NAME_KEYWORDS):
    """パネルが手足(筒)候補かどうかを返す純粋関数"""
    if panel.get("label") in labels:
        return True
    tokens = _name_tokens(name)
    return any(keyword in tokens for keyword in keywords)


def garment_centroid(verts):
    """全頂点の重心を返す純粋関数(体に近い側/遠い側の判定に使う)"""
    if not verts:
        return (0.0, 0.0, 0.0)
    count = float(len(verts))
    return tuple(sum(vertex[axis] for vertex in verts) / count for axis in range(3))


def _cluster_indices(names, panel_ranges):
    indices = []
    for name in names:
        start, end = panel_ranges[name]
        indices.extend(range(start, end))
    return indices


def detect_limb_clusters(panels, stitches, panel_ranges, verts):
    """手足パネルを『縫合で繋がった塊』->『軸方向に重なる塊』の順に分解する純粋関数

    袖とカフは縫合で繋がっているが軸方向には別の区間を占めるので分かれる。
    実測でも袖とカフには別々のオフセットが必要だった。

    返り値: [{"panels": [名前, ...], "axis": 0|1|2, "a_lo": float, "a_hi": float}, ...]
    """
    limbs = [name for name, panel in panels.items() if is_limb_panel(name, panel)]
    limbs = [name for name in limbs if name in panel_ranges]
    if not limbs:
        return []

    union = topology.UnionFind(limbs)
    limb_set = set(limbs)
    for side_a, side_b in stitches:
        if side_a["panel"] in limb_set and side_b["panel"] in limb_set:
            union.union(side_a["panel"], side_b["panel"])
    components = {}
    for name in limbs:
        components.setdefault(union.find(name), []).append(name)

    clusters = []
    for _root, names in sorted(components.items()):
        names = sorted(names)
        points = [verts[index] for index in _cluster_indices(names, panel_ranges)]
        if not points:
            continue
        size = [
            max(point[axis] for point in points) - min(point[axis] for point in points)
            for axis in range(3)
        ]
        ranked = sorted(range(3), key=lambda axis: size[axis], reverse=True)
        axis, second = ranked[0], ranked[1]
        if size[second] > 1e-6 and size[axis] / size[second] < MIN_ELONGATION:
            # 筒ではない(ほぼ等方な塊)ので触らない
            continue

        # 軸方向のインターバルが重なるパネル同士を1クラスタにまとめる
        intervals = []
        for name in names:
            start, end = panel_ranges[name]
            values = [verts[index][axis] for index in range(start, end)]
            intervals.append([min(values), max(values), [name]])
        intervals.sort(key=lambda item: item[0])
        merged = [intervals[0]]
        for interval in intervals[1:]:
            if interval[0] <= merged[-1][1] + 1e-6:
                merged[-1][1] = max(merged[-1][1], interval[1])
                merged[-1][2].extend(interval[2])
            else:
                merged.append(interval)
        for low, high, group in merged:
            clusters.append(
                {"panels": sorted(group), "axis": axis, "a_lo": low, "a_hi": high}
            )
    return clusters


def measure_axis_center(points, axis, a_lo, a_hi, outward_sign):
    """素体の断面中心(軸に直交する2成分)を測る純粋関数

    体に近い側は胴体が混ざって中心がずれるので、**体から遠い側** の
    MEASURE_OUTER_FRAC の区間だけを使う。各スラブで中央値を取り、
    スラブ間で平均する(外れ値・指先などに強い)。

    測れなければ None。
    """
    if not points:
        return None
    span = a_hi - a_lo
    if span <= 1e-6:
        return None
    fraction = max(0.05, min(1.0, MEASURE_OUTER_FRAC))
    if outward_sign < 0:
        window_lo, window_hi = a_lo, a_lo + span * fraction
    else:
        window_lo, window_hi = a_hi - span * fraction, a_hi

    other1, other2 = [index for index in range(3) if index != axis]
    # 区間が短いときはスラブを減らす(細かく割りすぎると各スラブが空になる)
    slab_count = max(
        1,
        min(
            MEASURE_SLABS,
            int((window_hi - window_lo) / max(1e-6, MEASURE_SLAB_MIN_LEN)),
        ),
    )
    slabs = [[] for _ in range(slab_count)]
    step = (window_hi - window_lo) / slab_count
    for point in points:
        along = point[axis]
        if along < window_lo or along > window_hi:
            continue
        slot = min(slab_count - 1, int((along - window_lo) / step)) if step > 1e-9 else 0
        slabs[slot].append(point)

    centers1, centers2, radii, used = [], [], [], 0
    for slab in slabs:
        if len(slab) < MEASURE_SLAB_MIN_POINTS:
            continue
        used += 1
        values1 = sorted(point[other1] for point in slab)
        values2 = sorted(point[other2] for point in slab)
        centers1.append(values1[len(values1) // 2])
        centers2.append(values2[len(values2) // 2])
        radii.append(0.5 * max(values1[-1] - values1[0], values2[-1] - values2[0]))
    if used == 0:
        return None
    return {
        "axis": axis,
        "other1": other1,
        "other2": other2,
        "center1": sum(centers1) / used,
        "center2": sum(centers2) / used,
        "slabs_used": used,
        "radius": sum(radii) / used,
        "window": (window_lo, window_hi),
    }


def limb_offsets(clusters, panel_ranges, verts, body_points, garment_center):
    """各クラスタの並進量を求める純粋関数(頂点は動かさない)

    返り値は報告用のエントリのリスト。適用するものは "applied" が True で
    "delta" に (dx, dy, dz) が入る。
    """
    report = []
    for cluster in clusters:
        axis = cluster["axis"]
        names = cluster["panels"]
        indices = _cluster_indices(names, panel_ranges)
        points = [verts[index] for index in indices]
        a_lo, a_hi = cluster["a_lo"], cluster["a_hi"]
        outward = 1.0 if 0.5 * (a_lo + a_hi) >= garment_center[axis] else -1.0

        entry = {
            "panels": names,
            "axis": AXIS_NAMES[axis],
            "span": (a_lo, a_hi),
            "applied": False,
            "delta": (0.0, 0.0, 0.0),
        }
        measured = measure_axis_center(body_points, axis, a_lo, a_hi, outward)
        if measured is None:
            entry["note"] = "測定窓に素体の点が入りませんでした"
            report.append(entry)
            continue

        other1, other2 = measured["other1"], measured["other2"]
        current1 = 0.5 * (
            min(point[other1] for point in points) + max(point[other1] for point in points)
        )
        current2 = 0.5 * (
            min(point[other2] for point in points) + max(point[other2] for point in points)
        )
        delta1 = measured["center1"] - current1
        delta2 = measured["center2"] - current2
        entry.update(
            {
                "offset": {AXIS_NAMES[other1]: delta1, AXIS_NAMES[other2]: delta2},
                "target": {
                    AXIS_NAMES[other1]: measured["center1"],
                    AXIS_NAMES[other2]: measured["center2"],
                },
                "current": {AXIS_NAMES[other1]: current1, AXIS_NAMES[other2]: current2},
                "slabs": measured["slabs_used"],
                "limb_radius": measured["radius"],
                "window": measured["window"],
            }
        )
        if max(abs(delta1), abs(delta2)) > MAX_OFFSET:
            entry["note"] = "オフセットが上限 %.1fcm を超えたので適用しません" % MAX_OFFSET
            report.append(entry)
            continue

        delta = [0.0, 0.0, 0.0]
        delta[other1] = delta1
        delta[other2] = delta2
        entry["delta"] = tuple(delta)
        entry["applied"] = True
        report.append(entry)
    return report


def apply_offsets(verts, panel_ranges, report):
    """報告どおりに頂点を並進させた **新しい** 点列を返す純粋関数"""
    moved = list(verts)
    for entry in report:
        if not entry["applied"]:
            continue
        dx, dy, dz = entry["delta"]
        for index in _cluster_indices(entry["panels"], panel_ranges):
            x, y, z = moved[index]
            moved[index] = (x + dx, y + dy, z + dz)
    return moved


def center_limbs(panels, stitches, panel_ranges, verts, body_points):
    """クラスタ検出 -> オフセット計算 -> 適用 をまとめた純粋関数。(点列, 報告) を返す"""
    clusters = detect_limb_clusters(panels, stitches, panel_ranges, verts)
    report = limb_offsets(
        clusters, panel_ranges, verts, body_points, garment_centroid(verts)
    )
    return apply_offsets(verts, panel_ranges, report), report
