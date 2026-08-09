#!/usr/bin/env python3
"""Blender Extensions リモートリポジトリ用の index.json を生成する。

使い方:
    python3 scripts/generate_index.py <extension_zip> <output_index_json>

blender_manifest.toml の内容と zip のサイズ・sha256 ハッシュから
Blender が「リモートリポジトリ」として読める index.json を作る。
"""

import hashlib
import json
import sys
import tomllib
from pathlib import Path

WEBSITE = "https://github.com/fftakumi/my-blender-plugin"


def build_index_entry(manifest, archive_name, archive_size, archive_sha256):
    """manifest(dict) と zip の情報から index.json の data エントリを作る純粋関数"""
    entry = {
        "schema_version": manifest["schema_version"],
        "id": manifest["id"],
        "name": manifest["name"],
        "version": manifest["version"],
        "tagline": manifest.get("tagline", ""),
        "type": manifest.get("type", "add-on"),
        "maintainer": manifest.get("maintainer", ""),
        "license": manifest.get("license", []),
        "blender_version_min": manifest.get("blender_version_min", "4.2.0"),
        "tags": manifest.get("tags", []),
        "website": WEBSITE,
        "archive_url": f"./{archive_name}",
        "archive_size": archive_size,
        "archive_hash": f"sha256:{archive_sha256}",
    }
    return entry


def build_index(manifest, archive_name, archive_size, archive_sha256):
    return {
        "version": "v1",
        "blocklist": [],
        "data": [build_index_entry(manifest, archive_name, archive_size, archive_sha256)],
    }


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 1

    zip_path = Path(argv[1])
    out_path = Path(argv[2])
    manifest_path = Path(__file__).resolve().parent.parent / "my_blender_plugin" / "blender_manifest.toml"

    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    data = zip_path.read_bytes()
    index = build_index(
        manifest,
        archive_name=zip_path.name,
        archive_size=len(data),
        archive_sha256=hashlib.sha256(data).hexdigest(),
    )

    out_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"created: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
