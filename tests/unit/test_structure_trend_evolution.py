from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal

from quanthus.stage_learning.third_group_profile import build_observation_metrics
from quanthus.structure_trend_evolution import (
    DIRECTIONAL_EFFICIENCY,
    DRAWDOWN_FROM_HIGH,
    RANGE_POSITION,
    REBOUND_FROM_LOW,
    RETURN_VELOCITY,
    VELOCITY_DELTA,
    WINDOW_RETURN,
    build_trend_evolution_facts,
    trend_fact_counts,
)
from quanthus.structure_v2_inputs import admit_daily

from .test_structure_v2_inputs import policy, row


def sequence(count: int = 90):
    values = []
    close = Decimal("100")
    for index in range(count):
        if index % 7 in {0, 1, 2, 3}:
            close += Decimal("1.5")
        else:
            close -= Decimal("0.8")
        values.append(
            row(
                index,
                str(close),
                high=close + Decimal("2"),
                low=close - Decimal("2"),
            )
        )
    return values


def build(values):
    admissions = tuple(admit_daily(item, policy()) for item in values)
    return build_trend_evolution_facts(
        admissions,
        report_start=values[0].market_date,
        report_end=values[-1].market_date,
    )


def by_key(facts, market_date: date):
    return {
        (item.concept_id, item.window_sessions, item.comparison_window_sessions): item
        for item in facts.measurements
        if item.market_date == market_date
    }


def test_formal_facts_exactly_match_frozen_third_group_metric_definitions():
    values = sequence()
    facts = build(values)
    last = values[-1]
    metrics = build_observation_metrics(values[-61:])
    observed = by_key(facts, last.market_date)

    assert observed[(WINDOW_RETURN, 5, None)].value == metrics["return_5_sessions"]
    assert observed[(WINDOW_RETURN, 20, None)].value == metrics["return_20_sessions"]
    assert observed[(WINDOW_RETURN, 60, None)].value == metrics["return_60_sessions"]
    assert observed[(DIRECTIONAL_EFFICIENCY, 5, None)].value == metrics[
        "signed_directional_efficiency_5"
    ]
    assert observed[(DIRECTIONAL_EFFICIENCY, 20, None)].value == metrics[
        "signed_directional_efficiency_20"
    ]
    assert observed[(RETURN_VELOCITY, 5, None)].value == metrics[
        "return_velocity_5_per_session"
    ]
    assert observed[(RETURN_VELOCITY, 20, None)].value == metrics[
        "return_velocity_20_per_session"
    ]
    assert observed[(VELOCITY_DELTA, 5, 20)].value == metrics["velocity_delta_5_vs_20"]
    assert observed[(RANGE_POSITION, 60, None)].value == metrics["position_in_range_60"]
    assert observed[(DRAWDOWN_FROM_HIGH, 60, None)].value == metrics[
        "drawdown_from_high_60"
    ]
    assert observed[(REBOUND_FROM_LOW, 60, None)].value == metrics["rebound_from_low_60"]


def test_fact_family_has_no_stage_or_threshold_semantics():
    facts = build(sequence())
    for item in facts.measurements:
        payload = item.to_dict()
        text = str(payload).lower()
        assert "stage_1" not in text
        assert "stage_12" not in text
        assert "threshold" not in text
        assert {"run_id", "consumed_at", "used_at", "created_at"}.isdisjoint(payload)


def test_velocity_delta_is_math_only_not_acceleration_label():
    facts = build(sequence())
    delta = next(item for item in facts.measurements if item.concept_id == VELOCITY_DELTA)
    assert delta.window_sessions == 5
    assert delta.comparison_window_sessions == 20
    assert "faster" not in delta.to_dict()
    assert "slower" not in delta.to_dict()


def test_nonadmitted_or_coordinate_change_resets_and_rewarms():
    values = sequence(70)
    values.append(row(70, close=None, quality_reason="missing_bar"))
    tail = [row(index, str(Decimal("120") + index)) for index in range(71, 75)]
    values.extend(tail)
    facts = build(values)
    unavailable = {item.market_date: item for item in facts.availability if not item.available}
    assert row(70).market_date in unavailable
    assert unavailable[row(70).market_date].reason == "missing_bar"
    assert facts.reset_counts["nonadmitted"] >= 1
    assert facts.reset_counts["insufficient_history"] >= 1


def test_listing_or_coordinate_change_cannot_carry_old_trend_history():
    values = sequence(70)
    changed = replace(
        row(70, "150", high=Decimal("152"), low=Decimal("148")),
        listing_id="listing-B",
        coordinate_epoch="coordinate-B",
    )
    values.append(changed)
    facts = build(values)
    item = next(entry for entry in facts.availability if entry.market_date == changed.market_date)
    assert not item.available
    assert item.reason == "insufficient_contiguous_history"
    assert facts.reset_counts["listing_or_coordinate_change"] == 1


def test_zero_range_preserves_other_metrics_and_marks_only_range_position_unavailable():
    values = [
        row(index, "100", high=Decimal("100"), low=Decimal("100")) for index in range(70)
    ]
    facts = build(values)
    observed = by_key(facts, values[-1].market_date)
    position = observed[(RANGE_POSITION, 60, None)]
    assert position.value is None
    assert position.availability_reason == "zero_window_range"
    assert observed[(WINDOW_RETURN, 60, None)].value == Decimal(0)
    assert observed[(DIRECTIONAL_EFFICIENCY, 20, None)].value == Decimal(0)
    assert trend_fact_counts(facts)["unavailable_measurement_count"] > 0


def test_prefix_replay_does_not_rewrite_earlier_facts():
    values = sequence(90)
    before = build(values[:75])
    after = build(values)
    cutoff = values[74].market_date
    assert [item.to_dict() for item in before.measurements] == [
        item.to_dict() for item in after.measurements if item.market_date <= cutoff
    ]
    assert [asdict(item) for item in before.availability] == [
        asdict(item) for item in after.availability if item.market_date <= cutoff
    ]
