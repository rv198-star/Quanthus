from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.aggregator import GovernedAction
from quanthus.expert_protocols import RequiredAction
from quanthus.expert_strategy import ExpertStrategyRegistry, FormalStrategyRunner
from quanthus.expert_strategy.held_structure_shadow import (
    SupportDecisionInput,
    evaluate_shadow,
    restore_context,
    restore_position_facts,
    support_decision_inputs,
)
from quanthus.stage_projection import load_stage_projection_contract
from quanthus.structure_v2_inputs import canonical

from .test_formal_expert_strategy import EVENT_ID, _context, _held_facts
from .test_structure_support_facts import build, sequence

ROOT = Path(__file__).resolve().parents[2]
SHA = "sha256:" + "a" * 64


@pytest.fixture
def runner():
    registry = ExpertStrategyRegistry(ROOT)
    return FormalStrategyRunner(registry.card, registry.topology_manifest.digest)


def support(**kwargs):
    base = SupportDecisionInput(
        "security-cn-000001",
        date(2020, 1, 6),
        True,
        "unreclaimed",
        "process",
        SHA,
        "unreclaimed",
        5,
        Decimal("-0.03"),
        Decimal("0.01"),
        True,
        date(2020, 1, 6),
        True,
        False,
        SHA,
        SHA,
    )
    return replace(base, **kwargs)


def evaluate(
    runner,
    *,
    structural=None,
    sessions=3,
    reductions=0,
    quantity=300,
    base_action=RequiredAction.NONE,
    representation="continuous",
    price=Decimal("100"),
):
    facts = _held_facts(sessions=sessions, reductions=reductions)
    return evaluate_shadow(
        runner,
        event_id=EVENT_ID,
        context=_context(position_state="dynamic", position_evidence=facts.evidence),
        position_facts=facts,
        base_required_action=base_action,
        base_reason_code="base_position_management_clear",
        support=structural or support(),
        representation=representation,
        quantity=quantity,
        target_notional=Decimal("100000"),
        last_mark_price=price,
    )


def test_confirmed_structure_changes_a_real_aggregator_decision(runner):
    value = evaluate(runner)
    assert value.baseline.action.final_action is GovernedAction.NO_ACTION
    assert value.candidate.action.final_action is GovernedAction.REDUCE
    assert value.final_action_changed and value.proposed and value.target_quantity == 100
    assert value.candidate.target_maximum_position == Decimal("0.10")
    assert {r.expert_id for r in value.candidate.governance.veto_outputs} >= {
        "system.formal-entry-guardian",
        "expert.position-risk-review",
        "shadow.support-failure",
    }


def test_base_force_exit_wins_over_optional_reduction(runner):
    value = evaluate(runner, base_action=RequiredAction.FORCE_LIQUIDATE)
    assert value.proposed and not value.final_action_changed
    assert value.candidate.action.final_action is GovernedAction.FULL_EXIT


@pytest.mark.parametrize("base_action", [RequiredAction.NONE, RequiredAction.FORCE_LIQUIDATE])
def test_unknown_cannot_sell_or_remove_baseline_safety(runner, base_action):
    value = evaluate(runner, structural=support(available=False), base_action=base_action)
    assert not value.proposed
    assert value.candidate == value.baseline


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"sessions": 2}, "minimum_holding_not_met"),
        ({"reductions": 1}, "prior_reduction_preserved"),
        ({"quantity": 100}, "already_at_or_below_cap"),
        ({"quantity": 50}, "already_at_or_below_cap"),
        ({"price": None}, "position_mark_unavailable_no_extra_sell"),
        (
            {
                "structural": support(
                    continuous_confirmed=False, stage10_active=False, stage11_active=True
                )
            },
            "structural_not_confirmed",
        ),
    ],
)
def test_existing_holding_and_reduce_only_limits_are_preserved(runner, kwargs, reason):
    value = evaluate(runner, **kwargs)
    assert not value.proposed and value.reason == reason
    assert value.candidate == value.baseline


def test_same_information_and_policy_have_equal_final_decisions(runner):
    left = evaluate(runner, representation="continuous")
    right = evaluate(runner, representation="stage10_11")
    assert left.candidate == right.candidate


@pytest.mark.parametrize(
    "changes",
    [
        {"market_date": date(2020, 1, 7)},
        {"confirmation_date": date(2020, 1, 7)},
        {"confirmation_ref": None},
    ],
)
def test_future_or_unproven_confirmation_is_rejected(runner, changes):
    with pytest.raises(ValueError):
        evaluate(runner, structural=support(**changes))


def test_context_roundtrip_preserves_frozen_decision_identity(runner):
    facts = _held_facts()
    context = _context(position_state="dynamic", position_evidence=facts.evidence)
    restored = restore_context(json.loads(canonical(asdict(context))))
    restored_facts = restore_position_facts(json.loads(canonical(asdict(facts))))
    assert restored == context and restored_facts == facts
    assert (
        runner.evaluate_held(event_id=EVENT_ID, context=context, position_facts=facts).digest
        == runner.evaluate_held(
            event_id=EVENT_ID, context=restored, position_facts=restored_facts
        ).digest
    )


def test_continuous_confirmation_persists_after_depth_recovers_and_is_prefix_causal():
    contract = load_stage_projection_contract(ROOT / "config/stage-10-11-projection.v1.json")
    full = support_decision_inputs(build(sequence())[2], contract)
    prefix = support_decision_inputs(build(sequence()[:72])[2], contract)
    assert {k: r for k, r in full.items() if k[1] <= sequence()[71].market_date} == prefix
    row = full[(sequence()[72].security_code, sequence()[72].market_date)]
    assert row.signed_distance > Decimal("-0.02")
    assert row.continuous_confirmed and row.stage10_active and row.stage11_active
    final = full[(sequence()[73].security_code, sequence()[73].market_date)]
    assert not final.continuous_confirmed and not final.stage10_active


def test_multiple_supports_are_retained_without_result_selected_anchor():
    from .test_structure_v2_inputs import row

    values = sequence()[:66]
    values += [
        row(66, "99", high=Decimal("101"), low=Decimal("95")),
        row(67, "102", high=Decimal("104"), low=Decimal("95")),
        row(68, "105", high=Decimal("106"), low=Decimal("95")),
    ]
    values += [row(i, "88", high=Decimal("89"), low=Decimal("87")) for i in range(69, 76)]
    contract = load_stage_projection_contract(ROOT / "config/stage-10-11-projection.v1.json")
    mapped = support_decision_inputs(build(values)[2], contract)
    last = mapped[(values[-1].security_code, values[-1].market_date)]
    assert last.process_count == len(last.process_revisions) == 2
    assert last.continuous_confirmed == last.stage10_active is True
    prefix = support_decision_inputs(build(values[:-1])[2], contract)
    assert {k: v for k, v in mapped.items() if k[1] < values[-1].market_date} == prefix
