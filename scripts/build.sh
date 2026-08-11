#!/usr/bin/env bash
# 配布用の zip を dist/ に作る。
#
# - dist/my_blender_plugin-<version>.zip : Extension 形式(マニフェストが zip 直下)。
#   Blender 4.2+ の Get Extensions / リモートリポジトリ用。
# - dist/my_blender_plugin-legacy.zip    : 従来のアドオン形式(パッケージディレクトリごと)。
#   Blender 3.x の Edit > Preferences > Add-ons > Install... 用。
#
# Python は uv 経由で呼ぶ。Windows の `python` / `python3` は App Execution Alias の
# スタブで何も実行せず exit 49 を返すため、直接呼ぶと必ず失敗する。
# zip も外部コマンドに頼らず Python の zipfile で作る(Git Bash に zip が無い環境がある)。
set -euo pipefail

cd "$(dirname "$0")/.."

if command -v uv >/dev/null 2>&1; then
  PY=(uv run --quiet python)
elif command -v python3 >/dev/null 2>&1 && python3 -c "" >/dev/null 2>&1; then
  PY=(python3)
elif command -v python >/dev/null 2>&1 && python -c "" >/dev/null 2>&1; then
  PY=(python)
else
  echo "error: 使える Python がありません。uv を入れてください" >&2
  exit 1
fi

rm -rf dist
mkdir -p dist

"${PY[@]}" - <<'PY'
import os
import tomllib
import zipfile

PACKAGE = "my_blender_plugin"
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = (".pyc", ".pyo")

with open(os.path.join(PACKAGE, "blender_manifest.toml"), "rb") as handle:
    # バイナリで読むので、日本語を含むマニフェストでもロケール既定の
    # エンコーディング(Windows 日本語環境では cp932)に左右されない
    version = tomllib.load(handle)["version"]


def collect():
    for root, dirs, files in os.walk(PACKAGE):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(SKIP_SUFFIXES):
                continue
            yield os.path.join(root, name)


paths = list(collect())

# Extension 形式: my_blender_plugin/ の中身を zip 直下に置く
extension = os.path.join("dist", "my_blender_plugin-%s.zip" % version)
with zipfile.ZipFile(extension, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in paths:
        archive.write(path, os.path.relpath(path, PACKAGE).replace(os.sep, "/"))

# 従来形式: パッケージディレクトリごと zip にする
legacy = os.path.join("dist", "my_blender_plugin-legacy.zip")
with zipfile.ZipFile(legacy, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in paths:
        archive.write(path, path.replace(os.sep, "/"))

print("created: %s (Blender 4.2+ Extension) %d files" % (extension, len(paths)))
print("created: %s (legacy add-on)" % legacy)
PY
