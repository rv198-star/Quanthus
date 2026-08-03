"""Validate additive DWD model extensions without invalidating built domains."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class ModelExtensionError(RuntimeError):
    """Raised when a model extension is not strictly additive and bounded."""


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelExtensionError(f"cannot read model contract: {path}") from exc
    if not isinstance(value, dict):
        raise ModelExtensionError(f"model contract is not an object: {path}")
    return value


def verify_additive_model_extension(
    base_path: Path, extension_path: Path
) -> tuple[dict[str, Any], str]:
    """Return a verified extension and its digest.

    The extension may add columns only to tables owned by its declared unbuilt
    issue owners. It cannot replace an existing column or introduce a new table.
    """

    base = _object(base_path)
    extension = _object(extension_path)
    if (
        extension.get("schema_version") != 1
        or extension.get("base_contract_id") != base.get("contract_id")
        or extension.get("base_model_sha256") != _sha256(base_path)
        or extension.get("compatibility")
        != "additive_columns_on_unbuilt_issue_63_and_64_objects_only"
    ):
        raise ModelExtensionError("model extension is not bound to the exact base model")
    owners = extension.get("allowed_issue_owners")
    if owners != [63, 64]:
        raise ModelExtensionError("model extension owner scope differs")
    ownership = base.get("build_execution_contract", {}).get("dwd_script_ownership", {})
    allowed_tables = {
        table for issue in owners for table in ownership.get(str(issue), [])
    }
    base_tables = {
        table.get("name"): table
        for table in base.get("tables", [])
        if isinstance(table, Mapping) and isinstance(table.get("name"), str)
    }
    table_extensions = extension.get("table_extensions")
    if not isinstance(table_extensions, Mapping) or set(table_extensions) != allowed_tables:
        raise ModelExtensionError("model extension table scope differs")
    for name, raw in table_extensions.items():
        if name not in base_tables or not isinstance(raw, Mapping):
            raise ModelExtensionError(f"invalid model extension table: {name}")
        additions = raw.get("columns")
        if not isinstance(additions, Mapping) or not additions:
            raise ModelExtensionError(f"model extension has no columns: {name}")
        overlap = set(additions).intersection(base_tables[name].get("columns", {}))
        if overlap:
            raise ModelExtensionError(
                f"model extension replaces base columns in {name}: {sorted(overlap)}"
            )
        if any(
            not isinstance(column, str) or not isinstance(kind, str)
            for column, kind in additions.items()
        ):
            raise ModelExtensionError(f"model extension has invalid columns: {name}")
    return extension, _sha256(extension_path)
