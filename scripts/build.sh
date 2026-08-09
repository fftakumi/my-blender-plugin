#!/usr/bin/env bash
# 配布用の zip を dist/ に作る。
#
# - dist/my_blender_plugin-<version>.zip : Extension 形式(マニフェストが zip 直下)。
#   Blender 4.2+ の Get Extensions / リモートリポジトリ用。
# - dist/my_blender_plugin-legacy.zip    : 従来のアドオン形式(パッケージディレクトリごと)。
#   Blender 3.x の Edit > Preferences > Add-ons > Install... 用。
set -euo pipefail

cd "$(dirname "$0")/.."

# tomllib.load はバイナリで読むので、日本語を含むマニフェストでも
# ロケール既定のエンコーディング(Windows 日本語環境では cp932)に左右されない
VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('my_blender_plugin/blender_manifest.toml','rb'))['version'])")

rm -rf dist
mkdir -p dist

# Extension 形式: my_blender_plugin/ の中身を zip 直下に置く
(cd my_blender_plugin && zip -r "../dist/my_blender_plugin-${VERSION}.zip" . \
  -x "__pycache__/*" -x "*.pyc")

# 従来形式: パッケージディレクトリごと zip にする
zip -r dist/my_blender_plugin-legacy.zip my_blender_plugin \
  -x "*/__pycache__/*" -x "*.pyc"

echo "created: dist/my_blender_plugin-${VERSION}.zip (Blender 4.2+ Extension)"
echo "created: dist/my_blender_plugin-legacy.zip (legacy add-on)"
