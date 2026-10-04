"""Offline safe full-column audit of sealed observation scopes, not image acceptance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, semantic_scan
from .base22_sources import _tree, physical_path


def audit_observation_stage(root: Path, descriptor: dict[str, Any], config: Path) -> dict[str, Any]:
    """No source-root opening, old-path fallback, network or value-bearing report.

    Each stage is a bounded explicit output scope. Native/source union, all33
    coverage and quality qualification remain separate required image gates.
    """
    root = physical_path(root)
    body = {k: v for k, v in descriptor.items() if k != "identity"}
    if (
        descriptor.get("kind") != "sealed_actual_domain_observations_not_complete_base22"
        or digest(body) != descriptor.get("identity")
        or descriptor.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("OBSERVATION_DESCRIPTOR_INVALID")
    _, model, extension = load_contract(config)
    expected_schemas = schemas(model, extension)
    seen = set()
    counts: dict[str, int] = {}
    scans = {}
    for item in descriptor["outputs"]:
        path = physical_path(root / item["path"])
        table = item["table"]
        if root not in path.parents or table not in expected_schemas or path in seen:
            raise BoundedError("OBSERVATION_PHYSICAL_MEMBER_INVALID")
        if (
            path.stat().st_nlink != 1
            or path.stat().st_size != item["size_bytes"]
            or file_sha(path) != item["sha256"]
        ):
            raise BoundedError("OBSERVATION_MEMBER_HASH_MISMATCH")
        scan = semantic_scan([path], expected_schemas[table], batch_rows=1024)
        if scan != item["semantic_scan"] or scan["rows"] != item["rows"]:
            raise BoundedError("OBSERVATION_RECOMPUTED_SEMANTICS_MISMATCH")
        seen.add(path)
        counts[table] = counts.get(table, 0) + scan["rows"]
        scans[item["path"]] = scan
    if counts != {t: n for t, n in descriptor["output_rows"].items() if n}:
        raise BoundedError("OBSERVATION_EXACT_TABLE_ROW_CENSUS_MISMATCH")
    # Check physical files, including zero-row files: no inference from nonempty rows.
    payload = root / "observations" / descriptor["label"] / "payload"
    physical = {physical_path(payload / p) for p in _tree(payload)} if payload.exists() else set()
    if physical != seen:
        raise BoundedError("OBSERVATION_EXACT_PHYSICAL_FILE_SET_MISMATCH")
    for item in descriptor["archived_true_source_observations"]:
        path = physical_path(root / item["path"])
        if (
            root not in path.parents
            or path.stat().st_nlink != 1
            or file_sha(path) != item["sha256"]
        ):
            raise BoundedError("OBSERVATION_ARCHIVED_SOURCE_BYTES_MISMATCH")
    return {
        "identity": descriptor["identity"],
        "tables": counts,
        "files": len(seen),
        "semantic_scans": scans,
        "status": "OFFLINE_SEALED_SCOPE_SCAN_PASS_NOT_IMAGE_ACCEPTANCE",
    }


def load_observation_descriptor(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_bytes())
        if not isinstance(result, dict):
            raise ValueError("object required")
        return dict(result)
    except (OSError, ValueError) as exc:
        raise BoundedError("OBSERVATION_DESCRIPTOR_UNREADABLE") from exc
