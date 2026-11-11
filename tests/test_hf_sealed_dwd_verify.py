import hashlib
import json
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.hf_sealed_dwd_verify import compare, hashes, inventory


def sealed(root: Path):
    root.mkdir()
    data = root / "MATERIALIZATION.json"
    data.write_text("{}")
    body = {
        "kind": "quanthus_dwd_base_bundle_v1",
        "content": {"files": [{"path": data.name, "size_bytes": 2, "sha256": hashes(data)[0]}]},
    }
    (root / "manifest.json").write_text(json.dumps(body))
    sha = hashes(root / "manifest.json")[0]
    (root / "READY").write_text(sha + "\n")
    return sha


def test_root_metadata_is_part_of_exact_inventory(tmp_path):
    root = tmp_path / "sealed"
    sha = sealed(root)
    expected = inventory(root, sha)
    assert set(expected) == {"DWD/READY", "DWD/manifest.json", "DWD/MATERIALIZATION.json"}
    assert (
        expected["DWD/MATERIALIZATION.json"]["git_blob_sha1"]
        == hashlib.sha1(b"blob 2\0{}").hexdigest()
    )


def test_inventory_rejects_corrupt_or_extra_sources(tmp_path):
    root = tmp_path / "sealed"
    sha = sealed(root)
    (root / "extra").write_text("extra")
    with pytest.raises(ValueError, match="FILE_SET"):
        inventory(root, sha)
    (root / "extra").unlink()
    (root / "MATERIALIZATION.json").write_text("[]")
    with pytest.raises(ValueError, match="MEMBER_CHANGED"):
        inventory(root, sha)


def test_remote_exact_members_size_and_two_hash_protocols():
    expected = {"DWD/a": {"size": 2, "sha256": "sha256:abc", "git_blob_sha1": "def"}}
    lfs = {"DWD/a": {"size": 2, "lfs_sha256": "abc", "blob_id": "unused"}}
    assert compare(expected, lfs)["status"] == "VERIFIED"
    blob = {"DWD/a": {"size": 2, "lfs_sha256": None, "blob_id": "def"}}
    assert compare(expected, blob)["status"] == "VERIFIED"
    assert compare(expected, {**blob, "DWD/extra": blob["DWD/a"]})["status"] == "FAILED"
    absent = compare(expected, {})
    assert absent["status"] == "FAILED"
    assert absent["expected_files"] == 1
    assert absent["expected_bytes"] == 2
    assert absent["verified_files"] == absent["files"] == 0
    assert absent["verified_bytes"] == absent["bytes"] == 0
    assert (
        compare(expected, {"DWD/a": {**lfs["DWD/a"], "lfs_sha256": "wrong"}})["status"] == "FAILED"
    )
    assert compare(expected, {"DWD/a": {**blob["DWD/a"], "blob_id": "wrong"}})["status"] == "FAILED"
    assert compare(expected, {"DWD/a": {**blob["DWD/a"], "size": 3}})["status"] == "FAILED"
