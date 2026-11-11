"""Exact native financial members, including the legacy flat company table."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from .base22_bounded import BoundedError
from .base22_financial_layout import TABLES


def native_members(manifest: dict[str, Any], table: str) -> list[dict[str, Any]]:
    if table not in TABLES or not isinstance(manifest.get("files"), list):
        raise BoundedError("FINANCIAL_NATIVE_MEMBER_MANIFEST_OR_TABLE_INVALID")
    result = []
    paths = set()
    for member in manifest["files"]:
        rel = member.get("path")
        if not isinstance(rel, str) or not rel:
            raise BoundedError("FINANCIAL_NATIVE_MEMBER_PATH_INVALID")
        path = PurePosixPath(rel)
        if path.is_absolute() or ".." in path.parts or str(path) != rel:
            raise BoundedError("FINANCIAL_NATIVE_MEMBER_PATH_INVALID")
        if path.parts[0] != "payload" or path.suffix != ".parquet":
            continue
        if table not in path.parts[1:-1] and path.name != table + ".parquet":
            continue
        if rel in paths:
            raise BoundedError("FINANCIAL_NATIVE_MEMBER_DUPLICATE")
        paths.add(rel)
        result.append(member)
    if not result:
        raise BoundedError("FINANCIAL_NATIVE_MEMBER_SET_EMPTY")
    return sorted(result, key=lambda i: i["path"])
