"""パーツカタログ。spec の1パーツ → PartMesh。bpy 非依存。

衣装 = 裁縫パーツの集合。パーツごとに独立して作って独立して検証できるので、
不合格になったときにどのパーツのどのパラメータが原因か分かる。
新しい衣装は「新しいコード」ではなく「新しいパーツの組み合わせ」で作る。

`design` に入れる期待値は、**プロファイル計算を通さない最短経路**で出す
(例: 上端周長 = サイズ表のウエスト寸法そのもの)。プロファイル側にバグが入ったとき
設計値と実物がずれて検証で落ちるようにするため。同じ計算を2回書いて突き合わせても
自己整合しか確かめられない。
"""

import math

from . import kernels, modulate, sizing as sizing_module, spec as spec_module


class PartError(ValueError):
    """パーツの組み立てに失敗したときに投げる"""


def _tube(name, profile, z_per_ring, angles, modulations, scale, material):
    """リングごとの寸法から筒を1本作る(単位変換はここで1回だけ)"""
    segments = len(angles)
    ring_points = []
    for index, (semi_major, depth_ratio, z) in enumerate(
        zip(profile["semi_major"], profile["depth_ratio"], z_per_ring)
    ):
        combined = modulate.combine_offsets(modulations, index, segments)
        radii = modulate.ring_radii(semi_major, combined)
        ring_points.append(
            kernels.ring_from_polar(
                [radius * scale for radius in radii],
                angles,
                z * scale,
                depth_ratio=depth_ratio,
            )
        )
    mesh = kernels.loft_rings(ring_points, name=name, closed=True, tubular=True)
    mesh.material = material
    return mesh


def build_skirt_body(part, table, scale):
    """スカート本体。ウエストから下へ、ヒップに沿ってから裾へ広がる筒"""
    params = part["params"]
    height = table["height"]
    length = params["length"] * height
    waist_z = params["waist_z"] * height
    flare = params["flare"]

    profile = modulate.skirt_profile(
        rings=params["rings"],
        length=length,
        waist_perimeter=table["waist"],
        hip_perimeter=table["hip"],
        hip_drop=table["hip_drop"],
        hip_hug=params["hip_hug"],
        flare=flare,
        flare_curve=params["flare_curve"],
        depth_ratio=table["depth_ratio"],
    )
    angles = modulate.circle_angles(params["segments"])
    modulations = [
        {
            "kind": "pleat",
            "frequency": params["pleats"],
            "depth": params["pleat_depth"],
            "offsets": modulate.pleat_offsets(
                params["segments"],
                params["pleats"],
                params["pleat_depth"],
                params["pleat_duty"],
            ),
            "tapers": [
                modulate.pleat_taper(
                    t, params["pleat_stitch_down"], params["pleat_closed"]
                )
                for t in profile["t"]
            ],
        },
        {
            "kind": "drape",
            "frequency": params["drape_folds"],
            "depth": params["drape_depth"],
            "offsets": modulate.drape_offsets(
                params["segments"], params["drape_folds"], params["drape_depth"]
            ),
            "tapers": [modulate.drape_taper(t) for t in profile["t"]],
        },
    ]
    z_per_ring = [waist_z - t * length for t in profile["t"]]

    mesh = _tube(
        part["name"], profile, z_per_ring, angles, modulations, scale, part["material"]
    )
    # プリーツの折り線だけを「陰影を割る辺」として渡す(ドレープは割らない)
    mesh.sharp_segments = modulate.pleat_fold_segments(
        params["segments"], params["pleats"], params["pleat_depth"], params["pleat_duty"]
    )

    # 裾の周長を独立に検算できるのは、丈がヒップまで届いていてヒップに沿わせ、
    # かつ周方向の変調(プリーツ・ドレープ)が無い場合だけ。変調が入ると実周長に
    # 折り込み/たるみの布が乗って「着装時の外周」とは別の量になる
    # (そちらは周波数成分と布量の下限で検算する)。
    active = [
        item for item in modulations if item["frequency"] > 0 and item["depth"] > 0.0
    ]
    reaches_hip = length >= table["hip_drop"] and params["hip_hug"] == 1.0
    checkable = reaches_hip and not active
    if checkable:
        note = None
    elif not reaches_hip:
        note = "丈がヒップまで届かない(または hip_hug<1)ので裾周長は独立に検算しない"
    else:
        note = "周方向の変調(%s)で実周長が伸びるので裾周長は独立に検算しない" % ", ".join(
            "%s×%d" % (item["kind"], item["frequency"]) for item in active
        )
    mesh.design = {
        "top_perimeter": table["waist"] * scale,
        "bottom_perimeter": (table["hip"] * flare * scale) if checkable else None,
        "bottom_fit_perimeter": profile["bottom_perimeter"] * scale,
        "bottom_perimeter_note": note,
        "top_z": waist_z * scale,
        "bottom_z": (waist_z - length) * scale,
        "length": length * scale,
        "depth_ratio_top": profile["depth_ratio"][0],
        "depth_ratio_bottom": profile["depth_ratio"][-1],
        # 検証側が「指定した周期成分が本当に入っているか」を周波数で確かめるための一覧
        "radial_modulations": [
            {"kind": item["kind"], "frequency": item["frequency"], "depth": item["depth"]}
            for item in active
        ],
        "pleats": params["pleats"],
        "pleat_depth": params["pleat_depth"],
        "boundary_loops": 2,
        "boundary_verts": params["segments"],
    }
    return mesh


def build_waistband(part, table, scale):
    """ウエストバンド。ウエストラインから上へ立ち上がる短い筒。

    バンドの**下端**がウエストライン。こうするとスカート本体の上端リングと
    座標がそのまま一致し、接合(リング共有)が計算で保証される。
    """
    params = part["params"]
    height = table["height"]
    band_height = params["height"] * height
    waist_z = params["waist_z"] * height
    flare = params["flare"]

    profile = modulate.band_profile(
        rings=params["rings"],
        bottom_perimeter=table["waist"],
        flare=flare,
        depth_ratio=table["depth_ratio"],
    )
    angles = modulate.circle_angles(params["segments"])
    z_per_ring = [waist_z + band_height * (1.0 - t) for t in profile["t"]]

    # バンドは折り目もドレープも入れない(縫い付けられていて動かない)
    mesh = _tube(part["name"], profile, z_per_ring, angles, [], scale, part["material"])
    mesh.design = {
        "top_perimeter": table["waist"] / flare * scale,
        "bottom_perimeter": table["waist"] * scale,
        "bottom_fit_perimeter": table["waist"] * scale,
        "bottom_perimeter_note": None,
        "top_z": (waist_z + band_height) * scale,
        "bottom_z": waist_z * scale,
        "length": band_height * scale,
        "depth_ratio_top": profile["depth_ratio"][0],
        "depth_ratio_bottom": profile["depth_ratio"][-1],
        "radial_modulations": [],
        "pleats": 0,
        "pleat_depth": 0.0,
        "boundary_loops": 2,
        "boundary_verts": params["segments"],
    }
    return mesh


# ------------------------------------------------------------------ ブラウス
#
# 定義は docs/garments.md。要点だけ:
#   - 前が開いている(かぶりものではない)ので、胴は閉じた筒ではなく開いた筒
#   - 袖ぐりが2つ空いていて、その上に肩が渡っている(穴が上端リングに達してはいけない)
#   - 袖は「袖ぐりの実寸から」作る。袖ぐり周長 × ゆとり = 袖口(袖山)の周長。
#     これが set-in sleeve の定義そのもので、寸法表の別項目から作ってはいけない

#: 前中心の角度(-Y 方向を正面とする)
FRONT_ANGLE = -math.pi / 2


def _front_open_angles(segments, gap_angle=None):
    """前中心に開き口が来るように角度を並べる。

    loft_rings(closed=False) は最後の分割と最初の分割の間に面を作らない。
    その隙間が前開きになるので、**隙間の角度を明示して細くできる**ようにする。

    既定(gap_angle=None)は1分割ぶん。分割数を増やすと隙間も細くなるが、
    分割数28だと隙間が 3.3cm もあり、幅 3.0cm の前立てで覆いきれずに
    裾のアップで黒い筋として出た。前立てより細くすること。
    """
    if gap_angle is None:
        gap_angle = 2.0 * math.pi / segments
    if not 0.0 < gap_angle < 2.0 * math.pi:
        raise PartError("前開きの隙間の角度が範囲外です: %r" % (gap_angle,))
    step = (2.0 * math.pi - gap_angle) / (segments - 1)
    return [FRONT_ANGLE + gap_angle * 0.5 + step * index for index in range(segments)]


def _side_segment(angles, target):
    """target 角度に最も近い分割番号(袖ぐりを開ける位置を決めるのに使う)"""
    def gap(index):
        difference = (angles[index] - target) % (2.0 * math.pi)
        return min(difference, 2.0 * math.pi - difference)

    return min(range(len(angles)), key=gap)


def build_bodice(part, table, scale):
    """ブラウスの胴。**首の穴と肩線を別のリングにする**のが要点(定義 #1)。

        リング0: 襟ぐり(小さい・肩より少し高い)  ← 上端の境界がそのまま襟ぐりになる
        リング1: 肩線(長半径 = 肩幅/2)           ← 0→1 の帯が肩ヨーク
        リング2..: バスト → 裾                    ← 1→2 以降に袖ぐりの穴を開ける

    前の版は上端を1本にしたので、そのリングが襟ぐりと肩線を兼ねてしまい肩が作れなかった。
    新しいカーネルは要らず、リングの並べ方だけの問題だった。
    """
    params = part["params"]
    height = table["height"]
    shoulder_z = params["shoulder_z"] * height

    segments = params["segments"]
    body_rings = params["rings"]
    body_depth = table["depth_ratio"]

    # 襟ぐり: 首の**素寸** + 襟ぐり固有のゆとり。胴のゆとり(ease)は首には掛けない
    # (バストを 12% 出すために首まで 12% 広げるとボートネックになる)
    neck_perimeter = table["body"]["neck"] * (1.0 + params["neck_ease"])
    neck_semi = modulate.ellipse_semi_major(neck_perimeter, params["neck_depth_ratio"])

    # 肩線: 長半径がそのまま肩幅の半分(定義 #3 をここで満たす)
    shoulder_semi = table["shoulder_width"] / 2.0

    # 胴: 肩の下でバストへ、ウエストで少し絞り、そこから裾へ
    bust_semi = modulate.ellipse_semi_major(table["bust"], body_depth)
    waist_semi = modulate.ellipse_semi_major(table["bust"] * params["waist_scale"], body_depth)
    hem_semi = modulate.ellipse_semi_major(table["bust"] * params["hem_scale"], body_depth)

    # 前開きの隙間は**幅で**指定する。分割数の副産物にすると、分割数を変えた
    # だけで前立てからはみ出す(28分割で 3.3cm あり、幅 3.0cm の前立てで隠れなかった)。
    # 隙間の幅は x 方向の差し渡しなので、**いちばん太いリング**で指定幅になるよう
    # 角度を決める。そこが隙間の最大値になり、前立てが覆う相手になる
    front_gap = params["front_gap"] * height
    widest_semi = max(neck_semi, shoulder_semi, bust_semi, waist_semi, hem_semi)
    gap_angle = 2.0 * math.asin(min(0.9, front_gap / (2.0 * widest_semi)))
    angles = _front_open_angles(segments, gap_angle)

    # 肩傾斜(定義 #9): 側頸点は肩先より「水平の渡り × tan(傾斜)」だけ高い
    slope = math.radians(table["shoulder_slope_degrees"])
    shoulder_drop = max(0.0, shoulder_semi - neck_semi) * math.tan(slope)
    snp_z = shoulder_z + shoulder_drop

    # 前下がり(定義 #8): 襟ぐりは水平の輪ではない。前が深く後ろが浅い
    scale_drop = params["neck_drop_scale"]
    front_drop = table["front_neck_drop"] * scale_drop
    back_drop = table["back_neck_drop"] * scale_drop
    drops = [
        modulate.neckline_drop(angle, front_drop, back_drop, FRONT_ANGLE) for angle in angles
    ]

    # 着丈(定義 #7): 側頸点から下へ。製品実寸表から取る(勘で置いた hem_z が
    # クロップ丈の原因だった。経緯は docs/garments.md)
    garment_length = table["garment_length"] * params["length_scale"]
    hem_z = snp_z - garment_length
    if hem_z >= shoulder_z:
        raise PartError(
            "着丈(%.3fm)が短すぎて裾が肩線より上に来ます" % (garment_length,)
        )

    span = shoulder_z - hem_z
    spacing = span / (body_rings - 1)
    bust_t = max(0.0, min(1.0, params["bust_t"]))
    waist_t = max(bust_t, min(1.0, params["waist_t"]))

    # 襟ぐりの落差は下へ行くほど消える(でないと裾までうねる)
    fade_span = max(1e-6, params["neck_fade"] * height)

    def fade(z_base):
        return 1.0 - modulate.smoothstep(min(1.0, (shoulder_z - z_base) / fade_span))

    def body_semi(t):
        """軸方向 t での長半径と断面の厚み比。肩 → バスト → ウエスト → 裾"""
        if t <= bust_t and bust_t > 0.0:
            local = modulate.smoothstep(t / bust_t)
            return (
                shoulder_semi + (bust_semi - shoulder_semi) * local,
                params["shoulder_depth_ratio"]
                + (body_depth - params["shoulder_depth_ratio"]) * local,
            )
        if t <= waist_t and waist_t > bust_t:
            local = modulate.smoothstep((t - bust_t) / (waist_t - bust_t))
            return bust_semi + (waist_semi - bust_semi) * local, body_depth
        base = waist_semi if waist_t > bust_t else bust_semi
        local = 0.0 if waist_t >= 1.0 else (t - waist_t) / (1.0 - waist_t)
        return base + (hem_semi - base) * modulate.smoothstep(local), body_depth

    # リング0 = 襟ぐり、リング1 = 肩線、リング2.. = バスト→ウエスト→裾。
    # 0 と 1 は落差をそのまま持つので、その間の帯が「前下がりに沿った肩ヨーク」になる
    # 各行は (分割ごとのz, 前中心のz, 長半径, 断面の厚み比)。
    # 前中心の z は前立てが乗る面を出すのに使う(頂点はそこに無いので式から出す)
    rows = [
        (
            [snp_z - drop for drop in drops],
            snp_z - front_drop,
            neck_semi,
            params["neck_depth_ratio"],
        ),
        (
            [shoulder_z - drop for drop in drops],
            shoulder_z - front_drop,
            shoulder_semi,
            params["shoulder_depth_ratio"],
        ),
    ]
    for index in range(1, body_rings):
        t = index / (body_rings - 1)
        semi, depth = body_semi(t)
        base = shoulder_z - t * span
        weight = fade(base)
        rows.append(
            (
                [base - drop * weight for drop in drops],
                base - front_drop * weight,
                semi,
                depth,
            )
        )

    ring_points = [
        kernels.ring_from_polar(
            [semi * scale] * segments,
            angles,
            [z * scale for z in zs],
            depth_ratio=depth,
        )
        for zs, _front_z, semi, depth in rows
    ]

    # 袖ぐり: 肩線の直下(リング1と2の間)から、袖ぐり深さのところまで(定義 #4)
    armhole_depth = table["armhole_depth"]
    armhole_rows = max(1, min(int(round(armhole_depth / spacing)), body_rings - 2))
    armhole_segments = max(1, params["armhole_segments"])
    skip = set()
    centres = {}
    for label, target in (("l", 0.0), ("r", math.pi)):
        centre = _side_segment(angles, target)
        centres[label] = centre
        for row in range(1, 1 + armhole_rows):
            for offset in range(
                -(armhole_segments // 2), armhole_segments - armhole_segments // 2
            ):
                skip.add((row, (centre + offset) % segments))

    mesh = kernels.loft_rings(
        ring_points, name=part["name"], closed=False, tubular=True, skip_faces=skip
    )
    mesh.material = part["material"]
    mesh.design = {
        # 襟ぐりは前下がりで水平ではないので、上端の実周長は設計の楕円より長い。
        # 「首に合うか」は**水平に投影した周長**で見る(下の neck_perimeter)
        "top_perimeter": None,
        "bottom_perimeter": table["bust"] * params["hem_scale"] * scale,
        "bottom_fit_perimeter": table["bust"] * params["hem_scale"] * scale,
        "bottom_perimeter_note": None,
        "top_z": snp_z * scale,
        "bottom_z": hem_z * scale,
        "length": None,  # 襟ぐりが肩より上にあるので z 幅と着丈は一致しない
        "depth_ratio_top": params["neck_depth_ratio"],
        "depth_ratio_bottom": body_depth,
        "radial_modulations": [],
        "pleats": 0,
        "pleat_depth": 0.0,
        "boundary_loops": 3,  # 外周(襟ぐり+前開き+裾)1本 + 袖ぐり2本
        "boundary_verts": None,
        # --- 定義 #1〜#6 を検証するための設計値 ---
        "neck_perimeter": neck_perimeter * scale,
        "shoulder_width": table["shoulder_width"] * scale,
        "shoulder_z": shoulder_z * scale,
        "armhole_depth": armhole_depth * scale,
        "shoulder_span": (table["shoulder_width"] / 2.0 - neck_semi) * scale,
        "sleeve_length": table["sleeve_length"] * scale,
        # --- 定義 #7〜#9(形) ---
        "garment_length": garment_length * scale,
        # 検証はここではなく**製図の値そのもの**と突き合わせる。spec の
        # neck_drop_scale を 0 にすると設計値も 0 になり、設計値と比べる形の
        # ゲートは「0 と 0 が一致する」で通ってしまう(前下がりが消えても落ちない)
        "front_neck_drop": table["front_neck_drop"] * scale,
        "back_neck_drop": table["back_neck_drop"] * scale,
        "built_front_neck_drop": front_drop * scale,
        "shoulder_slope_degrees": table["shoulder_slope_degrees"],
        # 前立てとボタンが乗る前中心の面。(z, 前面の y) を上から下へ
        "front_profile": [
            (front_z * scale, -semi * depth * scale)
            for _zs, front_z, semi, depth in rows
        ],
        # 前開きの隙間の最大幅。前立てはこれより広くないと隙間が見える(定義 #13)
        "front_gap_width": max(
            2.0 * semi * math.sin(gap_angle * 0.5) for _zs, _front_z, semi, _depth in rows
        )
        * scale,
    }
    mesh.design["armholes"] = _armhole_frames(ring_points, centres, segments, skip)
    from . import validate as validate_module

    for label, loop in validate_module.armhole_loops(mesh).items():
        mesh.rings["armhole_" + label] = loop["indices"]
    # 肩線リングは定義 #3 の検証に使う
    mesh.rings["shoulder"] = list(range(segments, 2 * segments))
    return mesh


def _armhole_frames(ring_points, centres, segments, skip):
    """袖ぐりごとの中心・半径・伸ばす向きを、実際に開いた穴の座標から求める"""
    rows = sorted({ring for ring, _segment in skip})
    frames = {}
    for label, centre in centres.items():
        used = []
        for ring in rows + [rows[-1] + 1]:
            for offset in (-1, 0, 1):
                index = (centre + offset) % segments
                used.append(ring_points[ring][index])
        cx = sum(point[0] for point in used) / len(used)
        cy = sum(point[1] for point in used) / len(used)
        cz = sum(point[2] for point in used) / len(used)
        frames[label] = {
            "centre": (cx, cy, cz),
            "outward": (1.0 if label == "l" else -1.0, 0.0, 0.0),
        }
    return frames


def build_sleeve(part, table, scale, host=None):
    """袖。**上端リングを袖ぐりの境界ループそのものにする**(定義 #6 と袖山)。

    前の版は上端を円のリングにしていたので、矩形に抜いた袖ぐりの穴の上に円が浮き、
    隙間が見えて袖山も無かった。穴の頂点をそのまま使えば継ぎ目は定義上ゼロになり、
    そこから数リングかけて円へ寄せる過程がそのまま袖山(cap)になる。

    そのため**袖の周方向分割数は袖ぐりの境界頂点数で決まる**(spec では指定しない)。
    """
    if host is None:
        raise PartError("sleeve は attach_to で胴パーツを指定してください(袖ぐりの実寸が必要)")
    params = part["params"]
    side = params["side"]
    ring_name = "armhole_" + side
    indices = host.rings.get(ring_name)
    if not indices:
        raise PartError(
            "%s に %r がありません(あるのは %s)"
            % (host.name, ring_name, ", ".join(sorted(host.rings)))
        )

    loop = [host.verts[index] for index in indices]
    centre = tuple(sum(point[axis] for point in loop) / len(loop) for axis in range(3))

    droop = math.radians(params["droop_degrees"])
    sign = 1.0 if side == "l" else -1.0
    direction = (sign * math.cos(droop), 0.0, -math.sin(droop))
    right, up = kernels.orthonormal_frame(direction)

    # 穴の各頂点を「袖の軸まわりの角度・半径・軸方向のずれ」に分解する
    polar = []
    for point in loop:
        offset = tuple(point[axis] - centre[axis] for axis in range(3))
        u = sum(offset[axis] * right[axis] for axis in range(3))
        v = sum(offset[axis] * up[axis] for axis in range(3))
        axial = sum(offset[axis] * direction[axis] for axis in range(3))
        polar.append({"angle": math.atan2(v, u), "radius": math.hypot(u, v), "axial": axial})
    # 角度順に並べ替える(穴は軸から見て星型なので巡回順は変わらない)。
    # loft_rings は反時計回りのリングを積むと法線が外を向く
    polar.sort(key=lambda item: item["angle"])

    armhole_perimeter = sum(
        _distance3(loop[index], loop[index - 1]) for index in range(len(loop))
    )
    seam_length = armhole_perimeter * (1.0 + params["sleeve_ease"])
    # 袖幅は**サイズ表から**取る。メッシュに開けた袖ぐりは矩形なので周長が実物の
    # 袖ぐり寸法より3割ほど大きく、そこから出すと袖がコウモリ袖に膨らむ
    bicep_perimeter = table["bicep"] * params["bicep_scale"] * scale
    bicep_radius = bicep_perimeter / (2.0 * math.pi)
    cuff_radius = table["cuff"] * scale * params["cuff_scale"] / (2.0 * math.pi)

    # 袖丈は**肩先から袖口まで**(定義 #6)。筒は袖ぐりの重心から伸ばすので、
    # 「肩先からの距離が袖丈になる軸長」を解く。重心から袖丈ぶん伸ばすと
    # 肩先→袖口が袖丈を超える(袖ぐりの重心は肩先より内側かつ下にある)
    shoulder_point = (
        sign * table["shoulder_width"] / 2.0 * scale,
        0.0,
        host.design["shoulder_z"],
    )
    target = table["sleeve_length"] * params["length_scale"] * scale
    gap = tuple(centre[axis] - shoulder_point[axis] for axis in range(3))
    along = sum(gap[axis] * direction[axis] for axis in range(3))
    radial = sum(value * value for value in gap) - along * along
    discriminant = target * target - radial
    if discriminant <= 0.0:
        raise PartError(
            "袖丈(%.3f)が短すぎて肩先から袖口まで届きません(袖ぐりまでで %.3f)"
            % (target, math.sqrt(max(0.0, radial)))
        )
    length = math.sqrt(discriminant) - along
    if length <= 0.0:
        raise PartError("袖丈(%.3f)が短すぎます(袖ぐりより内側で終わります)" % (target,))

    rings = params["rings"]
    cap = max(1e-6, min(0.9, params["cap_fraction"]))
    elbow = max(cap, min(0.95, params["elbow_fraction"]))
    cuff_start = min(0.999, max(elbow + 1e-3, params["cuff_start"]))
    ts = modulate.axis_fractions(rings)

    # 袖山では**角度も**等間隔へ寄せる。半径だけ寄せると、袖ぐりの穴の不揃いな
    # 角度が袖口まで残って「円のつもりの多角形」になり、袖口の周長が設計より
    # 数%短くなる(袖ぐりを狭くすると許容差を超えて落ちた)
    step = 2.0 * math.pi / len(polar)
    for index, item in enumerate(polar):
        item["even_angle"] = polar[0]["angle"] + step * index

    ring_points = []
    for t in ts:
        blend = modulate.smoothstep(min(1.0, t / cap))  # 穴の形 → 円
        # 全長を滑らかに細めると針のような円錐になる(定義 #11)。
        # 肘まで二の腕の太さを保ち、そこから絞り、最後は一定 = カフス(定義 #12)
        target_radius = modulate.sleeve_radius_profile(
            t, bicep_radius, cuff_radius, elbow, cuff_start
        )
        axial_plane = t * length
        points = []
        for item in polar:
            radius = item["radius"] + (target_radius - item["radius"]) * blend
            angle = item["angle"] + (item["even_angle"] - item["angle"]) * blend
            axial = item["axial"] * (1.0 - blend) + axial_plane
            u = radius * math.cos(angle)
            v = radius * math.sin(angle)
            points.append(
                tuple(
                    centre[axis] + right[axis] * u + up[axis] * v + direction[axis] * axial
                    for axis in range(3)
                )
            )
        ring_points.append(points)

    mesh = kernels.loft_rings(ring_points, name=part["name"], closed=True, tubular=False)
    mesh.material = part["material"]
    mesh.design = {
        "top_perimeter": armhole_perimeter,
        "bottom_perimeter": 2.0 * math.pi * cuff_radius,
        "bottom_fit_perimeter": 2.0 * math.pi * cuff_radius,
        "bottom_perimeter_note": None,
        "top_z": centre[2],
        "bottom_z": centre[2] - math.sin(droop) * length,
        "length": None,  # 斜めなので z 幅と袖丈は一致しない
        "depth_ratio_top": 1.0,
        "depth_ratio_bottom": 1.0,
        "radial_modulations": [],
        "pleats": 0,
        "pleat_depth": 0.0,
        "boundary_loops": 2,
        "boundary_verts": len(polar),
        # 定義 #6: 肩先から袖の下端までが袖丈
        "shoulder_point": shoulder_point,
        "sleeve_length_target": table["sleeve_length"] * params["length_scale"] * scale,
        "armhole_perimeter": armhole_perimeter,
        "seam_length": seam_length,
        "sleeve_ease": params["sleeve_ease"],
        # --- 定義 #10〜#12(形) ---
        "droop_degrees": params["droop_degrees"],
        "bicep_perimeter": bicep_perimeter,
        "cuff_perimeter": table["cuff"] * params["cuff_scale"] * scale,
        # 二の腕の太さを保っていなければならない区間(袖山の下〜**肘**)のリング番号。
        # 上限は spec の elbow_fraction ではなく解剖の位置 SLEEVE_ELBOW_T。
        # ここを spec 側の値にすると、テーパーを早く始めた瞬間に区間も一緒に
        # 縮んでゲートが逃げ、円錐が通ってしまう
        "hold_rings": _hold_ring_indices(ts, cap, modulate.SLEEVE_ELBOW_T),
        # カフスの帯(一定半径)に入るリング番号。2本以上必要
        "cuff_rings": [index for index, t in enumerate(ts) if t >= cuff_start],
    }
    return mesh


def _distance3(a, b):
    return math.sqrt(sum((a[axis] - b[axis]) ** 2 for axis in range(3)))


def _hold_ring_indices(ts, cap, elbow_t):
    """袖山の下から肘までのリング番号。リングが粗くて区間に入らないときは肘に一番近い1本"""
    inside = [index for index, t in enumerate(ts) if cap <= t <= elbow_t]
    if inside:
        return inside
    return [min(range(len(ts)), key=lambda index: abs(ts[index] - elbow_t))]


def build_collar(part, table, scale, host=None):
    """立ち襟。**下端リングを胴の襟ぐりそのものにする**(袖と同じ手口)。

    前の版は水平な輪を base_z に置いていたので、前下がりの襟ぐりから浮いて
    煙突に見えた。襟ぐりの頂点をそのまま使えば継ぎ目はゼロで、前下がりにも自動で乗る。
    """
    if host is None:
        raise PartError("collar は attach_to で胴パーツを指定してください(襟ぐりの実物が必要)")
    params = part["params"]
    height = table["height"]
    collar_height = params["height_ratio"] * height * scale
    indices = host.rings.get("top")
    if not indices:
        raise PartError("%s に襟ぐり(top)がありません" % (host.name,))

    seam = [host.verts[index] for index in indices]
    cx = sum(point[0] for point in seam) / len(seam)
    cy = sum(point[1] for point in seam) / len(seam)

    rings = params["rings"]
    ts = modulate.axis_fractions(rings)
    # 上ほど外へ開く(立ち襟は首から少し離れる)。リングは上から下へ並べる
    ring_points = []
    for t in ts:
        up = 1.0 - t  # 1 = 襟の上端、0 = 襟ぐりの縫い目
        widen = 1.0 + (params["flare"] - 1.0) * up
        ring_points.append(
            [
                (
                    cx + (point[0] - cx) * widen,
                    cy + (point[1] - cy) * widen,
                    point[2] + collar_height * up,
                )
                for point in seam
            ]
        )

    mesh = kernels.loft_rings(ring_points, name=part["name"], closed=False, tubular=True)
    mesh.material = part["material"]
    seam_length = sum(
        _distance3(seam[index], seam[index - 1]) for index in range(1, len(seam))
    )
    mesh.design = {
        "top_perimeter": None,  # 前下がりに沿うので水平な輪ではない
        "bottom_perimeter": None,
        "bottom_fit_perimeter": None,
        "bottom_perimeter_note": "襟ぐりの実物に乗るので周長は胴側で検算する",
        "top_z": max(point[2] for point in ring_points[0]),
        "bottom_z": min(point[2] for point in seam),
        "length": None,
        "depth_ratio_top": None,
        "depth_ratio_bottom": None,
        "radial_modulations": [],
        "pleats": 0,
        "pleat_depth": 0.0,
        # 前が開いた短い筒 = 外周が1本の輪
        "boundary_loops": 1,
        "boundary_verts": None,
        "collar_seam_length": seam_length,
        "collar_height": collar_height,
    }
    return mesh


def build_collar_fall(part, table, scale, host=None):
    """襟の羽根(折り返し)。台襟の上端リングから**下へ・外へ**降りる。

    立ち襟(台襟)だけだと板が立っているだけで、シャツの襟には見えない。
    実物は台襟の上で折り返して羽根が肩へ倒れている。

    **別パーツにしたのは法線の都合。** ロフトは「リングを上から下へ積む」前提で
    巻き方向を決めているので、台襟(上へ)と羽根(下へ)を1本の鎖に繋ぐと
    前半の面が裏返る。折り返しの位置で切って2パーツにすれば、どちらも上から下へ積める。
    """
    if host is None:
        raise PartError("collar_fall は attach_to で台襟を指定してください")
    indices = host.rings.get("top")
    if not indices:
        raise PartError("%s に折り返し位置(top)がありません" % (host.name,))
    params = part["params"]
    fold = [host.verts[index] for index in indices]
    cx = sum(point[0] for point in fold) / len(fold)
    cy = sum(point[1] for point in fold) / len(fold)
    drop = params["height_ratio"] * table["height"] * scale

    ring_points = []
    for t in modulate.axis_fractions(params["rings"]):
        widen = 1.0 + (params["flare"] - 1.0) * t
        ring_points.append(
            [
                (
                    cx + (point[0] - cx) * widen,
                    cy + (point[1] - cy) * widen,
                    point[2] - drop * t,
                )
                for point in fold
            ]
        )

    mesh = kernels.loft_rings(ring_points, name=part["name"], closed=False, tubular=True)
    mesh.material = part["material"]
    mesh.design = {
        "top_perimeter": None,
        "bottom_perimeter": None,
        "bottom_fit_perimeter": None,
        "bottom_perimeter_note": "台襟の上端に乗るので周長は台襟側で決まる",
        "top_z": max(point[2] for point in fold),
        "bottom_z": min(point[2] for point in ring_points[-1]),
        "length": None,
        "depth_ratio_top": None,
        "depth_ratio_bottom": None,
        "radial_modulations": [],
        "pleats": 0,
        "pleat_depth": 0.0,
        "boundary_loops": 1,
        "boundary_verts": None,
        "fall_drop": drop,
        "fall_flare": params["flare"],
    }
    return mesh


def build_placket(part, table, scale, host=None):
    """前立て。前中心を縦に走る帯(定義 #13)。

    前を1分割ぶん空けるだけでは**細いスリット**にしかならない。実物のシャツは
    別布の帯が前中心に重なって付いていて、そこにボタンが並ぶ。
    胴の前面プロファイル(z, y)に沿わせるので、胴の断面を変えても剥がれない。
    """
    if host is None:
        raise PartError("placket は attach_to で胴パーツを指定してください(前面の実寸が必要)")
    profile = (host.design or {}).get("front_profile")
    if not profile:
        raise PartError("%s に front_profile がありません" % (host.name,))

    params = part["params"]
    height = table["height"]
    width = params["width"] * height * scale
    standoff = params["standoff"] * height * scale
    columns = max(3, params["columns"])
    half = width * 0.5
    # 帯はわずかに丸く張り出す(平らな板は貼り紙に見える)
    bulge = params["bulge"] * width

    top_z = profile[0][0] + params["top_extend"] * height * scale
    bottom_z = profile[-1][0]

    def front_y(z):
        """胴の前面の y を z で線形補間する(プロファイルは上から下へ並んでいる)"""
        if z >= profile[0][0]:
            return profile[0][1]
        for index in range(1, len(profile)):
            upper_z, upper_y = profile[index - 1]
            lower_z, lower_y = profile[index]
            if lower_z <= z <= upper_z:
                if upper_z == lower_z:
                    return lower_y
                local = (upper_z - z) / (upper_z - lower_z)
                return upper_y + (lower_y - upper_y) * local
        return profile[-1][1]

    # 縦の刻みは**胴の前面プロファイルと同じ z** にする。等間隔に刻むと、
    # 前面の y が急に動く区間(襟ぐり→肩線で4.5cmの間に5cm前へ出る)を
    # 前立て側が直線で跨いでしまい、胴の中へ食い込んで交差の検査に落ちる。
    # 袖の周方向分割数を袖ぐりから決めたのと同じで、相手の実物に合わせる
    heights = [z for z, _y in profile if bottom_z <= z <= top_z]
    if top_z - heights[0] > 1e-12:
        heights.insert(0, top_z)
    if len(heights) < 2:
        raise PartError("前立てを張る高さが足りません: %r" % (heights,))

    ring_points = []
    for z in heights:
        base_y = front_y(z) - standoff
        points = []
        for column in range(columns):
            u = column / (columns - 1)  # 0 = +X 側、1 = -X 側
            x = half - width * u
            # 中央がいちばん前に出る
            points.append((x, base_y - bulge * math.sin(math.pi * u), z))
        ring_points.append(points)

    mesh = kernels.loft_rings(
        ring_points, name=part["name"], closed=False, tubular=False
    )
    mesh.material = part["material"]
    mesh.design = {
        "top_perimeter": None,
        "bottom_perimeter": None,
        "bottom_fit_perimeter": None,
        "bottom_perimeter_note": None,
        "top_z": top_z,
        "bottom_z": bottom_z,
        "length": None,
        "depth_ratio_top": None,
        "depth_ratio_bottom": None,
        "radial_modulations": [],
        "pleats": 0,
        "pleat_depth": 0.0,
        "boundary_loops": 1,  # 開いたシート = 外周1本
        "boundary_verts": None,
        # --- 定義 #13 ---
        "placket_width": width,
        "placket_top_z": top_z,
        "placket_bottom_z": bottom_z,
        "placket_front_y": min(point[1] for ring in ring_points for point in ring),
        # 覆う相手。これより狭いと前開きが黒い筋として見える
        "front_gap_width": (host.design or {}).get("front_gap_width"),
    }
    return mesh


def build_buttons(part, table, scale, host=None):
    """ボタン列(定義 #14)。前立ての上に等間隔で並ぶ小さなドーム。

    1パーツに全個ぶんのシェルを入れる(spec にボタンを1個ずつ書かせない)。
    """
    if host is None:
        raise PartError("buttons は attach_to で前立てを指定してください")
    design = host.design or {}
    if not design.get("placket_width"):
        raise PartError("%s は前立てではありません(placket_width が無い)" % (host.name,))

    params = part["params"]
    count = params["count"]
    radius = params["radius"] * table["height"] * scale
    zs = modulate.button_positions(
        design["placket_top_z"],
        design["placket_bottom_z"],
        count,
        params["top_inset"],
        params["bottom_inset"],
    )
    # 前立ての表面よりわずかに前。触れさせると面が交差して不合格になる
    base_y = design["placket_front_y"] - params["standoff"] * table["height"] * scale
    segments = max(6, params["segments"])
    angles = modulate.circle_angles(segments)

    verts, quads, uv_loops = [], [], []
    for z in zs:
        # ドーム: 外周 → 中ほど(前へ) → ほぼ中心(いちばん前)
        profile = ((1.0, 0.0), (0.62, 0.38), (0.14, 0.52))
        shell = [
            [
                (
                    radius * factor * math.cos(angle),
                    base_y - radius * depth,
                    z + radius * factor * math.sin(angle),
                )
                for angle in angles
            ]
            for factor, depth in profile
        ]
        piece = kernels.loft_rings(shell, name=part["name"], closed=True, tubular=False)
        offset = len(verts)
        verts.extend(piece.verts)
        quads.extend(tuple(index + offset for index in quad) for quad in piece.quads)
        uv_loops.extend(piece.uv_loops)

    mesh = kernels.PartMesh(
        name=part["name"],
        verts=verts,
        quads=quads,
        uv_loops=uv_loops,
        rings={},
        ring_size=segments,
        ring_count=3 * count,
        tubular=False,
        material=part["material"],
    )
    mesh.design = {
        "top_perimeter": None,
        "bottom_perimeter": None,
        "bottom_fit_perimeter": None,
        "bottom_perimeter_note": None,
        "top_z": max(zs) + radius,
        "bottom_z": min(zs) - radius,
        "length": None,
        "depth_ratio_top": None,
        "depth_ratio_bottom": None,
        "radial_modulations": [],
        "pleats": 0,
        "pleat_depth": 0.0,
        "boundary_loops": 2 * count,  # ボタン1個につき 外周 + 中心の小穴
        "boundary_verts": None,
        # --- 定義 #14 ---
        "button_count": count,
        "button_zs": list(zs),
        "button_radius": radius,
        "placket_width": design["placket_width"],
        "placket_top_z": design["placket_top_z"],
        "placket_bottom_z": design["placket_bottom_z"],
    }
    return mesh


BUILDERS = {
    "skirt_body": build_skirt_body,
    "waistband": build_waistband,
    "bodice": build_bodice,
    "sleeve": build_sleeve,
    "collar": build_collar,
    "collar_fall": build_collar_fall,
    "placket": build_placket,
    "buttons": build_buttons,
}

#: 他のパーツの**実物**から作るパーツ。build_all が依存順を保証する。
#: 継ぎ目や位置を計算し直さずに相手の頂点をそのまま使うので、ずれが原理的に起きない
DEPENDENT_TYPES = frozenset(
    ("sleeve", "collar", "collar_fall", "placket", "buttons")
)

# spec が知っているパーツ種別と、ここで作れる種別がずれていないことを import 時に確かめる
_MISSING = sorted(set(spec_module.PART_SCHEMAS) - set(BUILDERS))
if _MISSING:  # pragma: no cover - 実装漏れの検出用
    raise ImportError("parts.BUILDERS に実装が無いパーツ種別があります: %s" % ", ".join(_MISSING))


def build_all(normalized_spec):
    """正規化済み spec からパーツ一式を組み立てて返す。

    戻り値: {"parts": [PartMesh, ...], "sizing": サイズ表, "scale": unit変換係数}
    """
    table = sizing_module.resolve(
        normalized_spec["assumed_height"],
        normalized_spec.get("sizing"),
        normalized_spec["ease"],
    )
    # メートルで計算して最後に unit へ直す(1 unit = meters_per_unit メートル)
    scale = 1.0 / normalized_spec["meters_per_unit"]

    # 依存パーツは相手の**実物**から作るので、依存の解けたものから順に組む。
    # ボタンは前立てに、前立て・襟・袖は胴に付くので二段の依存がある
    pending = list(normalized_spec["parts"])
    meshes = []
    built = {}
    while pending:
        progressed = False
        for part in list(pending):
            builder = BUILDERS.get(part["type"])
            if builder is None:  # pragma: no cover - spec 側で弾かれている
                raise PartError("未知のパーツ種別です: %r" % (part["type"],))
            if part["type"] in DEPENDENT_TYPES:
                host = built.get(part.get("attach_to"))
                if host is None:
                    continue
                mesh = builder(part, table, scale, host=host)
            else:
                mesh = builder(part, table, scale)
            built[mesh.name] = mesh
            meshes.append(mesh)
            pending.remove(part)
            progressed = True
        if not progressed:
            raise PartError(
                "attach_to の依存が解けません(相手が無いか循環しています): %s"
                % ", ".join(
                    "%s→%r" % (part["name"], part.get("attach_to")) for part in pending
                )
            )

    # spec に書かれた順に戻す(レポートの並びを spec と揃える)
    order = {part["name"]: index for index, part in enumerate(normalized_spec["parts"])}
    meshes.sort(key=lambda mesh: order.get(mesh.name, len(order)))

    by_name = {mesh.name: mesh for mesh in meshes}
    for joint in normalized_spec["joints"]:
        # 縫い合わせ(sewn)は分割数が違ってよい。縫製でも袖山にはいせ込みが入る
        if joint.get("kind", "shared") != "shared":
            continue
        a, b = by_name[joint["a"]], by_name[joint["b"]]
        if a.ring_size != b.ring_size:
            raise PartError(
                "接合するパーツの周方向分割数が違います: %s=%d, %s=%d"
                % (a.name, a.ring_size, b.name, b.ring_size)
            )

    return {"parts": meshes, "sizing": table, "scale": scale}
