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
