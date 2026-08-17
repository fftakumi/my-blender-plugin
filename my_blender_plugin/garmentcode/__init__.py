"""GarmentCode の縫製パターン(*_specification.json)取り込みサブパッケージ。

**このパッケージは何も再エクスポートしない。**
`tests/conftest.py` のフェイク bpy には `bpy.data` / `bmesh` / `mathutils` が無いため、
ここで `build` や `weld`(bpy依存)を import すると純粋関数のテストまで
ImportError で落ちる。bpy依存モジュールは呼ぶ側が使う直前に import すること
(`costume` サブパッケージとまったく同じ約束)。

- bpy 非依存(pytest から直接テストできる):
  spec, curves, panel_mesh, transform, topology, limbs, stitches
- bpy 依存(Blender の中だけ): build(`mathutils.geometry.delaunay_2d_cdt` と bmesh)、
  weld(bmesh)

やること / やらないこと
----------------------
やるのは3つだけ:

1. spec を読んで平面パネルを1枚のメッシュ(縫合エッジ付き)に組み、3D に配置する
2. 袖・カフのような筒状パーツを腕の軸へ自動センタリングする
3. 縫合エッジで結ばれた頂点ペアだけを溶接する

Cloth の設定(重力・剛性・縫合力・ベイク)は **Blender 標準の
プロパティ > 物理演算 > クロス がそのまま使えるので実装しない**
(CLAUDE.md の最重要方針)。コリジョンも標準の Collision モディファイアで足りる。
手順は docs/garmentcode.md を見ること。

モジュールを増やしたら `_MODULE_NAMES` にも足すこと
(tests/test_garmentcode.py がディレクトリの中身と突き合わせて検査する)。
"""

# 依存の浅い順。importlib.reload はサブモジュールを辿らないので自分で並べる。
# curves -> panel_mesh、topology -> limbs の順序だけ守れば良い。
_MODULE_NAMES = (
    "spec",
    "curves",
    "panel_mesh",
    "transform",
    "topology",
    "limbs",
    "stitches",
    "build",
    "weld",
)


def reload_all():
    """F3 > Reload Scripts でサブモジュールまで確実に読み直す。

    親の __init__.py の reload 分岐から、operators / panels より先に呼ぶ。
    まだ import されていないモジュールは触らない(bpy依存モジュールを
    Blender 外で無理に読み込まないため)。
    """
    import importlib
    import sys

    for name in _MODULE_NAMES:
        module = sys.modules.get(__name__ + "." + name)
        if module is not None:
            importlib.reload(module)
