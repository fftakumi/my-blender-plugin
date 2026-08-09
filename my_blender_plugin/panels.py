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


_classes = (MYPLUGIN_PT_main,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
