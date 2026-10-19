"""Reuse pinned ODS seal metadata for transport, without claiming a byte audit.

The copy stage must hash every source byte against this inventory. Independent
cold audits remain mandatory; this only removes an identical pre-copy hash pass.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha
from .base22_sources import _fingerprint, _member, _tree, physical_path
from .base24_ods_image import KIND, Base24OdsError


def prepare_sealed_copy_inventory(
    root: Path, *, expected_manifest_sha256: str
) -> dict[str, dict[str, Any]]:
    """Prepare pinned expectations; every copy and cold audit still reads bytes."""
    root = physical_path(root)
    raw = _member(root, "manifest.json").read_bytes()
    manifest_sha = "sha256:" + hashlib.sha256(raw).hexdigest()
    if manifest_sha != expected_manifest_sha256 or (
        _member(root, "READY").read_text(encoding="ascii").strip() != manifest_sha
    ):
        raise Base24OdsError("BASE24_ODS_TRANSPORT_SEAL_CHANGED")
    manifest = json.loads(raw)
    content = manifest.get("content", {})
    if manifest.get("kind") != KIND or digest(content) != manifest.get("content_digest"):
        raise Base24OdsError("BASE24_ODS_TRANSPORT_SEAL_CHANGED")
    rows = content.get("files", [])
    declared: dict[str, dict[str, Any]] = {}
    for row in rows:
        relative = row["path"]
        _member(root, relative)
        if relative in declared or relative in {"manifest.json", "READY"}:
            raise Base24OdsError("BASE24_ODS_TRANSPORT_FILE_SET_CHANGED")
        if (
            type(row.get("size_bytes")) is not int
            or row["size_bytes"] < 0
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("sha256"))) is None
        ):
            raise Base24OdsError("BASE24_ODS_TRANSPORT_MEMBER_INVALID")
        declared[relative] = {"size_bytes": row["size_bytes"], "sha256": row["sha256"]}
    evidence = declared.get("evidence/MATERIALIZATION.json")
    if evidence is None or file_sha(root / "evidence/MATERIALIZATION.json") != evidence["sha256"]:
        raise Base24OdsError("BASE24_ODS_TRANSPORT_EVIDENCE_CHANGED")
    for relative, expected in (
        ("manifest.json", manifest_sha),
        ("READY", file_sha(root / "READY")),
    ):
        declared[relative] = {"size_bytes": (root / relative).stat().st_size, "sha256": expected}
    if _tree(root) != set(declared):
        raise Base24OdsError("BASE24_ODS_TRANSPORT_FILE_SET_CHANGED")
    for relative, item in declared.items():
        path = _member(root, relative)
        dev, ino, size, mtime, ctime = _fingerprint(path)
        if size != item["size_bytes"] or path.stat().st_nlink != 1:
            raise Base24OdsError("BASE24_ODS_TRANSPORT_MEMBER_INVALID")
        item.update(device=dev, inode=ino, mtime_ns=mtime, ctime_ns=ctime)
    return declared


def physical_metadata(root: Path) -> dict[str, tuple[int, int, int, int, int]]:
    """Snapshot the exact independent file set without re-reading payload bytes."""
    root = physical_path(root)
    result = {}
    for relative in sorted(_tree(root)):
        path = _member(root, relative)
        if path.stat().st_nlink != 1:
            raise Base24OdsError("BASE24_ODS_TRANSPORT_MEMBER_INVALID")
        result[relative] = _fingerprint(path)
    return result


def verify_restored_fault_copy(
    root: Path,
    *,
    before: dict[str, tuple[int, int, int, int, int]],
    canary: str,
    expected_sha256: str,
) -> None:
    """After read-only negative workers, re-read only the restored canary.

    This requires a successful whole-image cold audit before the before snapshot.
    Namespace or stat changes to any untouched member reject the optimization.
    """
    current = physical_metadata(root)
    if canary not in before or set(current) != set(before):
        raise Base24OdsError("BASE24_ODS_FAULT_RESTORATION_FAILED")
    if any(current[name] != stamp for name, stamp in before.items() if name != canary):
        raise Base24OdsError("BASE24_ODS_UNTOUCHED_MEMBER_CHANGED")
    path = _member(root, canary)
    if current[canary][2] != before[canary][2] or file_sha(path) != expected_sha256:
        raise Base24OdsError("BASE24_ODS_FAULT_RESTORATION_FAILED")
