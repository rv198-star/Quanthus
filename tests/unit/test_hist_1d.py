from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from quanthus.pipelines import hist_1d
from quanthus.pipelines.hist_1d import extract_members, recovery_selection, safe_destination


def _member(year: int, interval: str = "1d") -> str:
    return (
        f"DWD/payload/dwd_security_bar/interval_size={interval}/"
        f"observation_start_year={year}/observation_start_month=1/part.parquet"
    )


def test_selection_excludes_minutes_future_and_nested_evidence() -> None:
    names = [
        _member(1990),
        _member(2019),
        _member(2021),
        _member(2022),
        _member(2000, "5m"),
        "DWD/evidence/old/payload/dwd_security_bar/part.parquet",
    ]
    index = {"archive_parts": [{"name": "a.tar.zst", "members": [[n, 1, "a"] for n in names]}]}
    assert [m[0] for m in recovery_selection(index)[0]["members"]] == names[:3]


def test_duplicate_source_member_rejected() -> None:
    member = [_member(2019), 1, "a"]
    with pytest.raises(ValueError, match="DUPLICATE_SOURCE_MEMBER"):
        recovery_selection({"archive_parts": [{"members": [member, member]}]})


@pytest.mark.parametrize("name", ["/etc/passwd", "DWD/payload/../../escape", "ODS/payload/a"])
def test_unsafe_member_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="UNSAFE_ARCHIVE_PATH"):
        safe_destination(tmp_path, name)


def _tar(name: str, raw: bytes, link: bool = False) -> io.BytesIO:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        info = tarfile.TarInfo(name)
        if link:
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
        else:
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    output.seek(0)
    return output


def test_extraction_hashes_and_idempotent_resume(tmp_path: Path) -> None:
    name, raw = _member(2019), b"raw parquet stand-in"
    members = [[name, len(raw), hashlib.sha256(raw).hexdigest()]]
    for _ in range(2):
        with tarfile.open(fileobj=_tar(name, raw), mode="r|") as archive:
            assert extract_members(archive, members, tmp_path) == 1
    assert (tmp_path / name).read_bytes() == raw
    (tmp_path / name).write_bytes(b"corrupt")
    with (
        tarfile.open(fileobj=_tar(name, raw), mode="r|") as archive,
        pytest.raises(ValueError, match="EXISTING_RECOVERY_MEMBER_DIFFERS"),
    ):
        extract_members(archive, members, tmp_path)


def test_source_hash_failure_and_links_are_rejected(tmp_path: Path) -> None:
    name, raw = _member(2019), b"value"
    with (
        tarfile.open(fileobj=_tar(name, raw), mode="r|") as archive,
        pytest.raises(ValueError, match="ARCHIVE_MEMBER_HASH_MISMATCH"),
    ):
        extract_members(archive, [[name, len(raw), "0" * 64]], tmp_path)
    with (
        tarfile.open(fileobj=_tar(name, raw, link=True), mode="r|") as archive,
        pytest.raises(ValueError, match="INVALID_OR_DUPLICATE_TAR_MEMBER"),
    ):
        extract_members(archive, [[name, 0, "0" * 64]], tmp_path)


def test_missing_member_rejected(tmp_path: Path) -> None:
    with (
        tarfile.open(fileobj=_tar(_member(2020), b"a"), mode="r|") as archive,
        pytest.raises(ValueError, match="ARCHIVE_MEMBERS_MISSING"),
    ):
        extract_members(archive, [[_member(2019), 1, "a"]], tmp_path)


@pytest.mark.parametrize("partial", [b"raw", b"raw parquet"])
def test_member_partial_is_verified_or_reextracted(tmp_path: Path, partial: bytes) -> None:
    name, raw = _member(2019), b"raw parquet"
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.with_name(path.name + ".partial").write_bytes(partial)
    with tarfile.open(fileobj=_tar(name, raw), mode="r|") as archive:
        extract_members(archive, [[name, len(raw), hashlib.sha256(raw).hexdigest()]], tmp_path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("offset", [0, 3, 11])
def test_archive_partial_empty_range_and_complete(tmp_path: Path, monkeypatch, offset: int) -> None:
    raw = b"raw archive"
    part = {
        "name": "a.tar.zst",
        "archive_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "file_id": "fake",
    }
    (tmp_path / "a.tar.zst.partial").write_bytes(raw[:offset])

    class Response(io.BytesIO):
        status = 206 if offset else 200

    class Opener:
        def open(self, req, timeout):
            assert offset < len(raw), "complete partial must avoid network"
            assert req.get_header("Range") == (f"bytes={offset}-" if offset else None)
            return Response(raw[offset:])

    monkeypatch.setattr(hist_1d, "_opener", lambda proxy: Opener())
    assert hist_1d.download_archive(part, tmp_path, "not-a-real-token", None).read_bytes() == raw
