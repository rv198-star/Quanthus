"""Read sealed native Base 2.3 parent references without rewriting its manifest.

This bounded reader does not replace full cold acceptance and does not grant
trading/research admission. Production use requires the separately accepted,
versioned daily-core parent-reference disposition.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

KIND = "dwd_base23_native_purpose_qualified_candidate_v1"
PARENT_TABLES = (
    "dwd_listing",
    "dwd_security",
    "dwd_trading_calendar",
    "dwd_market_index",
    "dwd_trading_venue",
)


class NativeBase23Error(ValueError):
    """A native image or its declared consumer binding is not usable."""


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except (OSError, ValueError) as exc:
        raise NativeBase23Error(f"native metadata is unreadable: {path.name}") from exc


def _member(root: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise NativeBase23Error("native member path must be a string")
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts or rel.as_posix() == ".":
        raise NativeBase23Error("unsafe native member path")
    path = root.joinpath(*rel.parts)
    cursor = root
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise NativeBase23Error("native member contains a symlink")
    if not path.resolve().is_relative_to(root):
        raise NativeBase23Error("native member escapes root")
    if not path.is_file():
        raise NativeBase23Error("native member is missing")
    return path


@dataclass(frozen=True)
class NativeBase23:
    manifest: Mapping[str, Any]
    binding: Mapping[str, Any]
    table_paths: Mapping[str, tuple[Path, ...]]

    def files(self, table: str) -> list[Path]:
        try:
            return list(self.table_paths[table])
        except KeyError as exc:
            raise NativeBase23Error(f"native parent table was not verified: {table}") from exc


def read_native_parent_references(root: Path, config: Mapping[str, Any]) -> NativeBase23:
    """Verify metadata once and physically hash the five declared parent tables."""
    return read_native_tables(root, config, PARENT_TABLES)


def read_native_tables(
    root: Path, config: Mapping[str, Any], tables: tuple[str, ...]
) -> NativeBase23:
    """Verify an explicit consumer table closure against sealed native metadata.

    This verifies source bytes, not consumer admission. The caller must separately
    validate its purpose-specific source and projection receipts.
    """
    return _read_native_tables(
        root,
        config,
        tables,
        profile=(KIND, "2.3", "p10_governance_baseline_with_disclosed_coverage", "native_base23"),
    )


def _read_native_tables(
    root: Path,
    config: Mapping[str, Any],
    tables: tuple[str, ...],
    *,
    profile: tuple[str, str, str, str],
    independent_files: bool = False,
    default_intervals: tuple[str, ...] | None = None,
    one_minute_disposition: str | None = None,
) -> NativeBase23:
    """Shared physical reader; public entry points retain exact version contracts."""
    if not tables or len(tables) != len(set(tables)):
        raise NativeBase23Error("native consumer table closure is invalid")
    kind, version, purpose, physical_format = profile
    control_fingerprints = {}
    if independent_files:
        from .base22_sources import SourceAssessmentError, _fingerprint, _read_json, physical_path

        try:
            root = physical_path(root)
        except SourceAssessmentError as exc:
            raise NativeBase23Error("native root contains a symlink") from exc
    root = root.resolve()
    manifest_path = _member(root, "manifest.json")
    if independent_files and manifest_path.stat().st_nlink != 1:
        raise NativeBase23Error("native metadata requires an independent file")
    if independent_files:
        try:
            manifest, manifest_sha, fingerprint = _read_json(manifest_path)
        except (OSError, ValueError, SourceAssessmentError) as exc:
            raise NativeBase23Error("native manifest is unreadable or changed") from exc
        control_fingerprints[manifest_path] = fingerprint
    else:
        manifest = _object(manifest_path)
        manifest_sha = _hash(manifest_path)
    expected = config.get("layers", {}).get("dwd", {})
    try:
        ready_path = _member(root, "READY")
        if independent_files:
            control_fingerprints[ready_path] = _fingerprint(ready_path)
        ready = ready_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise NativeBase23Error("native READY is unreadable") from exc
    if (
        manifest.get("kind") != kind
        or manifest.get("version") != version
        or manifest.get("purpose") != purpose
        or manifest.get("business_timezone") != "Asia/Shanghai"
        or manifest.get("business_through") != config.get("through_date")
        or default_intervals is not None
        and manifest.get("default_intervals") != list(default_intervals)
        or one_minute_disposition is not None
        and manifest.get("one_minute_disposition") != one_minute_disposition
        or ready != manifest_sha
        or manifest_sha != expected.get("manifest_sha256")
        or manifest.get("content_digest") != expected.get("content_digest")
        or manifest.get("materialization_sha256") != expected.get("materialization_sha256")
        or expected.get("format") != physical_format
        or not isinstance(expected.get("package_id"), str)
        or not expected["package_id"]
    ):
        raise NativeBase23Error("native Base does not match the versioned release binding")
    try:
        date.fromisoformat(str(manifest["business_through"]))
    except ValueError as exc:
        raise NativeBase23Error("native business cutoff is invalid") from exc
    materialization_path = _member(root, "MATERIALIZATION.json")
    if independent_files and (
        materialization_path.stat().st_nlink != 1 or _member(root, "READY").stat().st_nlink != 1
    ):
        raise NativeBase23Error("native metadata requires an independent file")
    if independent_files:
        try:
            materialization, materialization_sha, fingerprint = _read_json(materialization_path)
        except (OSError, ValueError, SourceAssessmentError) as exc:
            raise NativeBase23Error("native materialization is unreadable or changed") from exc
        control_fingerprints[materialization_path] = fingerprint
    else:
        materialization_sha = _hash(materialization_path)
        materialization = _object(materialization_path)
    if materialization_sha != manifest["materialization_sha256"]:
        raise NativeBase23Error("native MATERIALIZATION hash differs")
    if materialization.get("kind") != kind or materialization.get("purpose") != manifest["purpose"]:
        raise NativeBase23Error("native materialization identity differs")
    layout = materialization.get("layout")
    if not isinstance(layout, dict):
        raise NativeBase23Error("native table layout is absent")
    paths: dict[str, tuple[Path, ...]] = {}
    seen: set[str] = set()
    for table in tables:
        entry = layout.get(table, {})
        files = entry.get("files") if isinstance(entry, dict) else None
        if not isinstance(files, list) or not files:
            raise NativeBase23Error(f"native parent table is absent: {table}")
        table_paths = []
        rows: Counter[str] = Counter()
        for record in files:
            if not isinstance(record, dict) or record.get("table") != table:
                raise NativeBase23Error("native file table identity differs")
            relative = record.get("path")
            if not isinstance(relative, str):
                raise NativeBase23Error("native member path must be a string")
            if independent_files and PurePosixPath(relative).as_posix() != relative:
                raise NativeBase23Error("native member path is not canonical")
            path = _member(root, relative)
            if independent_files and path.stat().st_nlink != 1:
                raise NativeBase23Error("native parent requires an independent file")
            before = _fingerprint(path) if independent_files else None
            if not str(relative).startswith(f"payload/{table}/") or relative in seen:
                raise NativeBase23Error("native file inventory overlaps or names another table")
            seen.add(relative)
            count = record.get("rows")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise NativeBase23Error("native declared row count is invalid")
            if path.stat().st_size != record.get("size_bytes") or _hash(path) != record.get(
                "sha256"
            ):
                raise NativeBase23Error(f"native parent file differs: {relative}")
            if independent_files and _fingerprint(path) != before:
                raise NativeBase23Error("native parent changed during verification")
            rows[str(record.get("partition"))] += count
            table_paths.append(path)
        if dict(rows) != entry.get("expected_partition_rows"):
            raise NativeBase23Error(f"native parent partition counts differ: {table}")
        summaries = manifest.get("summaries", {}).get(table, {})
        if {key: value.get("rows") for key, value in summaries.items()} != dict(rows):
            raise NativeBase23Error(f"native sealed partition counts differ: {table}")
        physical = set()
        if independent_files:
            from .base22_sources import SourceAssessmentError, _tree

            directory = root / "payload" / table
            try:
                physical = {directory / name for name in _tree(directory)}
            except (OSError, SourceAssessmentError) as exc:
                raise NativeBase23Error("native parent physical scan failed") from exc
        else:
            for path in (root / "payload" / table).rglob("*"):
                if path.is_symlink():
                    raise NativeBase23Error("native parent directory contains a symlink")
                if path.is_file():
                    physical.add(path)
        if physical != set(table_paths):
            raise NativeBase23Error(f"native parent physical inventory differs: {table}")
        paths[table] = tuple(sorted(table_paths))
    if independent_files and any(
        _fingerprint(path) != before for path, before in control_fingerprints.items()
    ):
        raise NativeBase23Error("native control changed during verification")
    binding = {
        "package_id": expected["package_id"],
        "kind": kind,
        "manifest_sha256": manifest_sha,
        "content_digest": manifest["content_digest"],
        "materialization_sha256": manifest["materialization_sha256"],
        "business_through": manifest["business_through"],
        "governance_use": "governance_baseline",
        "source_consumer_admission": manifest.get("consumer_admission"),
    }
    return NativeBase23(manifest=manifest, binding=binding, table_paths=paths)


def require_daily_core_parent_admission(config: Mapping[str, Any]) -> None:
    disposition = config.get("daily_core_parent_reference", {})
    if (
        disposition.get("status") != "accepted"
        or disposition.get("product_scope") != "daily-core-v1"
        or disposition.get("governance_use") != "governance_baseline"
        or disposition.get("tables") != list(PARENT_TABLES)
        or disposition.get("trading_or_research_admission") is not False
        or not isinstance(disposition.get("acceptance_receipts"), list)
        or len(disposition["acceptance_receipts"]) < 2
        or any(
            not isinstance(x, str)
            or len(x) != 71
            or not x.startswith("sha256:")
            or any(c not in "0123456789abcdef" for c in x[7:])
            for x in disposition["acceptance_receipts"]
        )
        or len(set(disposition["acceptance_receipts"])) != len(disposition["acceptance_receipts"])
    ):
        raise NativeBase23Error("native daily-core parent-reference acceptance is pending")


def verify_native_prior_transition(
    *,
    native: NativeBase23,
    config: Mapping[str, Any],
    prior: Mapping[str, Any],
    prior_sha256: str,
) -> dict[str, Any] | None:
    """Bind an existing October increment without rewriting its old Base identity."""
    through = date.fromisoformat(str(native.binding["business_through"]))
    try:
        prior_date = date.fromisoformat(str(prior["market_date"]))
    except (KeyError, ValueError) as exc:
        raise NativeBase23Error("native transition prior date is invalid") from exc
    if prior_date <= through:
        raise NativeBase23Error("native transition prior does not follow the Base cutoff")
    old = prior.get("base", {})
    if old.get("manifest_sha256") == native.binding["manifest_sha256"]:
        if dict(old) != dict(native.binding):
            raise NativeBase23Error("native prior Base binding differs")
        return None
    accepted = config.get("daily_core_transition", {}).get("accepted_prior_manifests", {})
    expected = accepted.get(prior_sha256, {})
    if (
        expected.get("market_date") != prior_date.isoformat()
        or expected.get("base_manifest_sha256") != old.get("manifest_sha256")
        or not expected.get("base_manifest_sha256")
    ):
        raise NativeBase23Error("cross-Base daily prior lacks an exact versioned transition")
    return {
        "prior_manifest_sha256": prior_sha256,
        "prior_base_manifest_sha256": old["manifest_sha256"],
        "new_base_manifest_sha256": native.binding["manifest_sha256"],
        "new_base_through": through.isoformat(),
        "prior_market_date": prior_date.isoformat(),
        "prior_bytes_and_lineage": "unchanged",
    }
