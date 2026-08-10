"""Read-only coverage and exclusion audit for the canonical CN01 DWD Base."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


class DwdCoverageExclusionAuditError(RuntimeError):
    """Raised when coverage evidence cannot be established."""


_PROFILE_COORDINATES = {
    "effective_interval": ("effective_from",),
    "effective_event": ("effective_at",),
    "market_observation": ("observation_start",),
    "reporting_period": ("period_end",),
    "governance_event": ("affected_effective_at", "detected_at"),
}
_THROUGH_DATE_COORDINATES = {
    "effective_from",
    "effective_at",
    "observation_start",
    "period_end",
    "affected_effective_at",
}
_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("ascii")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdCoverageExclusionAuditError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdCoverageExclusionAuditError(f"{label} must be a JSON object: {path}")
    return value


def _verify_sealed_report(path: Path, label: str) -> dict[str, Any]:
    value = _object(path, label)
    supplied = value.get("canonical_digest")
    unsigned = dict(value)
    unsigned.pop("canonical_digest", None)
    if supplied != _canonical_digest(unsigned):
        raise DwdCoverageExclusionAuditError(f"{label} canonical digest mismatch: {path}")
    return value


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _business_date(value: object) -> date | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(_BUSINESS_TIMEZONE).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(_BUSINESS_TIMEZONE).date()
    return None


def _stats_for_column(parquet: Any, field: str) -> tuple[list[object], list[object], int, bool]:
    try:
        index = parquet.schema_arrow.get_field_index(field)
    except AttributeError:
        index = next((i for i, item in enumerate(parquet.schema_arrow) if item.name == field), -1)
    if index < 0:
        return [], [], 0, False
    minimums: list[object] = []
    maximums: list[object] = []
    null_count = 0
    complete = True
    for row_group in range(parquet.metadata.num_row_groups):
        statistics = parquet.metadata.row_group(row_group).column(index).statistics
        if statistics is None or not statistics.has_min_max:
            complete = False
            continue
        minimums.append(statistics.min)
        maximums.append(statistics.max)
        if statistics.null_count is not None:
            null_count += int(statistics.null_count)
    return minimums, maximums, null_count, complete


def _coordinate_profile(
    *, root: Path, table: str, profile: str, parts: Sequence[Mapping[str, Any]], through_date: str
) -> dict[str, Any]:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise DwdCoverageExclusionAuditError("pyarrow is required for coverage audit") from exc
    fields = _PROFILE_COORDINATES.get(profile, ())
    field_reports: dict[str, Any] = {}
    for field in fields:
        minima: list[object] = []
        maxima: list[object] = []
        null_count = 0
        complete = True
        missing_parts = 0
        for raw_part in parts:
            relative = raw_part.get("path")
            if not isinstance(relative, str):
                raise DwdCoverageExclusionAuditError(f"{table} has an invalid part path")
            parquet = pq.ParquetFile(root / relative)
            minimum, maximum, nulls, stats_complete = _stats_for_column(parquet, field)
            if not minimum and field not in parquet.schema_arrow.names:
                missing_parts += 1
                complete = False
            minima.extend(minimum)
            maxima.extend(maximum)
            null_count += nulls
            complete = complete and stats_complete
        rendered_min = min(
            (_iso(value) for value in minima if _iso(value) is not None), default=None
        )
        rendered_max = max(
            (_iso(value) for value in maxima if _iso(value) is not None), default=None
        )
        maximum_business_date = max(
            (_business_date(value) for value in maxima if _business_date(value) is not None),
            default=None,
        )
        field_reports[field] = {
            "minimum": rendered_min,
            "maximum": rendered_max,
            "null_count_from_statistics": null_count,
            "statistics_complete": complete,
            "missing_part_count": missing_parts,
            "after_through_date": bool(
                field in _THROUGH_DATE_COORDINATES
                and maximum_business_date is not None
                and maximum_business_date > date.fromisoformat(through_date)
            ),
            "through_date_checked": field in _THROUGH_DATE_COORDINATES,
        }
    return {
        "time_profile": profile,
        "coordinate_fields": list(fields),
        "fields": field_reports,
    }


def audit_dwd_coverage_exclusions(
    *, root: Path, plan_root: Path, model_path: Path, schema_report: Path
) -> dict[str, Any]:
    """Audit expected-vs-actual table scope and coordinate/statistics coverage."""

    root = root.resolve()
    plan_root = plan_root.resolve()
    model_path = model_path.resolve()
    schema_report = schema_report.resolve()
    manifest = _object(root / "manifest.json", "DWD manifest")
    schema = _verify_sealed_report(schema_report, "schema report")
    model = _object(model_path, "DWD model")
    tables = model.get("tables")
    profiles = model.get("time_profiles")
    if not isinstance(tables, list) or not isinstance(profiles, Mapping) or len(tables) != 33:
        raise DwdCoverageExclusionAuditError("DWD model must contain 33 tables and time profiles")
    plan = _object(plan_root / "input-plan.json", "input plan")
    coverage = _object(plan_root / "coverage-matrix.json", "coverage matrix")
    ledger = _object(plan_root / "source-disposition-ledger.json", "source disposition ledger")
    capacity = _object(plan_root / "capacity-estimate.json", "capacity estimate")
    through_date = str(manifest.get("through_date"))
    schema_tables = schema.get("tables")
    if not isinstance(schema_tables, Mapping):
        raise DwdCoverageExclusionAuditError("schema report has no table map")

    table_reports: dict[str, Any] = {}
    reasons: Counter[str] = Counter()
    for raw_table in sorted(tables, key=lambda item: str(item.get("name"))):
        if not isinstance(raw_table, Mapping):
            raise DwdCoverageExclusionAuditError("DWD model table row is invalid")
        table = str(raw_table.get("name"))
        profile = str(raw_table.get("time_profile"))
        raw_profile = schema_tables.get(table)
        if not isinstance(raw_profile, Mapping):
            reasons["table_missing_from_schema_inventory"] += 1
            table_reports[table] = {
                "status": "finding",
                "reason_codes": ["table_missing_from_schema_inventory"],
            }
            continue
        parts = raw_profile.get("parts")
        if not isinstance(parts, list):
            raise DwdCoverageExclusionAuditError(f"schema inventory parts are invalid: {table}")
        coordinates = _coordinate_profile(
            root=root, table=table, profile=profile, parts=parts, through_date=through_date
        )
        reason_codes: list[str] = []
        if not raw_profile.get("row_count_matches_manifest", False):
            reason_codes.append("manifest_row_count_mismatch")
        if raw_profile.get("classified_empty_part_count", 0):
            reason_codes.append("classified_empty_parts")
        for field, field_report in coordinates["fields"].items():
            if not field_report["statistics_complete"]:
                reason_codes.append(f"coordinate_statistics_incomplete:{field}")
            if field_report["after_through_date"]:
                reason_codes.append(f"coordinate_after_through_date:{field}")
        reasons.update(reason_codes)
        table_reports[table] = {
            "status": "verified" if not reason_codes else "finding",
            "row_count": raw_profile.get("row_count", 0),
            "expected_rows": raw_profile.get("expected_rows", 0),
            "part_count": raw_profile.get("part_count", 0),
            "coordinates": coordinates,
            "reason_codes": sorted(reason_codes),
        }

    domains = coverage.get("domains")
    if not isinstance(domains, list):
        raise DwdCoverageExclusionAuditError("coverage matrix domains are invalid")
    domain_reports: list[dict[str, Any]] = []
    for raw_domain in domains:
        if not isinstance(raw_domain, Mapping):
            raise DwdCoverageExclusionAuditError("coverage domain row is invalid")
        domain = dict(raw_domain)
        if domain.get("status") != "complete":
            reasons["source_domain_incomplete"] += 1
        if domain.get("missing_source_tables"):
            reasons["missing_source_tables"] += len(domain["missing_source_tables"])
        if domain.get("source_gaps"):
            reasons["source_gaps"] += len(domain["source_gaps"])
        domain_reports.append(domain)

    dispositions = Counter()
    exclusions: list[dict[str, Any]] = []
    records = ledger.get("records")
    if not isinstance(records, list):
        raise DwdCoverageExclusionAuditError("source disposition records are invalid")
    for item in records:
        if not isinstance(item, Mapping):
            continue
        disposition = str(item.get("disposition"))
        dispositions[disposition] += 1
        if disposition in {"deferred", "ods_only", "superseded"}:
            exclusions.append(
                {
                    "dataset_id": item.get("dataset_id"),
                    "disposition": disposition,
                    "reason": item.get("reason"),
                    "target_tables": item.get("target_tables", []),
                    "row_count": item.get("row_count"),
                    "path": item.get("path"),
                }
            )

    finding_tables = [
        table for table, value in table_reports.items() if value.get("status") == "finding"
    ]
    reasons.update({"finding_table": len(finding_tables)} if finding_tables else {})
    status = "verified" if not reasons else "finding"
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dwd_base_v2_coverage_exclusion_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_release": {
            "package_id": manifest.get("package_id"),
            "manifest_sha256": _sha256(root / "manifest.json"),
            "content_digest": manifest.get("content_digest"),
            "through_date": through_date,
            "table_count": manifest.get("table_count"),
            "row_count": sum(int(value) for value in (manifest.get("row_counts") or {}).values()),
        },
        "plan": {
            "run_id": plan.get("run_id"),
            "plan_digest": plan.get("plan_digest"),
            "coverage_matrix_sha256": _sha256(plan_root / "coverage-matrix.json"),
            "source_disposition_sha256": _sha256(plan_root / "source-disposition-ledger.json"),
            "capacity_estimate": capacity.get("environment"),
        },
        "summary": {
            "status": status,
            "audited_table_count": len(table_reports),
            "finding_table_count": len(finding_tables),
            "finding_tables": sorted(finding_tables),
            "reason_counts": dict(sorted(reasons.items())),
            "source_disposition_counts": dict(sorted(dispositions.items())),
            "mutation_performed": False,
        },
        "domains": domain_reports,
        "tables": table_reports,
        "exclusions": exclusions,
        "coverage_contract": {
            "expected_table_count": 33,
            "through_date": through_date,
            "coordinate_statistics_are_row_group_bounds": True,
            "excluded_rows_require_explicit_source_disposition": True,
        },
    }
    report["semantic_digest"] = _canonical_digest(
        {
            "kind": report["kind"],
            "base_release": report["base_release"],
            "plan": report["plan"],
            "summary": report["summary"],
            "domains": report["domains"],
            "tables": report["tables"],
            "exclusions": report["exclusions"],
        }
    )
    report["canonical_digest"] = _canonical_digest(report)
    return report


def write_coverage_exclusion_report(
    path: Path, report: Mapping[str, Any], *, sealed_roots: Sequence[Path] = ()
) -> None:
    resolved = path.resolve()
    for root in sealed_roots:
        sealed = root.resolve()
        if resolved == sealed or resolved.is_relative_to(sealed):
            raise DwdCoverageExclusionAuditError(
                f"audit output must remain outside sealed package: {resolved}"
            )
    if resolved.exists():
        raise DwdCoverageExclusionAuditError(f"refusing to overwrite coverage report: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.partial")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="ascii"
    )
    temporary.chmod(0o640)
    temporary.replace(resolved)
