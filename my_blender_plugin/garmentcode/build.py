"""GarmentCode パターンを Blender のメッシュに組む(**bpy 依存**)。

`tests/conftest.py` のフェイク bpy には `bpy.data` / `bmesh` / `mathutils` が無いので
**このモジュールをトップレベルで import してはいけない**(`operators.py` の
`execute()` の中で遅延 import する)。

ここに置くのは Blender が要る仕事だけ:

- `mathutils.geometry.delaunay_2d_cdt`(三角形分割。mathutils にあるので bpy 側)
- コレクション / オブジェクト / 頂点グループの生成
- 素体の評価済みメッシュからサンプル点を取る(**素体自体は一切変更しない**)

形の計算は全部 bpy 非依存モジュールにある(`panel_mesh` / `transform` /
`limbs` / `stitches`)。ここはその糊付けだけ。
"""

import bpy

from . import limbs, panel_mesh, stitches as gc_stitches, topology, transform

# 目標エッジ長 [cm]。参照実装の実測で平均エッジ長 1.57cm になる値
TARGET_EDGE = 1.5
# CDT の許容誤差
CDT_EPSILON = 1e-6

COLLECTION_NAME = "GarmentCode"
CLOTH_OBJECT_NAME = "GC_Cloth"


def _name_free(name):
    return name not in bpy.data.collections and name not in bpy.data.objects


def resolve_suffix(names):
    """すべての出力名が空いている共通サフィックス('' / '.001' / ...)を返す

    既存のコレクション・オブジェクトには絶対に触らない(名前を版管理する)。
    """
    if all(_name_free(name) for name in names):
        return ""
    for index in range(1, 1000):
        suffix = ".%03d" % index
        if all(_name_free(name + suffix) for name in names):
            return suffix
    raise RuntimeError("空いている名前のサフィックスが見つかりません")


def body_sample_points(depsgraph, body_object):
    """素体の評価済みメッシュのワールド座標を読むだけの関数(素体は変更しない)"""
    if body_object is None or body_object.type != "MESH":
        return []
    evaluated = body_object.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        matrix = evaluated.matrix_world
        return [tuple(matrix @ vertex.co) for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def mesh_panel(panel, target_edge=TARGET_EDGE):
    """1パネルを 2D 三角メッシュにする。

    返り値 dict: points(2D) / faces / chains(エッジ添字 -> 頂点添字列) / stats
    """
    from mathutils import Vector
    from mathutils.geometry import delaunay_2d_cdt

    inputs = panel_mesh.panel_inputs(panel, target_edge)
    # output_type=1 は CDT_INSIDE(拘束の内側だけを三角形にする)
    result = delaunay_2d_cdt(
        [Vector(point) for point in inputs["points"]],
        inputs["constraints"],
        [],
        1,
        CDT_EPSILON,
        True,
    )
    # 戻り値は (verts, edges, faces, orig_verts, orig_edges, orig_faces)
    out_verts, out_faces, orig_verts = result[0], result[2], result[3]

    # 入力添字 -> 出力添字
    vertex_map = {}
    for out_index, sources in enumerate(orig_verts):
        for source in sources:
            vertex_map[source] = out_index

    points = [(vertex[0], vertex[1]) for vertex in out_verts]
    faces = []
    for face in out_faces:
        if len(face) == 3:
            faces.append(tuple(face))
        else:  # 念のための扇状分割(CDT_INSIDE なら本来は三角形だけ)
            for index in range(1, len(face) - 1):
                faces.append((face[0], face[index], face[index + 1]))

    chains = {
        edge_index: [vertex_map[index] for index in chain]
        for edge_index, chain in inputs["chains"].items()
    }
    points, faces, chains, merged = panel_mesh.merge_close_points(
        points, faces, chains, panel_mesh.MERGE_DIST
    )

    poly_area = inputs["area"]
    mesh_area = panel_mesh.triangle_area_sum(points, faces)
    return {
        "points": points,
        "faces": faces,
        "chains": chains,
        "stats": {
            "verts": len(points),
            "faces": len(faces),
            "boundary": inputs["boundary_count"],
            "poly_area": poly_area,
            "mesh_area": mesh_area,
            "area_err": (mesh_area - poly_area) / poly_area * 100.0 if poly_area > 1e-9 else 0.0,
            "merged": merged,
        },
    }


def assemble(parsed, target_edge=TARGET_EDGE, body_points=None, center_limbs=True):
    """パネル群を 3D に配置して縫合エッジまで作る(オブジェクトはまだ作らない)。

    返り値 dict: verts / faces / sewing / panel_ranges / panel_stats /
                 limb_report / sew_stats / components
    """
    verts = []
    faces = []
    panel_ranges = {}
    chains_by_panel = {}
    panel_stats = {}

    for name in parsed["order"]:
        panel = parsed["panels"][name]
        meshed = mesh_panel(panel, target_edge)
        base = len(verts)
        verts.extend(transform.place_panel(panel, meshed["points"]))
        faces.extend(
            (base + i, base + j, base + k) for (i, j, k) in meshed["faces"]
        )
        panel_ranges[name] = (base, len(verts))
        for edge_index, chain in meshed["chains"].items():
            chains_by_panel[(name, edge_index)] = [base + index for index in chain]
        panel_stats[name] = meshed["stats"]

    limb_report = []
    if center_limbs:
        verts, limb_report = limbs.center_limbs(
            parsed["panels"], parsed["stitches"], panel_ranges, verts, body_points or []
        )

    sewing, sew_stats = gc_stitches.build_sewing_edges(
        parsed["stitches"], chains_by_panel, verts
    )
    return {
        "verts": verts,
        "faces": faces,
        "sewing": sewing,
        "panel_ranges": panel_ranges,
        "panel_stats": panel_stats,
        "limb_report": limb_report,
        "sew_stats": sew_stats,
        "gaps": gc_stitches.gap_stats(sewing, verts),
        "components": topology.count_face_components(len(verts), faces),
    }


def create_object(assembled, scene, collection_name=COLLECTION_NAME, object_name=CLOTH_OBJECT_NAME):
    """組み上げた頂点・面・縫合エッジから布オブジェクトを作ってコレクションに入れる"""
    from mathutils import Matrix

    suffix = resolve_suffix([collection_name, object_name])
    collection = bpy.data.collections.new(collection_name + suffix)
    scene.collection.children.link(collection)

    mesh = bpy.data.meshes.new(object_name + suffix)
    mesh.from_pydata(
        [tuple(vertex) for vertex in assembled["verts"]],
        [tuple(edge) for edge in assembled["sewing"]],
        [tuple(face) for face in assembled["faces"]],
    )
    mesh.update(calc_edges=True)
    mesh.validate(verbose=False)

    cloth = bpy.data.objects.new(object_name + suffix, mesh)
    collection.objects.link(cloth)
    cloth.matrix_world = Matrix.Identity(4)

    # パネルごとの頂点グループ。Cloth の部分的な剛性・ピン留めに使う
    for name, (start, end) in assembled["panel_ranges"].items():
        group = cloth.vertex_groups.new(name="P_" + name)
        group.add(list(range(start, end)), 1.0, "REPLACE")
    for index, entry in enumerate(assembled["limb_report"]):
        indices = []
        for name in entry["panels"]:
            start, end = assembled["panel_ranges"][name]
            indices.extend(range(start, end))
        group = cloth.vertex_groups.new(name="GC_Limb_%02d" % index)
        group.add(indices, 1.0, "REPLACE")

    return {"object": cloth, "collection": collection}
