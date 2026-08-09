# my-blender-plugin

自分用のBlenderプラグイン(アドオン)。

## 構成

```
my_blender_plugin/
├── __init__.py             # bl_info と register/unregister
├── blender_manifest.toml   # Blender 4.2+ Extension 用マニフェスト
├── operators.py            # オペレーター定義
└── panels.py               # UIパネル定義
tests/                      # pytest テスト(フェイク bpy で Blender なしで実行可能)
scripts/
└── build.sh                # 配布用 zip を作るスクリプト
```

## テスト

```sh
pip install pytest        # 初回のみ
python3 -m pytest tests/ -v
```

サンプルとして以下が入っています。

- `Hello` : インフォエリアにメッセージを出すだけの動作確認用オペレーター
- `Add Cube Grid` : キューブをグリッド状に並べるオペレーター(個数・間隔をUNDOパネルで調整可能)
- 3Dビューポートのサイドバー(`N`キー)に `My Plugin` タブとしてパネルを表示

## インストール

### 方法1: リモートリポジトリとして登録(Blender 4.2 以降・推奨)

GitHub Actions が push のたびに Extension リポジトリ(zip + index.json)をビルドして GitHub Pages に公開しています。
BlenderにURLを一度登録すれば、以後は Blender の UI から インストール/アップデート できます。

1. `Edit > Preferences > Get Extensions` を開く
2. 右上の `Repositories` ドロップダウン > `+` > `Add Remote Repository...`
3. URL に以下を入力して追加:

   ```
   https://fftakumi.github.io/my-blender-plugin/index.json
   ```

4. 拡張機能一覧に `My Blender Plugin` が出るので `Install` を押す

更新を配信したいときは、`blender_manifest.toml` と `__init__.py` の `bl_info` のバージョンを上げて push するだけです。
Blender 側では `Repositories > Check for Updates` で新バージョンが表示されます。

### 方法2: zip からインストール

```sh
./scripts/build.sh
```

- **Blender 4.2 以降**: `Get Extensions > 右上の▼ > Install from Disk...` で `dist/my_blender_plugin-<version>.zip` を選択
- **従来のアドオンとして (Blender 3.x)**: `Edit > Preferences > Add-ons > Install...` で `dist/my_blender_plugin-legacy.zip` を選択し、チェックを入れて有効化

### 開発中はシンボリックリンクが楽

Blender のアドオンディレクトリにリポジトリ内の `my_blender_plugin/` をリンクしておくと、
編集内容が `F3 > Reload Scripts` で反映されます。

```sh
# Linux の例(バージョンは自分の環境に合わせる)
ln -s "$(pwd)/my_blender_plugin" ~/.config/blender/4.2/scripts/addons/my_blender_plugin

# macOS の例
ln -s "$(pwd)/my_blender_plugin" ~/Library/Application\ Support/Blender/4.2/scripts/addons/my_blender_plugin
```

Windows の場合は `%APPDATA%\Blender Foundation\Blender\4.2\scripts\addons\` にジャンクションを作成します。

```bat
mklink /J "%APPDATA%\Blender Foundation\Blender\4.2\scripts\addons\my_blender_plugin" "C:\path\to\repo\my_blender_plugin"
```

## 開発メモ

- オペレーターを追加するときは `operators.py` にクラスを追加し、`_classes` に登録する
- パネルにボタンを追加するときは `panels.py` の `draw()` に `layout.operator(...)` を追加する
- 新しいモジュールを作ったら `__init__.py` の import と `_modules` に追加する(リロード対応の `importlib.reload` も忘れずに)

## ライセンス

Blender アドオンは Blender 本体(GPL)とリンクするため GPL-3.0-or-later としています。
