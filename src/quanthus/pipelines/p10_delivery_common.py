"""Small sealed-product primitives; all paths and inventories fail closed."""

from __future__ import annotations

import grp
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

COMPLETE_KIND = "cn01_complete_daily_dwd_increment"
COMPLETE_POINTER = "CURRENT_COMPLETE_DAILY.json"
COMPLETE_MINUTE_FILE = "payload/security_market_observation/dwd_security_bar/native-minute.parquet"
BASE23_MANIFEST = "sha256:6991d0c9440d4117c3dfdd3375e2267d0e43174203fc834751c9476cd7598206"
BASE23_CONTENT = "sha256:f5b1c35e9d60874a5daa8ce36f50d8d4edf62317075e3f223d344a1eb7292608"


def require_revision(value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("delivery code identity must be an exact lowercase Git SHA")


def encoded(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
    ).encode()


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def file_digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return "sha256:" + sha.hexdigest()


def object_at(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("sealed JSON must be an object")
    return value


def atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("xb") as handle:
        os.fchmod(handle.fileno(), 0o640)
        handle.write(encoded(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare_export(root: Path, package: Path) -> None:
    """Grant the configured existing read-only group access before pointer publication.

    No ancestor outside this product root, SSH jail, or source member is changed.
    Setgid directories make subsequent atomic pointer/receipt files inherit the group.
    """
    group = os.environ.get("QUANTHUS_COMPLETE_DAILY_EXPORT_GROUP")
    if not group:
        return
    gid = grp.getgrnam(group).gr_gid
    relative = package.relative_to(root)
    parents = [root / Path(*relative.parts[:i]) for i in range(len(relative.parts) + 1)]
    paths = parents + list(package.rglob("*"))
    for path in paths:
        if path.is_symlink():
            raise ValueError("read-only export may not traverse symlinks")
        os.chown(path, -1, gid)
        os.chmod(path, 0o2750 if path.is_dir() else 0o640)
    # These may already exist after a interrupted or repeated publication.
    for name in ("date-pointers", "verification", "visibility"):
        path = root / name
        path.mkdir(exist_ok=True)
        if path.is_symlink():
            raise ValueError("read-only export receipt directory is a symlink")
        os.chown(path, -1, gid)
        os.chmod(path, 0o2750)


def safe_path(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if not isinstance(relative, str) or rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise ValueError("unsafe sealed product relative path")
    path = root / rel
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("sealed product path escapes root")
    if any(
        (root / Path(*rel.parts[:index])).is_symlink() for index in range(1, len(rel.parts) + 1)
    ):
        raise ValueError("sealed product contains symlink")
    return path


def inventory(root: Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("product may not contain symlinks")
        if path.is_file() and path.parent == root and path.name in {"manifest.json", "READY"}:
            continue
        if path.is_file():
            result.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": file_digest(path),
                }
            )
    return result


def seal(root: Path, manifest: dict[str, Any]) -> None:
    manifest["files"] = inventory(root)
    manifest["content_digest"] = digest(encoded(manifest["files"]))
    atomic(root / "manifest.json", manifest)
    with (root / "READY").open("xb") as handle:
        handle.write((file_digest(root / "manifest.json") + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())


def verify_seal(root: Path, kind: str) -> dict[str, Any]:
    manifest = object_at(root / "manifest.json")
    if manifest.get("kind") != kind or (root / "READY").read_text().strip() != file_digest(
        root / "manifest.json"
    ):
        raise ValueError("product kind or manifest seal differs")
    paths = set()
    for item in manifest["files"]:
        relative = item["path"]
        path = safe_path(root, relative)
        if (
            relative in paths
            or not path.is_file()
            or path.stat().st_size != item["bytes"]
            or file_digest(path) != item["sha256"]
        ):
            raise ValueError("sealed physical inventory differs")
        paths.add(relative)
    if paths != {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} - {
        "manifest.json",
        "READY",
    }:
        raise ValueError("undeclared sealed payload")
    if digest(encoded(manifest["files"])) != manifest.get("content_digest"):
        raise ValueError("sealed content digest differs")
    return manifest
