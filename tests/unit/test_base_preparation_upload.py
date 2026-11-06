import hashlib
import importlib.util
from pathlib import Path
from urllib.error import URLError

import pytest


@pytest.fixture
def client():
    path = Path(__file__).parents[2] / "scripts/upload_base_preparation_backup.py"
    spec = importlib.util.spec_from_file_location("base_preparation_upload", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lost_final_reply_resumes_from_server_offset_without_reupload(
    client, tmp_path, monkeypatch
):
    data = b"immutable archive bytes"
    source = tmp_path / "archive.zst"
    source.write_bytes(data)
    state = tmp_path / "private"
    state.mkdir()
    metadata = {
        "id": "archive-id",
        "name": source.name,
        "size": str(len(data)),
        "md5Checksum": hashlib.md5(data).hexdigest(),
        "sha256Checksum": hashlib.sha256(data).hexdigest(),
    }

    class FakeDrive:
        chunks = 0

        def children(self, parent):
            return []

        def call(self, url, method="GET", body=None, headers=None):
            if method == "POST":
                return 200, {"Location": "https://upload.example/session"}, None
            if method == "GET":
                return 200, {}, metadata
            if body:
                self.chunks += 1
                assert body == data
                raise URLError("server committed bytes, final reply was lost")
            if self.chunks:
                return 200, {}, metadata
            return 308, {}, None

    drive = FakeDrive()
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)
    result = client.upload(drive, "parent", source, state)
    assert drive.chunks == 1
    assert result["remote_sha256_verified"] is True
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert next(state.glob("*.json")).stat().st_mode & 0o077 == 0


def test_existing_remote_checksum_conflict_is_rejected_without_write(client, tmp_path):
    source = tmp_path / "archive.zst"
    source.write_bytes(b"immutable archive")

    class FakeDrive:
        def children(self, parent):
            return [
                {
                    "id": "existing",
                    "name": source.name,
                    "size": str(source.stat().st_size),
                    "md5Checksum": "0" * 32,
                }
            ]

        def call(self, *args, **kwargs):
            pytest.fail("an existing mismatched backup must remain untouched")

    with pytest.raises(ValueError, match="REMOTE_SIZE_OR_MD5_DIFFERS"):
        client.upload(FakeDrive(), "parent", source, tmp_path)
