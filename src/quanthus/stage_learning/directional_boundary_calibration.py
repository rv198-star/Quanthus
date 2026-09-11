"""Seal P3-STAGE-008-D calibration references and select one frozen relocation guard.

This module has no market-data provider, Worker submission, evaluation-data access, opinion
generation, or candidate-grid expansion path. It consumes the already prepared D1 Primary-only
cases and the owner-authorized same-origin review ledger.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.directional_boundary_development import (
    DirectionalBoundaryDevelopmentError,
    select_calibration_candidate,
    stage0_metrics,
)
from quanthus.stage_learning.order_reference_protocol_v2 import project_atomic_primary
from quanthus.structure_v2_inputs import digest, exact_sha

LEDGER_PATH = "config/stage-learning/directional-boundary-calibration-review.v1.tsv"
LEDGER_SCHEMA = "quanthus.stage-directional-boundary-calibration-review.v1"
TRUTH = {"TRUE", "FALSE", "UNKNOWN"}
EXPECTED_CASE_COUNT = 64


class DirectionalBoundaryCalibrationError(ValueError):
    """The frozen calibration ledger or D1 case denominator differs."""


def _meta(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    metadata: dict[str, str] = {}
    body: list[str] = []
    for line in lines:
        if line.startswith("# "):
            key, sep, value = line[2:].partition("=")
            if not sep or key in metadata:
                raise DirectionalBoundaryCalibrationError("calibration_ledger_metadata")
            metadata[key] = value
        elif line.strip():
            body.append(line)
    return metadata, body


def load_ledger(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    path = root / LEDGER_PATH
    try:
        raw = path.read_bytes()
        lines = raw.decode("utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DirectionalBoundaryCalibrationError("calibration_ledger_unreadable") from exc
    metadata, body = _meta(lines)
    required_meta = {
        "schema", "work_item", "source_job", "source_manifest_sha256",
        "calibration_evidence_digest", "provenance", "candidate_output_seen_during_review",
        "explicit_recent_derived_facts_seen", "cognitive_blinding_claimed",
    }
    if set(metadata) != required_meta or metadata["schema"] != LEDGER_SCHEMA:
        raise DirectionalBoundaryCalibrationError("calibration_ledger_metadata")
    if metadata["work_item"] != "P3-STAGE-008-D":
        raise DirectionalBoundaryCalibrationError("calibration_ledger_work_item")
    if (
        not exact_sha(metadata["source_manifest_sha256"])
        or not exact_sha(metadata["calibration_evidence_digest"])
        or metadata["candidate_output_seen_during_review"] != "false"
        or metadata["explicit_recent_derived_facts_seen"] != "false"
        or metadata["cognitive_blinding_claimed"] != "false"
    ):
        raise DirectionalBoundaryCalibrationError("calibration_ledger_authority")
    header = (
        "index|case_id|short_instability|short_relocation|short_order|fund_instability|"
        "fund_relocation|fund_order|short_reason|fund_reason"
    )
    if not body or body[0] != header:
        raise DirectionalBoundaryCalibrationError("calibration_ledger_header")
    rows = []
    seen_cases = set()
    for expected_index, line in enumerate(body[1:]):
        values = line.split("|")
        if len(values) != 10:
            raise DirectionalBoundaryCalibrationError("calibration_ledger_row_shape")
        (
            index, case_id, short_i, short_r, short_o, fund_i, fund_r, fund_o,
            short_reason, fund_reason,
        ) = values
        if index != str(expected_index) or not exact_sha(case_id) or case_id in seen_cases:
            raise DirectionalBoundaryCalibrationError("calibration_ledger_identity")
        if any(value not in TRUTH for value in (short_i, short_r, short_o, fund_i, fund_r, fund_o)):
            raise DirectionalBoundaryCalibrationError("calibration_ledger_truth_value")
        if not short_reason.strip() or not fund_reason.strip():
            raise DirectionalBoundaryCalibrationError("calibration_ledger_reason")
        seen_cases.add(case_id)
        rows.append({
            "index": expected_index,
            "case_id": case_id,
            "short_term": {
                "structural_instability_20": short_i,
                "coherent_directional_relocation_20": short_r,
                "orderly_consolidation_20": short_o,
            },
            "fund": {
                "structural_instability_20": fund_i,
                "coherent_directional_relocation_20": fund_r,
                "orderly_consolidation_20": fund_o,
            },
            "short_reason": short_reason,
            "fund_reason": fund_reason,
        })
    if len(rows) != EXPECTED_CASE_COUNT:
        raise DirectionalBoundaryCalibrationError("calibration_ledger_case_count")
    return {
        "path": LEDGER_PATH,
        "sha256": "sha256:" + __import__("hashlib").sha256(raw).hexdigest(),
        "metadata": metadata,
        "rows": rows,
    }


def resolve_references(
    cases: Sequence[Mapping[str, Any]], ledger: Mapping[str, Any]
) -> dict[str, Any]:
    if len(cases) != EXPECTED_CASE_COUNT:
        raise DirectionalBoundaryCalibrationError("calibration_case_count")
    by_case = {str(case.get("case_id")): case for case in cases}
    if len(by_case) != len(cases):
        raise DirectionalBoundaryCalibrationError("calibration_case_identity")
    rows = ledger.get("rows")
    if not isinstance(rows, list) or {row["case_id"] for row in rows} != set(by_case):
        raise DirectionalBoundaryCalibrationError("calibration_case_ledger_binding")
    references: dict[str, str] = {}
    role_status_counts: Counter[str] = Counter()
    disagreement = 0
    for row in rows:
        case_id = row["case_id"]
        available = bool(by_case[case_id].get("primary_available"))
        role_results = {
            role: project_atomic_primary(row[role], available=available)
            for role in ("short_term", "fund")
        }
        for result in role_results.values():
            role_status_counts[result["status"]] += 1
        left, right = role_results["short_term"]["status"], role_results["fund"]["status"]
        if left != right:
            references[case_id] = "AMBIGUOUS"
            disagreement += 1
        else:
            references[case_id] = left
    counts = Counter(references.values())
    return {
        "references": references,
        "primary_reference_counts": dict(sorted(counts.items())),
        "role_status_counts": dict(sorted(role_status_counts.items())),
        "primary_disagreement_count": disagreement,
    }


def calibrate(cases: Sequence[Mapping[str, Any]], ledger: Mapping[str, Any]) -> dict[str, Any]:
    resolved = resolve_references(cases, ledger)
    references = resolved["references"]
    try:
        baseline = stage0_metrics(cases, references, candidate=None)
        selection = select_calibration_candidate(cases, references)
    except DirectionalBoundaryDevelopmentError as exc:
        raise DirectionalBoundaryCalibrationError(str(exc)) from exc
    return {
        "schema_version": "quanthus.stage-directional-boundary-calibration-result.v1",
        "status": selection["disposition"],
        "case_count": len(cases),
        "review_ledger_sha256": ledger["sha256"],
        "primary_reference_counts": resolved["primary_reference_counts"],
        "primary_disagreement_count": resolved["primary_disagreement_count"],
        "old_r0_stage0": baseline,
        "candidate": selection.get("candidate"),
        "candidate_metrics": selection.get("metrics"),
        "trial_count": selection["trial_count"],
        "eligible_count": selection["eligible_count"],
        "trials_digest": digest(selection["trials"]),
        "trials": selection["trials"],
        "evaluation_data_read": False,
        "stage_007_threshold_learning": False,
        "candidate_grid_expanded": False,
    }
