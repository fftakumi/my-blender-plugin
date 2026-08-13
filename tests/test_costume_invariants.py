"""衣装に依らない不変条件(validate.py)のテスト。

パーツごとの寸法ゲートは「定義した項目」しか見ないので、定義していない欠陥は
全ゲート緑のまま素通りする — ブラウス・スク水・ショーツで3回起きた失敗は全部これ。
ここで検査する4つの不変条件は衣装の定義を知らずに効くので、**未知の衣装が来ても
何も足さずに掛かる**。各ゲートには「出荷してしまった実際の欠陥を再現して
捕まえる」テストを付ける(捕まえないゲートは網ではなく飾り)。
"""

import pytest

from my_blender_plugin.costume import parts, spec as spec_module, validate


def report_for(preset, mutate=None):
    normalized = spec_module.load_preset(preset)
    if mutate:
        mutate(normalized)
        normalized = spec_module.normalize_spec(normalized)
    built = parts.build_all(normalized)
    return validate.costume_report(built, normalized), built


# ---------------------------------------- 体の突き抜け(body_clearance)


def test_body_clearance_catches_a_hip_garment_built_at_waist_size():
    """出荷した欠陥の再現: 腰穿き(0.56H)なのにウエスト周で作ると、
    体より 14cm 細いのに設計値どうしの照合は全部通ってしまった。
    素体はサイズ表から独立に引くので、設計値ごと間違っていても落ちる"""

    def strip_waist_drop(normalized):
        for part in normalized["parts"]:
            part["params"]["waist_drop"] = 0.0

    report, _built = report_for("panty", strip_waist_drop)
    assert report["verdict"] == "FAIL"
    assert any(key.startswith("body_clearance") for key in report["failed"])


def test_body_clearance_skips_rings_that_do_not_wrap_the_torso():
    """袖(腕を囲む)・脚口(脚を囲む)・エプロン(弧)は胴の寸法と比べては
    いけない。巻き角を体の軸で数えることで除外される(重心で数えた初版は
    この3種が誤検知した)"""
    for preset, part in (("blouse", "Sleeve"), ("panty", "hem"), ("swimsuit", "Apron")):
        report, _built = report_for(preset)
        for key in report["hard"]:
            if key.startswith("body_clearance"):
                assert part.lower() not in key.lower(), (preset, key)


def test_every_preset_passes_body_clearance():
    for preset in spec_module.list_presets():
        report, _built = report_for(preset)
        bad = [
            key
            for key, check in report["hard"].items()
            if key.startswith("body_clearance") and not check["ok"]
        ]
        assert bad == [], (preset, bad)


# ---------------------------------------- 左右対称(mirror)


def test_mirror_catches_a_shifted_vertex():
    """出荷した欠陥の再現: 左右の脚の位相ずれ(ズボンで 16mm)は寸法ゲートでは
    捕まらない。頂点を 5mm ずらすと warn が立つこと"""
    normalized = spec_module.load_preset("pants")
    built = parts.build_all(normalized)
    body = next(m for m in built["parts"] if m.name == "Pants_Body")
    index = body.rings["hem_l"][0]
    x, y, z = body.verts[index]
    body.verts[index] = (x + 0.005, y, z)
    report = validate.costume_report(built, normalized)
    check = report["warn"]["mirror.Pants_Body"]
    assert not check["ok"]
    assert check["value"] == pytest.approx(0.005, rel=0.01)


def test_mirror_pairs_left_with_right():
    """_L のパーツは自分ではなく相方 _R との鏡像で見る(袖は1本では非対称が正しい)"""
    report, _built = report_for("blouse")
    assert "mirror.Blouse_Sleeve_L" in report["warn"]
    assert "mirror.Blouse_Sleeve_R" not in report["warn"]  # 二重報告しない


# ---------------------------------------- 厚み付きシェル(shell_intersections)


def test_the_shell_gate_reproduces_the_layer2_only_crotch_failure(monkeypatch):
    """出荷した欠陥の再現: マチの分割比 0.56 は層1の素の面では交差 0 なのに、
    厚みを付けると股が折れて交差した(Blender の Solidify でしか見えなかった)。
    法線オフセットの近似で層1に引き寄せたので、素の面 0 のままここで落ちること"""
    monkeypatch.setattr(parts, "LEG_LINE_BRIDGE_RATIO", (0.0, 99.0))

    def coarse_gusset(normalized):
        for part in normalized["parts"]:
            if part["type"] == "pants":
                part["params"]["crotch_segments"] = 14

    report, _built = report_for("panty", coarse_gusset)
    entry = next(e for e in report["parts"] if e["part"] == "Panty_Body")
    assert entry["self_intersections"] == 0  # 層1の素の面は通る(だから見逃した)
    assert not report["warn"]["shell_intersections.Panty_Body"]["ok"]


def test_every_preset_passes_the_shell_gate():
    for preset in spec_module.list_presets():
        report, _built = report_for(preset)
        bad = [
            key
            for key, check in report["warn"].items()
            if key.startswith("shell_intersections") and not check["ok"]
        ]
        assert bad == [], (preset, bad)


def test_trim_parts_are_exempt_from_the_shell_gate():
    """ボタン(ドーム)は法線オフセットで必ず自己交差するが、Solidify を
    掛けない硬い部品なので対象外。誤検知で網を薄めない"""
    report, _built = report_for("blouse")
    assert "shell_intersections.Blouse_Buttons" not in report["warn"]


# ---------------------------------------- 接合の折れ角(測るだけ)


def test_joint_folds_are_measured_and_reported():
    """しきい値は引けない(襟は 63〜77° 折れるのが正しく、欠陥の 17° は
    プリーツの設計値 20° より小さい)ので、共通ゲートにせず**測って報告**する。
    衣装ごとの上限はその衣装のテストが持つ"""
    report, _built = report_for("panty")
    joint = report["joints"][0]
    assert joint["fold_degrees"] is not None
    assert joint["fold_degrees"] < 5.0  # 直した後のショーツはほぼまっすぐ

    report, _built = report_for("cape")
    collar = next(j for j in report["joints"] if "Collar" in j["b"])
    assert collar["fold_degrees"] > 45.0  # 襟の折れは設計(これが 0 なら襟が寝ている)
