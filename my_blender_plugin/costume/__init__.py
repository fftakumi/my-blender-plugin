"""衣装生成サブパッケージ。

**このパッケージは何も再エクスポートしない。**
`tests/conftest.py` のフェイク bpy には `bpy.data` / `bmesh` / `mathutils` が無いため、
ここで `build` や `materials`(bpy依存)を import すると純粋関数のテストまで
ImportError で落ちる。bpy依存モジュールは呼ぶ側が使う直前に import すること。

- bpy 非依存(pytest から直接テストできる): spec, sizing, modulate, kernels, validate,
  parts, parse_text, palette, ai_bridge
- bpy 依存(Blender の中だけ): build, materials

モジュールを増やしたら `_MODULE_NAMES` にも足すこと(tests/test_addon.py が
ディレクトリの中身と突き合わせて検査する)。
"""

# 依存の浅い順。importlib.reload はサブモジュールを辿らないので自分で並べる。
_MODULE_NAMES = (
    "spec",
    "sizing",
    "modulate",
    "kernels",
    "validate",
    "parts",
    "palette",
    "parse_text",
    "ai_bridge",
    "materials",
    "image_input",
    "build",
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
