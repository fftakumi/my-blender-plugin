# Blender 4.2 以降の Extension としても、従来のアドオンとしても動くようにしている。
# 従来形式でインストールする場合は bl_info が使われ、
# Extension 形式では blender_manifest.toml が使われる。

bl_info = {
    "name": "My Blender Plugin",
    "author": "fftakumi",
    "version": (0, 12, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > My Plugin",
    "description": "自分用のBlenderプラグイン",
    "category": "3D View",
}

# アドオンを再読み込み(F3 > Reload Scripts)したときに
# サブモジュールも確実にリロードされるようにする
if "bpy" in locals():
    import importlib

    # costume はサブパッケージなので importlib.reload では中身が読み直されない。
    # 自前の reload_all() を operators / panels より先に呼ぶ(依存の向きがこの順)。
    costume.reload_all()
    importlib.reload(operators)
    importlib.reload(panels)
else:
    from . import costume
    from . import operators
    from . import panels

import bpy  # noqa: E402

# costume は register/unregister を持たない(UIクラスが無い)ので _modules に入れない。
# 入れると有効化時に AttributeError になる。
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
