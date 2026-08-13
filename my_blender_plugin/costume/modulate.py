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


def interp_profile(profile, z):
    """(z, 値) を上から下へ並べた折れ線を z で線形補間する(範囲外は端の値)。

    胴の前面プロファイルや前立ての表面プロファイルの参照に使う。
    生成側と検証側が**同じ補間**で表面を読むための共有実装。
    """
    if not profile:
        raise ValueError("profile が空です")
    if z >= profile[0][0]:
        return profile[0][1]
    for index in range(1, len(profile)):
        upper_z, upper_value = profile[index - 1]
        lower_z, lower_value = profile[index]
        if lower_z <= z <= upper_z:
            if upper_z == lower_z:
                return lower_value
            local = (upper_z - z) / (upper_z - lower_z)
            return upper_value + (lower_value - upper_value) * local
    return profile[-1][1]


def ellipse_arc(semi_major, depth_ratio, start_angle, end_angle, steps=512):
    """楕円弧の長さ(kernels.ring_from_polar と同じパラメータ化: x=r·cosθ, y=r·sinθ·b)。

    楕円は弧長が角度に比例しない(前後に潰れた断面では前面の弧が濃い)ので、
    「周長 × 角度の割合」の近似は前開きの楔が大きくなるほどずれる
    (実測: 開き40°・厚み比0.68 で 2.3%)。細分した折れ線で数値的に積む。
    """
    if end_angle < start_angle:
        raise ValueError("end_angle は start_angle 以上にしてください")
    length = 0.0
    previous = None
    for index in range(steps + 1):
        angle = start_angle + (end_angle - start_angle) * index / steps
        point = (
            semi_major * math.cos(angle),
            semi_major * math.sin(angle) * depth_ratio,
        )
        if previous is not None:
            length += math.hypot(point[0] - previous[0], point[1] - previous[1])
        previous = point
    return length


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


# ------------------------------------------------------------------ 袖と襟ぐり
#
# 定義は docs/garments.md #8〜#12。どちらも「距離」ではなく**形**を決める関数で、
# ブラウスが服に見えるかどうかはここで決まる。


#: 肘の位置(袖丈に対する比)。**解剖の位置であってパラメータではない。**
#: 検証はここで袖幅が保たれているかを見るので、生成側の elbow_fraction を
#: 動かしてもゲートは逃げない(逃げると「全長を細めた円錐」が通ってしまう)
SLEEVE_ELBOW_T = 0.5

#: パンツの脚の形状定数。SLEEVE_ELBOW_T と同じ理屈で spec には持たせない
THIGH_HOLD_T = 0.3  # 股〜ここまでは太もも周を保つ(脚の付け根は円筒に近い)
KNEE_T = 0.5  # 膝の位置(股〜裾の比)


def leg_radius_profile(t, thigh_radius, knee_radius, hem_radius):
    """パンツの脚の半径。太もも(保持)→膝→裾へ smoothstep で細くなる。

    検証の thigh ゲートは THIGH_HOLD_T までのリングを見るので、
    保持区間を無くした「全長を細めた円錐の脚」は通らない。
    """
    if not 0.0 <= t <= 1.0:
        raise ValueError("t は 0〜1 にしてください: %r" % (t,))
    if t <= THIGH_HOLD_T:
        return thigh_radius
    if t <= KNEE_T:
        local = smoothstep((t - THIGH_HOLD_T) / (KNEE_T - THIGH_HOLD_T))
        return thigh_radius + (knee_radius - thigh_radius) * local
    local = smoothstep((t - KNEE_T) / (1.0 - KNEE_T))
    return knee_radius + (hem_radius - knee_radius) * local


def leg_line_shape(angle, front_angle):
    """脚ぐりが脇へ向かって持ち上がる量(0〜1)。

    前中心・後ろ中心で 0(そこがマチ)、両脇で 1。ビキニ型のショーツを
    寸胴のショーツから分けているのはこの関数の形そのもの(定義は
    docs/garments.md のビキニショーツの節)。

    素の |sin| だとマチの真下から一定の傾きで上がり続ける「斜めに切った」形に
    なる。smoothstep を掛けると前後中心のまわりが平らになって**マチの幅**が生まれ、
    脇では上がりきって水平になり**脇の帯**になる。実物の脚ぐりはこの
    「平ら → 立ち上がり → 平ら」でできている。

    **sin² は試して却下した。** 脇の頂点は smoothstep より狭くなる(脇の 15° 手前で
    93%、smoothstep 版は 30° 手前でまだ 95%)が、その代わり中間の角度で脚ぐりが
    軒並み下がり(45° で 0.50 対 0.62)、正面から見た脚ぐりの切れ込みがほとんど
    消えて丸いポーチになった。頂点の狭さより切れ込みの深さのほうが見た目に効く。
    """
    return smoothstep(abs(math.sin(angle - front_angle)))


def seat_shape(angle, front_angle):
    """尻の張り出しの周方向の分布(0〜1)。後ろ中心で 1、前半分は 0。

    人体の腰の断面は楕円ではなく、**後ろだけが出た卵形**。楕円のままだと
    後ろ姿が寸胴に見える(パンツとスカートに共通の既知の差だが、丈が短くて
    腰しか無いショーツでは尻がそのまま輪郭になるので特に効く)。

    2乗しているのは脇(0 になる位置)で傾きも 0 にするため。1乗だと脇に
    折れ目が出る。前半分を切り落としているのは、腹は出ていないから。
    """
    return max(0.0, -math.cos(angle - front_angle)) ** 2


def sleeve_radius_profile(t, bicep_radius, cuff_radius, elbow, cuff_start, gather=1.0):
    """袖の半径。**肘まで二の腕の太さを保ち、袖口で絞られ、最後は一定(カフス)**。

    全長を滑らかに細めると針のような円錐になり、袖に見えない(定義 #11)。
    実物の袖は肘までほぼ同じ太さで、前腕で絞られ、カフスは一定幅の帯で終わる。

        t <= elbow                : 二の腕の太さのまま
        elbow < t < cuff_start    : 袖口の手前(カフス × gather)へ滑らかに細る
        t >= cuff_start           : 一定 = カフスの帯(定義 #12)

    `gather` は **袖をカフスに縫い込むときのいせ込み**。1.0 だとカフスが
    silhouette に段差を作らず、幾何としては帯が在るのに絵では見えない
    (「幾何としては在るが見えない」= 反省の原因3 のミニチュア)。
    実物は袖のほうが太く、カフスに絞り込まれるので段差ができる。
    """
    if not 0.0 <= elbow < cuff_start <= 1.0:
        raise ValueError(
            "0 <= elbow < cuff_start <= 1 にしてください: elbow=%r, cuff_start=%r"
            % (elbow, cuff_start)
        )
    if gather < 1.0:
        raise ValueError("gather は 1.0 以上にしてください: %r" % (gather,))
    if t <= elbow:
        return bicep_radius
    if t >= cuff_start:
        return cuff_radius
    local = (t - elbow) / (cuff_start - elbow)
    gathered = cuff_radius * gather
    return bicep_radius + (gathered - bicep_radius) * smoothstep(local)


def neckline_drop(angle, front_drop, back_drop, front_angle):
    """襟ぐりの**前下がり**。側頸点で 0、前中心で front_drop、後ろ中心で back_drop 下がる。

    水平な輪にすると襟が煙突・肩ヨークが平皿に見える(定義 #8)。
    実物の襟ぐりは前が深く後ろが浅い、非対称な曲線になっている。

    側頸点(前から ±90°)で折れるのは**意図どおり**。実物の製図でも前身頃の
    襟ぐり線と後ろ身頃の襟ぐり線は側頸点で角度を持って出会う。
    """
    if front_drop < 0.0 or back_drop < 0.0:
        raise ValueError(
            "襟ぐりの落差は 0 以上にしてください: front=%r, back=%r" % (front_drop, back_drop)
        )
    phase = math.cos(angle - front_angle)
    return front_drop * max(0.0, phase) + back_drop * max(0.0, -phase)


def bust_projection(
    angle,
    t,
    amount,
    angular_width,
    bust_t,
    axial_width,
    front_angle,
    apex_angle=0.0,
    axial_width_up=None,
):
    """胸のふくらみ。バスト位置に**左右2つ**の局所的な出っぱりを作る(定義 #18)。

    断面を楕円にしただけの胴は**メンズシャツ**にしか見えない。ブラウスとの違いは
    前面がバストの高さで前へ出ていること。角度方向と軸方向の両方で減衰する
    「こぶ」にするので、袖ぐり(角度 0 と π)にも裾にも影響しない。

    `apex_angle` は前中心から左右の頂点までの角度。**0 にすると山が前中心で1つに
    重なり、胸骨のところがいちばん前へ出た1枚のドームになる**(実物は逆にそこが窪む
    ので不自然に見える)。0 より大きくすると2つの山に割れて谷ができる。

    角度方向の総半幅は apex_angle の値によらず `angular_width` のまま
    (各山の半幅を `angular_width - apex_angle` に取る)。袖ぐりに掛かる範囲が
    apex を動かしても変わらないようにするため。

    `axial_width_up` は**上側だけ別の半幅**にする指定(既定は下側と同じ)。
    ふくらみが襟ぐりより上へ届くと**襟ぐりの縁そのものが前へ押し出され、
    横から見ると嘴のような角ができる**。上側だけ襟ぐりで止めれば、下側の
    落ち方(実物の胸は下へ長い)を保ったまま角が消える。頂点では上下とも
    値 1・傾き 0 なので、幅が違っても折れ目にはならない。

    戻り値は**半径に足す量**(メートル)。角度・軸方向とも余弦の山で、
    範囲の外はきっかり 0 になる(裾までうねると別の破綻になる)。
    """
    if amount < 0.0:
        raise ValueError("amount は 0 以上にしてください: %r" % (amount,))
    if angular_width <= 0.0 or axial_width <= 0.0:
        raise ValueError(
            "幅は正の数にしてください: angular=%r, axial=%r" % (angular_width, axial_width)
        )
    if not 0.0 <= apex_angle < angular_width:
        raise ValueError(
            "胸の頂点の角度は 0 以上・角度幅未満にしてください: apex=%r, angular=%r"
            % (apex_angle, angular_width)
        )
    up_width = axial_width if axial_width_up is None else axial_width_up
    if up_width <= 0.0:
        raise ValueError("上側の幅は正の数にしてください: %r" % (axial_width_up,))
    delta = (angle - front_angle + math.pi) % (2.0 * math.pi) - math.pi
    if abs(delta) >= angular_width:
        return 0.0
    # t は上端が 0 なので、t < bust_t が「頂点より上」
    signed = t - bust_t
    reach = axial_width if signed >= 0.0 else up_width
    axial = abs(signed)
    if axial >= reach:
        return 0.0
    half = angular_width - apex_angle
    lobe = 0.0
    for centre in (-apex_angle, apex_angle):
        offset = abs(delta - centre)
        if offset < half:
            # 重ねずに max を取る。足すと前中心で山を超えて逆に盛り上がる
            lobe = max(lobe, 0.5 * (1.0 + math.cos(math.pi * offset / half)))
    if lobe <= 0.0:
        return 0.0
    band = 0.5 * (1.0 + math.cos(math.pi * axial / reach))
    return amount * lobe * band


def shirttail_drop(angle, back_drop, front_ratio, front_angle):
    """裾のシャツテール。**脇でいちばん高く、前後の中心が下がる**(定義 #16)。

    襟ぐりの前下がり(`neckline_drop`)と同じ形の落差だが、こちらは裾。
    水平に切った裾は「筒を切った」ようにしか見えず、シャツにもブラウスにも見えない。
    実物は前後が長く脇が短い曲線で、後ろのほうが前より少し長い。
    """
    if back_drop < 0.0:
        raise ValueError("back_drop は 0 以上にしてください: %r" % (back_drop,))
    if not 0.0 <= front_ratio <= 1.5:
        raise ValueError("front_ratio は 0〜1.5 にしてください: %r" % (front_ratio,))
    return neckline_drop(angle, back_drop * front_ratio, back_drop, front_angle)


def button_hole_segments(segments, holes):
    """ボタンの穴を開ける分割番号(定義 #15)。

    最初は「頂点を奥へ押した窪み」にしたが、寄って見ると陰影がぼやけて
    穴に見えなかった。**面を抜いて本当に開ける**(袖ぐりと同じ `skip_faces`)。
    そうすると検証側も「メッシュに穴がいくつあるか」を境界ループとして数えられる。
    窪みだと「押した頂点の数」を数えることになり、生成側の申告に近づいてしまう。
    """
    if holes <= 0:
        return []
    if segments % holes != 0:
        raise ValueError(
            "分割数(%d)は穴の数(%d)の倍数にしてください(等間隔に置けません)"
            % (segments, holes)
        )
    period = segments // holes
    return [index * period for index in range(holes)]


def button_positions(top_z, bottom_z, count, top_inset, bottom_inset):
    """ボタンの高さを等間隔で返す(定義 #14)。

    実物のシャツは第1ボタンが襟のすぐ下、最後が裾より少し上に来る。
    上下の余白を入れて、その間を等間隔に割る。**間隔が等しいこと**を
    検証側が変動係数で確かめるので、ここは等間隔でなければならない。
    """
    if count < 1:
        raise ValueError("count は1以上にしてください: %r" % (count,))
    if bottom_z >= top_z:
        raise ValueError("bottom_z(%r) は top_z(%r) より下にしてください" % (bottom_z, top_z))
    span = top_z - bottom_z
    if top_inset < 0.0 or bottom_inset < 0.0 or top_inset + bottom_inset >= 1.0:
        raise ValueError(
            "余白は 0 以上で合計 1 未満にしてください: top=%r, bottom=%r"
            % (top_inset, bottom_inset)
        )
    first = top_z - span * top_inset
    last = bottom_z + span * bottom_inset
    if count == 1:
        return [(first + last) * 0.5]
    step = (first - last) / (count - 1)
    return [first - step * index for index in range(count)]
