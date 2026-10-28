"""A pending Head2 assessment cannot acquire consumer authority from a raw score."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quanthus.aggregator import (
    GovernedAction,
    ScoreActionPolicy,
    aggregate_governance,
    aggregate_score_and_map_action,
)
from quanthus.expert_protocols import (
    ConstraintOutput,
    ConstraintScope,
    ExpertApplicability,
    ExpertContext,
    RequiredAction,
    ScoringOutput,
    VetoAuthority,
    VetoOutput,
)
from quanthus.label_registry import (
    FeatureEvidenceRef,
    LabelDimension,
    LabelEvidence,
    LabelResult,
    LabelResultStatus,
)
from quanthus.stage_learning.head2_consumer import pending_assessment
from quanthus.stage_learning.p5_assessment import P5AssessmentError


def decisions(inputs, candidate):
    """Use the existing actual aggregator, without importing the old replay helpers."""
    context = inputs["context"]
    governance = aggregate_governance(context, inputs["vetoes"], inputs["constraints"])
    return (
        aggregate_score_and_map_action(context, governance, inputs["baseline"], inputs["policy"]),
        aggregate_score_and_map_action(context, governance, candidate, inputs["policy"]),
    )


@pytest.fixture
def inputs():
    cutoff = datetime(2020, 7, 1, 7, tzinfo=timezone.utc)
    label = LabelResult(
        label_id="head2.synthetic.fact",
        label_version="1.0.0",
        card_digest="sha256:" + "a" * 64,
        dimension=LabelDimension.PRICE_VOLUME_ACCEPTANCE,
        security_code="synthetic",
        snapshot_id="synthetic-head2-only",
        knowledge_cutoff=cutoff,
        status=LabelResultStatus.APPLIED,
        value="available",
        reason_code="synthetic",
        evidence=(LabelEvidence("synthetic", "no_market_rows"),),
        feature_evidence=(FeatureEvidenceRef("synthetic", "1.0.0", "sha256:" + "b" * 64),),
        evaluator_id="synthetic",
        evaluator_version="1.0.0",
    )
    context = ExpertContext("synthetic", "synthetic-head2-only", cutoff, "empty", (), (label,))
    evidence = (context.evidence_refs[0],)
    veto = VetoOutput(
        "system",
        "1.0.0",
        ExpertApplicability.APPLICABLE,
        "synthetic",
        "allowed",
        evidence,
        VetoAuthority.SYSTEM,
        True,
        RequiredAction.NONE,
    )
    score = ScoringOutput(
        "baseline",
        "1.0.0",
        ExpertApplicability.APPLICABLE,
        "synthetic",
        "synthetic",
        evidence,
        Decimal("0.8"),
        Decimal(1),
    )
    constraint = ConstraintOutput(
        expert_id="risk",
        expert_version="1.0.0",
        applicability=ExpertApplicability.APPLICABLE,
        applicability_reason="synthetic",
        reason_code="bounded",
        evidence=evidence,
        minimum_position=Decimal(0),
        maximum_position=Decimal(1),
        stop_rule="existing_stop",
        hold_rule="existing_hold",
        scope=ConstraintScope.UNIT,
    )
    return dict(
        context=context,
        vetoes=(veto,),
        constraints=(constraint,),
        baseline=(score,),
        policy=ScoreActionPolicy("existing-synthetic-policy", Decimal("0.5")),
    )


@pytest.mark.parametrize("raw", (None, 0.0, 0.49, 0.5, 1.0))
def test_raw_score_never_grants_consumer_or_probability_authority(inputs, raw):
    result = pending_assessment(
        inputs["context"],
        raw_score=raw,
        model_digest="sha256:" + "c" * 64,
        purpose_digest="sha256:" + "d" * 64,
    )
    assert result.consumer_admission == "NOT_GRANTED"
    assert result.calibrated_probability is None
    assert result.assessment.applicability is ExpertApplicability.ABSTAINED
    assert result.assessment.signal is None and result.assessment.confidence is None
    baseline, candidate = decisions(inputs, (*inputs["baseline"], result.assessment))
    assert baseline.final_action is candidate.final_action
    assert baseline.governance_digest == candidate.governance_digest


@pytest.mark.parametrize("position", ("empty", "held"))
def test_missing_data_veto_still_blocks_entry_without_forcing_sale(inputs, position):
    inputs["context"] = replace(inputs["context"], position_state=position)
    inputs["vetoes"] = (
        replace(inputs["vetoes"][0], allow=False, required_action=RequiredAction.BLOCK_NEW),
    )
    result = pending_assessment(
        inputs["context"],
        raw_score=None,
        model_digest="sha256:" + "c" * 64,
        purpose_digest="sha256:" + "d" * 64,
    )
    baseline, candidate = decisions(inputs, (*inputs["baseline"], result.assessment))
    assert baseline.final_action is candidate.final_action is GovernedAction.NO_ACTION


@pytest.mark.parametrize("raw", (True, -0.01, 1.01, float("nan"), float("inf")))
def test_invalid_scores_are_rejected(inputs, raw):
    with pytest.raises(P5AssessmentError):
        pending_assessment(
            inputs["context"],
            raw_score=raw,
            model_digest="sha256:" + "c" * 64,
            purpose_digest="sha256:" + "d" * 64,
        )
