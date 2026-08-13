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

    腰穿きの衣装では下端がウエストではないので、`waist_drop`(ウエストからの落差)
    を渡してその高さの胴回りで作る。**pants 側と同じ式**なので、接合の一致は
    waist_drop が 0 でなくても計算で保証される。
    """
    params = part["params"]
    height = table["height"]
    band_height = params["height"] * height
    waist_z = params["waist_z"] * height
    flare = params["flare"]
    bottom_perimeter = modulate.follow_measure(
        1.0,
        params["waist_drop"] * height,
        table["waist"],
        table["hip"],
        table["hip_drop"],
        1.0,
    )

    profile = modulate.band_profile(
        rings=params["rings"],
        bottom_perimeter=bottom_perimeter,
        flare=flare,
        depth_ratio=table["depth_ratio"],
    )
    angles = modulate.circle_angles(params["segments"])
    z_per_ring = [waist_z + band_height * (1.0 - t) for t in profile["t"]]

    # バンドは折り目もドレープも入れない(縫い付けられていて動かない)
    mesh = _tube(part["name"], profile, z_per_ring, angles, [], scale, part["material"])
    mesh.design = {
        "top_perimeter": bottom_perimeter / flare * scale,
        "bottom_perimeter": bottom_perimeter * scale,
        "bottom_fit_perimeter": bottom_perimeter * scale,
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


#: ケープの首→肩線の落差 /H。**設計値**(bodice の肩傾斜と釣り合う値)。
#: 検証の肩ゲートが見る位置なので spec には持たせない(SLEEVE_ELBOW_T と同じ理屈)
CAPE_SHOULDER_DROP = 0.045
#: 肩を覆う布の半幅 / 肩幅。**設計値**(肩の上に布が少しかぶる)
CAPE_SHOULDER_COVER = 0.55


def build_cape(part, table, scale):
    """ケープ。首まわり→**肩の張り**→裾へ広がる、前の開いたシート。

    定義は docs/garments.md。bodice の builder は使わない — bodice は design に
    shoulder_width 等を入れるのでブラウス固有のゲート(シャツテール・胸のふくらみ・
    製品着丈)が発火する。ケープはケープの design キーだけを申告する。

    **ただの円錐はスカートと区別が付かない**(ブラインド識別のテストで実証)。
    首から肩線までの短い区間で幅を肩幅まで一気に広げ、そこから flare で裾へ
    広げる「ハンガー型」のプロファイルにする。肩の折れがケープをケープに見せる。

    リングは水平のまま(前下がりは入れない)。上端の弧長は「首回り×(1+ゆとり)の
    周長から前開きの楔ぶんを除いた長さ」= 製図の値として design に入れ、実測と
    突き合わせる。断面は体と同じ厚み比の楕円で一定(肩に掛かる布なので)。
    """
    params = part["params"]
    height = table["height"]
    length = params["length"] * height
    neck_z = params["neck_z"] * height
    flare = params["flare"]
    gap_angle = math.radians(params["front_open_degrees"])

    neck_perimeter = table["neck"] * (1.0 + params["neck_ease"])
    angles = _front_open_angles(params["segments"], gap_angle)
    depth_ratio = table["depth_ratio"]
    # 上端の弧長の製図値。楕円は弧長が角度に比例しない(前面の弧が濃い)ので
    # 「周長×角度割合」ではなく楕円弧を数値的に積む。前開きの楔は前中心の周り
    top_semi = modulate.ellipse_semi_major(neck_perimeter, depth_ratio)
    top_arc = modulate.ellipse_arc(
        top_semi,
        depth_ratio,
        FRONT_ANGLE + gap_angle * 0.5,
        FRONT_ANGLE + 2.0 * math.pi - gap_angle * 0.5,
    )

    # 肩線: 首の下 CAPE_SHOULDER_DROP、半幅は肩幅 × CAPE_SHOULDER_COVER(製図値)
    shoulder_semi = table["shoulder_width"] * CAPE_SHOULDER_COVER
    shoulder_perimeter = modulate.ellipse_perimeter(shoulder_semi, depth_ratio)
    t_shoulder = min(0.5, CAPE_SHOULDER_DROP * height / length)

    def perimeter_at(t):
        if t <= t_shoulder:
            local = modulate.smoothstep(t / t_shoulder)
            return neck_perimeter + (shoulder_perimeter - neck_perimeter) * local
        local = (t - t_shoulder) / (1.0 - t_shoulder)
        return shoulder_perimeter * modulate.flare_multiplier(
            local, flare, params["flare_curve"]
        )

    ring_points = []
    hem_semi = None
    for t in modulate.axis_fractions(params["rings"]):
        semi = modulate.ellipse_semi_major(perimeter_at(t), depth_ratio)
        hem_semi = semi
        ring_points.append(
            kernels.ring_from_polar(
                [semi * scale] * params["segments"],
                angles,
                (neck_z - t * length) * scale,
                depth_ratio=depth_ratio,
            )
        )

    mesh = kernels.loft_rings(ring_points, name=part["name"], closed=False, tubular=True)
    mesh.material = part["material"]
    mesh.design = {
        # 開いた弧なので閉ループ用の周長検算は使わない(cape_* の弧長で見る)
        "top_perimeter": None,
        "bottom_perimeter": None,
        "bottom_fit_perimeter": None,
        "bottom_perimeter_note": "前が開いた弧なので cape_top_arc / cape_flare で検算する",
        "top_z": neck_z * scale,
        "bottom_z": (neck_z - length) * scale,
        "length": length * scale,
        "depth_ratio_top": depth_ratio,
        "depth_ratio_bottom": depth_ratio,
        "radial_modulations": [],
        "pleats": 0,
        "pleat_depth": 0.0,
        # 前が開いたシート = 外周が1本の輪
        "boundary_loops": 1,
        "boundary_verts": None,
        # ---- ケープ固有(validate 側のゲートが発火する)----
        "cape_top_arc": top_arc * scale,
        # 裾弧長 / 上端弧長 の期待値。弧の角度割合は全リング同じなので周長比と一致
        "cape_flare": perimeter_at(1.0) / neck_perimeter,
        # 裾の開き幅(前開き端点間の距離)。端点は前中心 ±gap/2 にあるので
        # x 成分が支配的で、弦長 ≈ 2·半径·sin(gap/2)
        "cape_hem_gap": 2.0 * hem_semi * math.sin(gap_angle * 0.5) * scale,
    }
    # 肩の張りの設計値は**肩線に最も近いリング行へスナップ**して出す。
    # 理論位置(t_shoulder)のままだと、格子1個ぶんのずれに flare の成長が乗って
    # 合法な spec で誤検知した(PR #8 レビュー: rings=20 / flare=3.0 / length=0.1)。
    # 検証側は同じ行を測るので格子誤差が消える(パンツの thigh_target と同じ手口)
    rings_count = params["rings"]
    row = min(
        range(rings_count),
        key=lambda index: abs(index / (rings_count - 1) - t_shoulder),
    )
    t_row = row / (rings_count - 1)
    if abs(t_row - t_shoulder) <= 0.35 * t_shoulder:
        mesh.design["cape_shoulder_t"] = t_row
        mesh.design["cape_shoulder_span"] = (
            2.0 * modulate.ellipse_semi_major(perimeter_at(t_row), depth_ratio) * scale
        )
    else:
        # リング割りが粗くて肩線の近くに行が無い。黙って消さず warn 側で知らせる
        mesh.design["cape_shoulder_t"] = t_shoulder
        mesh.design["cape_shoulder_span"] = None
    return mesh


def build_hood(part, table, scale, host=None):
    """フード。host(cape / bodice)の首の縫い目 = top リングから頭を包む。

    定義は docs/garments.md。リング0を host の頂点そのものにするので継ぎ目はゼロ
    (collar と同じ手口)。上へ行くほど:
    - 半径: 首 → 頭囲(ゆとり込み)へ膨らみ、taper_start から先端へ絞る
      (頂点数を変えられないので、キャップは張らず先端の小さな弧で終える)
    - 前の開口: host の前開き角から face_open_degrees へ、角度の再配置で広げる
    - 中心: 後ろへ逃がす(頭は首の真上ではなく少し後ろにある)
    """
    if host is None:
        raise PartError("hood は attach_to で首の縫い目を持つパーツを指定してください")
    indices = host.rings.get("top")
    if not indices:
        raise PartError("%s に首の縫い目(top)がありません" % (host.name,))
    params = part["params"]

    seam = [host.verts[index] for index in indices]
    count = len(seam)
    if count < 4:
        raise PartError("首の縫い目の頂点が少なすぎます: %d" % (count,))
    cx = sum(point[0] for point in seam) / count
    cy = sum(point[1] for point in seam) / count

    # 縫い目を前中心まわりの極座標に分解。host は前開きの弧なので、
    # 前中心からの相対角に直せば [gap/2, 2π-gap/2] の単調列になる
    seam_rel = []
    for x, y, z in seam:
        relative = (math.atan2(y - cy, x - cx) - FRONT_ANGLE) % (2.0 * math.pi)
        seam_rel.append((relative, math.hypot(x - cx, y - cy), z))
    host_gap = seam_rel[0][0] + (2.0 * math.pi - seam_rel[-1][0])
    neck_radius = sum(radius for _rel, radius, _z in seam_rel) / count
    seam_z_top = max(point[2] for point in seam)

    head_radius = (
        table["head_circumference"] * (1.0 + params["head_ease"]) / (2.0 * math.pi) * scale
    )
    hood_height = table["head_height"] * params["height_scale"] * scale
    face_gap = math.radians(params["face_open_degrees"])
    taper_start = params["taper_start"]

    rings = params["rings"]
    ring_points = []
    for index in range(rings):
        t = index / (rings - 1)
        if index == 0:
            ring_points.append(list(seam))
            continue
        grow = modulate.smoothstep(min(1.0, t / params["blend"]))
        gap_t = host_gap + (face_gap - host_gap) * modulate.smoothstep(t)
        step = (2.0 * math.pi - gap_t) / (count - 1)
        radius_t = neck_radius + (head_radius - neck_radius) * grow
        if t > taper_start:
            shrink = modulate.smoothstep((t - taper_start) / (1.0 - taper_start))
            radius_t *= 1.0 - (1.0 - params["tip_ratio"]) * shrink
        center_y = cy - params["back_shift"] * head_radius * t
        points = []
        for j, (rel, seam_radius, seam_z) in enumerate(seam_rel):
            target_rel = gap_t * 0.5 + step * j
            rel_j = rel + (target_rel - rel) * grow
            radius_j = seam_radius + (radius_t - seam_radius) * grow
            angle = FRONT_ANGLE + rel_j
            z = seam_z + (seam_z_top - seam_z) * grow + hood_height * t
            points.append(
                (cx + radius_j * math.cos(angle), center_y + radius_j * math.sin(angle), z)
            )
        ring_points.append(points)

    # リングは下(縫い目)から上(先端)へ積んだので、巻き方向の規約
    # (上から下へ)に合わせて逆順で渡す
    mesh = kernels.loft_rings(
        list(reversed(ring_points)), name=part["name"], closed=False, tubular=False
    )
    mesh.material = part["material"]
    mesh.design = {
        "top_perimeter": None,
        "bottom_perimeter": None,
        "bottom_fit_perimeter": None,
        "bottom_perimeter_note": "首の縫い目の実物に乗るので周長は host 側で検算する",
        "top_z": max(point[2] for point in ring_points[-1]),
        "bottom_z": min(point[2] for point in seam),
        "length": None,  # 高さは hood_depth で見る(縫い目の前下がりが z 全幅に混ざるため)
        "depth_ratio_top": None,
        "depth_ratio_bottom": None,
        "radial_modulations": None,  # 中心が後ろへ逃げるので周期成分の検査は掛けられない
        "pleats": 0,
        "pleat_depth": 0.0,
        "boundary_loops": 1,
        "boundary_verts": None,
        # ---- フード固有(validate 側のゲートが発火する)----
        # 頭を包むこと: 一番太いリングの弧長の下限(頭囲×ゆとりの3/4。設計値)
        "hood_arc_floor": table["head_circumference"]
        * (1.0 + params["head_ease"])
        * 0.75
        * scale,
        # 縫い目の上端からフードの頂までの高さ(設計値)
        "hood_depth": hood_height,
        # 顔の開口の幅の下限: 設計の開口弦長の半分(開いていることの確認)
        "hood_face_gap_floor": head_radius * math.sin(face_gap * 0.5),
    }
    return mesh


#: 脚の分岐→円の移行に要るリング行数(leg_rings × blend の下限)。
#: これを割ると移行が1行で終わり、折り返しに近い折れ目ができる。
#: **層1では捕まらない**(面は交差していない)ので生成側で弾く
LEG_BLEND_MIN_ROWS = 1.5

#: 脚ぐりを持ち上げた(leg_line > 0)ときの「ブリッジの辺 / 外周の辺」の許容範囲。
#: 分岐リングの辺の長さがここから外れると脚の縁が胴を突き抜ける。
#: **実測**。上限は層1(preset の他を固定して segments 20-44 × crotch_segments 1-24 を
#: 総当たり): 自己交差 0 は比 2.29 まで、2.46 以上で落ちる。
#: 下限は**層2(厚みを付けたシェル)**が決める — 層1は比 0.49 まで通るのに、
#: 厚み 1.3mm を付けると 0.72(segments 44)/ 0.65(segments 32)で股が折れて交差する。
#: 通ったのは 0.78 以上。安全側に 0.80 とした(LEG_BLEND_MIN_ROWS と同じ性質の下限)。
#: 水平に切った脚口(leg_line = 0)では縁が真下を向くので出ない → そちらには掛けない
LEG_LINE_BRIDGE_RATIO = (0.80, 2.20)


def build_pants(part, table, scale):
    """パンツ。腰からは1本の筒、股からは2本の脚(定義は docs/garments.md)。

    分岐は K1(リング列のロフト)では表現できない唯一の形。胴(腰→股)・左脚・
    右脚を別々に K1 でロフトし、K2(kernels.weld_meshes)で溶接する。
    股リングの半周と股の縫い目(ブリッジ)の頂点は**同じタプルのまま**両脚に渡す
    (再計算すると誤差で溶接できず、duplicate_verts / nonmanifold で落ちる)。
    """
    params = part["params"]
    height = table["height"]
    segments = params["segments"]
    if segments % 4 != 0:  # pragma: no cover - spec 側で弾かれている
        raise PartError("pants の segments は4の倍数にしてください: %d" % (segments,))

    waist_z_m = params["waist_z"] * height
    # 上端が自然なウエストからどれだけ下がっているか。腰穿きの衣装はここが 0 でない。
    # プロファイルを**体の高さで**読むために要る値で、0 なら従来どおり
    # 「上端 = ウエスト」として読む
    waist_drop_m = params["waist_drop"] * height
    # 股の位置は体で決まっているので、上端を下げたぶん股上は短くなる
    rise_m = (table["rise"] - waist_drop_m) * params["rise_scale"]
    if rise_m <= 0.0:
        raise PartError(
            "waist_drop(%.3f)がサイズ表の股上(%.3f /H)以上なので、"
            "上端が股より下に来ます" % (params["waist_drop"], table["rise"] / height)
        )
    inseam = table["inseam"] * params["inseam_scale"] * scale
    thigh = table["thigh"] * (1.0 + params["thigh_ease"]) * scale
    knee = table["knee"] * params["knee_scale"] * scale
    hem = table["hem_opening"] * params["hem_scale"] * scale

    # 胴(腰→股): スカートと同じ「ウエスト→ヒップに沿う」プロファイル。広がりは無し。
    #
    # ただしリング単位ではなく**頂点単位**で読む。脚ぐりを脇へ持ち上げる(leg_line)と
    # 下端が水平な輪ではなくなるので、同じリングの中でも頂点ごとに「上端からどれだけ
    # 下がったか」が変わるため。u_bottom[j] がその頂点の下端(1.0 = 股まで届く)で、
    # 行 t の頂点は u = t × u_bottom[j] の位置になる。leg_line=0 なら u = t で、
    # skirt_profile(flare=1)をリング単位に読んだ従来と**同じ座標**になる
    # (flare_multiplier(t,1,1) は常に 1.0 なので掛け算が入らない)
    depth_ratio = table["depth_ratio"]
    angles = modulate.circle_angles(segments)
    leg_line = params["leg_line"]
    u_bottom = [
        1.0 - leg_line * modulate.leg_line_shape(angle, FRONT_ANGLE) for angle in angles
    ]

    def body_perimeter(u):
        """上端から u(股上に対する比)だけ下がった高さの、体の胴回り。

        **ウエストからの落差で読む**(上端からではない)。上端から読むと
        `follow_measure` の smoothstep が上端で傾き 0 から始まるので、
        腰骨に乗る衣装では「そこだけ体に沿っていない垂直な筒」になり、
        ウエストバンドとの境目に稜線が出る(脇で 17° の折れ目として見つけた)。
        """
        return modulate.follow_measure(
            1.0,
            waist_drop_m + u * rise_m,
            table["waist"],
            table["hip"],
            table["hip_drop"],
            1.0,
        )

    def body_semi(u):
        return modulate.ellipse_semi_major(body_perimeter(u), depth_ratio) * scale

    # 尻の張り出し。周方向の分布は seat_shape、高さ方向は「ウエストで 0 → ヒップで
    # 満額 → 以降は保つ」。**周長と同じ follow_measure を 0→1 で使い回している**ので、
    # 尻が最大になる高さと胴が一番太くなる高さが定義から一致する
    seat = params["seat"]
    seat_angles = [modulate.seat_shape(angle, FRONT_ANGLE) for angle in angles]

    def seat_profile(u):
        """尻の張り出しの高さ方向の分布。**上端で 0**、ヒップまでの距離で満額。

        胴回りと違ってウエストからの落差では読まない — 上端で 0 でないと、
        尻の無いウエストバンドとの接合(リング共有)が開く。腰穿きだと
        股でも満額に届かないので、検証の設計値は下の seat_at_crotch で合わせる
        """
        return modulate.follow_measure(u, rise_m, 0.0, 1.0, table["hip_drop"], 1.0)

    def seat_offset(u, index, semi):
        if seat <= 0.0:
            return 0.0
        return semi * depth_ratio * seat * seat_angles[index] * seat_profile(u)

    def body_point(u, index):
        """角度 index・深さ u の体の表面。脚ぐりの縁を体に沿わせるのに使う"""
        semi = body_semi(u)
        return (
            semi * math.cos(angles[index]),
            semi * depth_ratio * math.sin(angles[index]) + seat_offset(u, index, semi),
        )

    torso_rings = []
    for t in modulate.axis_fractions(params["hip_rings"]):
        us = [t * bottom for bottom in u_bottom]
        semis = [body_semi(u) for u in us]
        ring = kernels.ring_from_polar(
            semis,
            angles,
            [(waist_z_m - u * rise_m) * scale for u in us],
            depth_ratio=depth_ratio,
        )
        torso_rings.append(
            [
                (x, y + seat_offset(u, index, semi), z)
                for index, ((x, y, z), u, semi) in enumerate(zip(ring, us, semis))
            ]
        )
    torso = kernels.loft_rings(torso_rings, name=part["name"], closed=True, tubular=True)

    # 股リングを前後中心で割る。segments%4==0 なので前中心・後ろ中心に頂点が必ずある。
    # leg_line を入れると下端リングは水平でなくなるが、前中心・後ろ中心は
    # leg_line_shape が 0 になる位置なので**そこだけは股の高さのまま**で、
    # ブリッジ(マチ)は leg_line に関係なく水平に渡る
    crotch = torso_rings[-1]
    crotch_z = (waist_z_m - rise_m) * scale
    front = _side_segment(angles, FRONT_ANGLE)
    back = _side_segment(angles, math.pi / 2.0)

    # 股の縫い目(ブリッジ): 前中心 → 後ろ中心を x=0 の直線で渡る
    crotch_segments = params["crotch_segments"]
    bridge = [
        tuple(
            crotch[front][axis]
            + (crotch[back][axis] - crotch[front][axis]) * step / crotch_segments
            for axis in range(3)
        )
        for step in range(1, crotch_segments)
    ]

    # 脚ぐりを持ち上げると、分岐リングの**辺の長さがブリッジと外周で揃っていない**
    # ことが自己交差になって出る。脚のロフトは「頂点を番号順に円周へ等間隔に送る」
    # ので、片側だけ細かいと送り先が実際の位置から大きくずれ、縁が胴の壁を突き抜ける。
    # 水平に切った脚口(ズボン)では縁が真下を向くので、ずれても抜けない
    if leg_line > 0.0:
        outer = [
            crotch[(front + step) % segments] for step in range(segments // 2 + 1)
        ]
        outer_edge = sum(
            _distance3(outer[index], outer[index + 1]) for index in range(len(outer) - 1)
        ) / (len(outer) - 1)
        bridge_edge = _distance3(crotch[front], crotch[back]) / crotch_segments
        ratio = bridge_edge / outer_edge
        low, high = LEG_LINE_BRIDGE_RATIO
        if not low <= ratio <= high:
            wanted = _distance3(crotch[front], crotch[back]) / outer_edge
            raise PartError(
                "マチの分割が周方向と釣り合っていません"
                "(ブリッジの辺 / 外周の辺 = %.2f、許容 %.2f〜%.2f)。"
                "crotch_segments を %d〜%d にしてください"
                % (ratio, low, high, math.ceil(wanted / high), int(wanted / low))
            )

    def torso_arc_indices(start, stop):
        indices = [start]
        while indices[-1] != stop:
            indices.append((indices[-1] + 1) % segments)
        return indices

    def torso_arc(start, stop):
        return [crotch[index] for index in torso_arc_indices(start, stop)]

    # 分岐リング(どちらも +z から見て反時計回り = ロフトで法線が外向き)。
    # 左脚(x>0): 前中心→(+x側)→後ろ中心 + ブリッジを後ろ→前へ。
    # 右脚(x<0): 後ろ中心→(-x側)→前中心 + ブリッジを前→後ろへ。
    # ブリッジの辺を両脚が逆向きに辿るので、溶接後の巻き方向が揃う
    left_ring = torso_arc(front, back) + list(reversed(bridge))
    right_ring = torso_arc(back, front) + list(bridge)
    # 分岐リングの頂点が胴のどの列(角度)から来たか。None はブリッジの点。
    # 脚ぐりの縁を体に沿って下ろすのに使う
    left_cols = torso_arc_indices(front, back) + [None] * len(bridge)
    right_cols = torso_arc_indices(back, front) + [None] * len(bridge)

    leg_rings_count = params["leg_rings"]
    blend = params["blend"]
    # 分岐リング→円の移行が**1行で終わると折り返しに近い折れ目**になる。
    # 層1は通る(面同士は交差していない)が、厚みを付けた瞬間にシェルが交差する。
    # 実測: rings×blend が 1.2 では落ち、1.5 で通った(股下の長さには依存しない)
    if leg_rings_count * blend < LEG_BLEND_MIN_ROWS:
        raise PartError(
            "脚の分岐から円への移行が急すぎます(leg_rings %d × blend %.2f = %.2f < %.1f)。"
            "leg_rings を増やすか blend を上げてください"
            % (leg_rings_count, blend, leg_rings_count * blend, LEG_BLEND_MIN_ROWS)
        )

    def leg_loft(ring0, anchor, cols):
        """anchor = 位相の基準にする頂点の番号(= 前中心)。

        円周へ送る先は「番号順に等間隔」なので、**どの頂点を基準にするかで
        左右の脚の位相が変わる**。左は分岐リングの先頭が前中心、右は先頭が
        後ろ中心なので、素直に先頭を基準にすると左右が鏡像にならない。
        脚が円のうちは位相がずれても面は同じ形なので見えなかったが、
        脚ぐりが曲線になると**そのまま左右非対称の形になって出る**。
        前中心は両方のリングにある幾何的な目印なので、そこを基準にすれば
        左右の位相が定義から鏡像になる(前中心は x=0 上にあり、
        左右の重心から見た角度がちょうど補角になる)。
        """
        count = len(ring0)
        cx = sum(point[0] for point in ring0) / count
        cy = sum(point[1] for point in ring0) / count
        side_sign = 1.0 if cx >= 0.0 else -1.0
        # 極分解は「各頂点を円周上のどこへ送るか」の順序決めに使う。
        # リングは CCW なので unwrap すれば角度は増える一方
        unwrapped = []
        previous = None
        for x, y, _z in ring0:
            angle = math.atan2(y - cy, x - cx)
            if previous is not None:
                while angle < previous - math.pi:
                    angle += 2.0 * math.pi
                while angle > previous + math.pi:
                    angle -= 2.0 * math.pi
            unwrapped.append(angle)
            previous = angle
        start_angle = unwrapped[anchor] - 2.0 * math.pi * anchor / count
        thigh_r = thigh / (2.0 * math.pi)
        knee_r = knee / (2.0 * math.pi)
        hem_r = hem / (2.0 * math.pi)
        # 脚の内側の壁が x=0 を跨ぐと反対の脚と貫通する(自己交差ゲートで落ちた)。
        # 円の中心を「半径 + 隙間」だけ体側へ逃がして、内壁を前後中心面の手前に保つ
        inner_gap = 0.05 * thigh_r
        # 円へ寄せる量を頂点ごとに持つ。**マチでは全部寄せ、脇では寄せない**。
        # 脚ぐりを持ち上げると脇の縁は体の細い高さに来るのに、円は太ももの太さの
        # ままなので、全周を寄せると脇だけ円が胴の輪郭より外へ出て角が飛び出す。
        # マチ側は円へ寄る動きが股を塞ぐ布そのものなので、そこは寄せ続ける
        # (leg_line = 0 では全頂点が 1.0 = 従来どおり)
        span = leg_line * rise_m * scale
        pull = [
            1.0 - min(1.0, max(0.0, (z0 - crotch_z) / span)) if span > 0.0 else 1.0
            for _x, _y, z0 in ring0
        ]
        # 円へ寄せない頂点(脇)の行き先。**真下ではなく体に沿って下ろす。**
        # 真下に下ろすと、体が広がり続けている高さで急に垂直になって
        # 脚ぐりの縁に折れ目が出る(脇で 18°)。体に沿わせれば胴の続きになる
        # その頂点の深さ u(0 = 上端, 1 = 股)と、縁 1 行ぶんで下がる量
        u_at = [1.0 - (z0 - crotch_z) / (rise_m * scale) for _x, _y, z0 in ring0]
        du = inseam / (rise_m * scale)
        rings_points = [list(ring0)]
        for index in range(1, leg_rings_count + 1):
            t = index / leg_rings_count
            grow = modulate.smoothstep(min(1.0, t / blend))
            target_radius = modulate.leg_radius_profile(t, thigh_r, knee_r, hem_r)
            centre_x = side_sign * max(abs(cx), target_radius + inner_gap)
            points = []
            for j, (x0, y0, z0) in enumerate(ring0):
                even = start_angle + 2.0 * math.pi * j / count
                tx = centre_x + target_radius * math.cos(even)
                ty = cy + target_radius * math.sin(even)
                weight = grow * pull[j]
                bx, by = x0, y0
                # 体に沿わせる量は**円へ寄せる量のちょうど裏返し**にする。
                # grow の残りにすると、円へ全部寄せるべきマチでもブレンドの
                # 途中行で体に引っ張られ、厚みを付けたとき股で折れて交差した
                follow = 1.0 - pull[j]
                if follow > 0.0 and cols[j] is not None:
                    # 体に沿って du×t ぶん下がった位置(u は 1.0 = 股で止める)
                    fx, fy = body_point(min(1.0, u_at[j] + du * t), cols[j])
                    bx, by = x0 + (fx - x0) * follow, y0 + (fy - y0) * follow
                # z は**頂点ごとに**分岐リングから真下へ下ろす。全頂点を同じ z に
                # 置くと leg_line で持ち上げた脚ぐりが1行で水平に潰れる
                points.append(
                    (bx + (tx - bx) * weight, by + (ty - by) * weight, z0 - inseam * t)
                )
            rings_points.append(points)
        return kernels.loft_rings(
            rings_points, name=part["name"], closed=True, tubular=False
        )

    # 前中心の位置: 左のリングは前中心から始まり、右のリングは後ろ中心から始まって
    # 外周を辿り切ったところが前中心(外周の点数 = segments//2 + 1)
    left = leg_loft(left_ring, 0, left_cols)
    right = leg_loft(right_ring, segments // 2, right_cols)
    mesh, maps = kernels.weld_meshes([torso, left, right], name=part["name"])
    mesh.material = part["material"]
    torso_map, left_map, right_map = maps

    # 溶接後は添字計算が使えないので、検証に使うリングをここで登録する
    mesh.rings["top"] = [torso_map[index] for index in torso.rings["top"]]
    half = segments // 2 + 1  # 分岐リングの外周部分の頂点数
    leg_size = len(left_ring)
    mesh.rings["crotch"] = [torso_map[index] for index in torso.rings["bottom"]] + [
        left_map[position] for position in range(half, leg_size)
    ]
    mesh.rings["hem_l"] = [left_map[index] for index in left.rings["bottom"]]
    mesh.rings["hem_r"] = [right_map[index] for index in right.rings["bottom"]]

    # thigh の検証リング: ブレンドが終わって完全な円になった行。太もも保持区間
    # (THIGH_HOLD_T)内の最後の行が第一候補。保持区間に行が無い粗い割りでは、
    # 円になった最初の行で測り、**設計値もその行のプロファイル値にする**
    # (行が無いからと黙ってゲートを消すと「針の脚」の網に穴が開く。PR #8 レビュー)
    thigh_row = None
    for index in range(1, leg_rings_count + 1):
        t = index / leg_rings_count
        if t >= blend and t <= modulate.THIGH_HOLD_T:
            thigh_row = index
    if thigh_row is None:
        for index in range(1, leg_rings_count + 1):
            if index / leg_rings_count >= blend:
                thigh_row = index
                break
    thigh_target = None
    if thigh_row is not None:
        thigh_target = 2.0 * math.pi * modulate.leg_radius_profile(
            thigh_row / leg_rings_count,
            thigh / (2.0 * math.pi),
            knee / (2.0 * math.pi),
            hem / (2.0 * math.pi),
        )
        for label, mapping in (("l", left_map), ("r", right_map)):
            mesh.rings["thigh_" + label] = [
                mapping[position]
                for position in range(thigh_row * leg_size, (thigh_row + 1) * leg_size)
            ]

    mesh.design = {
        # 上端は「その高さの体の胴回り」。waist_drop = 0 ならウエスト周そのもの
        "top_perimeter": body_perimeter(0.0) * scale,
        "bottom_perimeter": None,
        "bottom_fit_perimeter": None,
        "bottom_perimeter_note": "裾は2本あるので pants_hem_l / pants_hem_r で見る",
        "top_z": waist_z_m * scale,
        "bottom_z": crotch_z - inseam,
        "length": None,  # 腰〜裾は rise+inseam に分けて検証する
        "depth_ratio_top": depth_ratio,
        "depth_ratio_bottom": None,
        "radial_modulations": None,  # 溶接後の形なので周期成分の検査は掛けられない
        "pleats": 0,
        "pleat_depth": 0.0,
        "boundary_loops": 3,  # 腰 1 + 裾 2
        "boundary_verts": None,
        # ---- パンツ固有(validate 側のゲートが発火する)----
        "pants_rise": rise_m * scale,
        "pants_inseam": inseam,
        "pants_hem": hem,
        # 脚ぐりの脇とマチの高さの差。0 なら「水平に切った脚口」の検査になる
        # (キーを消すのではなく 0 を入れる — 消すとズボン側の網に穴が開く)
        "pants_leg_line": leg_line * rise_m * scale,
        # 股リングの「後ろの厚み / 前の厚み」。1.0 なら前後対称の楕円(ズボン)。
        # 股での張り出しは seat_profile のぶん満額に届かないことがあるので、
        # 設計値も同じ関数で出す(そうしないとゲートが甘くなる)
        "pants_seat_ratio": 1.0 + seat * seat_profile(1.0),
    }
    if thigh_target is not None:
        mesh.design["pants_thigh"] = thigh_target
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

#: 胸のふくらみの上側の半幅の下限(t)。襟ぐりで止めても、これより狭くすると
#: 幅の無い稜線になって別の不自然さが出る
BUST_AXIAL_MIN = 0.06


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


def _front_arc_angles(segments, span_angle):
    """前中心を中心に span_angle ぶんだけ並べた角度(前面だけを覆うシート用)。

    `_front_open_angles` の裏返し。あちらは前中心に**隙間**を空けて後ろに布を回すが、
    こちらは前中心に**布**を置いて左右へ回り込ませる(前垂れ・エプロン)。
    """
    if not 0.0 < span_angle < 2.0 * math.pi:
        raise PartError("前面シートの角度幅が範囲外です: %r" % (span_angle,))
    step = span_angle / (segments - 1)
    return [FRONT_ANGLE - span_angle * 0.5 + step * index for index in range(segments)]


def build_apron(part, table, scale):
    """前スカート(旧スクの前垂れ)。腰から前面だけを覆って垂れる一枚布。

    **これがスク水をレオタードから分ける。** 紺の一体型 + 胸の名札まで作っても、
    ブラインド識別では「ボディスーツ/レオタード」と読まれた(docs のスク水の節)。
    旧スクの見た目の署名は前面のフラップなので、パーツとして足す。

    形は skirt_body と同じ「体に沿う→裾で広がる」プロファイルを、前中心を中心とした
    弧の範囲でだけ張ったもの。下に着ている水着の面と重ならないよう standoff で外へ逃がす。
    """
    params = part["params"]
    height = table["height"]
    waist_z = params["waist_z"] * height
    length = params["length"] * height
    # **奇数にする。** 偶数だと前中心を2頂点が跨いで、いちばん前に出る点が中心から
    # ずれる(ゼッケンの columns と同じ理由)。前垂れは前中心が要の布なので揃える
    segments = max(3, params["segments"]) | 1
    span_angle = math.radians(params["span_degrees"])
    depth_ratio = table["depth_ratio"]
    standoff = params["standoff"] * height

    # 下の水着と同じ「ウエスト→ヒップに沿う」プロファイル。同じ関数・同じ寸法で
    # 出すので、standoff を足したぶんだけ確実に外側に来る(貫通しない)
    profile = modulate.skirt_profile(
        rings=params["rings"],
        length=length,
        waist_perimeter=table["waist"],
        hip_perimeter=table["hip"],
        hip_drop=table["hip_drop"],
        hip_hug=1.0,
        flare=params["flare"],
        flare_curve=params["flare_curve"],
        depth_ratio=depth_ratio,
    )
    angles = _front_arc_angles(segments, span_angle)
    ring_points = [
        kernels.ring_from_polar(
            [(semi + standoff) * scale] * segments,
            angles,
            (waist_z - t * length) * scale,
            depth_ratio=depth,
        )
        for semi, depth, t in zip(
            profile["semi_major"], profile["depth_ratio"], profile["t"]
        )
    ]

    mesh = kernels.loft_rings(ring_points, name=part["name"], closed=False, tubular=True)
    mesh.material = part["material"]
    # 上端の弧長の製図値。楕円は弧長が角度に比例しないので数値的に積む(ケープと同じ)
    top_arc = modulate.ellipse_arc(
        profile["semi_major"][0] + standoff,
        profile["depth_ratio"][0],
        FRONT_ANGLE - span_angle * 0.5,
        FRONT_ANGLE + span_angle * 0.5,
    )
    mesh.design = {
        "top_perimeter": None,  # 開いた弧なので閉ループの周長検算は使わない
        "bottom_perimeter": None,
        "bottom_fit_perimeter": None,
        "bottom_perimeter_note": "前面だけの弧なので apron_top_arc / apron_flare で検算する",
        "top_z": waist_z * scale,
        "bottom_z": (waist_z - length) * scale,
        "length": length * scale,
        "depth_ratio_top": profile["depth_ratio"][0],
        "depth_ratio_bottom": profile["depth_ratio"][-1],
        # **周期成分の検査は掛けられない。** 半周ぶんの弧の半径列は円周のスペクトルに
        # ならず、平らな弧でも k=3 が立つ(ケープが通るのは開きが40°で弧がほぼ全周だから)。
        # 代わりに上端の弧長と広がりを**製図値**と突き合わせる(下の apron_* ゲート)
        "radial_modulations": None,
        "pleats": 0,
        "pleat_depth": 0.0,
        "boundary_loops": 1,  # 開いたシート = 外周1本
        "boundary_verts": None,
        # ---- 前スカート固有(validate 側のゲートが発火する)----
        "apron_top_arc": top_arc * scale,
        "apron_span_degrees": params["span_degrees"],
        "apron_flare": profile["bottom_perimeter"] / profile["top_perimeter"],
    }
    return mesh


def _closed_front_angles(segments):
    """前が閉じた胴(かぶりもの)の角度。**前中心に頂点を置く**。

    loft_rings(closed=True) と組で使う。前中心に頂点があるので、胸のふくらみも
    ゼッケンの貼り位置も左右対称になる(前開きの版は隙間の中央に頂点が無い)。
    分割数を4の倍数にすると、袖ぐりの中心(±X)にも頂点が来て左右が揃う。
    """
    step = 2.0 * math.pi / segments
    return [FRONT_ANGLE + step * index for index in range(segments)]


def _side_segment(angles, target):
    """target 角度に最も近い分割番号(袖ぐりを開ける位置を決めるのに使う)"""
    def gap(index):
        difference = (angles[index] - target) % (2.0 * math.pi)
        return min(difference, 2.0 * math.pi - difference)

    return min(range(len(angles)), key=gap)


def round_armhole(ring_points, segments, row_lo, row_hi, col_lo, col_hi, amount):
    """袖ぐりの穴の**境界頂点を楕円へ載せて角を落とす**(純粋関数)。

    `skip_faces` は格子から矩形の面群を抜くので、穴は四隅が直角の長方形になる。
    実物の袖ぐりは丸いので、境界の頂点を「同じ差し渡しの楕円」の上へ動かす。

    **面を抜く単位で丸めてはいけない。** 行ごとの幅を変える方式だと、袖ぐりが
    3分割しかないとき幅は 3 か 1 しか取れず、1段狭めただけで境界に棘ができて
    そこから作る袖が自己交差した(ワンピースで実証)。頂点の移動なら連続量なので
    角だけを必要なぶん削れる。

    矩形の境界では max(|u|,|v|)=1、楕円では hypot(u,v)=1 なので、
    hypot で割れば矩形の境界がそのまま楕円へ写る(辺の中点は動かず、角がいちばん動く)。
    穴の頂点数は偶数なので辺の中点にちょうど頂点が来るとは限らず、肩線リングの
    最外点は**わずかに**動く(実測 0.2% 未満。肩幅ゲートの許容差 3% の内側)。

    amount は 0(矩形のまま)〜1(完全な楕円)。戻り値は新しい ring_points。
    """
    if not 0.0 <= amount <= 1.0:
        raise PartError("袖ぐりの丸めは 0〜1 にしてください: %r" % (amount,))
    if amount == 0.0 or col_hi - col_lo < 2 or row_hi - row_lo < 2:
        # 差し渡しが1分割しかない穴は、動かす先が境界の外になるので丸めない
        return ring_points

    def sample(row, col):
        """格子を実数座標で双一次補間する(列は周方向に巻き戻す)"""
        row0 = max(row_lo, min(row_hi - 1, int(math.floor(row))))
        col0 = int(math.floor(col))
        fr, fc = row - row0, col - col0
        corners = [
            ring_points[row0 + dr][(col0 + dc) % segments]
            for dr, dc in ((0, 0), (0, 1), (1, 0), (1, 1))
        ]
        return tuple(
            (corners[0][axis] * (1 - fc) + corners[1][axis] * fc) * (1 - fr)
            + (corners[2][axis] * (1 - fc) + corners[3][axis] * fc) * fr
            for axis in range(3)
        )

    half_col = (col_hi - col_lo) / 2.0
    half_row = (row_hi - row_lo) / 2.0
    moved = {}
    for row in range(row_lo, row_hi + 1):
        for col in range(col_lo, col_hi + 1):
            on_edge = row in (row_lo, row_hi) or col in (col_lo, col_hi)
            if not on_edge:
                continue  # 穴の内側の頂点は面ごと消えるので動かす意味がない
            u = (col - col_lo) / half_col - 1.0
            v = (row - row_lo) / half_row - 1.0
            radius = math.hypot(u, v)
            if radius <= 1e-9:
                continue
            shrink = 1.0 + (1.0 / radius - 1.0) * amount
            moved[(row, col % segments)] = sample(
                row_lo + (v * shrink + 1.0) * half_row,
                col_lo + (u * shrink + 1.0) * half_col,
            )

    # 元の格子から全部読んでから書く(先に書くと後続の補間が動いた点を拾う)
    result = [list(ring) for ring in ring_points]
    for (row, col), point in moved.items():
        result[row][col] = point
    return result


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
    shoulder_width = table["shoulder_width"] * params["shoulder_width_scale"]
    shoulder_semi = shoulder_width / 2.0

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
    # かぶりもの(水着・タンクトップ)は前が閉じる。隙間が無いので前立ての出番も無い
    closed_front = params["front_style"] == "closed"
    angles = (
        _closed_front_angles(segments) if closed_front else _front_open_angles(segments, gap_angle)
    )

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

    # 裾のシャツテール(定義 #16): 脇が高く前後が下がる。水平に切った裾は
    # 「筒を切った」ようにしか見えない。**ただしワンピースの胴のように裾に
    # スカートを縫い付ける場合は水平が正しい**(hem_style="flat")。
    # flat では design からシャツテールのキーを落とし、裾ゲートを発火させない
    flat_hem = params["hem_style"] == "flat"
    tail_back = 0.0 if flat_hem else params["shirttail_drop"] * height
    tail_front_ratio = params["shirttail_front_ratio"]
    tails = [
        modulate.shirttail_drop(angle, tail_back, tail_front_ratio, FRONT_ANGLE)
        for angle in angles
    ]

    # 着丈(定義 #7): 側頸点から**裾の最下点**まで。製品実寸表から取る
    # (勘で置いた hem_z がクロップ丈の原因だった。経緯は docs/garments.md)。
    # シャツテールのぶん脇の裾を持ち上げて、いちばん下が着丈になるようにする
    garment_length = table["garment_length"] * params["length_scale"]
    hem_z = snp_z - garment_length + max(tails)
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
    # シャツテールは逆に、裾に近づくほど効く
    tail_span = max(1e-6, params["shirttail_fade"] * height)

    def fade(z_base):
        return 1.0 - modulate.smoothstep(min(1.0, (shoulder_z - z_base) / fade_span))

    def tail_fade(z_base):
        return 1.0 - modulate.smoothstep(min(1.0, (z_base - hem_z) / tail_span))

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
        tail_weight = tail_fade(base)
        rows.append(
            (
                [
                    base - drop * weight - tail * tail_weight
                    for drop, tail in zip(drops, tails)
                ],
                base
                - front_drop * weight
                - tail_back * tail_front_ratio * tail_weight,
                semi,
                depth,
            )
        )

    # 胸のふくらみ(定義 #18)。楕円断面だけの胴はメンズシャツにしか見えない。
    # 角度方向・軸方向とも局所的な山にするので、袖ぐりにも裾にも触れない
    bust_amount = params["bust_projection"] * height
    bust_angular = math.radians(params["bust_angular_degrees"])
    bust_apex = math.radians(params["bust_apex_degrees"])
    bust_axial = params["bust_axial_width"]

    # ふくらみの**上側**は襟ぐりで止める。届かせると襟ぐりの縁そのものが前へ
    # 押し出されて、横から見ると嘴のような角ができる(同梱4プリセット全部で
    # 19〜29mm はみ出していた)。下側は spec の指定のまま — 実物の胸は下へ長い。
    # 黙って切らずに、**設計値も詰めた値に差し替える**(thigh_target と同じ手口)
    neck_front_z = snp_z - front_drop
    headroom = bust_t - max(0.0, (shoulder_z - neck_front_z) / max(span, 1e-9))
    bust_axial_up = min(bust_axial, max(BUST_AXIAL_MIN, headroom))

    def bust_bulge(angle, t):
        return modulate.bust_projection(
            angle,
            t,
            bust_amount,
            bust_angular,
            bust_t,
            bust_axial,
            FRONT_ANGLE,
            bust_apex,
            bust_axial_up,
        )

    ring_points = []
    front_profile = []
    for index, (zs, front_z, semi, depth) in enumerate(rows):
        # rows[0] と rows[1] は襟ぐりと肩線。胴の t は rows[2] から
        t = 0.0 if index < 2 else (index - 1) / (body_rings - 1)
        # ふくらみは y 方向(前)へ出すので、depth_ratio を割り戻して半径に足す
        radii = [
            (semi + bust_bulge(angle, t) / max(depth, 1e-6)) * scale for angle in angles
        ]
        ring_points.append(
            kernels.ring_from_polar(
                radii, angles, [z * scale for z in zs], depth_ratio=depth
            )
        )
        # 前立てが乗る面。ふくらみのぶんも前へ出るので、ここで足しておかないと
        # 前立てが胸から浮く(あるいは中へ食い込む)
        front_profile.append(
            (
                front_z * scale,
                -(semi * depth + bust_bulge(FRONT_ANGLE, t)) * scale,
            )
        )

    # 袖ぐり: 肩線の直下(リング1と2の間)から、袖ぐり深さのところまで(定義 #4)
    armhole_depth = table["armhole_depth"] * params["armhole_depth_scale"]
    armhole_rows = max(1, min(int(round(armhole_depth / spacing)), body_rings - 2))
    armhole_segments = max(1, params["armhole_segments"])
    skip = set()
    centres = {}
    spans = []
    for label, target in (("l", 0.0), ("r", math.pi)):
        centre = _side_segment(angles, target)
        centres[label] = centre
        low = -(armhole_segments // 2)
        high = armhole_segments - armhole_segments // 2
        for row in range(1, 1 + armhole_rows):
            for offset in range(low, high):
                skip.add((row, (centre + offset) % segments))
        # 面 (row, j) は頂点 j と j+1 を使うので、穴の頂点は high 側が1つ多い
        spans.append((centre + low, centre + high))

    # 穴の角を落として袖ぐりを丸くする(矩形のままだと実物に見えない)
    for col_lo, col_hi in spans:
        ring_points = round_armhole(
            ring_points,
            segments,
            1,
            1 + armhole_rows,
            col_lo,
            col_hi,
            params["armhole_round"],
        )

    # 前面の実物を行ごとに書き出す(袖ぐりを丸めたあとの最終形から取る)。
    # 前中心から左右 100 度ぶん。ゼッケンの幅(±30度)を余裕で覆う
    front_span = math.radians(100.0)
    front_surface = []
    for (_zs, front_z, _semi, _depth), ring in zip(rows, ring_points):
        row = [
            (point[0], point[1])
            for angle, point in zip(angles, ring)
            if abs((angle - FRONT_ANGLE + math.pi) % (2.0 * math.pi) - math.pi) <= front_span
        ]
        front_surface.append((front_z * scale, sorted(row)))

    mesh = kernels.loft_rings(
        ring_points, name=part["name"], closed=closed_front, tubular=True, skip_faces=skip
    )
    mesh.material = part["material"]
    mesh.design = {
        # 襟ぐりは前下がりで水平ではないので、上端の実周長は設計の楕円より長い。
        # 「首に合うか」は**水平に投影した周長**で見る(下の neck_perimeter)
        "top_perimeter": None,
        "bottom_perimeter": table["bust"] * params["hem_scale"] * scale,
        "bottom_fit_perimeter": table["bust"] * params["hem_scale"] * scale,
        # 裾がカーブしているので、実周長は上下動のぶん伸びる。
        # 「胴まわりの太さ」として比べたいのは水平に投影した周長
        "bottom_perimeter_projected": True,
        "bottom_perimeter_note": None,
        "top_z": snp_z * scale,
        "bottom_z": hem_z * scale,
        "length": None,  # 襟ぐりが肩より上にあるので z 幅と着丈は一致しない
        "depth_ratio_top": params["neck_depth_ratio"],
        "depth_ratio_bottom": body_depth,
        "radial_modulations": [],
        "pleats": 0,
        "pleat_depth": 0.0,
        # 前開き: 外周(襟ぐり+前開き+裾)1本 + 袖ぐり2本。
        # かぶりもの: 前でつながらないので外周が襟ぐりと裾に割れて4本になる
        "boundary_loops": 4 if closed_front else 3,
        "boundary_verts": None,
        # --- 定義 #1〜#6 を検証するための設計値 ---
        "neck_perimeter": neck_perimeter * scale,
        "shoulder_width": shoulder_width * scale,
        "shoulder_z": shoulder_z * scale,
        "armhole_depth": armhole_depth * scale,
        "shoulder_span": (shoulder_semi - neck_semi) * scale,
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
        "front_profile": front_profile,
        # 前面の**実物**。(z, [(x, y), ...]) を上から下へ。幅のある布(ゼッケン)は
        # ここから貼り位置を取る。前中心の y と断面の楕円で近似していたが、
        # 胸の山が左右2つになると前中心は谷なので、近似だと布が胸に沈む(交差18件)
        "front_surface": front_surface,
        # --- 定義 #18(胸のふくらみ) ---
        # 裾の前面より前へどれだけ出るか。楕円断面だけの胴だとほぼ 0 になる
        "bust_projection_over_hem": (
            (bust_semi * body_depth + bust_amount) - hem_semi * body_depth
        )
        * scale,
        "bust_projection": bust_amount * scale,
        # 襟ぐりで詰めたあとの上側の半幅(spec の指定と違うことがある)
        "bust_axial_width_up": bust_axial_up,
        "bust_axial_width": bust_axial,
        # ふくらみが始まる高さ。稜線の検査の走査範囲の上端に使う。
        # **上側が襟ぐりで詰められている胴では、この値は襟ぐりの z と一致する**
        # (`bust_axial_up = headroom` のとき代数的にそうなる)。効くのは
        # `bust_axial_width` を襟ぐりまでの余裕より小さくした胴だけ。
        # 稜線の床(ブラウスで 54.8 度)を消したのはこのキーではなく、
        # **窓を頂点の近くに限ったこと**(validate.BUST_RIDGE_WINDOW)
        "bust_top_z": (shoulder_z - (bust_t - bust_axial_up) * span) * scale,
    }
    if not closed_front:
        # 前開きの隙間の最大幅。前立てはこれより広くないと隙間が見える(定義 #13)。
        # かぶりものには隙間が無いので**キーごと出さない**(0 を入れると
        # 「幅 >= 0」で前立てゲートが素通りする)
        mesh.design["front_gap_width"] = (
            max(2.0 * semi * math.sin(gap_angle * 0.5) for _zs, _front_z, semi, _depth in rows)
            * scale
        )
    if not flat_hem:
        # --- 定義 #16(裾のシャツテール) --- キーの有無がゲートのスイッチ。
        # shirttail の胴だけが「裾が曲がっていること」を検査される
        mesh.design["shirttail_drop"] = tail_back * scale
        mesh.design["shirttail_front_ratio"] = tail_front_ratio
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
    # 肩先は**胴が実際に作った肩線**から取る(サイズ表から引き直さない)。
    # 胴が shoulder_width_scale で肩線を詰めていると、表の値では袖が肩の外に出る
    shoulder_point = (
        sign * host.design["shoulder_width"] / 2.0,
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
            t, bicep_radius, cuff_radius, elbow, cuff_start, params["cuff_gather"]
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
    cuff_rings = [index for index, t in enumerate(ts) if t >= cuff_start]
    # カフスの付け根は縫い目。段差だけだと陰影が繋がって縫い目に見えない
    mesh.sharp_rings = cuff_rings[:1]
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
        "cuff_rings": cuff_rings,
        "cuff_gather": params["cuff_gather"],
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


def front_surface_sampler(surface):
    """前面の実物 `(z, [(x, y), ...])` から y(x, z) を読む補間器を返す(純粋関数)。

    前中心の y だけを見て断面を楕円で近似すると、胸の山が左右2つあるときに
    前中心が谷になり、幅のある布(前立て・ゼッケン)が胸に沈む(交差ゲートで実証)。
    **相手の頂点をそのまま線形に読む**ので、相手のメッシュ面と一致する。

    行は上から下へ。行間は z で線形補間し、範囲外は端の行に張り付く。
    """
    rows = [(z, list(points)) for z, points in surface]
    if not rows:
        raise PartError("front_surface が空です")

    def along_row(points, x):
        if x <= points[0][0]:
            return points[0][1]
        for index in range(1, len(points)):
            left, right = points[index - 1], points[index]
            if left[0] <= x <= right[0]:
                if right[0] == left[0]:
                    return right[1]
                local = (x - left[0]) / (right[0] - left[0])
                return left[1] + (right[1] - left[1]) * local
        return points[-1][1]

    def sample(x, z):
        if z >= rows[0][0]:
            return along_row(rows[0][1], x)
        for index in range(1, len(rows)):
            upper_z, upper = rows[index - 1]
            lower_z, lower = rows[index]
            if lower_z <= z <= upper_z:
                if upper_z == lower_z:
                    return along_row(lower, x)
                local = (upper_z - z) / (upper_z - lower_z)
                return along_row(upper, x) + (along_row(lower, x) - along_row(upper, x)) * local
        return along_row(rows[-1][1], x)

    return sample


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

    # 帯は幅があるので、**前中心の y ではなく相手の面を x ごとに読む**。
    # 前中心だけを見ると、胸の山が左右2つあるとき谷に貼ることになり、
    # 帯の端(±1.5cm)が胸に沈む(交差ゲートで実証)
    surface = (host.design or {}).get("front_surface")
    surface_y = front_surface_sampler(surface) if surface else (lambda x, z: front_y(z))

    ring_points = []
    for z in heights:
        points = []
        for column in range(columns):
            u = column / (columns - 1)  # 0 = +X 側、1 = -X 側
            x = half - width * u
            # 中央がいちばん前に出る
            points.append(
                (x, surface_y(x, z) - standoff - bulge * math.sin(math.pi * u), z)
            )
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
        # 前立て中央(いちばん前)の表面の (z, y)。ボタンはこの面に沿って置く。
        # 単一の placket_front_y(全体の最前点)だけだと、前面が z で動く胴
        # (ワンピースの胸→ウエスト)でボタンが布から浮く
        "placket_front_profile": [
            (z, surface_y(0.0, z) - standoff - bulge) for z in heights
        ],
        # 覆う相手。これより狭いと前開きが黒い筋として見える
        "front_gap_width": (host.design or {}).get("front_gap_width"),
    }
    return mesh


def build_patch(part, table, scale, host=None):
    """ゼッケン(名札)。胴の前面に貼る一枚の布。スク水の白い名札がこれ。

    前立てと違うのは**幅があること**。前中心の y だけで平らな板を貼ると、
    幅の端(左右 8cm)で胴の断面が後ろへ逃げるぶんだけ布が浮いて、
    貼り紙が胸の前に浮いているように見える。断面の長半径を相手からもらって
    楕円に沿わせる(前中心を通る楕円に縮めるので、必ず胴の**外側**に来る)。
    """
    if host is None:
        raise PartError("patch は attach_to で胴パーツを指定してください(前面の実寸が必要)")
    design = host.design or {}
    profile = design.get("front_profile")
    surface = design.get("front_surface")
    if not profile or not surface:
        raise PartError("%s に front_profile / front_surface がありません" % (host.name,))

    params = part["params"]
    height = table["height"]
    width = params["width"] * height * scale
    patch_height = params["height"] * height * scale
    standoff = params["standoff"] * height * scale
    # **奇数にする。** 前中心に頂点が無いと、いちばん前に出る頂点が中心から
    # ずれた列になり、そこは断面が後ろへ逃げているぶん浮きが目減りする
    # (「胴の前面より手前」の検査が設計どおり作っても落ちる)
    columns = max(3, params["columns"]) | 1
    half = width * 0.5

    # 縦の刻みは**相手の行に吸着させる**(丈は spec の指定に一番近い行まで)。
    # 理由は2つ。相手の折れ点を跨いで直線で張ると前面が動く区間で胴の中へ食い込む
    # (前立てと同じ罠)。そして折れ点と等間隔の点を混ぜると刻みが不揃いになり、
    # 軸方向エッジ長のばらつきの検査に落ちる。行に乗せれば両方いっぺんに解ける。
    # **その代わり丈は指定どおりにならない**ので、設計値は吸着後の実寸にする
    knots = [z for z, _y in profile]
    span = knots[0] - knots[-1]
    target_top = knots[0] - params["top_inset"] * span
    top_index = min(range(len(knots)), key=lambda index: abs(knots[index] - target_top))
    if top_index >= len(knots) - 1:
        raise PartError(
            "ゼッケンの上端が胴の裾に寄りすぎです(top_inset %.2f)" % (params["top_inset"],)
        )
    target_bottom = knots[top_index] - patch_height
    bottom_index = min(
        range(top_index + 1, len(knots)),
        key=lambda index: abs(knots[index] - target_bottom),
    )
    top_z = knots[top_index]
    bottom_z = knots[bottom_index]
    patch_height = top_z - bottom_z

    # rows は「最低これだけ刻む」の意味。相手の行の間隔を等分するので、
    # 相手が等間隔なら刻みも等間隔のまま
    intervals = bottom_index - top_index
    factor = max(1, -(-max(1, params["rows"]) // intervals))
    heights = []
    for index in range(intervals):
        upper, lower = knots[top_index + index], knots[top_index + index + 1]
        for step in range(factor):
            heights.append(upper + (lower - upper) * step / factor)
    heights.append(bottom_z)

    surface_rows = {round(z, 9): row for z, row in surface}

    def surface_y(x, z):
        """相手の前面の y を (x, z) で読む。**行は相手の行に吸着済み**なので
        z の補間は要らず、行の中を x で線形に読むだけ = 相手の面そのもの"""
        row = surface_rows.get(round(z, 9))
        if row is None:  # pragma: no cover - 吸着しているので通らない
            raise PartError("ゼッケンの高さ %r が胴の行に乗っていません" % (z,))
        if x <= row[0][0]:
            return row[0][1]
        for index in range(1, len(row)):
            left, right = row[index - 1], row[index]
            if left[0] <= x <= right[0]:
                if right[0] == left[0]:
                    return right[1]
                local = (x - left[0]) / (right[0] - left[0])
                return left[1] + (right[1] - left[1]) * local
        return row[-1][1]

    ring_points = []
    for z in heights:
        points = []
        for column in range(columns):
            u = column / (columns - 1)  # 0 = +X 側、1 = -X 側
            x = half - width * u
            points.append((x, surface_y(x, z) - standoff, z))
        ring_points.append(points)

    mesh = kernels.loft_rings(ring_points, name=part["name"], closed=False, tubular=False)
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
        # --- ゼッケンの検証用 ---
        "patch_width": width,
        "patch_height": patch_height,
        "patch_standoff": standoff,
        # 貼った区間で胴がいちばん前に出ている y。**前中心ではなく貼った範囲の
        # 実物から取る**(胸の山は左右にあるので、前中心を基準にすると沈む)。
        # ゼッケンがこれより前に無いと「胸に埋まった名札」になる
        # (交差ゲートは面が交わるまで気付かない)
        "patch_host_front_y": min(
            surface_y(point[0], point[2]) for ring in ring_points for point in ring
        ),
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
    # 前立ての表面よりわずかに前。触れさせると面が交差して不合格になる。
    # 表面は z で動く(胸の張り出し・ウエストの絞り)ので、ボタンごとに
    # **その高さの表面**から浮かせる。全体の最前点からの一定 y に置くと、
    # 前面が後退する区間でボタンが宙に浮く(ワンピースで実証)
    standoff = params["standoff"] * table["height"] * scale
    surface = design.get("placket_front_profile")

    def base_y_at(z):
        if not surface:
            return design["placket_front_y"] - standoff
        # ボタンは平らな円盤のまま置くので、**ディスクの z 幅全体**で表面より
        # 前に出す(中心の高さだけ見ると、表面が傾く区間で縁が布に食い込む)
        span = [z - radius, z, z + radius] + [
            knot for knot, _value in surface if z - radius <= knot <= z + radius
        ]
        return min(modulate.interp_profile(surface, s) for s in span) - standoff

    segments = max(6, params["segments"])
    angles = modulate.circle_angles(segments)

    # シャツのボタンは**厚みのある平たい円盤**(実物 直径11.5mm / 厚み2.3mm / 4つ穴)。
    # 前の版は迫り出す円錐で、裏面も穴も無く真珠のスタッドに見えていた(定義 #15)。
    #
    #   リング0: 裏面の縁     半径 r、前立てに接する側
    #   リング1: 表面の縁     半径 r、厚みぶん前   ← 0→1 が円盤の側面
    #   リング2: 縁の内側     半径 0.82r、同じ高さ ← 立った縁
    #   リング3: 皿          半径 0.42r、少し奥   ← ここの4点を奥へ押して穴の窪みに
    #   リング4: 中心        半径 0.10r、皿と同じ高さ
    thickness = radius * 2.0 * params["thickness_ratio"]
    dished = 1.0 - params["dish"]
    #   リング0: 裏面の縁   半径 r       前立てに接する側
    #   リング1: 表面の縁   半径 r       厚みぶん前   ← 0→1 が円盤の側面
    #   リング2: 縁の内側   半径 0.82r   同じ高さ     ← 立った縁
    #   リング3: 穴の外側   半径 0.52r   少し奥(皿)
    #   リング4: 穴の内側   半径 0.30r   同じ         ← 3→4 の面を4か所抜いて穴にする
    #   リング5: 中心       半径 0.10r   同じ
    profile = (
        (1.0, 0.0),
        (1.0, 1.0),
        (0.82, 1.0),
        (0.52, dished),
        (0.30, dished),
        (0.10, dished),
    )
    hole_ring = 3
    hole_segments = modulate.button_hole_segments(segments, params["holes"])
    skip = {(hole_ring, segment) for segment in hole_segments}

    verts, quads, uv_loops = [], [], []
    for z in zs:
        base_y = base_y_at(z)
        shell = [
            [
                (
                    radius * factor * math.cos(angle),
                    base_y - thickness * forward,
                    z + radius * factor * math.sin(angle),
                )
                for angle in angles
            ]
            for factor, forward in profile
        ]
        piece = kernels.loft_rings(
            shell, name=part["name"], closed=True, tubular=False, skip_faces=skip
        )
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
        ring_count=len(profile) * count,
        tubular=False,
        material=part["material"],
        # ボタンは布ではなく硬い部品。スムーズシェーディングを掛けると
        # 円盤の縁が丸まって、寄って見ると白い塊にしか見えない
        flat_shaded=True,
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
        # ボタン1個につき 外周 + 中心の小穴 + 穴のぶん
        "boundary_loops": (2 + params["holes"]) * count,
        "boundary_verts": None,
        # --- 定義 #14 ---
        "button_count": count,
        "button_zs": list(zs),
        "button_radius": radius,
        "placket_width": design["placket_width"],
        "placket_top_z": design["placket_top_z"],
        "placket_bottom_z": design["placket_bottom_z"],
        # ボタンが前立ての**表面に沿っている**ことの検証用(定義 #19)。
        # 表面プロファイルは前立ての実物から、離隔は自分の standoff
        "placket_front_profile": design.get("placket_front_profile"),
        "button_standoff": standoff,
        # --- 定義 #15(ボタンそのものの形) ---
        "button_diameter": radius * 2.0,
        "button_thickness": thickness,
        "button_thickness_ratio": params["thickness_ratio"],
        "button_holes": params["holes"],
        "button_segments": segments,
    }
    return mesh


def seam_groups(part_names, joints):
    """joints で繋がったパーツ名をまとめる(**純粋関数**。bpy 非依存)。

    別オブジェクトのままだと Blender は頂点法線を繋がないので、面が連続していても
    継ぎ目に陰影の段が出る(「布の切れ目」に見える線の正体)。統合する単位を
    ここで決めて、実際の結合は build.py が Blender に投げる。

    戻り値: パーツ名のリストのリスト。**入力順を保つ**(結合後の名前が
    spec の並びで決まるようにするため)。繋がっていないパーツは1要素の組になる。
    """
    parent = {name: name for name in part_names}

    def find(name):
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for joint in joints:
        a, b = joint.get("a"), joint.get("b")
        if a in parent and b in parent:
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                parent[root_b] = root_a

    groups = {}
    for name in part_names:
        groups.setdefault(find(name), []).append(name)
    return [groups[find(name)] for name in part_names if find(name) == name]


#: 仕上げに積む標準モディファイアの名前(build.py が同名で作り直す)
SUBSURF_MODIFIER = "Costume_Smooth"
SOLIDIFY_MODIFIER = "Costume_Thickness"


def finish_modifiers(part_mesh, thickness, levels):
    """このパーツに積む標準モディファイアの一覧を決める(**純粋関数**。bpy 非依存)。

    素の格子をそのまま出すと、断面が24角形なので襟ぐりも袖ぐりも裾も
    多角形の縁として見え、布は厚みゼロの紙になる。**どちらも自前で作らない** —
    CLAUDE.md の「仕上げは標準モディファイアに投げる」に従って Subsurf と
    Solidify に任せる(層1の検査は素の格子に対して行うので影響を受けない)。

    順序は **Subsurf → Solidify**。逆にすると Solidify が作った細いリム面まで
    Subsurf が丸めて、厚みが2/3ほど痩せる。先に滑らかにしてから等厚で押し出せば
    厚みは指定どおりに残り、裁ち端は実物どおり角が立つ。

    硬い部品(ボタン)には掛けない。円盤の縁が丸まって白い塊に見えるうえ、
    厚みはもともとジオメトリで作ってある。

    戻り値: [(名前, 種別, {プロパティ: 値}), ...]
    """
    if thickness < 0.0:
        raise PartError("布の厚みは 0 以上にしてください: %r" % (thickness,))
    if levels < 0:
        raise PartError("細分レベルは 0 以上にしてください: %r" % (levels,))
    if part_mesh.flat_shaded:
        return []
    stack = []
    if levels > 0:
        stack.append(
            (
                SUBSURF_MODIFIER,
                "SUBSURF",
                {
                    "levels": int(levels),
                    "render_levels": int(levels),
                    # 開いたシート(前垂れ・ゼッケン)の角を保つ。既定のまま丸めると
                    # 布が縮んで相手から浮く
                    "boundary_smooth": "PRESERVE_CORNERS",
                    "uv_smooth": "PRESERVE_BOUNDARIES",
                },
            )
        )
    if thickness > 0.0:
        stack.append(
            (
                SOLIDIFY_MODIFIER,
                "SOLIDIFY",
                {
                    "thickness": float(thickness),
                    # **厚みは内へ出す**(元の面が外側)。ゼッケンも前垂れも
                    # 「相手の**外側の面**から standoff だけ浮かせる」で組んであるので、
                    # 外へ膨らませると胴が浮かせ量を追い越してゼッケンが布に沈む
                    # (外向きで実際に沈んだ)。内向きなら層の関係が設計のまま残る
                    "offset": -1.0,
                    "use_rim": True,
                    # 均等オフセットは鋭い折れ目で 1/cos に比例して伸び、
                    # 股の縫い目に棘を作る(自己交差が 26 件)。切っておく
                    "use_even_offset": False,
                    # 分岐(股)のように面が鋭く折り重なる所は複雑モードでないと
                    # シェルが交差する(26 件 → 4 件)
                    "solidify_mode": "NON_MANIFOLD",
                },
            )
        )
    return stack


BUILDERS = {
    "skirt_body": build_skirt_body,
    "waistband": build_waistband,
    "cape": build_cape,
    "pants": build_pants,
    "bodice": build_bodice,
    "sleeve": build_sleeve,
    "collar": build_collar,
    "collar_fall": build_collar_fall,
    "placket": build_placket,
    "buttons": build_buttons,
    "hood": build_hood,
    "patch": build_patch,
    "apron": build_apron,
}

#: 他のパーツの**実物**から作るパーツ。build_all が依存順を保証する。
#: 継ぎ目や位置を計算し直さずに相手の頂点をそのまま使うので、ずれが原理的に起きない
DEPENDENT_TYPES = frozenset(
    ("sleeve", "collar", "collar_fall", "placket", "buttons", "hood", "patch")
)

# spec が知っているパーツ種別と、ここで作れる種別がずれていないことを import 時に確かめる
_MISSING = sorted(set(spec_module.PART_SCHEMAS) - set(BUILDERS))
if _MISSING:  # pragma: no cover - 実装漏れの検出用
    raise ImportError("parts.BUILDERS に実装が無いパーツ種別があります: %s" % ", ".join(_MISSING))

# 依存パーツの集合は spec 側(attach_to を必須にする)とここ(host を渡す)の
# 二重管理なので、ずれると「spec は attach_to を要求するのに build_all は host を
# 渡さない」不整合が静かに起きる。BUILDERS の検査と同じく import 時に突き合わせる
if spec_module.DEPENDENT_PART_TYPES != DEPENDENT_TYPES:  # pragma: no cover - 実装漏れの検出用
    raise ImportError(
        "spec.DEPENDENT_PART_TYPES と parts.DEPENDENT_TYPES がずれています: %s / %s"
        % (sorted(spec_module.DEPENDENT_PART_TYPES), sorted(DEPENDENT_TYPES))
    )


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
