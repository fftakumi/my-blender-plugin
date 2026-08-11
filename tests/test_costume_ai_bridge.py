import json
import os
import types

import pytest

from my_blender_plugin.costume import ai_bridge, parts, spec as spec_module, validate


def blouse_envelope_json():
    """封筒型の正常応答(spec は同梱 blouse プリセットの生 JSON)"""
    path = os.path.join(spec_module.PRESET_DIR, "blouse.json")
    with open(path, encoding="utf-8") as handle:
        blouse = json.load(handle)
    return json.dumps(
        {
            "similar_preset": "blouse",
            "differences": ["袖丈が少し短い"],
            "unsupported": ["フリルは表現できない"],
            "parts_plan": ["bodice: 胴", "sleeve×2: 袖"],
            "spec": blouse,
        },
        ensure_ascii=False,
    )


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


def test_prompt_walks_through_the_design_procedure():
    """類似プリセット→差分→パーツリスト→spec の順に考えさせる指示があること"""
    prompt = ai_bridge.build_prompt("なにか")
    assert "similar_preset" in prompt
    assert "differences" in prompt
    assert "unsupported" in prompt
    assert "parts_plan" in prompt
    # attach_to の説明(schema hint)も同梱される
    assert "attach_to" in prompt


def test_prompt_carries_full_examples_and_a_catalog():
    prompt = ai_bridge.build_prompt("なにか")
    # 完全な実例は2つ(skirt_flare と blouse の生 JSON)
    assert '"Blouse_Bodice"' in prompt
    assert '"Skirt_Body"' in prompt
    # 残りは1行カタログとして全プリセットが載る
    for name in spec_module.list_presets():
        assert name in prompt


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


def test_spec_from_text_asks_the_ai_even_when_keywords_would_solve_it():
    """AI が解釈の主役。辞書で解ける文でも AI に読ませる(辞書はフォールバック)"""
    calls = []

    def runner(prompt):
        calls.append(prompt)
        return valid_spec_json()

    result = ai_bridge.spec_from_text("紺のプリーツミニスカート", runner=runner)
    assert result["source"] == "ai"
    assert result["parse"]["confidence"] >= 0.55  # 辞書でも解けていたのに
    assert len(calls) == 1


def test_spec_from_text_uses_the_ai_for_unknown_garments():
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


# ---------------------------------------- 封筒型出力・リトライ・フォールバック連鎖


def test_an_envelope_with_a_blouse_spec_travels_the_whole_pipeline():
    """封筒型応答のブラウス級 spec が採用され、ビルド前検証まで通ること"""
    result = ai_bridge.spec_from_text("白い半袖ブラウス", runner=lambda p: blouse_envelope_json())
    assert result["source"] == "ai"
    assert result["attempts"] == 1
    assert result["unsupported"] == ["フリルは表現できない"]
    types = [part["type"] for part in result["spec"]["parts"]]
    assert "bodice" in types and "sleeve" in types
    # 採用された spec は実際に組めて検証も通る
    built = parts.build_all(result["spec"])
    report = validate.costume_report(built, result["spec"])
    assert report["failed"] == []


def test_a_bare_spec_without_an_envelope_is_still_accepted():
    """封筒を忘れた旧形式(素の spec JSON)にも後方互換で対応する"""
    result = ai_bridge.request_spec("なにか", runner=lambda p: valid_spec_json())
    assert result["spec"]["name"] == "ai_skirt"
    assert result["envelope"] == {}


def test_a_bad_first_answer_is_retried_with_the_failure_reason():
    """1回目が不正なら、失敗理由を添えて1回だけ作り直させる"""
    prompts = []

    def runner(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return json.dumps(
                {"similar_preset": "blouse", "spec": {"schema": 1, "name": "x", "parts": [{"type": "hood"}]}}
            )
        return blouse_envelope_json()

    result = ai_bridge.spec_from_text("白いブラウス", runner=runner)
    assert result["source"] == "ai"
    assert result["attempts"] == 2
    assert len(prompts) == 2
    # 2回目のプロンプトには前回の出力と不採用の理由が含まれる
    assert "不採用の理由" in prompts[1]
    assert "hood" in prompts[1]


def test_a_runner_failure_is_not_retried():
    """CLI 不在やタイムアウトは環境の問題。120秒×2 待たせない"""
    calls = []

    def runner(prompt):
        calls.append(prompt)
        raise ai_bridge.AIBridgeError("claude が居ない")

    result = ai_bridge.spec_from_text("謎の衣装", runner=runner)
    assert result["source"] == "keywords"
    assert len(calls) == 1


def test_a_spec_that_normalises_but_cannot_build_is_rejected():
    """sizing の中身は normalize が見ない。ビルド前検証が防波堤になること"""
    broken = json.dumps(
        {
            "schema": 1,
            "name": "x",
            "materials": {"main": {}},
            "sizing": {"nonsense_measure": 1.0},
            "parts": [{"type": "skirt_body", "name": "S", "material": "main", "params": {}}],
            "joints": [],
        }
    )
    result = ai_bridge.spec_from_text("謎の衣装", runner=lambda p: broken)
    assert result["source"] == "keywords"
    assert "組み立てられませんでした" in result["ai_error"]


def test_fallback_prefers_the_preset_the_ai_said_was_similar():
    """封筒は読めたが spec が全滅 → AI が挙げた類似プリセットに落ちる"""
    bad_spec_envelope = json.dumps(
        {
            "similar_preset": "blouse",
            "spec": {"schema": 1, "name": "x", "parts": [{"type": "hood"}]},
        }
    )
    result = ai_bridge.spec_from_text("かっこいい鎧", runner=lambda p: bad_spec_envelope)
    assert result["source"] == "keywords"
    assert result["base_preset"] == "blouse"
    assert "blouse" in result["fallback_warning"]
    assert "かっこいい鎧" in result["fallback_warning"]


def test_fallback_warning_is_explicit_when_nothing_was_understood():
    result = ai_bridge.spec_from_text(
        "かっこいい鎧", runner=lambda p: (_ for _ in ()).throw(ai_bridge.AIBridgeError("オフ"))
    )
    assert result["source"] == "keywords"
    assert result["base_preset"] == "skirt_flare"
    warning = result["fallback_warning"]
    assert "かっこいい鎧" in warning
    assert "skirt_flare" in warning
    assert "解釈できなかった" in warning


def test_no_warning_when_the_dictionary_understood_the_garment():
    """種類が辞書で分かっているフォールバックは妥当な形なので警告しない"""

    def runner(prompt):
        raise ai_bridge.AIBridgeError("オフ")

    result = ai_bridge.spec_from_text("紺のプリーツミニスカート", runner=runner)
    assert result["source"] == "keywords"
    assert result["fallback_warning"] is None
    assert result["ai_error"]


def test_explicit_height_comes_only_from_the_text():
    def refuse(prompt):
        raise ai_bridge.AIBridgeError("オフ")

    with_height = ai_bridge.spec_from_text("身長160cmのスカート", runner=refuse)
    assert with_height["explicit_height"] == pytest.approx(1.6)
    without = ai_bridge.spec_from_text("スカート", runner=refuse)
    assert without["explicit_height"] is None


def test_default_runner_picks_a_fast_model_by_default():
    """spec 生成は構造化タスク。実測で既定モデル約120秒/回 → haiku 約55秒/回。
    品質は正規化+ビルド前検証+リトライが担保するので速いモデルを既定にする"""
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    original = ai_bridge.subprocess.run
    ai_bridge.subprocess.run = fake_run
    try:
        ai_bridge.default_runner("説明文", command="claude")
        argv = seen["argv"]
        assert argv[argv.index("--model") + 1] == ai_bridge.DEFAULT_MODEL
        # model=None なら CLI の既定モデルに任せる
        ai_bridge.default_runner("説明文", command="claude", model=None)
        assert "--model" not in seen["argv"]
    finally:
        ai_bridge.subprocess.run = original


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
