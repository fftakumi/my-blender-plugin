#!/usr/bin/env bash
# 配布用の zip (dist/my_blender_plugin.zip) を作る。
# Blender 4.2+ の Extension としても、従来のアドオンとしてもインストール可能。
set -euo pipefail

cd "$(dirname "$0")/.."

rm -rf dist
mkdir -p dist

zip -r dist/my_blender_plugin.zip my_blender_plugin \
  -x "*/__pycache__/*" -x "*.pyc"

echo "created: dist/my_blender_plugin.zip"
