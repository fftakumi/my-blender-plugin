"""説明文 → spec。日本語・英語のキーワード辞書だけで解く。bpy 非依存。

**ここで解ければ AI を呼ばない。** 辞書に当たらなかったときだけ ai_bridge に回す。
どこまで辞書で解けたかを `confidence` と `matched` で返すので、呼び出し側が判断できる。
"""

import re

from . import spec as spec_module


def srgb_to_linear(value):
    """sRGB(0-1)を Blender のベースカラーが期待するリニア値に直す"""
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def hex_to_linear(code):
    """"#1b2a5e" のような16進表記をリニア RGB のタプルにする"""
    code = code.lstrip("#")
    if len(code) != 6:
        raise ValueError("色は #rrggbb 形式で指定してください: %r" % (code,))
    return tuple(
        round(srgb_to_linear(int(code[index : index + 2], 16) / 255.0), 6)
        for index in (0, 2, 4)
    )


#: 色名 → sRGB 16進。日本語と英語の両方を引く
COLOR_WORDS = {
    "白": "#f2f2f0", "ホワイト": "#f2f2f0", "white": "#f2f2f0",
    "黒": "#1a1a1c", "ブラック": "#1a1a1c", "black": "#1a1a1c",
    "紺": "#1b2a5e", "ネイビー": "#1b2a5e", "navy": "#1b2a5e",
    "青": "#2f5fd0", "ブルー": "#2f5fd0", "blue": "#2f5fd0",
    "水色": "#8fc7e8", "ライトブルー": "#8fc7e8", "lightblue": "#8fc7e8",
    "赤": "#b62431", "レッド": "#b62431", "red": "#b62431",
    "えんじ": "#7a1f2b", "ワインレッド": "#7a1f2b", "burgundy": "#7a1f2b",
    "ピンク": "#e88ba6", "pink": "#e88ba6",
    "緑": "#2f7a4a", "グリーン": "#2f7a4a", "green": "#2f7a4a",
    "黄": "#e0be3a", "黄色": "#e0be3a", "イエロー": "#e0be3a", "yellow": "#e0be3a",
    "紫": "#6b4a9c", "パープル": "#6b4a9c", "purple": "#6b4a9c",
    "灰": "#8a8a8e", "グレー": "#8a8a8e", "gray": "#8a8a8e", "grey": "#8a8a8e",
    "茶": "#6b4a33", "ブラウン": "#6b4a33", "brown": "#6b4a33",
    "ベージュ": "#d8c3a3", "beige": "#d8c3a3",
    "クリーム": "#eee3c8", "cream": "#eee3c8",
}

#: 素材感 → マテリアルの上書き。実測値ではなく見た目の設計値
FABRIC_WORDS = {
    "シフォン": {"roughness": 0.42, "sheen": 0.55, "alpha": 0.85},
    "chiffon": {"roughness": 0.42, "sheen": 0.55, "alpha": 0.85},
    "サテン": {"roughness": 0.24, "sheen": 0.45},
    "satin": {"roughness": 0.24, "sheen": 0.45},
    "シルク": {"roughness": 0.28, "sheen": 0.5},
    "silk": {"roughness": 0.28, "sheen": 0.5},
    "デニム": {"roughness": 0.86, "sheen": 0.1},
    "denim": {"roughness": 0.86, "sheen": 0.1},
    "ウール": {"roughness": 0.9, "sheen": 0.35},
    "wool": {"roughness": 0.9, "sheen": 0.35},
    "コットン": {"roughness": 0.78, "sheen": 0.2},
    "cotton": {"roughness": 0.78, "sheen": 0.2},
    "レザー": {"roughness": 0.35, "sheen": 0.05, "metallic": 0.05},
    "leather": {"roughness": 0.35, "sheen": 0.05, "metallic": 0.05},
}

#: 丈 → 丈 / 身長。実測の裾の高さ z/H(ミニ 0.35-0.55、ロング 0.10-0.35)から逆算した
LENGTH_WORDS = {
    "マイクロミニ": 0.14, "超ミニ": 0.14,
    "ミニ": 0.20, "mini": 0.20, "short": 0.20,
    "ひざ上": 0.25, "膝上": 0.25,
    "ひざ丈": 0.30, "膝丈": 0.30, "knee": 0.30,
    "ミディ": 0.38, "midi": 0.38,
    "ロング": 0.48, "long": 0.48, "マキシ": 0.52, "maxi": 0.52,
}

#: シルエット → skirt_body のパラメータ上書き
SILHOUETTE_WORDS = {
    "プリーツ": {"pleats": 24, "pleat_depth": 0.22, "flare": 1.5, "segments": 48},
    "pleated": {"pleats": 24, "pleat_depth": 0.22, "flare": 1.5, "segments": 48},
    "フレア": {"flare": 1.55, "flare_curve": 1.35, "drape_folds": 14, "drape_depth": 0.065},
    "flare": {"flare": 1.55, "flare_curve": 1.35, "drape_folds": 14, "drape_depth": 0.065},
    "flared": {"flare": 1.55, "flare_curve": 1.35, "drape_folds": 14, "drape_depth": 0.065},
    "サーキュラー": {"flare": 2.4, "flare_curve": 1.1, "drape_folds": 16, "drape_depth": 0.07},
    "circular": {"flare": 2.4, "flare_curve": 1.1, "drape_folds": 16, "drape_depth": 0.07},
    "タイト": {"flare": 1.0, "flare_curve": 1.0, "drape_folds": 0, "drape_depth": 0.0},
    "tight": {"flare": 1.0, "flare_curve": 1.0, "drape_folds": 0, "drape_depth": 0.0},
    "ペンシル": {"flare": 1.0, "flare_curve": 1.0, "drape_folds": 0, "drape_depth": 0.0},
    "pencil": {"flare": 1.0, "flare_curve": 1.0, "drape_folds": 0, "drape_depth": 0.0},
    "ギャザー": {"flare": 1.7, "flare_curve": 1.0, "drape_folds": 20, "drape_depth": 0.1},
    "gathered": {"flare": 1.7, "flare_curve": 1.0, "drape_folds": 20, "drape_depth": 0.1},
    "Aライン": {"flare": 1.4, "flare_curve": 1.0, "drape_folds": 10, "drape_depth": 0.04},
    "aline": {"flare": 1.4, "flare_curve": 1.0, "drape_folds": 10, "drape_depth": 0.04},
}

#: 衣装の種類。今は作れるのがスカートだけなので、それ以外は confidence を出さない
GARMENT_WORDS = {
    "スカート": "skirt", "skirt": "skirt", "スカ": "skirt",
}

#: 「24本プリーツ」「プリーツ32」などから山数を拾う
_PLEAT_COUNT = re.compile(r"(\d{1,3})\s*(?:本|山|枚|-?pleats?)")
#: 「身長160cm」「1.6m」
_HEIGHT_CM = re.compile(r"(?:身長)?\s*(\d{2,3})\s*(?:cm|センチ)")
_HEIGHT_M = re.compile(r"(?:身長)?\s*(\d(?:\.\d+)?)\s*(?:m|メートル)")


def _find_words(text, table):
    """辞書の見出し語のうち text に含まれるものを、長い語を優先して返す"""
    lowered = text.lower()
    hits = []
    for word in sorted(table, key=len, reverse=True):
        if word.lower() in lowered:
            # 既に採用した長い語の一部なら重複採用しない
            if any(word.lower() in taken.lower() and word != taken for taken in hits):
                continue
            hits.append(word)
    return hits


#: 狙うエッジ長 / 身長。実測 Bottoms の 0.0206-0.0311 の中央
TARGET_EDGE_OVER_H = 0.0258


def _fix_density(spec, body, notes):
    """丈と広がりに合わせて分割数を決める。

    丈を変えても分割数が固定だと密度が実測レンジから外れる(実測: 既定10リングのまま
    「ロング」(0.48H)にすると軸方向のエッジが伸びてレンジ外になった)。
    軸方向は丈から、周方向は上端と裾の周長の平均から、狙いのエッジ長になる数を出す。
    """
    params = body["params"]
    length = params.get("length", 0.27)
    flare = params.get("flare", 1.0)

    rings = max(2, round(length / TARGET_EDGE_OVER_H) + 1)
    # 上端(ウエスト)と裾の周長の平均で周方向を決める。H 比なのでスケール非依存
    waist_over_h = 0.39 * (1.0 + spec.get("ease", 0.03))
    hem_over_h = 0.58 * (1.0 + spec.get("ease", 0.03)) * flare
    segments = max(8, round((waist_over_h + hem_over_h) / 2.0 / TARGET_EDGE_OVER_H))

    if rings != params.get("rings") or segments != params.get("segments"):
        notes.append(
            "丈 %.2fH / フレア %.2f に合わせて分割を リング %s→%d・周方向 %s→%d にした"
            % (length, flare, params.get("rings"), rings, params.get("segments"), segments)
        )
    params["rings"] = rings
    params["segments"] = segments
    _fix_segments(spec, body, notes)


def _fix_segments(spec, body, notes):
    """変調の山数を表現できる分割数へ引き上げ、ウエストバンドと揃える。

    分割数が山数の2倍未満だと折り返して別の山数になる(実測: 32分割で20山→12山)。
    プリーツは割り切れる必要もある。接合のためバンドも同じ分割数にする。
    """
    params = body["params"]
    segments = params.get("segments", 24)
    pleats = params.get("pleats") or 0
    folds = params.get("drape_folds") or 0

    required = max(segments, pleats * 2, folds * 2)
    if pleats:
        required = pleats * max(2, -(-required // pleats))  # 切り上げて倍数にする
    if required != segments:
        notes.append(
            "山数(プリーツ%d / ドレープ%d)を表現するため周方向分割を %d → %d に上げた"
            % (pleats, folds, segments, required)
        )
        params["segments"] = required

    for part in spec["parts"]:
        if part["type"] == "waistband":
            part["params"]["segments"] = params["segments"]


def parse(text, base_preset="skirt_flare"):
    """説明文から spec(正規化前)を組み立てる。

    戻り値: {"spec": dict, "matched": {分類: [語]}, "confidence": 0.0-1.0,
             "notes": [人向けの補足]}
    confidence が 0.0 なら衣装の種類が分からなかった = AI に回す判断材料。
    """
    if not isinstance(text, str):
        raise TypeError("text は文字列にしてください: %r" % (text,))

    spec = spec_module.load_preset(base_preset)
    spec["name"] = "parsed_costume"
    matched = {}
    notes = []

    garments = _find_words(text, GARMENT_WORDS)
    if garments:
        matched["garment"] = garments

    body = next((part for part in spec["parts"] if part["type"] == "skirt_body"), None)

    silhouettes = _find_words(text, SILHOUETTE_WORDS)
    if silhouettes and body is not None:
        matched["silhouette"] = silhouettes
        for word in silhouettes:
            body["params"].update(SILHOUETTE_WORDS[word])
        _fix_segments(spec, body, notes)

    lengths = _find_words(text, LENGTH_WORDS)
    if lengths and body is not None:
        matched["length"] = lengths
        body["params"]["length"] = LENGTH_WORDS[lengths[0]]

    pleat_count = _PLEAT_COUNT.search(text)
    if pleat_count and body is not None:
        count = int(pleat_count.group(1))

        if count <= 0:
            notes.append("プリーツ数 %d は無効なので無視した" % count)
        else:
            body["params"]["pleats"] = count
            if not body["params"].get("pleat_depth"):
                body["params"]["pleat_depth"] = 0.22
            _fix_segments(spec, body, notes)
            matched["pleat_count"] = [pleat_count.group(0)]

    height_cm = _HEIGHT_CM.search(text)
    height_m = _HEIGHT_M.search(text)
    if height_cm:
        spec["assumed_height"] = int(height_cm.group(1)) / 100.0
        matched["height"] = [height_cm.group(0)]
    elif height_m:
        spec["assumed_height"] = float(height_m.group(1))
        matched["height"] = [height_m.group(0)]

    colors = _find_words(text, COLOR_WORDS)
    if colors:
        matched["color"] = colors
        spec["materials"]["main"]["base_color"] = list(hex_to_linear(COLOR_WORDS[colors[0]]))
        if len(colors) > 1:
            notes.append(
                "色が複数見つかったので最初の %r を使った(残り: %s)"
                % (colors[0], ", ".join(colors[1:]))
            )

    fabrics = _find_words(text, FABRIC_WORDS)
    if fabrics:
        matched["fabric"] = fabrics
        spec["materials"]["main"].update(FABRIC_WORDS[fabrics[0]])

    # 丈やフレアが決まったあとに分割数を決める(順序が逆だと密度が合わない)
    if body is not None:
        _fix_density(spec, body, notes)

    # 種類が分からなければ何も作れない。
    # **形の情報(シルエット・丈)を色や素材より重く見る。** 色だけ当たっても
    # 形は既定値の当てずっぽうなので、そこは AI に読ませたほうが良い結果になる。
    if not garments:
        confidence = 0.0
        notes.append("衣装の種類を特定できなかった(作れるのは今のところスカートだけ)")
    else:
        confidence = 0.4
        confidence += 0.25 * sum(
            1 for key in ("silhouette", "length") if key in matched
        )
        confidence += 0.05 * sum(1 for key in ("color", "fabric") if key in matched)
        if "pleat_count" in matched:
            confidence += 0.1
        if confidence < 0.55:
            notes.append(
                "形の手がかり(シルエット・丈)が説明文に見つからなかったので、"
                "形は既定値のまま。AI に解釈させると精度が上がる"
            )

    return {
        "spec": spec,
        "matched": matched,
        "confidence": round(min(1.0, confidence), 3),
        "notes": notes,
    }
