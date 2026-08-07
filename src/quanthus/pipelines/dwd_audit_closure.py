"""Collect immutable DWD pre-rebuild audit evidence into a working closure packet.

The closure packet is deliberately read-only and provisional.  It classifies findings
already present in sealed reports, records evidence gaps explicitly, and keeps the
V2.1 builder gate closed until the remaining audit dimensions and human approval are
complete.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DwdAuditClosureError(RuntimeError):
    """Raised when a trustworthy closure packet cannot be produced."""


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_DISPOSITIONS = {
    "fix_in_v2_1",
    "retain_with_approved_contract",
    "forward_only",
    "fail_closed_exclusion",
    "add_immutable_evidence_then_fix_in_v2_1",
    "no_change_proven",
}

_SEMANTIC_DIMENSION_REPORTS = {
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DwdAuditClosureError(f"cannot hash evidence: {path}") from exc
    return "sha256:" + digest.hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdAuditClosureError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdAuditClosureError(f"{label} must be a JSON object: {path}")
    return value


def _sealed_report(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    value = _object(path, label)
    supplied = value.get("canonical_digest")
    if not isinstance(supplied, str) or not _SHA256.fullmatch(supplied):
        raise DwdAuditClosureError(f"{label} lacks a canonical digest: {path}")
    unsigned = dict(value)
    unsigned.pop("canonical_digest", None)
    if supplied != _canonical_digest(unsigned):
        raise DwdAuditClosureError(f"{label} canonical digest mismatch: {path}")
    mutation = value.get("mutation_performed")
    if mutation is None and isinstance(value.get("summary"), Mapping):
        mutation = value["summary"].get("mutation_performed")
    if mutation is not False and label != "Phase A findings report":
        raise DwdAuditClosureError(f"{label} is not read-only evidence: {path}")
    return value, supplied


def _report_digest(path: Path) -> str:
    value = _object(path, "audit evidence")
    supplied = value.get("canonical_digest") or value.get("report_digest")
    if isinstance(supplied, str) and _SHA256.fullmatch(supplied):
        return supplied
    return _sha256(path)


def _replay_audit_status(
    report: Mapping[str, Any], *, source: str, base_release: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate the aggregate replay report and classify an evidence gap."""

    if report.get("kind") != "dwd_base_v2_replay_determinism_audit":
        raise DwdAuditClosureError("replay report has an unexpected kind")
    observed = report.get("base_release")
    if not isinstance(observed, Mapping):
        raise DwdAuditClosureError("replay report has no Base identity")
    identity_keys = ("package_id", "manifest_sha256", "content_digest", "through_date")
    if any(observed.get(key) != base_release.get(key) for key in identity_keys):
        raise DwdAuditClosureError("replay report Base identity differs from the sealed package")
    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        raise DwdAuditClosureError("replay report has no summary")
    status = summary.get("status")
    if status not in {"verified", "finding"}:
        raise DwdAuditClosureError(f"replay report has invalid status: {status}")
    if summary.get("mutation_performed") is not False:
        raise DwdAuditClosureError("replay report does not prove read-only execution")
    reason_codes = summary.get("reason_codes", [])
    if not isinstance(reason_codes, list) or any(not isinstance(item, str) for item in reason_codes):
        raise DwdAuditClosureError("replay report reason codes are invalid")
    details = {
        "status": status,
        "report": source,
        "report_digest": report.get("canonical_digest"),
        "reason_codes": sorted(set(reason_codes)),
        "independent_execution_count": summary.get("independent_execution_count", 0),
        "evidence": report.get("evidence", []),
    }
    if status == "verified":
        return details, []
    return details, [
        _finding(
            finding_id="DWDV2-REPLAY-DETERMINISM",
            dimension_id="representative_replay_determinism",
            scope="CN01 DWD Base derived builders",
            severity="high",
            evidence={
                "reason_codes": details["reason_codes"],
                "independent_execution_count": details["independent_execution_count"],
                "evidence": details["evidence"],
            },
            root_cause="The frozen Base has no two digest-bound, independently named representative derivation executions that prove deterministic replay.",
            affected={"independent_executions": details["independent_execution_count"]},
            disposition="add_immutable_evidence_then_fix_in_v2_1",
            change="Capture two read-only CN01 replay receipts over representative real partitions, bind both to the frozen Base and input plan, and reject mutable or network-dependent derivation inputs.",
            tests=["test_dwd_replay_determinism_requires_two_bound_executions"],
            acceptance="A fresh CN01 replay report verifies two independently named executions with equal bytes or an approved semantic digest and zero replay reason codes.",
            source_report=source,
        )
    ]


def _readiness_audit_status(
    report: Mapping[str, Any], *, source: str, base_release: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate the readiness report and import its deterministic findings."""

    if report.get("kind") != "dwd_base_v2_rebuild_publication_rollback_readiness_audit":
        raise DwdAuditClosureError("readiness report has an unexpected kind")
    observed = report.get("base_release")
    if not isinstance(observed, Mapping):
        raise DwdAuditClosureError("readiness report has no Base identity")
    identity_keys = ("package_id", "manifest_sha256", "content_digest", "through_date")
    if any(observed.get(key) != base_release.get(key) for key in identity_keys):
        raise DwdAuditClosureError("readiness report Base identity differs from the sealed package")
    summary = report.get("summary")
    if not isinstance(summary, Mapping) or summary.get("status") not in {"verified", "finding"}:
        raise DwdAuditClosureError("readiness report has an invalid summary")
    if summary.get("mutation_performed") is not False:
        raise DwdAuditClosureError("readiness report does not prove read-only execution")
    raw_findings = report.get("findings")
    if not isinstance(raw_findings, list):
        raise DwdAuditClosureError("readiness report has no findings list")
    findings: list[dict[str, Any]] = []
    for raw in raw_findings:
        if not isinstance(raw, Mapping):
            raise DwdAuditClosureError("readiness finding is invalid")
        item = dict(raw)
        if item.get("disposition") not in _DISPOSITIONS:
            raise DwdAuditClosureError(
                f"readiness finding has invalid disposition: {item.get('finding_id')}"
            )
        item["evidence_report"] = source
        findings.append(item)
    details = {
        "status": summary["status"],
        "report": source,
        "report_digest": report.get("canonical_digest"),
        "finding_count": summary.get("finding_count", len(findings)),
        "reason_codes": summary.get("reason_codes", []),
        "checks": report.get("checks", {}),
    }
    return details, findings


def _missing_readiness_finding() -> dict[str, Any]:
    return _finding(
        finding_id="DWDV2-READINESS-REPORT",
        dimension_id="rebuild_publication_and_rollback_readiness",
        scope="CN01 rebuild/publication/rollback topology",
        severity="high",
        evidence={"reason_codes": ["readiness_report_missing"]},
        root_cause="No immutable CN01 readiness report proves the NFS, capacity, single-writer, pilot, pointer, and rollback contracts.",
        affected={"reports": 0},
        disposition="add_immutable_evidence_then_fix_in_v2_1",
        change="Run the read-only CN01 readiness auditor and bind its canonical report into the closure ledger before any builder approval.",
        tests=["test_rebuild_readiness_report_is_required_for_closure"],
        acceptance="A sealed CN01 readiness report verifies every topology check and carries zero unclassified readiness findings.",
        source_report="not_run_fallback",
    )


def _downstream_audit_status(
    report: Mapping[str, Any], *, source: str, base_release: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate the aggregate downstream/increment compatibility report."""

    if report.get("kind") != "dwd_base_v2_downstream_increment_impact_audit":
        raise DwdAuditClosureError("downstream report has an unexpected kind")
    observed = report.get("base_release")
    if not isinstance(observed, Mapping):
        raise DwdAuditClosureError("downstream report has no Base identity")
    identity_keys = ("package_id", "manifest_sha256", "content_digest", "through_date")
    if any(observed.get(key) != base_release.get(key) for key in identity_keys):
        raise DwdAuditClosureError("downstream report Base identity differs from the sealed package")
    summary = report.get("summary")
    if not isinstance(summary, Mapping) or summary.get("status") not in {"verified", "finding"}:
        raise DwdAuditClosureError("downstream report has an invalid summary")
    if summary.get("mutation_performed") is not False:
        raise DwdAuditClosureError("downstream report does not prove read-only execution")
    raw_findings = report.get("findings")
    if not isinstance(raw_findings, list):
        raise DwdAuditClosureError("downstream report has no findings list")
    findings: list[dict[str, Any]] = []
    for raw in raw_findings:
        if not isinstance(raw, Mapping):
            raise DwdAuditClosureError("downstream finding is invalid")
        item = dict(raw)
        if item.get("disposition") not in _DISPOSITIONS:
            raise DwdAuditClosureError(
                f"downstream finding has invalid disposition: {item.get('finding_id')}"
            )
        item["evidence_report"] = source
        findings.append(item)
    details = {
        "status": summary["status"],
        "report": source,
        "report_digest": report.get("canonical_digest"),
        "finding_count": summary.get("finding_count", len(findings)),
        "reason_codes": summary.get("reason_codes", []),
        "checks": report.get("checks", {}),
    }
    return details, findings


def _semantic_dimension_audit_status(
    report: Mapping[str, Any],
    *,
    source: str,
    base_release: Mapping[str, Any],
    sealed_roots: Sequence[Path],
) -> dict[str, Any]:
    """Validate the five named semantic-dimension projections as one packet."""

    if report.get("kind") != "dwd_base_v2_semantic_dimension_audit":
        raise DwdAuditClosureError("semantic dimension report has an unexpected kind")
    observed = report.get("base_release")
    if not isinstance(observed, Mapping):
        raise DwdAuditClosureError("semantic dimension report has no Base identity")
    identity_keys = ("package_id", "manifest_sha256", "content_digest", "through_date")
    if any(observed.get(key) != base_release.get(key) for key in identity_keys):
        raise DwdAuditClosureError(
            "semantic dimension report Base identity differs from the sealed package"
        )

    summary = report.get("summary")
    if not isinstance(summary, Mapping) or summary.get("status") not in {
        "verified",
        "finding",
    }:
        raise DwdAuditClosureError("semantic dimension report has an invalid summary")
    if summary.get("mutation_performed") is not False:
        raise DwdAuditClosureError(
            "semantic dimension report does not prove read-only execution"
        )
    projection = report.get("projection_contract")
    if not isinstance(projection, Mapping):
        raise DwdAuditClosureError("semantic dimension report has no projection contract")
    if (
        projection.get("rescan_performed") is not False
        or projection.get("findings_reclassified") is not False
        or projection.get("source_findings_remain_authoritative") is not True
    ):
        raise DwdAuditClosureError(
            "semantic dimension report violates the projection-only contract"
        )
    required_outputs = projection.get("required_outputs")
    expected_outputs = [filename for filename, _ in _SEMANTIC_DIMENSION_REPORTS.values()]
    if required_outputs != expected_outputs:
        raise DwdAuditClosureError(
            "semantic dimension report required outputs differ from policy"
        )
    dimensions = report.get("dimensions")
    if not isinstance(dimensions, Mapping) or set(dimensions) != set(_SEMANTIC_DIMENSION_REPORTS):
        raise DwdAuditClosureError("semantic dimension report does not cover all five dimensions")
    if summary.get("dimension_count") != len(_SEMANTIC_DIMENSION_REPORTS):
        raise DwdAuditClosureError("semantic dimension report dimension count differs")
    if summary.get("table_count") != 33:
        raise DwdAuditClosureError("semantic dimension report table count differs")

    source_reports = report.get("source_reports")
    if not isinstance(source_reports, Mapping):
        raise DwdAuditClosureError("semantic dimension report has no source reports")
    for label in ("semantic_contract", "pit_semantics"):
        raw_source = source_reports.get(label)
        if not isinstance(raw_source, Mapping) or not isinstance(
            raw_source.get("path"), str
        ) or not isinstance(raw_source.get("digest"), str) or not _SHA256.fullmatch(
            raw_source["digest"]
        ):
            raise DwdAuditClosureError(
                f"semantic dimension report has invalid {label} source binding"
            )

    output_reports = report.get("output_reports")
    if not isinstance(output_reports, Mapping) or set(output_reports) != set(
        _SEMANTIC_DIMENSION_REPORTS
    ):
        raise DwdAuditClosureError(
            "semantic dimension report does not list all five output reports"
        )
    checked_outputs: dict[str, Any] = {}
    resolved_roots = tuple(root.resolve() for root in sealed_roots)
    for dimension, (filename, expected_kind) in _SEMANTIC_DIMENSION_REPORTS.items():
        binding = output_reports.get(dimension)
        if not isinstance(binding, Mapping):
            raise DwdAuditClosureError(
                f"semantic dimension output binding is invalid: {dimension}"
            )
        raw_path = binding.get("path")
        supplied_digest = binding.get("digest")
        if not isinstance(raw_path, str) or not isinstance(supplied_digest, str) or not _SHA256.fullmatch(
            supplied_digest
        ):
            raise DwdAuditClosureError(
                f"semantic dimension output binding is incomplete: {dimension}"
            )
        output_path = Path(raw_path)
        if not output_path.is_absolute() or output_path.name != filename:
            raise DwdAuditClosureError(
                f"semantic dimension output path is invalid: {dimension}"
            )
        output_path = output_path.resolve()
        if any(output_path == root or output_path.is_relative_to(root) for root in resolved_roots):
            raise DwdAuditClosureError(
                f"semantic dimension output is inside sealed input: {output_path}"
            )
        output, output_digest = _sealed_report(
            output_path, label=f"semantic dimension output {dimension}"
        )
        if output_digest != supplied_digest:
            raise DwdAuditClosureError(
                f"semantic dimension output digest differs: {dimension}"
            )
        if output.get("kind") != expected_kind or output.get("dimension_id") != dimension:
            raise DwdAuditClosureError(
                f"semantic dimension output kind differs: {dimension}"
            )
        output_base = output.get("base_release")
        if not isinstance(output_base, Mapping) or any(
            output_base.get(key) != base_release.get(key) for key in identity_keys
        ):
            raise DwdAuditClosureError(
                f"semantic dimension output Base identity differs: {dimension}"
            )
        output_summary = output.get("summary")
        if not isinstance(output_summary, Mapping) or output_summary.get("table_count") != 33:
            raise DwdAuditClosureError(
                f"semantic dimension output table count differs: {dimension}"
            )
        checked_outputs[dimension] = {
            "path": str(output_path),
            "kind": expected_kind,
            "digest": output_digest,
        }
    return {
        "status": summary["status"],
        "report": source,
        "report_digest": report.get("canonical_digest"),
        "output_reports": checked_outputs,
        "dimension_count": len(checked_outputs),
        "table_count": summary["table_count"],
        "projection_only": True,
        "rescan_performed": False,
        "source_findings_remain_authoritative": True,
        "reason_codes": list(summary.get("reason_codes", [])),
    }


def _missing_semantic_dimension_finding() -> dict[str, Any]:
    return _finding(
        finding_id="DWDV2-SEMANTIC-DIMENSION-REPORT",
        dimension_id="semantic_dimension_split_reports",
        scope="CN01 DWD semantic dimension evidence",
        severity="high",
        evidence={"reason_codes": ["semantic_dimension_report_missing"]},
        root_cause=(
            "No immutable packet proves that field availability, identity graph, "
            "referential integrity, key/version duplicates, and temporal intervals "
            "were each emitted as named audit evidence."
        ),
        affected={"reports": 0, "required_dimensions": len(_SEMANTIC_DIMENSION_REPORTS)},
        disposition="add_immutable_evidence_then_fix_in_v2_1",
        change=(
            "Run the read-only semantic dimension projection against the sealed CN01 "
            "semantic and PIT reports, then bind its index and five child digests into closure."
        ),
        tests=["test_semantic_dimension_report_is_required_for_closure"],
        acceptance=(
            "The CN01 closure verifies the index plus all five named dimension files, "
            "with matching Base identity, canonical digests, and projection-only flags."
        ),
        source_report="not_run_fallback",
    )


def _missing_downstream_finding() -> dict[str, Any]:
    return _finding(
        finding_id="DWDV2-DOWNSTREAM-REPORT",
        dimension_id="downstream_and_increment_compatibility",
        scope="CN01 downstream and increment inventory",
        severity="high",
        evidence={"reason_codes": ["downstream_report_missing"]},
        root_cause="No immutable CN01 report proves Daily, weekly, historical PIT, pointer, mirror, and retained-artifact compatibility.",
        affected={"reports": 0},
        disposition="add_immutable_evidence_then_fix_in_v2_1",
        change="Run the read-only downstream/increment auditor and bind its canonical report before human approval.",
        tests=["test_downstream_impact_report_is_required_for_closure"],
        acceptance="A sealed CN01 downstream report classifies every required consumer and increment with an explicit disposition.",
        source_report="not_run_fallback",
    )


def _count_nonzero(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, raw in value.items():
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int) and raw:
            result[str(key)] = raw
    return result


def _semantic_components(table: Mapping[str, Any]) -> dict[str, Any]:
    components: dict[str, Any] = {}
    for section in (
        "foreign_keys",
        "intervals",
        "lineage",
        "numeric_invariants",
        "business_key_version_groups",
        "duplicate_row_keys",
    ):
        raw = table.get(section)
        if section == "foreign_keys" and isinstance(raw, list):
            rows = {
                f"{item.get('parent_table')}:{item.get('column')}": int(
                    item.get("temporal_or_future_parent_rows", 0)
                )
                for item in raw
                if isinstance(item, Mapping)
                and int(item.get("temporal_or_future_parent_rows", 0))
            }
            if rows:
                components[section] = rows
            continue
        nonzero = _count_nonzero(raw)
        if nonzero:
            components[section] = nonzero
    return components


def _severity_for_components(components: Mapping[str, Any]) -> str:
    if "foreign_keys" in components or "intervals" in components:
        return "high"
    numeric = components.get("numeric_invariants", {})
    if isinstance(numeric, Mapping) and any(
        key in numeric for key in ("ohlc_envelope_rows", "out_of_range_holding_percentage_rows")
    ):
        return "high"
    return "medium"


def _finding(
    *,
    finding_id: str,
    dimension_id: str,
    scope: str,
    severity: str,
    evidence: Mapping[str, Any],
    root_cause: str,
    affected: object,
    disposition: str,
    change: str,
    tests: Sequence[str],
    acceptance: str,
    source_report: str,
) -> dict[str, Any]:
    if disposition not in _DISPOSITIONS:
        raise DwdAuditClosureError(f"unsupported finding disposition: {disposition}")
    return {
        "finding_id": finding_id,
        "dimension_id": dimension_id,
        "table_or_cross_table_scope": scope,
        "severity": severity,
        "evidence": dict(evidence),
        "evidence_report": source_report,
        "root_cause": root_cause,
        "affected_rows_or_partitions": affected,
        "disposition": disposition,
        "builder_or_contract_change": change,
        "tests": list(tests),
        "v2_1_acceptance_check": acceptance,
    }


def _schema_findings(report: Mapping[str, Any], source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    tables = report.get("tables")
    if not isinstance(tables, Mapping):
        raise DwdAuditClosureError("schema report has no table map")
    for table, raw in sorted(tables.items()):
        if not isinstance(raw, Mapping) or raw.get("status") == "verified":
            continue
        reason_counts: Counter[str] = Counter()
        parts = raw.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, Mapping):
                    for reason in part.get("reason_codes", []):
                        if isinstance(reason, str):
                            reason_counts[reason] += 1
        findings.append(
            _finding(
                finding_id=f"DWDV2-SCHEMA-{table}",
                dimension_id="schema_and_partition_contract",
                scope=str(table),
                severity="high" if reason_counts else "medium",
                evidence={
                    "reason_counts": dict(sorted(reason_counts.items())),
                    "finding_part_count": raw.get("finding_part_count", 0),
                    "part_count": raw.get("part_count", 0),
                    "schema_variant_count": raw.get("schema_variant_count", 0),
                },
                root_cause="Physical partitions do not match the frozen DWD model or partition contract.",
                affected={
                    "parts": raw.get("finding_part_count", 0),
                    "rows": raw.get("row_count", 0),
                },
                disposition="fix_in_v2_1",
                change="Make the V2.1 writer emit the model field set and canonical partition keys, with a partition-level contract test.",
                tests=["test_dwd_schema_partition_contract_rejects_model_drift"],
                acceptance="The CN01 V2.1 schema audit reports zero finding parts and zero partition-contract reason codes across all 33 tables.",
                source_report=source,
            )
        )
    return findings


def _semantic_findings(report: Mapping[str, Any], source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    tables = report.get("tables")
    if not isinstance(tables, Mapping):
        raise DwdAuditClosureError("semantic report has no table map")
    for table, raw in sorted(tables.items()):
        if not isinstance(raw, Mapping) or raw.get("status") == "verified":
            continue
        components = _semantic_components(raw)
        if not components:
            continue
        findings.append(
            _finding(
                finding_id=f"DWDV2-SEMANTIC-{table}",
                dimension_id="numeric_and_domain_invariants"
                if "numeric_invariants" in components
                else "referential_integrity"
                if "foreign_keys" in components
                else "business_keys_row_keys_and_versions"
                if "business_key_version_groups" in components or "duplicate_row_keys" in components
                else "temporal_interval_integrity",
                scope=str(table),
                severity=_severity_for_components(components),
                evidence={
                    "row_count": raw.get("row_count", 0),
                    "violation_count": raw.get("violation_count", 0),
                    "components": components,
                    "business_key": raw.get("business_key"),
                    "row_key": raw.get("row_key"),
                },
                root_cause="The V2 package joins historical rows to current or future parent identity, or contains a semantic row/key violation.",
                affected={"rows": raw.get("violation_count", 0), "components": components},
                disposition="fix_in_v2_1",
                change="Rebuild dependent rows against parent intervals at the effective coordinate and enforce immutable row/version and domain contracts.",
                tests=["test_dwd_semantic_contract_rejects_future_parent_and_key_violations"],
                acceptance="A fresh CN01 semantic audit has zero orphan, future-parent, interval, duplicate-key, and domain violation counts.",
                source_report=source,
            )
        )
    return findings


def _invariant_findings(report: Mapping[str, Any], source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    tables = report.get("tables")
    if not isinstance(tables, Mapping):
        raise DwdAuditClosureError("invariant report has no table map")
    for table, raw in sorted(tables.items()):
        if not isinstance(raw, Mapping) or raw.get("status") == "verified":
            continue
        rules = _count_nonzero(raw.get("rules"))
        findings.append(
            _finding(
                finding_id=f"DWDV2-INVARIANT-{table}",
                dimension_id="numeric_and_domain_invariants",
                scope=str(table),
                severity="high",
                evidence={"row_count": raw.get("row_count", 0), "rules": rules},
                root_cause="The encoded value contract permits semantically incompatible representations.",
                affected={"rows": raw.get("violation_count", 0), "rules": rules},
                disposition="fix_in_v2_1",
                change="Make financial value representation mutually exclusive and fail the builder on conflicting value columns.",
                tests=["test_financial_fact_requires_one_value_representation"],
                acceptance="The invariant audit reports zero nonzero rules for every governed table, including zero multiple-value rows.",
                source_report=source,
            )
        )
    return findings


def _coverage_findings(report: Mapping[str, Any], source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    tables = report.get("tables")
    if not isinstance(tables, Mapping):
        raise DwdAuditClosureError("coverage report has no table map")
    for table, raw in sorted(tables.items()):
        if not isinstance(raw, Mapping) or raw.get("status") == "verified":
            continue
        findings.append(
            _finding(
                finding_id=f"DWDV2-COVERAGE-{table}",
                dimension_id="completeness_and_coverage",
                scope=str(table),
                severity="high",
                evidence={
                    "reason_codes": raw.get("reason_codes", []),
                    "coordinates": raw.get("coordinates", {}),
                    "row_count": raw.get("row_count", 0),
                    "expected_rows": raw.get("expected_rows", 0),
                },
                root_cause="A business coordinate is outside the frozen Base through-date or its coverage proof is incomplete.",
                affected={"rows_or_parts": raw.get("row_count", 0), "reason_codes": raw.get("reason_codes", [])},
                disposition="fix_in_v2_1",
                change="Bound V2.1 business coordinates to the through-date and preserve any post-cutoff records only as explicitly excluded evidence.",
                tests=["test_dwd_coverage_rejects_business_coordinates_after_through_date"],
                acceptance="The CN01 coverage/exclusion report has zero business-coordinate-after-through-date and zero incomplete-statistics reason codes across all 33 tables.",
                source_report=source,
            )
        )
    return findings


def _phase_a_findings(report: Mapping[str, Any], source: str) -> list[dict[str, Any]]:
    if report.get("kind") == "issue_78_dwd_v2_phase_a_utc_rerun_closure":
        closure = report.get("finding_closure")
        if isinstance(closure, Mapping) and closure.get("status") == "resolved_for_phase_a_audit_evidence":
            return []
        raise DwdAuditClosureError("Phase A UTC rerun closure does not resolve its finding")
    raw_findings = report.get("findings")
    if not isinstance(raw_findings, list):
        raise DwdAuditClosureError("Phase A findings report has no findings list")
    result: list[dict[str, Any]] = []
    for raw in raw_findings:
        if not isinstance(raw, Mapping):
            raise DwdAuditClosureError("Phase A finding is invalid")
        item = dict(raw)
        item["evidence_report"] = source
        if "tests" not in item:
            regression = item.get("regression_test")
            item["tests"] = [regression] if isinstance(regression, str) and regression else []
        if item.get("disposition") not in _DISPOSITIONS:
            raise DwdAuditClosureError(f"Phase A finding has invalid disposition: {item.get('finding_id')}")
        result.append(item)
    return result


def _daily_compatibility(
    *, daily_root: Path, base_release: Mapping[str, Any]
) -> dict[str, Any]:
    expected = {
        "package_id": base_release.get("package_id"),
        "manifest_sha256": base_release.get("manifest_sha256"),
        "content_digest": base_release.get("content_digest"),
    }
    manifests = sorted(daily_root.glob("packages/*/*/manifest.json"))
    checked: list[dict[str, Any]] = []
    mismatches: list[str] = []
    for path in manifests:
        value = _object(path, "daily increment manifest")
        base = value.get("base")
        if not isinstance(base, Mapping):
            mismatches.append(str(path))
            continue
        status = "compatible" if all(base.get(key) == val for key, val in expected.items()) else "mismatch"
        if status == "mismatch":
            mismatches.append(str(path))
        checked.append(
            {
                "path": str(path),
                "manifest_digest": _report_digest(path),
                "package_id": value.get("package_id"),
                "market_date": value.get("market_date"),
                "base": dict(base),
                "status": status,
            }
        )
    return {
        "status": "verified" if manifests and not mismatches else "finding",
        "expected_base": expected,
        "checked_package_count": len(checked),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "packages": checked,
    }


def _atomic_json(path: Path, value: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    unsigned = dict(value)
    unsigned.pop("canonical_digest", None)
    payload = dict(unsigned)
    digest = _canonical_digest(unsigned)
    payload["canonical_digest"] = digest
    if path.exists():
        raise DwdAuditClosureError(f"refusing to overwrite immutable closure artifact: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="ascii")
    temporary.chmod(0o640)
    os.replace(temporary, path)
    return digest


def build_audit_closure(
    *,
    base_root: Path,
    plan_root: Path,
    schema_report: Path,
    semantic_report: Path,
    lineage_report: Path,
    invariant_report: Path,
    phase_a_findings: Path,
    daily_root: Path,
    output_root: Path,
    code_revision: str,
    coverage_report: Path | None = None,
    replay_report: Path | None = None,
    readiness_report: Path | None = None,
    downstream_report: Path | None = None,
    semantic_dimension_report: Path | None = None,
    replay_evidence: Sequence[Path] = (),
) -> dict[str, Any]:
    """Build a provisional, digest-bound closure packet without touching sealed data."""

    if len(code_revision) != 40 or any(char not in "0123456789abcdef" for char in code_revision):
        raise DwdAuditClosureError("code revision must be a lowercase Git object id")
    base_root = base_root.resolve()
    plan_root = plan_root.resolve()
    output_root = output_root.resolve()
    if output_root == base_root or output_root.is_relative_to(base_root):
        raise DwdAuditClosureError("closure output must remain outside sealed DWD Base")
    if output_root == plan_root or output_root.is_relative_to(plan_root):
        raise DwdAuditClosureError("closure output must remain outside sealed input plan")
    if replay_report is not None and replay_evidence:
        raise DwdAuditClosureError("replay-report and replay-evidence cannot both be supplied")
    if replay_report is None and replay_evidence:
        if len(replay_evidence) != 1:
            raise DwdAuditClosureError("replay-evidence must contain one aggregate replay report")
        replay_report = replay_evidence[0]

    manifest = _object(base_root / "manifest.json", "DWD manifest")
    base_release = {
        "package_id": manifest.get("package_id"),
        "manifest_sha256": _sha256(base_root / "manifest.json"),
        "content_digest": manifest.get("content_digest"),
        "through_date": manifest.get("through_date"),
        "table_count": manifest.get("table_count"),
        "row_count": sum(int(value) for value in (manifest.get("row_counts") or {}).values()),
    }
    if base_release["package_id"] != "dwd-base-202607-v2":
        raise DwdAuditClosureError("closure is bound to the wrong DWD package")

    reports: list[tuple[dict[str, Any], str, Path]] = []
    for path, label in (
        (schema_report, "schema report"),
        (semantic_report, "semantic report"),
        (lineage_report, "lineage report"),
        (invariant_report, "invariant report"),
        (phase_a_findings, "Phase A findings report"),
    ):
        value, digest = _sealed_report(path.resolve(), label=label)
        reports.append((value, digest, path.resolve()))
    schema, schema_digest, schema_path = reports[0]
    semantic, semantic_digest, semantic_path = reports[1]
    lineage, lineage_digest, lineage_path = reports[2]
    invariant, invariant_digest, invariant_path = reports[3]
    phase_a, phase_a_digest, phase_a_path = reports[4]
    coverage: dict[str, Any] | None = None
    coverage_digest: str | None = None
    coverage_path: Path | None = None
    if coverage_report is not None:
        coverage, coverage_digest = _sealed_report(coverage_report.resolve(), label="coverage report")
        coverage_path = coverage_report.resolve()
    replay_value: dict[str, Any] | None = None
    replay_digest: str | None = None
    replay_path: Path | None = None
    if replay_report is not None:
        replay_path = replay_report.resolve()
        if replay_path == base_root or replay_path.is_relative_to(base_root):
            raise DwdAuditClosureError("replay report must remain outside sealed DWD Base")
        if replay_path == plan_root or replay_path.is_relative_to(plan_root):
            raise DwdAuditClosureError("replay report must remain outside sealed input plan")
        replay_value, replay_digest = _sealed_report(replay_path, label="replay report")
    readiness_value: dict[str, Any] | None = None
    readiness_digest: str | None = None
    readiness_path: Path | None = None
    if readiness_report is not None:
        readiness_path = readiness_report.resolve()
        if readiness_path == base_root or readiness_path.is_relative_to(base_root):
            raise DwdAuditClosureError("readiness report must remain outside sealed DWD Base")
        if readiness_path == plan_root or readiness_path.is_relative_to(plan_root):
            raise DwdAuditClosureError("readiness report must remain outside sealed input plan")
        readiness_value, readiness_digest = _sealed_report(
            readiness_path, label="readiness report"
        )
    downstream_value: dict[str, Any] | None = None
    downstream_digest: str | None = None
    downstream_path: Path | None = None
    if downstream_report is not None:
        downstream_path = downstream_report.resolve()
        if downstream_path == base_root or downstream_path.is_relative_to(base_root):
            raise DwdAuditClosureError("downstream report must remain outside sealed DWD Base")
        if downstream_path == plan_root or downstream_path.is_relative_to(plan_root):
            raise DwdAuditClosureError("downstream report must remain outside sealed input plan")
        downstream_value, downstream_digest = _sealed_report(
            downstream_path, label="downstream report"
        )
    semantic_dimension_value: dict[str, Any] | None = None
    semantic_dimension_digest: str | None = None
    semantic_dimension_path: Path | None = None
    if semantic_dimension_report is not None:
        semantic_dimension_path = semantic_dimension_report.resolve()
        if semantic_dimension_path == base_root or semantic_dimension_path.is_relative_to(base_root):
            raise DwdAuditClosureError(
                "semantic dimension report must remain outside sealed DWD Base"
            )
        if semantic_dimension_path == plan_root or semantic_dimension_path.is_relative_to(plan_root):
            raise DwdAuditClosureError(
                "semantic dimension report must remain outside sealed input plan"
            )
        semantic_dimension_value, semantic_dimension_digest = _sealed_report(
            semantic_dimension_path, label="semantic dimension report"
        )
    if lineage.get("dwd", {}).get("package_id") != base_release["package_id"]:
        raise DwdAuditClosureError("lineage report package differs from DWD Base")

    plan_files = {
        name: plan_root / name
        for name in ("input-plan.json", "coverage-matrix.json", "source-disposition-ledger.json", "capacity-estimate.json", "run-receipt.json")
    }
    plan = _object(plan_files["input-plan.json"], "input plan")
    ledger = _object(plan_files["source-disposition-ledger.json"], "source disposition ledger")
    capacity = _object(plan_files["capacity-estimate.json"], "capacity estimate")
    receipt = _object(plan_files["run-receipt.json"], "input plan receipt")
    plan_digests = {name: _sha256(path) for name, path in plan_files.items()}

    findings = []
    findings.extend(_phase_a_findings(phase_a, str(phase_a_path)))
    findings.extend(_schema_findings(schema, str(schema_path)))
    findings.extend(_semantic_findings(semantic, str(semantic_path)))
    findings.extend(_invariant_findings(invariant, str(invariant_path)))
    if coverage is not None and coverage_path is not None:
        findings.extend(_coverage_findings(coverage, str(coverage_path)))

    replay = {
        "status": "missing",
        "required": "two independently named representative Base derivation executions",
        "evidence": [],
        "reason_codes": ["base_replay_evidence_missing"],
    }
    replay_findings: list[dict[str, Any]]
    if replay_value is None:
        _, replay_findings = _replay_audit_status(
            {
                "kind": "dwd_base_v2_replay_determinism_audit",
                "base_release": base_release,
                "summary": {
                    "status": "finding",
                    "mutation_performed": False,
                    "reason_codes": ["base_replay_evidence_missing"],
                    "independent_execution_count": 0,
                },
                "evidence": [],
            },
            source="not_run_fallback",
            base_release=base_release,
        )
    else:
        replay, replay_findings = _replay_audit_status(
            replay_value, source=str(replay_path), base_release=base_release
        )
        replay["required"] = "two independently named representative Base derivation executions"
    findings.extend(replay_findings)

    readiness = {
        "status": "missing",
        "report": "not_run_fallback",
        "report_digest": None,
        "finding_count": 1,
        "reason_codes": ["readiness_report_missing"],
    }
    if readiness_value is None:
        readiness_findings = [_missing_readiness_finding()]
    else:
        readiness, readiness_findings = _readiness_audit_status(
            readiness_value, source=str(readiness_path), base_release=base_release
        )
    findings.extend(readiness_findings)

    if downstream_value is None:
        downstream: dict[str, Any] = {
            "status": "missing",
            "report": "not_run_fallback",
            "report_digest": None,
            "finding_count": 1,
            "reason_codes": ["downstream_report_missing"],
            "checks": {},
        }
        downstream_findings = [_missing_downstream_finding()]
    else:
        downstream, downstream_findings = _downstream_audit_status(
            downstream_value, source=str(downstream_path), base_release=base_release
        )
    findings.extend(downstream_findings)

    semantic_dimensions: dict[str, Any]
    if semantic_dimension_value is None:
        semantic_dimensions = {
            "status": "missing",
            "report": "not_run_fallback",
            "report_digest": None,
            "dimension_count": 0,
            "table_count": 0,
            "projection_only": None,
            "rescan_performed": None,
            "source_findings_remain_authoritative": None,
            "reason_codes": ["semantic_dimension_report_missing"],
            "output_reports": {},
        }
        findings.append(_missing_semantic_dimension_finding())
    else:
        assert semantic_dimension_path is not None
        semantic_dimensions = _semantic_dimension_audit_status(
            semantic_dimension_value,
            source=str(semantic_dimension_path),
            base_release=base_release,
            sealed_roots=(base_root, plan_root),
        )

    deferred = [
        item
        for item in ledger.get("records", [])
        if isinstance(item, Mapping) and item.get("disposition") == "deferred"
    ]
    if deferred:
        findings.append(
            _finding(
                finding_id="DWDV2-COVERAGE-DEFERRED-SOURCE",
                dimension_id="completeness_and_coverage",
                scope="input-plan source disposition",
                severity="medium",
                evidence={"records": deferred},
                root_cause="The source plan intentionally excludes trade-by-trade auxiliary artifacts from the approved DWD scope.",
                affected={"source_records": len(deferred), "rows": sum(int(item.get("row_count", 0)) for item in deferred)},
                disposition="retain_with_approved_contract",
                change="Keep the exclusion explicit in the V2.1 source ledger and reject consumers that require the excluded auxiliary grain.",
                tests=["test_source_disposition_ledger_requires_explicit_exclusion_reason"],
                acceptance="The V2.1 input-plan and coverage report contain the same explicit exclusion, reason, and zero DWD target tables.",
                source_report=str(plan_files["source-disposition-ledger.json"]),
            )
        )

    if capacity.get("environment", {}).get("capacity_sufficient") is not True:
        findings.append(
            _finding(
                finding_id="DWDV2-READINESS-CAPACITY",
                dimension_id="rebuild_publication_and_rollback_readiness",
                scope="CN01 /personal NFS build workspace",
                severity="high",
                evidence={
                    "capacity_estimate": capacity.get("environment"),
                    "requirement": capacity.get("requirement"),
                    "receipt_resource_use": receipt.get("resource_use"),
                },
                root_cause="The sealed build plan requires more free storage than the measured CN01 workspace provides.",
                affected={"free_bytes": capacity.get("environment", {}).get("available_free_bytes"), "required_free_bytes": capacity.get("requirement", {}).get("required_free_bytes")},
                disposition="add_immutable_evidence_then_fix_in_v2_1",
                change="Provision or reserve the required NFS capacity and rerun the partition pilot before opening the builder gate.",
                tests=["test_rebuild_readiness_rejects_insufficient_nfs_capacity"],
                acceptance="A fresh CN01 capacity receipt proves the required free bytes plus margin, with a successful pilot and no local-scratch fallback.",
                source_report=str(plan_files["capacity-estimate.json"]),
            )
        )

    compatibility = _daily_compatibility(daily_root=daily_root.resolve(), base_release=base_release)
    if downstream_value is None and compatibility["status"] != "verified":
        findings.append(
            _finding(
                finding_id="DWDV2-COMPAT-DAILY-BASE",
                dimension_id="downstream_and_increment_compatibility",
                scope="CN01 DWD daily increments",
                severity="high",
                evidence={"compatibility": compatibility},
                root_cause="A daily increment does not bind the exact canonical Base identity.",
                affected={"packages": compatibility.get("mismatch_count", 0)},
                disposition="fix_in_v2_1",
                change="Require every post-Base daily manifest to bind the accepted Base package, manifest SHA, and content digest.",
                tests=["test_daily_increment_rejects_base_identity_mismatch"],
                acceptance="All retained CN01 daily manifests verify against the accepted V2.1 Base identity before cutover.",
                source_report=str(daily_root.resolve()),
            )
        )

    open_scope = [
        "coverage_and_exclusion_matrix_coordinate_scan",
        "representative_replay_determinism_for_base_derivations",
        "semantic_dimension_split_reports",
        "downstream_increment_compatibility_review",
        "rebuild_publication_and_rollback_readiness_acceptance",
        "human_approval_of_findings_and_remediation_scope",
    ]
    if coverage is not None:
        open_scope.remove("coverage_and_exclusion_matrix_coordinate_scan")
    if replay_value is not None:
        open_scope.remove("representative_replay_determinism_for_base_derivations")
    if downstream_value is not None:
        open_scope.remove("downstream_increment_compatibility_review")
    if semantic_dimension_value is not None:
        open_scope.remove("semantic_dimension_split_reports")
    if readiness_value is not None:
        open_scope.remove("rebuild_publication_and_rollback_readiness_acceptance")

    severity_counts = Counter(str(item.get("severity")) for item in findings)
    invalid = [item.get("finding_id") for item in findings if item.get("disposition") not in _DISPOSITIONS]
    if invalid:
        raise DwdAuditClosureError(f"closure contains invalid dispositions: {invalid}")
    source_digests = {
        "schema": schema_digest,
        "semantic": semantic_digest,
        "lineage": lineage_digest,
        "invariant": invariant_digest,
        "phase_a": phase_a_digest,
        "coverage": coverage_digest,
        "replay": replay_digest,
        "readiness": readiness_digest,
        "downstream": downstream_digest,
        "semantic_dimensions": semantic_dimension_digest,
        "input_plan": plan.get("plan_digest"),
        "input_plan_files": plan_digests,
    }
    common = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_revision": code_revision,
        "base_release": base_release,
        "source_digests": source_digests,
        "mutation_performed": False,
        "builder_gate": "blocked",
    }
    ledger_value = {
        **common,
        "kind": "dwd_base_v2_findings_ledger",
        "status": "working_ledger",
        "findings": findings,
        "replay_determinism": replay,
        "rebuild_readiness": readiness,
        "downstream_compatibility_audit": downstream,
        "downstream_compatibility": compatibility,
        "semantic_dimension_audit": semantic_dimensions,
        "open_audit_scope": open_scope,
        "summary": {
            "finding_count": len(findings),
            "severity_counts": dict(sorted(severity_counts.items())),
            "invalid_disposition_count": len(invalid),
            "open_audit_scope_count": len(open_scope),
            "critical_findings": severity_counts.get("critical", 0),
        },
    }
    actionable = [item for item in findings if item.get("disposition") != "no_change_proven"]
    scope_value = {
        **common,
        "kind": "dwd_base_v2_v2_1_remediation_scope",
        "status": "pending_audit_closure",
        "source_ledger_digest": _canonical_digest(ledger_value),
        "items": [
            {
                "finding_id": item["finding_id"],
                "disposition": item["disposition"],
                "builder_or_contract_change": item["builder_or_contract_change"],
                "tests": item["tests"],
                "v2_1_acceptance_check": item["v2_1_acceptance_check"],
            }
            for item in actionable
        ],
        "open_audit_scope": open_scope,
    }
    contract_value = {
        **common,
        "kind": "dwd_base_v2_v2_1_build_contract",
        "status": "not_authorized",
        "allowed_execution_node": "cn01",
        "sealed_input_package": base_release,
        "storage_policy": {"workspace": "/personal", "local_scratch_fallback": False, "single_writer": True},
        "forbidden_actions": ["start_builder_before_human_approval", "mutate_v2", "create_partial_candidate", "move_current_pointer"],
        "required_gates": ["all_audit_dimensions_complete", "all_findings_classified", "capacity_pilot_passed", "human_approval", "semantic_digest_approved"],
        "finding_ledger_digest": _canonical_digest(ledger_value),
        "remediation_scope_digest": _canonical_digest(scope_value),
        "open_audit_scope": open_scope,
    }
    summary_lines = [
        "# DWD Base V2 Pre-Rebuild Audit Closure (Working)",
        "",
        f"- Package: `{base_release['package_id']}`",
        f"- Manifest: `{base_release['manifest_sha256']}`",
        f"- Content: `{base_release['content_digest']}`",
        f"- Findings: `{len(findings)}`; severities: `{dict(sorted(severity_counts.items()))}`",
        f"- Builder gate: **blocked**; open audit scope: `{len(open_scope)}`",
        "",
        "This packet is read-only working evidence. It does not authorize a V2.1 builder, candidate assembly, publication, or pointer movement.",
        "",
        "## Evidence",
        "",
        f"- Schema report: `{schema_path}` ({schema_digest})",
        f"- Semantic report: `{semantic_path}` ({semantic_digest})",
        f"- Lineage report: `{lineage_path}` ({lineage_digest})",
        f"- Invariant report: `{invariant_path}` ({invariant_digest})",
        f"- Phase A findings: `{phase_a_path}` ({phase_a_digest})",
        f"- Daily compatibility: `{compatibility['status']}` over `{compatibility['checked_package_count']}` packages",
        f"- Replay determinism evidence: `{replay['status']}`",
        f"- Rebuild/publication/rollback readiness: `{readiness['status']}`",
        f"- Downstream/increment compatibility: `{downstream['status']}`",
        f"- Semantic dimension projections: `{semantic_dimensions['status']}`",
        f"- Semantic dimension index: `{semantic_dimension_path or 'not_run_fallback'}` ({semantic_dimension_digest})",
        "",
        "## Immediate blockers",
        "",
    ]
    for item in findings:
        if item.get("severity") in {"critical", "high"}:
            summary_lines.append(f"- `{item['finding_id']}`: {item['root_cause']}")
    summary_lines.extend(["", "## Remaining audit scope", ""])
    summary_lines.extend(f"- `{item}`" for item in open_scope)
    output_root.mkdir(parents=True, exist_ok=False)
    ledger_digest = _atomic_json(output_root / "findings-ledger.json", ledger_value)
    scope_value["source_ledger_digest"] = ledger_digest
    scope_digest = _atomic_json(output_root / "v2.1-remediation-scope.json", scope_value)
    contract_value["finding_ledger_digest"] = ledger_digest
    contract_value["remediation_scope_digest"] = scope_digest
    contract_digest = _atomic_json(output_root / "v2.1-build-contract.json", contract_value)
    (output_root / "audit-summary.md").write_text("\n".join(summary_lines) + "\n", encoding="ascii")
    return {
        "status": "working_ledger",
        "builder_gate": "blocked",
        "finding_count": len(findings),
        "open_audit_scope": open_scope,
        "findings_ledger_digest": ledger_digest,
        "remediation_scope_digest": scope_digest,
        "build_contract_digest": contract_digest,
        "output_root": str(output_root),
    }
