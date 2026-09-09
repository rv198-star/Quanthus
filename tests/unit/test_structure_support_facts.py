from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from quanthus.structure_support_facts import (
    PROCESS_CONCEPT,
    REBOUND_CONCEPT,
    build_support_failure_facts,
    project_stage_10_11,
)
from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import admit_daily

from .test_structure_v2_inputs import SHA, policy, row


def sequence():
    values = [row(index) for index in range(65)]
    values.extend(
        [
            row(65, "101", high=Decimal("103"), low=Decimal("95")),
            row(66, "89", high=Decimal("92"), low=Decimal("88")),
            row(67, "86", high=Decimal("90"), low=Decimal("85")),
            row(68, "85", high=Decimal("88"), low=Decimal("84")),
            row(69, "86", high=Decimal("89"), low=Decimal("85")),
            row(70, "87", high=Decimal("90"), low=Decimal("86")),
            row(71, "88", high=Decimal("90"), low=Decimal("87")),
            row(72, "89.5", high=Decimal("90"), low=Decimal("89")),
            row(73, "91", high=Decimal("92"), low=Decimal("90")),
        ]
    )
    return values


def build(values):
    admissions = tuple(admit_daily(item, policy()) for item in values)
    structure = build_structure_v2(
        admissions, spec_digest=SHA, calendar_ref="calendar-v1", lookbacks=(20, 60)
    )
    facts = build_support_failure_facts(
        admissions, structure, spec_digest=SHA, maximum_sessions=60
    )
    return admissions, structure, facts


def test_support_role_binds_exact_breakout_start_rolling_low_anchor():
    _admissions, structure, facts = build(sequence())
    scale20 = next(item for item in facts.bindings if item.scale == 20)
    assert scale20.support_role == "breakout-start-rolling-low-candidate"
    assert scale20.support_level == Decimal("90")
    support = next(
        item for item in structure.anchors if item.revision_id == scale20.support_anchor_revision
    )
    breakout = next(
        item for item in structure.anchors if item.revision_id == scale20.breakout_anchor_revision
    )
    assert support.side == "low" and breakout.side == "high"
    assert support.window_start == breakout.window_start
    assert support.window_end == breakout.window_end
    assert scale20.to_dict()["concept_id"] == PROCESS_CONCEPT


def test_first_break_persistence_and_reclaim_use_same_support_identity():
    _admissions, _structure, facts = build(sequence())
    events = [item for item in facts.interactions if item.scale == 20]
    assert [item.kind for item in events] == ["close-cross-below", "close-reclaim"]
    assert len({item.anchor_revision for item in events}) == 1
    assert events[1].previous_loss_ref == events[0].revision_id

    observations = [item for item in facts.observations if item.scale == 20]
    broken = next(item for item in observations if item.market_date == row(66).market_date)
    age5 = next(item for item in observations if item.market_date == row(71).market_date)
    reclaim = next(item for item in observations if item.market_date == row(73).market_date)
    assert broken.phase == "unreclaimed" and broken.unreclaimed_age_sessions == 0
    assert age5.phase == "unreclaimed" and age5.unreclaimed_age_sessions == 5
    assert reclaim.phase == "reclaimed" and reclaim.unreclaimed_age_sessions is None
    assert reclaim.post_break_rebound_revision_ref is None


def test_post_break_rebound_is_continuous_fact_not_stage_threshold():
    _admissions, _structure, facts = build(sequence())
    scale20 = [item for item in facts.rebounds if item.scale == 20]
    latest = next(item for item in scale20 if item.market_date == row(72).market_date)
    assert latest.post_break_running_low == Decimal("85")
    assert latest.value == Decimal("89.5") / Decimal("85") - 1
    assert latest.to_dict()["concept_id"] == REBOUND_CONCEPT


def test_stage_11_consumes_prior_stage_10_ref_even_when_current_depth_is_below_two_percent():
    _admissions, _structure, facts = build(sequence())
    projected = [
        item
        for item in project_stage_10_11(facts)
        if item.process_id
        == next(binding.process_id for binding in facts.bindings if binding.scale == 20)
    ]
    day71 = next(item for item in projected if item.market_date == row(71).market_date)
    day72 = next(item for item in projected if item.market_date == row(72).market_date)
    reclaim = next(item for item in projected if item.market_date == row(73).market_date)
    assert day71.stage_10_active is True and day71.stage_11_active is False
    assert day71.prior_stage_10_confirmation_ref is not None
    # Day 72 is only 0.56% below support, so a current-depth Stage-10 recomputation would fail.
    assert day72.stage_10_active is True and day72.stage_11_active is True
    assert day72.prior_stage_10_confirmation_ref == day71.prior_stage_10_confirmation_ref
    assert reclaim.stage_10_active is False and reclaim.stage_11_active is False
    assert reclaim.prior_stage_10_confirmation_ref == day71.prior_stage_10_confirmation_ref


def test_missing_or_coordinate_discontinuity_censors_without_fabricating_failure():
    values = sequence()[:66]
    values.append(row(66, close=None, quality_reason="missing_bar"))
    admissions = tuple(admit_daily(item, policy()) for item in values)
    structure = build_structure_v2(
        admissions, spec_digest=SHA, calendar_ref="calendar-v1", lookbacks=(20, 60)
    )
    facts = build_support_failure_facts(admissions, structure, spec_digest=SHA)
    censored = [item for item in facts.observations if item.market_date == row(66).market_date]
    assert censored and all(item.phase == "censored" for item in censored)
    assert all(item.break_interaction_ref is None for item in censored)
    assert not facts.interactions


def test_prefix_replay_does_not_rewrite_earlier_support_facts():
    before_values = sequence()[:72]
    after_values = sequence()
    _a1, _s1, before = build(before_values)
    _a2, _s2, after = build(after_values)
    cutoff = before_values[-1].market_date
    assert [asdict(item) for item in before.bindings] == [asdict(item) for item in after.bindings]
    assert [item.to_dict() for item in before.observations] == [
        item.to_dict() for item in after.observations if item.market_date <= cutoff
    ]
    assert [item.to_dict() for item in before.rebounds] == [
        item.to_dict() for item in after.rebounds if item.market_date <= cutoff
    ]


def test_fact_identity_contains_no_use_or_run_metadata():
    _admissions, _structure, facts = build(sequence())
    for collection in (
        facts.bindings,
        facts.distances,
        facts.interactions,
        facts.rebounds,
        facts.observations,
    ):
        for item in collection:
            payload = item.to_dict()
            assert {"run_id", "consumed_at", "used_at", "created_at"}.isdisjoint(payload)
    assert build(sequence())[2].digest == facts.digest
