import bpy

from . import operators


class MYPLUGIN_PT_main(bpy.types.Panel):
    """3DビューポートのサイドバーN(パネル)に表示されるメインパネル"""

    bl_label = "My Plugin"
    bl_idname = "MYPLUGIN_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "My Plugin"

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        col.operator(operators.MYPLUGIN_OT_hello.bl_idname, icon="INFO")
        col.operator(operators.MYPLUGIN_OT_add_cube_grid.bl_idname, icon="MESH_CUBE")
        col.operator(
            operators.MYPLUGIN_OT_fit_body_to_corset.bl_idname, icon="MOD_SHRINKWRAP"
        )

        layout.separator()
        box = layout.box()
        box.label(text="衣装生成", icon="MATCLOTH")
        box.operator(operators.MYPLUGIN_OT_generate_costume.bl_idname, icon="OUTLINER_OB_SURFACE")

        layout.separator()
        box = layout.box()
        box.label(text="GarmentCode 型紙", icon="MOD_CLOTH")
        box.operator(operators.MYPLUGIN_OT_import_garmentcode.bl_idname, icon="IMPORT")
        box.operator(
            operators.MYPLUGIN_OT_weld_garmentcode_seams.bl_idname, icon="AUTOMERGE_ON"
        )
        # クロスの設定・ベイクは Blender 標準の プロパティ > 物理演算 > クロス を使う
        # (CLAUDE.md の最重要方針: 標準機能で足りるものは実装しない)
        box.label(text="クロス設定は 物理演算 > クロス", icon="INFO")


_classes = (MYPLUGIN_PT_main,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
