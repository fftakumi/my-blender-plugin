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
    "pleat_start": (float, 0.15, 0.0, 0.9),  # プリーツが開き始める位置(0=ウエスト)
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

PART_SCHEMAS = {
    "skirt_body": _SKIRT_BODY,
    "waistband": _WAISTBAND,
}

# パーツごとの「筒状かどうか」。法線の外向き判定を掛けてよいのは筒状パーツだけ。
TUBULAR_PART_TYPES = frozenset(("skirt_body", "waistband"))

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


def _coerce(value, field, schema, where, errors):
    """1フィールドを型変換して範囲を確認する。戻り値は採用する値"""
    expected, default, low, high = schema
    if value is None:
        return default
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

        result["parts"].append(
            {
                "type": part_type,
                "name": part_name,
                "material": material_key,
                "params": _normalize_table(
                    part.get("params"), PART_SCHEMAS[part_type], where + ".params", errors
                ),
            }
        )

    if not result["parts"]:
        raise SpecError("有効なパーツが1つもありません: " + " / ".join(errors))

    for index, part in enumerate(result["parts"]):
        errors.extend(_modulation_errors(part, "parts[%d]" % index))

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
            if joint[side + "_ring"] not in ("top", "bottom"):
                errors.append(
                    "%s.%s_ring は 'top' か 'bottom' です: %r" % (where, side, joint[side + "_ring"])
                )
        result["joints"].append(
            {
                "a": joint["a"],
                "a_ring": joint["a_ring"],
                "b": joint["b"],
                "b_ring": joint["b_ring"],
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
