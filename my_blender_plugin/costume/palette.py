"""画像の画素列 → 代表色。bpy 非依存で**決定的**。

色は AI に投げない。median cut は初期値も乱数も使わないので、同じ画像から必ず
同じパレットが出る(k-means は初期値依存で非決定的なので使わない)。

画素の取り出し(bpy 側)は呼ぶ側の仕事。2048² の `image.pixels` を丸ごと
純 Python に流すと遅いので、複製画像を `image.scale(64, 64)` してから渡す。
"""


def rgba_to_rgb(flat, min_alpha=0.5, stride=1):
    """`image.pixels` 相当の平坦な RGBA 列を [(r, g, b), ...] にする。

    透明な画素は落とす(切り抜き画像の背景を色として拾わないため)。
    """
    if len(flat) % 4 != 0:
        raise ValueError("RGBA 列の長さが4の倍数ではありません: %d" % len(flat))
    if stride < 1:
        raise ValueError("stride は1以上にしてください: %r" % (stride,))
    pixels = []
    for index in range(0, len(flat) // 4, stride):
        base = index * 4
        if flat[base + 3] >= min_alpha:
            pixels.append((flat[base], flat[base + 1], flat[base + 2]))
    return pixels


def _split(box):
    """最も広がっている軸で、分散が最小になる位置で箱を2つに割る。

    素朴な median cut は「個数の中央」で割るので、色の塊が偏った画像で
    **塊の内側を切ってしまう**(実測: 赤60px + 青20px を2色に量子化すると
    どちらの代表色も (0.5, 0.1, 0.5) という混ざった紫になった)。
    軸方向に並べて左右の二乗和が最小になる位置を全探索すれば、
    乱数も初期値も使わないまま塊の切れ目で割れる。
    """
    spans = [
        (max(pixel[axis] for pixel in box) - min(pixel[axis] for pixel in box), axis)
        for axis in range(3)
    ]
    _span, axis = max(spans)
    ordered = sorted(box, key=lambda pixel: (pixel[axis], pixel))
    values = [pixel[axis] for pixel in ordered]

    count = len(values)
    prefix_sum = [0.0] * (count + 1)
    prefix_square = [0.0] * (count + 1)
    for index, value in enumerate(values):
        prefix_sum[index + 1] = prefix_sum[index] + value
        prefix_square[index + 1] = prefix_square[index] + value * value

    def error(low, high):
        """[low, high) の二乗和誤差"""
        size = high - low
        if size <= 0:
            return 0.0
        total = prefix_sum[high] - prefix_sum[low]
        return (prefix_square[high] - prefix_square[low]) - total * total / size

    best_index, best_cost = count // 2, None
    for index in range(1, count):
        cost = error(0, index) + error(index, count)
        if best_cost is None or cost < best_cost - 1e-15:
            best_index, best_cost = index, cost
    return ordered[:best_index], ordered[best_index:]


def _average(box):
    count = len(box)
    return tuple(sum(pixel[axis] for pixel in box) / count for axis in range(3))


def median_cut(pixels, count):
    """median cut で count 色に量子化する。母数の多い色から順に返す。

    戻り値: [{"color": (r, g, b), "weight": 0-1}, ...]
    """
    if count < 1:
        raise ValueError("count は1以上にしてください: %r" % (count,))
    if not pixels:
        return []

    boxes = [list(pixels)]
    while len(boxes) < count:
        # 一番広がっている箱から割る。割れない(同色だけの)箱は飛ばす
        splittable = [
            (
                max(
                    max(pixel[axis] for pixel in box) - min(pixel[axis] for pixel in box)
                    for axis in range(3)
                ),
                index,
            )
            for index, box in enumerate(boxes)
            if len(box) > 1
        ]
        if not splittable:
            break
        _span, index = max(splittable)
        if _span <= 0.0:
            break
        left, right = _split(boxes.pop(index))
        boxes.extend(box for box in (left, right) if box)

    total = float(len(pixels))
    result = [
        {"color": _average(box), "weight": len(box) / total, "pixels": len(box)}
        for box in boxes
    ]
    result.sort(key=lambda item: (-item["weight"], item["color"]))
    return result


def _luminance(color):
    return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]


def _saturation(color):
    high, low = max(color), min(color)
    return 0.0 if high <= 0.0 else (high - low) / high


def dominant_colors(pixels, count=4, drop_extremes=False):
    """代表色を返す。drop_extremes で真っ白・真っ黒に近い色を落とす。

    背景が白抜きの資料画像から服の色を拾うときに drop_extremes=True が効く。
    ただし白い服・黒い服も落ちるので既定は False。
    """
    colors = median_cut(pixels, count * 2 if drop_extremes else count)
    if drop_extremes:
        kept = [
            item
            for item in colors
            if 0.03 < _luminance(item["color"]) < 0.95 or _saturation(item["color"]) > 0.15
        ]
        colors = (kept or colors)[:count]
    return colors[:count]


def to_material_params(colors, alpha=1.0):
    """代表色を spec の materials 用の dict に直す。

    彩度が低い色ほど布として艶を控えめにする(白い綿と光沢サテンを混同しないため)。
    """
    materials = {}
    for index, item in enumerate(colors):
        color = item["color"]
        materials["main" if index == 0 else "accent%d" % index] = {
            "base_color": [round(channel, 6) for channel in color],
            "roughness": round(0.62 + 0.25 * (1.0 - _saturation(color)), 3),
            "sheen": round(0.15 + 0.3 * _saturation(color), 3),
            "alpha": alpha,
        }
    return materials
