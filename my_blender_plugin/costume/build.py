"""PartMesh を Blender のオブジェクトにする。**bpy 依存**。

やっているのは「純粋関数が決めた頂点・面・UV を bmesh/mesh に流し込む」だけ。
形を決める計算は kernels/modulate/parts、形を作る操作は Blender 標準機能、という切り分け。

UV は kernels.py が解析的に決めた値をそのまま入れる。`bpy.ops.uv.smart_project` は
EDIT モードとアクティブオブジェクトを要求して headless で poll が落ちうるし、
バージョン間で結果が変わるので使わない。
"""

import bpy

from . import materials as materials_module, parts as parts_module

#: 生成物を入れるコレクション名の接頭辞
COLLECTION_PREFIX = "Costume_"


def collection_name(spec_name):
    return COLLECTION_PREFIX + spec_name


def ensure_collection(name, scene=None):
    """名前つきコレクションを用意してシーンに繋ぐ"""
    scene = scene or bpy.context.scene
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if collection.name not in {child.name for child in scene.collection.children}:
        scene.collection.children.link(collection)
    return collection


def _remove_existing(name, collection=None):
    """同名オブジェクトを消す(同じ衣装を作り直したときに .001 が増えるのを防ぐ)。

    **消すのは同じコレクションに居るものだけ。** パーツ名は spec が決めるので
    別の衣装でも "Skirt_Body" が衝突しうる。無条件に消すと、違う衣装を作った瞬間に
    前の衣装が消えてしまう(実機で確認)。別コレクションのものは Blender の
    自動連番(.001)に任せる。
    """
    existing = bpy.data.objects.get(name)
    if existing is None:
        return
    if collection is not None and existing.name not in collection.objects:
        return
    mesh = existing.data if existing.type == "MESH" else None
    bpy.data.objects.remove(existing, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def mark_sharp_folds(mesh, part_mesh):
    """プリーツの折り線の辺をシャープにして、陰影を割る。

    スムーズシェーディングだけだと浅い折り目がぼやけて「プレスした折り目」に見えない。
    実物のプリーツは折り目が立っているので、そこは陰影が切れているのが正しい。
    深さを盛って見せるより、浅い折り目のまま辺をシャープにするほうが実物に近い。

    戻り値: シャープにした辺の本数。
    """
    if not part_mesh.sharp_segments:
        return 0
    lookup = {}
    for edge in mesh.edges:
        lookup[frozenset(edge.vertices)] = edge

    marked = 0
    ring_size, ring_count = part_mesh.ring_size, part_mesh.ring_count
    for segment in part_mesh.sharp_segments:
        for ring in range(ring_count - 1):
            key = frozenset((ring * ring_size + segment, (ring + 1) * ring_size + segment))
            edge = lookup.get(key)
            if edge is not None:
                edge.use_edge_sharp = True
                marked += 1
    return marked


def part_to_object(part_mesh, material=None, collection=None):
    """PartMesh 1枚から Blender オブジェクトを作る"""
    _remove_existing(part_mesh.name, collection)

    mesh = bpy.data.meshes.new(part_mesh.name)
    mesh.from_pydata([list(point) for point in part_mesh.verts], [], [list(q) for q in part_mesh.quads])
    mesh.validate(verbose=False)

    uv_layer = mesh.uv_layers.new(name="UVMap")
    for polygon, loop_uvs in zip(mesh.polygons, part_mesh.uv_loops):
        for loop_index, uv in zip(polygon.loop_indices, loop_uvs):
            uv_layer.data[loop_index].uv = uv

    # 4.1 以降は面ごとの use_smooth ではなく sharp_face 属性。
    # Mesh.shade_smooth() / shade_flat() が正攻法。
    # 布はスムーズ、硬い部品(ボタン)はフラット — 円盤の縁を丸めない
    shade = "shade_flat" if part_mesh.flat_shaded else "shade_smooth"
    if hasattr(mesh, shade):
        getattr(mesh, shade)()
    mark_sharp_folds(mesh, part_mesh)

    if material is not None:
        mesh.materials.append(material)

    obj = bpy.data.objects.new(part_mesh.name, mesh)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def build_costume(normalized_spec, scene=None):
    """spec から衣装一式を生成してシーンに置く。

    戻り値: {"built": parts.build_all の結果, "objects": [Object], "collection": Collection}
    """
    built = parts_module.build_all(normalized_spec)
    material_map = materials_module.ensure_spec_materials(normalized_spec)
    collection = ensure_collection(collection_name(normalized_spec["name"]), scene)

    objects = [
        part_to_object(part_mesh, material_map.get(part_mesh.material), collection)
        for part_mesh in built["parts"]
    ]
    return {"built": built, "objects": objects, "collection": collection}


def evaluated_part_mesh(obj, part_mesh_name=None):
    """モディファイア評価後のメッシュを (頂点, 面) で取り出す。

    モディファイアを積んだ場合に「評価後も層1の検査を通るか」を確かめるために使う。
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        matrix = evaluated.matrix_world
        verts = [tuple(matrix @ vertex.co) for vertex in mesh.vertices]
        faces = [tuple(polygon.vertices) for polygon in mesh.polygons]
    finally:
        evaluated.to_mesh_clear()
    return {"name": part_mesh_name or obj.name, "verts": verts, "faces": faces}
