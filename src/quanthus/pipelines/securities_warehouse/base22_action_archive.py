"""Exact retained response bytes in one bounded sequential authority archive."""

from __future__ import annotations

import hashlib
import io
import re
import tarfile
from collections.abc import Iterator, Mapping
from pathlib import Path

from .base22_bounded import BoundedError

CONTRACT_ID = "p10-base22-retained-authority-response-bytes-v1"
MAX_BYTES = 1024 * 1024
MAX_MEMBERS = 8192


def add_response(archive: tarfile.TarFile, name: str, data: bytes, sha256: str) -> None:
    """Only actual original bytes; TAR metadata never supplies a source clock."""
    if (
        not re.fullmatch(r"em_raw/\d{6}\.json", name)
        or not 0 < len(data) <= MAX_BYTES
        or "sha256:" + hashlib.sha256(data).hexdigest() != sha256
    ):
        raise BoundedError("ACTION_ARCHIVE_ORIGINAL_MEMBER_INVALID")
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o400
    info.pax_headers = {"original_sha256": sha256, "contract_id": CONTRACT_ID}
    archive.addfile(info, io.BytesIO(data))


def response_bytes(
    path: Path, expected: Mapping[str, tuple[str, int]]
) -> Iterator[tuple[str, str, bytes]]:
    """Read without extraction; exact frozen member set/hash/size is mandatory."""
    if not 0 < len(expected) <= MAX_MEMBERS:
        raise BoundedError("ACTION_ARCHIVE_EXPECTED_SET_INVALID")
    seen = set()
    with tarfile.open(path, mode="r|") as archive:
        for item in archive:
            if (
                not item.isreg()
                or item.name in seen
                or item.name not in expected
                or not re.fullmatch(r"em_raw/\d{6}\.json", item.name)
                or not 0 < item.size <= MAX_BYTES
            ):
                raise BoundedError("ACTION_ARCHIVE_PHYSICAL_MEMBER_INVALID")
            sha, size = expected[item.name]
            if (
                item.size != size
                or item.pax_headers.get("original_sha256") != sha
                or item.pax_headers.get("contract_id") != CONTRACT_ID
            ):
                raise BoundedError("ACTION_ARCHIVE_MEMBER_IDENTITY_MISMATCH")
            stream = archive.extractfile(item)
            if stream is None:
                raise BoundedError("ACTION_ARCHIVE_MEMBER_UNREADABLE")
            with stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) != size or "sha256:" + hashlib.sha256(data).hexdigest() != sha:
                raise BoundedError("ACTION_ARCHIVE_MEMBER_BYTES_CHANGED")
            seen.add(item.name)
            yield item.name, sha, data
    if seen != set(expected):
        raise BoundedError("ACTION_ARCHIVE_EXACT_SET_INCOMPLETE")
