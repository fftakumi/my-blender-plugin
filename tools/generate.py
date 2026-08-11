#!/usr/bin/env python
"""spec から衣装を生成して .blend に保存する(headless 専用)。

    blender --background --factory-startup -noaudio --python-exit-code 1 \
        --python tools/generate.py -- --preset skirt_flare --out out.blend

--factory-startup で起動するのでアドオンは読み込まれない(ユーザーの Blender が
MCP アドオンで動いているため、同じポートを掴まないようにこれが必須)。
そのぶんここでは register を通さず、リポジトリを sys.path に挿して
`my_blender_plugin.costume.build` を直接呼ぶ。
"""

import argparse
import json
import os
import sys

import bpy

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from my_blender_plugin.costume import build, spec as spec_module, validate  # noqa: E402


def _refuse_ai(_prompt):
    """--ai を付けていないときは AI 経路を確実に失敗させる(黙って外部プロセスを起動しない)"""
    from my_blender_plugin.costume import ai_bridge

    raise ai_bridge.AIBridgeError("--ai が指定されていません")


def clear_scene():
    """--factory-startup の既定オブジェクト(立方体・カメラ・ライト)を消す"""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def parse_args(argv=None):
    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preset", help="同梱プリセット名")
    group.add_argument("--spec", help="spec JSON のパス")
    group.add_argument("--text", help="衣装の説明文(キーワード辞書で spec を組む)")
    parser.add_argument("--out", required=True, help="保存先の .blend")
    parser.add_argument("--report", help="層1レポート JSON の書き出し先")
    parser.add_argument("--image", help="参考画像。色だけを spec のマテリアルに反映する")
    parser.add_argument("--image-colors", type=int, default=3, help="画像から取る色数")
    parser.add_argument(
        "--ai",
        action="store_true",
        help="説明文がキーワードで解けないときに claude -p に spec を作らせる",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    if args.preset:
        raw = spec_module.load_preset(args.preset)
    elif args.spec:
        raw = spec_module.load_spec_file(args.spec)
    else:
        from my_blender_plugin.costume import ai_bridge

        outcome = ai_bridge.spec_from_text(
            args.text, runner=None if args.ai else _refuse_ai
        )
        raw = outcome["spec"]
        print("SPEC_SOURCE %s" % outcome["source"])
        if outcome["ai_error"]:
            print("AI_ERROR %s" % outcome["ai_error"])

    if args.image:
        from my_blender_plugin.costume import image_input

        for note in image_input.apply_image(raw, args.image, count=args.image_colors):
            print("IMAGE %s" % note)

    normalized = spec_module.normalize_spec(raw)

    clear_scene()
    result = build.build_costume(normalized)

    report = validate.costume_report(result["built"], normalized)
    report["spec_hash"] = spec_module.spec_hash(normalized)
    report["blender"] = bpy.app.version_string
    report["objects"] = [obj.name for obj in result["objects"]]

    # 検証側が spec を読み直せるようにテキストデータブロックに埋め込む
    text_name = "costume_spec.json"
    text = bpy.data.texts.get(text_name) or bpy.data.texts.new(text_name)
    text.clear()
    text.write(json.dumps(normalized, ensure_ascii=False, indent=1))

    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.out), compress=True)
    print("GENERATE_OK %s verdict=%s faces=%d" % (args.out, report["verdict"], report["totals"]["faces"]))

    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1, default=list)
    if report["failed"]:
        raise SystemExit("層1で不合格: " + ", ".join(report["failed"]))


if __name__ == "__main__":
    main()
