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
    if lineage.get("dwd", {}).get("package_id") != base_release["package_id"]:
        raise DwdAuditClosureError("lineage report package differs from DWD Base")

    plan_files = {
        name: plan_root / name
        for name in ("input-plan.json", "coverage-matrix.json", "source-disposition-ledger.json", "capacity-estimate.json", "run-receipt.json")
    }
    plan = _object(plan_files["input-plan.json"], "input plan")
    plan_coverage = _object(plan_files["coverage-matrix.json"], "coverage matrix")
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
    if compatibility["status"] != "verified":
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

    replay = {
        "status": "missing",
        "required": "two independently named representative Base derivation executions",
        "evidence": [],
    }
    if replay_evidence:
        replay["evidence"] = [{"path": str(path.resolve()), "digest": _report_digest(path.resolve())} for path in replay_evidence]
        replay["status"] = "verified" if len(replay_evidence) >= 2 and len({_report_digest(path.resolve()) for path in replay_evidence}) == 1 else "finding"
    open_scope = [
        "coverage_and_exclusion_matrix_coordinate_scan",
        "representative_replay_determinism_for_base_derivations",
        "downstream_increment_compatibility_review",
        "rebuild_publication_and_rollback_readiness_acceptance",
        "human_approval_of_findings_and_remediation_scope",
    ]
    if coverage is not None:
        open_scope.remove("coverage_and_exclusion_matrix_coordinate_scan")
    if replay["status"] == "verified":
        open_scope.remove("representative_replay_determinism_for_base_derivations")
    if compatibility["status"] == "verified":
        open_scope.remove("downstream_increment_compatibility_review")

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
        "downstream_compatibility": compatibility,
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
