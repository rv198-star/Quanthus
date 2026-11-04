"""Portable complete member inventories for the standard Base build pipeline.

This stage proves byte/physical integrity only. It never grants data acceptance,
creates product READY, collects data, or dereferences archival source paths.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any

INVENTORY_KIND = "p10-base-input-inventory-v1"
SUPPORTED_KINDS = {
    "quanthus_ods_base24_bundle",
    "dwd_base24_native_1d5m_purpose_qualified_candidate_v1",
    "cn02_base24_daily_1d_native5m_v1",
    "quanthus_ods_base_bundle_v1",
    "quanthus_dwd_base_bundle_v1",
    "quanthus_daily_1d5m_v1",
}
GENERIC_KINDS = {
    "ods": "quanthus_ods_base_bundle_v1",
    "dwd": "quanthus_dwd_base_bundle_v1",
    "daily": "quanthus_daily_1d5m_v1",
}


class BuildInputError(ValueError):
    """Stable reason codes, without source row values or credentials."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def identity(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def _physical(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in [*path.parents, path]):
        raise BuildInputError("BUILD_INPUT_SYMLINK")
    return path


def member(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise BuildInputError("BUILD_INPUT_MEMBER_PATH")
    part = PurePosixPath(relative)
    if (part.is_absolute() or ".." in part.parts or part.as_posix() != relative
            or relative == "." or "\\" in relative):
        raise BuildInputError("BUILD_INPUT_MEMBER_PATH")
    return _physical(root / relative)


def _fingerprint(path: Path) -> tuple[int, ...]:
    s = path.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
        raise BuildInputError("BUILD_INPUT_NOT_INDEPENDENT_REGULAR_FILE")
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def file_sha(path: Path) -> str:
    path = _physical(path)
    before = _fingerprint(path)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(data)
    if _fingerprint(path) != before:
        raise BuildInputError("BUILD_INPUT_CHANGED_DURING_READ")
    return "sha256:" + h.hexdigest()


def _object(path: Path) -> dict[str, Any]:
    before = _fingerprint(_physical(path))
    data = path.read_bytes()
    if _fingerprint(path) != before:
        raise BuildInputError("BUILD_INPUT_CHANGED_DURING_READ")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise BuildInputError("BUILD_INPUT_OBJECT_REQUIRED")
    return value


def _tree(root: Path) -> set[str]:
    found: set[str] = set()
    def failed(error: OSError) -> None:
        raise BuildInputError("BUILD_INPUT_UNREADABLE_TREE") from error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        for name in [*dirs, *files]:
            path = _physical(Path(directory) / name)
            if name in files:
                _fingerprint(path)
                found.add(path.relative_to(root).as_posix())
    return found


def _declarations(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    kind = manifest.get("kind")
    if kind not in SUPPORTED_KINDS:
        raise BuildInputError("BUILD_INPUT_UNSUPPORTED_KIND")
    if kind in {"quanthus_ods_base24_bundle", *GENERIC_KINDS.values()}:
        return manifest["content"]["files"]
    if kind == "cn02_base24_daily_1d_native5m_v1":
        return manifest["files"]
    descriptor = member(root, "MATERIALIZATION.json")
    if file_sha(descriptor) != manifest.get("materialization_sha256"):
        raise BuildInputError("BUILD_INPUT_DESCRIPTOR_IDENTITY")
    value = _object(descriptor)
    records = [r for table in value["layout"].values() for r in table["files"]]
    records = [dict(r) for r in records]
    for relative, expected in value["evidence"].items():
        records.append({"path": relative, "sha256": expected,
                        "size_bytes": member(root, relative).stat().st_size})
    records.append({"path": "MATERIALIZATION.json",
                    "sha256": manifest["materialization_sha256"],
                    "size_bytes": descriptor.stat().st_size})
    return records


def inventory(root: Path, expected_manifest: str, *, workers: int = 4) -> dict[str, Any]:
    """Hash every declared member and reject missing/extra/linked/changed files.

    An expected manifest identity is mandatory; a self-consistent replacement
    package must not silently become a new selected input. Returned identity is
    independent of the physical root so relocation can be verified cold.
    """
    root = _physical(root)
    if not root.is_dir() or type(workers) is not int or not 1 <= workers <= 8:
        raise BuildInputError("BUILD_INPUT_ROOT_OR_WORKERS")
    manifest_path = member(root, "manifest.json")
    if file_sha(manifest_path) != expected_manifest:
        raise BuildInputError("BUILD_INPUT_MANIFEST_IDENTITY")
    manifest = _object(manifest_path)
    ready = member(root, "READY")
    ready_identity = ready.read_text(encoding="ascii").strip().removeprefix("sha256:")
    if ready_identity != expected_manifest.removeprefix("sha256:"):
        raise BuildInputError("BUILD_INPUT_READY_BINDING")
    records = _declarations(root, manifest)
    expected: dict[str, dict[str, Any]] = {}
    for row in records:
        relative = row["path"]
        member(root, relative)
        if (relative in expected or relative in {"manifest.json", "READY"}
                or type(row.get("size_bytes")) is not int or row["size_bytes"] < 0
                or not isinstance(row.get("sha256"), str)
                or len(row["sha256"]) != 71 or not row["sha256"].startswith("sha256:")):
            raise BuildInputError("BUILD_INPUT_DECLARATION_INVALID")
        expected[relative] = row
    paths = set(expected) | {"manifest.json", "READY"}
    if _tree(root) != paths:
        raise BuildInputError("BUILD_INPUT_MEMBER_SET")
    before = {relative: _fingerprint(member(root, relative)) for relative in sorted(paths)}
    def check(relative: str) -> dict[str, Any]:
        path = member(root, relative)
        actual = {"path": relative, "size_bytes": path.stat().st_size,
                  "sha256": file_sha(path)}
        if relative == "manifest.json" and actual["sha256"] != expected_manifest:
            raise BuildInputError("BUILD_INPUT_MANIFEST_IDENTITY")
        if relative in expected:
            row = expected[relative]
            if any(actual[k] != row[k] for k in ["size_bytes", "sha256"]):
                raise BuildInputError("BUILD_INPUT_MEMBER_IDENTITY")
        return actual
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="base-input") as pool:
        files = list(pool.map(check, sorted(paths)))
    if (_tree(root) != paths or
            any(_fingerprint(member(root, rel)) != fp for rel, fp in before.items())):
        raise BuildInputError("BUILD_INPUT_CHANGED_DURING_INVENTORY")
    content = {"kind": INVENTORY_KIND, "package_kind": manifest["kind"],
               "manifest_sha256": expected_manifest, "files": files}
    return {"status": "FULL_MEMBER_INTEGRITY_PASS_NOT_DATA_ACCEPTANCE",
            "content": content, "identity": identity(content),
            "file_count": len(files), "bytes": sum(f["size_bytes"] for f in files)}


def verify_inventory(root: Path, frozen: dict[str, Any], *, workers: int = 4) -> dict[str, Any]:
    """Full cold relocation verification; never trusts cached paths/fingerprints."""
    content = frozen.get("content", {})
    if frozen.get("identity") != identity(content) or content.get("kind") != INVENTORY_KIND:
        raise BuildInputError("BUILD_INPUT_FROZEN_IDENTITY")
    actual = inventory(root, content["manifest_sha256"], workers=workers)
    if actual["content"] != content or actual["identity"] != frozen["identity"]:
        raise BuildInputError("BUILD_INPUT_FROZEN_CONTENT")
    return actual


def inventory_evidence(root: Path, records: list[dict[str, Any]], *,
                       workers: int = 4) -> dict[str, Any]:
    """Freeze explicitly selected evidence bytes without inventing a product READY.

    The caller pins declarations in the selection. Parent relationships, use
    limits and calendar semantics remain separate admission/audit requirements.
    """
    root = _physical(root)
    if not root.is_dir() or type(workers) is not int or not 1 <= workers <= 8:
        raise BuildInputError("BUILD_INPUT_ROOT_OR_WORKERS")
    if not isinstance(records, list) or not records:
        raise BuildInputError("BUILD_EVIDENCE_MEMBERS_REQUIRED")
    declared = {}
    for row in records:
        if (not isinstance(row, dict) or set(row) != {"path", "sha256", "size_bytes"}
                or type(row["size_bytes"]) is not int or row["size_bytes"] < 0
                or not isinstance(row["sha256"], str)
                or len(row["sha256"]) != 71 or not row["sha256"].startswith("sha256:")
                or any(c not in "0123456789abcdef" for c in row["sha256"][7:])):
            raise BuildInputError("BUILD_EVIDENCE_DECLARATION_INVALID")
        member(root, row["path"])
        if row["path"] in declared:
            raise BuildInputError("BUILD_EVIDENCE_DUPLICATE_MEMBER")
        declared[row["path"]] = row
    if _tree(root) != set(declared):
        raise BuildInputError("BUILD_EVIDENCE_MEMBER_SET")
    before = {name: _fingerprint(member(root, name)) for name in sorted(declared)}

    def check(name: str) -> dict[str, Any]:
        path = member(root, name)
        actual = {"path": name, "size_bytes": path.stat().st_size, "sha256": file_sha(path)}
        if actual != declared[name]:
            raise BuildInputError("BUILD_EVIDENCE_MEMBER_IDENTITY")
        return actual

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="base-evidence") as pool:
        files = list(pool.map(check, sorted(declared)))
    if (_tree(root) != set(declared)
            or any(_fingerprint(member(root, name)) != fp for name, fp in before.items())):
        raise BuildInputError("BUILD_EVIDENCE_CHANGED_DURING_INVENTORY")
    content = {"kind": "p10-base-evidence-input-v1", "files": files}
    return {"status": "FULL_EVIDENCE_INTEGRITY_PASS_NOT_DATA_ACCEPTANCE",
            "content": content, "identity": identity(content), "file_count": len(files),
            "bytes": sum(row["size_bytes"] for row in files)}
