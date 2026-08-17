"""ビキニショーツ(脚ぐりが脇へ上がるパンツ)のテスト。

定義は docs/garments.md のビキニショーツの節。足したコードは `leg_line`
(下端の境界を周方向の角度の関数にする)だけなので、テストもそこに集中する:
形が出ているか・ズボン側の読みが変わっていないか・preset の数値が定義どおりか。
"""

import math
import re

import pytest

from my_blender_plugin.costume import (
    modulate,
    parse_text,
    parts,
    spec as spec_module,
    validate,
)

FRONT = parts.FRONT_ANGLE


def build_panty(overrides=None):
    normalized = spec_module.load_preset("panty")
    if overrides:
        for part in normalized["parts"]:
            part["params"].update(overrides.get(part["name"], {}))
        normalized = spec_module.normalize_spec(normalized)
    return normalized, parts.build_all(normalized)


def panty_entry(built, normalized):
    report = validate.costume_report(built, normalized)
    return report, next(e for e in report["parts"] if e["part"] == "Panty_Body")


# ---------------------------------------- 層1: 純粋関数(脚ぐりの形)


def test_leg_line_shape_is_flat_at_the_gusset_and_high_at_the_sides():
    assert modulate.leg_line_shape(FRONT, FRONT) == pytest.approx(0.0)
    assert modulate.leg_line_shape(FRONT + math.pi, FRONT) == pytest.approx(0.0)
    for side in (FRONT + math.pi / 2.0, FRONT - math.pi / 2.0):
        assert modulate.leg_line_shape(side, FRONT) == pytest.approx(1.0)


def test_leg_line_shape_stays_inside_zero_to_one():
    for step in range(72):
        value = modulate.leg_line_shape(step * math.pi / 36.0, FRONT)
        assert 0.0 <= value <= 1.0


def test_leg_line_shape_is_an_s_curve_not_a_plain_sine():
    """smoothstep を掛けている理由。素の |sin| だとマチの真下から一定の傾きで
    上がり始めて「斜めに切っただけ」の形になる。S 字なので、マチ寄りでは
    |sin| より下に沈み(= マチの平ら)、脇寄りでは上に張り出す(= 脇の帯)"""
    for degrees in (10.0, 20.0):
        angle = FRONT + math.radians(degrees)
        assert modulate.leg_line_shape(angle, FRONT) < abs(math.sin(angle - FRONT))
    for degrees in (50.0, 70.0):
        angle = FRONT + math.radians(degrees)
        assert modulate.leg_line_shape(angle, FRONT) > abs(math.sin(angle - FRONT))


def test_leg_line_shape_keeps_the_cut_deep_in_the_middle_angles():
    """sin² を却下した根拠(docs のビキニショーツの節)。脇の頂点は sin² のほうが
    狭いが、中間の角度で脚ぐりが下がって正面の切れ込みが消える。45° で
    はっきり上にいること"""
    middle = FRONT + math.radians(45.0)
    assert modulate.leg_line_shape(middle, FRONT) > math.sin(middle - FRONT) ** 2 + 0.1


# ---------------------------------------- 層1: 純粋関数(尻の張り出し)


def test_seat_shape_is_behind_only():
    back = FRONT + math.pi
    assert modulate.seat_shape(back, FRONT) == pytest.approx(1.0)
    assert modulate.seat_shape(FRONT, FRONT) == pytest.approx(0.0)  # 腹は出ていない
    for side in (FRONT + math.pi / 2.0, FRONT - math.pi / 2.0):
        assert modulate.seat_shape(side, FRONT) == pytest.approx(0.0)
    for degrees in (10.0, 60.0, 89.0):  # 前半分はどこも 0
        assert modulate.seat_shape(FRONT + math.radians(degrees), FRONT) == 0.0


def test_seat_shape_leaves_no_crease_at_the_side():
    """脇(0 になる位置)で傾きも 0 であること。1乗だとここに折れ目が出る。
    脇の 1° 手前の値が、傾き 0 でない場合(≒ 1乗)の 1/10 未満"""
    just_behind = FRONT + math.pi / 2.0 + math.radians(1.0)
    linear = abs(math.cos(just_behind - FRONT))
    assert modulate.seat_shape(just_behind, FRONT) < linear / 10.0


def test_leg_line_shape_is_symmetric_left_to_right():
    for degrees in (10.0, 45.0, 80.0):
        offset = math.radians(degrees)
        assert modulate.leg_line_shape(FRONT + offset, FRONT) == pytest.approx(
            modulate.leg_line_shape(FRONT - offset, FRONT)
        )


# ---------------------------------------- 層2: ズボン側が動いていないこと


def test_trousers_keep_a_flat_leg_opening():
    """leg_line の既定は 0。ズボンの脚口は水平のままで、同じゲートが
    「水平に切れているか」の検査として効く"""
    normalized = spec_module.load_preset("pants")
    built = parts.build_all(normalized)
    report = validate.costume_report(built, normalized)
    entry = next(e for e in report["parts"] if e["part"] == "Pants_Body")
    assert report["failed"] == [], report["failed"]
    assert entry["measured_leg_line_l"] == pytest.approx(0.0, abs=1e-12)
    assert entry["hard"]["pants_leg_line_l"]["ok"]


def test_the_flat_cut_gate_catches_a_tilted_leg_opening():
    """上のゲートが反証可能であること。ズボンに脚ぐりを入れたら落ちる"""
    normalized = spec_module.load_preset("pants")
    body = next(part for part in normalized["parts"] if part["type"] == "pants")
    body["params"]["leg_line"] = 0.0  # design 側だけ 0 のまま実物を傾ける
    built = parts.build_all(spec_module.normalize_spec(normalized))
    tilted = next(mesh for mesh in built["parts"] if mesh.name == "Pants_Body")
    hem = tilted.rings["hem_l"]
    tilted.verts[hem[0]] = (
        tilted.verts[hem[0]][0],
        tilted.verts[hem[0]][1],
        tilted.verts[hem[0]][2] + 0.05,
    )
    entry = validate.part_report(tilted, 1.58)
    assert not entry["hard"]["pants_leg_line_l"]["ok"]


def test_trousers_stay_front_back_symmetric():
    """seat の既定は 0。ズボンの断面は前後対称の楕円のままで、同じゲートが
    「前後対称か」の検査として効く"""
    normalized = spec_module.load_preset("pants")
    report = validate.costume_report(parts.build_all(normalized), normalized)
    entry = next(e for e in report["parts"] if e["part"] == "Pants_Body")
    assert entry["measured_seat_ratio"] == pytest.approx(1.0, abs=1e-9)
    assert entry["hard"]["pants_seat_ratio"]["ok"]


def test_the_bridge_ratio_guard_rejects_a_gusset_that_would_self_intersect():
    """脚ぐりを持ち上げたとき、マチの分割が周方向と釣り合っていないと脚の縁が
    胴を突き抜ける。層1で FAIL にする前に生成側で止め、直し方を文で返す"""
    normalized, _built = build_panty()
    body = next(part for part in normalized["parts"] if part["type"] == "pants")
    body["params"]["crotch_segments"] = 24  # 外周 17 点に対してブリッジ 23 点
    with pytest.raises(parts.PartError) as caught:
        parts.build_all(spec_module.normalize_spec(normalized))
    assert "crotch_segments" in str(caught.value)

    # エラー文が案内した範囲で作り直すと、実際に自己交差が 0 になる
    wanted = re.search(r"crotch_segments を (\d+)〜(\d+)", str(caught.value))
    assert wanted, str(caught.value)
    for value in (int(wanted.group(1)), int(wanted.group(2))):
        body["params"]["crotch_segments"] = value
        built = parts.build_all(spec_module.normalize_spec(normalized))
        mesh = next(m for m in built["parts"] if m.name == "Panty_Body")
        report = validate.part_report(mesh, normalized["assumed_height"])
        assert report["self_intersections"] == 0, value


# 左右対称そのものの検査は tests/test_costume_symmetry.py に移した
# (全プリセットに掛かる普遍的な不変条件なので、パンティー固有の場所に置かない)。
# 元はここで見つけた: 脚ぐりを曲線にしたら左右の脚の周長が 0.16% 違い、
# 調べたら**ズボンでも元から 16mm ずれていた**


def side_silhouette(built):
    """脇(+X・y=0 の列)を上から下へ並べた (z, x)。全パーツを混ぜる"""
    points = sorted(
        (z, x)
        for mesh in built["parts"]
        for x, y, z in mesh.verts
        if abs(y) < 1e-9 and x > 0.0
    )
    points.reverse()
    merged = []
    for z, x in points:  # 接合リングは2パーツぶん同じ点が並ぶので畳む
        if merged and abs(merged[-1][0] - z) < 1e-12:
            continue
        merged.append((z, x))
    return merged


def side_slopes(built):
    """脇の輪郭の各区間の傾き(度、垂直が 0)"""
    points = side_silhouette(built)
    return [
        math.degrees(math.atan2(b[1] - a[1], a[0] - b[0]))
        for a, b in zip(points, points[1:])
    ]


def test_the_side_silhouette_has_no_crease():
    """脇の輪郭が滑らかであること(隣り合う区間の傾きの差が 5° 未満)。

    直した2つの折れ目がここに出ていた:
      - ウエストバンドと本体の境目 17°(本体のプロファイルが上端で傾き 0 から
        始まっていた。腰穿きなのにウエストとして読んでいたため)
      - 脚ぐりの縁 18°(縁を体に沿わせず真下に下ろしていた)
    どちらも層1のゲートは全通過していて、レンダーの拡大でしか見えなかった"""
    _normalized, built = build_panty()
    slopes = side_slopes(built)
    worst = max(abs(b - a) for a, b in zip(slopes, slopes[1:]))
    assert worst < 5.0, (worst, slopes)


def test_the_crease_test_can_fail():
    """上のテストが反証可能であること。腰穿きなのに waist_drop を 0 に戻すと
    (= 上端をウエストとして読むと)バンドとの境目に折れ目が戻る"""
    _normalized, built = build_panty(
        {"Panty_Waistband": {"waist_drop": 0.0}, "Panty_Body": {"waist_drop": 0.0}}
    )
    slopes = side_slopes(built)
    assert max(abs(b - a) for a, b in zip(slopes, slopes[1:])) > 10.0


def test_the_bridge_ratio_guard_leaves_trousers_alone():
    """水平に切った脚口では縁が真下を向くので突き抜けない。ズボンの
    crotch_segments 4(外周 17 点)はそのまま通ること"""
    normalized = spec_module.load_preset("pants")
    body = next(part for part in normalized["parts"] if part["type"] == "pants")
    assert body["params"]["crotch_segments"] == 4
    assert body["params"]["leg_line"] == 0.0
    built = parts.build_all(normalized)
    mesh = next(m for m in built["parts"] if m.name == "Pants_Body")
    assert validate.part_report(mesh, 1.58)["self_intersections"] == 0


def test_zero_leg_line_reproduces_the_ring_wise_profile():
    """頂点ごとにプロファイルを読むようにしたが、leg_line=0 では
    skirt_profile をリング単位に読んでいた従来と同じ座標になること"""
    normalized = spec_module.load_preset("pants")
    body_spec = next(part for part in normalized["parts"] if part["type"] == "pants")
    params = body_spec["params"]
    table = normalized_sizing(normalized)
    profile = modulate.skirt_profile(
        rings=params["hip_rings"],
        length=table["rise"] * params["rise_scale"],
        waist_perimeter=table["waist"],
        hip_perimeter=table["hip"],
        hip_drop=table["hip_drop"],
        hip_hug=1.0,
        flare=1.0,
        flare_curve=1.0,
        depth_ratio=table["depth_ratio"],
    )
    built = parts.build_all(normalized)
    mesh = next(m for m in built["parts"] if m.name == "Pants_Body")
    segments = params["segments"]
    for row, semi in enumerate(profile["semi_major"]):
        # 各行の +X 側先頭の頂点(角度 0)の x が長半径そのもの
        assert mesh.verts[row * segments][0] == pytest.approx(semi, abs=1e-12)


def normalized_sizing(normalized, ease=None):
    from my_blender_plugin.costume import sizing

    return sizing.resolve(
        normalized["assumed_height"],
        overrides=normalized.get("sizing"),
        ease=normalized["ease"] if ease is None else ease,
    )


# ---------------------------------------- 定義 #1: パンツと同じ骨格


def test_the_panty_has_one_waist_and_two_leg_openings():
    normalized, built = build_panty()
    report, entry = panty_entry(built, normalized)
    assert report["failed"] == [], report["failed"]
    assert entry["boundary_loops"] == 3
    assert entry["measured_hem_perimeter_l"] == pytest.approx(
        entry["measured_hem_perimeter_r"], rel=1e-6
    )


# ---------------------------------------- 定義 #2: 脚ぐりが脇へ上がる


def test_the_leg_opening_rises_towards_the_side():
    """ビキニ型かどうかを決めている唯一の形。脇の脚ぐりがマチより高い"""
    normalized, built = build_panty()
    _report, entry = panty_entry(built, normalized)
    assert entry["measured_leg_line_l"] > 0.0
    assert entry["hard"]["pants_leg_line_l"]["ok"]
    assert entry["hard"]["pants_leg_line_r"]["ok"]
    # 立ち上がりは布の丈の半分以上(寸胴のショーツと見間違えない量)
    assert entry["measured_leg_line_l"] > entry["measured_rise"] * 0.5


def test_the_rise_is_measured_at_the_gusset_not_the_average():
    """脚ぐりを持ち上げると股リングの平均 z は上がる。股上は**マチ**で測るので、
    平均で測っていたら設計値から外れて落ちる"""
    normalized, built = build_panty()
    _report, entry = panty_entry(built, normalized)
    body = next(mesh for mesh in built["parts"] if mesh.name == "Panty_Body")
    ring = body.rings["crotch"]
    zs = [body.verts[index][2] for index in ring]
    assert max(zs) - min(zs) > 0.0  # 股リングが水平でないこと(前提の確認)
    assert entry["measured_crotch_z"] == pytest.approx(min(zs), abs=1e-12)
    assert entry["hard"]["pants_rise"]["ok"]


def test_the_gusset_stays_on_the_front_and_back_centre():
    """マチ(下端の一番低いところ)が前中心・後ろ中心にあること。
    ここがずれると脚ぐりが左右にねじれる"""
    _normalized, built = build_panty()
    body = next(mesh for mesh in built["parts"] if mesh.name == "Panty_Body")
    ring = [body.verts[index] for index in body.rings["crotch"]]
    lowest = min(point[2] for point in ring)
    for point in ring:
        if point[2] < lowest + 1e-9:
            assert abs(point[0]) < 1e-9, point  # x = 0 の面に乗っている


# ---------------------------------------- 定義 #3: 尻が張り出している


def test_the_back_is_deeper_than_the_front():
    """人体の腰の断面は楕円ではなく後ろだけ出た卵形。丈が短くて腰しか無い
    ショーツでは、尻の輪郭がそのまま衣装の輪郭になる"""
    normalized, built = build_panty()
    _report, entry = panty_entry(built, normalized)
    body_spec = next(part for part in normalized["parts"] if part["type"] == "pants")
    assert entry["measured_seat_ratio"] == pytest.approx(
        1.0 + body_spec["params"]["seat"], rel=0.05
    )
    assert entry["hard"]["pants_seat_ratio"]["ok"]


def test_the_seat_grows_from_the_waist_down_to_the_hip():
    """尻はウエストでは 0、ヒップで満額。上端リングまで膨らませると
    ウエストの締まりが消える"""
    _normalized, built = build_panty()
    body = next(mesh for mesh in built["parts"] if mesh.name == "Panty_Body")

    def ratio(ring_name):
        ys = [body.verts[index][1] for index in body.rings[ring_name]]
        return max(ys) / abs(min(ys))

    assert ratio("top") == pytest.approx(1.0, abs=1e-9)  # 上端は前後対称のまま
    assert ratio("crotch") > 1.1


def test_turning_the_seat_off_makes_the_section_an_ellipse_again():
    """上の2つが反証可能であること。seat を 0 に戻すと前後対称に戻る"""
    normalized, built = build_panty({"Panty_Body": {"seat": 0.0}})
    _report, entry = panty_entry(built, normalized)
    assert entry["measured_seat_ratio"] == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------- 定義 #4: 脚が無い


def test_the_leg_opening_ends_just_below_the_crotch():
    """股下が股上の 0.2 未満。パンツは 2 倍以上あるので、この比が両者を分ける"""
    normalized, built = build_panty()
    _report, entry = panty_entry(built, normalized)
    assert entry["measured_inseam_l"] < entry["measured_rise"] * 0.2

    pants_normalized = spec_module.load_preset("pants")
    pants_report = validate.costume_report(
        parts.build_all(pants_normalized), pants_normalized
    )
    pants = next(e for e in pants_report["parts"] if e["part"] == "Pants_Body")
    assert pants["measured_inseam_l"] > pants["measured_rise"] * 2.0


def test_the_leg_opening_is_as_wide_as_the_thigh():
    """締めないゴム口なのでわたりと同じ太さ。細いと「裾の絞られた短パン」になる"""
    normalized, built = build_panty()
    _report, entry = panty_entry(built, normalized)
    thigh = built["sizing"]["thigh"]
    assert entry["measured_hem_perimeter_l"] == pytest.approx(thigh, rel=0.05)
    assert entry["measured_hem_perimeter_r"] == pytest.approx(thigh, rel=0.05)


def test_the_default_hem_scale_would_fail_the_gate():
    """脚口のゲートに歯があること。hem_scale を既定(スラックスの裾周 40cm)に
    戻すと、設計値と実測がずれてハードゲートが落ちる"""
    normalized, built = build_panty(
        {"Panty_Body": {"hem_scale": 1.0, "knee_scale": 1.0}}
    )
    report, _entry = panty_entry(built, normalized)
    assert "Panty_Body.pants_hem_l" in report["failed"], report["failed"]
    assert "Panty_Body.pants_hem_r" in report["failed"], report["failed"]


# ---------------------------------------- 定義 #4: 腰骨に乗る


def test_the_top_edge_sits_on_the_hip_bone_not_the_natural_waist():
    normalized, built = build_panty()
    band = next(part for part in normalized["parts"] if part["type"] == "waistband")
    height = normalized["assumed_height"]
    top_z = (band["params"]["waist_z"] + band["params"]["height"]) * height
    assert top_z / height == pytest.approx(0.58, abs=0.005)  # へその下・腰骨の上
    # 自然なウエスト(0.62H)ではない
    assert band["params"]["waist_z"] < 0.60


def test_the_top_ring_is_the_body_at_that_height_not_the_waist():
    """上端の胴回りが「その高さの体」であること。waist_drop を 0 に戻すと
    ウエスト周(64cm)で作られて 13cm 細くなり、腰に入らない"""
    normalized, built = build_panty()
    body = next(mesh for mesh in built["parts"] if mesh.name == "Panty_Body")
    table = normalized_sizing(normalized)
    body_spec = next(part for part in normalized["parts"] if part["type"] == "pants")
    drop = body_spec["params"]["waist_drop"] * normalized["assumed_height"]

    expected = modulate.follow_measure(
        1.0, drop, table["waist"], table["hip"], table["hip_drop"], 1.0
    )
    assert body.design["top_perimeter"] == pytest.approx(expected, rel=1e-9)
    assert expected > table["waist"] * 1.15  # ウエストよりはっきり太い

    # waist_drop を 0 に戻すとウエスト周そのものになる(= 効いていることの反証)
    _n, plain = build_panty(
        {"Panty_Waistband": {"waist_drop": 0.0}, "Panty_Body": {"waist_drop": 0.0}}
    )
    plain_body = next(m for m in plain["parts"] if m.name == "Panty_Body")
    assert plain_body.design["top_perimeter"] == pytest.approx(table["waist"], rel=1e-9)


def test_the_waistband_and_the_body_must_share_the_same_waist_drop():
    """バンドと本体で waist_drop がずれると、リング共有の接合が開く"""
    normalized, built = build_panty({"Panty_Waistband": {"waist_drop": 0.0}})
    report, _entry = panty_entry(built, normalized)
    assert not report["joints"][0]["ok"]


def test_the_garment_rise_shrinks_so_the_gusset_stays_at_the_body_crotch():
    """上端を下げたぶん股上が短くなること。短くしないとマチが股より下に行く"""
    normalized, built = build_panty()
    _report, entry = panty_entry(built, normalized)
    table = normalized_sizing(normalized)
    body_spec = next(part for part in normalized["parts"] if part["type"] == "pants")
    drop = body_spec["params"]["waist_drop"] * normalized["assumed_height"]
    assert entry["measured_rise"] == pytest.approx(table["rise"] - drop, rel=0.05)


def test_the_waistband_follows_the_body_taper():
    """バンドの flare が 1.0(円筒)だと、上端で体が細くなるぶんバンドだけ浮いて
    襟のように見える。上端が下端より細いこと"""
    _normalized, built = build_panty()
    band = next(mesh for mesh in built["parts"] if mesh.name == "Panty_Waistband")
    top = validate.ring_metrics(band.verts, band.rings["top"])
    bottom = validate.ring_metrics(band.verts, band.rings["bottom"])
    assert top["z"] > bottom["z"]
    assert top["perimeter"] < bottom["perimeter"]


# ---------------------------------------- 定義 #5: 股の位置は人体の股


def test_the_gusset_stays_at_the_body_crotch():
    """浅く見せるためにマチを上げていないこと。上げるとパンツの股と z が合わなくなる"""
    _normalized, built = build_panty()
    body = next(mesh for mesh in built["parts"] if mesh.name == "Panty_Body")
    pants_built = parts.build_all(spec_module.load_preset("pants"))
    pants_body = next(m for m in pants_built["parts"] if m.name == "Pants_Body")
    gusset_z = min(body.verts[index][2] for index in body.rings["crotch"])
    pants_crotch_z = min(
        pants_body.verts[index][2] for index in pants_body.rings["crotch"]
    )
    assert gusset_z == pytest.approx(pants_crotch_z, abs=1e-9)


# ---------------------------------------- 接合と材質


def test_the_waistband_shares_the_top_ring():
    normalized, built = build_panty()
    report, _entry = panty_entry(built, normalized)
    joint = report["joints"][0]
    assert joint["kind"] == "shared"
    assert joint["gap"] == pytest.approx(0.0, abs=1e-12), joint


def test_every_part_uses_the_same_material():
    """色語は materials["main"] にしか効かない。バンドを別材質にすると
    「赤いショーツ」が赤い本体 + 灰色のバンドになる(docs の実装メモ)"""
    spec = spec_module.load_preset("panty")
    assert {part["material"] for part in spec["parts"]} == {"main"}


# ---------------------------------------- 説明文から届くか


def test_parse_reaches_the_panty_preset():
    for text in ("パンティー", "白いショーツ", "下着", "panties", "white panty", "briefs"):
        assert parse_text.parse(text)["base_preset"] == "panty", text


def test_the_pants_vocabulary_is_not_hijacked():
    """「パンツ」はズボンのまま。下着の語を足したせいでズボンが作れなくなると困る"""
    for text in ("黒いズボン", "スラックス", "パンツ", "ハーフパンツ", "black pants"):
        assert parse_text.parse(text)["base_preset"] == "pants", text


def test_the_panty_vocabulary_does_not_leak_into_other_garments():
    # スカートの土台は skirt_flare(プリーツは preset ではなく params で入る)
    assert parse_text.parse("紺のプリーツスカート")["base_preset"] == "skirt_flare"
    assert parse_text.parse("スク水")["base_preset"] == "swimsuit"
    assert parse_text.parse("白いブラウス")["base_preset"] == "blouse"


def test_parsing_a_panty_is_confident_enough_to_skip_the_ai():
    """種類が当たれば confidence > 0。0 だと衣装不明のフォールバック扱いになり、
    形の手がかりが無いまま skirt_flare が返る"""
    assert parse_text.parse("パンティー")["confidence"] > 0.0


# ---------------------------------------- 残っている warn の理由


def test_the_axial_cv_warn_comes_from_the_short_leg_not_the_divisions():
    """docs に「定義 #3 の帰結なので消せない」と書いた根拠。

    軸方向のエッジ長がばらつくのは、胴(布の丈 15.8cm ÷ 6行)と脚ぐりの縁
    (2cm ÷ 6行)で7倍違うから。**分割はそのまま**で脚の丈だけを胴に釣り合う
    長さ(股下 ≒ 布の丈)にすると warn が消える = 原因は分割ではなく丈"""
    normalized, built = build_panty()
    _report, entry = panty_entry(built, normalized)
    assert not entry["warn"]["edge_length_cv_axial"]["ok"]

    table = normalized_sizing(normalized)
    balanced = entry["measured_rise"] / table["inseam"]  # 股下 = 布の丈 になる比
    normalized, built = build_panty(
        {"Panty_Body": {"inseam_scale": balanced, "leg_line": 0.0}}
    )
    _report, long_leg = panty_entry(built, normalized)
    assert long_leg["warn"]["edge_length_cv_axial"]["ok"]
