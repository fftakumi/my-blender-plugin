"""AI(`claude -p`)へ「説明文 → spec JSON」だけを頼む橋。bpy 非依存。

方針: **AI は入力の解釈にしか使わない。** 形状の計算・寸法・マテリアル値は
プラグイン内の決定的な処理が決める。AI が返した JSON は必ず `spec.normalize_spec()`
を通し、落ちたら捨ててキーワード解析の結果にフォールバックする。

コマンド実行は `runner` 引数で差し替えられる。pytest はフェイクを注入して、
プロンプト生成と JSON 取り出し・検証だけを純粋にテストする。
"""

import json
import os
import re
import shutil
import subprocess

from . import parse_text, spec as spec_module

#: `claude -p` の応答を待つ上限(秒)。UI スレッドから呼ぶと固まるので必須
DEFAULT_TIMEOUT = 120

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class AIBridgeError(RuntimeError):
    """AI の呼び出しか応答の解釈に失敗したときに投げる"""


def build_prompt(text, hint=None):
    """spec スキーマを添えたプロンプトを組み立てる"""
    return "\n".join(
        [
            "あなたは Blender 用の衣装生成プラグインの入力解釈器です。",
            "次の衣装の説明を、下記スキーマの JSON 1個だけに変換してください。",
            "説明に無いことは推測せず、そのフィールドを省略してください(既定値が入ります)。",
            "",
            "--- 衣装の説明 ---",
            text.strip(),
            "--- 説明おわり ---",
            "",
            hint or spec_module.json_schema_hint(),
        ]
    )


def extract_json(output):
    """AI の出力から JSON を1個取り出す。コードフェンスや前後の文を許容する"""
    if not output or not output.strip():
        raise AIBridgeError("AI の出力が空でした")

    candidates = [match.strip() for match in _JSON_BLOCK.findall(output)]
    candidates.append(output.strip())
    # 最初の { から対応する } までを切り出したものも候補にする
    start = output.find("{")
    end = output.rfind("}")
    if start != -1 and end > start:
        candidates.append(output[start : end + 1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise AIBridgeError("AI の出力から JSON を取り出せませんでした: %r" % output[:400])


def default_runner(prompt, timeout=DEFAULT_TIMEOUT, command=None):
    """`claude -p` を subprocess で呼ぶ。

    Windows の `claude` は .cmd シムなので shutil.which で実体を解決する。
    出力の復号は cp932 に落ちないよう utf-8 を明示する。
    """
    executable = command or shutil.which("claude")
    if not executable:
        raise AIBridgeError(
            "claude コマンドが見つかりません。PATH を確認するか runner を渡してください"
        )
    try:
        completed = subprocess.run(
            [executable, "-p", prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise AIBridgeError("claude -p が %d 秒で終わりませんでした" % timeout) from error
    except OSError as error:
        raise AIBridgeError("claude -p の起動に失敗しました: %s" % error) from error
    if completed.returncode != 0:
        # 失敗理由が stdout に出ることがある(認証切れは stdout に
        # "Failed to authenticate: OAuth session expired" と出て stderr は空)。
        # stderr だけ見ると理由が分からないので両方を載せる。
        detail = (completed.stderr or "").strip() or (completed.stdout or "").strip()
        raise AIBridgeError(
            "claude -p が異常終了しました(code %d): %s"
            % (completed.returncode, detail[:400] or "(出力なし)")
        )
    return completed.stdout


def request_spec(text, runner=None, timeout=DEFAULT_TIMEOUT):
    """AI に spec を作らせ、**検証を通ったものだけ**返す。

    戻り値: {"spec": 正規化済み spec, "raw": AIの生JSON, "prompt": 送った文}
    検証に落ちたら AIBridgeError。
    """
    prompt = build_prompt(text)
    call = runner or (lambda value: default_runner(value, timeout=timeout))
    output = call(prompt)
    raw = extract_json(output)
    try:
        normalized = spec_module.normalize_spec(raw)
    except spec_module.SpecError as error:
        raise AIBridgeError("AI が返した spec が検証に落ちました: %s" % error) from error
    return {"spec": normalized, "raw": raw, "prompt": prompt}


def spec_from_text(text, runner=None, min_confidence=0.55, timeout=DEFAULT_TIMEOUT):
    """説明文 → 正規化済み spec。辞書で解ければ AI を呼ばない。

    戻り値: {"spec": ..., "source": "keywords"|"ai", "parse": 辞書解析の結果,
             "ai_error": AI が失敗したときの理由}

    AI が失敗しても例外にはせず、キーワード解析の結果で続行する
    (AI が使えない環境でもプラグインが動くようにするため)。
    """
    parsed = parse_text.parse(text)
    result = {"parse": parsed, "ai_error": None}

    if parsed["confidence"] >= min_confidence:
        result["spec"] = spec_module.normalize_spec(parsed["spec"])
        result["source"] = "keywords"
        return result

    try:
        answer = request_spec(text, runner=runner, timeout=timeout)
    except AIBridgeError as error:
        result["ai_error"] = str(error)
        result["spec"] = spec_module.normalize_spec(parsed["spec"])
        result["source"] = "keywords"
        return result

    result["spec"] = answer["spec"]
    result["source"] = "ai"
    result["raw"] = answer["raw"]
    return result
