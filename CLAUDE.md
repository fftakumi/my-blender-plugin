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
└── panels.py               # UIパネル定義(3Dビューポートのサイドバー「My Plugin」タブ)
scripts/build.sh            # 配布用 zip を dist/ に生成
```

従来のアドオン形式(Blender 3.0+、`bl_info`)とExtension形式(Blender 4.2+、`blender_manifest.toml`)の両対応を維持すること。

## コーディング規約

- オペレーターの追加: `operators.py` にクラスを追加し、ファイル末尾の `_classes` タプルに登録する
- パネルへのボタン追加: `panels.py` の `draw()` に `layout.operator(...)` を追加する
- 新規モジュール作成時: `__init__.py` の import・`importlib.reload(...)`・`_modules` の3箇所すべてに追加する(リロード対応を壊さない)
- クラス名は Blender の命名規則に従う: `MYPLUGIN_OT_xxx`(オペレーター)、`MYPLUGIN_PT_xxx`(パネル)
- `bl_idname` は `myplugin.xxx` 形式
- オペレーターには `bl_description`(ツールチップ)を必ず付け、`bl_options = {"REGISTER", "UNDO"}` を基本とする
- UI表示文字列・コメントは日本語でよい

## 検証

このリポジトリの実行環境にBlenderはない。最低限以下を行うこと。

```sh
python3 -m py_compile my_blender_plugin/*.py   # 構文チェック
./scripts/build.sh                              # zip 生成確認(dist/ はコミットしない)
```

バージョンを上げるときは `__init__.py` の `bl_info["version"]` と `blender_manifest.toml` の `version` を両方更新する。
