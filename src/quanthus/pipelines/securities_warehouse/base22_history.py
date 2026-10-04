"""One immutable, job-local repaired-history TMP; no cross-job cache or admission."""

from __future__ import annotations

import importlib
import json
import time
from typing import Any

from .base22 import canonical, digest, file_sha
from .base22_bounded import BoundedError, Budget
from .base22_sources import _tree

CLOSURE_FIELDS = {
    "source_inventory",
    "configuration",
    "policy",
    "evaluator",
    "schema",
    "scope",
    "universe",
    "repair_evidence",
}


def _identity(closure: dict[str, str]) -> str:
    import re

    if set(closure) != CLOSURE_FIELDS or any(
        not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
        for value in closure.values()
    ):
        raise BoundedError("HISTORY_SEMANTIC_CLOSURE_INVALID")
    return digest(closure)


def seal_history(
    budget: Budget, closure: dict[str, str], census: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Seal already validated partition files once; never rebuild a prerequisite.

    The upstream materializer owns repair qualification and the row/key scan. This
    function binds its exact evidence digest, files, schemas and counts for every
    downstream consumer. It never admits a source or marks a candidate READY.
    """
    identity = _identity(closure)
    root = budget.destination(budget.root / "history")
    if not census or not root.is_dir() or (root / "HISTORY.json").exists():
        raise BoundedError("HISTORY_MISSING_OR_ALREADY_SEALED")
    actual = set(_tree(root))
    if actual != set(census):
        raise BoundedError("HISTORY_PHYSICAL_CENSUS_MISMATCH")
    start = time.monotonic()
    files = {}
    pq = importlib.import_module("pyarrow.parquet")
    for relative, item in sorted(census.items()):
        path = budget.destination(root / relative)
        if root not in path.parents or path.stat().st_nlink != 1:
            raise BoundedError("HISTORY_INDEPENDENT_FILE_REQUIRED")
        if (
            set(item) != {"sha256", "rows", "schema_sha256"}
            or not isinstance(item["rows"], int)
            or isinstance(item["rows"], bool)
            or item["rows"] < 0
            or file_sha(path) != item["sha256"]
        ):
            raise BoundedError("HISTORY_MEMBER_IDENTITY_MISMATCH")
        footer = pq.ParquetFile(path)
        if (
            footer.metadata.num_rows != item["rows"]
            or digest(str(footer.schema_arrow.remove_metadata())) != item["schema_sha256"]
        ):
            raise BoundedError("HISTORY_SCHEMA_ROW_CENSUS_MISMATCH")
        budget.check()
        files[relative] = {**item, "size_bytes": path.stat().st_size}
    body = {
        "schema_version": 1,
        "lifecycle": "TMP",
        "job_root": str(budget.root),
        "semantic_identity": identity,
        "closure": closure,
        "files": files,
        "source_admission": "NOT_GRANTED_BY_THIS_COMPONENT",
    }
    descriptor = {**body, "descriptor_sha256": digest(body)}
    # Exclusive marker is written only after exact physical member verification.
    with (root / "HISTORY.json").open("xb") as stream:
        stream.write(canonical(descriptor) + b"\n")
    return {
        "identity": identity,
        "descriptor_sha256": descriptor["descriptor_sha256"],
        "disposition": "SEAL_ONCE",
        "elapsed_seconds": time.monotonic() - start,
        "rows": sum(item["rows"] for item in files.values()),
        "bytes": sum(item["size_bytes"] for item in files.values()),
    }


def consume_history(budget: Budget, closure: dict[str, str], descriptor_sha: str) -> dict[str, Any]:
    """Bind the same job-local immutable files; no old-source/final-image fallback."""
    identity = _identity(closure)
    root = budget.destination(budget.root / "history")
    start = time.monotonic()
    try:
        descriptor = json.loads((root / "HISTORY.json").read_bytes())
        recorded = descriptor.pop("descriptor_sha256")
        if (
            recorded != descriptor_sha
            or digest(descriptor) != recorded
            or descriptor["semantic_identity"] != identity
            or descriptor["closure"] != closure
            or descriptor["job_root"] != str(budget.root)
            or descriptor["lifecycle"] != "TMP"
            or set(_tree(root)) != {"HISTORY.json", *descriptor["files"]}
        ):
            raise BoundedError("HISTORY_CHECKPOINT_IDENTITY_MISMATCH")
        for relative, item in descriptor["files"].items():
            path = budget.destination(root / relative)
            if root not in path.parents or path.stat().st_nlink != 1:
                raise BoundedError("HISTORY_INDEPENDENT_FILE_REQUIRED")
            if path.stat().st_size != item["size_bytes"] or file_sha(path) != item["sha256"]:
                raise BoundedError("HISTORY_MEMBER_IDENTITY_MISMATCH")
            budget.check()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BoundedError("HISTORY_CHECKPOINT_UNREADABLE") from exc
    return {
        "identity": identity,
        "files": descriptor["files"],
        "disposition": "REUSE_IMMUTABLE_TMP",
        "elapsed_seconds": time.monotonic() - start,
        "rows": sum(item["rows"] for item in descriptor["files"].values()),
        "bytes": sum(item["size_bytes"] for item in descriptor["files"].values()),
    }
