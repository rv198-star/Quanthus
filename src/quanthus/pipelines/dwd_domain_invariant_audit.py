"""Read-only explicit cross-field domain invariants for DWD Base V2."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from quanthus.pipelines.dwd_pit_audit import _manifest_payload_files, _parquet_source
from quanthus.pipelines.securities_warehouse.domain_base import DomainBaseError, verify_domain_base


class DwdDomainInvariantAuditError(RuntimeError):
    """Raised when the explicit invariant audit cannot produce complete evidence."""


_MEMORY_LIMIT_PATTERN = re.compile(r"[1-9][0-9]*(?:MB|GB)")


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdDomainInvariantAuditError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdDomainInvariantAuditError(f"{label} must be a JSON object: {path}")
    return value


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _validated_memory_limit(value: str) -> str:
    normalized = value.strip().upper()
    if _MEMORY_LIMIT_PATTERN.fullmatch(normalized) is None:
        raise DwdDomainInvariantAuditError(
            "memory limit must be a positive whole number followed by MB or GB"
        )
    return normalized


def _outside_sealed(root: Path, path: Path, label: str) -> Path:
    sealed = root.resolve()
    resolved = path.resolve()
    if resolved == sealed or resolved.is_relative_to(sealed) or path.is_symlink():
        raise DwdDomainInvariantAuditError(
            f"audit {label} must remain outside sealed package: {resolved}"
        )
    return resolved


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    temporary.chmod(0o640)
    temporary.replace(path)


def _checkpoint_value(
    identity: Mapping[str, Any], table_reports: Mapping[str, Any]
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dwd_base_v2_domain_invariant_audit_checkpoint",
        "identity": dict(identity),
        "completed_table_count": len(table_reports),
        "tables": dict(table_reports),
        "mutation_performed": False,
    }
    value["canonical_digest"] = _canonical_digest(value)
    return value


def _load_checkpoint(
    path: Path, *, identity: Mapping[str, Any], allowed_tables: set[str]
) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = _object(path, "Phase D invariant checkpoint")
    supplied_digest = value.pop("canonical_digest", None)
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "dwd_base_v2_domain_invariant_audit_checkpoint"
        or value.get("identity") != dict(identity)
        or value.get("mutation_performed") is not False
        or supplied_digest != _canonical_digest(value)
    ):
        raise DwdDomainInvariantAuditError("Phase D invariant checkpoint identity differs")
    tables = value.get("tables")
    if not isinstance(tables, dict) or not set(tables).issubset(allowed_tables):
        raise DwdDomainInvariantAuditError("Phase D invariant checkpoint table scope differs")
    if value.get("completed_table_count") != len(tables):
        raise DwdDomainInvariantAuditError("Phase D invariant checkpoint count differs")
    return tables


def _manifest_table_names(manifest: Mapping[str, Any]) -> list[str]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise DwdDomainInvariantAuditError("DWD Base manifest files are invalid")
    names: set[str] = set()
    for raw_file in raw_files:
        if not isinstance(raw_file, Mapping) or not isinstance(raw_file.get("path"), str):
            continue
        relative = PurePosixPath(raw_file["path"])
        names.update(
            part
            for part in relative.parts
            if part.startswith("dwd_") and "." not in part
        )
        if relative.stem.startswith("dwd_"):
            names.add(relative.stem)
    return sorted(names)


def invariant_predicates() -> dict[str, dict[str, str]]:
    """Return unambiguous cross-field rules not covered by generic numeric checks."""

    digest = "'sha256:[0-9a-f]{64}'"
    return {
        "dwd_adjustment_factor": {
            "invalid_evidence_set_digest_rows": (
                f"NOT regexp_full_match(evidence_set_digest, {digest})"
            ),
        },
        "dwd_adjustment_factor_input": {
            "invalid_input_evidence_digest_rows": (
                f"NOT regexp_full_match(input_evidence_digest, {digest})"
            ),
        },
        "dwd_corporate_action": {
            "payment_before_ex_date_rows": (
                "payment_date IS NOT NULL AND ex_date IS NOT NULL AND payment_date < ex_date"
            ),
        },
        "dwd_corporate_action_term": {
            "negative_amount_per_share_rows": "amount_per_share < 0",
            "negative_ratio_numerator_rows": "ratio_numerator < 0",
            "nonpositive_ratio_denominator_rows": "ratio_denominator <= 0",
            "negative_subscription_price_rows": "subscription_price < 0",
        },
        "dwd_disclosure_subject": {
            "invalid_subject_cardinality_rows": (
                "(security_id IS NOT NULL)::INTEGER + "
                "(corporate_action_id IS NOT NULL)::INTEGER + "
                "(financial_report_id IS NOT NULL)::INTEGER <> 1"
            ),
        },
        "dwd_financial_fact": {
            "nil_with_value_rows": (
                "is_nil AND (value_decimal IS NOT NULL OR value_text IS NOT NULL)"
            ),
            "nonnull_without_value_rows": (
                "NOT is_nil AND value_decimal IS NULL AND value_text IS NULL"
            ),
            "multiple_value_representations_rows": (
                "value_decimal IS NOT NULL AND value_text IS NOT NULL"
            ),
            "invalid_dimensions_json_rows": "NOT json_valid(dimensions_json)",
        },
        "dwd_observation_gap": {
            "unknown_gap_reason_rows": "lower(trim(gap_reason)) IN ('', 'unknown')",
            "invalid_gap_evidence_digest_rows": f"NOT regexp_full_match(evidence_digest, {digest})",
        },
        "dwd_price_limit_regime": {
            "negative_priority_rows": "priority < 0",
            "negative_upper_limit_ratio_rows": "upper_limit_ratio < 0",
            "negative_lower_limit_ratio_rows": "lower_limit_ratio < 0",
            "inverted_limit_ratio_rows": (
                "upper_limit_ratio IS NOT NULL AND lower_limit_ratio IS NOT NULL "
                "AND upper_limit_ratio < lower_limit_ratio"
            ),
        },
        "dwd_quality_exception": {
            "invalid_affected_key_json_rows": "NOT json_valid(affected_business_key_json)",
            "invalid_evidence_digest_rows": f"NOT regexp_full_match(evidence_digest, {digest})",
            "blank_resolution_state_rows": "trim(resolution_state) = ''",
        },
        "dwd_tradability_state": {
            "permission_on_nontradable_rows": "NOT is_tradable AND (can_buy OR can_sell)",
            "nonpositive_upper_price_rows": "upper_price <= 0",
            "nonpositive_lower_price_rows": "lower_price <= 0",
            "inverted_price_bounds_rows": (
                "upper_price IS NOT NULL AND lower_price IS NOT NULL AND upper_price < lower_price"
            ),
            "invalid_input_revision_digest_rows": (
                f"NOT regexp_full_match(input_revision_digest, {digest})"
            ),
        },
        "dwd_trading_calendar": {
            "open_session_without_bounds_rows": "is_open AND (open_at IS NULL OR close_at IS NULL)",
            "invalid_open_close_order_rows": (
                "open_at IS NOT NULL AND close_at IS NOT NULL AND close_at <= open_at"
            ),
        },
    }


def audit_dwd_domain_invariants(
    *,
    root: Path,
    scratch: Path,
    checkpoint_path: Path,
    memory_limit: str = "2GB",
) -> dict[str, Any]:
    """Count every explicit violation over all manifest parts for governed tables."""

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise DwdDomainInvariantAuditError("duckdb is required for Phase D") from exc
    root = root.resolve()
    scratch = _outside_sealed(root, scratch, "scratch")
    scratch.mkdir(parents=True, exist_ok=True)
    if not scratch.is_dir():
        raise DwdDomainInvariantAuditError(f"audit scratch is not a directory: {scratch}")
    checkpoint_path = _outside_sealed(root, checkpoint_path, "checkpoint")
    memory_limit = _validated_memory_limit(memory_limit)
    try:
        verified = verify_domain_base(root)
    except DomainBaseError as exc:
        raise DwdDomainInvariantAuditError(f"DWD Base verification failed: {exc}") from exc
    manifest = _object(root / "manifest.json", "DWD manifest")
    predicates = invariant_predicates()
    manifest_tables = _manifest_table_names(manifest)
    all_paths = _manifest_payload_files(root, manifest, table_names=manifest_tables)
    paths = {table: all_paths[table] for table in predicates}
    audit_identity = {
        "package_id": verified.package_id,
        "manifest_sha256": verified.manifest_sha256,
        "content_digest": verified.content_digest,
        "auditor_sha256": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "memory_limit": memory_limit,
        "threads": 1,
    }
    tables = _load_checkpoint(
        checkpoint_path,
        identity=audit_identity,
        allowed_tables=set(predicates),
    )
    connection = duckdb.connect(":memory:")
    connection.execute("SET TimeZone='UTC'")
    connection.execute("SET threads=1")
    escaped_scratch = str(scratch).replace("'", "''")
    connection.execute(f"SET temp_directory='{escaped_scratch}'")
    connection.execute(f"SET memory_limit='{memory_limit}'")
    connection.execute("SET preserve_insertion_order=false")
    try:
        for table, rules in predicates.items():
            if table in tables:
                continue
            source = _parquet_source(paths[table])
            names = list(rules)
            select = ", ".join(
                f"count(*) FILTER (WHERE {predicate})" for predicate in rules.values()
            )
            row = connection.execute(f"SELECT count(*), {select} FROM {source}").fetchone()
            if row is None:
                raise DwdDomainInvariantAuditError(f"invariant query returned no row: {table}")
            counts = {name: int(value) for name, value in zip(names, row[1:], strict=True)}
            violation_count = sum(counts.values())
            tables[table] = {
                "row_count": int(row[0]),
                "rules": counts,
                "violation_count": violation_count,
                "status": "verified" if violation_count == 0 else "finding",
            }
            _write_json_atomic(
                checkpoint_path,
                _checkpoint_value(audit_identity, tables),
            )
    except Exception as exc:
        if isinstance(exc, DwdDomainInvariantAuditError):
            raise
        raise DwdDomainInvariantAuditError(f"domain invariant query failed: {exc}") from exc
    finally:
        connection.close()
    finding_tables = [table for table, item in tables.items() if item["status"] == "finding"]
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dwd_base_v2_domain_invariant_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_release": {
            "package_id": verified.package_id,
            "manifest_sha256": verified.manifest_sha256,
            "content_digest": verified.content_digest,
        },
        "auditor_sha256": audit_identity["auditor_sha256"],
        "execution": {
            "memory_limit": memory_limit,
            "scratch": str(scratch),
            "threads": 1,
        },
        "checkpoint_path": str(checkpoint_path),
        "summary": {
            "status": "verified" if not finding_tables else "finding",
            "audited_table_count": len(tables),
            "finding_table_count": len(finding_tables),
            "finding_tables": finding_tables,
            "mutation_performed": False,
        },
        "tables": tables,
    }
    report["canonical_digest"] = _canonical_digest(report)
    return report


def write_domain_invariant_report(
    path: Path, report: Mapping[str, Any], *, sealed_roots: Sequence[Path] = ()
) -> None:
    """Write an atomic invariant report outside sealed packages."""

    resolved = path.resolve()
    for root in sealed_roots:
        sealed = root.resolve()
        if resolved == sealed or resolved.is_relative_to(sealed):
            raise DwdDomainInvariantAuditError(
                f"audit output must remain outside sealed package: {resolved}"
            )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.partial")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    temporary.chmod(0o640)
    temporary.replace(resolved)
