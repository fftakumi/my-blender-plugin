"""シルエット(輪郭)の幅プロファイルと比較。bpy 非依存。

参照画像と生成物を**同じ量**で比べるための道具。周長やパラメータではなく
「正面から見た幅が高さ方向にどう変わるか」を測る。参照画像から取れるのは投影された
形だけなので、比較する側もレンダーの投影で揃える(正射影で撮ること)。

各行で「中心線を含む連続した黒い区間」の幅を取る。参照画像では髪がスカートの
外側に垂れて輪郭を太らせるが、髪は細く途切れた別の区間になるので、
中心線を含む区間だけを見れば巻き込まずに済む(閾値の選び方は下記)。

**この方法の限界**(数値を読むときに必ず考慮すること):
  - 参照画像のスカート上端はコルセットや上着に隠れることが多い。隠れている場合、
    プロファイルの上端は「本当のウエスト」より下(より広い位置)から始まるので、
    裾/上端の比は**本当のフレア量より小さく出る**
  - 髪や小物がスカートと同系色で、かつ中心線を含む区間に食い込む場合は分離できない。
    そのときはマスク画像を目で見て判断する(`tools/fit_silhouette.py` が保存する)
"""

import math

#: 参照画像を2値化する輝度のしきい値(sRGB 0-1)。0.40 = 102/255。
#: 実測: この参照画像の紺のスカートは輝度 40-73/255、髪は明るく、0.55 だと髪を巻き込み
#: 0.40 だとほぼ落ちた(0.39 でも同様)
DEFAULT_LUMINANCE_THRESHOLD = 0.40


def linear_to_srgb(value):
    """Blender の画像画素(リニア)を sRGB 0-1 へ。しきい値は sRGB 空間で決める"""
    if value <= 0.0031308:
        return value * 12.92
    return 1.055 * (value ** (1.0 / 2.4)) - 0.055


def srgb_luminance(red, green, blue):
    """sRGB ガンマ空間の輝度(見た目の明るさ)"""
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def mask_from_srgb_rows(rows, threshold=DEFAULT_LUMINANCE_THRESHOLD):
    """[[(r, g, b), ...], ...](上の行から順)→ 暗い画素を True にした2値マスク"""
    return [
        [srgb_luminance(*pixel) < threshold for pixel in row]
        for row in rows
    ]


def mask_from_alpha_rows(rows, threshold=0.5):
    """アルファ値の行列から2値マスクを作る(透過背景でレンダーした自作物向け)"""
    return [[value >= threshold for value in row] for row in rows]


def runs(mask_row):
    """1行の中の連続した True の区間 [(start, end_inclusive), ...]"""
    result = []
    start = None
    for index, value in enumerate(mask_row):
        if value and start is None:
            start = index
        elif not value and start is not None:
            result.append((start, index - 1))
            start = None
    if start is not None:
        result.append((start, len(mask_row) - 1))
    return result


def close_row_gaps(row_runs, max_gap):
    """近すぎる区間をつなぐ(行ごとのモルフォロジー・クロージング)。

    衣装の内側に**明るい部分**があると輪郭が途切れる。実測: この参照画像の
    プリーツスカートには白の差し込みパネルと裾の明るい飾り線があり、そこで
    暗い区間が割れて裾の幅が実際の 6 割ほどに測れていた。
    衣装の内側の隙間だけを埋めたいので、max_gap は「服の中の模様の幅」程度にする
    (大きくしすぎると髪までつながる)。
    """
    if not row_runs or max_gap <= 0:
        return list(row_runs)
    merged = [list(row_runs[0])]
    for start, end in row_runs[1:]:
        if start - merged[-1][1] - 1 <= max_gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [tuple(item) for item in merged]


def centered_run(row_runs, center):
    """中心線を含む区間。無ければ中心に最も近い区間。空なら None"""
    if not row_runs:
        return None
    for start, end in row_runs:
        if start <= center <= end:
            return (start, end)
    return min(row_runs, key=lambda item: min(abs(item[0] - center), abs(item[1] - center)))


def dominant_center(mask):
    """マスク全体で最も画素が多い列を中心線とみなす。

    中心線を明示しないときの既定。スカートは左右対称に近いので、
    列ごとの画素数が最大になるところがだいたい正中線になる。
    """
    if not mask or not mask[0]:
        return 0
    width = len(mask[0])
    counts = [0] * width
    for row in mask:
        for index, value in enumerate(row):
            if value:
                counts[index] += 1
    peak = max(counts)
    if peak == 0:
        return width // 2
    # 胴のようにまっすぐな部分があると最大値の列が複数並ぶ。**その真ん中**を返す
    # (先頭を返すと中心線が左端に寄って、髪の区間を掴んでしまう)
    tied = [index for index, count in enumerate(counts) if count == peak]
    return tied[len(tied) // 2]


def width_profile(mask, center=None, min_width=2, max_gap=0):
    """行ごとの幅プロファイル。

    戻り値: {"center": 中心列, "rows": [{"y", "left", "right", "width"}, ...]}
    幅が min_width 未満の行(髪一本だけ拾ったような行)は落とす。
    max_gap を与えると、その画素数までの隙間を埋めてから測る(服の中の明るい模様対策)。
    """
    if center is None:
        center = dominant_center(mask)
    entries = []
    for y, row in enumerate(mask):
        span = centered_run(close_row_gaps(runs(row), max_gap), center)
        if span is None:
            continue
        width = span[1] - span[0] + 1
        if width < min_width:
            continue
        entries.append({"y": y, "left": span[0], "right": span[1], "width": width})
    return {"center": center, "rows": entries}


def trim_at_widest(profile):
    """一番広い行までで打ち切る。スカートの一番広い行は裾なので、それより下は衣装ではない。

    切り出し範囲が裾より下(タイツ・脚・靴)まで届いていると、プロファイルの下端が
    急に細くなって「裾/上端の比」が 1 未満になる(実測: 1.89 まで広がったあと
    0.41 に落ちた)。これを自動で落とせば切り出しの精度に神経を使わなくて済む。
    """
    entries = profile["rows"]
    if not entries:
        return profile
    widest = max(range(len(entries)), key=lambda index: entries[index]["width"])
    return {"center": profile["center"], "rows": entries[: widest + 1]}


def normalized_widths(profile, samples=24):
    """プロファイルを縦 [0,1] の samples 点へ等間隔に均し、上端の幅で割った比を返す。

    戻り値: [(t, width_ratio), ...]。t=0 が上端、t=1 が下端。
    上端の幅で割るので、画像の解像度やレンダーの画角に依存しない。
    """
    entries = profile["rows"]
    if len(entries) < 2:
        raise ValueError("プロファイルの行が足りません: %d" % len(entries))
    top, bottom = entries[0]["y"], entries[-1]["y"]
    if bottom == top:
        raise ValueError("プロファイルの高さが 0 です")
    by_row = {entry["y"]: entry["width"] for entry in entries}
    rows_sorted = sorted(by_row)

    def width_at(y):
        """欠けている行は上下の実在する行から線形に補う"""
        if y in by_row:
            return by_row[y]
        lower = [row for row in rows_sorted if row < y]
        upper = [row for row in rows_sorted if row > y]
        if not lower:
            return by_row[upper[0]]
        if not upper:
            return by_row[lower[-1]]
        a, b = lower[-1], upper[0]
        ratio = (y - a) / (b - a)
        return by_row[a] + (by_row[b] - by_row[a]) * ratio

    top_width = width_at(top)
    if top_width <= 0:
        raise ValueError("上端の幅が 0 です")
    result = []
    for index in range(samples):
        t = index / (samples - 1)
        y = top + (bottom - top) * t
        result.append((t, width_at(int(round(y))) / top_width))
    return result


def compare(reference, produced, samples=24):
    """2つのプロファイルを比べる。数値はすべて「上端の幅に対する比」"""
    a = normalized_widths(reference, samples)
    b = normalized_widths(produced, samples)
    differences = [abs(x[1] - y[1]) for x, y in zip(a, b)]
    return {
        "samples": samples,
        "rms": math.sqrt(sum(d * d for d in differences) / len(differences)),
        "max_deviation": max(differences),
        "hem_ratio_reference": a[-1][1],
        "hem_ratio_produced": b[-1][1],
        "hem_ratio_error": b[-1][1] / a[-1][1] - 1.0 if a[-1][1] else None,
        "reference": a,
        "produced": b,
    }


def fit_flare(normalized, flare_range=(1.0, 3.2), curve_range=(0.4, 3.0)):
    """幅プロファイルに `1 + (flare-1) * t**curve` を当てはめる。

    この式は modulate.flare_multiplier と同じ形なので、当てはめた結果を
    そのまま spec の flare / flare_curve の目安に使える。
    総当たりなので乱数も初期値も要らない(決定的)。
    """
    best = None
    flare_low, flare_high = flare_range
    curve_low, curve_high = curve_range
    flare_steps = int(round((flare_high - flare_low) / 0.01)) + 1
    curve_steps = int(round((curve_high - curve_low) / 0.05)) + 1
    for flare_index in range(flare_steps):
        flare = flare_low + flare_index * 0.01
        for curve_index in range(curve_steps):
            curve = curve_low + curve_index * 0.05
            total = 0.0
            for t, measured in normalized:
                model = 1.0 + (flare - 1.0) * (t**curve)
                total += (model - measured) ** 2
            rms = math.sqrt(total / len(normalized))
            if best is None or rms < best["rms"] - 1e-12:
                best = {"flare": round(flare, 3), "curve": round(curve, 3), "rms": rms}
    return best
