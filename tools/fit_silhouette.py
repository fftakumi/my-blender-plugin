#!/usr/bin/env python
"""参照画像のシルエットと生成物のシルエットを比べる(headless 専用)。

    blender --background --factory-startup -noaudio --python-exit-code 1 \
        --python tools/fit_silhouette.py -- \
        --ref "ref.png" --ref-box 150,280,560,560 --preset skirt_pleated \
        --out-dir renders/fit

やること:
  1. 参照画像を切り出して輝度で2値化し、幅プロファイルを取る
  2. 同じ spec で衣装を作り、**正射影**で正面からレンダーしてアルファでマスクを作る
  3. 2つを正規化して比べ、`1+(flare-1)t^curve` を当てはめる
  4. 両方のマスクを PNG で保存する(**必ず目で見て**、髪や小物を巻き込んでいないか確認する)

正射影で撮るのは、参照画像(ほぼ平行投影の絵)と同じ量にするため。
透視投影だと下側が近くなって裾が実際より広く写る。
"""

import argparse
import json
import os
import sys

import bpy
from mathutils import Vector

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from my_blender_plugin.costume import (  # noqa: E402
    build,
    silhouette,
    spec as spec_module,
)

RENDER_SIZE = 512


def image_rows(image, box=None):
    """画像から (行, 列) の sRGB 0-1 行列を作る。

    **8bit 画像の `pixels` はリニアではない。** 実測(Blender 5.1.2、24bit PNG):
    sRGB で (36,43,74) の画素が `pixels` から 0.1412/0.1686/0.2902 = そのまま 値/255 で返る。
    つまり格納値そのまま。ここでリニア→sRGB 変換を掛けると二重にガンマが乗って
    暗い部分が明るくなり、紺色がしきい値を超えてマスクから漏れる(実際に踏んだ)。
    float バッファ(`is_float`)のときだけシーンリニアなので変換する。

    `pixels` は左下始まりなので、上の行から順になるよう詰め直す。
    """
    width, height = image.size
    if width == 0 or height == 0:
        raise SystemExit("画像を読めていません(パスを確認してください): %s" % image.filepath)
    flat = list(image.pixels)
    channels = len(flat) // (width * height)
    convert = silhouette.linear_to_srgb if image.is_float else (lambda value: value)
    x0, y0, x1, y1 = box or (0, 0, width, height)
    x0, x1 = max(0, x0), min(width, x1)
    y0, y1 = max(0, y0), min(height, y1)

    rows = []
    for row_from_top in range(y0, y1):
        source_y = height - 1 - row_from_top  # pixels は下から上
        base = source_y * width * channels
        row = []
        for x in range(x0, x1):
            offset = base + x * channels
            row.append(tuple(convert(flat[offset + c]) for c in range(3)))
        rows.append(row)
    return rows


def alpha_rows(image):
    """レンダー結果のアルファを (行, 列) で取り出す(上の行から順)"""
    width, height = image.size
    flat = list(image.pixels)
    channels = len(flat) // (width * height)
    if channels < 4:
        raise SystemExit("アルファが無い画像です。film_transparent を有効にしてください")
    rows = []
    for row_from_top in range(height):
        source_y = height - 1 - row_from_top
        base = source_y * width * channels
        rows.append([flat[base + x * channels + 3] for x in range(width)])
    return rows


def save_mask(mask, path):
    """マスクを白黒 PNG に書き出す(目視確認用)"""
    height = len(mask)
    width = len(mask[0]) if height else 0
    image = bpy.data.images.new("mask", width, height, alpha=False)
    flat = []
    for row_from_top in reversed(range(height)):  # pixels は下から上
        for value in mask[row_from_top]:
            shade = 0.0 if value else 1.0
            flat.extend([shade, shade, shade, 1.0])
    image.pixels[:] = flat
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def render_front_silhouette(objects, path):
    """正射影・透過背景で正面から撮って、アルファのマスクを返す"""
    scene = bpy.context.scene
    lows = [float("inf")] * 3
    highs = [float("-inf")] * 3
    for obj in objects:
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            for axis in range(3):
                lows[axis] = min(lows[axis], point[axis])
                highs[axis] = max(highs[axis], point[axis])
    center = Vector([(lows[i] + highs[i]) / 2.0 for i in range(3)])
    size = max(highs[i] - lows[i] for i in range(3))

    camera_data = bpy.data.cameras.new("SilhouetteCam")
    camera_data.type = "ORTHO"  # 透視だと裾が実際より広く写る
    camera_data.ortho_scale = size * 1.15
    camera = bpy.data.objects.new("SilhouetteCam", camera_data)
    scene.collection.objects.link(camera)
    camera.location = center + Vector((0.0, -size * 4.0, 0.0))
    camera.rotation_euler = (1.5707963, 0.0, 0.0)  # 真正面(+Y方向を見る)
    scene.camera = camera

    scene.render.resolution_x = scene.render.resolution_y = RENDER_SIZE
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = 1  # 形だけ要るのでサンプルは最小
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)

    rendered = bpy.data.images.load(path)
    try:
        return silhouette.mask_from_alpha_rows(alpha_rows(rendered))
    finally:
        bpy.data.images.remove(rendered)


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True, help="参照画像のパス")
    parser.add_argument(
        "--ref-box", required=True, help="参照画像の切り出し範囲 x0,y0,x1,y1(左上原点)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=silhouette.DEFAULT_LUMINANCE_THRESHOLD,
        help="参照画像を2値化する輝度(sRGB 0-1)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preset")
    group.add_argument("--spec")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--report")
    parser.add_argument(
        "--no-auto-hem",
        action="store_true",
        help="一番広い行(=裾)での自動打ち切りをやめる。切り出しが裾より下まで届くと比が壊れる",
    )
    parser.add_argument(
        "--max-gap",
        type=int,
        default=0,
        help=(
            "服の内側の明るい模様で割れた輪郭をつなぐ画素数。"
            "**大きくすると髪までつながって裾の位置ごと壊れる**"
            "(実測: 12px で最大幅の行が裾から中腹の髪へ移った)。"
            "上げたら必ず mask_reference.png を見て確認すること"
        ),
    )
    args = parser.parse_args(argv)

    box = tuple(int(value) for value in args.ref_box.split(","))
    if len(box) != 4:
        raise SystemExit("--ref-box は x0,y0,x1,y1 の4つで指定してください")
    os.makedirs(args.out_dir, exist_ok=True)

    reference_image = bpy.data.images.load(os.path.abspath(args.ref))
    reference_mask = silhouette.mask_from_srgb_rows(
        image_rows(reference_image, box), args.threshold
    )
    save_mask(reference_mask, os.path.join(args.out_dir, "mask_reference.png"))
    reference_profile = silhouette.width_profile(
        reference_mask, min_width=6, max_gap=args.max_gap
    )
    if not args.no_auto_hem:
        reference_profile = silhouette.trim_at_widest(reference_profile)

    normalized = spec_module.load_preset(args.preset) if args.preset else spec_module.load_spec_file(args.spec)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    created = build.build_costume(normalized)
    produced_mask = render_front_silhouette(
        [obj for obj in created["objects"] if obj.type == "MESH"],
        os.path.join(args.out_dir, "render_silhouette.png"),
    )
    save_mask(produced_mask, os.path.join(args.out_dir, "mask_produced.png"))
    produced_profile = silhouette.width_profile(
        produced_mask, min_width=6, max_gap=args.max_gap
    )
    if not args.no_auto_hem:
        produced_profile = silhouette.trim_at_widest(produced_profile)

    result = silhouette.compare(reference_profile, produced_profile)
    result["fit_reference"] = silhouette.fit_flare(result["reference"])
    result["fit_produced"] = silhouette.fit_flare(result["produced"])
    result["spec"] = normalized["name"]
    result["spec_hash"] = spec_module.spec_hash(normalized)
    result["reference_image"] = args.ref
    result["reference_box"] = list(box)
    result["threshold"] = args.threshold
    result["max_gap"] = args.max_gap
    result["caveat"] = (
        "参照画像のスカート上端がコルセット等で隠れている場合、上端幅が実際のウエストより"
        "広く測れるので、裾/上端の比は本当のフレア量より小さく出る。"
        "マスク画像を目で見て、髪や小物を巻き込んでいないか必ず確認すること"
    )

    print("SILHOUETTE " + json.dumps(
        {
            "rms": round(result["rms"], 4),
            "max_deviation": round(result["max_deviation"], 4),
            "hem_ratio_reference": round(result["hem_ratio_reference"], 4),
            "hem_ratio_produced": round(result["hem_ratio_produced"], 4),
            "hem_ratio_error": round(result["hem_ratio_error"], 4),
            "fit_reference": result["fit_reference"],
            "fit_produced": result["fit_produced"],
        },
        ensure_ascii=False,
    ))

    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
