"""Independent structural checks for engineering stage candidates and decisions.

The oracle intentionally sees only published predicate/candidate records. It does not call the
resolver or inspect future bars/targets, so it can catch schema/resolver drift without pretending to
be a human semantic ground truth.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from quanthus.stage_learning.core import (
    InferenceStatus,
    PredicateState,
    StageCandidateObservation,
    StageDecision,
)


class StageEngineeringOracleError(ValueError):
    """Raised when engineering candidate/decision records are internally inconsistent."""


def _expected_candidate_state(candidate: StageCandidateObservation) -> PredicateState:
    states = {predicate.state for predicate in candidate.predicate_evidence}
    if PredicateState.FALSE in states:
        return PredicateState.FALSE
    if PredicateState.UNKNOWN in states:
        return PredicateState.UNKNOWN
    return PredicateState.TRUE


def validate_stage34_records(
    candidates: Iterable[StageCandidateObservation], decisions: Iterable[StageDecision]
) -> dict[str, int]:
    candidate_rows = tuple(candidates)
    decision_rows = tuple(decisions)
    by_slot: dict[tuple[str, object, int], list[StageCandidateObservation]] = defaultdict(list)
    for candidate in candidate_rows:
        if candidate.stage_code not in {3, 4}:
            raise StageEngineeringOracleError("stage-3/4 oracle received another stage code")
        if candidate.official_label or candidate.authority != "engineering_candidate_only":
            raise StageEngineeringOracleError(
                "engineering candidate leaked official label authority"
            )
        if candidate.candidate_state is not _expected_candidate_state(candidate):
            raise StageEngineeringOracleError("candidate state differs from predicate evidence")
        by_slot[(candidate.security_code, candidate.market_date, candidate.anchor_scale)].append(
            candidate
        )

    if len({decision.key for decision in decision_rows}) != len(decision_rows):
        raise StageEngineeringOracleError("duplicate decision slot")
    for decision in decision_rows:
        if decision.official_label or decision.authority != "engineering_candidate_only":
            raise StageEngineeringOracleError(
                "engineering decision leaked official label authority"
            )
        slot = (decision.security_code, decision.market_date, decision.anchor_scale)
        observed = by_slot.get(slot, [])
        revisions = tuple(item.revision_id for item in observed)
        if revisions != decision.candidate_revisions:
            raise StageEngineeringOracleError("decision candidate revisions differ")
        supported = tuple(
            item.stage_code for item in observed if item.candidate_state is PredicateState.TRUE
        )
        if supported != decision.supported_candidates:
            raise StageEngineeringOracleError("decision supported candidate set differs")
        if len(supported) == 1:
            expected_status = InferenceStatus.CONFIRMED
            expected_primary = supported[0]
        elif len(supported) > 1:
            expected_status = InferenceStatus.RULE_CONFLICT
            expected_primary = None
        elif any(item.candidate_state is PredicateState.UNKNOWN for item in observed):
            expected_status = InferenceStatus.ABSTAIN
            expected_primary = None
        else:
            expected_status = InferenceStatus.NOT_EVALUATED
            expected_primary = None
        if (
            decision.inference_status is not expected_status
            or decision.primary_stage != expected_primary
        ):
            raise StageEngineeringOracleError("decision resolution differs from candidate evidence")
    return {
        "candidate_count": len(candidate_rows),
        "decision_count": len(decision_rows),
        "confirmed_count": sum(
            item.inference_status is InferenceStatus.CONFIRMED for item in decision_rows
        ),
        "abstain_count": sum(
            item.inference_status is InferenceStatus.ABSTAIN for item in decision_rows
        ),
        "rule_conflict_count": sum(
            item.inference_status is InferenceStatus.RULE_CONFLICT for item in decision_rows
        ),
    }
