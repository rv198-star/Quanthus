"""Project sealed CN01 semantic evidence into the policy's named dimensions."""

from __future__ import annotations

import hashlib
import json
import os
import socket
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DwdSemanticDimensionAuditError(RuntimeError):
    """Raised when semantic dimension evidence cannot be bound safely."""


_DIMENSIONS = {
    "field_availability": (
        "field-availability-matrix.json",
        "dwd_base_v2_field_availability_matrix",
    ),
    "identity_graph": (
        "identity-graph-audit.json",
        "dwd_base_v2_identity_graph_audit",
    ),
    "referential_integrity": (
        "referential-integrity-audit.json",
        "dwd_base_v2_referential_integrity_audit",
    ),
    "key_version_duplicates": (
        "key-version-and-duplicate-ledger.json",
        "dwd_base_v2_key_version_duplicate_audit",
    ),
    "temporal_intervals": (
        "temporal-interval-audit.json",
        "dwd_base_v2_temporal_interval_audit",
    ),
}


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("ascii")).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdSemanticDimensionAuditError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdSemanticDimensionAuditError(f"{label} must be a JSON object: {path}")
    return value


def _sealed(path: Path, label: str, digest_key: str) -> tuple[dict[str, Any], str]:
    value = _object(path, label)
    supplied = value.get(digest_key)
    if not isinstance(supplied, str) or not supplied.startswith("sha256:"):
        raise DwdSemanticDimensionAuditError(f"{label} lacks {digest_key}: {path}")
    unsigned = dict(value)
    unsigned.pop(digest_key, None)
    if supplied != _canonical_digest(unsigned):
        raise DwdSemanticDimensionAuditError(f"{label} digest mismatch: {path}")
    return value, supplied


def _base_release(semantic: Mapping[str, Any], pit: Mapping[str, Any]) -> dict[str, Any]:
    semantic_base = semantic.get("base_release")
    pit_base = pit.get("base_release")
    if not isinstance(semantic_base, Mapping) or not isinstance(pit_base, Mapping):
        raise DwdSemanticDimensionAuditError("semantic reports lack Base identity")
    keys = ("package_id", "manifest_sha256", "content_digest")
    if any(semantic_base.get(key) != pit_base.get(key) for key in keys):
        raise DwdSemanticDimensionAuditError("semantic reports bind different Base identities")
    through_date = pit_base.get("through_date")
    if not isinstance(through_date, str) or not through_date:
        raise DwdSemanticDimensionAuditError("PIT report lacks Base through_date")
    return {
        **{key: semantic_base.get(key) for key in keys},
        "through_date": through_date,
        "table_count": semantic_base.get("table_count"),
        "row_count": semantic_base.get("row_count"),
    }


def _table_map(value: object, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise DwdSemanticDimensionAuditError(f"{label} has no table map")
    result: dict[str, dict[str, Any]] = {}
    for name, raw in value.items():
        if not isinstance(name, str) or not isinstance(raw, Mapping):
            raise DwdSemanticDimensionAuditError(f"{label} contains an invalid table row")
        result[name] = dict(raw)
    if len(result) != 33:
        raise DwdSemanticDimensionAuditError(f"{label} must contain 33 tables")
    return result


def _pit_map(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise DwdSemanticDimensionAuditError("PIT report has no semantic matrix")
    result: dict[str, dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("table"), str):
            raise DwdSemanticDimensionAuditError("PIT semantic matrix contains an invalid row")
        table = str(raw["table"])
        if table in result:
            raise DwdSemanticDimensionAuditError(f"PIT semantic matrix repeats {table}")
        result[table] = dict(raw)
    if len(result) != 33:
        raise DwdSemanticDimensionAuditError("PIT semantic matrix must contain 33 tables")
    return result


def _common_table_row(
    table: str, semantic: Mapping[str, Any], pit: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "table": table,
        "time_profile": pit.get("time_profile"),
        "reference_field": pit.get("reference_field"),
        "pit_disposition": pit.get("disposition"),
        "historical_pit_authority": pit.get("historical_pit_authority"),
        "knowledge_rule": pit.get("knowledge_rule"),
        "ods_sufficiency": pit.get("ods_sufficiency"),
        "semantic_status": semantic.get("status"),
        "semantic_row_count": semantic.get("row_count"),
        "semantic_violation_count": semantic.get("violation_count"),
    }


def _field_rows(
    semantic_tables: Mapping[str, Mapping[str, Any]], pit_tables: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in sorted(semantic_tables):
        semantic = semantic_tables[table]
        pit = pit_tables[table]
        row = _common_table_row(table, semantic, pit)
        row.update(
            {
                "evidence": pit.get("evidence", []),
                "remediation_note": pit.get("remediation_note"),
                "lineage_metrics": semantic.get("lineage", {}),
                "row_key": semantic.get("row_key", []),
            }
        )
        rows.append(row)
    return rows


def _identity_rows(
    semantic_tables: Mapping[str, Mapping[str, Any]], pit_tables: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in sorted(semantic_tables):
        semantic = semantic_tables[table]
        pit = pit_tables[table]
        foreign_keys = semantic.get("foreign_keys", [])
        rows.append(
            {
                **_common_table_row(table, semantic, pit),
                "business_key": semantic.get("business_key", []),
                "row_key": semantic.get("row_key", []),
                "foreign_key_targets": [
                    {
                        "column": item.get("column"),
                        "parent_table": item.get("parent_table"),
                        "parent_column": item.get("parent_column"),
                    }
                    for item in foreign_keys
                    if isinstance(item, Mapping)
                ],
            }
        )
    return rows


def _referential_rows(semantic_tables: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in sorted(semantic_tables):
        raw_foreign_keys = semantic_tables[table].get("foreign_keys", [])
        if not isinstance(raw_foreign_keys, list):
            raise DwdSemanticDimensionAuditError(f"foreign-key metrics are invalid: {table}")
        for item in raw_foreign_keys:
            if not isinstance(item, Mapping):
                raise DwdSemanticDimensionAuditError(f"foreign-key row is invalid: {table}")
            rows.append(
                {
                    "child_table": table,
                    "column": item.get("column"),
                    "parent_table": item.get("parent_table"),
                    "parent_column": item.get("parent_column"),
                    "effective_coordinate": item.get("effective_coordinate"),
                    "orphan_rows": item.get("orphan_rows", 0),
                    "temporal_or_future_parent_rows": item.get(
                        "temporal_or_future_parent_rows", 0
                    ),
                }
            )
    return rows


def _key_rows(semantic_tables: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in sorted(semantic_tables):
        semantic = semantic_tables[table]
        rows.append(
            {
                "table": table,
                "business_key": semantic.get("business_key", []),
                "row_key": semantic.get("row_key", []),
                "null_row_key_rows": semantic.get("null_row_key_rows", 0),
                "duplicate_row_keys": semantic.get("duplicate_row_keys", {}),
                "business_key_version_groups": semantic.get(
                    "business_key_version_groups", {}
                ),
            }
        )
    return rows


def _temporal_rows(
    semantic_tables: Mapping[str, Mapping[str, Any]], pit_tables: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in sorted(semantic_tables):
        semantic = semantic_tables[table]
        pit = pit_tables[table]
        rows.append(
            {
                **_common_table_row(table, semantic, pit),
                "interval_metrics": semantic.get("intervals", {}),
                "effective_interval_rules": pit.get("knowledge_rule"),
            }
        )
    return rows


def _source_summary(semantic: Mapping[str, Any], pit: Mapping[str, Any]) -> dict[str, Any]:
    semantic_summary = semantic.get("summary")
    pit_execution = pit.get("execution_gate")
    pit_historical = pit.get("historical_pit_gate")
    if not isinstance(semantic_summary, Mapping):
        raise DwdSemanticDimensionAuditError("semantic report has no summary")
    if not isinstance(pit_execution, Mapping) or not isinstance(pit_historical, Mapping):
        raise DwdSemanticDimensionAuditError("PIT report has incomplete gates")
    reasons: set[str] = set()
    if semantic_summary.get("status") != "verified":
        reasons.add("semantic_contract_source_has_findings")
    if pit_execution.get("status") != "complete":
        reasons.add("pit_source_execution_incomplete")
    if pit_historical.get("status") != "verified":
        reasons.update(str(item) for item in pit_historical.get("reason_codes", []))
    finding_tables = semantic_summary.get("finding_tables", [])
    affected_tables = pit_historical.get("affected_tables", [])
    return {
        "status": "verified" if not reasons else "finding",
        "reason_codes": sorted(reasons),
        "source_semantic_status": semantic_summary.get("status"),
        "source_semantic_finding_tables": list(finding_tables)
        if isinstance(finding_tables, list)
        else [],
        "source_pit_execution_status": pit_execution.get("status"),
        "source_pit_historical_status": pit_historical.get("status"),
        "source_pit_affected_tables": list(affected_tables)
        if isinstance(affected_tables, list)
        else [],
        "mutation_performed": False,
    }


def audit_dwd_semantic_dimensions(
    *, semantic_report_path: Path, pit_report_path: Path
) -> dict[str, Any]:
    """Create named, digest-bound projections without rescanning sealed parquet."""

    semantic_report_path = semantic_report_path.resolve()
    pit_report_path = pit_report_path.resolve()
    semantic, semantic_digest = _sealed(
        semantic_report_path, "semantic contract report", "canonical_digest"
    )
    pit, pit_digest = _sealed(pit_report_path, "PIT semantics report", "report_digest")
    base_release = _base_release(semantic, pit)
    semantic_tables = _table_map(semantic.get("tables"), "semantic report")
    pit_tables = _pit_map(pit.get("semantic_matrix"))
    if set(semantic_tables) != set(pit_tables):
        raise DwdSemanticDimensionAuditError("semantic reports cover different table sets")
    source_summary = _source_summary(semantic, pit)
    execution = {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "read_only": True,
        "projection_only": True,
    }
    dimensions = {
        "field_availability": {"tables": _field_rows(semantic_tables, pit_tables)},
        "identity_graph": {"tables": _identity_rows(semantic_tables, pit_tables)},
        "referential_integrity": {"foreign_keys": _referential_rows(semantic_tables)},
        "key_version_duplicates": {"tables": _key_rows(semantic_tables)},
        "temporal_intervals": {"tables": _temporal_rows(semantic_tables, pit_tables)},
    }
    return {
        "schema_version": 1,
        "kind": "dwd_base_v2_semantic_dimension_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_release": base_release,
        "execution": execution,
        "source_reports": {
            "semantic_contract": {"path": str(semantic_report_path), "digest": semantic_digest},
            "pit_semantics": {"path": str(pit_report_path), "digest": pit_digest},
        },
        "projection_contract": {
            "rescan_performed": False,
            "findings_reclassified": False,
            "source_findings_remain_authoritative": True,
            "required_outputs": [name for name, _ in _DIMENSIONS.values()],
        },
        "source_summary": source_summary,
        "dimensions": dimensions,
        "summary": {
            "status": source_summary["status"],
            "dimension_count": len(dimensions),
            "table_count": len(semantic_tables),
            "foreign_key_edge_count": len(dimensions["referential_integrity"]["foreign_keys"]),
            "reason_codes": source_summary["reason_codes"],
            "mutation_performed": False,
        },
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("canonical_digest", None)
    payload = {**unsigned, "canonical_digest": _canonical_digest(unsigned)}
    if path.exists():
        raise DwdSemanticDimensionAuditError(f"refusing to overwrite report: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="ascii"
    )
    temporary.chmod(0o640)
    temporary.replace(path)
    return str(payload["canonical_digest"])


def write_semantic_dimension_reports(
    output_root: Path,
    report: Mapping[str, Any],
    *,
    sealed_roots: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Write the five policy-named outputs and their aggregate immutable index."""

    output_root = output_root.resolve()
    for root in sealed_roots:
        sealed = root.resolve()
        if output_root == sealed or output_root.is_relative_to(sealed):
            raise DwdSemanticDimensionAuditError(
                f"semantic dimension output must remain outside sealed input: {output_root}"
            )
    output_root.mkdir(parents=True, exist_ok=True)
    dimensions = report.get("dimensions")
    if not isinstance(dimensions, Mapping):
        raise DwdSemanticDimensionAuditError("semantic dimension report has no dimensions")
    output_reports: dict[str, Any] = {}
    for name, (filename, kind) in _DIMENSIONS.items():
        payload = {
            "schema_version": 1,
            "kind": kind,
            "generated_at": report["generated_at"],
            "base_release": report["base_release"],
            "execution": report["execution"],
            "source_reports": report["source_reports"],
            "projection_contract": report["projection_contract"],
            "source_summary": report["source_summary"],
            "dimension_id": name,
            "evidence": dimensions[name],
            "summary": report["summary"],
            "mutation_performed": False,
        }
        path = output_root / filename
        digest = _write_json(path, payload)
        output_reports[name] = {"path": str(path), "kind": kind, "digest": digest}
    index = {
        **dict(report),
        "output_reports": output_reports,
        "mutation_performed": False,
    }
    index_path = output_root / "semantic-dimension-audit.json"
    index_digest = _write_json(index_path, index)
    return {
        "index_path": str(index_path),
        "index_digest": index_digest,
        "output_reports": output_reports,
        "summary": report["summary"],
    }
