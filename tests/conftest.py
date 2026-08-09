# この環境には Blender(bpy)がないため、フェイクの bpy モジュールを
# sys.modules に注入してからアドオンを import できるようにする。
import sys
import types
from pathlib import Path

# リポジトリルートを import パスに追加(my_blender_plugin を import 可能にする)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _AnyOps:
    """bpy.ops.* の任意の呼び出しを受け付けるフェイク"""

    def __getattr__(self, name):
        return _AnyOps()

    def __call__(self, *args, **kwargs):
        return {"FINISHED"}


def _fake_property(**kwargs):
    # クラス定義時のアノテーション評価用。デフォルト値を返しておくと
    # テストでインスタンス属性が未設定でも困らない。
    return kwargs.get("default")


def _install_fake_bpy():
    if "bpy" in sys.modules:
        return

    bpy = types.ModuleType("bpy")

    bpy_types = types.ModuleType("bpy.types")
    bpy_types.Operator = type("Operator", (), {"report": lambda self, level, msg: None})
    bpy_types.Panel = type("Panel", (), {})

    bpy_props = types.ModuleType("bpy.props")
    for prop_name in (
        "IntProperty",
        "FloatProperty",
        "BoolProperty",
        "StringProperty",
        "EnumProperty",
        "FloatVectorProperty",
        "PointerProperty",
        "CollectionProperty",
    ):
        setattr(bpy_props, prop_name, _fake_property)

    bpy_utils = types.ModuleType("bpy.utils")
    bpy_utils.register_class = lambda cls: None
    bpy_utils.unregister_class = lambda cls: None

    bpy.types = bpy_types
    bpy.props = bpy_props
    bpy.utils = bpy_utils
    bpy.ops = _AnyOps()

    sys.modules["bpy"] = bpy
    sys.modules["bpy.types"] = bpy_types
    sys.modules["bpy.props"] = bpy_props
    sys.modules["bpy.utils"] = bpy_utils


_install_fake_bpy()
