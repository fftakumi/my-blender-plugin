"""画像 → 色。**bpy 依存**なので import は使う直前に行う。

役割はここまで: 画像から代表色を取って spec のマテリアルに載せる。
**色は AI に投げない**(palette.py の median cut で決定的に決まる)。
形をどう読むかは別問題で、そちらは ai_bridge の担当(未接続)。

2048² の `image.pixels` を丸ごと純 Python に流すと遅いので、**複製を縮小してから**
画素を取り出す。元の画像データブロックは変更しない。
"""

import bpy

from . import palette

#: 画素を取り出す前に縮小する一辺のピクセル数。64×64 = 4096画素で十分な精度が出る
SAMPLE_SIZE = 64


class ImageInputError(ValueError):
    """画像を読めなかったときに投げる"""


def load_image(path):
    """パスから画像データブロックを得る(既に読み込んであれば使い回す)"""
    try:
        return bpy.data.images.load(bpy.path.abspath(path), check_existing=True)
    except RuntimeError as error:
        raise ImageInputError("画像を読めませんでした: %s (%s)" % (path, error)) from error


def sample_pixels(image, size=SAMPLE_SIZE, min_alpha=0.5):
    """画像を縮小した複製から [(r, g, b), ...] を取り出す。元画像は変更しない。

    `image.pixels` はリニア RGBA。spec のベースカラーもリニアなので変換は要らない。
    """
    if image.size[0] == 0 or image.size[1] == 0:
        raise ImageInputError(
            "画像 %r に画素がありません(ファイルが見つかっていない可能性)" % image.name
        )
    copy = image.copy()
    try:
        copy.scale(min(size, image.size[0]), min(size, image.size[1]))
        flat = list(copy.pixels)
    finally:
        bpy.data.images.remove(copy)
    if len(flat) % 4 != 0:
        raise ImageInputError("画像 %r のチャンネル数が RGBA ではありません" % image.name)
    return palette.rgba_to_rgb(flat, min_alpha=min_alpha)


def palette_from_image(path, count=3, drop_extremes=True):
    """画像ファイルから代表色を取る。

    drop_extremes は既定で True。資料画像は白抜き背景が多く、そのままだと
    背景の白が主色になってしまう(真っ白しか無い画像では落とさずに残す)。
    """
    pixels = sample_pixels(load_image(path))
    if not pixels:
        raise ImageInputError(
            "不透明な画素がありませんでした(全面が透明な画像かもしれません)"
        )
    return palette.dominant_colors(pixels, count=count, drop_extremes=drop_extremes)


def apply_image(spec, path, count=3, drop_extremes=True):
    """画像から色を取って spec に反映し、人向けの補足を返す。

    spec への載せ方(純粋なロジック)は `palette.apply_to_spec` が持つ。
    ここは「Blender で画像を読んで縮小して画素を取る」ところだけを担当する。
    """
    colors = palette_from_image(path, count=count, drop_extremes=drop_extremes)
    return palette.apply_to_spec(spec, colors)
