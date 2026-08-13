"""スクール水着(前を閉じた bodice + 脚口の短い pants + ゼッケン)のテスト。

新しいカーネルは無い。追加したのは bodice の front_style / armhole_depth_scale と
patch パーツだけ。定義は docs/garments.md のスクール水着の節。
"""

import copy
import math

import pytest

from my_blender_plugin.costume import parse_text, parts, spec as spec_module, validate


def build_swimsuit():
    normalized = spec_module.load_preset("swimsuit")
    return normalized, parts.build_all(normalized)


def part_named(built, name):
    return next(mesh for mesh in built["parts"] if mesh.name == name)


def test_swimsuit_passes_every_hard_gate():
    normalized, built = build_swimsuit()
    report = validate.costume_report(built, normalized)
    assert report["failed"] == [], report["failed"]


# ---------------------------------------- 定義 #1: かぶりもの(前が閉じている)


def test_closed_front_body_is_a_closed_tube():
    """境界ループは4本(襟ぐり・裾・袖ぐり2)。前開きの3本と区別が付く"""
    _normalized, built = build_swimsuit()
    body = part_named(built, "Swimsuit_Body")
    assert body.design["boundary_loops"] == 4
    report = validate.part_report(body, 1.58)
    assert report["boundary_loops"] == 4
    assert report["hard"]["boundary_loop_count"]["ok"]


def test_closed_front_puts_a_vertex_on_the_front_centre():
    """前中心に頂点が無いと胸のふくらみもゼッケンの貼り位置も左右非対称になる"""
    _normalized, built = build_swimsuit()
    body = part_named(built, "Swimsuit_Body")
    top = [body.verts[index] for index in body.rings["top"]]
    front = min(top, key=lambda point: point[1])
    assert front[0] == pytest.approx(0.0, abs=1e-9)


def test_closed_front_drops_the_front_gap_key_and_open_front_keeps_it():
    """ゲートのスイッチは design のキーの有無。0 を入れると前立てゲートが素通りする"""
    _normalized, built = build_swimsuit()
    assert "front_gap_width" not in part_named(built, "Swimsuit_Body").design

    blouse = parts.build_all(spec_module.load_preset("blouse"))
    assert "front_gap_width" in part_named(blouse, "Blouse_Bodice").design


def test_front_style_rejects_unknown_values():
    spec = spec_module.load_preset("swimsuit")
    bodice = next(part for part in spec["parts"] if part["type"] == "bodice")
    bodice["params"]["front_style"] = "zipper"
    with pytest.raises(spec_module.SpecError):
        spec_module.normalize_spec(spec)


# ---------------------------------------- armhole_loops(前を閉じた胴での取り違え)
#
# 「境界ループのうち一番長い1本が外周、残りが袖ぐり」は前を閉じると成り立たない。
# 外周が襟ぐりと裾の2本に割れ、裾より短い襟ぐりが袖ぐりに化ける(落ちずに黙って狂う)。


def test_armhole_loops_finds_the_armholes_not_the_neckline():
    _normalized, built = build_swimsuit()
    body = part_named(built, "Swimsuit_Body")
    loops = validate.armhole_loops(body)
    assert sorted(loops) == ["l", "r"]

    neck = set(body.rings["top"])
    hem = set(body.rings["bottom"])
    for label, loop in loops.items():
        indices = set(loop["indices"])
        assert not indices & neck, label
        assert not indices & hem, label
    # 袖ぐりは体側にある(左が +X)
    assert loops["l"]["center"][0] > 0.0
    assert loops["r"]["center"][0] < 0.0


def test_the_neckline_is_longer_than_the_armholes_so_the_old_rule_would_have_failed():
    """この preset が実際に「襟ぐり > 袖ぐり」であること。
    そうでなければ上のテストは古い規則でも通ってしまい、回帰を守れない"""
    _normalized, built = build_swimsuit()
    body = part_named(built, "Swimsuit_Body")
    neck = validate.ring_metrics(body.verts, body.rings["top"])["perimeter"]
    for loop in validate.armhole_loops(body).values():
        assert neck > loop["perimeter"]


def test_open_front_armholes_are_unchanged():
    """回帰ガード: 前開きの胴では従来どおり外周を落として袖ぐり2本を返す"""
    blouse = parts.build_all(spec_module.load_preset("blouse"))
    bodice = part_named(blouse, "Blouse_Bodice")
    loops = validate.armhole_loops(bodice)
    assert sorted(loops) == ["l", "r"]
    assert loops["l"]["center"][0] > 0.0 > loops["r"]["center"][0]


# ---------------------------------------- 定義 #2: 袖ぐりが大きく開いている


def test_armhole_depth_scale_moves_the_design_value_too():
    """袖ぐりを深くしたぶん設計値も動く。ゲートを緩めずに形だけ変えられる"""
    spec = spec_module.load_preset("swimsuit")
    bodice = next(part for part in spec["parts"] if part["type"] == "bodice")
    base = bodice["params"]["armhole_depth_scale"]

    depths = {}
    for scale in (base, base * 1.2):
        variant = copy.deepcopy(spec)
        target = next(part for part in variant["parts"] if part["type"] == "bodice")
        target["params"]["armhole_depth_scale"] = scale
        normalized = spec_module.normalize_spec(variant)
        built = parts.build_all(normalized)
        report = validate.costume_report(built, normalized)
        assert report["failed"] == [], (scale, report["failed"])
        depths[scale] = part_named(built, "Swimsuit_Body").design["armhole_depth"]

    assert depths[base * 1.2] == pytest.approx(depths[base] * 1.2)


def test_shoulder_width_scale_moves_the_design_value_too():
    """肩線を詰めたぶん設計値も動く。詰めても肩幅ゲートは逃げない"""
    spec = spec_module.load_preset("swimsuit")
    widths = {}
    for scale in (1.0, 0.9):
        variant = copy.deepcopy(spec)
        target = next(part for part in variant["parts"] if part["type"] == "bodice")
        target["params"]["shoulder_width_scale"] = scale
        normalized = spec_module.normalize_spec(variant)
        built = parts.build_all(normalized)
        report = validate.costume_report(built, normalized)
        assert report["failed"] == [], (scale, report["failed"])
        entry = next(e for e in report["parts"] if e["part"] == "Swimsuit_Body")
        # 実測は袖ぐりの丸めで肩線リングがわずかに動くぶんだけ設計値からずれる。
        # 守りたいのは「設計値が倍率どおり動く(ゲートが緩まない)」ことなので
        # 設計値は厳密に、実測はゲートの許容差で見る
        widths[scale] = entry["design"]["shoulder_width"]
        assert entry["hard"]["shoulder_width"]["ok"]

    assert widths[0.9] == pytest.approx(widths[1.0] * 0.9, rel=1e-9)


def test_swimsuit_armhole_is_narrower_than_the_blouse_shoulder_line():
    """スク水の肩紐は肩先まで来ない(袖を付ける衣装との違い)"""
    _normalized, built = build_swimsuit()
    swim = part_named(built, "Swimsuit_Body").design["shoulder_width"]
    blouse = parts.build_all(spec_module.load_preset("blouse"))
    assert swim < part_named(blouse, "Blouse_Bodice").design["shoulder_width"]


def test_sleeve_takes_the_shoulder_point_from_the_bodice_it_is_attached_to():
    """袖はサイズ表ではなく**胴が実際に作った肩線**から肩先を取る。
    表から引き直すと、肩線を詰めた胴で袖が肩の外に出る"""
    blouse = spec_module.load_preset("blouse")
    bodice = next(part for part in blouse["parts"] if part["type"] == "bodice")
    bodice["params"]["shoulder_width_scale"] = 0.85
    built = parts.build_all(blouse)
    host = part_named(built, "Blouse_Bodice")
    sleeve = next(mesh for mesh in built["parts"] if mesh.name.endswith("Sleeve_L"))
    assert sleeve.design["shoulder_point"][0] == pytest.approx(
        host.design["shoulder_width"] / 2.0
    )


# ---------------------------------------- 袖ぐりの角を丸める
#
# skip_faces は格子から矩形の面群を抜くので、穴は四隅が直角の長方形になる。
# 実物の袖ぐりは丸いので境界頂点を楕円へ載せる。
# **面を抜く単位で丸めるのは駄目**(3分割の袖ぐりでは幅が 3 か 1 しか取れず、
# 1段狭めただけで境界に棘ができて、そこから作る袖が自己交差した)。


def flat_grid(rows, segments):
    """(列, 0, -行) の平らな格子。丸めの幾何だけを見るための足場"""
    return [
        [(float(col), 0.0, -float(row)) for col in range(segments)]
        for row in range(rows)
    ]


def test_round_armhole_is_identity_at_zero():
    grid = flat_grid(8, 12)
    assert parts.round_armhole(grid, 12, 1, 5, 2, 5, 0.0) == grid


def test_round_armhole_rejects_out_of_range():
    grid = flat_grid(8, 12)
    with pytest.raises(parts.PartError):
        parts.round_armhole(grid, 12, 1, 5, 2, 5, 1.5)


def test_round_armhole_leaves_a_one_wide_hole_alone():
    """差し渡しが1分割しかない穴は、動かす先が境界の外になるので丸めない"""
    grid = flat_grid(8, 12)
    assert parts.round_armhole(grid, 12, 1, 5, 2, 3, 1.0) == grid


def test_round_armhole_moves_corners_more_than_edge_midpoints():
    grid = flat_grid(8, 12)
    rounded = parts.round_armhole(grid, 12, 1, 5, 2, 5, 1.0)

    def moved(row, col):
        before, after = grid[row][col], rounded[row][col]
        return math.dist(before, after)

    assert moved(1, 2) > moved(1, 3) > 0.0  # 角 > 辺の途中 > 動く
    assert moved(1, 2) == pytest.approx(moved(5, 5))  # 対角の角は同じだけ動く
    assert moved(3, 3) == 0.0  # 穴の内側は面ごと消えるので動かさない
    assert moved(1, 8) == 0.0  # 穴の外は触らない


def test_round_armhole_pulls_corners_toward_the_hole_centre():
    grid = flat_grid(8, 12)
    rounded = parts.round_armhole(grid, 12, 1, 5, 2, 5, 1.0)
    centre = (3.5, 0.0, -3.0)  # (col_lo+col_hi)/2, (row_lo+row_hi)/2
    assert math.dist(rounded[1][2], centre) < math.dist(grid[1][2], centre)


def test_rounding_shortens_the_armhole_boundary():
    """角を落とすので袖ぐりの境界は矩形より短くなる(丸い証拠)"""
    perimeters = {}
    for amount in (0.0, 1.0):
        spec = spec_module.load_preset("swimsuit")
        bodice = next(part for part in spec["parts"] if part["type"] == "bodice")
        bodice["params"]["armhole_round"] = amount
        built = parts.build_all(spec_module.normalize_spec(spec))
        mesh = part_named(built, "Swimsuit_Body")
        loop = validate.armhole_loops(mesh)["l"]
        perimeters[amount] = loop["perimeter"]
    assert perimeters[1.0] < perimeters[0.0]


def test_rounded_armholes_keep_sleeves_intact():
    """回帰ガード: 袖は袖ぐりの穴から作るので、丸め方を誤ると袖が自己交差する
    (行ごとの幅を変える方式はここで落ちた)"""
    normalized = spec_module.load_preset("onepiece")
    built = parts.build_all(normalized)
    report = validate.costume_report(built, normalized)
    assert report["failed"] == [], report["failed"]
    sleeve = next(e for e in report["parts"] if e["part"] == "Dress_Sleeve_L")
    assert sleeve["self_intersections"] == 0


# ---------------------------------------- 定義 #4: ウエストで縫い合わさっている


def test_body_and_brief_are_sewn_at_the_waist():
    normalized, built = build_swimsuit()
    report = validate.costume_report(built, normalized)
    seam = next(joint for joint in report["joints"] if joint["b"] == "Swimsuit_Brief")
    assert seam["kind"] == "sewn"
    assert seam["ok"], seam


# ---------------------------------------- 定義 #5: 股が閉じていて脚口が2つある


def test_brief_has_two_leg_openings_below_the_crotch():
    _normalized, built = build_swimsuit()
    brief = part_named(built, "Swimsuit_Brief")
    crotch_z = validate.ring_metrics(brief.verts, brief.rings["crotch"])["z"]
    for side in ("l", "r"):
        hem = validate.ring_metrics(brief.verts, brief.rings["hem_" + side])
        assert hem["z"] < crotch_z
    left = validate.ring_metrics(brief.verts, brief.rings["hem_l"])["center"][0]
    right = validate.ring_metrics(brief.verts, brief.rings["hem_r"])["center"][0]
    assert left > 0.0 > right


def test_inseam_scale_allows_a_swimsuit_leg_opening():
    """脚口(股から数cm)を表現できること。下限 0.1 のままでは作れなかった"""
    spec = spec_module.load_preset("swimsuit")
    brief = next(part for part in spec["parts"] if part["type"] == "pants")
    assert brief["params"]["inseam_scale"] < 0.1


# ---------------------------------------- 定義 #6: 胸に白いゼッケンがある


def test_name_tag_sits_in_front_of_the_body():
    """**全頂点が**胴の面から standoff だけ前に出ていること。
    「いちばん前の点」だけ見るのでは足りない — 胸の山は左右にあるので、
    前中心を基準にすると山のところで布が沈む(交差18件で実証)"""
    normalized, built = build_swimsuit()
    report = validate.costume_report(built, normalized)
    entry = next(e for e in report["parts"] if e["part"] == "Swimsuit_NameTag")
    assert entry["hard"]["patch_sits_on_the_front"]["ok"]
    assert entry["hard"]["patch_centred"]["ok"]

    body = part_named(built, "Swimsuit_Body")
    tag = part_named(built, "Swimsuit_NameTag")
    surface = parts.front_surface_sampler(body.design["front_surface"])
    standoff = tag.design["patch_standoff"]
    for x, y, z in tag.verts:
        assert surface(x, z) - y == pytest.approx(standoff, abs=1e-9), (x, z)


def test_sinking_the_name_tag_into_the_body_breaks_the_gate():
    """design はそのまま、頂点だけ胴の中(+y)へ押し込む → 捕まえる"""
    _normalized, built = build_swimsuit()
    tag = copy.deepcopy(part_named(built, "Swimsuit_NameTag"))
    tag.verts = [(x, y + 0.01, z) for x, y, z in tag.verts]
    after = validate.part_report(tag, 1.58)
    assert "patch_sits_on_the_front" in after["failed"]


def test_name_tag_wraps_the_chest_instead_of_staying_flat():
    """胴の丸みに沿っていること。平らな板だと胸から浮いて貼り紙に見える。

    **前中心がいちばん前とは限らない** — 胸の山は左右にあるので前中心は谷。
    ここで見るのは「x 方向に平らでないこと」と「胴の起伏をなぞっていること」"""
    _normalized, built = build_swimsuit()
    body = part_named(built, "Swimsuit_Body")
    tag = part_named(built, "Swimsuit_NameTag")
    surface = parts.front_surface_sampler(body.design["front_surface"])

    rows = {}
    for x, y, z in tag.verts:
        rows.setdefault(round(z, 9), []).append((x, y))
    for z, points in rows.items():
        ys = [y for _x, y in points]
        # 平らではない(胴の起伏ぶんの幅がある)
        assert max(ys) - min(ys) > 1e-4, z
        # 起伏の向きが胴と一致している(前後関係が保たれている)
        for x, y in points:
            other = [(ox, oy) for ox, oy in points if ox != x]
            for ox, oy in other:
                assert (y < oy) == (surface(x, z) < surface(ox, z)), (x, ox, z)


def test_name_tag_rows_land_on_the_host_rows():
    """相手の行に吸着していること。折れ点を跨いで直線で張ると胴へ食い込む"""
    _normalized, built = build_swimsuit()
    body = part_named(built, "Swimsuit_Body")
    tag = part_named(built, "Swimsuit_NameTag")
    knots = [round(z, 9) for z, _y in body.design["front_profile"]]
    for z in {round(point[2], 9) for point in tag.verts}:
        assert z in knots, z


def test_name_tag_columns_are_forced_odd():
    """偶数だと前中心に頂点が来ず、設計どおり作っても浮きの検査に落ちる"""
    spec = spec_module.load_preset("swimsuit")
    tag = next(part for part in spec["parts"] if part["type"] == "patch")
    tag["params"]["columns"] = 6
    normalized = spec_module.normalize_spec(spec)
    built = parts.build_all(normalized)
    mesh = part_named(built, "Swimsuit_NameTag")
    assert mesh.ring_size == 7
    report = validate.part_report(mesh, 1.58)
    assert report["failed"] == [], report["failed"]


def test_patch_needs_a_host_with_a_front_profile():
    table = {"height": 1.58}
    part = {"name": "Tag", "material": "trim", "params": {}}
    with pytest.raises(parts.PartError):
        parts.build_patch(part, table, 1.0, host=None)


def test_patch_is_registered_as_a_dependent_part():
    assert "patch" in spec_module.DEPENDENT_PART_TYPES
    assert "patch" in parts.DEPENDENT_TYPES
    spec = spec_module.load_preset("swimsuit")
    tag = next(part for part in spec["parts"] if part["type"] == "patch")
    del tag["attach_to"]
    with pytest.raises(spec_module.SpecError):
        spec_module.normalize_spec(spec)


# ---------------------------------------- 前スカート(旧スクの前垂れ)


def test_apron_faces_the_front():
    """前中心に布が来ること。後ろに回っていたらそれはケープであって前垂れではない"""
    normalized, built = build_swimsuit()
    report = validate.costume_report(built, normalized)
    entry = next(e for e in report["parts"] if e["part"] == "Swimsuit_Apron")
    assert entry["hard"]["apron_faces_front"]["ok"]
    assert entry["hard"]["apron_centred"]["ok"]
    apron = part_named(built, "Swimsuit_Apron")
    nose = min(apron.verts, key=lambda point: point[1])
    assert nose[0] == pytest.approx(0.0, abs=1e-9)


def test_apron_top_arc_matches_the_draft():
    """上端の弧長は製図値(楕円弧)と一致する。角度割合の近似では合わない"""
    normalized, built = build_swimsuit()
    report = validate.costume_report(built, normalized)
    entry = next(e for e in report["parts"] if e["part"] == "Swimsuit_Apron")
    assert entry["hard"]["apron_top_arc"]["ok"], entry["hard"]["apron_top_arc"]


def test_apron_hangs_outside_the_brief():
    """下に着ている水着の面より外にあること(貫通したら交差ゲートが拾う)"""
    normalized, built = build_swimsuit()
    report = validate.costume_report(built, normalized)
    pair = next(
        item
        for item in report["part_pairs"]
        if {item["a"], item["b"]} == {"Swimsuit_Apron", "Swimsuit_Brief"}
    )
    assert pair["intersections"] == 0


def test_apron_arc_angles_stay_inside_one_turn():
    with pytest.raises(parts.PartError):
        parts._front_arc_angles(8, 7.0)


def test_apron_skips_the_periodic_check_and_says_why():
    """半周の弧の半径列は円周のスペクトルにならない。**キーを None にして外す**"""
    _normalized, built = build_swimsuit()
    apron = part_named(built, "Swimsuit_Apron")
    assert apron.design["radial_modulations"] is None
    report = validate.part_report(apron, 1.58)
    assert "no_unexpected_modulation" not in report["hard"]


def test_flattening_the_apron_breaks_the_arc_gate():
    """design はそのまま、弧を x 方向に潰す → 上端弧長のゲートが捕まえる"""
    _normalized, built = build_swimsuit()
    apron = copy.deepcopy(part_named(built, "Swimsuit_Apron"))
    apron.verts = [(x * 0.5, y, z) for x, y, z in apron.verts]
    after = validate.part_report(apron, 1.58)
    assert "apron_top_arc" in after["failed"]


# ---------------------------------------- 入力の解釈


def test_parse_reaches_the_swimsuit_preset():
    for text in ("スク水", "紺色のスクール水着", "ワンピース水着", "navy swimsuit", "競泳水着"):
        assert parse_text.parse(text)["base_preset"] == "swimsuit", text


def test_onepiece_dress_is_not_hijacked_by_the_swimsuit_vocabulary():
    """「ワンピース水着」は水着、「白いワンピース」はドレスのまま"""
    assert parse_text.parse("白いワンピース")["base_preset"] == "onepiece"
