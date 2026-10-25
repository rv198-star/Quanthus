"""The shared comparison path preserves the existing expert/veto policy."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quanthus import aggregator
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
from quanthus.expert_strategy import assessment_comparison as comparison
from quanthus.label_registry import (
    FeatureEvidenceRef,
    LabelDimension,
    LabelEvidence,
    LabelResult,
    LabelResultStatus,
)


@pytest.fixture
def inputs():
    cutoff = datetime(2020, 1, 2, 7, tzinfo=timezone.utc)
    label = LabelResult(
        label_id="stage.synthetic",
        label_version="1.0.0",
        card_digest="sha256:" + "a" * 64,
        dimension=LabelDimension.PRICE_VOLUME_ACCEPTANCE,
        security_code="600000",
        snapshot_id="synthetic-comparison",
        knowledge_cutoff=cutoff,
        status=LabelResultStatus.APPLIED,
        value="accepted",
        reason_code="synthetic",
        evidence=(LabelEvidence("synthetic", "synthetic_only"),),
        feature_evidence=(FeatureEvidenceRef("synthetic.fact", "1.0.0", "sha256:" + "b" * 64),),
        evaluator_id="synthetic.comparison",
        evaluator_version="1.0.0",
    )
    context = ExpertContext("600000", "synthetic-comparison", cutoff, "empty", (), (label,))
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
    score = ScoringOutput(
        "signal",
        "1.0.0",
        ExpertApplicability.APPLICABLE,
        "synthetic",
        "synthetic",
        evidence,
        Decimal("0.8"),
        Decimal(1),
    )
    return {
        "context": context,
        "vetoes": (veto,),
        "constraints": (constraint,),
        "baseline": (score,),
        "candidate": (replace(score, expert_version="2.0.0"),),
        "policy": aggregator.ScoreActionPolicy("fixed-synthetic-v1", Decimal("0.5")),
    }


def test_comparison_equals_two_existing_calls_and_builds_governance_once(inputs, monkeypatch):
    governance = aggregator.aggregate_governance(
        inputs["context"], inputs["vetoes"], inputs["constraints"]
    )
    expected = [
        aggregator.aggregate_score_and_map_action(
            inputs["context"], governance, inputs[key], inputs["policy"]
        )
        for key in ("baseline", "candidate")
    ]
    calls = []
    original = comparison.aggregate_governance

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(comparison, "aggregate_governance", counted)
    result = comparison.compare_entry_assessments(**inputs)
    assert result.governance == governance
    assert [result.baseline, result.candidate] == expected
    assert calls == [1]
    assert result.baseline.governance_digest == result.candidate.governance_digest
    assert result.baseline.action_policy_digest == result.candidate.action_policy_digest


def test_unknown_data_blocks_entries_without_forcing_sale(inputs):
    blocked = replace(
        inputs["vetoes"][0],
        allow=False,
        required_action=RequiredAction.BLOCK_NEW,
        reason_code="missing_data",
    )
    inputs["vetoes"] = (blocked,)
    for position in ("empty", "held"):
        inputs["context"] = replace(inputs["context"], position_state=position)
        result = comparison.compare_entry_assessments(**inputs)
        for arm in (result.baseline, result.candidate):
            assert arm.final_action is aggregator.GovernedAction.NO_ACTION


@pytest.mark.parametrize(
    "action,expected",
    [
        (RequiredAction.REDUCE_ONLY, aggregator.GovernedAction.REDUCE),
        (RequiredAction.FORCE_LIQUIDATE, aggregator.GovernedAction.FULL_EXIT),
    ],
)
def test_model_score_cannot_bypass_existing_veto(inputs, action, expected):
    inputs["vetoes"] = (replace(inputs["vetoes"][0], allow=False, required_action=action),)
    result = comparison.compare_entry_assessments(**inputs)
    assert result.baseline.final_action is result.candidate.final_action is expected


def test_abstention_is_not_negative_and_does_not_change_baseline(inputs):
    other = replace(inputs["baseline"][0], expert_id="other")
    abstain = replace(
        inputs["candidate"][0],
        applicability=ExpertApplicability.ABSTAINED,
        signal=None,
        confidence=None,
        reason_code="unknown_reference",
    )
    inputs["baseline"] = (other,)
    inputs["candidate"] = (other, abstain)
    result = comparison.compare_entry_assessments(**inputs)
    assert result.candidate.aggregate_score == result.baseline.aggregate_score
    assert result.candidate.final_action == result.baseline.final_action


def test_candidate_evidence_from_another_context_is_rejected(inputs):
    foreign = replace(inputs["candidate"][0].evidence[0], digest="sha256:" + "f" * 64)
    inputs["candidate"] = (replace(inputs["candidate"][0], evidence=(foreign,)),)
    with pytest.raises(aggregator.AggregationError, match="context_evidence_mismatch"):
        comparison.compare_entry_assessments(**inputs)


def test_absent_system_veto_and_no_applicable_candidate_fail_closed(inputs):
    inputs["vetoes"] = ()
    with pytest.raises(aggregator.AggregationError, match="missing_system_veto"):
        comparison.compare_entry_assessments(**inputs)


def test_conflicting_constraints_are_not_resolved_by_candidate(inputs):
    lower = replace(inputs["constraints"][0], minimum_position=Decimal("0.7"))
    upper = replace(inputs["constraints"][0], expert_id="second", maximum_position=Decimal("0.2"))
    inputs["constraints"] = (lower, upper)
    with pytest.raises(aggregator.AggregationError, match="constraint_conflict"):
        comparison.compare_entry_assessments(**inputs)


def test_no_applicable_candidate_is_not_a_valid_zero_score(inputs):
    inputs["candidate"] = ()
    with pytest.raises(aggregator.AggregationError, match="no_applicable_scores"):
        comparison.compare_entry_assessments(**inputs)


def test_only_changed_assessment_changes_entry_decision(inputs):
    inputs["candidate"] = (replace(inputs["candidate"][0], signal=Decimal("-0.8")),)
    result = comparison.compare_entry_assessments(**inputs)
    assert result.baseline.final_action is aggregator.GovernedAction.TRIAL_ENTRY
    assert result.candidate.final_action is aggregator.GovernedAction.NO_ACTION
    assert result.baseline.knowledge_cutoff == result.candidate.knowledge_cutoff
    assert result.baseline.snapshot_id == result.candidate.snapshot_id


def test_pairing_keeps_existing_descriptive_intervals():
    from quanthus.expert_strategy.p3_benchmark_held import paired_intervals

    assert paired_intervals is comparison.paired_intervals
    assert paired_intervals([0.01, -0.01] * 22, [0.01, -0.01] * 22, 17) == {
        "net_total_return": {"lower": "0.0", "upper": "0.0"},
        "maximum_drawdown": {"lower": "0.0", "upper": "0.0"},
    }


def test_nonzero_interval_golden_from_before_extraction():
    assert comparison.paired_intervals(
        [0.02, -0.015, 0.005] * 30, [0.01, -0.012, 0.004] * 30, 113
    ) == {
        "net_total_return": {"lower": "0.24805718208347716", "upper": "0.32275054652408164"},
        "maximum_drawdown": {"lower": "0.0009054400000000351", "upper": "0.005919000000000119"},
    }
