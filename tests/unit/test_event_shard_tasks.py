from __future__ import annotations

from pathlib import Path

from quanthus.event_library.shard_tasks import _release_file_cache


def test_shard_file_cache_release_handles_one_file_and_tree(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    first = root / "first.bin"
    second = root / "second.bin"
    first.write_bytes(b"a" * 4096)
    second.write_bytes(b"b" * 4096)

    _release_file_cache((first,), flush=True)
    _release_file_cache((root,), flush=False)

    assert first.read_bytes() == b"a" * 4096
    assert second.read_bytes() == b"b" * 4096
