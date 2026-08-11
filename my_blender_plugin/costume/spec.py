"""衣装 spec のスキーマ・既定値・検証。bpy 非依存。

spec は「衣装をどう作るか」を全部持つ1個の dict。ここがスキーマの権威で、
parts.py / kernels.py / build.py はここが通した spec しか受け取らない。
AI(ai_bridge)が返した JSON も必ず normalize_spec() を通してから使う。
"""

import hashlib
import json
import os

SCHEMA_VERSION = 1

#: 配布物に同梱する spec プリセットの置き場(zip に入る位置に置く)
PRESET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets")


class SpecError(ValueError):
    """spec が壊れているときに投げる"""


# ---------------------------------------------------------------- パーツのスキーマ
#
# (型, 既定値, 最小, 最大) の組。最小・最大は None で無制限。
# 「/H」と書いてあるものは assumed_height に対する比率(シーンのスケールに依存しない)。

_SKIRT_BODY = {
    # ウエストラインの高さ /H。実測のトップスの裾・ベルトは z/H 0.55-0.72
    "waist_z": (float, 0.62, 0.0, 1.0),
    # 丈 /H。0.27 なら裾は z/H 0.35(実測のミニスカートの裾 0.35-0.55 の下端)
    "length": (float, 0.27, 0.01, 1.0),
    "segments": (int, 24, 3, 256),  # 周方向の分割数
    "rings": (int, 10, 2, 128),  # 軸方向のリング本数(= 分割数+1)
    "flare": (float, 2.0, 1.0, 8.0),  # 裾のフィット周長 / ヒップ周長
    "flare_curve": (float, 1.6, 0.2, 6.0),  # >1 で裾側にぐっと広がる
    "hip_hug": (float, 1.0, 0.0, 1.0),  # 1 でヒップに沿う、0 でウエストから直線
    "pleats": (int, 0, 0, 128),  # プリーツの山数(折り目が立つ矩形波)。0 でフレアのみ
    "pleat_depth": (float, 0.0, 0.0, 1.0),  # 折り込みの深さ / その高さの長半径
    "pleat_duty": (float, 0.5, 0.05, 0.95),  # 1周期のうち外側(見える面)の割合
    # 縫い止め区間(丈に対する比)。実測: ウエストバンドから約9cm = 丈の 0.22
    "pleat_stitch_down": (float, 0.22, 0.0, 0.9),
    # 縫い止め区間での開き具合。**0 にしてはいけない** — 実物は腰でも折り目線が見える
    "pleat_closed": (float, 0.35, 0.05, 1.0),
    # ドレープ(布のたるみ)。折り目の立たないなだらかな波。プリーツと併用できる
    "drape_folds": (int, 0, 0, 64),
    "drape_depth": (float, 0.0, 0.0, 0.5),  # 波の深さ / その高さの長半径
}

_WAISTBAND = {
    "waist_z": (float, 0.62, 0.0, 1.0),
    "height": (float, 0.035, 0.002, 0.3),  # バンドの高さ /H
    "segments": (int, 24, 3, 256),
    "rings": (int, 2, 2, 32),
    "flare": (float, 1.0, 0.5, 2.0),  # 上端に対する下端のフィット周長比
}

#: ブラウスの胴。前が開いた筒に袖ぐりの穴を2つ開ける(定義は docs/garments.md)
_BODICE = {
    "shoulder_z": (float, 0.82, 0.3, 1.0),  # 肩の高さ /H
    # 着丈は **サイズ表の製品実寸**から決まる。ここは倍率だけ(勘で置いた
    # hem_z がクロップ丈の原因だったので、絶対値を spec に書かせない)
    "length_scale": (float, 1.0, 0.3, 2.0),
    "segments": (int, 28, 8, 256),
    "rings": (int, 12, 3, 128),
    "bust_t": (float, 0.28, 0.0, 1.0),  # バストが来る軸方向の位置
    "waist_t": (float, 0.62, 0.0, 1.0),  # ウエストが来る軸方向の位置
    "waist_scale": (float, 0.93, 0.3, 2.0),  # ウエストの周長 / バスト
    "hem_scale": (float, 0.99, 0.3, 2.0),  # 裾の周長 / バスト
    "neck_ease": (float, 0.12, 0.0, 1.0),  # 襟ぐりのゆとり(首の素寸に対する比)
    "neck_depth_ratio": (float, 0.85, 0.2, 1.0),
    "shoulder_depth_ratio": (float, 0.55, 0.2, 1.0),  # 肩線の断面 前後/左右
    # 襟ぐりの前下がり / サイズ表の値。0 にすると水平な輪 = 煙突に見える
    "neck_drop_scale": (float, 1.0, 0.0, 3.0),
    "neck_fade": (float, 0.09, 0.01, 0.5),  # 前下がりが消えるまでの垂直距離 /H
    # 前開きの隙間の幅 /H。0.006 = 0.95cm。前立て(3.0cm)より細くすること
    "front_gap": (float, 0.006, 0.0005, 0.06),
    # 胸のふくらみ。**設計値**(製品実寸表にも製図資料にも「前へ何cm出るか」は無い)。
    # 0.022 = 3.5cm 相当。0 にすると楕円断面だけの胴 = メンズシャツに見える
    "bust_projection": (float, 0.022, 0.0, 0.12),
    "bust_angular_degrees": (float, 62.0, 5.0, 175.0),  # 前中心から左右にこの角度まで
    "bust_axial_width": (float, 0.26, 0.02, 0.9),  # 軸方向にこの幅まで(t の単位)
    # 裾のシャツテール。**設計値**(製品実寸表に裾のカーブの深さは無い)。
    # 6cm 相当。前後を長くして脇を短くしないと「筒を切った」ようにしか見えない
    "shirttail_drop": (float, 0.038, 0.0, 0.15),
    "shirttail_front_ratio": (float, 0.85, 0.0, 1.5),  # 前の落差 / 後ろの落差
    "shirttail_fade": (float, 0.10, 0.01, 0.5),  # カーブが効き始める高さ /H
    "armhole_segments": (int, 3, 1, 32),  # 袖ぐりが占める周方向の分割数
}

#: 袖。**袖ぐりの実寸から**作るので attach_to が必須
_SLEEVE = {
    "side": (str, "l", None, None),  # "l" か "r"
    # 周方向の分割数は指定しない。袖ぐりの境界頂点数で決まる
    "rings": (int, 11, 3, 128),
    "cap_fraction": (float, 0.22, 0.05, 0.9),  # 袖山(穴の形→円)に使う袖丈の割合
    "elbow_fraction": (float, 0.50, 0.1, 0.95),  # ここまで二の腕の太さを保つ(肘)
    "cuff_start": (float, 0.90, 0.2, 0.999),  # ここから下が一定半径 = カフスの帯
    # 袖をカフスに縫い込むときのいせ込み。1.0 だと段差が出ず、幾何としては
    # 帯が在るのに絵ではカフスに見えない
    "cuff_gather": (float, 1.30, 1.0, 2.5),
    "sleeve_ease": (float, 0.10, 0.0, 0.5),  # 袖山のいせ込み(縫い目長の検算に使う)
    # 二の腕の太さ / **サイズ表の袖幅**。袖ぐりの穴は矩形なので周長が実物の
    # 袖ぐり寸法より3割大きく、そこから出すとコウモリ袖に膨らむ
    "bicep_scale": (float, 1.0, 0.3, 2.0),
    "length_scale": (float, 1.0, 0.05, 1.5),  # 袖丈 / サイズ表の袖丈
    "cuff_scale": (float, 1.0, 0.3, 3.0),  # 袖口 / サイズ表の袖口
    "droop_degrees": (float, 35.0, -30.0, 80.0),  # 水平からの下がり角(姿勢は設計値)
}

#: 台襟(立ち襟)。**襟ぐりの実物に乗せる**ので attach_to が必須
_COLLAR = {
    "height_ratio": (float, 0.022, 0.002, 0.15),  # 台襟の高さ /H。3.5cm 相当
    "rings": (int, 3, 2, 32),
    "flare": (float, 1.06, 0.8, 2.0),  # 上端 / 下端 の差し渡し比
}

#: 襟の羽根(折り返し)。台襟の上端から下へ倒れる。attach_to は台襟
_COLLAR_FALL = {
    "height_ratio": (float, 0.030, 0.002, 0.15),  # 羽根の丈 /H。4.7cm 相当
    "rings": (int, 3, 2, 32),
    "flare": (float, 1.34, 0.8, 3.0),  # 先端 / 折り返し の差し渡し比
}

#: 前立て。胴の前面に沿う帯。attach_to が必須
_PLACKET = {
    "width": (float, 0.019, 0.004, 0.08),  # 帯の幅 /H。3.0cm 相当
    "standoff": (float, 0.0016, 0.0, 0.02),  # 胴の面からどれだけ前へ出すか /H
    "bulge": (float, 0.18, 0.0, 1.0),  # 中央のふくらみ / 幅
    "top_extend": (float, 0.0, 0.0, 0.1),  # 襟ぐりより上へ伸ばす量 /H
    "columns": (int, 5, 3, 32),  # 幅方向の分割数
    # 縦の分割数は指定しない。胴の前面プロファイルの刻みで決まる(食い込み防止)
}

#: ボタン列。前立てに付くので attach_to は前立て。
#: 形は実物のシャツ用貝ボタン(直径11.5mm / 厚み2.3mm / 4つ穴)に合わせる
_BUTTONS = {
    "count": (int, 6, 1, 24),
    "radius": (float, 0.00364, 0.0005, 0.02),  # 半径 /H。直径 11.5mm 相当
    "segments": (int, 16, 6, 64),  # 穴の数の倍数にすること(等間隔に置けない)
    "standoff": (float, 0.0004, 0.0, 0.01),  # 前立ての面からどれだけ前へ出すか /H
    "top_inset": (float, 0.06, 0.0, 0.5),  # 上端からの余白 / 前立ての丈
    "bottom_inset": (float, 0.10, 0.0, 0.5),  # 下端からの余白 / 前立ての丈
    "thickness_ratio": (float, 0.20, 0.05, 0.6),  # 厚み / 直径。実物 2.3/11.5
    "dish": (float, 0.35, 0.0, 0.9),  # 中央の凹み / 厚み
    "holes": (int, 4, 0, 8),  # 穴の数。シャツは4つ穴が標準
}

PART_SCHEMAS = {
    "skirt_body": _SKIRT_BODY,
    "waistband": _WAISTBAND,
    "bodice": _BODICE,
    "sleeve": _SLEEVE,
    "collar": _COLLAR,
    "collar_fall": _COLLAR_FALL,
    "placket": _PLACKET,
    "buttons": _BUTTONS,
}

#: 他のパーツの**実物**から作るパーツ。spec に attach_to が必須
DEPENDENT_PART_TYPES = frozenset(
    ("sleeve", "collar", "collar_fall", "placket", "buttons")
)

#: joints で指定できる境界リングの名前
JOINT_RING_NAMES = frozenset(("top", "bottom", "armhole_l", "armhole_r"))

#: 接合の種類。
#:   shared … 頂点座標が一致している必要がある(ウエストバンドとスカートのように
#:            同じ分割数で作ったリングを共有する)
#:   sewn  … 座標の一致は求めない。**縫い合わせ**なので分割数が違ってよい
#:            (袖ぐりと袖山。縫製でも袖山にはいせ込みが入って長さが違う)。
#:            交差の検査からは外す
JOINT_KINDS = frozenset(("shared", "sewn"))

_MATERIAL_SCHEMA = {
    "base_color": (list, [0.16, 0.19, 0.35], None, None),  # linear RGB
    "roughness": (float, 0.72, 0.0, 1.0),
    "sheen": (float, 0.25, 0.0, 1.0),  # 布の艶。Principled の Sheen Weight
    "metallic": (float, 0.0, 0.0, 1.0),
    "alpha": (float, 1.0, 0.0, 1.0),
    "double_sided": (bool, True, None, None),  # 片面シートなので既定で両面表示
}

_TOP_LEVEL_DEFAULTS = {
    # 1 unit = 何メートルか。ユーザーのシーンが 1unit=1cm なら 0.01 を渡す
    "meters_per_unit": (float, 1.0, 1e-6, 1e6),
    # 単体の衣装に身長は無いので、H比の基準として spec に明示させる。
    # 1.53 は参照実測(VRM 10体)の身長レンジ 1.36-1.75 の中央付近。
    "assumed_height": (float, 1.53, 0.1, 100.0),
    # ゆとり(周長に対する比率)。寸法の検算はゆとり込みの値に対して行う
    "ease": (float, 0.03, 0.0, 1.0),
    "poly_budget": (int, 3000, 1, 1000000),
}


# ------------------------------------------------------------------- 既定の spec


def default_spec(name="skirt_flare"):
    """フレアスカート(ウエストバンド + 本体)の既定 spec を返す"""
    return {
        "schema": SCHEMA_VERSION,
        "name": name,
        "materials": {"main": {}},
        "parts": [
            {"type": "waistband", "name": "Skirt_Waistband", "material": "main", "params": {}},
            {"type": "skirt_body", "name": "Skirt_Body", "material": "main", "params": {}},
        ],
        # 接合: バンドの下端リングとスカート本体の上端リングを共有する
        "joints": [
            {"a": "Skirt_Waistband", "a_ring": "bottom", "b": "Skirt_Body", "b_ring": "top"}
        ],
    }


# --------------------------------------------------------------------- 検証


#: 文字列で選択肢が決まっているフィールド
_ENUM_FIELDS = {"side": ("l", "r")}


def _coerce(value, field, schema, where, errors):
    """1フィールドを型変換して範囲を確認する。戻り値は採用する値"""
    expected, default, low, high = schema
    if value is None:
        return default
    if expected is str:
        allowed = _ENUM_FIELDS.get(field)
        if not isinstance(value, str) or (allowed and value not in allowed):
            errors.append(
                "%s.%s は %s のいずれかにしてください: %r"
                % (where, field, " / ".join(allowed) if allowed else "文字列", value)
            )
            return default
        return value
    if expected is bool:
        if not isinstance(value, bool):
            errors.append("%s.%s は真偽値で指定してください: %r" % (where, field, value))
            return default
        return value
    if expected is list:
        if not isinstance(value, (list, tuple)) or len(value) != len(default):
            errors.append(
                "%s.%s は長さ %d のリストで指定してください: %r"
                % (where, field, len(default), value)
            )
            return list(default)
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            errors.append("%s.%s に数値以外が入っています: %r" % (where, field, value))
            return list(default)
    if isinstance(value, bool):  # bool は int のサブクラスなので先に弾く
        errors.append("%s.%s は数値で指定してください: %r" % (where, field, value))
        return default
    try:
        coerced = expected(value)
    except (TypeError, ValueError):
        errors.append("%s.%s は %s で指定してください: %r" % (where, field, expected.__name__, value))
        return default
    if low is not None and coerced < low:
        errors.append("%s.%s は %s 以上にしてください(現在 %s)" % (where, field, low, coerced))
        return default
    if high is not None and coerced > high:
        errors.append("%s.%s は %s 以下にしてください(現在 %s)" % (where, field, high, coerced))
        return default
    return coerced


def _normalize_table(raw, schema, where, errors):
    """スキーマ表どおりに dict を埋める。未知のキーはエラーにする"""
    raw = raw or {}
    if not isinstance(raw, dict):
        errors.append("%s は辞書で指定してください: %r" % (where, raw))
        raw = {}
    for key in raw:
        if key not in schema:
            errors.append(
                "%s に未知のキー %r があります(使えるのは %s)"
                % (where, key, ", ".join(sorted(schema)))
            )
    return {
        field: _coerce(raw.get(field), field, field_schema, where, errors)
        for field, field_schema in schema.items()
    }


def _modulation_errors(part, where):
    """周方向の変調が分割数で表現できるかを確かめる。

    分割数が足りないと**折り返し(エイリアシング)**が起きて、頼んだ山数とは違う
    数の波が出る(実測: 32分割で 20山を頼むと 12山に化けた)。黙って別の形を作るより
    エラーにする。
    """
    params = part.get("params") or {}
    segments = params.get("segments")
    if not segments:
        return []

    errors = []
    pleats = params.get("pleats") or 0
    if pleats:
        if segments % pleats != 0:
            errors.append(
                "%s: segments(%d) は pleats(%d) の倍数にしてください"
                "(折り目が周方向にずれます)" % (where, segments, pleats)
            )
        if segments < pleats * 2:
            errors.append(
                "%s: プリーツ %d 山には segments が %d 以上必要です(現在 %d)。"
                "1山に外側の面と折り込みの2分割が要ります"
                % (where, pleats, pleats * 2, segments)
            )
    folds = params.get("drape_folds") or 0
    if folds and segments < folds * 2:
        errors.append(
            "%s: ドレープ %d 山には segments が %d 以上必要です(現在 %d)。"
            "足りないと折り返して別の山数になります" % (where, folds, folds * 2, segments)
        )

    # 山数と深さは**両方**指定するか、両方 0 か。片方だけだと形が出ないのに
    # 「プリーツを頼んだ」ことになり、周波数のゲート(modulation_pleat_k)が
    # 無言で消える。docs/garments.md の罠表2行目(設計値が 0 だとゲートごと消える)
    # と同じ形なので、spec の段階で止める
    for count_field, depth_field, label in (
        ("pleats", "pleat_depth", "プリーツ"),
        ("drape_folds", "drape_depth", "ドレープ"),
    ):
        count = params.get(count_field) or 0
        depth = params.get(depth_field) or 0.0
        if count and depth <= 0.0:
            errors.append(
                "%s: %s の山数(%s=%d)を指定したのに深さ(%s)が 0 です。"
                "形が出ないうえ周波数の検証項目も消えます"
                % (where, label, count_field, count, depth_field)
            )
        if depth > 0.0 and not count:
            errors.append(
                "%s: %s の深さ(%s=%.3f)を指定したのに山数(%s)が 0 です"
                % (where, label, depth_field, depth, count_field)
            )
    return errors


def normalize_spec(spec):
    """spec を既定値で埋め、型と範囲を検証した新しい dict を返す。

    壊れていれば SpecError。AI が返した JSON はここを通らなければ使わない。
    """
    errors = []
    if not isinstance(spec, dict):
        raise SpecError("spec は辞書である必要があります: %r" % (spec,))

    schema_version = spec.get("schema", SCHEMA_VERSION)
    if schema_version != SCHEMA_VERSION:
        errors.append(
            "schema が %r です。このプラグインが読めるのは %d だけです"
            % (schema_version, SCHEMA_VERSION)
        )

    name = spec.get("name", "costume")
    if not isinstance(name, str) or not name.strip():
        errors.append("name は空でない文字列にしてください: %r" % (name,))
        name = "costume"

    result = {"schema": SCHEMA_VERSION, "name": name}
    for field, field_schema in _TOP_LEVEL_DEFAULTS.items():
        result[field] = _coerce(spec.get(field), field, field_schema, "spec", errors)

    # サイズ表の上書き。中身の検証は sizing.py が持つ(単位付きの実寸もあるため)
    sizing = spec.get("sizing", {})
    if not isinstance(sizing, dict):
        errors.append("sizing は辞書で指定してください: %r" % (sizing,))
        sizing = {}
    result["sizing"] = dict(sizing)

    materials = spec.get("materials") or {"main": {}}
    if not isinstance(materials, dict) or not materials:
        errors.append("materials は空でない辞書で指定してください: %r" % (materials,))
        materials = {"main": {}}
    result["materials"] = {
        key: _normalize_table(value, _MATERIAL_SCHEMA, "materials.%s" % key, errors)
        for key, value in materials.items()
    }

    parts = spec.get("parts")
    if not isinstance(parts, list) or not parts:
        raise SpecError(
            "parts に最低1つのパーツが必要です: %r\n" % (parts,)
            + ("他のエラー: " + " / ".join(errors) if errors else "")
        )

    result["parts"] = []
    seen_names = set()
    for index, part in enumerate(parts):
        where = "parts[%d]" % index
        if not isinstance(part, dict):
            errors.append("%s は辞書で指定してください: %r" % (where, part))
            continue
        part_type = part.get("type")
        if part_type not in PART_SCHEMAS:
            errors.append(
                "%s.type が %r です。使えるのは %s"
                % (where, part_type, ", ".join(sorted(PART_SCHEMAS)))
            )
            continue
        part_name = part.get("name") or "%s_%02d" % (part_type, index + 1)
        if not isinstance(part_name, str) or not part_name.strip():
            errors.append("%s.name は空でない文字列にしてください: %r" % (where, part_name))
            part_name = "%s_%02d" % (part_type, index + 1)
        if part_name in seen_names:
            errors.append("パーツ名 %r が重複しています" % (part_name,))
        seen_names.add(part_name)

        material_key = part.get("material", "main")
        if material_key not in result["materials"]:
            errors.append(
                "%s.material が %r ですが materials に定義がありません" % (where, material_key)
            )
            material_key = sorted(result["materials"])[0]

        entry = {
            "type": part_type,
            "name": part_name,
            "material": material_key,
            "params": _normalize_table(
                part.get("params"), PART_SCHEMAS[part_type], where + ".params", errors
            ),
        }
        if part_type in DEPENDENT_PART_TYPES:
            # 袖は袖ぐりの実寸から作るので、どのパーツに付くかが必須
            entry["attach_to"] = part.get("attach_to")
            if not isinstance(entry["attach_to"], str) or not entry["attach_to"]:
                errors.append(
                    "%s: %s は attach_to に胴パーツの名前を指定してください"
                    % (where, part_type)
                )
        elif part.get("attach_to"):
            errors.append(
                "%s: %s は attach_to を取りません(取るのは %s)"
                % (where, part_type, ", ".join(sorted(DEPENDENT_PART_TYPES)))
            )
        result["parts"].append(entry)

    if not result["parts"]:
        raise SpecError("有効なパーツが1つもありません: " + " / ".join(errors))

    for index, part in enumerate(result["parts"]):
        errors.extend(_modulation_errors(part, "parts[%d]" % index))
        target = part.get("attach_to")
        if target and target not in seen_names:
            errors.append(
                "parts[%d].attach_to が未知のパーツ名です: %r(あるのは %s)"
                % (index, target, ", ".join(sorted(seen_names)))
            )

    result["joints"] = []
    for index, joint in enumerate(spec.get("joints") or []):
        where = "joints[%d]" % index
        if not isinstance(joint, dict):
            errors.append("%s は辞書で指定してください: %r" % (where, joint))
            continue
        missing = [key for key in ("a", "a_ring", "b", "b_ring") if key not in joint]
        if missing:
            errors.append("%s に %s がありません" % (where, ", ".join(missing)))
            continue
        for side in ("a", "b"):
            if joint[side] not in seen_names:
                errors.append("%s.%s が未知のパーツ名です: %r" % (where, side, joint[side]))
            if joint[side + "_ring"] not in JOINT_RING_NAMES:
                errors.append(
                    "%s.%s_ring は %s のいずれかです: %r"
                    % (where, side, " / ".join(sorted(JOINT_RING_NAMES)), joint[side + "_ring"])
                )
        kind = joint.get("kind", "shared")
        if kind not in JOINT_KINDS:
            errors.append(
                "%s.kind は %s のいずれかです: %r"
                % (where, " / ".join(sorted(JOINT_KINDS)), kind)
            )
            kind = "shared"
        result["joints"].append(
            {
                "a": joint["a"],
                "a_ring": joint["a_ring"],
                "b": joint["b"],
                "b_ring": joint["b_ring"],
                "kind": kind,
            }
        )

    if errors:
        raise SpecError("spec に問題があります:\n- " + "\n- ".join(errors))
    return result


def spec_errors(spec):
    """検証だけして問題の一覧を返す(例外を投げない版)"""
    try:
        normalize_spec(spec)
    except SpecError as error:
        return str(error).splitlines()
    return []


def list_presets():
    """同梱プリセットの名前一覧"""
    if not os.path.isdir(PRESET_DIR):
        return []
    return sorted(
        name[: -len(".json")] for name in os.listdir(PRESET_DIR) if name.endswith(".json")
    )


def load_preset(name):
    """同梱プリセットを読んで正規化済み spec を返す。

    日本語 Windows の既定は cp932 なので encoding は必ず明示する。
    """
    path = os.path.join(PRESET_DIR, name + ".json")
    if not os.path.isfile(path):
        raise SpecError(
            "プリセット %r がありません(あるのは %s)" % (name, ", ".join(list_presets()))
        )
    with open(path, "r", encoding="utf-8") as handle:
        return normalize_spec(json.load(handle))


def load_spec_file(path):
    """任意の spec JSON ファイルを読んで正規化済み spec を返す"""
    with open(path, "r", encoding="utf-8") as handle:
        return normalize_spec(json.load(handle))


def spec_hash(spec):
    """spec の内容から決定的な短いハッシュを作る(レポートの再現性確認用)"""
    canonical = json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def json_schema_hint():
    """AI に渡す用のスキーマ説明を組み立てる(プロンプトに埋める)"""
    lines = [
        "衣装 spec は以下の JSON です。数値は assumed_height に対する比率です。",
        '{"schema": %d, "name": "<英数字の名前>",' % SCHEMA_VERSION,
        ' "assumed_height": <身長 m>, "ease": <ゆとり比>, "meters_per_unit": 1.0,',
        ' "materials": {"<キー>": {%s}},'
        % ", ".join(
            '"%s": <%s>' % (field, schema[0].__name__) for field, schema in _MATERIAL_SCHEMA.items()
        ),
        ' "parts": [{"type": "<種別>", "name": "<オブジェクト名>",'
        ' "material": "<materialsのキー>", "params": {...}}],',
        ' "joints": [{"a": "<パーツ名>", "a_ring": "top|bottom",'
        ' "b": "<パーツ名>", "b_ring": "top|bottom"}]}',
        "",
        "type ごとに使える params(型・既定値・最小・最大):",
    ]
    for part_type, schema in sorted(PART_SCHEMAS.items()):
        lines.append("  %s:" % part_type)
        for field, (expected, default, low, high) in schema.items():
            lines.append(
                "    %s: %s 既定 %s 範囲 %s〜%s"
                % (field, expected.__name__, default, low, high)
            )
    lines.append("")
    lines.append("上記以外のキーを入れてはいけません。JSON 以外の文字を出力してはいけません。")
    return "\n".join(lines)
