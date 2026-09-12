"""Seal P3-STAGE-008-D evaluation references and score the frozen relocation guard.

This module has no market-data provider, Worker submission, parameter search, candidate replacement,
future-partition access, opinion generation, or Stage-2 threshold path. It consumes already prepared
D3 Primary-only cases, one frozen evaluation ledger, and the D2 candidate contract.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.directional_boundary_development import (
    DirectionalBoundaryDevelopmentError,
    RelocationCandidate,
    evaluate_frozen_candidate,
)
from quanthus.stage_learning.order_reference_protocol_v2 import project_atomic_primary
from quanthus.structure_v2_inputs import digest, exact_sha

CANDIDATE_PATH = "config/stage-learning/directional-boundary-candidate.v1.json"
CANDIDATE_DIGEST = "sha256:c815077233ff837e7a8d0ebd884b12dcfb3d6d5c31abfe302cefe97e6997b3ff"
LEDGER_PATH = "config/stage-learning/directional-boundary-evaluation-review.v1.tsv"
LEDGER_SCHEMA = "quanthus.stage-directional-boundary-evaluation-review.v1"
TRUTH = {"TRUE", "FALSE", "UNKNOWN"}
EXPECTED_CASE_COUNT = 95


class DirectionalBoundaryEvaluationError(ValueError):
    """The frozen evaluation denominator, ledger, or candidate differs."""


def load_candidate(root: Path | None = None) -> tuple[dict[str, Any], RelocationCandidate]:
    root = root or find_project_root()
    try:
        raw = json.loads((root / CANDIDATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DirectionalBoundaryEvaluationError("candidate_contract_unreadable") from exc
    if digest(raw) != CANDIDATE_DIGEST:
        raise DirectionalBoundaryEvaluationError("candidate_contract_drift")
    frozen = raw.get("frozen_candidate")
    authority = raw.get("evaluation_authority")
    if not isinstance(frozen, Mapping) or not isinstance(authority, Mapping):
        raise DirectionalBoundaryEvaluationError("candidate_contract_shape")
    if (
        authority.get("d3_read_exact_frozen_2021_evaluation_identities") is not True
        or authority.get("evaluation_parameter_search") is not False
        or authority.get("candidate_replacement_after_evaluation_read") is not False
        or authority.get("2022_2024_access") is not False
        or authority.get("2025_access") is not False
    ):
        raise DirectionalBoundaryEvaluationError("candidate_evaluation_authority")
    candidate = RelocationCandidate(
        Decimal(str(frozen["absolute_window_return20_min"])),
        Decimal(str(frozen["range_traversal20_min"])),
        Decimal(str(frozen["counter_direction_excursion_share20_max"])),
    )
    if candidate.candidate_id != frozen.get("candidate_id"):
        raise DirectionalBoundaryEvaluationError("candidate_identity_drift")
    return raw, candidate


def _metadata_and_body(raw: bytes) -> tuple[dict[str, str], list[str]]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise DirectionalBoundaryEvaluationError("evaluation_ledger_encoding") from exc
    metadata: dict[str, str] = {}
    body: list[str] = []
    for line in lines:
        if line.startswith("# "):
            key, sep, value = line[2:].partition("=")
            if not sep or key in metadata:
                raise DirectionalBoundaryEvaluationError("evaluation_ledger_metadata")
            metadata[key] = value
        elif line.strip():
            body.append(line)
    return metadata, body


def load_ledger(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    try:
        raw = (root / LEDGER_PATH).read_bytes()
    except OSError as exc:
        raise DirectionalBoundaryEvaluationError("evaluation_ledger_unreadable") from exc
    metadata, body = _metadata_and_body(raw)
    required_meta = {
        "schema", "work_item", "source_job", "source_manifest_sha256",
        "evaluation_evidence_digest", "frozen_candidate_digest", "provenance",
        "candidate_output_seen_during_review", "explicit_recent_derived_facts_seen",
        "cognitive_blinding_claimed",
    }
    if set(metadata) != required_meta or metadata.get("schema") != LEDGER_SCHEMA:
        raise DirectionalBoundaryEvaluationError("evaluation_ledger_metadata")
    if metadata.get("work_item") != "P3-STAGE-008-D":
        raise DirectionalBoundaryEvaluationError("evaluation_ledger_work_item")
    if (
        not exact_sha(metadata.get("source_manifest_sha256"))
        or not exact_sha(metadata.get("evaluation_evidence_digest"))
        or metadata.get("frozen_candidate_digest") != CANDIDATE_DIGEST
        or metadata.get("candidate_output_seen_during_review") != "false"
        or metadata.get("explicit_recent_derived_facts_seen") != "false"
        or metadata.get("cognitive_blinding_claimed") != "false"
    ):
        raise DirectionalBoundaryEvaluationError("evaluation_ledger_authority")
    header = (
        "index|short_instability|short_relocation|short_order|fund_instability|"
        "fund_relocation|fund_order|short_reason|fund_reason"
    )
    if not body or body[0] != header:
        raise DirectionalBoundaryEvaluationError("evaluation_ledger_header")
    rows = []
    seen = set()
    for expected_index, line in enumerate(body[1:]):
        values = line.split("|")
        if len(values) != 9:
            raise DirectionalBoundaryEvaluationError("evaluation_ledger_row_shape")
        (
            index, short_i, short_r, short_o, fund_i, fund_r, fund_o,
            short_reason, fund_reason,
        ) = values
        truths = (short_i, short_r, short_o, fund_i, fund_r, fund_o)
        if index != str(expected_index) or expected_index in seen:
            raise DirectionalBoundaryEvaluationError("evaluation_ledger_identity")
        if any(value not in TRUTH for value in truths):
            raise DirectionalBoundaryEvaluationError("evaluation_ledger_truth_value")
        if not short_reason.strip() or not fund_reason.strip():
            raise DirectionalBoundaryEvaluationError("evaluation_ledger_reason")
        seen.add(expected_index)
        rows.append({
            "index": expected_index,
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
        raise DirectionalBoundaryEvaluationError("evaluation_ledger_case_count")
    return {
        "path": LEDGER_PATH,
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "metadata": metadata,
        "rows": rows,
    }


def resolve_references(
    cases: Sequence[Mapping[str, Any]], ledger: Mapping[str, Any]
) -> dict[str, Any]:
    if len(cases) != EXPECTED_CASE_COUNT:
        raise DirectionalBoundaryEvaluationError("evaluation_case_count")
    by_case = {str(case.get("case_id")): case for case in cases}
    if len(by_case) != len(cases):
        raise DirectionalBoundaryEvaluationError("evaluation_case_identity")
    rows = ledger.get("rows")
    if not isinstance(rows, list) or len(rows) != len(cases):
        raise DirectionalBoundaryEvaluationError("evaluation_case_ledger_binding")
    references: dict[str, str] = {}
    role_status_counts: Counter[str] = Counter()
    disagreement = 0
    for row, case in zip(rows, cases, strict=True):
        case_id = str(case.get("case_id"))
        available = bool(case.get("primary_available"))
        role_results = {
            role: project_atomic_primary(row[role], available=available)
            for role in ("short_term", "fund")
        }
        for result in role_results.values():
            role_status_counts[result["status"]] += 1
        left = role_results["short_term"]["status"]
        right = role_results["fund"]["status"]
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


def evaluate(cases: Sequence[Mapping[str, Any]], ledger: Mapping[str, Any]) -> dict[str, Any]:
    candidate_contract, candidate = load_candidate()
    resolved = resolve_references(cases, ledger)
    try:
        result = evaluate_frozen_candidate(cases, resolved["references"], candidate)
    except DirectionalBoundaryDevelopmentError as exc:
        raise DirectionalBoundaryEvaluationError(str(exc)) from exc
    return {
        "schema_version": "quanthus.stage-directional-boundary-evaluation-result.v1",
        "status": result["disposition"],
        "reasons": result["reasons"],
        "case_count": len(cases),
        "review_ledger_sha256": ledger["sha256"],
        "candidate_contract_digest": CANDIDATE_DIGEST,
        "candidate": result["candidate"],
        "primary_reference_counts": resolved["primary_reference_counts"],
        "primary_disagreement_count": resolved["primary_disagreement_count"],
        "baseline_stage0": result["baseline_stage0"],
        "candidate_stage0": result["candidate_stage0"],
        "stage2_predictions_unchanged": result["stage2_predictions_unchanged"],
        "independent_validation": False,
        "official_label_authorized": False,
        "strategy_or_trading_authorized": False,
        "evaluation_parameter_search": False,
        "candidate_replacement_after_evaluation_read": False,
        "stage_007_threshold_learning": False,
        "stage_2_rule_changed": candidate_contract["stage_2"]["rule_changed"],
    }
