# CLAUDE.md

このリポジトリは自分用のBlenderプラグイン(アドオン)です。

## 最重要方針: Blender標準機能で足りるものは実装しない

新しい機能を追加する前に、必ずBlender本体の標準機能・既存アドオンで実現できないかを確認すること。

- **Blenderの標準機能(オペレーター、モディファイア、ノード、標準同梱アドオン)で実現できることは、このプラグインには実装しない。** その場合は実装せず、Blenderでの操作手順(メニュー位置やオペレーター名)をユーザーに案内する
- 実装するのは「Blender標準では手が届かないこと」または「標準機能の組み合わせを大幅に省力化すること」だけ
- 実装する場合も、車輪の再発明をせず既存の `bpy.ops.*` やモディファイア、`bmesh` などのAPIを内部で呼び出して組み合わせる。自前でジオメトリ計算を書き直さない
- 例: 単純な配列複製 → Arrayモディファイアで足りるので実装しない。オブジェクト名の一括リネーム → Batch Rename (`Ctrl+F2`) で足りるので実装しない
- 迷ったら実装前にユーザーに「これはBlenderの◯◯で実現できますが、それでも実装しますか?」と確認する

## プロジェクト構成

```
my_blender_plugin/          # アドオン本体(このディレクトリをzip化して配布)
├── __init__.py             # bl_info と register/unregister、モジュールのリロード処理
├── blender_manifest.toml   # Blender 4.2+ Extension 用マニフェスト
├── operators.py            # オペレーター定義
├── panels.py               # UIパネル定義(3Dビューポートのサイドバー「My Plugin」タブ)
└── costume/                # 衣装生成(詳細は下の「衣装生成サブパッケージ」)
    ├── spec.py sizing.py modulate.py kernels.py parts.py validate.py   # bpy非依存
    ├── parse_text.py palette.py ai_bridge.py                           # bpy非依存
    ├── build.py materials.py                                           # bpy依存
    └── presets/*.json      # 同梱 spec(zip に入る位置に置く)
tests/
├── conftest.py             # フェイク bpy の注入(Blenderなしでテストを可能にする)
├── data/                   # ゴールデンファイル(再現性の回帰テスト用)
└── test_*.py               # pytest テスト
tools/                      # 開発用。**配布 zip には入らない**
├── precheck.py             # Blender なしで幾何検証(速い内側ループ)
├── generate.py             # headless で spec → .blend
└── verify.py               # headless で UV/マテリアル/レンダー検証
scripts/
├── build.sh                # 配布用 zip を dist/ に生成(Extension形式 + 従来形式)
└── generate_index.py       # Extension リモートリポジトリ用 index.json を生成
.github/workflows/
└── publish-extension.yml   # push ごとに zip + index.json を GitHub Pages へ公開
```

## 配布(リモートリポジトリ)

push すると GitHub Actions が Extension zip と index.json をビルドして GitHub Pages に公開する。
ユーザー(自分)は Blender に `https://fftakumi.github.io/my-blender-plugin/index.json` を
リモートリポジトリとして登録してインストール・更新する。

- 機能追加・修正を配信するときは `blender_manifest.toml` の `version` と
  `__init__.py` の `bl_info["version"]` を両方上げてから push する(上げ忘れるとBlender側で更新検知されない)
- index.json の生成ロジックは `scripts/generate_index.py`(テストは `tests/test_generate_index.py`)

従来のアドオン形式(Blender 3.0+、`bl_info`)とExtension形式(Blender 4.2+、`blender_manifest.toml`)の両対応を維持すること。

## コーディング規約

- オペレーターの追加: `operators.py` にクラスを追加し、ファイル末尾の `_classes` タプルに登録する
- パネルへのボタン追加: `panels.py` の `draw()` に `layout.operator(...)` を追加する
- 新規モジュール作成時: `__init__.py` の import・`importlib.reload(...)`・`_modules` の3箇所すべてに追加する(リロード対応を壊さない)
  - **サブパッケージは例外。** `costume` は `_modules` に入れない(`_modules` は `mod.register()` を呼ぶ契約で、
    `costume` に register は無いので `AttributeError` になる)。`costume/__init__.py` の `_MODULE_NAMES` に
    追加し、`reload_all()` が依存順に読み直す。`importlib.reload(package)` はサブモジュールを辿らない
- **bpy 依存モジュールはトップレベルで import しない。** `tests/conftest.py` のフェイク bpy には
  `bpy.data` / `bmesh` / `mathutils` が無いので、`build.py` や `materials.py` をモジュール先頭で読むと
  純粋関数のテストまで ImportError で落ちる。`execute()` の中で遅延 import する
  (前例: `operators.py` の `_coverage_weights` 内の `from mathutils.bvhtree import BVHTree`)
- クラス名は Blender の命名規則に従う: `MYPLUGIN_OT_xxx`(オペレーター)、`MYPLUGIN_PT_xxx`(パネル)
- `bl_idname` は `myplugin.xxx` 形式
- オペレーターには `bl_description`(ツールチップ)を必ず付け、`bl_options = {"REGISTER", "UNDO"}` を基本とする
- UI表示文字列・コメントは日本語でよい

## テスト(必須)

**機能を実装したら、必ずテストも実装すること。テストのないコードはコミットしない。**

この環境にBlender(`bpy`)はないため、テストしやすい構造にすることが前提になる。

- 座標計算・名前生成などのロジックは `bpy` に依存しない純粋関数として切り出し、オペレーターの `execute()` はその関数を呼ぶ薄いラッパーにする
- 純粋関数は pytest で直接テストする
- `bpy` に依存する部分をテストする場合は、`sys.modules["bpy"]` にフェイクモジュールを注入してからimportする(conftest.py で行う)
- テストは `tests/` ディレクトリに置き、`tests/test_<モジュール名>.py` という命名にする
- 既存の例: `operators.py` の `grid_positions()`(純粋関数)と `tests/test_operators.py`
- 実行方法:

```sh
uv run --with pytest python -m pytest tests/ -v
```

**`python` / `python3` を直接呼んではいけない。** この環境ではどちらも Windows の
App Execution Alias のスタブで、何も実行せず exit 49 を返す。`uv` を使う。
`zip` コマンドも無いので `build.sh` は Python の `zipfile` で zip を作っている。

## 検証

このリポジトリの実行環境にもBlenderはある(`C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`、
**5.1.2**)。ただし**ユーザーが作業中の可能性があるので、必ず別プロセスの `--background` で動かす**。
`--factory-startup` を付けること(ユーザーの Blender が MCP アドオンで動いており、
2つ目のインスタンスが同じポートを掴むのを避ける)。副作用としてこのアドオンも読み込まれないので、
`tools/*.py` は `sys.path` にリポジトリを挿して直接 import する。

コミット前に最低限以下を行う。

```sh
uv run --with pytest python -m pytest tests/ -v
```

```sh
uv run python tools/precheck.py --preset skirt_flare
```

```sh
./scripts/build.sh
```

Blender 実機での確認(生成 → 検証 + レンダー6枚):

```sh
"/c/Program Files/Blender Foundation/Blender 5.1/blender.exe" --background --factory-startup -noaudio --python-exit-code 1 --python tools/generate.py -- --preset skirt_flare --out /tmp/out.blend
```

`--python-exit-code 1` は必須。付けないとスクリプトが例外で落ちても終了コード0が返り、失敗を成功と誤認する。

バージョンを上げるときは `__init__.py` の `bl_info["version"]` と `blender_manifest.toml` の `version` を両方更新する
(`tests/test_addon.py` が突き合わせて検査する)。

## 衣装生成サブパッケージ(`my_blender_plugin/costume/`)

画像・説明文から**衣装単体**を生成する。素体にフィットさせない / VRM に合わせない。

- **戦略はパーツ分解。** 衣装 = 裁縫パーツの集合(spec の `parts`)。パーツごとに生成して
  パーツごとに検証するので、不合格になったときに原因のパーツとパラメータが特定できる。
  新しい衣装は「新しいコード」ではなく「新しいパーツの組み合わせ」で作る
- **自前で書くのはリング寸法の計算(`modulate.py`)とカーネル2つだけ**
  (`kernels.py` の K1 `loft_rings` = リング列のロフト、K2 `weld_meshes` = 複数ロフトの
  頂点共有溶接。K2 はパンツのような**分岐**のためにあり、K1 で表現できる形に使わない)。
  掃引(リボン・紐)は Curve + Bevel Object、回転体(ボタン)は Screw、シートの曲げは
  SimpleDeform、対称は Mirror、UV 以外の仕上げは標準モディファイアに投げる。
  **Geometry Nodes で全部書くのは却下** — ノードツリーはテキストで書けず bpy 必須なので
  pytest が書けなくなり、「テストのないコードはコミットしない」に反する
- **UV は解析的に決める**(`kernels.py`)。`bpy.ops.uv.smart_project` は EDIT モードを要求して
  headless で poll が落ちうるし、バージョン間で結果が変わるので既定では使わない
- **検証は2層。** 層1 `costume/validate.py` は bpy 非依存で pytest から直接呼べる
  (多様体性・境界ループ・退化面・巻き方向・自己交差・寸法・周期成分)。層2 `tools/verify.py` は
  Blender が要るもの(UV・マテリアル・モディファイア評価後・レンダー)だけを見る。
  層1を Blender の外に出しているので生成↔検証ループが速く回る
- **AI は入力の解釈だけ。** 既定は `parse_text.py` のキーワード辞書(AI 呼び出しゼロ)。
  形の手がかりが見つからなかったときだけ `ai_bridge.py` → `claude -p` に spec JSON を作らせ、
  **必ず `spec.normalize_spec()` を通してから使う**。落ちたら捨てて辞書の結果で続行する。
  画像の色は `palette.py`(median cut・決定的)で抽出して AI に投げない。
  画像から取るのは**色だけ**で、質感(roughness/sheen)は説明文の指定を残す。
  bpy 側の読み込み・縮小は `image_input.py`、spec への載せ方は `palette.apply_to_spec`(純粋関数)。
  **画像から形を読む経路はまだ無い**(AI 前提なので次のイテレーション)
- 数値の根拠は character-modeling スキルの実測値(VRM 10体)。**測っていない値を実測として書かない**。
  設計値なら設計値と明記する(`sizing.py` の冒頭が例)
