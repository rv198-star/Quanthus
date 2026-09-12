"""Seal P3-STAGE-009-D development references and score the frozen 27-candidate grid.

This module consumes only already-prepared D1 Primary-only cases, the sealed same-origin review
ledger, and the D1 threshold basis. It has no market-data reader, evaluation access, candidate-grid
expansion, or runtime Job submission path.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from quanthus.policy import find_project_root
from quanthus.stage_learning.order_reference_protocol_v2 import project_atomic_primary
from quanthus.stage_learning.positive_instability_development import select_development_candidate
from quanthus.structure_v2_inputs import digest, exact_sha

LEDGER_PATH = "config/stage-learning/positive-instability-development-review.v1.tsv"
LEDGER_SCHEMA = "quanthus.stage-positive-instability-development-review.v1"
EXPECTED_CASE_COUNT = 122
TRUTH = {"TRUE", "FALSE", "UNKNOWN"}


class PositiveInstabilityDevelopmentReviewError(ValueError):
    """The sealed development ledger or D1 denominator differs."""


def _metadata(lines: Sequence[str]) -> tuple[dict[str, str], list[str]]:
    meta: dict[str, str] = {}
    body: list[str] = []
    for line in lines:
        if line.startswith("# "):
            key, sep, value = line[2:].partition("=")
            if not sep or key in meta:
                raise PositiveInstabilityDevelopmentReviewError("development_ledger_metadata")
            meta[key] = value
        elif line.strip():
            body.append(line)
    return meta, body


def load_ledger(root: Path | None = None) -> dict[str, Any]:
    root = root or find_project_root()
    path = root / LEDGER_PATH
    try:
        raw = path.read_bytes()
        lines = raw.decode("utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PositiveInstabilityDevelopmentReviewError("development_ledger_unreadable") from exc
    meta, body = _metadata(lines)
    required = {
        "schema", "work_item", "source_job", "source_manifest_sha256",
        "development_evidence_digest", "provenance", "candidate_output_seen_during_review",
        "threshold_basis_seen_during_review", "explicit_recent_derived_facts_seen",
        "cognitive_blinding_claimed",
    }
    if set(meta) != required or meta["schema"] != LEDGER_SCHEMA:
        raise PositiveInstabilityDevelopmentReviewError("development_ledger_metadata")
    if meta["work_item"] != "P3-STAGE-009-D":
        raise PositiveInstabilityDevelopmentReviewError("development_ledger_work_item")
    if (
        not exact_sha(meta["source_manifest_sha256"])
        or not exact_sha(meta["development_evidence_digest"])
        or meta["candidate_output_seen_during_review"] != "false"
        or meta["threshold_basis_seen_during_review"] != "false"
        or meta["explicit_recent_derived_facts_seen"] != "false"
        or meta["cognitive_blinding_claimed"] != "false"
    ):
        raise PositiveInstabilityDevelopmentReviewError("development_ledger_authority")
    header = (
        "index|short_instability|short_relocation|short_order|fund_instability|fund_relocation|"
        "fund_order|short_reason|fund_reason"
    )
    if not body or body[0] != header:
        raise PositiveInstabilityDevelopmentReviewError("development_ledger_header")
    rows = []
    for expected_index, line in enumerate(body[1:]):
        values = line.split("|")
        if len(values) != 9:
            raise PositiveInstabilityDevelopmentReviewError("development_ledger_row_shape")
        index, si, sr, so, fi, fr, fo, short_reason, fund_reason = values
        if index != str(expected_index):
            raise PositiveInstabilityDevelopmentReviewError("development_ledger_index")
        if any(value not in TRUTH for value in (si, sr, so, fi, fr, fo)):
            raise PositiveInstabilityDevelopmentReviewError("development_ledger_truth_value")
        if not short_reason.strip() or not fund_reason.strip():
            raise PositiveInstabilityDevelopmentReviewError("development_ledger_reason")
        rows.append({
            "index": expected_index,
            "short_term": {
                "structural_instability_20": si,
                "coherent_directional_relocation_20": sr,
                "orderly_consolidation_20": so,
            },
            "fund": {
                "structural_instability_20": fi,
                "coherent_directional_relocation_20": fr,
                "orderly_consolidation_20": fo,
            },
            "short_reason": short_reason,
            "fund_reason": fund_reason,
        })
    if len(rows) != EXPECTED_CASE_COUNT:
        raise PositiveInstabilityDevelopmentReviewError("development_ledger_case_count")
    return {
        "path": LEDGER_PATH,
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "metadata": meta,
        "rows": rows,
    }


def resolve_references(
    cases: Sequence[Mapping[str, Any]], ledger: Mapping[str, Any]
) -> dict[str, Any]:
    if len(cases) != EXPECTED_CASE_COUNT:
        raise PositiveInstabilityDevelopmentReviewError("development_case_count")
    rows = ledger.get("rows")
    if not isinstance(rows, list) or len(rows) != len(cases):
        raise PositiveInstabilityDevelopmentReviewError("development_case_ledger_binding")
    references: dict[str, str] = {}
    role_counts: Counter[str] = Counter()
    disagreement = 0
    for index, case in enumerate(cases):
        row = rows[index]
        if row.get("index") != index:
            raise PositiveInstabilityDevelopmentReviewError("development_case_ledger_order")
        case_id = str(case.get("case_id") or "")
        if not exact_sha(case_id):
            raise PositiveInstabilityDevelopmentReviewError("development_case_identity")
        available = bool(case.get("primary_available"))
        projected = {
            role: project_atomic_primary(row[role], available=available)
            for role in ("short_term", "fund")
        }
        for item in projected.values():
            role_counts[item["status"]] += 1
        left = projected["short_term"]["status"]
        right = projected["fund"]["status"]
        if left != right:
            references[case_id] = "AMBIGUOUS"
            disagreement += 1
        else:
            references[case_id] = left
    counts = Counter(references.values())
    return {
        "references": references,
        "primary_reference_counts": dict(sorted(counts.items())),
        "role_status_counts": dict(sorted(role_counts.items())),
        "primary_disagreement_count": disagreement,
    }


def select_candidate(
    cases: Sequence[Mapping[str, Any]],
    ledger: Mapping[str, Any],
    threshold_basis: Mapping[str, Any],
) -> dict[str, Any]:
    resolved = resolve_references(cases, ledger)
    selection = select_development_candidate(
        cases,
        resolved["references"],
        threshold_basis,
    )
    return {
        "schema_version": "quanthus.stage-positive-instability-development-selection-result.v1",
        "work_item": "P3-STAGE-009-D",
        "status": selection["disposition"],
        "case_count": len(cases),
        "review_ledger_sha256": ledger["sha256"],
        "primary_reference_counts": resolved["primary_reference_counts"],
        "primary_disagreement_count": resolved["primary_disagreement_count"],
        "baseline": selection["baseline"],
        "candidate": selection.get("candidate"),
        "candidate_metrics": selection.get("metrics"),
        "trial_count": selection["trial_count"],
        "eligible_count": selection["eligible_count"],
        "trials_digest": digest(selection["trials"]),
        "trials": selection["trials"],
        "evaluation_data_read": False,
        "candidate_grid_expanded": False,
        "stage_2_changed": False,
        "relocation_guard_retuned": False,
    }
