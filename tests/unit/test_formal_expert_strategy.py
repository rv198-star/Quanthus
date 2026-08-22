from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.aggregator import GovernedAction
from quanthus.expert_protocols import EvidenceKind, ExpertEvidenceRef
from quanthus.expert_strategy import (
    ExpertStrategyRegistry,
    FormalExpertContext,
    FormalStrategyRunner,
)
from quanthus.expert_strategy.position_risk import HeldPositionFacts
from quanthus.expert_strategy.runner import position_facts_evidence
from quanthus.feature_registry import (
    FeatureCutoffRule,
    FeatureLineage,
    FeatureSourceEvidence,
)
from quanthus.label_registry import FeatureFact

ROOT = Path(__file__).resolve().parents[2]
EVENT_ID = "event:formal-strategy-test"
SNAPSHOT_ID = "snapshot-formal-strategy-test"
CUTOFF = datetime(2020, 1, 6, 7, tzinfo=timezone.utc)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _evidence(evidence_id: str) -> ExpertEvidenceRef:
    return ExpertEvidenceRef(EvidenceKind.SNAPSHOT, evidence_id, "1.0.0", _digest(evidence_id))


def _fact(feature_id: str, value: Decimal | None) -> FeatureFact:
    source = FeatureSourceEvidence(
        dataset="formal-strategy-test",
        column=feature_id,
        dataset_version="1.0.0",
        manifest_id="manifest-formal-strategy-test",
        knowledge_cutoff=CUTOFF,
    )
    lineage = FeatureLineage(
        feature_id=feature_id,
        feature_version="1.0.0",
        definition_digest=_digest(f"definition:{feature_id}"),
        snapshot_id=SNAPSHOT_ID,
        market_date=date(2020, 1, 6),
        knowledge_cutoff=CUTOFF,
        source_revision="source-formal-strategy-test",
        universe_policy_version="universe-formal-strategy-test",
        sources=(source,),
        history_sessions=60,
        cross_section_complete=None,
        cutoff_rule=FeatureCutoffRule.SNAPSHOT_OR_EARLIER,
    )
    return FeatureFact(
        security_code="security-cn-000001",
        feature_id=feature_id,
        feature_version="1.0.0",
        lineage=lineage,
        value=value,
        unavailable_reason=None if value is not None else "feature_unavailable",
    )


def _context(
    *,
    volatility: Decimal | None = Decimal("0.20"),
    rsi: Decimal | None = Decimal("60"),
    position_state: str = "empty",
    position_evidence: ExpertEvidenceRef | None = None,
) -> FormalExpertContext:
    additional = [_evidence(EVENT_ID), _evidence(f"trend-observation:{EVENT_ID}")]
    if position_evidence is not None:
        additional.append(position_evidence)
    return FormalExpertContext(
        security_code="security-cn-000001",
        snapshot_id=SNAPSHOT_ID,
        knowledge_cutoff=CUTOFF,
        position_state=position_state,
        feature_facts=(
            _fact("price.rsi14", rsi),
            _fact("risk.realized_volatility20", volatility),
        ),
        label_results=(),
        additional_evidence_refs=tuple(additional),
    )


@pytest.fixture(scope="module")
def runner() -> FormalStrategyRunner:
    registry = ExpertStrategyRegistry(ROOT)
    return FormalStrategyRunner(registry.card, registry.topology_manifest.digest)


def _held_facts(
    *,
    sessions: int = 3,
    marked_return: Decimal | None = Decimal("-0.05"),
    reductions: int = 0,
) -> HeldPositionFacts:
    evidence = position_facts_evidence(
        unit_id="unit-formal-test",
        holding_sessions=sessions,
        marked_return=marked_return,
        prior_strategy_reduction_count=reductions,
    )
    return HeldPositionFacts(sessions, marked_return, reductions, evidence)


def test_stable_entry_is_scored_once_and_capped_by_position_risk(
    runner: FormalStrategyRunner,
) -> None:
    decision = runner.evaluate_entry(
        event_id=EVENT_ID,
        context=_context(),
        trend_state="stable",
        entry_evidence_ready=True,
    )

    assert decision.action.final_action is GovernedAction.TRIAL_ENTRY
    assert decision.action.aggregate_score == Decimal("1")
    assert decision.target_maximum_position == Decimal("0.20")
    assert len(decision.action.scoring_outputs) == 1


@pytest.mark.parametrize(
    ("volatility", "rsi"),
    ((Decimal("0.51"), Decimal("60")), (Decimal("0.20"), Decimal("76"))),
)
def test_position_risk_blocks_entry_above_either_threshold(
    runner: FormalStrategyRunner, volatility: Decimal, rsi: Decimal
) -> None:
    decision = runner.evaluate_entry(
        event_id=EVENT_ID,
        context=_context(volatility=volatility, rsi=rsi),
        trend_state="stable",
        entry_evidence_ready=True,
    )

    assert decision.action.final_action is GovernedAction.NO_ACTION
    assert decision.governance.required_action.value == "block_new"
    assert "expert.position-risk-review" in decision.governance.terminating_veto_ids


@pytest.mark.parametrize("missing", ("volatility", "rsi", "trend"))
def test_missing_entry_evidence_fails_closed(
    runner: FormalStrategyRunner, missing: str
) -> None:
    decision = runner.evaluate_entry(
        event_id=EVENT_ID,
        context=_context(
            volatility=None if missing == "volatility" else Decimal("0.20"),
            rsi=None if missing == "rsi" else Decimal("60"),
        ),
        trend_state=None if missing == "trend" else "stable",
        entry_evidence_ready=True,
    )

    assert decision.action.final_action is GovernedAction.NO_ACTION
    assert decision.governance.required_action.value == "block_new"
    if missing == "trend":
        assert decision.action.reason_codes == ("all_scoring_abstained_block_new",)


def test_held_position_reduces_once_then_returns_to_hold(
    runner: FormalStrategyRunner,
) -> None:
    first = _held_facts()
    reduced = runner.evaluate_held(
        event_id=EVENT_ID,
        context=_context(position_state="held", position_evidence=first.evidence),
        position_facts=first,
    )
    already_reduced = _held_facts(reductions=1)
    held = runner.evaluate_held(
        event_id=EVENT_ID,
        context=_context(position_state="held", position_evidence=already_reduced.evidence),
        position_facts=already_reduced,
    )

    assert reduced.action.final_action is GovernedAction.REDUCE
    assert reduced.target_maximum_position == Decimal("0.10")
    assert held.action.final_action is GovernedAction.NO_ACTION
    assert held.governance.required_action.value == "none"


def test_extreme_held_risk_forces_exit(runner: FormalStrategyRunner) -> None:
    facts = _held_facts(marked_return=Decimal("0"))
    decision = runner.evaluate_held(
        event_id=EVENT_ID,
        context=_context(
            volatility=Decimal("0.71"),
            rsi=Decimal("86"),
            position_state="held",
            position_evidence=facts.evidence,
        ),
        position_facts=facts,
    )

    assert decision.action.final_action is GovernedAction.FULL_EXIT
    assert decision.target_maximum_position == Decimal("0")


def test_missing_held_evidence_never_forces_sale(runner: FormalStrategyRunner) -> None:
    facts = _held_facts(marked_return=None)
    decision = runner.evaluate_held(
        event_id=EVENT_ID,
        context=_context(position_state="held", position_evidence=facts.evidence),
        position_facts=facts,
    )

    assert decision.action.final_action is GovernedAction.NO_ACTION
    assert decision.governance.required_action.value == "none"


def test_fallbacks_preserve_base_rules_and_joint_entry_fails_closed(
    runner: FormalStrategyRunner,
) -> None:
    position_fallback = runner.evaluate_entry(
        event_id=EVENT_ID,
        context=_context(),
        trend_state="stable",
        entry_evidence_ready=True,
        intervention_id="intervention.position-risk-base-policy",
    )
    joint_entry = runner.evaluate_entry(
        event_id=EVENT_ID,
        context=_context(),
        trend_state="stable",
        entry_evidence_ready=True,
        intervention_id="intervention.both-experts-fallback",
    )
    facts = _held_facts()
    joint_held = runner.evaluate_held(
        event_id=EVENT_ID,
        context=_context(position_state="held", position_evidence=facts.evidence),
        position_facts=facts,
        intervention_id="intervention.both-experts-fallback",
    )

    assert position_fallback.action.final_action is GovernedAction.TRIAL_ENTRY
    assert position_fallback.target_maximum_position == Decimal("0.30")
    assert joint_entry.action.final_action is GovernedAction.NO_ACTION
    assert joint_entry.action.reason_codes == ("all_scoring_abstained_block_new",)
    assert joint_held.action.final_action is GovernedAction.NO_ACTION
    assert joint_held.action.reason_codes == ("base_position_management_only",)


def test_unknown_intervention_fails_closed_and_decisions_are_byte_stable(
    runner: FormalStrategyRunner,
) -> None:
    context = _context()
    first = runner.evaluate_entry(
        event_id=EVENT_ID,
        context=context,
        trend_state="stable",
        entry_evidence_ready=True,
    )
    second = runner.evaluate_entry(
        event_id=EVENT_ID,
        context=context,
        trend_state="stable",
        entry_evidence_ready=True,
    )

    assert first.canonical_bytes == second.canonical_bytes
    assert b"order" not in first.canonical_bytes
    assert b"execution_authorized" not in first.canonical_bytes
    with pytest.raises(ValueError, match="not admitted"):
        runner.evaluate_entry(
            event_id=EVENT_ID,
            context=context,
            trend_state="stable",
            entry_evidence_ready=True,
            intervention_id="intervention.unknown",
        )
