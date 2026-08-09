# my-blender-plugin

自分用のBlenderプラグイン(アドオン)。

## 構成

```
my_blender_plugin/
├── __init__.py             # bl_info と register/unregister
├── blender_manifest.toml   # Blender 4.2+ Extension 用マニフェスト
├── operators.py            # オペレーター定義
└── panels.py               # UIパネル定義
scripts/
└── build.sh                # 配布用 zip を作るスクリプト
```

サンプルとして以下が入っています。

- `Hello` : インフォエリアにメッセージを出すだけの動作確認用オペレーター
- `Add Cube Grid` : キューブをグリッド状に並べるオペレーター(個数・間隔をUNDOパネルで調整可能)
- 3Dビューポートのサイドバー(`N`キー)に `My Plugin` タブとしてパネルを表示

## インストール

### zip からインストール

```sh
./scripts/build.sh   # dist/my_blender_plugin.zip ができる
```

- **Blender 4.2 以降**: `Edit > Preferences > Get Extensions > 右上の▼ > Install from Disk...` で zip を選択
- **従来のアドオンとして**: `Edit > Preferences > Add-ons > Install...` で zip を選択し、チェックを入れて有効化

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
