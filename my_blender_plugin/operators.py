import bpy


class MYPLUGIN_OT_hello(bpy.types.Operator):
    """動作確認用のサンプルオペレーター"""

    bl_idname = "myplugin.hello"
    bl_label = "Hello"
    bl_description = "インフォエリアにメッセージを表示する"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        self.report({"INFO"}, "Hello from My Blender Plugin!")
        return {"FINISHED"}


class MYPLUGIN_OT_add_cube_grid(bpy.types.Operator):
    """キューブをグリッド状に並べるサンプルオペレーター"""

    bl_idname = "myplugin.add_cube_grid"
    bl_label = "Add Cube Grid"
    bl_description = "キューブをグリッド状に配置する"
    bl_options = {"REGISTER", "UNDO"}

    count: bpy.props.IntProperty(
        name="Count",
        description="1辺あたりの個数",
        default=3,
        min=1,
        max=20,
    )
    spacing: bpy.props.FloatProperty(
        name="Spacing",
        description="キューブ同士の間隔",
        default=3.0,
        min=0.1,
    )

    def execute(self, context):
        offset = (self.count - 1) * self.spacing / 2
        for x in range(self.count):
            for y in range(self.count):
                bpy.ops.mesh.primitive_cube_add(
                    location=(
                        x * self.spacing - offset,
                        y * self.spacing - offset,
                        0.0,
                    )
                )
        return {"FINISHED"}


_classes = (
    MYPLUGIN_OT_hello,
    MYPLUGIN_OT_add_cube_grid,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
