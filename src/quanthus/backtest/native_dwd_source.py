"""Native Base 2.3 source and exact legacy correction continuation.

The image and legacy correction retain their sealed identities. A separate,
hash-bound continuation proves state preimages and preserves normalized actions;
it does not reparent the correction or grant another research purpose.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.securities_warehouse.native_base23_reader import (
    KIND,
    NativeBase23,
    NativeBase23Error,
    read_native_tables,
)
from quanthus.policy import ConfigurationError, find_project_root

LAYOUT = "base23-native-v1"
MATERIALIZER_VERSION = "direct-dwd-native-base23-v1"
CANONICAL_RECEIPT = "config/p4-direct-dwd-canonical-projection.base23.v1.json"
TABLES = (
    "dwd_listing",
    "dwd_security",
    "dwd_trading_calendar",
    "dwd_security_bar",
    "dwd_tradability_state",
    "dwd_price_limit_regime",
    "dwd_adjustment_factor",
    "dwd_company_industry_membership",
    "dwd_industry_node",
)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return "sha256:" + result.hexdigest()


def object_file(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"native source metadata is unreadable: {path.name}") from exc
    if not isinstance(result, dict):
        raise ConfigurationError("native source metadata must be an object")
    return result


def member(root: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise ConfigurationError("native source member path is invalid")
    part = PurePosixPath(relative)
    if part.is_absolute() or ".." in part.parts or not part.parts:
        raise ConfigurationError("native source member path escapes its package")
    cursor = root
    for name in part.parts:
        cursor = cursor / name
        if cursor.is_symlink():
            raise ConfigurationError("native source member contains a symlink")
    if not cursor.is_file() or not cursor.resolve().is_relative_to(root.resolve()):
        raise ConfigurationError("native source member is missing or outside its package")
    return cursor


def is_native(config: Any) -> bool:
    return config.materialization.get("source_layout") == LAYOUT


def native_options(config: Any) -> Mapping[str, Any]:
    options = config.materialization.get("native_base23")
    if not is_native(config) or not isinstance(options, Mapping):
        raise ConfigurationError("native provider has no versioned source contract")
    if (
        options.get("purpose") != "development_retrospective_input_v1"
        or config.scope.partition != "development"
        or config.scope.reader_role != "development_research"
        or config.materialization.get("per_decision_publication_proven") is not False
        or config.scope.decision_start < date(2012, 1, 4)
        or config.scope.decision_end > date(2021, 12, 30)
        or config.scope.execution_end > date(2021, 12, 31)
        or config.history_start is None
        or config.history_start < date(2011, 1, 1)
    ):
        raise ConfigurationError("native source purpose or visibility differs")
    return options


def physical_paths(root: Path, table: str) -> tuple[Path, ...]:
    """Resolve native inventory after authority validation, retaining sealed metadata."""
    manifest_path = member(root, "manifest.json")
    manifest = object_file(manifest_path)
    if manifest.get("kind") != KIND:
        raise ConfigurationError("source is not a native Base 2.3 image")
    if member(root, "READY").read_text().strip() != digest(manifest_path):
        raise ConfigurationError("native source READY differs")
    materialization_path = member(root, "MATERIALIZATION.json")
    if digest(materialization_path) != manifest.get("materialization_sha256"):
        raise ConfigurationError("native source layout hash differs")
    materialization = object_file(materialization_path)
    files = materialization.get("layout", {}).get(table, {}).get("files")
    if not isinstance(files, list) or not files:
        raise ConfigurationError(f"native source table is absent: {table}")
    paths = []
    for record in files:
        if not isinstance(record, dict) or record.get("table") != table:
            raise ConfigurationError("native source inventory table differs")
        relative = record.get("path")
        if not isinstance(relative, str) or not relative.startswith(f"payload/{table}/"):
            raise ConfigurationError("native source member belongs to another table")
        path = member(root, relative)
        if path.stat().st_size != record.get("size_bytes"):
            raise ConfigurationError("native source member size differs")
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise ConfigurationError("native source inventory repeats a member")
    return tuple(sorted(paths))


def verify_source(config: Any) -> tuple[NativeBase23, dict[str, Any], dict[str, Any]]:
    """Verify source bytes for qualification or consumption; no consumer grant here."""
    options = native_options(config)
    release = options.get("release")
    if not isinstance(release, Mapping):
        raise ConfigurationError("native source release binding is absent")
    try:
        native = read_native_tables(config.dwd_root, release, TABLES)
    except (OSError, NativeBase23Error) as exc:
        raise ConfigurationError("native replay source verification failed") from exc
    actual = {
        "package_id": native.binding["package_id"],
        "through_date": native.binding["business_through"],
        "content_digest": native.binding["content_digest"],
        "manifest_sha256": native.binding["manifest_sha256"],
        "relationship_to_replay_source": "direct-job-local-source",
    }
    if actual != dict(config.dwd_identity):
        raise ConfigurationError("native replay source identity differs")
    if config.correction_root is None or config.correction_identity is None:
        raise ConfigurationError("native replay correction attachment is absent")
    correction_path = member(config.correction_root, "manifest.json")
    correction = object_file(correction_path)
    observed_correction = {
        "correction_id": correction.get("correction_id"),
        "content_digest": correction.get("content_digest"),
        "manifest_sha256": digest(correction_path),
    }
    if observed_correction != dict(config.correction_identity):
        raise ConfigurationError("native correction attachment identity differs")
    if member(config.correction_root, "READY").read_text().strip() != digest(correction_path):
        raise ConfigurationError("native correction attachment READY differs")
    inventory = correction.get("files")
    if not isinstance(inventory, list) or not inventory:
        raise ConfigurationError("native correction attachment inventory is absent")
    for record in inventory:
        path = member(config.correction_root, record.get("path"))
        if path.stat().st_size != record.get("size_bytes") or digest(path) != record.get("sha256"):
            raise ConfigurationError("native correction attachment member differs")
    continuation = options.get("continuation")
    if not isinstance(continuation, Mapping):
        raise ConfigurationError("native correction continuation binding is absent")
    receipt_path = member(config.correction_root.parent, continuation.get("path"))
    if digest(receipt_path) != continuation.get("sha256"):
        raise ConfigurationError("native correction continuation checksum differs")
    receipt = object_file(receipt_path)
    if (
        receipt.get("schema_version") != "quanthus.native-correction-continuation.v1"
        or receipt.get("purpose") != "development_retrospective_input_v1"
        or receipt.get("native_manifest_sha256") != actual["manifest_sha256"]
        or receipt.get("native_materialization_sha256") != native.binding["materialization_sha256"]
        or receipt.get("legacy_correction") != observed_correction
        or receipt.get("legacy_parent") != correction.get("parent")
        or receipt.get("normalization_bytes_unchanged") is not True
        or receipt.get("raw_action_term_delete_overlay_applied") is not False
    ):
        raise ConfigurationError("native correction continuation authority differs")
    proof_path = member(config.correction_root.parent, receipt.get("state_preimage_proof_path"))
    if digest(proof_path) != receipt.get("state_preimage_proof_sha256"):
        raise ConfigurationError("native state preimage proof checksum differs")
    proof = object_file(proof_path)
    totals = proof.get("state_total", {})
    if (
        proof.get("status") != "COMPARISON_COMPLETE_NOT_CONSUMER_ADMISSION"
        or totals.get("rows") != receipt.get("state_replacement_rows")
        or totals.get("native_equals_old_preimage") != totals.get("rows")
        or any(
            totals.get(key) != 0
            for key in ("missing_native_key", "missing_old_key", "native_differs_from_both")
        )
    ):
        raise ConfigurationError("native state preimage continuation is not proven")
    mapping = proof.get("state_byte_equivalence", {}).get("mapping")
    if not isinstance(mapping, list) or not mapping:
        raise ConfigurationError("native state byte mapping is absent")
    materialization = object_file(config.dwd_root / "MATERIALIZATION.json")
    states = {
        entry["path"]: entry
        for entry in materialization["layout"]["dwd_tradability_state"]["files"]
    }
    for row in mapping:
        entry = states.get(row.get("native_path"), {})
        if entry.get("sha256") != "sha256:" + str(row.get("sha256")) or entry.get(
            "size_bytes"
        ) != row.get("bytes"):
            raise ConfigurationError("native state preimage member binding differs")
    for path, expected in (
        (config.benchmark_path, config.benchmark_sha256),
        (config.industry_path, config.industry_sha256),
    ):
        if path is None or digest(path) != expected:
            raise ConfigurationError("native research reference checksum differs")
    return native, observed_correction, correction


def require_canonical_admission(config: Any) -> None:
    """Require the separately generated native input receipt before normal consumption."""
    receipt = object_file(find_project_root(config.path) / CANONICAL_RECEIPT)
    if (
        receipt.get("schema_version") != "quanthus.direct-dwd-canonical-projection.v1"
        or receipt.get("materializer_version") != MATERIALIZER_VERSION
        or receipt.get("dwd_authority") != dict(config.dwd_identity)
        or receipt.get("dwd_correction") != dict(config.correction_identity)
        or receipt.get("scope", {}).get("partition") != "development"
        or receipt.get("scope", {}).get("decision_end") != "2021-12-30"
        or receipt.get("history_start") != "2011-01-01"
    ):
        raise ConfigurationError("native canonical input admission binding differs")
