"""縫合エッジで結ばれた頂点ペアだけを溶接する(**bpy 依存**)。

`tests/conftest.py` のフェイク bpy には `bmesh` が無いので
**このモジュールをトップレベルで import してはいけない**。

なぜ標準機能で足りないか(CLAUDE.md の最重要方針への回答):

- `メッシュ > マージ > 距離で` (`bpy.ops.mesh.remove_doubles`) は **全体マージ**。
  前中心・後中心のパネル対は spec 上まったく同じ座標を共有しているので、
  0.001cm でも別パネル同士が溶接されて面コンポーネントが 16 -> 12 に減る
- Weld モディファイアの `CONNECTED` モードは「エッジで繋がった **距離しきい値以内** の
  頂点」を溶接する。縫合ギャップは 0.3〜1.0cm に収束する一方、素の三角形の辺は
  約 1.5cm なので、閉じ切っていない縫い目を拾えるしきい値では **本物の辺まで潰れる**

ここでやるのは「**面を持たない孤立エッジ = 縫合エッジ** で結ばれた頂点だけを、
距離に関係なく連結成分ごとに1点へまとめる」。距離ではなく繋がりで決めるので
別パネルが巻き込まれることが原理的に無い。
"""

import bpy

from . import topology

# このギャップを超えて残っていたら縫合が失敗している疑い [cm]
DEFAULT_MAX_GAP = 2.0


def duplicate_evaluated(context, source):
    """現在フレームの評価済みメッシュを新しいオブジェクトに複製する(元は無変更)"""
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = source.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh().copy()
    evaluated.to_mesh_clear()

    name = "%s_Welded_f%d" % (source.name.split(".")[0], context.scene.frame_current)
    mesh.name = name
    duplicate = bpy.data.objects.new(name, mesh)
    for collection in source.users_collection:
        collection.objects.link(duplicate)
    if not duplicate.users_collection:
        context.scene.collection.objects.link(duplicate)
    duplicate.matrix_world = source.matrix_world.copy()
    return duplicate


def weld_sewing_pairs(obj, remove_loose=True):
    """孤立エッジのペアを連結成分ごとに1点へ溶接する。統計の dict を返す。

    縫合エッジが1本も無ければ RuntimeError。
    """
    import bmesh
    from mathutils import Vector

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()

    before = {
        "verts": len(bm.verts),
        "edges": len(bm.edges),
        "faces": len(bm.faces),
    }

    # 縫合エッジ = 面を持たない孤立エッジ。Cloth はトポロジを変えないので
    # 取り込み時に張った縫合エッジがシミュ後もそのまま残っている。
    pairs = [
        (edge.verts[0].index, edge.verts[1].index)
        for edge in bm.edges
        if len(edge.link_faces) == 0
    ]
    if not pairs:
        bm.free()
        raise RuntimeError("縫合エッジ(孤立エッジ)が1本もありません")

    gaps = [
        (bm.verts[a].co - bm.verts[b].co).length for a, b in pairs
    ]
    gap_max = max(gaps)
    gap_mean = sum(gaps) / len(gaps)

    targetmap = {}
    groups = topology.pair_groups(pairs)
    for members in groups:
        vertices = [bm.verts[index] for index in members]
        centre = Vector((0.0, 0.0, 0.0))
        for vertex in vertices:
            centre += vertex.co
        centre /= len(vertices)
        keep = vertices[0]
        keep.co = centre
        for vertex in vertices[1:]:
            targetmap[vertex] = keep

    bmesh.ops.weld_verts(bm, targetmap=targetmap)

    loose_removed = 0
    if remove_loose:
        bm.edges.ensure_lookup_table()
        leftovers = [edge for edge in bm.edges if len(edge.link_faces) == 0]
        loose_removed = len(leftovers)
        if leftovers:
            bmesh.ops.delete(bm, geom=leftovers, context="EDGES")
        stray = [
            vertex for vertex in bm.verts if not vertex.link_edges and not vertex.link_faces
        ]
        if stray:
            bmesh.ops.delete(bm, geom=stray, context="VERTS")

    after = {
        "verts": len(bm.verts),
        "edges": len(bm.edges),
        "faces": len(bm.faces),
    }
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    return {
        "pairs": len(pairs),
        "groups": len(groups),
        "gap_max": gap_max,
        "gap_mean": gap_mean,
        "before": before,
        "after": after,
        "loose_removed": loose_removed,
        "components": topology.count_face_components(
            len(mesh.vertices), [tuple(polygon.vertices) for polygon in mesh.polygons]
        ),
    }
