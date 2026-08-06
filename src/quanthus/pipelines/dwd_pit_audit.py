"""Read-only historical point-in-time audit for sealed domain DWD Base packages."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.securities_warehouse.domain_base import (
    DomainBaseError,
    verify_domain_base,
)


class DwdPitAuditError(RuntimeError):
    """Raised when a DWD PIT audit request or policy violates its contract."""


_ALLOWED_DISPOSITIONS = {
    "build_timestamp_substituted",
    "ambiguous_source_insufficient",
    "source_derived_historical",
    "forward_operational",
}
_ALLOWED_ODS_SUFFICIENCY = {
    "reconstructable_existing_ods",
    "partially_reconstructable_fail_closed_remainder",
    "requires_additive_evidence_or_fail_closed",
    "forward_only_not_historical",
}
_ALLOWED_MODES = {"full", "manifest-only"}
_LINEAGE_FIELDS = {
    "record_source",
    "source_record_id",
    "source_revision",
    "source_artifact_sha256",
    "knowledge_from",
    "knowledge_to",
}


@dataclass(frozen=True)
class DwdPitTablePolicy:
    """One table's code disposition and historical reconstruction assessment."""

    table: str
    disposition: str
    historical_pit_authority: str
    reference_field: str
    ods_sufficiency: str
    builder_path: str
    knowledge_rule: str
    evidence: tuple[str, ...]
    remediation_note: str


@dataclass(frozen=True)
class DwdPitAuditPolicy:
    """Validated machine-readable policy for one sealed DWD release audit."""

    path: Path
    policy_id: str
    policy_version: str
    policy_sha256: str
    package_id: str
    manifest_sha256: str
    content_digest: str
    through_date: date
    required_mirror_labels: tuple[str, ...]
    development_start: date
    development_end: date
    tables: tuple[DwdPitTablePolicy, ...]


@dataclass(frozen=True)
class DwdModelTable:
    """The model fields required to locate and scan one DWD table."""

    name: str
    domain: str
    time_profile: str
    allowed_fields: frozenset[str]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdPitAuditError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdPitAuditError(f"{label} must be a JSON object: {path}")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DwdPitAuditError(f"{field} must be non-empty text")
    return value.strip()


def _date(value: object, field: str) -> date:
    try:
        return date.fromisoformat(_text(value, field))
    except ValueError as exc:
        raise DwdPitAuditError(f"{field} must be an ISO date") from exc


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise DwdPitAuditError(f"{field} must be an array")
    values = tuple(_text(item, field) for item in value)
    if not values or len(values) != len(set(values)):
        raise DwdPitAuditError(f"{field} must contain unique values")
    return values


def load_dwd_model_tables(path: Path) -> dict[str, DwdModelTable]:
    """Load the authoritative 33-table model and its time-profile fields."""

    raw = _object(path, "DWD model")
    raw_profiles = raw.get("time_profiles")
    raw_tables = raw.get("tables")
    if not isinstance(raw_profiles, Mapping) or not isinstance(raw_tables, list):
        raise DwdPitAuditError("DWD model lacks time_profiles or tables")
    profiles: dict[str, frozenset[str]] = {}
    for profile_name, fields in raw_profiles.items():
        if not isinstance(profile_name, str) or not isinstance(fields, Mapping):
            raise DwdPitAuditError("DWD model time profile is invalid")
        profiles[profile_name] = frozenset(str(field) for field in fields)

    result: dict[str, DwdModelTable] = {}
    for raw_table in raw_tables:
        if not isinstance(raw_table, Mapping):
            raise DwdPitAuditError("DWD model table row is invalid")
        name = _text(raw_table.get("name"), "tables.name")
        domain = _text(raw_table.get("domain"), f"{name}.domain")
        profile = _text(raw_table.get("time_profile"), f"{name}.time_profile")
        raw_columns = raw_table.get("columns")
        if profile not in profiles or not isinstance(raw_columns, Mapping):
            raise DwdPitAuditError(f"DWD model table fields are invalid: {name}")
        if name in result:
            raise DwdPitAuditError(f"DWD model has duplicate table: {name}")
        result[name] = DwdModelTable(
            name=name,
            domain=domain,
            time_profile=profile,
            allowed_fields=frozenset(raw_columns) | profiles[profile] | _LINEAGE_FIELDS,
        )
    if len(result) != 33:
        raise DwdPitAuditError(f"DWD model must contain 33 tables, found {len(result)}")
    return result


def load_dwd_pit_audit_policy(
    path: Path, *, model_path: Path
) -> tuple[DwdPitAuditPolicy, dict[str, DwdModelTable]]:
    """Load and cross-check the PIT audit policy against the DWD model."""

    raw = _object(path, "DWD PIT audit policy")
    if raw.get("schema_version") != 1 or raw.get("kind") != "dwd_pit_semantics_audit_policy":
        raise DwdPitAuditError("DWD PIT audit policy identity is invalid")
    model_tables = load_dwd_model_tables(model_path)
    release = raw.get("base_release")
    window = raw.get("development_window")
    raw_tables = raw.get("tables")
    if not isinstance(release, Mapping) or not isinstance(window, Mapping):
        raise DwdPitAuditError("DWD PIT audit policy release/window is invalid")
    if not isinstance(raw_tables, list):
        raise DwdPitAuditError("DWD PIT audit policy tables must be an array")

    tables: list[DwdPitTablePolicy] = []
    observed: set[str] = set()
    for raw_table in raw_tables:
        if not isinstance(raw_table, Mapping):
            raise DwdPitAuditError("DWD PIT audit policy table row is invalid")
        table = _text(raw_table.get("table"), "tables.table")
        disposition = _text(raw_table.get("disposition"), f"{table}.disposition")
        authority = _text(
            raw_table.get("historical_pit_authority"), f"{table}.historical_pit_authority"
        )
        reference_field = _text(raw_table.get("reference_field"), f"{table}.reference_field")
        ods_sufficiency = _text(
            raw_table.get("ods_sufficiency"), f"{table}.ods_sufficiency"
        )
        if table in observed or table not in model_tables:
            raise DwdPitAuditError(f"policy table is duplicate or unknown: {table}")
        if disposition not in _ALLOWED_DISPOSITIONS:
            raise DwdPitAuditError(f"unsupported disposition for {table}: {disposition}")
        if ods_sufficiency not in _ALLOWED_ODS_SUFFICIENCY:
            raise DwdPitAuditError(
                f"unsupported ODS sufficiency for {table}: {ods_sufficiency}"
            )
        if reference_field not in model_tables[table].allowed_fields:
            raise DwdPitAuditError(
                f"reference field is absent from the DWD model: {table}.{reference_field}"
            )
        evidence = _string_list(raw_table.get("evidence"), f"{table}.evidence")
        observed.add(table)
        tables.append(
            DwdPitTablePolicy(
                table=table,
                disposition=disposition,
                historical_pit_authority=authority,
                reference_field=reference_field,
                ods_sufficiency=ods_sufficiency,
                builder_path=_text(raw_table.get("builder_path"), f"{table}.builder_path"),
                knowledge_rule=_text(raw_table.get("knowledge_rule"), f"{table}.knowledge_rule"),
                evidence=evidence,
                remediation_note=_text(
                    raw_table.get("remediation_note"), f"{table}.remediation_note"
                ),
            )
        )
    if observed != set(model_tables):
        missing = sorted(set(model_tables).difference(observed))
        extra = sorted(observed.difference(model_tables))
        raise DwdPitAuditError(f"policy/model table mismatch: missing={missing}, extra={extra}")

    development_start = _date(window.get("start"), "development_window.start")
    development_end = _date(window.get("end"), "development_window.end")
    through_date = _date(release.get("through_date"), "base_release.through_date")
    if not development_start <= development_end < through_date:
        raise DwdPitAuditError("development window must precede the Base through date")
    policy = DwdPitAuditPolicy(
        path=path.resolve(),
        policy_id=_text(raw.get("policy_id"), "policy_id"),
        policy_version=_text(raw.get("policy_version"), "policy_version"),
        policy_sha256=_sha256(path),
        package_id=_text(release.get("package_id"), "base_release.package_id"),
        manifest_sha256=_text(
            release.get("manifest_sha256"), "base_release.manifest_sha256"
        ),
        content_digest=_text(release.get("content_digest"), "base_release.content_digest"),
        through_date=through_date,
        required_mirror_labels=_string_list(
            raw.get("required_mirror_labels"), "required_mirror_labels"
        ),
        development_start=development_start,
        development_end=development_end,
        tables=tuple(sorted(tables, key=lambda item: item.table)),
    )
    return policy, model_tables


def _manifest_payload_files(
    root: Path,
    manifest: Mapping[str, Any],
    *,
    table_names: Sequence[str],
) -> dict[str, list[Path]]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise DwdPitAuditError("DWD Base manifest files are invalid")
    names = set(table_names)
    result: dict[str, list[Path]] = {name: [] for name in table_names}
    unmapped: list[str] = []
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            raise DwdPitAuditError("DWD Base manifest file row is invalid")
        relative_value = raw.get("path")
        if not isinstance(relative_value, str):
            raise DwdPitAuditError("DWD Base manifest file path is invalid")
        relative = PurePosixPath(relative_value)
        if not relative_value.startswith("payload/") or relative.suffix != ".parquet":
            continue
        matches = {part for part in relative.parts if part in names}
        if relative.stem in names:
            matches.add(relative.stem)
        if len(matches) != 1:
            unmapped.append(relative_value)
            continue
        table = next(iter(matches))
        result[table].append(root.joinpath(*relative.parts))
    if unmapped:
        raise DwdPitAuditError(f"payload Parquet files do not map to one DWD table: {unmapped[:5]}")
    missing = sorted(table for table, paths in result.items() if not paths)
    if missing:
        raise DwdPitAuditError(f"DWD Base manifest has no Parquet payload for: {missing}")
    return {table: sorted(paths) for table, paths in result.items()}


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _parquet_source(paths: Sequence[Path]) -> str:
    rendered = ",".join(_sql_text(str(path)) for path in paths)
    return f"read_parquet([{rendered}], union_by_name=true, hive_partitioning=true)"


def _json_scalar(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _scan_table(
    paths: Sequence[Path],
    *,
    policy: DwdPitTablePolicy,
    expected_rows: int,
    development_start: date,
    development_end: date,
    through_date: date,
) -> dict[str, Any]:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DwdPitAuditError("duckdb is required for a full DWD PIT audit") from exc

    source = _parquet_source(paths)
    reference = '"' + policy.reference_field.replace('"', '""') + '"'
    knowledge = '"knowledge_from"'
    development_start_sql = _sql_text(development_start.isoformat())
    development_end_sql = _sql_text(development_end.isoformat())
    through_date_sql = _sql_text(through_date.isoformat())
    connection = duckdb.connect(":memory:")
    try:
        schema_rows = connection.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()
        columns = {str(row[0]) for row in schema_rows}
        required = {"knowledge_from", "record_source", "source_record_id", policy.reference_field}
        missing = sorted(required.difference(columns))
        if missing:
            raise DwdPitAuditError(f"{policy.table} schema lacks audit fields: {missing}")
        aggregate = connection.execute(
            f"""
            SELECT
              count(*) AS total_rows,
              count(*) FILTER (WHERE {knowledge} IS NULL) AS null_knowledge_rows,
              count(*) FILTER (WHERE {reference} IS NULL) AS null_reference_rows,
              min({knowledge}) AS minimum_knowledge_from,
              max({knowledge}) AS maximum_knowledge_from,
              min({reference}) AS minimum_reference,
              max({reference}) AS maximum_reference,
              count(*) FILTER (
                WHERE CAST({reference} AS DATE)
                  BETWEEN DATE {development_start_sql} AND DATE {development_end_sql}
              ) AS development_rows,
              count(*) FILTER (
                WHERE CAST({reference} AS DATE)
                  BETWEEN DATE {development_start_sql} AND DATE {development_end_sql}
                  AND CAST({knowledge} AS DATE) > DATE {development_end_sql}
              ) AS development_future_knowledge_rows,
              count(*) FILTER (
                WHERE CAST({reference} AS DATE)
                  BETWEEN DATE '2026-01-01' AND DATE {through_date_sql}
              ) AS build_year_reference_rows,
              count(*) FILTER (
                WHERE {reference} IS NOT NULL
                  AND {knowledge} > CAST({reference} AS TIMESTAMPTZ)
              ) AS knowledge_after_reference_rows
            FROM {source}
            """
        ).fetchone()
        if aggregate is None:
            raise DwdPitAuditError(f"{policy.table} aggregate query returned no row")
        names = [str(item[0]) for item in connection.description]
        summary = {name: _json_scalar(value) for name, value in zip(names, aggregate, strict=True)}
        summary["expected_rows"] = expected_rows
        summary["row_count_matches_manifest"] = summary["total_rows"] == expected_rows
        summary["parquet_file_count"] = len(paths)
        summary["representative_schema"] = [
            {"name": str(row[0]), "type": str(row[1]), "nullable": str(row[2])}
            for row in schema_rows
        ]
        distribution_rows = connection.execute(
            f"""
            SELECT CAST(year({knowledge}) AS INTEGER) AS knowledge_year, count(*) AS row_count
            FROM {source}
            WHERE CAST({reference} AS DATE)
              BETWEEN DATE {development_start_sql} AND DATE {development_end_sql}
            GROUP BY knowledge_year
            ORDER BY knowledge_year
            """
        ).fetchall()
        summary["development_knowledge_year_distribution"] = {
            str(year): int(count) for year, count in distribution_rows if year is not None
        }
        sample_rows = connection.execute(
            f"""
            SELECT record_source, source_record_id,
                   CAST({reference} AS VARCHAR) AS reference_value,
                   CAST({knowledge} AS VARCHAR) AS knowledge_from
            FROM {source}
            WHERE CAST({reference} AS DATE)
              BETWEEN DATE {development_start_sql} AND DATE {development_end_sql}
              AND CAST({knowledge} AS DATE) > DATE {development_end_sql}
            ORDER BY {reference}, source_record_id
            LIMIT 10
            """
        ).fetchall()
        summary["development_future_knowledge_samples"] = [
            {
                "record_source": str(row[0]),
                "source_record_id": str(row[1]),
                "reference_value": str(row[2]),
                "knowledge_from": str(row[3]),
            }
            for row in sample_rows
        ]
        return summary
    except DwdPitAuditError:
        raise
    except Exception as exc:
        raise DwdPitAuditError(f"cannot scan {policy.table}: {exc}") from exc
    finally:
        connection.close()


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_output_paths(
    outputs: Sequence[Path], *, mirrors: Mapping[str, Path]
) -> None:
    roots = {label: _resolved(root) for label, root in mirrors.items()}
    for output in outputs:
        resolved = _resolved(output)
        for label, root in roots.items():
            if resolved == root or _is_relative_to(resolved, root):
                raise DwdPitAuditError(
                    f"audit output must not be written inside sealed mirror {label}: {resolved}"
                )


def _load_manifest(root: Path) -> dict[str, Any]:
    return _object(root / "manifest.json", "DWD Base manifest")


def _mirror_identity_matches(
    *,
    package_id: str,
    manifest_sha256: str,
    content_digest: str,
    policy: DwdPitAuditPolicy,
) -> bool:
    return (
        package_id == policy.package_id
        and manifest_sha256 == policy.manifest_sha256
        and content_digest == policy.content_digest
    )


def audit_dwd_pit_semantics(
    *,
    mirrors: Mapping[str, Path],
    policy_path: Path,
    model_path: Path,
    mode: str,
) -> dict[str, Any]:
    """Verify mirrors and, in full mode, scan all 33 tables without mutating the Base."""

    if mode not in _ALLOWED_MODES:
        raise DwdPitAuditError(f"unsupported audit mode: {mode}")
    if not mirrors or any(not label.strip() for label in mirrors):
        raise DwdPitAuditError("at least one uniquely labeled mirror is required")
    resolved_roots = [_resolved(root) for root in mirrors.values()]
    if len(resolved_roots) != len(set(resolved_roots)):
        raise DwdPitAuditError("mirror labels must resolve to distinct physical roots")
    policy, model_tables = load_dwd_pit_audit_policy(policy_path, model_path=model_path)
    policy_by_table = {item.table: item for item in policy.tables}
    mirror_reports: list[dict[str, Any]] = []
    accessed: dict[str, Any] = {
        "attempted_roots": [str(_resolved(root)) for root in mirrors.values()],
        "verified_files": [],
        "scanned_parquet_files": [],
        "mutation_performed": False,
    }

    for label, requested_root in sorted(mirrors.items()):
        root = _resolved(requested_root)
        mirror_report: dict[str, Any] = {
            "label": label,
            "root": str(root),
            "verification_status": "failed",
            "identity_matches_policy": False,
            "scan_status": "not_started",
            "tables": {},
        }
        try:
            verified = verify_domain_base(root)
            manifest = _load_manifest(root)
            mirror_report.update(
                {
                    "verification_status": "verified",
                    "package_id": verified.package_id,
                    "manifest_sha256": verified.manifest_sha256,
                    "content_digest": verified.content_digest,
                    "table_count": verified.table_count,
                    "row_count": verified.row_count,
                    "payload_bytes": verified.payload_bytes,
                    "payload_file_count": verified.payload_file_count,
                    "identity_matches_policy": _mirror_identity_matches(
                        package_id=verified.package_id,
                        manifest_sha256=verified.manifest_sha256,
                        content_digest=verified.content_digest,
                        policy=policy,
                    ),
                }
            )
            raw_files = manifest.get("files")
            if isinstance(raw_files, list):
                accessed["verified_files"].extend(
                    f"{label}:{raw['path']}"
                    for raw in raw_files
                    if isinstance(raw, Mapping) and isinstance(raw.get("path"), str)
                )
            table_files = _manifest_payload_files(
                root, manifest, table_names=sorted(model_tables)
            )
            raw_counts = manifest.get("row_counts")
            if not isinstance(raw_counts, Mapping):
                raise DwdPitAuditError("DWD Base manifest row_counts are invalid")
            if mode == "manifest-only":
                mirror_report["scan_status"] = "manifest_only"
                mirror_report["tables"] = {
                    table: {
                        "expected_rows": int(raw_counts[table]),
                        "parquet_file_count": len(paths),
                    }
                    for table, paths in sorted(table_files.items())
                }
            else:
                table_reports: dict[str, Any] = {}
                for table, paths in sorted(table_files.items()):
                    accessed["scanned_parquet_files"].extend(
                        f"{label}:{path.relative_to(root).as_posix()}" for path in paths
                    )
                    table_reports[table] = _scan_table(
                        paths,
                        policy=policy_by_table[table],
                        expected_rows=int(raw_counts[table]),
                        development_start=policy.development_start,
                        development_end=policy.development_end,
                        through_date=policy.through_date,
                    )
                mirror_report["tables"] = table_reports
                mirror_report["scan_status"] = "full"
        except (DwdPitAuditError, DomainBaseError, OSError, PermissionError) as exc:
            mirror_report["error"] = str(exc)
        mirror_reports.append(mirror_report)

    observed_labels = {str(item["label"]) for item in mirror_reports}
    missing_labels = sorted(set(policy.required_mirror_labels).difference(observed_labels))
    unexpected_labels = sorted(observed_labels.difference(policy.required_mirror_labels))
    verified_reports = [
        item for item in mirror_reports if item["verification_status"] == "verified"
    ]
    failed_labels = [
        str(item["label"])
        for item in mirror_reports
        if item["verification_status"] != "verified"
    ]
    identity_mismatches = [
        str(item["label"])
        for item in verified_reports
        if not bool(item["identity_matches_policy"])
    ]
    incomplete_scans = [
        str(item["label"])
        for item in verified_reports
        if item["scan_status"] != ("full" if mode == "full" else "manifest_only")
    ]
    row_count_mismatches: list[str] = []
    if mode == "full":
        for mirror_report in verified_reports:
            reported_tables = mirror_report.get("tables")
            if not isinstance(reported_tables, Mapping):
                continue
            for table, table_report in reported_tables.items():
                if isinstance(table_report, Mapping) and not table_report.get(
                    "row_count_matches_manifest", False
                ):
                    row_count_mismatches.append(f"{mirror_report['label']}:{table}")

    execution_reasons: list[str] = []
    if missing_labels:
        execution_reasons.append("required_mirror_not_supplied")
    if failed_labels:
        execution_reasons.append("mirror_verification_failed")
    if identity_mismatches:
        execution_reasons.append("mirror_identity_mismatch")
    if incomplete_scans:
        execution_reasons.append("table_scan_incomplete")
    if row_count_mismatches:
        execution_reasons.append("manifest_row_count_mismatch")
    execution_complete = not execution_reasons

    dispositions = Counter(item.disposition for item in policy.tables)
    ods_sufficiency = Counter(item.ods_sufficiency for item in policy.tables)
    code_blocked_tables = sorted(
        item.table
        for item in policy.tables
        if item.disposition
        in {"build_timestamp_substituted", "ambiguous_source_insufficient"}
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dwd_pit_semantics_audit_report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "policy": {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_sha256": policy.policy_sha256,
            "path": str(policy.path),
        },
        "base_release": {
            "package_id": policy.package_id,
            "manifest_sha256": policy.manifest_sha256,
            "content_digest": policy.content_digest,
            "through_date": policy.through_date.isoformat(),
        },
        "development_window": {
            "start": policy.development_start.isoformat(),
            "end": policy.development_end.isoformat(),
        },
        "mirror_inventory": {
            "required_labels": list(policy.required_mirror_labels),
            "observed_labels": sorted(observed_labels),
            "missing_labels": missing_labels,
            "unexpected_labels": unexpected_labels,
            "failed_labels": failed_labels,
            "identity_mismatches": identity_mismatches,
            "mirrors": mirror_reports,
        },
        "semantic_matrix": [
            {
                "table": item.table,
                "domain": model_tables[item.table].domain,
                "time_profile": model_tables[item.table].time_profile,
                "disposition": item.disposition,
                "historical_pit_authority": item.historical_pit_authority,
                "reference_field": item.reference_field,
                "ods_sufficiency": item.ods_sufficiency,
                "builder_path": item.builder_path,
                "knowledge_rule": item.knowledge_rule,
                "evidence": list(item.evidence),
                "remediation_note": item.remediation_note,
            }
            for item in policy.tables
        ],
        "classification_summary": {
            "dispositions": dict(sorted(dispositions.items())),
            "ods_sufficiency": dict(sorted(ods_sufficiency.items())),
            "code_blocked_tables": code_blocked_tables,
        },
        "execution_gate": {
            "status": "complete" if execution_complete else "blocked",
            "reason_codes": execution_reasons,
            "incomplete_scans": incomplete_scans,
            "row_count_mismatches": row_count_mismatches,
        },
        "historical_pit_gate": {
            "status": "blocked",
            "reason_codes": [
                "v2_contains_code_confirmed_historical_knowledge_defects",
                "ambiguous_tables_require_source_evidence_or_fail_closed_rules",
                "successor_projection_or_base_requires_separate_approval",
            ],
            "affected_tables": code_blocked_tables,
            "in_place_v2_mutation": "forbidden",
        },
        "accessed_artifacts": accessed,
    }
    report["report_digest"] = _digest(report)
    return report


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o640)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_dwd_pit_audit_report(path: Path, report: Mapping[str, Any]) -> Path:
    """Atomically write one JSON audit report outside all sealed mirrors."""

    payload = (
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    )
    _write_bytes_atomic(path, payload)
    return path.resolve()


def render_dwd_pit_audit_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact human audit summary from the machine report."""

    release = report.get("base_release")
    inventory = report.get("mirror_inventory")
    execution = report.get("execution_gate")
    historical = report.get("historical_pit_gate")
    summary = report.get("classification_summary")
    sections = (release, inventory, execution, historical, summary)
    if not all(isinstance(value, Mapping) for value in sections):
        raise DwdPitAuditError("audit report cannot be rendered")
    assert isinstance(release, Mapping)
    assert isinstance(inventory, Mapping)
    assert isinstance(execution, Mapping)
    assert isinstance(historical, Mapping)
    assert isinstance(summary, Mapping)
    lines = [
        "# DWD Base Historical PIT Audit",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Package | `{release.get('package_id')}` |",
        f"| Audit mode | `{report.get('mode')}` |",
        f"| Execution gate | `{execution.get('status')}` |",
        f"| Historical PIT gate | `{historical.get('status')}` |",
        f"| Report digest | `{report.get('report_digest')}` |",
        "",
        "## Mirror inventory",
        "",
        "| Mirror | Verification | Identity | Scan |",
        "| --- | --- | --- | --- |",
    ]
    mirrors = inventory.get("mirrors")
    if isinstance(mirrors, list):
        for raw in mirrors:
            if not isinstance(raw, Mapping):
                continue
            lines.append(
                "| "
                + " | ".join(
                    (
                        f"`{raw.get('label')}`",
                        f"`{raw.get('verification_status')}`",
                        f"`{raw.get('identity_matches_policy')}`",
                        f"`{raw.get('scan_status')}`",
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Classification",
            "",
            f"- Dispositions: `{json.dumps(summary.get('dispositions'), sort_keys=True)}`",
            f"- ODS sufficiency: `{json.dumps(summary.get('ods_sufficiency'), sort_keys=True)}`",
            f"- Code-blocked tables: `{len(summary.get('code_blocked_tables', []))}`",
            "",
            "## Table matrix",
            "",
            "| Table | Disposition | Historical authority | ODS sufficiency |",
            "| --- | --- | --- | --- |",
        ]
    )
    matrix = report.get("semantic_matrix")
    if isinstance(matrix, list):
        for raw in matrix:
            if not isinstance(raw, Mapping):
                continue
            lines.append(
                "| "
                + " | ".join(
                    (
                        f"`{raw.get('table')}`",
                        f"`{raw.get('disposition')}`",
                        f"`{raw.get('historical_pit_authority')}`",
                        f"`{raw.get('ods_sufficiency')}`",
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Gates",
            "",
            "The sealed V2 package remains blocked for historical PIT use. The report does not "
            "authorize timestamp substitution, legacy fallback, or in-place mutation.",
            "",
        ]
    )
    return "\n".join(lines)


def write_dwd_pit_audit_markdown(path: Path, report: Mapping[str, Any]) -> Path:
    """Atomically write the Markdown projection of an audit report."""

    _write_bytes_atomic(path, render_dwd_pit_audit_markdown(report).encode("utf-8"))
    return path.resolve()


def audit_and_write_dwd_pit_semantics(
    *,
    mirrors: Mapping[str, Path],
    policy_path: Path,
    model_path: Path,
    mode: str,
    output: Path,
    markdown_output: Path | None = None,
) -> dict[str, Any]:
    """Validate destinations, run the read-only audit, and write external evidence."""

    outputs = [output]
    if markdown_output is not None:
        outputs.append(markdown_output)
    _validate_output_paths(outputs, mirrors=mirrors)
    report = audit_dwd_pit_semantics(
        mirrors=mirrors,
        policy_path=policy_path,
        model_path=model_path,
        mode=mode,
    )
    write_dwd_pit_audit_report(output, report)
    if markdown_output is not None:
        write_dwd_pit_audit_markdown(markdown_output, report)
    return report
