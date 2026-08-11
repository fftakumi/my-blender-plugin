"""パーツカタログ。spec の1パーツ → PartMesh。bpy 非依存。

衣装 = 裁縫パーツの集合。パーツごとに独立して作って独立して検証できるので、
不合格になったときにどのパーツのどのパラメータが原因か分かる。
新しい衣装は「新しいコード」ではなく「新しいパーツの組み合わせ」で作る。

`design` に入れる期待値は、**プロファイル計算を通さない最短経路**で出す
(例: 上端周長 = サイズ表のウエスト寸法そのもの)。プロファイル側にバグが入ったとき
設計値と実物がずれて検証で落ちるようにするため。同じ計算を2回書いて突き合わせても
自己整合しか確かめられない。
"""

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
                modulate.pleat_taper(t, params["pleat_start"]) for t in profile["t"]
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


BUILDERS = {
    "skirt_body": build_skirt_body,
    "waistband": build_waistband,
}

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

    meshes = []
    for part in normalized_spec["parts"]:
        builder = BUILDERS.get(part["type"])
        if builder is None:  # pragma: no cover - spec 側で弾かれている
            raise PartError("未知のパーツ種別です: %r" % (part["type"],))
        meshes.append(builder(part, table, scale))

    by_name = {mesh.name: mesh for mesh in meshes}
    for joint in normalized_spec["joints"]:
        a, b = by_name[joint["a"]], by_name[joint["b"]]
        if a.ring_size != b.ring_size:
            raise PartError(
                "接合するパーツの周方向分割数が違います: %s=%d, %s=%d"
                % (a.name, a.ring_size, b.name, b.ring_size)
            )

    return {"parts": meshes, "sizing": table, "scale": scale}
