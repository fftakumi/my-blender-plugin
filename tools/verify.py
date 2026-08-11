#!/usr/bin/env python
"""層2の検証(Blender 必須): UV・マテリアル・モディファイア評価後メッシュ + レンダー6枚。

    blender --background --factory-startup -noaudio --python-exit-code 1 out.blend \
        --python tools/verify.py -- --report report.json --renders renders/

層1(トポロジ・寸法・自己交差)は tools/precheck.py が Blender 無しで回すぶんと
同じコードで再計算し、**.blend の中身が層1の想定と一致しているか**まで突き合わせる。
"""

import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Vector

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from my_blender_plugin.costume import (  # noqa: E402
    build,
    parts,
    spec as spec_module,
    validate,
)

#: レンダーの向き。(名前, カメラ方向の単位ベクトル, 寄せ具合)
VIEWS = (
    ("front", (0.0, -1.0, 0.15), 1.0),
    ("side", (1.0, 0.0, 0.15), 1.0),
    ("back", (0.0, 1.0, 0.15), 1.0),
    ("oblique", (-0.8, -0.8, 0.55), 1.0),
    ("hem", (0.0, -1.0, -0.45), 0.45),
)
RENDER_SIZE = 720
EEVEE_SAMPLES = 24


# ------------------------------------------------------------------ 層2の検査


def uv_report(obj):
    """UV レイヤの健全性とテクセル密度のばらつき"""
    mesh = obj.data
    if not mesh.uv_layers:
        return {"has_uv": False}
    layer = mesh.uv_layers.active or mesh.uv_layers[0]
    coords = [tuple(item.uv) for item in layer.data]
    outside = sum(1 for u, v in coords if u < -1e-6 or u > 1 + 1e-6 or v < -1e-6 or v > 1 + 1e-6)

    zero_area = 0
    densities = []
    uv_area_total = 0.0
    for polygon in mesh.polygons:
        loop = [coords[index] for index in polygon.loop_indices]
        count = len(loop)
        area_uv = abs(
            sum(
                loop[i][0] * loop[(i + 1) % count][1] - loop[(i + 1) % count][0] * loop[i][1]
                for i in range(count)
            )
        ) / 2.0
        uv_area_total += area_uv
        if area_uv <= 1e-12:
            zero_area += 1
        elif polygon.area > 0:
            densities.append(area_uv / polygon.area)

    if densities:
        mean = sum(densities) / len(densities)
        variance = sum((value - mean) ** 2 for value in densities) / len(densities)
        cv = math.sqrt(variance) / mean if mean else 0.0
    else:
        cv = None

    return {
        "has_uv": True,
        "layer": layer.name,
        "outside_unit_square": outside,
        "zero_area_uv_faces": zero_area,
        "uv_utilisation": uv_area_total,  # 0-1 空間のうち使っている面積
        "texel_density_cv": cv,
    }


def material_report(obj):
    """全面にマテリアルが付いているか、両面表示になっているか"""
    mesh = obj.data
    slots = [slot.material.name if slot.material else None for slot in obj.material_slots]
    unassigned = sum(
        1
        for polygon in mesh.polygons
        if polygon.material_index >= len(slots) or slots[polygon.material_index] is None
    )
    culled = [
        slot.material.name
        for slot in obj.material_slots
        if slot.material is not None and slot.material.use_backface_culling
    ]
    return {
        "slots": slots,
        "faces_without_material": unassigned,
        "backface_culled_materials": culled,
    }


def object_geometry(obj):
    """オブジェクトのメッシュをワールド座標の (頂点, 面) で取り出す"""
    matrix = obj.matrix_world
    return (
        [tuple(matrix @ vertex.co) for vertex in obj.data.vertices],
        [tuple(polygon.vertices) for polygon in obj.data.polygons],
    )


def compare_to_spec(part_mesh, verts, faces):
    """spec から作り直した PartMesh と .blend の中身が一致しているか"""
    if len(verts) != len(part_mesh.verts) or len(faces) != len(part_mesh.quads):
        return {
            "ok": False,
            "reason": "頂点/面の数が違う (blend %d/%d, spec %d/%d)"
            % (len(verts), len(faces), len(part_mesh.verts), len(part_mesh.quads)),
            "max_offset": None,
        }
    offset = max(
        (Vector(a) - Vector(b)).length for a, b in zip(verts, part_mesh.verts)
    )
    faces_match = [tuple(face) for face in faces] == [tuple(q) for q in part_mesh.quads]
    return {
        "ok": offset < 1e-6 and faces_match,
        "reason": None if faces_match else "面のインデックスが違う",
        "max_offset": offset,
    }


# ------------------------------------------------------------------ レンダー


def _bounds(objects):
    lows = [float("inf")] * 3
    highs = [float("-inf")] * 3
    for obj in objects:
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            for axis in range(3):
                lows[axis] = min(lows[axis], point[axis])
                highs[axis] = max(highs[axis], point[axis])
    center = Vector([(lows[axis] + highs[axis]) / 2.0 for axis in range(3)])
    size = max(highs[axis] - lows[axis] for axis in range(3))
    return center, size, lows, highs


def setup_render(objects):
    """カメラ・ライト・ワールド・エンジンを用意する。毎回同じ画角になるよう bbox から決める"""
    scene = bpy.context.scene
    center, size, _lows, _highs = _bounds(objects)

    # 既定のビュー変換(AgX)は色を大きく持ち上げて彩度を落とすので、
    # マテリアル色を目視で確かめる用途では Standard にする(紺が白く見えてしまう)
    if hasattr(scene, "view_settings"):
        try:
            scene.view_settings.view_transform = "Standard"
            scene.view_settings.look = "None"
        except (TypeError, AttributeError):  # pragma: no cover - バージョン差
            pass

    world = bpy.data.worlds.get("CostumeWorld") or bpy.data.worlds.new("CostumeWorld")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        # 環境光は形が読める程度に弱く。強いと全体が白飛びして色が分からなくなる
        background.inputs[0].default_value = (0.09, 0.10, 0.13, 1.0)
        background.inputs[1].default_value = 1.0
    scene.world = world

    for name, offset, energy_scale, light_size in (
        ("CostumeKey", (-1.4, -1.7, 1.8), 90.0, 1.5),
        ("CostumeFill", (1.8, -1.2, 0.4), 28.0, 2.5),
    ):
        light_data = bpy.data.lights.new(name, type="AREA")
        light_data.energy = energy_scale * max(size, 0.1) ** 2
        light_data.size = size * light_size
        light = bpy.data.objects.new(name, light_data)
        scene.collection.objects.link(light)
        light.location = center + Vector(offset) * size
        light.rotation_euler = (
            (center - light.location).to_track_quat("-Z", "Y").to_euler()
        )

    camera_data = bpy.data.cameras.new("CostumeCam")
    camera_data.lens = 60.0
    camera = bpy.data.objects.new("CostumeCam", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera

    scene.render.resolution_x = scene.render.resolution_y = RENDER_SIZE
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    try:
        scene.render.engine = "BLENDER_EEVEE"
        scene.eevee.taa_render_samples = EEVEE_SAMPLES
        engine = "BLENDER_EEVEE"
    except Exception:  # pragma: no cover - GPU が無い環境向けのフォールバック
        bpy.ops.preferences.addon_enable(module="cycles")
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 16
        engine = "CYCLES"
    return camera, center, size, engine


def aim(camera, center, size, direction, zoom):
    """カメラを center に向けて距離を bbox から決める"""
    offset = Vector(direction)
    offset.normalize()
    camera.location = center + offset * (size * 2.2 * zoom)
    # -Z 軸を center に向ける
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()


def render_views(objects, out_dir):
    """規定の5方向 + 裏面カリング1枚をレンダーする"""
    os.makedirs(out_dir, exist_ok=True)
    camera, center, size, engine = setup_render(objects)
    scene = bpy.context.scene
    written = []

    for name, direction, zoom in VIEWS:
        aim(camera, center, size, direction, zoom)
        scene.render.filepath = os.path.join(out_dir, "%s.png" % name)
        bpy.ops.render.render(write_still=True)
        written.append(scene.render.filepath + ".png" if not scene.render.filepath.endswith(".png") else scene.render.filepath)

    # 法線の目視ゲート: 裏面カリングを一時的に入れる。法線が内向きなら面が消える
    materials = {slot.material for obj in objects for slot in obj.material_slots if slot.material}
    saved = {material: material.use_backface_culling for material in materials}
    for material in materials:
        material.use_backface_culling = True
    aim(camera, center, size, (0.0, -1.0, 0.15), 1.0)
    scene.render.filepath = os.path.join(out_dir, "backface.png")
    bpy.ops.render.render(write_still=True)
    written.append(scene.render.filepath)
    for material, value in saved.items():
        material.use_backface_culling = value

    return {"engine": engine, "images": written, "size": RENDER_SIZE}


# ------------------------------------------------------------------ 本体


def load_spec_from_blend():
    """generate.py が埋め込んだ spec テキストを読み出す"""
    text = bpy.data.texts.get("costume_spec.json")
    if text is None:
        raise SystemExit(
            "この .blend に costume_spec.json が入っていません。"
            "tools/generate.py で作った .blend を渡してください"
        )
    return spec_module.normalize_spec(json.loads(text.as_string()))


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--renders")
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args(argv)

    normalized = load_spec_from_blend()
    built = parts.build_all(normalized)
    report = validate.costume_report(built, normalized)
    report["spec_hash"] = spec_module.spec_hash(normalized)
    report["blender"] = bpy.app.version_string
    report["layer"] = "1+2"

    collection = bpy.data.collections.get(build.collection_name(normalized["name"]))
    if collection is None:
        raise SystemExit("コレクション %r が見つかりません" % build.collection_name(normalized["name"]))
    objects = [obj for obj in collection.objects if obj.type == "MESH"]
    by_name = {obj.name: obj for obj in objects}

    layer2 = {}
    hard = {}
    for part_mesh in built["parts"]:
        obj = by_name.get(part_mesh.name)
        if obj is None:
            hard["object_exists.%s" % part_mesh.name] = {
                "ok": False,
                "value": sorted(by_name),
                "expected": part_mesh.name,
            }
            continue
        verts, faces = object_geometry(obj)
        entry = {
            "uv": uv_report(obj),
            "material": material_report(obj),
            "spec_match": compare_to_spec(part_mesh, verts, faces),
            "evaluated": validate.raw_mesh_report(
                obj.name, *(lambda g: (g["verts"], g["faces"]))(build.evaluated_part_mesh(obj))
            ),
            "modifiers": [(modifier.name, modifier.type) for modifier in obj.modifiers],
        }
        layer2[part_mesh.name] = entry

        prefix = part_mesh.name
        uv = entry["uv"]
        hard["%s.has_uv" % prefix] = _ok(uv.get("has_uv"), uv.get("has_uv"), True)
        if uv.get("has_uv"):
            hard["%s.uv_inside_unit" % prefix] = _ok(
                uv["outside_unit_square"] == 0, uv["outside_unit_square"], 0
            )
            hard["%s.uv_no_zero_area" % prefix] = _ok(
                uv["zero_area_uv_faces"] == 0, uv["zero_area_uv_faces"], 0
            )
        material = entry["material"]
        hard["%s.material_on_every_face" % prefix] = _ok(
            material["faces_without_material"] == 0, material["faces_without_material"], 0
        )
        hard["%s.double_sided" % prefix] = _ok(
            not material["backface_culled_materials"], material["backface_culled_materials"], []
        )
        hard["%s.blend_matches_spec" % prefix] = _ok(
            entry["spec_match"]["ok"], entry["spec_match"], True
        )
        hard["%s.evaluated_mesh" % prefix] = _ok(
            entry["evaluated"]["failed"] == [], entry["evaluated"]["failed"], []
        )

    report["layer2"] = layer2
    report["hard_layer2"] = hard
    layer2_failed = sorted(key for key, value in hard.items() if not value["ok"])
    report["failed"] = sorted(report["failed"] + layer2_failed)
    report["verdict"] = (
        "FAIL" if report["failed"] else ("WARN" if report["warned"] else "PASS")
    )

    if args.renders and not args.no_render:
        report["renders"] = render_views(objects, args.renders)

    with open(args.report, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1, default=list)

    print(
        "VERIFY %s verdict=%s failed=%s warned=%s"
        % (normalized["name"], report["verdict"], report["failed"], report["warned"])
    )
    if report["failed"]:
        raise SystemExit("検証で不合格: " + ", ".join(report["failed"]))


def _ok(condition, value, expected):
    return {"ok": bool(condition), "value": value, "expected": expected}


if __name__ == "__main__":
    main()
