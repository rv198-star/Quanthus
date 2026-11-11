from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.pipelines.hist_1d_release import verify_manifest, write_manifest


def test_member_readback_detects_corruption_and_omission(tmp_path: Path) -> None:
    data = tmp_path / "payload"
    data.mkdir()
    (data / "a").write_bytes(b"source bytes")
    write_manifest(tmp_path, {"revision": "a" * 40})
    assert verify_manifest(tmp_path)["members"] == 1
    (data / "a").write_bytes(b"changed bytes")
    with pytest.raises(ValueError, match="MEMBER_MISMATCH"):
        verify_manifest(tmp_path)
    write_manifest(tmp_path, {})
    (data / "orphan").write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="MEMBER_SET_MISMATCH"):
        verify_manifest(tmp_path)


@pytest.mark.parametrize("path", ["../outside", "/outside", "payload/../outside"])
def test_member_readback_rejects_path_escape(tmp_path: Path, path: str) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"members": [{"path": path}]}))
    with pytest.raises(ValueError, match="PATH_INVALID"):
        verify_manifest(tmp_path)


def test_manifest_rejects_symlink_members(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"a")
    (tmp_path / "b").symlink_to(tmp_path / "a")
    with pytest.raises(ValueError, match="SYMLINK_MEMBER"):
        write_manifest(tmp_path, {})
