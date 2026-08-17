"""既知の左右非対称の台帳。

検査そのものは validate.mirror_gap(costume_report が全パーツに自動で掛ける
warn ゲート)がやる。ここは**まだ直していないバグの現在値**を記録して、
(a) 悪化していない (b) 直したのに台帳から消し忘れていない、の2つだけを見る。
発見の経緯: ショーツの脚ぐりを曲線にしたら左右の周長が 0.16% 違い、調べたら
ズボンでも元から 16mm ずれていた(脚が円の間は寸法ゲートに映らない)。
"""

import pytest

from my_blender_plugin.costume import parts, spec as spec_module, validate

#: 自分自身の鏡像とずれるパーツ(メートル)。**既知のバグの上限**であって許容値では
#: ない。原因は閉じた前身頃(front_style="closed")の袖ぐりまわり。直したら消すこと
KNOWN_SELF_ASYMMETRY = {
    "onepiece/Dress_Bodice": 0.0288,
    "swimsuit/Swimsuit_Body": 0.0192,
}

#: 左右ペア(_L / _R)が鏡像になっていないパーツ。袖は袖ぐりの実物から作るので、
#: 袖ぐりのずれがそのまま伝わる(上と同じ原因)
KNOWN_PAIR_ASYMMETRY = {
    "blouse/Blouse_Sleeve_L": 0.0204,
    "onepiece/Dress_Sleeve_L": 0.0297,
}


def built_parts(preset):
    return {
        mesh.name: mesh
        for mesh in parts.build_all(spec_module.load_preset(preset))["parts"]
    }


@pytest.mark.parametrize("preset", spec_module.list_presets())
def test_asymmetry_never_grows_and_new_parts_are_symmetric(preset):
    meshes = built_parts(preset)
    for name, mesh in meshes.items():
        if name.endswith("_R"):
            continue
        if name.endswith("_L"):
            mate = meshes.get(name[:-2] + "_R")
            assert mate is not None, (preset, name)
            gap = validate.mirror_gap(mesh.verts, mate.verts)
            known = KNOWN_PAIR_ASYMMETRY.get("%s/%s" % (preset, name))
        else:
            gap = validate.mirror_gap(mesh.verts)
            known = KNOWN_SELF_ASYMMETRY.get("%s/%s" % (preset, name))
        if known is None:
            assert gap == pytest.approx(0.0, abs=1e-9), (preset, name, gap)
        else:
            assert gap <= known * 1.01, (preset, name, gap, known)


def test_the_ledger_has_no_stale_entries():
    """直したのに台帳へ残っていると、次の回帰を隠してしまう"""
    for key in list(KNOWN_SELF_ASYMMETRY) + list(KNOWN_PAIR_ASYMMETRY):
        preset, name = key.split("/")
        meshes = built_parts(preset)
        assert name in meshes, key
        if key in KNOWN_SELF_ASYMMETRY:
            gap = validate.mirror_gap(meshes[name].verts)
        else:
            gap = validate.mirror_gap(meshes[name].verts, meshes[name[:-2] + "_R"].verts)
        assert gap > 1e-9, "%s は対称になっている。台帳から消すこと" % key
