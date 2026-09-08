from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from quanthus.stage_learning.core import (
    ActivityEngine,
    InferenceStatus,
    PredicateState,
    build_activity_observations,
    build_stage34_decisions,
)
from quanthus.stage_learning.oracle import StageEngineeringOracleError, validate_stage34_records
from quanthus.stage_learning.source import DevelopmentReadScope, StageDevelopmentSourceError
from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import admit_daily

from .test_structure_v2_inputs import SHA, policy, row


def admissions(values):
    return tuple(admit_daily(item, policy()) for item in values)


def stage_sequence(*, breakout_volume: Decimal | None = Decimal("1500")):
    values = [row(index, "95", volume=Decimal("1000")) for index in range(25)]
    values.extend(
        [
            row(25, "101", high=Decimal("102"), volume=breakout_volume),
            row(26, "101.5", high=Decimal("103"), volume=Decimal("1000")),
            row(27, "102", high=Decimal("103"), volume=Decimal("1000")),
        ]
    )
    return values


def rules():
    return {
        "probe_volume_ratio_min": Decimal("1.25"),
        "acceptance_hold_sessions": 2,
        "acceptance_signed_distance_min": Decimal("0"),
        "acceptance_window_volume_ratio_min": Decimal("1"),
    }


def test_development_scope_rejects_holdout_before_any_source_read():
    scope = DevelopmentReadScope(
        date(2012, 1, 1), date(2020, 1, 1), date(2021, 12, 31)
    )
    assert scope.years[-1] == 2021
    with pytest.raises(StageDevelopmentSourceError, match="outside"):
        DevelopmentReadScope(date(2020, 1, 1), date(2021, 1, 1), date(2022, 1, 1))
    with pytest.raises(StageDevelopmentSourceError, match="outside"):
        DevelopmentReadScope(date(2011, 12, 31), date(2012, 1, 1), date(2012, 2, 1))


def test_activity_fact_is_causal_and_batch_incremental_exact():
    observed = admissions(stage_sequence())
    batch = build_activity_observations(observed)
    engine = ActivityEngine(window=20)
    incremental = (*engine.consume(observed[:13]), *engine.consume(observed[13:]))
    assert [item.to_dict() for item in batch] == [item.to_dict() for item in incremental]
    breakout = next(item for item in batch if item.market_date == observed[25].row.market_date)
    assert breakout.available
    assert breakout.volume_ratio == Decimal("1.5")


def test_missing_volume_is_unknown_not_zero_and_contaminates_prior_window():
    observed = admissions(stage_sequence(breakout_volume=None))
    facts = build_activity_observations(observed)
    current = facts[25]
    following = facts[26]
    assert not current.available and current.reason == "current_volume_unavailable"
    assert not following.available and following.reason == "prior_volume_unavailable"
    assert current.volume_ratio is None and following.volume_ratio is None


def test_coordinate_reset_clears_activity_history_and_requires_rewarm():
    values = stage_sequence()
    values[25] = replace(
        values[25],
        coordinate_epoch="split-v2",
        reset_reason="corporate_action_coordinate_change",
    )
    facts = build_activity_observations(admissions(values))
    assert not facts[25].available
    assert facts[25].reason == "insufficient_history"


def test_stage3_then_stage4_share_frozen_anchor_and_are_not_official_labels():
    observed = admissions(stage_sequence())
    structure = build_structure_v2(
        observed, spec_digest=SHA, calendar_ref="calendar-v1", lookbacks=(20,)
    )
    activity = build_activity_observations(observed)
    candidates, decisions = build_stage34_decisions(structure, activity, **rules())
    confirmed = [item for item in decisions if item.inference_status is InferenceStatus.CONFIRMED]
    assert [(item.market_date, item.primary_stage) for item in confirmed] == [
        (observed[25].row.market_date, 3),
        (observed[27].row.market_date, 4),
    ]
    stage3 = next(item for item in candidates if item.stage_code == 3)
    stage4 = next(
        item
        for item in candidates
        if item.stage_code == 4 and item.candidate_state is PredicateState.TRUE
    )
    assert stage3.anchor_key == stage4.anchor_key
    assert stage3.anchor_revision == stage4.anchor_revision
    assert stage4.reference_role == "same_frozen_high_as_acceptance_support"
    assert not stage3.official_label and not stage4.official_label
    assert all(not item.official_label for item in decisions)
    assert validate_stage34_records(candidates, decisions)["rule_conflict_count"] == 0


def test_missing_activity_makes_structural_probe_abstain_instead_of_price_only_label():
    observed = admissions(stage_sequence(breakout_volume=None))
    structure = build_structure_v2(
        observed, spec_digest=SHA, calendar_ref="calendar-v1", lookbacks=(20,)
    )
    candidates, decisions = build_stage34_decisions(
        structure, build_activity_observations(observed), **rules()
    )
    stage3 = next(item for item in candidates if item.stage_code == 3)
    decision = next(item for item in decisions if item.market_date == stage3.market_date)
    assert stage3.candidate_state is PredicateState.UNKNOWN
    assert decision.inference_status is InferenceStatus.ABSTAIN
    assert decision.primary_stage is None


def test_same_anchor_loss_prevents_stage4_even_if_later_price_is_above_a_moving_level():
    values = stage_sequence()
    values[26] = row(26, "99", high=Decimal("103"), volume=Decimal("1000"))
    values[27] = row(27, "101", high=Decimal("103"), volume=Decimal("1000"))
    observed = admissions(values)
    structure = build_structure_v2(
        observed, spec_digest=SHA, calendar_ref="calendar-v1", lookbacks=(20,)
    )
    candidates, decisions = build_stage34_decisions(
        structure, build_activity_observations(observed), **rules()
    )
    assert any(item.stage_code == 3 for item in candidates)
    assert not any(item.primary_stage == 4 for item in decisions)


def test_engineering_oracle_detects_candidate_state_tamper():
    observed = admissions(stage_sequence())
    structure = build_structure_v2(
        observed, spec_digest=SHA, calendar_ref="calendar-v1", lookbacks=(20,)
    )
    candidates, decisions = build_stage34_decisions(
        structure, build_activity_observations(observed), **rules()
    )
    first = candidates[0]
    corrupted = (replace(first, candidate_state=PredicateState.FALSE), *candidates[1:])
    with pytest.raises(StageEngineeringOracleError, match="predicate evidence"):
        validate_stage34_records(corrupted, decisions)


def test_future_suffix_does_not_rewrite_earlier_engineering_records():
    observed = admissions(stage_sequence())
    prefix = observed[:27]
    before_structure = build_structure_v2(
        prefix, spec_digest=SHA, calendar_ref="calendar-v1", lookbacks=(20,)
    )
    after_structure = build_structure_v2(
        observed, spec_digest=SHA, calendar_ref="calendar-v1", lookbacks=(20,)
    )
    before = build_stage34_decisions(
        before_structure, build_activity_observations(prefix), **rules()
    )
    after = build_stage34_decisions(
        after_structure, build_activity_observations(observed), **rules()
    )
    cutoff = prefix[-1].row.market_date
    assert [item.to_dict() for item in before[0]] == [
        item.to_dict() for item in after[0] if item.market_date <= cutoff
    ]
    assert [item.to_dict() for item in before[1]] == [
        item.to_dict() for item in after[1] if item.market_date <= cutoff
    ]
