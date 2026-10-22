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


def prepare_source_retirement_inventory(
    root: Path, *, plan: dict[str, Any], discard: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Reuse one job's sealed/copy-verified source inventory for retirement.

    The caller must pin this plan to the successfully sealed image and preserve
    its retirement authority. Every retained source must still have the exact
    physical fingerprint recorded before copying. Explicitly discarded members
    (e.g. optional 1m) carry declared hashes, not a claim of byte recovery.
    No source payload bytes are opened here. This is not a cold image audit.
    """
    root = physical_path(root)
    frozen = {key: value for key, value in plan.items() if key not in {"identity", "built_at"}}
    if digest(frozen) != plan.get("identity"):
        raise Base24OdsError("BASE24_ODS_RETIREMENT_PLAN_CHANGED")
    expected = {
        row["source_relative"]: row for row in plan["files"]
        if row["source_root_archival"] == str(root)
    }
    discard = {} if discard is None else discard
    if (
        not expected or expected.keys() & discard.keys()
        or _tree(root) != expected.keys() | discard.keys()
    ):
        raise Base24OdsError("BASE24_ODS_RETIREMENT_FILE_SET_CHANGED")
    result = {}
    for relative in sorted(expected.keys() | discard.keys()):
        path = _member(root, relative)
        stamp = _fingerprint(path)
        row = expected.get(relative, discard.get(relative))
        if row is None or stamp[2] != row["size_bytes"]:
            raise Base24OdsError("BASE24_ODS_RETIREMENT_SOURCE_CHANGED")
        if relative in expected and (
            str(path) != row["source_path"] or list(stamp) != row["source_fingerprint"]
        ):
            raise Base24OdsError("BASE24_ODS_RETIREMENT_SOURCE_CHANGED")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("sha256"))) is None:
            raise Base24OdsError("BASE24_ODS_RETIREMENT_SOURCE_CHANGED")
        dev, ino, size, mtime, ctime = stamp
        result[relative] = {
            "size_bytes": size, "sha256": row["sha256"],
            "device": dev, "inode": ino, "mtime_ns": mtime, "ctime_ns": ctime,
            "verification": (
                "COPY_AND_SEAL_VERIFIED_UNCHANGED_FINGERPRINT" if relative in expected
                else "EXPLICIT_DISCARD_DECLARED_HASH_ONLY"
            ),
        }
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
