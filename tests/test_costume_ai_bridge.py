import json
import types

import pytest

from my_blender_plugin.costume import ai_bridge, spec as spec_module


def valid_spec_json():
    return json.dumps(
        {
            "schema": 1,
            "name": "ai_skirt",
            "assumed_height": 1.6,
            "materials": {"main": {"base_color": [0.1, 0.1, 0.3]}},
            "parts": [
                {
                    "type": "skirt_body",
                    "name": "Skirt_Body",
                    "material": "main",
                    "params": {"segments": 24, "rings": 8, "flare": 1.4},
                }
            ],
            "joints": [],
        },
        ensure_ascii=False,
    )


def test_prompt_includes_the_text_and_the_schema():
    prompt = ai_bridge.build_prompt("紺のプリーツスカート")
    assert "紺のプリーツスカート" in prompt
    for part_type in spec_module.PART_SCHEMAS:
        assert part_type in prompt


def test_extract_json_from_a_bare_object():
    assert ai_bridge.extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_from_a_code_fence():
    assert ai_bridge.extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_chatter_around_it():
    output = 'はい、こちらです。\n{"a": 1, "b": [2, 3]}\nご確認ください。'
    assert ai_bridge.extract_json(output) == {"a": 1, "b": [2, 3]}


def test_extract_json_rejects_empty_and_garbage():
    for output in ("", "   ", "JSON は出せません"):
        with pytest.raises(ai_bridge.AIBridgeError):
            ai_bridge.extract_json(output)


def test_extract_json_rejects_a_bare_list():
    with pytest.raises(ai_bridge.AIBridgeError):
        ai_bridge.extract_json("[1, 2, 3]")


def test_request_spec_normalises_the_ai_output():
    result = ai_bridge.request_spec("なにか", runner=lambda prompt: valid_spec_json())
    assert result["spec"]["name"] == "ai_skirt"
    assert result["spec"]["assumed_height"] == pytest.approx(1.6)
    # 既定値が埋まっている
    assert result["spec"]["parts"][0]["params"]["pleats"] == 0


def test_request_spec_rejects_an_invalid_ai_spec():
    bad = json.dumps({"schema": 1, "name": "x", "parts": [{"type": "cape"}]})
    with pytest.raises(ai_bridge.AIBridgeError) as error:
        ai_bridge.request_spec("なにか", runner=lambda prompt: bad)
    assert "検証に落ち" in str(error.value)


def test_request_spec_rejects_aliasing_from_the_ai():
    """AI が分割数の足りない山数を返しても通さない"""
    bad = json.dumps(
        {
            "schema": 1,
            "name": "x",
            "materials": {"main": {}},
            "parts": [
                {
                    "type": "skirt_body",
                    "name": "S",
                    "material": "main",
                    "params": {"segments": 24, "drape_folds": 20, "drape_depth": 0.1},
                }
            ],
            "joints": [],
        }
    )
    with pytest.raises(ai_bridge.AIBridgeError):
        ai_bridge.request_spec("なにか", runner=lambda prompt: bad)


def test_spec_from_text_uses_keywords_and_never_calls_the_ai():
    def explode(prompt):
        raise AssertionError("辞書で解けたのに AI を呼んだ")

    result = ai_bridge.spec_from_text("紺のプリーツミニスカート", runner=explode)
    assert result["source"] == "keywords"
    assert result["ai_error"] is None
    assert result["parse"]["confidence"] >= 0.55


def test_spec_from_text_falls_back_to_the_ai_when_keywords_fail():
    calls = []

    def runner(prompt):
        calls.append(prompt)
        return valid_spec_json()

    result = ai_bridge.spec_from_text("よくわからない謎の衣装", runner=runner)
    assert result["source"] == "ai"
    assert result["spec"]["name"] == "ai_skirt"
    assert len(calls) == 1


def test_spec_from_text_survives_an_ai_failure():
    """AI が使えない環境でもキーワード結果で続行する(例外を投げない)"""

    def runner(prompt):
        raise ai_bridge.AIBridgeError("claude が居ない")

    result = ai_bridge.spec_from_text("謎の衣装", runner=runner)
    assert result["source"] == "keywords"
    assert "claude が居ない" in result["ai_error"]
    assert result["spec"]["parts"]  # それでも作れる spec が返る


def test_spec_from_text_survives_ai_garbage():
    result = ai_bridge.spec_from_text("謎の衣装", runner=lambda prompt: "むりです")
    assert result["source"] == "keywords"
    assert result["ai_error"]


def test_default_runner_reports_a_missing_command(monkeypatch):
    monkeypatch.setattr(ai_bridge.shutil, "which", lambda name: None)
    with pytest.raises(ai_bridge.AIBridgeError) as error:
        ai_bridge.default_runner("prompt")
    assert "claude" in str(error.value)


def test_default_runner_surfaces_a_reason_printed_on_stdout(monkeypatch):
    """認証切れは stdout に出て stderr は空。stderr だけ見ると理由が消える"""

    class Completed:
        returncode = 1
        stdout = "Failed to authenticate: OAuth session expired"
        stderr = ""

    monkeypatch.setattr(ai_bridge.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(ai_bridge.subprocess, "run", lambda *a, **k: Completed())
    with pytest.raises(ai_bridge.AIBridgeError) as error:
        ai_bridge.default_runner("prompt")
    assert "OAuth session expired" in str(error.value)


def test_default_runner_never_reports_an_empty_reason(monkeypatch):
    class Completed:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(ai_bridge.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(ai_bridge.subprocess, "run", lambda *a, **k: Completed())
    with pytest.raises(ai_bridge.AIBridgeError) as error:
        ai_bridge.default_runner("prompt")
    assert "出力なし" in str(error.value)


def test_default_runner_disables_tools_and_settings():
    """`claude -p` に衣装の説明文がそのまま入る。**説明文はデータであって指示ではない。**

    何も付けずに起動すると、呼ばれた claude はユーザー設定で事前許可済みの
    ツールと cwd の CLAUDE.md を引き継ぐので、説明文経由のプロンプト
    インジェクションで許可済みツールが走り得る。出力は normalize_spec() を
    通すが、それは**実行中の副作用**を防がない。
    """
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    original = ai_bridge.subprocess.run
    ai_bridge.subprocess.run = fake_run
    try:
        ai_bridge.default_runner("説明文", command="claude")
    finally:
        ai_bridge.subprocess.run = original

    argv = seen["argv"]
    assert argv[0] == "claude"
    assert "-p" in argv and argv[-1] == "説明文"
    # ツールを一切許可しない / 事前許可を継承しない / 設定と CLAUDE.md を読まない
    assert argv[argv.index("--allowedTools") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "default"
    assert argv[argv.index("--setting-sources") + 1] == ""


def test_default_runner_passes_arguments_as_a_list():
    """シェルを介さないのでインジェクションの経路にならない"""
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["shell"] = kwargs.get("shell", False)
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    original = ai_bridge.subprocess.run
    ai_bridge.subprocess.run = fake_run
    try:
        ai_bridge.default_runner("a; rm -rf /", command="claude")
    finally:
        ai_bridge.subprocess.run = original
    assert isinstance(seen["argv"], list)
    assert seen["shell"] is False
    assert seen["argv"][-1] == "a; rm -rf /"
