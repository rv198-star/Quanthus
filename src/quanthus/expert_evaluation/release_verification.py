"""Read-only verification for immutable formal attribution releases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    digest_value,
)

FORMAL_RELEASE_SCHEMA = "quanthus.formal-attribution-release.v1"


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def verify_formal_attribution_release(root: Path) -> dict[str, Any]:
    """Verify an exact release without importing replay or strategy runtimes."""

    if root.is_symlink():
        raise ExpertEvaluationError("path_escape", "formal release root must not be a symlink")
    resolved = root.resolve()
    try:
        manifest_bytes = (resolved / "release.json").read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError(
            "formal_release_unreadable", "formal attribution release is unreadable"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != FORMAL_RELEASE_SCHEMA
        or canonical_json_bytes(manifest) != manifest_bytes
    ):
        raise ExpertEvaluationError(
            "formal_release_invalid", "formal attribution release manifest is invalid"
        )
    declared = manifest.get("files")
    if not isinstance(declared, list):
        raise ExpertEvaluationError("formal_release_invalid", "release files are invalid")
    expected: set[str] = set()
    for item in declared:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes"}:
            raise ExpertEvaluationError("formal_release_invalid", "release file record differs")
        raw_path = resolved / str(item["path"])
        path = raw_path.resolve()
        if resolved not in path.parents or raw_path.is_symlink():
            raise ExpertEvaluationError("path_escape", "formal release file escapes root")
        if (
            not path.is_file()
            or _sha256(path) != item["sha256"]
            or path.stat().st_size != item["size_bytes"]
        ):
            raise ExpertEvaluationError(
                "formal_release_checksum_mismatch", "formal release file differs"
            )
        expected.add(str(item["path"]))
    physical = {
        item.relative_to(resolved).as_posix() for item in resolved.rglob("*") if item.is_file()
    }
    if physical != expected.union({"release.json", "READY"}):
        raise ExpertEvaluationError(
            "formal_release_inventory_mismatch", "formal release inventory differs"
        )
    observed_digest = digest_value(
        {key: value for key, value in manifest.items() if key != "release_digest"}
    )
    if manifest.get("release_digest") != observed_digest:
        raise ExpertEvaluationError(
            "formal_release_digest_mismatch", "formal release digest differs"
        )
    try:
        ready = (resolved / "READY").read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ExpertEvaluationError("formal_release_not_ready", "READY is unreadable") from exc
    if ready != observed_digest + "\n":
        raise ExpertEvaluationError("formal_release_not_ready", "READY differs")
    return manifest
