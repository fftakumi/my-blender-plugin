import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from scripts.generate_index import build_index, build_index_entry

MANIFEST = {
    "schema_version": "1.0.0",
    "id": "my_blender_plugin",
    "version": "0.1.0",
    "name": "My Blender Plugin",
    "tagline": "Personal Blender add-on",
    "maintainer": "fftakumi",
    "type": "add-on",
    "blender_version_min": "4.2.0",
    "license": ["SPDX:GPL-3.0-or-later"],
    "tags": ["3D View"],
}


def test_build_index_entry():
    entry = build_index_entry(MANIFEST, "my_blender_plugin-0.1.0.zip", 1234, "abc123")
    assert entry["id"] == "my_blender_plugin"
    assert entry["version"] == "0.1.0"
    assert entry["archive_url"] == "./my_blender_plugin-0.1.0.zip"
    assert entry["archive_size"] == 1234
    assert entry["archive_hash"] == "sha256:abc123"
    assert entry["blender_version_min"] == "4.2.0"


def test_build_index_shape():
    index = build_index(MANIFEST, "a.zip", 1, "x")
    assert index["version"] == "v1"
    assert index["blocklist"] == []
    assert len(index["data"]) == 1


def test_cli_generates_valid_json(tmp_path):
    # ダミー zip を作って CLI 経由で index.json を生成し、サイズとハッシュを検証する
    zip_path = tmp_path / "my_blender_plugin-0.1.0.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("blender_manifest.toml", "dummy")
    out_path = tmp_path / "index.json"

    repo_root = Path(__file__).resolve().parent.parent
    subprocess.run(
        [sys.executable, "scripts/generate_index.py", str(zip_path), str(out_path)],
        cwd=repo_root,
        check=True,
    )

    index = json.loads(out_path.read_text(encoding="utf-8"))
    entry = index["data"][0]
    data = zip_path.read_bytes()
    assert entry["archive_size"] == len(data)
    assert entry["archive_hash"] == f"sha256:{hashlib.sha256(data).hexdigest()}"
    # 実際の blender_manifest.toml の内容が反映されている
    assert entry["id"] == "my_blender_plugin"
