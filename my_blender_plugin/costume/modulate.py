"""形の変調(断面の楕円・フレア・プリーツ)。bpy 非依存の純粋関数だけ。

ここが「Blender の標準機能では手が届かないもの」の本体。
リング1本ごとの寸法と、リング上の分割ごとの半径オフセットを決める。
生成そのもの(頂点を並べて四角面を張る)は kernels.py。

**寸法は半径ではなく周長で扱う。** ウエスト寸法・ヒップ寸法は現実でも周長で測る量で、
断面が楕円になると周長と半径は比例しなくなる。周長を主にしておけば
「ウエスト 62cm の衣装」が断面の形に関係なく成立する。
"""

import math


def smoothstep(x):
    """0〜1 に収めた滑らかな補間係数"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x * x * (3.0 - 2.0 * x)


def axis_fractions(rings):
    """リング本数から軸方向の位置 t(0=上端, 1=下端)を等間隔で返す"""
    if rings < 2:
        raise ValueError("rings は2以上にしてください: %r" % (rings,))
    last = rings - 1
    return [index / last for index in range(rings)]


# ------------------------------------------------------------------ 楕円断面


def ellipse_perimeter(semi_major, depth_ratio):
    """楕円の周長(ラマヌジャンの近似。相対誤差は扁平率0.5でも1e-5未満)"""
    a = semi_major
    b = semi_major * depth_ratio
    return math.pi * (3.0 * (a + b) - math.sqrt((3.0 * a + b) * (a + 3.0 * b)))


def ellipse_semi_major(perimeter, depth_ratio):
    """周長と厚み比から長半径(x方向)を逆算する。周長は長半径に比例する"""
    if depth_ratio <= 0.0:
        raise ValueError("depth_ratio は正の数にしてください: %r" % (depth_ratio,))
    return perimeter / ellipse_perimeter(1.0, depth_ratio)


def depth_ratio_profile(ts, waist_depth_ratio, flare, flare_curve):
    """リングごとの厚み比(前後 / 左右)。

    腰では体の断面(楕円)に沿い、**広がったぶんだけ円に近づく**。フレアで体から
    離れた布は自由に落ちて断面が円錐に近づくので、これが物理的にも素直。
    flare=1(タイトスカート)なら最後まで楕円のまま。
    """
    span = flare - 1.0
    if span <= 1e-9:
        return [waist_depth_ratio] * len(ts)
    profile = []
    for t in ts:
        opened = (flare_multiplier(t, flare, flare_curve) - 1.0) / span
        opened = min(1.0, max(0.0, opened))
        profile.append(waist_depth_ratio + (1.0 - waist_depth_ratio) * opened)
    return profile


# ------------------------------------------------------------------ 軸方向の寸法


def follow_measure(t, length, waist_value, hip_value, hip_drop, hip_hug):
    """体に沿った場合の寸法。ウエストからヒップへ滑らかに増え、以降は一定。

    周長でも半径でも使える(どちらも線形なので)。
    hip_hug=0 ならウエスト寸法のまま(体に沿わせない筒)。
    丈がヒップまで届かない短い衣装では、ヒップに到達する前で止まる。
    """
    target = waist_value + (hip_value - waist_value) * hip_hug
    if hip_drop <= 0.0:
        return target
    drop = t * length  # 上端からの垂直距離
    return waist_value + (target - waist_value) * smoothstep(drop / hip_drop)


def flare_multiplier(t, flare, flare_curve):
    """裾に向かって広がる倍率。t=0 で 1.0、t=1 で flare。

    flare_curve > 1 で「腰まわりは体に沿い、裾でぐっと広がる」形になる。
    1.0 前後だと A ラインのまっすぐな円錐、1.5 以上だと裾が朝顔状に開く。
    """
    return 1.0 + (flare - 1.0) * (t**flare_curve)


def skirt_profile(
    rings,
    length,
    waist_perimeter,
    hip_perimeter,
    hip_drop,
    hip_hug,
    flare,
    flare_curve,
    depth_ratio,
):
    """スカートのリングごとの寸法一式を返す。

    perimeter = 「体に沿った周長」×「フレア倍率」。長半径はその周長と厚み比から逆算する。
    丈がヒップより短い場合でも破綻せず、下端の値は実際に到達した寸法になる。
    """
    ts = axis_fractions(rings)
    perimeters = [
        follow_measure(t, length, waist_perimeter, hip_perimeter, hip_drop, hip_hug)
        * flare_multiplier(t, flare, flare_curve)
        for t in ts
    ]
    depth_ratios = depth_ratio_profile(ts, depth_ratio, flare, flare_curve)
    semi_majors = [
        ellipse_semi_major(perimeter, ratio)
        for perimeter, ratio in zip(perimeters, depth_ratios)
    ]
    return {
        "t": ts,
        "perimeter": perimeters,
        "depth_ratio": depth_ratios,
        "semi_major": semi_majors,
        "top_perimeter": perimeters[0],
        "bottom_perimeter": perimeters[-1],
    }


def band_profile(rings, bottom_perimeter, flare, depth_ratio):
    """ウエストバンドのリングごとの寸法。

    flare は「下端 / 上端」の比。**下端をちょうど狙いの周長に合わせる**ので、
    スカート本体の上端リングと座標が一致し、接合が計算で保証される。
    """
    ts = axis_fractions(rings)
    top_perimeter = bottom_perimeter / flare
    perimeters = [top_perimeter * (1.0 + (flare - 1.0) * t) for t in ts]
    depth_ratios = [depth_ratio] * len(ts)
    return {
        "t": ts,
        "perimeter": perimeters,
        "depth_ratio": depth_ratios,
        "semi_major": [
            ellipse_semi_major(perimeter, depth_ratio) for perimeter in perimeters
        ],
        "top_perimeter": perimeters[0],
        "bottom_perimeter": perimeters[-1],
    }


# ------------------------------------------------------------------ プリーツ


def pleat_offsets(segments, pleats, depth, duty):
    """プリーツの半径オフセット(分割ごと・無次元・**平均ゼロ**)を返す。

    ナイフプリーツを矩形波でモデル化する。1周期のうち duty ぶんが外側(見える面)、
    残りが内側(折り込み)。

    **平均をゼロにしてある**のは寸法検算のため。「外側の半径を設計値にして内側へ
    折り込む」形にすると境界リングの平均半径が設計値より小さくなり、寸法の検算が
    プリーツを入れた瞬間に必ず落ちる。シルエットの線がプリーツの中央を通る形に
    すれば平均が保たれる。
    """
    if pleats <= 0 or depth <= 0.0:
        return [0.0] * segments
    if segments % pleats != 0:
        raise ValueError(
            "segments(%d) は pleats(%d) の倍数にしてください(1山あたりの分割数が"
            "整数にならないと折り目が周方向にずれます)" % (segments, pleats)
        )
    period = segments // pleats
    if period < 2:
        raise ValueError(
            "1山あたりの分割数が %d しかありません。プリーツは外側の面と折り込みの"
            "2つが要るので segments は pleats の2倍以上にしてください"
            "(segments=%d, pleats=%d)" % (period, segments, pleats)
        )
    outer_count = max(1, min(period - 1, int(round(duty * period))))
    inner_count = period - outer_count
    # 平均ゼロになるように外側 / 内側の振幅を配分する
    outer_value = depth * inner_count / period
    inner_value = -depth * outer_count / period
    return [
        outer_value if (index % period) < outer_count else inner_value
        for index in range(segments)
    ]


def pleat_taper(t, stitch_down, closed):
    """プリーツの開き具合。**上端の直下から非ゼロ**にする。

    実物のプリーツスカートは、ウエストバンドから裾まで**折り目線が連続している**。
    上部は縫い止め(実測: ウエストバンドから約9cm = 丈の 0.22)とプレスで
    「閉じている」だけで、線は見えている。

    最初の実装はここを「上部は折り目が無い」と取り違えて 0 から smoothstep で
    立ち上げていたため、**上半分が折り目のない滑らかな筒**になり
    プリーツスカートに見えなかった(docs/garments.md 参照)。

      t = 0            … 0(上端リングだけは真円。ウエストバンドとの接合のため)
      0 < t <= stitch_down … closed(閉じているが折り目線は出る)
      stitch_down < t      … closed から 1.0 へ滑らかに開く
    """
    if t <= 0.0:
        return 0.0
    if t <= stitch_down:
        return closed
    if stitch_down >= 1.0:
        return closed
    return closed + (1.0 - closed) * smoothstep((t - stitch_down) / (1.0 - stitch_down))


def drape_offsets(segments, folds, depth):
    """柔らかいドレープ(布のたるみ)の半径オフセット。余弦なので平均ゼロ。

    プリーツ(矩形波・折り目が立つ)との違いは2つ:
      - 折り目が立たず、なだらかに波打つ。布が自重で落ちたときの形
      - 山数が分割数を割り切らなくてよい(余弦は整数周期でなくても連続)
    これが無いと、正しく作った円錐が「ランプシェード」に見えて衣装に見えない。
    """
    if folds <= 0 or depth <= 0.0:
        return [0.0] * segments
    return [
        depth * math.cos(2.0 * math.pi * folds * index / segments)
        for index in range(segments)
    ]


def drape_taper(t):
    """ドレープはウエストで 0、裾で最大。腰は体に沿うので波打たない"""
    return smoothstep(t)


def combine_offsets(modulations, ring_index, segments):
    """複数の変調を1リングぶんの合成オフセットにする。

    modulations の各要素は {"offsets": 分割ごと, "tapers": リングごと}。
    プリーツとドレープはテーパーの形が違うので、掛け合わせてから足す。
    """
    combined = [0.0] * segments
    for modulation in modulations:
        taper = modulation["tapers"][ring_index]
        if taper == 0.0:
            continue
        offsets = modulation["offsets"]
        for index in range(segments):
            combined[index] += offsets[index] * taper
    return combined


def pleat_fold_segments(segments, pleats, depth, duty):
    """プリーツの折り目が来る分割位置を返す(陰影を割る辺の位置)。

    `pleat_offsets` と同じ周期の計算から**直接**求める。オフセット列の差分に
    しきい値を掛けて判定する方式は駄目で、ドレープ(余弦)を粗く刻んだときの
    段差と区別できない(実際にドレープ48分割で全48本を折り目と誤検出した)。
    折り目として陰影を割ってよいのは矩形波のプリーツだけ。
    """
    if pleats <= 0 or depth <= 0.0 or segments % pleats != 0:
        return []
    period = segments // pleats
    if period < 2:
        return []
    outer_count = max(1, min(period - 1, int(round(duty * period))))
    # 内→外 が period の先頭、外→内 が outer_count の位置
    return [
        index for index in range(segments) if index % period in (0, outer_count)
    ]


def ring_radii(base_semi_major, combined_offsets):
    """1リングぶんの分割ごとの長半径。base に合成オフセットを乗せる"""
    return [base_semi_major * (1.0 + offset) for offset in combined_offsets]


def circle_angles(segments):
    """周方向の角度(ラジアン)。反時計回り(+Z から見て)"""
    if segments < 3:
        raise ValueError("segments は3以上にしてください: %r" % (segments,))
    step = 2.0 * math.pi / segments
    return [index * step for index in range(segments)]
