#!/usr/bin/env python
"""spec → パーツ生成 → 幾何検証 を **Blender なしで** 走らせる。

層1(costume/validate.py)の検査はすべて bpy 非依存なので、Blender の起動を挟まずに
生成↔検証ループを回せる。UV・マテリアル・レンダーの確認は tools/verify.py(Blender 必須)。

使い方:
    uv run python tools/precheck.py --preset skirt_flare
    uv run python tools/precheck.py --spec path/to/spec.json --report out.json
"""

import argparse
import json
import os
import sys

# costume を単体のトップレベルパッケージとして読む。こうすると
# my_blender_plugin/__init__.py(bpy を import する)を通らずに済む。
_ADDON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "my_blender_plugin")
if _ADDON_DIR not in sys.path:
    sys.path.insert(0, _ADDON_DIR)

from costume import parts, spec as spec_module, validate  # noqa: E402


def run(normalized_spec):
    """spec からパーツを作って検証レポートを返す"""
    built = parts.build_all(normalized_spec)
    report = validate.costume_report(built, normalized_spec)
    report["spec_hash"] = spec_module.spec_hash(normalized_spec)
    report["layer"] = "1 (bpy非依存)"
    return built, report


def summarize(report):
    """人が読む1行サマリを組み立てる"""
    lines = [
        "%s: %s  頂点 %d / 面 %d  エッジ長/H %.4f (実測 0.0206-0.0311)  (spec %s)"
        % (
            report["name"],
            report["verdict"],
            report["totals"]["verts"],
            report["totals"]["faces"],
            report["edge_length_over_h"] or 0.0,
            report["spec_hash"],
        )
    ]
    for key, check in report["warn"].items():
        if not check["ok"]:
            lines.append("  WARN %s: 実測 %r / 期待 %r" % (key, check["value"], check["expected"]))
    for part in report["parts"]:
        ring = part["edge_length"]["ring"]["mean"]
        axial = part["edge_length"]["axial"]["mean"]
        lines.append(
            "  %-18s %-4s 頂点 %4d / 面 %4d  境界 %s  エッジ長 周%.4f 軸%.4f (/H %.4f)"
            % (
                part["part"],
                part["verdict"],
                part["topology"]["verts"],
                part["topology"]["faces"],
                part["boundary_loop_verts"],
                ring,
                axial,
                part["edge_length_over_h"]["all"],
            )
        )
        for key in part["failed"]:
            check = part["hard"][key]
            lines.append(
                "    FAIL %s: 実測 %r / 期待 %r" % (key, check["value"], check["expected"])
            )
        for key in part["warned"]:
            check = part["warn"][key]
            lines.append(
                "    WARN %s: 実測 %r / 期待 %r" % (key, check["value"], check["expected"])
            )
    for key, check in report["hard"].items():
        if not check["ok"]:
            lines.append("  FAIL %s: 実測 %r / 期待 %r" % (key, check["value"], check["expected"]))
    for joint in report["joints"]:
        lines.append(
            "  接合 %s.%s - %s.%s: ずれ %.3g (%s)"
            % (
                joint["a"],
                joint["a_ring"],
                joint["b"],
                joint["b_ring"],
                joint["gap"],
                "OK" if joint["ok"] else "NG",
            )
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preset", help="同梱プリセット名(%s)" % ", ".join(spec_module.list_presets()))
    group.add_argument("--spec", help="spec JSON のパス")
    parser.add_argument("--report", help="レポート JSON の書き出し先")
    args = parser.parse_args(argv)

    if args.preset:
        normalized = spec_module.load_preset(args.preset)
    else:
        normalized = spec_module.load_spec_file(args.spec)

    _built, report = run(normalized)
    print(summarize(report))

    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1, default=list)
        print("レポート: %s" % args.report)

    return 0 if report["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
