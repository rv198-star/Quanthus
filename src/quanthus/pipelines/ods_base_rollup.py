"""Build one self-contained ODS Base from a Base and its fill-only Hotfix.

The rollup preserves every governed source file at its original package-relative
path.  Files are hard-linked into a new immutable root, so removing an older
package name later does not remove bytes still referenced by the rollup.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.ods_base import (
    OdsBaseBundle,
    OdsBaseError,
    OdsBasePolicy,
    _atomic_json,
    _canonical_digest,
    _parquet_metadata,
    _selection_digest,
    _sha256,
    verify_ods_base_bundle,
)


class OdsBaseRollupError(OdsBaseError):
    """Raised when a rollup source, package, or pointer transition is invalid."""


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,191}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_AUTHORITY = {
    "execution_authorized": False,
    "historical_source": "hermes",
    "oci_ods_enabled": False,
    "reconstruction_seed": True,
}

OVERLAY_BUSINESS_KEYS: dict[str, list[str]] = {
    "daily_ohlcv_none_hotfix": ["stock_code", "date"],
    "daily_status_observation_hotfix": ["stock_code", "date"],
    "minute_1m_hotfix": ["stock_code", "ts"],
    "minute_5m_hotfix": ["stock_code", "ts"],
    "valuation_history_hotfix": ["stock_code", "date"],
    "fundamentals_financial_statements_parquet": [
        "stock_code",
        "statement_type",
    ],
    "fundamentals_financial_quarterly_parquet": ["stock_code", "report_date"],
    "fundamentals_raw_f10_parquet": ["stock_code", "category"],
    "fundamentals_industry_membership_parquet": ["stock_code", "market_date"],
}

OVERLAY_PRECEDENCE: dict[str, Any] = {
    "application_mode": "fill_absent_keys_only",
    "default": "parent_base_wins_on_overlapping_business_keys_hotfix_fills_absent_keys_only",
    "business_keys": OVERLAY_BUSINESS_KEYS,
    "overlap_mismatch_policy": "fail_build",
}

CALENDAR_HISTORY_BUSINESS_KEYS: dict[str, list[str]] = {
    "trading_calendar_history_hotfix": ["exchange", "trade_date", "session"],
}

CALENDAR_HISTORY_APPLICATION: dict[str, Any] = {
    "business_keys": CALENDAR_HISTORY_BUSINESS_KEYS,
    "current_switch": False,
    "mode": "add_absent_business_keys_only",
    "parent_mutation": False,
}


def _direct_hotfix_precedence(application: Mapping[str, Any]) -> dict[str, Any]:
    mode = application.get("mode")
    if mode == "fill_absent_keys_only":
        return copy.deepcopy(OVERLAY_PRECEDENCE)
    if mode == "add_absent_business_keys_only":
        return {
            "application_mode": mode,
            "business_keys": copy.deepcopy(CALENDAR_HISTORY_BUSINESS_KEYS),
            "default": "parent_base_wins_hotfix_adds_absent_business_keys_only",
            "overlap_mismatch_policy": "fail_build",
        }
    raise OdsBaseRollupError("Hotfix application mode is unsupported")


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OdsBaseRollupError(f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise OdsBaseRollupError(f"JSON root must be an object: {path}")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OdsBaseRollupError(f"{field} must be an object")
    return value


def _sequence(value: object, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise OdsBaseRollupError(f"{field} must be an array")
    return value


def _text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise OdsBaseRollupError(f"{key} must be a non-empty string")
    return result


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise OdsBaseRollupError(f"{field} must be a SHA-256 digest")
    return value


def _safe_relative(value: object, field: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise OdsBaseRollupError(f"{field} must be a relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() == "." or ".." in path.parts:
        raise OdsBaseRollupError(f"{field} must be a safe relative path")
    return path


def _inside(root: Path, value: object, field: str) -> Path:
    relative = _safe_relative(value, field)
    target = root.joinpath(*relative.parts)
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise OdsBaseRollupError(f"{field} escapes its package root")
    return target


def _normalize_built_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OdsBaseRollupError("built_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise OdsBaseRollupError("built_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _write_json_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o640)
        json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _read_envelope(root: Path) -> tuple[dict[str, Any], str]:
    root = root.resolve()
    if not root.is_dir() or root.is_symlink():
        raise OdsBaseRollupError(f"package root is invalid: {root}")
    manifest_path = root / "manifest.json"
    ready_path = root / "READY"
    manifest_sha = _sha256(manifest_path)
    try:
        ready = ready_path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise OdsBaseRollupError(f"package has no READY: {root}") from exc
    if ready != manifest_sha:
        raise OdsBaseRollupError(f"READY does not bind manifest.json: {root}")
    return _object(manifest_path), manifest_sha


def _file_inventory(root: Path, files_value: object, *, source_label: str) -> list[dict[str, Any]]:
    files = _sequence(files_value, f"{source_label}.files")
    if not files:
        raise OdsBaseRollupError(f"{source_label} has no governed files")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in files:
        item = dict(_mapping(raw, f"{source_label}.files[]"))
        relative = _safe_relative(item.get("path"), f"{source_label}.files.path").as_posix()
        if not relative.startswith("payload/") or relative in seen:
            raise OdsBaseRollupError(
                f"{source_label} file path is duplicate or outside payload: {relative}"
            )
        seen.add(relative)
        size = item.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise OdsBaseRollupError(f"{source_label} file size is invalid: {relative}")
        _digest(item.get("sha256"), f"{source_label}.files.sha256")
        _text(item, "dataset_id")
        _text(item, "role")
        _text(item, "format")
        target = _inside(root, relative, f"{source_label}.files.path")
        if target.is_symlink() or not target.is_file() or target.stat().st_size != size:
            raise OdsBaseRollupError(
                f"{source_label} file is missing, unsafe, or has wrong size: {relative}"
            )
        result.append(item)
    envelope_files = {root / "manifest.json", root / "READY"}
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path not in envelope_files
    }
    if actual != seen:
        raise OdsBaseRollupError(f"{source_label} manifest file set differs from disk")
    if [str(item["path"]) for item in result] != sorted(seen):
        raise OdsBaseRollupError(f"{source_label} file inventory is not path-sorted")
    return result


def _load_parent(
    root: Path,
    *,
    policy: OdsBasePolicy,
    expected_manifest_sha256: str,
    expected_content_digest: str,
) -> tuple[dict[str, Any], Mapping[str, Any], list[dict[str, Any]], str]:
    manifest, manifest_sha = _read_envelope(root)
    if manifest_sha != _digest(expected_manifest_sha256, "expected parent manifest"):
        raise OdsBaseRollupError("parent manifest differs from the expected identity")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "quanthus_ods_base_bundle"
        or manifest.get("bundle_id") != root.resolve().name
        or _REVISION.fullmatch(str(manifest.get("code_revision", ""))) is None
    ):
        raise OdsBaseRollupError("parent ODS Base manifest is unsupported")
    content = _mapping(manifest.get("content"), "parent.content")
    content_digest = _canonical_digest(content)
    if content_digest != manifest.get("content_digest") or content_digest != _digest(
        expected_content_digest, "expected parent content"
    ):
        raise OdsBaseRollupError("parent content digest is invalid or unexpected")
    if (
        content.get("contract_id") != policy.contract_id
        or content.get("policy_sha256") != policy.digest
        or content.get("authority") != _AUTHORITY
    ):
        raise OdsBaseRollupError("parent ODS Base policy or authority binding is invalid")
    files = _file_inventory(root.resolve(), content.get("files"), source_label="parent")
    if content.get("rollup") is not None:
        _verify_rollup_contract(root.resolve(), manifest, content)
    return manifest, content, files, manifest_sha


def _load_hotfix(
    root: Path,
    *,
    parent_identity: Mapping[str, Any],
    expected_manifest_sha256: str,
    expected_content_digest: str,
) -> tuple[dict[str, Any], Mapping[str, Any], list[dict[str, Any]], str]:
    manifest, manifest_sha = _read_envelope(root)
    if manifest_sha != _digest(expected_manifest_sha256, "expected Hotfix manifest"):
        raise OdsBaseRollupError("Hotfix manifest differs from the expected identity")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "ods_base_hotfix"
        or manifest.get("hotfix_id") != root.resolve().name
    ):
        raise OdsBaseRollupError("Hotfix manifest is unsupported")
    if manifest.get("parent") != dict(parent_identity):
        raise OdsBaseRollupError("Hotfix is not bound to the exact parent Base")
    application = _mapping(manifest.get("application"), "hotfix.application")
    application_mode = application.get("mode")
    legacy_application = {
        "current_switch": False,
        "mode": "fill_absent_keys_only",
        "parent_mutation": False,
    }
    if application_mode == "fill_absent_keys_only":
        if application != legacy_application:
            raise OdsBaseRollupError("Hotfix application contract is not fill-only")
    elif application_mode == "add_absent_business_keys_only":
        if application != CALENDAR_HISTORY_APPLICATION:
            raise OdsBaseRollupError("calendar-history Hotfix application contract is invalid")
    else:
        raise OdsBaseRollupError("Hotfix application mode is unsupported")
    scope = _mapping(manifest.get("scope"), "hotfix.scope")
    if scope.get("through_date") != parent_identity["through_date"]:
        raise OdsBaseRollupError("Hotfix scope is invalid")
    if application_mode == "fill_absent_keys_only":
        if (
            not isinstance(scope.get("security_count"), int)
            or isinstance(scope.get("security_count"), bool)
            or int(scope["security_count"]) <= 0
        ):
            raise OdsBaseRollupError("Hotfix scope is invalid")
    else:
        if (
            scope.get("kind") != "calendar_history"
            or scope.get("venues") != ["SSE", "SZSE"]
            or not isinstance(scope.get("calendar_date_count"), int)
            or isinstance(scope.get("calendar_date_count"), bool)
            or int(scope["calendar_date_count"]) <= 0
        ):
            raise OdsBaseRollupError("calendar-history Hotfix scope is invalid")
        try:
            minimum_date = date.fromisoformat(str(scope.get("minimum_date")))
            maximum_date = date.fromisoformat(str(scope.get("maximum_date")))
        except ValueError as exc:
            raise OdsBaseRollupError("calendar-history Hotfix date scope is invalid") from exc
        if minimum_date > maximum_date:
            raise OdsBaseRollupError("calendar-history Hotfix date scope is reversed")
    _digest(scope.get("scope_digest"), "hotfix.scope.scope_digest")
    content = _mapping(manifest.get("content"), "hotfix.content")
    files = _file_inventory(root.resolve(), content.get("files"), source_label="hotfix")
    content_identity = {
        "parent": dict(parent_identity),
        "scope_digest": scope["scope_digest"],
        "through_date": scope["through_date"],
        "files": files,
    }
    content_digest = _canonical_digest(content_identity)
    if content_digest != manifest.get("content_digest") or content_digest != _digest(
        expected_content_digest, "expected Hotfix content"
    ):
        raise OdsBaseRollupError("Hotfix content digest is invalid or unexpected")
    coverage = _mapping(content.get("coverage"), "hotfix.content.coverage")
    dataset_ids = {str(item["dataset_id"]) for item in files}
    if application_mode == "fill_absent_keys_only":
        daily = _mapping(coverage.get("daily"), "hotfix.content.coverage.daily")
        if daily.get("overlap_mismatches") != 0:
            raise OdsBaseRollupError("Hotfix overlap mismatch gate did not pass")
        missing = sorted(set(OVERLAY_BUSINESS_KEYS) - dataset_ids)
        if missing:
            raise OdsBaseRollupError(f"Hotfix is missing overlay datasets: {missing}")
    else:
        calendar = _mapping(coverage.get("calendar"), "hotfix.content.coverage.calendar")
        if (
            dataset_ids != set(CALENDAR_HISTORY_BUSINESS_KEYS)
            or calendar.get("overlap_business_keys") != 0
            or calendar.get("conflicting_business_keys") != 0
            or calendar.get("venues") != ["SSE", "SZSE"]
            or calendar.get("calendar_date_count") != scope.get("calendar_date_count")
        ):
            raise OdsBaseRollupError("calendar-history Hotfix coverage gate did not pass")
    return manifest, content, files, manifest_sha


def _link_member(source: Path, target: Path, *, expected_size: int) -> None:
    if source.is_symlink() or not source.is_file():
        raise OdsBaseRollupError(f"rollup source member is unsafe: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    before = source.stat()
    if before.st_size != expected_size:
        raise OdsBaseRollupError(f"rollup source member changed size: {source}")
    if before.st_dev != target.parent.stat().st_dev:
        raise OdsBaseRollupError(f"hard-link rollup requires one filesystem: {source}")
    try:
        os.link(source, target, follow_symlinks=False)
    except OSError as exc:
        raise OdsBaseRollupError(f"cannot hard-link rollup member: {source}") from exc
    after = source.stat()
    linked = target.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or (linked.st_dev, linked.st_ino, linked.st_size) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
    ):
        raise OdsBaseRollupError(f"hard-linked member identity changed: {source}")


def _rollup_entry(
    item: Mapping[str, Any], *, source_root: Path, source_label: str
) -> dict[str, Any]:
    result = copy.deepcopy(dict(item))
    result["rollup_source"] = source_label
    if result.get("format") == "parquet" and "schema_sha256" not in result:
        source = _inside(source_root, result.get("path"), "rollup.files.path")
        metadata = _parquet_metadata(source, ())
        if "row_count" in result and result["row_count"] != metadata["row_count"]:
            raise OdsBaseRollupError(f"Hotfix Parquet row count drift: {source}")
        result.update(metadata)
    return result


def _parent_datasets(
    datasets_value: object,
    *,
    source_files: Mapping[str, Mapping[str, Any]],
    rollup_files: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    datasets = _sequence(datasets_value, "parent.datasets")
    result: list[dict[str, Any]] = []
    seen_parts: set[str] = set()
    seen_datasets: set[str] = set()
    for raw in datasets:
        dataset = copy.deepcopy(dict(_mapping(raw, "parent.datasets[]")))
        dataset_id = _text(dataset, "dataset_id")
        if dataset_id in seen_datasets:
            raise OdsBaseRollupError(f"duplicate parent dataset: {dataset_id}")
        seen_datasets.add(dataset_id)
        parts = _sequence(dataset.get("parts"), f"parent.datasets.{dataset_id}.parts")
        converted: list[dict[str, Any]] = []
        for raw_part in parts:
            part = dict(_mapping(raw_part, f"parent.datasets.{dataset_id}.parts[]"))
            path = _text(part, "path")
            if path in seen_parts or part != source_files.get(path):
                raise OdsBaseRollupError(f"parent dataset part inventory drift: {path}")
            seen_parts.add(path)
            converted.append(copy.deepcopy(dict(rollup_files[path])))
        dataset["parts"] = converted
        dataset["part_count"] = len(converted)
        dataset["size_bytes"] = sum(int(item["size_bytes"]) for item in converted)
        result.append(dataset)
    if seen_parts != set(source_files):
        raise OdsBaseRollupError("parent dataset summaries do not cover every parent file")
    return result


def _hotfix_datasets(files: Sequence[Mapping[str, Any]], hotfix_id: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in files:
        grouped[str(item["dataset_id"])].append(copy.deepcopy(dict(item)))
    result: list[dict[str, Any]] = []
    for dataset_id, parts in sorted(grouped.items()):
        roles = {str(item["role"]) for item in parts}
        if len(roles) != 1:
            raise OdsBaseRollupError(f"Hotfix dataset has ambiguous roles: {dataset_id}")
        parts.sort(key=lambda item: str(item["path"]))
        result.append(
            {
                "dataset_id": dataset_id,
                "role": roles.pop(),
                "part_count": len(parts),
                "size_bytes": sum(int(item["size_bytes"]) for item in parts),
                "parts": parts,
                "quality": {
                    "quality_source": "verified immutable fill-only Hotfix",
                    "hotfix_id": hotfix_id,
                },
            }
        )
    return result


def _source_identity(
    manifest: Mapping[str, Any],
    content: Mapping[str, Any],
    files: Sequence[Mapping[str, Any]],
    manifest_sha: str,
    *,
    package_id_key: str,
    package_id: str,
) -> dict[str, Any]:
    return {
        package_id_key: package_id,
        "manifest_sha256": manifest_sha,
        "content_digest": str(manifest["content_digest"]),
        "through_date": str(
            content.get("through_date")
            or _mapping(manifest.get("scope"), "manifest.scope").get("through_date")
        ),
        "built_at": str(manifest.get("built_at")),
        "code_revision": str(manifest.get("code_revision")),
        "file_count": len(files),
        "size_bytes": sum(int(item["size_bytes"]) for item in files),
        "source_file_inventory_digest": _canonical_digest(files),
    }


def build_ods_base_rollup(
    *,
    parent_base: Path,
    hotfix_bundle: Path,
    output: Path,
    bundle_id: str,
    policy: OdsBasePolicy,
    code_revision: str,
    built_at: str,
    expected_parent_manifest_sha256: str,
    expected_parent_content_digest: str,
    expected_hotfix_manifest_sha256: str,
    expected_hotfix_content_digest: str,
) -> OdsBaseBundle:
    """Build a deterministic, hard-linked, self-contained Base rollup."""

    parent_base = parent_base.resolve()
    hotfix_bundle = hotfix_bundle.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite ODS Base rollup: {output}")
    if output.name != bundle_id or _SAFE_ID.fullmatch(bundle_id) is None:
        raise OdsBaseRollupError("rollup output path and bundle identity differ")
    if _REVISION.fullmatch(code_revision) is None:
        raise OdsBaseRollupError("code_revision must be a full Git object id")
    normalized_built_at = _normalize_built_at(built_at)

    parent_manifest, parent_content, parent_files, parent_manifest_sha = _load_parent(
        parent_base,
        policy=policy,
        expected_manifest_sha256=expected_parent_manifest_sha256,
        expected_content_digest=expected_parent_content_digest,
    )
    parent_identity = {
        "bundle_id": str(parent_manifest["bundle_id"]),
        "manifest_sha256": parent_manifest_sha,
        "content_digest": str(parent_manifest["content_digest"]),
        "through_date": str(parent_content["through_date"]),
    }
    hotfix_manifest, hotfix_content, hotfix_files, hotfix_manifest_sha = _load_hotfix(
        hotfix_bundle,
        parent_identity=parent_identity,
        expected_manifest_sha256=expected_hotfix_manifest_sha256,
        expected_content_digest=expected_hotfix_content_digest,
    )
    hotfix_id = str(hotfix_manifest["hotfix_id"])

    parent_paths = {str(item["path"]) for item in parent_files}
    hotfix_paths = {str(item["path"]) for item in hotfix_files}
    overlap = sorted(parent_paths & hotfix_paths)
    if overlap:
        raise OdsBaseRollupError(f"parent and Hotfix target paths overlap: {overlap[:5]}")
    parent_dataset_ids = {str(item["dataset_id"]) for item in parent_files}
    hotfix_dataset_ids = {str(item["dataset_id"]) for item in hotfix_files}
    dataset_overlap = sorted(parent_dataset_ids & hotfix_dataset_ids)
    if dataset_overlap:
        raise OdsBaseRollupError(
            f"parent and Hotfix dataset identities overlap: {dataset_overlap[:5]}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(tempfile.mkdtemp(prefix=f".{bundle_id}.", dir=output.parent))
    staging = staging_parent / bundle_id
    staging.mkdir()
    try:
        combined_files: list[dict[str, Any]] = []
        for source_root, source_label, source_files in (
            (parent_base, str(parent_manifest["bundle_id"]), parent_files),
            (hotfix_bundle, hotfix_id, hotfix_files),
        ):
            for item in source_files:
                relative = str(item["path"])
                source = _inside(source_root, relative, "source.files.path")
                target = _inside(staging, relative, "rollup.files.path")
                _link_member(source, target, expected_size=int(item["size_bytes"]))
                combined_files.append(
                    _rollup_entry(
                        item,
                        source_root=source_root,
                        source_label=source_label,
                    )
                )
        combined_files.sort(key=lambda item: str(item["path"]))
        rollup_file_map = {str(item["path"]): item for item in combined_files}
        parent_rollup_files = [rollup_file_map[str(item["path"])] for item in parent_files]
        hotfix_rollup_files = [rollup_file_map[str(item["path"])] for item in hotfix_files]
        parent_rollup_map = {str(item["path"]): item for item in parent_rollup_files}
        parent_source_map = {str(item["path"]): item for item in parent_files}
        datasets = _parent_datasets(
            parent_content.get("datasets"),
            source_files=parent_source_map,
            rollup_files=parent_rollup_map,
        )
        datasets.extend(_hotfix_datasets(hotfix_rollup_files, hotfix_id))
        datasets.sort(key=lambda item: str(item["dataset_id"]))

        parent_source = _source_identity(
            parent_manifest,
            parent_content,
            parent_files,
            parent_manifest_sha,
            package_id_key="bundle_id",
            package_id=str(parent_manifest["bundle_id"]),
        )
        parent_source["rollup_file_inventory_digest"] = _canonical_digest(parent_rollup_files)
        parent_source["parent_base"] = copy.deepcopy(parent_content.get("parent_base"))
        if parent_content.get("rollup") is not None:
            parent_source["rollup"] = copy.deepcopy(parent_content["rollup"])
            parent_source["rollup_digest"] = _canonical_digest(parent_content["rollup"])
        hotfix_source = _source_identity(
            hotfix_manifest,
            _mapping(hotfix_manifest.get("scope"), "hotfix.scope"),
            hotfix_files,
            hotfix_manifest_sha,
            package_id_key="hotfix_id",
            package_id=hotfix_id,
        )
        hotfix_source["rollup_file_inventory_digest"] = _canonical_digest(hotfix_rollup_files)
        hotfix_source["parent"] = copy.deepcopy(hotfix_manifest["parent"])
        hotfix_source["scope"] = copy.deepcopy(hotfix_manifest["scope"])
        hotfix_source["application"] = copy.deepcopy(hotfix_manifest["application"])
        hotfix_source["coverage"] = copy.deepcopy(hotfix_content["coverage"])

        content = copy.deepcopy(dict(parent_content))
        content["parent_base"] = parent_identity
        content["files"] = combined_files
        content["datasets"] = datasets
        content["source_inventory_selection_digest"] = _selection_digest(
            source_inventory=_sequence(content.get("source_inventory"), "source_inventory"),
            release_lineage=_sequence(content.get("daily_releases"), "daily_releases"),
            files=combined_files,
            scope_gaps=_sequence(content.get("scope_gaps"), "scope_gaps"),
        )
        chained = parent_content.get("rollup") is not None
        application = _mapping(hotfix_manifest.get("application"), "hotfix.application")
        use_chained_contract = chained or application.get("mode") != "fill_absent_keys_only"
        content["rollup"] = {
            "schema_version": 2 if use_chained_contract else 1,
            "mode": (
                "self_contained_chained_rollup"
                if use_chained_contract
                else "self_contained_fill_only_rollup"
            ),
            "source_paths_preserved": True,
            "source_packages_required_after_build": False,
            "parent": parent_source,
            "hotfixes": [hotfix_source],
            "precedence": _direct_hotfix_precedence(application),
            "validated_overlap_mismatches": 0,
            "combined_file_inventory_digest": _canonical_digest(combined_files),
        }
        manifest = {
            "schema_version": 1,
            "kind": "quanthus_ods_base_bundle",
            "bundle_id": bundle_id,
            "built_at": normalized_built_at,
            "code_revision": code_revision,
            "content_digest": _canonical_digest(content),
            "content": content,
            "storage_reuse": {
                "method": "hard_link",
                "file_count": len(combined_files),
                "size_bytes": sum(int(item["size_bytes"]) for item in combined_files),
                "sources": [
                    {
                        "package_id": str(parent_manifest["bundle_id"]),
                        "file_count": len(parent_files),
                        "size_bytes": sum(int(item["size_bytes"]) for item in parent_files),
                    },
                    {
                        "package_id": hotfix_id,
                        "file_count": len(hotfix_files),
                        "size_bytes": sum(int(item["size_bytes"]) for item in hotfix_files),
                    },
                ],
            },
        }
        _write_json_new(staging / "manifest.json", manifest)
        with (staging / "READY").open("x", encoding="ascii") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(f"{_sha256(staging / 'manifest.json')}\n")
            stream.flush()
            os.fsync(stream.fileno())
        for path in sorted(staging.rglob("*")):
            if path.is_dir():
                path.chmod(0o750)
        staging.chmod(0o750)
        result = verify_ods_base_rollup(staging, policy=policy, deep=False)
        os.replace(staging, output)
        staging_parent.rmdir()
        return OdsBaseBundle(
            root=output,
            bundle_id=result.bundle_id,
            manifest_sha256=result.manifest_sha256,
            content_digest=result.content_digest,
            through_date=result.through_date,
            increment_start=result.increment_start,
            source_node=result.source_node,
            size_bytes=result.size_bytes,
            file_count=result.file_count,
        )
    except BaseException:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise


def republish_ods_base_identity(
    *,
    source_bundle: Path,
    output: Path,
    bundle_id: str,
    policy: OdsBasePolicy,
    code_revision: str,
    built_at: str,
    expected_source_manifest_sha256: str,
    expected_source_content_digest: str,
) -> OdsBaseBundle:
    """Republish one verified rollup under a new immutable bundle identity.

    This is deliberately a content-preserving operation: every governed member is
    hard-linked from the source bundle and the canonical content is unchanged.
    """

    source_bundle = source_bundle.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite ODS Base republish: {output}")
    if output.name != bundle_id or _SAFE_ID.fullmatch(bundle_id) is None:
        raise OdsBaseRollupError("republish output path and bundle identity differ")
    if _REVISION.fullmatch(code_revision) is None:
        raise OdsBaseRollupError("code_revision must be a full Git object id")
    if source_bundle.parent != output.parent:
        raise OdsBaseRollupError("identity republish must remain under one Base root")
    normalized_built_at = _normalize_built_at(built_at)
    source = verify_ods_base_rollup(source_bundle, policy=policy, deep=False)
    if source.manifest_sha256 != _digest(
        expected_source_manifest_sha256, "expected source manifest"
    ) or source.content_digest != _digest(
        expected_source_content_digest, "expected source content"
    ):
        raise OdsBaseRollupError("identity republish source differs from expected identity")
    source_manifest, source_manifest_sha = _read_envelope(source_bundle)
    content = copy.deepcopy(dict(_mapping(source_manifest.get("content"), "content")))
    files = _file_inventory(source_bundle, content.get("files"), source_label="rollup")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(tempfile.mkdtemp(prefix=f".{bundle_id}.", dir=output.parent))
    staging = staging_parent / bundle_id
    staging.mkdir()
    try:
        for item in files:
            relative = str(item["path"])
            _link_member(
                _inside(source_bundle, relative, "source.files.path"),
                _inside(staging, relative, "republish.files.path"),
                expected_size=int(item["size_bytes"]),
            )
        manifest = copy.deepcopy(source_manifest)
        manifest.update(
            {
                "bundle_id": bundle_id,
                "built_at": normalized_built_at,
                "code_revision": code_revision,
                "content_digest": source.content_digest,
                "storage_reuse": {
                    "method": "hard_link",
                    "file_count": len(files),
                    "size_bytes": sum(int(item["size_bytes"]) for item in files),
                    "sources": [
                        {
                            "bundle_id": source.bundle_id,
                            "manifest_sha256": source_manifest_sha,
                            "content_digest": source.content_digest,
                            "file_count": len(files),
                            "size_bytes": sum(int(item["size_bytes"]) for item in files),
                        }
                    ],
                },
            }
        )
        _write_json_new(staging / "manifest.json", manifest)
        with (staging / "READY").open("x", encoding="ascii") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(f"{_sha256(staging / 'manifest.json')}\n")
            stream.flush()
            os.fsync(stream.fileno())
        for path in sorted(staging.rglob("*")):
            if path.is_dir():
                path.chmod(0o750)
        staging.chmod(0o750)
        result = verify_ods_base_rollup(staging, policy=policy, deep=False)
        if result.content_digest != source.content_digest:
            raise OdsBaseRollupError("identity republish changed canonical content")
        os.replace(staging, output)
        staging_parent.rmdir()
        return OdsBaseBundle(
            root=output,
            bundle_id=result.bundle_id,
            manifest_sha256=result.manifest_sha256,
            content_digest=result.content_digest,
            through_date=result.through_date,
            increment_start=result.increment_start,
            source_node=result.source_node,
            size_bytes=result.size_bytes,
            file_count=result.file_count,
        )
    except BaseException:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise


def _verify_rollup_contract(
    root: Path, manifest: Mapping[str, Any], content: Mapping[str, Any]
) -> None:
    files = [
        dict(_mapping(item, "content.files[]"))
        for item in _sequence(content.get("files"), "content.files")
    ]
    paths = [str(item.get("path")) for item in files]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise OdsBaseRollupError("rollup file inventory must be unique and path-sorted")
    rollup = _mapping(content.get("rollup"), "content.rollup")
    schema_version = rollup.get("schema_version")
    expected_modes: dict[object, str] = {
        1: "self_contained_fill_only_rollup",
        2: "self_contained_chained_rollup",
    }
    expected_mode = expected_modes.get(schema_version)
    if (
        expected_mode is None
        or rollup.get("mode") != expected_mode
        or rollup.get("source_paths_preserved") is not True
        or rollup.get("source_packages_required_after_build") is not False
        or rollup.get("validated_overlap_mismatches") != 0
        or rollup.get("combined_file_inventory_digest") != _canonical_digest(files)
    ):
        raise OdsBaseRollupError("rollup contract or combined inventory binding is invalid")
    parent = _mapping(rollup.get("parent"), "content.rollup.parent")
    hotfixes = _sequence(rollup.get("hotfixes"), "content.rollup.hotfixes")
    if len(hotfixes) != 1:
        raise OdsBaseRollupError("rollup must bind exactly one Hotfix")
    hotfix = _mapping(hotfixes[0], "content.rollup.hotfixes[]")
    parent_id = _text(parent, "bundle_id")
    hotfix_id = _text(hotfix, "hotfix_id")
    for source, source_id, field in (
        (parent, parent_id, "parent"),
        (hotfix, hotfix_id, "hotfix"),
    ):
        _digest(source.get("manifest_sha256"), f"rollup.{field}.manifest_sha256")
        _digest(source.get("content_digest"), f"rollup.{field}.content_digest")
        _digest(
            source.get("source_file_inventory_digest"),
            f"rollup.{field}.source_file_inventory_digest",
        )
        selected = [item for item in files if item.get("rollup_source") == source_id]
        if (
            len(selected) != source.get("file_count")
            or sum(int(item["size_bytes"]) for item in selected) != source.get("size_bytes")
            or _canonical_digest(selected) != source.get("rollup_file_inventory_digest")
        ):
            raise OdsBaseRollupError(f"rollup {field} file-set binding is invalid")
    labels = {str(item.get("rollup_source")) for item in files}
    if labels != {parent_id, hotfix_id}:
        raise OdsBaseRollupError("rollup file source labels are invalid")
    if schema_version == 1:
        if rollup.get("precedence") != OVERLAY_PRECEDENCE or "rollup" in parent:
            raise OdsBaseRollupError("legacy rollup precedence or parent lineage is invalid")
    else:
        parent_rollup = _mapping(parent.get("rollup"), "rollup.parent.rollup")
        if parent.get("rollup_digest") != _canonical_digest(parent_rollup) or parent_rollup.get(
            "schema_version"
        ) not in {1, 2}:
            raise OdsBaseRollupError("chained rollup parent lineage is invalid")
    parent_identity = {
        "bundle_id": parent_id,
        "manifest_sha256": parent["manifest_sha256"],
        "content_digest": parent["content_digest"],
        "through_date": parent["through_date"],
    }
    if content.get("parent_base") != parent_identity or hotfix.get("parent") != parent_identity:
        raise OdsBaseRollupError("rollup parent lineage is inconsistent")
    application = _mapping(hotfix.get("application"), "rollup.hotfix.application")
    coverage = _mapping(hotfix.get("coverage"), "rollup.hotfix.coverage")
    if application.get("mode") == "fill_absent_keys_only":
        daily = _mapping(coverage.get("daily"), "rollup.hotfix.coverage.daily")
        valid_precedence = rollup.get("precedence") == OVERLAY_PRECEDENCE
        valid_coverage = daily.get("overlap_mismatches") == 0
    elif application.get("mode") == "add_absent_business_keys_only":
        calendar = _mapping(coverage.get("calendar"), "rollup.hotfix.coverage.calendar")
        valid_precedence = rollup.get("precedence") == _direct_hotfix_precedence(application)
        valid_coverage = (
            application == CALENDAR_HISTORY_APPLICATION
            and calendar.get("overlap_business_keys") == 0
            and calendar.get("conflicting_business_keys") == 0
        )
    else:
        valid_precedence = False
        valid_coverage = False
    if (
        application.get("parent_mutation") is not False
        or application.get("current_switch") is not False
        or not valid_precedence
        or not valid_coverage
    ):
        raise OdsBaseRollupError("rollup Hotfix precedence evidence is invalid")

    datasets = [
        dict(_mapping(item, "content.datasets[]"))
        for item in _sequence(content.get("datasets"), "content.datasets")
    ]
    dataset_ids = [str(item.get("dataset_id")) for item in datasets]
    if dataset_ids != sorted(dataset_ids) or len(dataset_ids) != len(set(dataset_ids)):
        raise OdsBaseRollupError("rollup datasets must be unique and identity-sorted")
    by_path = {str(item["path"]): item for item in files}
    seen_parts: set[str] = set()
    for dataset in datasets:
        dataset_id = _text(dataset, "dataset_id")
        parts = [
            dict(_mapping(item, f"datasets.{dataset_id}.parts[]"))
            for item in _sequence(dataset.get("parts"), f"datasets.{dataset_id}.parts")
        ]
        if dataset.get("part_count") != len(parts) or dataset.get("size_bytes") != sum(
            int(item["size_bytes"]) for item in parts
        ):
            raise OdsBaseRollupError(f"rollup dataset summary is invalid: {dataset_id}")
        for part in parts:
            path = str(part.get("path"))
            if path in seen_parts or part != by_path.get(path):
                raise OdsBaseRollupError(f"rollup dataset part inventory drift: {path}")
            seen_parts.add(path)
    if seen_parts != set(by_path):
        raise OdsBaseRollupError("rollup datasets do not cover every governed file")
    selection = _selection_digest(
        source_inventory=_sequence(content.get("source_inventory"), "source_inventory"),
        release_lineage=_sequence(content.get("daily_releases"), "daily_releases"),
        files=files,
        scope_gaps=_sequence(content.get("scope_gaps"), "scope_gaps"),
    )
    if content.get("source_inventory_selection_digest") != selection:
        raise OdsBaseRollupError("rollup source selection digest is invalid")


def verify_ods_base_rollup(
    root: Path, *, policy: OdsBasePolicy | None = None, deep: bool = False
) -> OdsBaseBundle:
    """Verify rollup structure, and optionally every payload checksum/schema."""

    root = root.resolve()
    manifest, manifest_sha = _read_envelope(root)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "quanthus_ods_base_bundle"
        or manifest.get("bundle_id") != root.name
        or _SAFE_ID.fullmatch(str(manifest.get("bundle_id", ""))) is None
        or _REVISION.fullmatch(str(manifest.get("code_revision", ""))) is None
    ):
        raise OdsBaseRollupError("unsupported ODS Base rollup manifest")
    content = _mapping(manifest.get("content"), "content")
    content_digest = _canonical_digest(content)
    if content_digest != manifest.get("content_digest"):
        raise OdsBaseRollupError("ODS Base rollup content digest is invalid")
    if content.get("authority") != _AUTHORITY:
        raise OdsBaseRollupError("ODS Base rollup authority boundary is invalid")
    if policy is not None and (
        content.get("contract_id") != policy.contract_id
        or content.get("policy_sha256") != policy.digest
    ):
        raise OdsBaseRollupError("ODS Base rollup differs from the active policy")
    files = _file_inventory(root, content.get("files"), source_label="rollup")
    _verify_rollup_contract(root, manifest, content)
    try:
        through_date = date.fromisoformat(str(content["through_date"]))
        increment_start = date.fromisoformat(str(content["increment_start"]))
    except (KeyError, ValueError) as exc:
        raise OdsBaseRollupError("rollup date binding is invalid") from exc
    result = OdsBaseBundle(
        root=root,
        bundle_id=str(manifest["bundle_id"]),
        manifest_sha256=manifest_sha,
        content_digest=content_digest,
        through_date=through_date,
        increment_start=increment_start,
        source_node=_text(content, "source_node"),
        size_bytes=sum(int(item["size_bytes"]) for item in files),
        file_count=len(files),
    )
    if deep:
        verified = verify_ods_base_bundle(root, policy=policy, deep=True)
        if verified != result:
            raise OdsBaseRollupError("standard ODS Base verification summary differs")
    return result


def advance_ods_base_rollup_current(
    *,
    bundle: Path,
    current_pointer: Path,
    policy: OdsBasePolicy,
    expected_current_pointer_sha256: str,
    expected_current_bundle_id: str,
    expected_target_manifest_sha256: str,
    expected_target_content_digest: str,
    deep: bool = False,
) -> OdsBaseBundle:
    """CAS one installed ODS pointer to a previously verified rollup."""

    expected_pointer_sha = _digest(expected_current_pointer_sha256, "expected current pointer")
    expected_target_manifest = _digest(expected_target_manifest_sha256, "expected target manifest")
    expected_target_content = _digest(expected_target_content_digest, "expected target content")
    bundle = bundle.resolve()
    current_pointer = current_pointer.resolve()
    if bundle.parent != current_pointer.parent:
        raise OdsBaseRollupError("rollup bundle and BASE_CURRENT must share one base root")
    target = verify_ods_base_rollup(bundle, policy=policy, deep=deep)
    if (
        target.manifest_sha256 != expected_target_manifest
        or target.content_digest != expected_target_content
    ):
        raise OdsBaseRollupError("rollup target differs from the expected identity")

    lock_path = current_pointer.parent / ".BASE_CURRENT.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current_sha = _sha256(current_pointer)
        current = _object(current_pointer)
        already_current = (
            current.get("schema_version") == 1
            and current.get("bundle_id") == target.bundle_id
            and current.get("manifest_sha256") == target.manifest_sha256
            and current.get("content_digest") == target.content_digest
            and current.get("through_date") == target.through_date.isoformat()
        )
        if already_current:
            return target
        if current_sha != expected_pointer_sha:
            raise OdsBaseRollupError("ODS BASE_CURRENT file SHA precondition failed")
        if (
            current.get("schema_version") != 1
            or current.get("bundle_id") != expected_current_bundle_id
        ):
            raise OdsBaseRollupError("ODS BASE_CURRENT bundle precondition failed")
        current_relative = _safe_relative(current.get("bundle_path"), "BASE_CURRENT.bundle_path")
        current_root = current_pointer.parent.joinpath(*current_relative.parts).resolve()
        if current_root.parent != current_pointer.parent:
            raise OdsBaseRollupError("ODS BASE_CURRENT escapes its base root")
        current_manifest_sha = _sha256(current_root / "manifest.json")
        if current_manifest_sha != current.get("manifest_sha256"):
            raise OdsBaseRollupError("ODS BASE_CURRENT no longer binds its manifest")
        before = current_pointer.stat()
        relative = os.path.relpath(target.root, start=current_pointer.parent)
        _atomic_json(
            current_pointer,
            {
                "schema_version": 1,
                "bundle_id": target.bundle_id,
                "bundle_path": Path(relative).as_posix(),
                "manifest_sha256": target.manifest_sha256,
                "content_digest": target.content_digest,
                "through_date": target.through_date.isoformat(),
            },
        )
        os.chown(current_pointer, before.st_uid, before.st_gid)
    return target
