import pytest

from my_blender_plugin.costume import parse_text, spec as spec_module, validate, parts


def body_of(spec):
    return next(part for part in spec["parts"] if part["type"] == "skirt_body")


def test_srgb_to_linear_endpoints():
    assert parse_text.srgb_to_linear(0.0) == 0.0
    assert parse_text.srgb_to_linear(1.0) == pytest.approx(1.0)
    # リニアは中間で暗くなる
    assert parse_text.srgb_to_linear(0.5) == pytest.approx(0.2140, abs=1e-3)


def test_hex_to_linear():
    assert parse_text.hex_to_linear("#000000") == (0.0, 0.0, 0.0)
    assert parse_text.hex_to_linear("ffffff") == pytest.approx((1.0, 1.0, 1.0))
    with pytest.raises(ValueError):
        parse_text.hex_to_linear("#fff")


def test_plain_skirt_is_recognised():
    result = parse_text.parse("スカート")
    assert result["matched"]["garment"] == ["スカート"]
    assert result["confidence"] > 0.0


def test_unknown_garment_gets_zero_confidence():
    result = parse_text.parse("かっこいい鎧")
    assert result["confidence"] == 0.0
    assert result["notes"]


def test_pleated_navy_mini_skirt():
    result = parse_text.parse("紺のプリーツミニスカート")
    body = body_of(result["spec"])
    assert body["params"]["pleats"] == 24
    assert body["params"]["length"] == pytest.approx(0.20)
    assert result["spec"]["materials"]["main"]["base_color"] == list(
        parse_text.hex_to_linear("#1b2a5e")
    )
    assert result["confidence"] >= 0.7


def test_english_input_works():
    result = parse_text.parse("long flared red skirt")
    body = body_of(result["spec"])
    assert body["params"]["length"] == pytest.approx(0.48)
    assert body["params"]["flare"] > 1.4
    assert "red" in result["matched"]["color"]


def test_tight_skirt_removes_flare_and_drape():
    result = parse_text.parse("黒のタイトスカート")
    body = body_of(result["spec"])
    assert body["params"]["flare"] == pytest.approx(1.0)
    assert body["params"]["drape_folds"] == 0


def test_explicit_pleat_count_is_used():
    result = parse_text.parse("32本プリーツのスカート")
    body = body_of(result["spec"])
    assert body["params"]["pleats"] == 32
    # 1山につき2分割以上、かつ割り切れること
    assert body["params"]["segments"] % 32 == 0
    assert body["params"]["segments"] >= 64


def test_pleat_count_keeps_the_waistband_joinable():
    result = parse_text.parse("18本プリーツのスカート")
    band = next(
        part for part in result["spec"]["parts"] if part["type"] == "waistband"
    )
    assert band["params"]["segments"] == body_of(result["spec"])["params"]["segments"]


def test_height_in_centimetres_and_metres():
    assert parse_text.parse("身長150cmのスカート")["spec"]["assumed_height"] == pytest.approx(1.5)
    assert parse_text.parse("身長1.7mのスカート")["spec"]["assumed_height"] == pytest.approx(1.7)


def test_fabric_words_change_the_material():
    result = parse_text.parse("サテンのロングスカート")
    material = result["spec"]["materials"]["main"]
    assert material["roughness"] == pytest.approx(0.24)
    assert material["sheen"] == pytest.approx(0.45)


def test_chiffon_becomes_translucent():
    result = parse_text.parse("シフォンのフレアスカート")
    assert result["spec"]["materials"]["main"]["alpha"] < 1.0


def test_multiple_colours_note_the_ignored_ones():
    result = parse_text.parse("白と黒のスカート")
    assert len(result["matched"]["color"]) >= 2
    assert any("複数" in note for note in result["notes"])


def test_longer_keyword_wins_over_substring():
    """「ひざ上」と「ひざ丈」のように部分一致する語で取り違えない"""
    assert parse_text.parse("ひざ上丈のスカート")["spec"] is not None
    result = parse_text.parse("膝丈のスカート")
    assert body_of(result["spec"])["params"]["length"] == pytest.approx(0.30)


def test_parse_rejects_non_string():
    with pytest.raises(TypeError):
        parse_text.parse(None)


# ---------------------------------------- 丈に応じた分割数


def test_longer_skirt_gets_more_rings():
    short = body_of(parse_text.parse("ミニスカート")["spec"])
    long_ = body_of(parse_text.parse("マキシスカート")["spec"])
    assert long_["params"]["rings"] > short["params"]["rings"]


def test_wider_flare_gets_more_segments():
    narrow = body_of(parse_text.parse("タイトスカート")["spec"])
    wide = body_of(parse_text.parse("サーキュラースカート")["spec"])
    assert wide["params"]["segments"] > narrow["params"]["segments"]


@pytest.mark.parametrize(
    "text", ["超ミニスカート", "ミニスカート", "ひざ丈スカート", "ロングスカート", "マキシスカート"]
)
def test_density_stays_in_the_measured_range_at_every_length(text):
    """丈を変えても分割数が固定だと密度がレンジ外に出る。丈から決めていること"""
    normalized = spec_module.normalize_spec(parse_text.parse(text)["spec"])
    report = validate.costume_report(parts.build_all(normalized), normalized)
    low, high = validate.EDGE_LENGTH_OVER_H_RANGE
    assert low <= report["edge_length_over_h"] <= high, report["edge_length_over_h"]
    assert report["failed"] == []


# ---------------------------------------- 解析結果がそのまま生成に通ること


@pytest.mark.parametrize(
    "text",
    [
        "紺のプリーツミニスカート",
        "白のロングフレアスカート",
        "黒のタイトスカート",
        "24本プリーツの赤いひざ丈スカート",
        "シフォンのサーキュラースカート 身長155cm",
        "デニムのAラインスカート",
        "ギャザースカート",
    ],
)
def test_parsed_specs_build_and_validate(text):
    result = parse_text.parse(text)
    normalized = spec_module.normalize_spec(result["spec"])
    built = parts.build_all(normalized)
    report = validate.costume_report(built, normalized)
    assert report["failed"] == [], (text, report["failed"])
