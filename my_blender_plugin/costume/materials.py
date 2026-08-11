"""マテリアル生成。**bpy 依存**なので import は使う直前に行う。

実測(VRM 10体)の衣装は片面シートで両面表示だった。VRM に合わせる必要は無いが
「片面で作って両面表示する」方針自体は厚みぶんのポリゴンを節約できるので踏襲する。

Blender 5.1.2 実機で確認した事実:
  - Principled BSDF の艶入力の名前は **"Sheen Weight"**(旧 "Sheen" ではない)
  - `Material.blend_method` は非推奨。代わりは `surface_render_method`
    (取りうる値は DITHERED / BLENDED の2つだけ)
  - `alpha_threshold` と `use_backface_culling` は健在
"""

import bpy

#: spec のマテリアル値 → Principled BSDF の入力名
_SOCKET_MAP = {
    "roughness": "Roughness",
    "metallic": "Metallic",
    "alpha": "Alpha",
    "sheen": "Sheen Weight",
}


def _principled(material):
    """マテリアルの Principled BSDF ノードを返す(無ければ作る)"""
    tree = material.node_tree
    for node in tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    node = tree.nodes.new("ShaderNodeBsdfPrincipled")
    output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(node.outputs["BSDF"], output.inputs["Surface"])
    return node


def material_name(spec_name, key):
    """衣装名とマテリアルキーから決定的な名前を作る"""
    return "%s_%s" % (spec_name, key)


def ensure_material(name, params):
    """spec のマテリアル定義から布らしい Principled マテリアルを作る/更新する"""
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.use_nodes = True
    bsdf = _principled(material)

    red, green, blue = params["base_color"]
    alpha = params["alpha"]
    bsdf.inputs["Base Color"].default_value = (red, green, blue, 1.0)
    for key, socket_name in _SOCKET_MAP.items():
        socket = bsdf.inputs.get(socket_name)
        if socket is not None:
            socket.default_value = params[key]

    # 片面シートなので裏面も描く。glTF に出せば doubleSided=true になる
    material.use_backface_culling = not params["double_sided"]

    if alpha < 1.0:
        # blend_method は 5.x で非推奨。存在するものだけ触る
        if hasattr(material, "surface_render_method"):
            material.surface_render_method = "DITHERED"
        if hasattr(material, "alpha_threshold"):
            material.alpha_threshold = 0.5

    # ソリッド表示・Workbench でも色が出るようにビューポート表示色を揃える
    material.diffuse_color = (red, green, blue, alpha)
    material.roughness = params["roughness"]
    material.metallic = params["metallic"]
    return material


def ensure_spec_materials(normalized_spec):
    """spec の materials 全部を作って {キー: マテリアル} を返す"""
    return {
        key: ensure_material(material_name(normalized_spec["name"], key), params)
        for key, params in normalized_spec["materials"].items()
    }
