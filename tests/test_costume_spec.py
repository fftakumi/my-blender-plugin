import json
import os

import pytest

from my_blender_plugin.costume import spec as spec_module


def test_default_spec_is_valid():
    normalized = spec_module.normalize_spec(spec_module.default_spec())
    assert normalized["schema"] == spec_module.SCHEMA_VERSION
    assert [part["name"] for part in normalized["parts"]] == [
        "Skirt_Waistband",
        "Skirt_Body",
    ]
    # 既定値がすべて埋まる
    body = next(part for part in normalized["parts"] if part["type"] == "skirt_body")
    assert body["params"]["segments"] == 24
    assert body["params"]["pleats"] == 0
    assert normalized["assumed_height"] == pytest.approx(1.53)


def test_unknown_param_key_is_rejected():
    spec = spec_module.default_spec()
    spec["parts"][1]["params"]["segment"] = 24  # タイポ
    with pytest.raises(spec_module.SpecError) as error:
        spec_module.normalize_spec(spec)
    assert "segment" in str(error.value)


def test_out_of_range_value_is_rejected():
    spec = spec_module.default_spec()
    spec["parts"][1]["params"]["segments"] = 2  # 最小3
    with pytest.raises(spec_module.SpecError) as error:
        spec_module.normalize_spec(spec)
    assert "segments" in str(error.value)


def test_unknown_part_type_is_rejected():
    spec = spec_module.default_spec()
    spec["parts"] = [{"type": "cape", "name": "Cape"}]
    with pytest.raises(spec_module.SpecError):
        spec_module.normalize_spec(spec)


def test_duplicate_part_names_are_rejected():
    spec = spec_module.default_spec()
    spec["parts"][0]["name"] = spec["parts"][1]["name"]
    with pytest.raises(spec_module.SpecError) as error:
        spec_module.normalize_spec(spec)
    assert "重複" in str(error.value)


def test_joint_referencing_missing_part_is_rejected():
    spec = spec_module.default_spec()
    spec["joints"][0]["a"] = "Nope"
    with pytest.raises(spec_module.SpecError):
        spec_module.normalize_spec(spec)


def test_joint_ring_must_be_top_or_bottom():
    spec = spec_module.default_spec()
    spec["joints"][0]["a_ring"] = "middle"
    with pytest.raises(spec_module.SpecError):
        spec_module.normalize_spec(spec)


def test_material_reference_must_exist():
    spec = spec_module.default_spec()
    spec["parts"][0]["material"] = "missing"
    with pytest.raises(spec_module.SpecError):
        spec_module.normalize_spec(spec)


def test_bool_is_not_accepted_as_number():
    spec = spec_module.default_spec()
    spec["parts"][1]["params"]["segments"] = True
    with pytest.raises(spec_module.SpecError):
        spec_module.normalize_spec(spec)


def test_spec_hash_is_stable_and_sensitive():
    first = spec_module.normalize_spec(spec_module.default_spec())
    second = spec_module.normalize_spec(spec_module.default_spec())
    assert spec_module.spec_hash(first) == spec_module.spec_hash(second)
    second["parts"][1]["params"]["flare"] += 0.1
    assert spec_module.spec_hash(first) != spec_module.spec_hash(second)


def test_spec_errors_returns_list_instead_of_raising():
    assert spec_module.spec_errors(spec_module.default_spec()) == []
    broken = spec_module.default_spec()
    broken["parts"][1]["params"]["flare"] = 99.0
    assert spec_module.spec_errors(broken)


@pytest.mark.parametrize("name", ["skirt_a0", "skirt_flare", "skirt_pleated"])
def test_bundled_presets_load_and_validate(name):
    normalized = spec_module.load_preset(name)
    assert normalized["name"] == name


def test_all_presets_are_listed():
    assert set(spec_module.list_presets()) >= {"skirt_a0", "skirt_flare", "skirt_pleated"}


def test_missing_preset_raises():
    with pytest.raises(spec_module.SpecError):
        spec_module.load_preset("does_not_exist")


def test_json_schema_hint_mentions_every_part_type():
    hint = spec_module.json_schema_hint()
    for part_type in spec_module.PART_SCHEMAS:
        assert part_type in hint


def test_json_schema_hint_explains_attachment_and_joints():
    # attach_to / joints.kind / armhole リングの説明が hint に無いと、
    # プロンプトに忠実な AI のブラウス出力が attach_to 必須検証で全滅する
    hint = spec_module.json_schema_hint()
    assert "attach_to" in hint
    for part_type, target in spec_module.ATTACH_TARGETS.items():
        assert "%s は attach_to に %s" % (part_type, target) in hint
    for kind in spec_module.JOINT_KINDS:
        assert kind in hint
    for ring in spec_module.JOINT_RING_NAMES:
        assert ring in hint
    # AI に書かせない約束のフィールド
    assert "sizing キーは書いてはいけません" in hint
    assert "身長の指定が無ければキーごと省略" in hint


def _collect_keys(node, keys):
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            _collect_keys(value, keys)
    elif isinstance(node, list):
        for item in node:
            _collect_keys(item, keys)


def test_json_schema_hint_covers_every_preset_key():
    # 同梱プリセットは「hint に従って書いた spec の実例」。プリセットの生 JSON に
    # 登場するキーが hint に載っていない = AI に伝わらない構文がある、を検出する
    # (過去に attach_to と kind が漏れて AI のブラウス級出力が全滅していた)
    hint = spec_module.json_schema_hint()
    for name in spec_module.list_presets():
        path = os.path.join(spec_module.PRESET_DIR, name + ".json")
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        keys = set()
        for key, value in raw.items():
            keys.add(key)
            if key == "materials":
                # マテリアルのキー名(main 等)は自由な名前なので中身だけ見る
                for material in value.values():
                    _collect_keys(material, keys)
            elif key == "sizing":
                # sizing は hint が明示的に「書いてはいけません」としている領域
                # (中身の検証は sizing.py が持つ)。載っていないのが正しいので、
                # 中身のキーは突き合わせない。preset は AI ではなく人が書くので使える
                continue
            else:
                _collect_keys(value, keys)
        missing = {key for key in keys if key not in hint}
        assert not missing, "%s のキーが hint に無い: %s" % (name, sorted(missing))


def test_a_blouse_class_spec_written_from_the_hint_alone_validates():
    # hint に書いてあることだけを頼りに手書きしたブラウス級 spec が
    # normalize を通ること(= hint と validator が矛盾していないこと)
    spec = {
        "schema": spec_module.SCHEMA_VERSION,
        "name": "hinted_blouse",
        "materials": {"main": {}},
        "parts": [
            {"type": "bodice", "name": "Body", "material": "main", "params": {}},
            {
                "type": "sleeve",
                "name": "Sleeve_L",
                "material": "main",
                "attach_to": "Body",
                "params": {"side": "l"},
            },
            {
                "type": "sleeve",
                "name": "Sleeve_R",
                "material": "main",
                "attach_to": "Body",
                "params": {"side": "r"},
            },
        ],
        "joints": [
            {"a": "Body", "a_ring": "armhole_l", "b": "Sleeve_L", "b_ring": "top", "kind": "sewn"},
            {"a": "Body", "a_ring": "armhole_r", "b": "Sleeve_R", "b_ring": "top", "kind": "sewn"},
        ],
    }
    normalized = spec_module.normalize_spec(spec)
    assert [part["type"] for part in normalized["parts"]] == ["bodice", "sleeve", "sleeve"]
