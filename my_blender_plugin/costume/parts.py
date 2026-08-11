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


def _front_open_angles(segments):
    """前中心に開き口が来るように角度を並べる。

    loft_rings(closed=False) は最後の分割と最初の分割の間に面を作らないので、
    その隙間が前中心に来るよう半ステップずらして始める。
    """
    step = 2.0 * math.pi / segments
    return [FRONT_ANGLE + step * (0.5 + index) for index in range(segments)]


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
    hem_z = params["hem_z"] * height
    if hem_z >= shoulder_z:
        raise PartError(
            "hem_z(%.3f) は shoulder_z(%.3f) より下にしてください"
            % (params["hem_z"], params["shoulder_z"])
        )

    segments = params["segments"]
    body_rings = params["rings"]
    angles = _front_open_angles(segments)
    body_depth = table["depth_ratio"]

    # 襟ぐり: 首回り + ゆとり。肩より少し高い位置に置く(肩の下がりぶん)
    neck_perimeter = table["neck"] * (1.0 + params["neck_ease"])
    neck_semi = modulate.ellipse_semi_major(neck_perimeter, params["neck_depth_ratio"])
    neck_z = shoulder_z + params["shoulder_slope"] * height

    # 肩線: 長半径がそのまま肩幅の半分(定義 #3 をここで満たす)
    shoulder_semi = table["shoulder_width"] / 2.0

    # 胴: 肩の下でバストへ、そこから裾へ
    bust_semi = modulate.ellipse_semi_major(table["bust"], body_depth)
    hem_semi = modulate.ellipse_semi_major(table["bust"] * params["hem_scale"], body_depth)
    span = shoulder_z - hem_z
    spacing = span / (body_rings - 1)
    bust_t = max(0.0, min(1.0, params["bust_t"]))

    rows = [(neck_z, neck_semi, params["neck_depth_ratio"])]
    rows.append((shoulder_z, shoulder_semi, params["shoulder_depth_ratio"]))
    for index in range(1, body_rings):
        t = index / (body_rings - 1)
        if t <= bust_t and bust_t > 0.0:
            local = modulate.smoothstep(t / bust_t)
            semi = shoulder_semi + (bust_semi - shoulder_semi) * local
            depth = params["shoulder_depth_ratio"] + (
                body_depth - params["shoulder_depth_ratio"]
            ) * local
        else:
            local = 0.0 if bust_t >= 1.0 else (t - bust_t) / (1.0 - bust_t)
            semi = bust_semi + (hem_semi - bust_semi) * modulate.smoothstep(local)
            depth = body_depth
        rows.append((shoulder_z - t * span, semi, depth))

    ring_points = [
        kernels.ring_from_polar(
            [semi * scale] * segments, angles, z * scale, depth_ratio=depth
        )
        for z, semi, depth in rows
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
        "top_perimeter": neck_perimeter * scale,
        "bottom_perimeter": table["bust"] * params["hem_scale"] * scale,
        "bottom_fit_perimeter": table["bust"] * params["hem_scale"] * scale,
        "bottom_perimeter_note": None,
        "top_z": neck_z * scale,
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
    """袖。**袖ぐりの実寸から**作る(寸法表の別項目からは作らない)。

    host は袖ぐりを持つ胴パーツ。袖山の周長 = 袖ぐりの周長 × (1 + ゆとり)。
    縫製では袖山に 10% 前後のいせ込みを入れるので、その関係をそのまま使う。
    """
    if host is None:
        raise PartError(
            "sleeve は attach_to で胴パーツを指定してください(袖ぐりの実寸が必要)"
        )
    params = part["params"]
    side = params["side"]
    frames = host.design.get("armholes") or {}
    frame = frames.get(side)
    if frame is None:
        raise PartError(
            "%s に側 %r の袖ぐりがありません(あるのは %s)"
            % (host.name, side, ", ".join(sorted(frames)))
        )

    armhole = validate_armhole_perimeter(host, side)
    seam_length = armhole * (1.0 + params["sleeve_ease"])
    top_perimeter = armhole * params["bicep_scale"]
    cuff_perimeter = table["cuff"] * scale * params["cuff_scale"]
    length = table["sleeve_length"] * params["length_scale"] * scale

    droop = math.radians(params["droop_degrees"])
    sign = 1.0 if side == "l" else -1.0
    direction = (sign * math.cos(droop), 0.0, -math.sin(droop))
    right, up = kernels.orthonormal_frame(direction)

    segments, rings = params["segments"], params["rings"]
    angles = modulate.circle_angles(segments)
    ts = modulate.axis_fractions(rings)
    origin = frame["centre"]

    ring_points = []
    for t in ts:
        perimeter = top_perimeter + (cuff_perimeter - top_perimeter) * modulate.smoothstep(t)
        semi_major = modulate.ellipse_semi_major(perimeter, 1.0)
        centre = tuple(origin[axis] + direction[axis] * length * t for axis in range(3))
        ring_points.append(ring_on_frame_wrapper(semi_major, angles, centre, right, up))

    mesh = kernels.loft_rings(ring_points, name=part["name"], closed=True, tubular=False)
    mesh.material = part["material"]
    mesh.design = {
        "top_perimeter": top_perimeter,
        "bottom_perimeter": cuff_perimeter,
        "bottom_fit_perimeter": cuff_perimeter,
        "bottom_perimeter_note": None,
        "top_z": origin[2],
        "bottom_z": origin[2] - math.sin(droop) * length,
        "length": None,  # 斜めなので z 方向の伸びと丈は一致しない
        "depth_ratio_top": 1.0,
        "depth_ratio_bottom": 1.0,
        "radial_modulations": [],
        "pleats": 0,
        "pleat_depth": 0.0,
        "boundary_loops": 2,
        "boundary_verts": segments,
        "armhole_perimeter": armhole,
        "sleeve_ease": params["sleeve_ease"],
        "seam_length": seam_length,
    }
    return mesh


def ring_on_frame_wrapper(semi_major, angles, centre, right, up):
    return kernels.ring_on_frame([semi_major] * len(angles), angles, centre, right, up)


def validate_armhole_perimeter(host, side):
    """胴に開いた袖ぐりの実周長。袖はこの値から作る"""
    from . import validate as validate_module

    loops = validate_module.armhole_loops(host)
    if side not in loops:
        raise PartError("%s の袖ぐりを特定できませんでした" % host.name)
    return loops[side]["perimeter"]


def build_collar(part, table, scale):
    """立ち襟。首まわりの短い開いた筒(前が開いている)"""
    params = part["params"]
    height = table["height"]
    base_z = params["base_z"] * height
    collar_height = params["height_ratio"] * height
    segments, rings = params["segments"], params["rings"]
    angles = _front_open_angles(segments)
    ts = modulate.axis_fractions(rings)

    neck = table["neck"]
    depth_ratio = params["depth_ratio"]
    ring_points = []
    for t in ts:
        # 上に向かってわずかに開く(立ち襟は首から少し離れる)
        perimeter = neck * (1.0 + (params["flare"] - 1.0) * (1.0 - t))
        semi_major = modulate.ellipse_semi_major(perimeter, depth_ratio)
        ring_points.append(
            kernels.ring_from_polar(
                [semi_major * scale] * segments,
                angles,
                (base_z + collar_height * (1.0 - t)) * scale,
                depth_ratio=depth_ratio,
            )
        )

    mesh = kernels.loft_rings(ring_points, name=part["name"], closed=False, tubular=True)
    mesh.material = part["material"]
    mesh.design = {
        "top_perimeter": neck * params["flare"] * scale,
        "bottom_perimeter": neck * scale,
        "bottom_fit_perimeter": neck * scale,
        "bottom_perimeter_note": None,
        "top_z": (base_z + collar_height) * scale,
        "bottom_z": base_z * scale,
        "length": collar_height * scale,
        "depth_ratio_top": depth_ratio,
        "depth_ratio_bottom": depth_ratio,
        "radial_modulations": [],
        "pleats": 0,
        "pleat_depth": 0.0,
        # 前が開いた短い筒 = 外周が1本の輪
        "boundary_loops": 1,
        "boundary_verts": None,
    }
    return mesh


BUILDERS = {
    "skirt_body": build_skirt_body,
    "waistband": build_waistband,
    "bodice": build_bodice,
    "sleeve": build_sleeve,
    "collar": build_collar,
}

#: 他のパーツの実寸を必要とするパーツ(袖は袖ぐりから作る)。build_all が順番を保証する
DEPENDENT_TYPES = frozenset(("sleeve",))

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

    # 袖は袖ぐりの実寸から作るので、依存しないパーツを先に組む
    ordered = sorted(
        normalized_spec["parts"], key=lambda part: part["type"] in DEPENDENT_TYPES
    )
    meshes = []
    built = {}
    for part in ordered:
        builder = BUILDERS.get(part["type"])
        if builder is None:  # pragma: no cover - spec 側で弾かれている
            raise PartError("未知のパーツ種別です: %r" % (part["type"],))
        if part["type"] in DEPENDENT_TYPES:
            host_name = part.get("attach_to")
            host = built.get(host_name)
            if host is None:
                raise PartError(
                    "%s の attach_to が %r ですが、そのパーツがありません(あるのは %s)"
                    % (part["name"], host_name, ", ".join(sorted(built)))
                )
            mesh = builder(part, table, scale, host=host)
        else:
            mesh = builder(part, table, scale)
        built[mesh.name] = mesh
        meshes.append(mesh)

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
