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

from . import parse_text, parts as parts_module, spec as spec_module, validate as validate_module

#: `claude -p` の応答を待つ上限(秒)。UI スレッドから呼ぶと固まるので必須
DEFAULT_TIMEOUT = 120

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class AIBridgeError(RuntimeError):
    """AI の呼び出しか応答の解釈に失敗したときに投げる"""


#: few-shot として**完全な生 JSON** を載せるプリセット。増やすとプロンプトが
#: 線形に伸びるので「筒もの」と「パーツ数最大」の2例に固定し、残りのプリセットは
#: 1行カタログに落とす
PROMPT_EXAMPLE_PRESETS = ("skirt_flare", "blouse")

_EXAMPLE_CAPTIONS = {
    "skirt_flare": "スカート = waistband + skirt_body(shared joint で接合)",
    "blouse": "ブラウス = bodice + sleeve×2 + collar + collar_fall + placket + buttons",
}


def _raw_preset(name):
    """同梱プリセットの生 JSON(normalize 前)を返す。

    few-shot には normalize 済みではなく**書かれたままの形**を見せる。
    normalize 済みは全既定値が埋まっていて「説明に無いことは省略せよ」の
    指示と矛盾するため。
    """
    path = os.path.join(spec_module.PRESET_DIR, name + ".json")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _preset_catalog_lines():
    """同梱プリセットの1行カタログ(名前: パーツ構成)"""
    lines = []
    for name in spec_module.list_presets():
        raw = _raw_preset(name)
        types = [part.get("type", "?") for part in raw.get("parts", [])]
        lines.append("  %s: %s" % (name, " + ".join(types)))
    return lines


def build_prompt(text, hint=None):
    """設計手順と spec スキーマを添えたプロンプトを組み立てる。

    いきなり完成 JSON を書かせず、「似ているプリセット → 差分 → 構成パーツの
    リスト → spec」の順に考えさせる(このプラグインのパーツ分解戦略を
    そのまま AI の思考手順にする)。差分のうち今のパーツで表現できないものは
    unsupported として spec の外に出させ、ユーザーへの注記に使う。
    """
    lines = [
        "あなたは Blender 用の衣装生成プラグインの入力解釈器です。",
        "次の衣装の説明を読み、裁縫パーツの組み合わせ(spec)として設計してください。",
        "",
        "--- 衣装の説明 ---",
        text.strip(),
        "--- 説明おわり ---",
        "",
        "手順(この順に考えること):",
        "1. 説明の衣装に一番近い同梱プリセットを1つ選び similar_preset に入れる",
        "2. 説明の衣装とそのプリセットの違いを differences に列挙する",
        "3. 違いのうち、下記のパーツでは表現できないものは unsupported に分け、"
        "spec に無理に入れない",
        "4. 衣装を構成する裁縫パーツを parts_plan に1行ずつ挙げ、"
        "それぞれの寸法・形のパラメータを決める",
        "5. その結果を spec に落とす(スキーマは後述)。"
        "説明に無いことは推測せず省略する(既定値が入る)",
        "",
        "出力は次の形の JSON 1個だけ。JSON 以外の文字を出力してはいけません:",
        '{"similar_preset": "<プリセット名>", "differences": ["<差分>", ...],',
        ' "unsupported": ["<表現できない要素>", ...], "parts_plan": ["<パーツと役割>", ...],',
        ' "spec": {<衣装 spec>}}',
        "",
        "同梱プリセット一覧:",
    ]
    lines += _preset_catalog_lines()
    lines.append("")
    lines.append("プリセットの実例:")
    for name in PROMPT_EXAMPLE_PRESETS:
        lines.append("# " + _EXAMPLE_CAPTIONS.get(name, name))
        lines.append(json.dumps(_raw_preset(name), ensure_ascii=False, separators=(",", ":")))
    lines.append("")
    lines.append(hint or spec_module.json_schema_hint())
    return "\n".join(lines)


def retry_prompt(base_prompt, previous_output, error):
    """検証に落ちた前回の出力と理由を添えて、作り直しを頼むプロンプト。

    `claude -p` は毎回まっさらなので、元のプロンプト一式も含める。
    """
    return "\n".join(
        [
            base_prompt,
            "",
            "--- 前回のあなたの出力(不採用) ---",
            (previous_output or "").strip()[:2000],
            "--- 不採用の理由 ---",
            str(error),
            "",
            "理由をすべて解消して、同じ封筒型 JSON を完全な形で出力し直してください。",
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


#: `claude -p` に付ける制限フラグ。**JSON を書かせるだけなのでツールは全部落とす。**
#:
#: プロンプトには衣装の説明文がそのまま入る。ユーザーの説明文が信頼できても、
#: 画像やファイル由来のテキストが混ざる経路がある以上、**説明文はデータであって
#: 指示ではない**。何も付けずに起動すると、呼び出された claude は
#: ユーザー設定で事前許可済みのツールと cwd の CLAUDE.md を引き継ぐので、
#: 説明文経由のプロンプトインジェクションで許可済みツールが走り得る。
#: 出力側は必ず normalize_spec() を通すが、それは**実行中の副作用**を防がない。
SANDBOX_FLAGS = (
    "--allowedTools",
    "",  # 何も許可しない
    "--permission-mode",
    "default",  # 事前許可の bypass を継承しない
    "--setting-sources",
    "",  # ユーザー設定・プロジェクト設定・CLAUDE.md を読み込ませない
)


def default_runner(prompt, timeout=DEFAULT_TIMEOUT, command=None, flags=SANDBOX_FLAGS):
    """`claude -p` を subprocess で呼ぶ。

    Windows の `claude` は .cmd シムなので shutil.which で実体を解決する。
    出力の復号は cp932 に落ちないよう utf-8 を明示する。
    引数はリストで渡すのでシェルは介在しない(インジェクションの経路にならない)。
    ツールを落とす理由は SANDBOX_FLAGS を見ること。
    """
    executable = command or shutil.which("claude")
    if not executable:
        raise AIBridgeError(
            "claude コマンドが見つかりません。PATH を確認するか runner を渡してください"
        )
    try:
        completed = subprocess.run(
            [executable, *flags, "-p", prompt],
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


def split_envelope(payload):
    """AI の出力から (spec, 封筒メタ) を取り出す。

    封筒型 {"similar_preset", "differences", "unsupported", "parts_plan", "spec"}
    と、素の spec(旧形式・封筒を忘れた応答)の両方を受け付ける。
    """
    if isinstance(payload.get("spec"), dict):
        meta = {
            key: payload.get(key)
            for key in ("similar_preset", "differences", "unsupported", "parts_plan")
        }
        return payload["spec"], meta
    return payload, {}


def _prebuild_check(normalized):
    """採用前に bpy 非依存の生成と検証を通す。

    normalize_spec は sizing の中身などを検査しない(sizing.py の責務)ので、
    ここを飛ばすと「正規化は通るのに実際は組めない spec」を operator まで
    運んでしまい、フォールバックできる場所を過ぎてから CANCELLED になる。
    build_all / costume_report は bpy 非依存で軽い。
    """
    try:
        built = parts_module.build_all(normalized)
    except Exception as error:  # AI 由来の spec は何で落ちるか分からない
        raise AIBridgeError("spec から組み立てられませんでした: %s" % error) from error
    report = validate_module.costume_report(built, normalized)
    if report["failed"]:
        raise AIBridgeError("生成物が検証に落ちました: %s" % ", ".join(report["failed"]))


def request_spec(text, runner=None, timeout=DEFAULT_TIMEOUT, max_attempts=2):
    """AI に spec を作らせ、**正規化とビルド前検証を通ったものだけ**返す。

    応答の質が悪かったとき(JSON が無い・検証落ち・組み立て不能)は、失敗理由と
    前回の出力を添えて max_attempts 回まで作り直させる。normalize_spec の
    エラーメッセージは修正指示としてそのまま使える具体性がある。
    runner 自体の失敗(CLI 不在・タイムアウト)は環境の問題なのでリトライしない。

    戻り値: {"spec": 正規化済み spec, "raw": AIの生JSON, "prompt": 最後に送った文,
             "attempts": 試行回数, "envelope": 封筒メタ}
    だめなら AIBridgeError(.envelope に読めた範囲の封筒メタが付く)。
    """
    call = runner or (lambda value: default_runner(value, timeout=timeout))
    base_prompt = build_prompt(text)
    prompt = base_prompt
    last_error = None
    last_envelope = {}
    for attempt in range(1, max_attempts + 1):
        output = call(prompt)  # runner の例外はそのまま伝播(リトライしない)
        try:
            payload = extract_json(output)
            raw, envelope = split_envelope(payload)
            last_envelope = envelope or last_envelope
            normalized = spec_module.normalize_spec(raw)
            _prebuild_check(normalized)
        except (AIBridgeError, spec_module.SpecError) as error:
            last_error = error
            prompt = retry_prompt(base_prompt, output, error)
            continue
        return {
            "spec": normalized,
            "raw": payload,
            "prompt": prompt,
            "attempts": attempt,
            "envelope": envelope,
        }
    failure = AIBridgeError(
        "AI が返した spec が検証に落ちました(%d回試行): %s" % (max_attempts, last_error)
    )
    failure.envelope = last_envelope
    raise failure


def fallback_warning(text, parse_result, ai_error, used_preset):
    """AI に頼れず辞書結果で続行したときのユーザー向け警告文(純粋関数)。

    衣装の種類が辞書で特定できていた場合は None(その場合のフォールバックは
    妥当な形なので、notes と ai_error の表示で足りる)。特定できていない
    場合だけ「何で代用したか」をはっきり言う(黙ってスカートを出さない)。
    """
    if parse_result["confidence"] > 0.0:
        return None
    summary = text.strip() or "(空の説明文)"
    if len(summary) > 40:
        summary = summary[:40] + "…"
    return (
        "『%s』は衣装の種類を解釈できなかったため、プリセット %s で代用しました(AI: %s)"
        % (summary, used_preset, ai_error)
    )


def spec_from_text(text, runner=None, timeout=DEFAULT_TIMEOUT):
    """説明文 → 正規化済み spec。**AI が解釈の主役**で、辞書はフォールバック。

    キーワード辞書は語彙を列挙する方式なので多様性に限界がある。runner が
    使える限り常に AI に解釈させ、AI が失敗したとき(オフ設定・CLI 不在・
    検証落ち)だけフォールバックする。フォールバック先は
    AI が挙げた類似プリセット → 辞書が選んだプリセット の順で選ぶ。

    戻り値: {"spec", "source": "keywords"|"ai", "parse": 辞書解析の結果,
             "ai_error": AI 失敗の理由 or None,
             "explicit_height": 説明文に明記された身長 or None,
             "fallback_warning": ユーザー向け警告文 or None,
             "unsupported": AI が「表現できない」と申告した要素のリスト}
    ai 採用時はさらに {"raw", "attempts"}、keywords 時は {"base_preset"}。
    """
    parsed = parse_text.parse(text)
    result = {
        "parse": parsed,
        "ai_error": None,
        # AI も assumed_height を埋めてくる(スキーマに見えている以上、指定が
        # 無くても書いてくる)ので、「説明文に本当に書いてあった身長」は
        # 辞書の正規表現マッチだけを信号にする
        "explicit_height": (
            parsed["spec"]["assumed_height"] if "height" in parsed["matched"] else None
        ),
        "fallback_warning": None,
        "unsupported": [],
    }

    try:
        answer = request_spec(text, runner=runner, timeout=timeout)
    except AIBridgeError as error:
        result["ai_error"] = str(error)
        envelope = getattr(error, "envelope", None) or {}
        similar = envelope.get("similar_preset")
        chosen = parsed
        used_preset = parsed["base_preset"]
        if (
            parsed["confidence"] == 0.0
            and isinstance(similar, str)
            and similar != used_preset
            and similar in spec_module.list_presets()
        ):
            # 辞書は種類を特定できなかったが、AI は「何に似ているか」までは
            # 答えられている。フォールバックの土台をそちらへ寄せる
            chosen = parse_text.parse(text, base_preset=similar)
            used_preset = similar
        result["spec"] = spec_module.normalize_spec(chosen["spec"])
        result["source"] = "keywords"
        result["base_preset"] = used_preset
        result["fallback_warning"] = fallback_warning(
            text, parsed, str(error), used_preset
        )
        return result

    result["spec"] = answer["spec"]
    result["source"] = "ai"
    result["raw"] = answer["raw"]
    result["attempts"] = answer["attempts"]
    unsupported = answer["envelope"].get("unsupported")
    if isinstance(unsupported, list):
        result["unsupported"] = [str(item) for item in unsupported if item]
    return result
