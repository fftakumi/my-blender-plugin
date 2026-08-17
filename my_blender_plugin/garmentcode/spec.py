"""GarmentCode の `*_specification.json` の読み込みと検証(bpy 非依存)。

spec の構造(GarmentCode 側の出力仕様):

```
{"pattern": {"panels": {<名前>: {"translation": [x,y,z],
                                 "rotation": [rx,ry,rz],   # 度・外因性 XYZ
                                 "vertices": [[x,y], ...],
                                 "edges": [{"endpoints": [a,b],
                                            "curvature": {...}}, ...],
                                 "label": "arm" | "body" | ...}},
             "stitches": [[{"panel": n, "edge": i}, {"panel": n, "edge": i}], ...],
             "panel_order": [名前, ...]},
 "properties": {"curvature_coords": "relative", "units_in_meter": 100}}
```

**壊れた入力は黙って通さない。** ここで `SpecError` にしておかないと、
ずれた添字が CDT やステッチの段で意味の分からない例外になって原因が追えない。
"""

import json

# 曲率の型。GarmentCode が出しうるのはこの3つ(curves.py が実装している)
CURVATURE_TYPES = ("circle", "quadratic", "cubic")

# 期待する単位系。GarmentCode の出力は cm(1m = 100 単位)
EXPECTED_UNITS_IN_METER = 100

# Blender シーンの単位スケール。1 BU = 1 cm のシーンを前提に 1:1 で取り込む
EXPECTED_SCALE_LENGTH = 0.01


class SpecError(ValueError):
    """spec が壊れている / この取り込み器が解釈できない形をしている"""


def load_spec(path):
    """JSON ファイルを読んで `parse_spec` に通した結果を返す"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as error:
        raise SpecError("spec を開けません: %s" % error)
    except ValueError as error:
        raise SpecError("spec が JSON として読めません: %s" % error)
    return parse_spec(data)


def _require_mapping(value, what):
    if not isinstance(value, dict):
        raise SpecError("%s が辞書ではありません(%s)" % (what, type(value).__name__))
    return value


def _require_sequence(value, what):
    if not isinstance(value, (list, tuple)):
        raise SpecError("%s がリストではありません(%s)" % (what, type(value).__name__))
    return value


def _require_vector(value, size, what):
    seq = _require_sequence(value, what)
    if len(seq) != size:
        raise SpecError("%s の要素数が %d ではありません(%d)" % (what, size, len(seq)))
    out = []
    for index, component in enumerate(seq):
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise SpecError("%s[%d] が数値ではありません(%r)" % (what, index, component))
        out.append(float(component))
    return tuple(out)


def _parse_curvature(raw, what):
    """曲率の辞書を検証して {"type": ..., "params": ...} に正規化する(無曲率は None)"""
    if raw is None:
        return None
    curvature = _require_mapping(raw, "%s の curvature" % what)
    ctype = curvature.get("type")
    if ctype not in CURVATURE_TYPES:
        raise SpecError(
            "%s の曲率タイプ %r は未対応です(対応: %s)"
            % (what, ctype, ", ".join(CURVATURE_TYPES))
        )
    params = _require_sequence(curvature.get("params"), "%s の curvature.params" % what)
    if ctype == "circle":
        radius, large_arc, right = _require_vector(params, 3, "%s の円弧パラメータ" % what)
        if radius <= 0.0:
            raise SpecError("%s の円弧の半径が 0 以下です(%r)" % (what, radius))
        return {"type": ctype, "params": (radius, int(large_arc), int(right))}
    expected = 1 if ctype == "quadratic" else 2
    if len(params) != expected:
        raise SpecError(
            "%s の %s の制御点が %d 個ではありません(%d)"
            % (what, ctype, expected, len(params))
        )
    control = tuple(
        _require_vector(point, 2, "%s の制御点[%d]" % (what, index))
        for index, point in enumerate(params)
    )
    return {"type": ctype, "params": control}


def _parse_panel(name, raw):
    panel = _require_mapping(raw, "パネル %s" % name)
    vertices_raw = _require_sequence(panel.get("vertices"), "パネル %s の vertices" % name)
    if len(vertices_raw) < 3:
        raise SpecError("パネル %s の頂点が %d 個しかありません" % (name, len(vertices_raw)))
    vertices = [
        _require_vector(point, 2, "パネル %s の頂点[%d]" % (name, index))
        for index, point in enumerate(vertices_raw)
    ]

    edges_raw = _require_sequence(panel.get("edges"), "パネル %s の edges" % name)
    if len(edges_raw) < 3:
        raise SpecError("パネル %s のエッジが %d 本しかありません" % (name, len(edges_raw)))
    edges = []
    for index, edge_raw in enumerate(edges_raw):
        what = "パネル %s のエッジ[%d]" % (name, index)
        edge = _require_mapping(edge_raw, what)
        endpoints = _require_sequence(edge.get("endpoints"), "%s の endpoints" % what)
        if len(endpoints) != 2:
            raise SpecError("%s の endpoints が2要素ではありません" % what)
        pair = []
        for endpoint in endpoints:
            if isinstance(endpoint, bool) or not isinstance(endpoint, int):
                raise SpecError("%s の endpoints に整数でない値があります(%r)" % (what, endpoint))
            if not 0 <= endpoint < len(vertices):
                raise SpecError(
                    "%s の endpoints %d が頂点数 %d の範囲外です"
                    % (what, endpoint, len(vertices))
                )
            pair.append(endpoint)
        if pair[0] == pair[1]:
            raise SpecError("%s の endpoints が同じ頂点を指しています(%d)" % (what, pair[0]))
        edges.append(
            {
                "endpoints": (pair[0], pair[1]),
                "curvature": _parse_curvature(edge.get("curvature"), what),
                "label": edge.get("label"),
            }
        )

    return {
        "vertices": vertices,
        "edges": edges,
        "translation": _require_vector(
            panel.get("translation"), 3, "パネル %s の translation" % name
        ),
        "rotation": _require_vector(panel.get("rotation"), 3, "パネル %s の rotation" % name),
        "label": panel.get("label"),
    }


def _parse_stitch_side(raw, panels, what):
    side = _require_mapping(raw, what)
    panel_name = side.get("panel")
    if panel_name not in panels:
        raise SpecError("%s が知らないパネル %r を指しています" % (what, panel_name))
    edge = side.get("edge")
    if isinstance(edge, bool) or not isinstance(edge, int):
        raise SpecError("%s の edge が整数ではありません(%r)" % (what, edge))
    if not 0 <= edge < len(panels[panel_name]["edges"]):
        raise SpecError(
            "%s の edge %d がパネル %s のエッジ数 %d の範囲外です"
            % (what, edge, panel_name, len(panels[panel_name]["edges"]))
        )
    return {"panel": panel_name, "edge": edge}


def parse_spec(data):
    """spec の dict を検証して、この取り込み器が使う形に正規化する。

    返り値: {"panels": {名前: パネル}, "stitches": [(a, b), ...],
             "order": [名前, ...], "properties": {...}}
    """
    root = _require_mapping(data, "spec")
    pattern = _require_mapping(root.get("pattern"), "spec の pattern")
    panels_raw = _require_mapping(pattern.get("panels"), "pattern の panels")
    if not panels_raw:
        raise SpecError("パネルが1枚もありません")

    panels = {name: _parse_panel(name, raw) for name, raw in panels_raw.items()}

    stitches = []
    for index, stitch_raw in enumerate(
        _require_sequence(pattern.get("stitches", []), "pattern の stitches")
    ):
        pair = _require_sequence(stitch_raw, "ステッチ[%d]" % index)
        if len(pair) != 2:
            raise SpecError("ステッチ[%d] が2要素ではありません(%d)" % (index, len(pair)))
        stitches.append(
            (
                _parse_stitch_side(pair[0], panels, "ステッチ[%d] の左辺" % index),
                _parse_stitch_side(pair[1], panels, "ステッチ[%d] の右辺" % index),
            )
        )

    order_raw = pattern.get("panel_order")
    if order_raw is None:
        order = list(panels_raw.keys())
    else:
        order = [
            name
            for name in _require_sequence(order_raw, "pattern の panel_order")
            if name in panels
        ]
        # panel_order に載っていないパネルも必ず出力する(取りこぼすと縫合が欠ける)
        order += [name for name in panels_raw if name not in order]

    properties = root.get("properties") or {}
    if not isinstance(properties, dict):
        raise SpecError("spec の properties が辞書ではありません")

    return {
        "panels": panels,
        "stitches": stitches,
        "order": order,
        "properties": properties,
    }


def spec_warnings(parsed):
    """取り込みは続けられるが伝えるべきことを日本語の文字列リストで返す純粋関数"""
    notes = []
    properties = parsed["properties"]

    coords = properties.get("curvature_coords")
    if coords not in (None, "relative"):
        notes.append(
            "curvature_coords が %r です。制御点をエッジ座標系として読むので"
            "曲率が正しく出ない可能性があります" % coords
        )

    units = properties.get("units_in_meter")
    if units is not None and units != EXPECTED_UNITS_IN_METER:
        notes.append(
            "spec の units_in_meter が %r です(期待 %d = cm)。"
            "この取り込み器は cm を 1 BU = 1 cm として 1:1 で置きます"
            % (units, EXPECTED_UNITS_IN_METER)
        )

    unstitched = set(parsed["panels"])
    for side_a, side_b in parsed["stitches"]:
        unstitched.discard(side_a["panel"])
        unstitched.discard(side_b["panel"])
    if unstitched:
        notes.append(
            "どのステッチにも現れないパネルがあります: %s" % ", ".join(sorted(unstitched))
        )
    return notes


def scale_length_warning(scale_length):
    """シーンの unit_settings.scale_length が cm シーンでないときの警告を返す純粋関数

    GarmentCode の出力は cm。1 BU = 1 cm のシーン(scale_length = 0.01)を
    前提に無変換で置くので、それ以外だと寸法の意味が変わる。
    ハードコードせず毎回シーンから読んで確かめる。
    """
    if abs(scale_length - EXPECTED_SCALE_LENGTH) <= 1e-6:
        return None
    return (
        "シーンの unit_settings.scale_length が %.5f です(期待 %.2f = cm シーン)。"
        "GarmentCode の cm をそのまま Blender 単位として置くので、"
        "1 BU = 1 cm のシーンにしてから取り込んでください" % (scale_length, EXPECTED_SCALE_LENGTH)
    )
