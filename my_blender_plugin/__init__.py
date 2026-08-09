# Blender 4.2 以降の Extension としても、従来のアドオンとしても動くようにしている。
# 従来形式でインストールする場合は bl_info が使われ、
# Extension 形式では blender_manifest.toml が使われる。

bl_info = {
    "name": "My Blender Plugin",
    "author": "fftakumi",
    "version": (0, 2, 2),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > My Plugin",
    "description": "自分用のBlenderプラグイン",
    "category": "3D View",
}

# アドオンを再読み込み(F3 > Reload Scripts)したときに
# サブモジュールも確実にリロードされるようにする
if "bpy" in locals():
    import importlib

    importlib.reload(operators)
    importlib.reload(panels)
else:
    from . import operators
    from . import panels

import bpy  # noqa: E402

_modules = (
    operators,
    panels,
)


def register():
    for mod in _modules:
        mod.register()


def unregister():
    for mod in reversed(_modules):
        mod.unregister()


if __name__ == "__main__":
    register()
