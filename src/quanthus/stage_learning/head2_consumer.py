"""Explicit pending consumer envelope; raw Head2 scores remain unqualified.

There is no enable flag or new scoring policy here. An unavailable factual input
still requires the caller's normal system BLOCK_NEW veto, not a sell instruction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from quanthus.expert_protocols import ExpertApplicability, ExpertContext, ScoringOutput
from quanthus.stage_learning.p5_assessment import P5AssessmentError
from quanthus.stage_learning.p5_contracts import require_digest


@dataclass(frozen=True)
class PendingHead2Assessment:
    model_digest: str
    purpose_digest: str
    raw_score: float | None
    assessment: ScoringOutput
    consumer_admission: str = "NOT_GRANTED"
    calibrated_probability: None = None


def pending_assessment(
    context: ExpertContext, *, raw_score: float | None, model_digest: str, purpose_digest: str
) -> PendingHead2Assessment:
    """Preserve model/purpose identity while abstaining in the real expert protocol."""
    require_digest(model_digest)
    require_digest(purpose_digest)
    if raw_score is not None and (
        type(raw_score) not in (float, int)
        or not math.isfinite(raw_score)
        or not 0 <= raw_score <= 1
    ):
        raise P5AssessmentError("head2_consumer_invalid_raw_score")
    assessment = ScoringOutput(
        expert_id="head2-research-pending",
        expert_version="2.0.0",
        applicability=ExpertApplicability.ABSTAINED,
        applicability_reason="head2_consumer_not_qualified",
        reason_code="head2_facts_unavailable"
        if raw_score is None
        else "head2_consumer_not_granted",
        evidence=context.evidence_refs,
        signal=None,
        confidence=None,
    )
    return PendingHead2Assessment(model_digest, purpose_digest, raw_score, assessment)
