from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal

from quanthus.stage_learning.third_group_profile import build_observation_metrics
from quanthus.structure_order_facts import (
    RANGE_CENTER_SHIFT,
    RANGE_WIDTH,
    WIDTH_CHANGE,
    build_structure_order_facts,
    structure_order_counts,
)
from quanthus.structure_v2_inputs import admit_daily

from .test_structure_v2_inputs import policy, row


def sequence(count: int = 90):
    values = []
    close = Decimal("100")
    for index in range(count):
        close += Decimal("1.2") if index % 6 < 3 else Decimal("-0.7")
        span = Decimal("2") + Decimal(index % 5) / Decimal("10")
        values.append(row(index, str(close), high=close + span, low=close - span))
    return values


def build(values):
    admissions = tuple(admit_daily(item, policy()) for item in values)
    return build_structure_order_facts(
        admissions,
        report_start=values[0].market_date,
        report_end=values[-1].market_date,
    )


def by_key(facts, day):
    return {
        (item.concept_id, item.window_sessions, item.comparison_window_sessions): item
        for item in facts.measurements
        if item.market_date == day
    }


def test_structure_order_facts_match_frozen_third_group_geometry_exactly():
    values = sequence()
    facts = build(values)
    metrics = build_observation_metrics(values[-61:])
    observed = by_key(facts, values[-1].market_date)
    assert observed[(RANGE_WIDTH, 10, None)].value == metrics["range_width_10"]
    assert observed[(RANGE_WIDTH, 20, None)].value == metrics["range_width_20"]
    assert observed[(RANGE_WIDTH, 60, None)].value == metrics["range_width_60"]
    assert observed[(WIDTH_CHANGE, 10, 10)].value == metrics[
        "range_width_change_10_vs_prior10"
    ]
    assert observed[(WIDTH_CHANGE, 20, 20)].value == metrics[
        "range_width_change_20_vs_prior20"
    ]
    assert observed[(RANGE_CENTER_SHIFT, 10, 10)].value == metrics[
        "center_shift_10_vs_prior10"
    ]
    assert observed[(RANGE_CENTER_SHIFT, 20, 20)].value == metrics[
        "center_shift_20_vs_prior20"
    ]


def test_structure_order_facts_contain_no_order_label_or_stage_threshold():
    facts = build(sequence())
    for item in facts.measurements:
        payload = item.to_dict()
        text = str(payload).lower()
        assert "stage" not in text
        assert "orderly" not in text
        assert "disorder" not in text
        assert "threshold" not in text
        assert {"run_id", "consumed_at", "used_at", "created_at"}.isdisjoint(payload)


def test_prior_zero_width_only_suppresses_width_change_not_center_shift():
    values = sequence(80)
    for index in range(60, 70):
        values[index] = row(
            index,
            "120",
            high=Decimal("120"),
            low=Decimal("120"),
        )
    # Day 79 current 10-window compares against the zero-width 60:70 prior window.
    facts = build(values)
    observed = by_key(facts, values[-1].market_date)
    width_change = observed[(WIDTH_CHANGE, 10, 10)]
    center_shift = observed[(RANGE_CENTER_SHIFT, 10, 10)]
    assert width_change.value is None
    assert width_change.availability_reason == "prior_zero_range_width"
    assert center_shift.value is not None
    assert observed[(RANGE_WIDTH, 10, None)].value is not None
    assert structure_order_counts(facts)["unavailable_measurement_count"] >= 1


def test_nonadmitted_row_resets_and_rewarms_without_order_claim():
    values = sequence(70)
    values.append(row(70, close=None, quality_reason="missing_bar"))
    values.extend(
        row(index, str(Decimal("140") + index), high=Decimal("220"), low=Decimal("200"))
        for index in range(71, 75)
    )
    facts = build(values)
    missing = next(item for item in facts.availability if item.market_date == row(70).market_date)
    assert not missing.available and missing.reason == "missing_bar"
    assert facts.reset_counts["nonadmitted"] >= 1
    assert facts.reset_counts["insufficient_history"] >= 1


def test_listing_or_coordinate_change_resets_history():
    values = sequence(70)
    changed = replace(
        row(70, "150", high=Decimal("152"), low=Decimal("148")),
        listing_id="listing-B",
        coordinate_epoch="coordinate-B",
    )
    values.append(changed)
    facts = build(values)
    current = next(item for item in facts.availability if item.market_date == changed.market_date)
    assert not current.available
    assert current.reason == "insufficient_contiguous_history"
    assert facts.reset_counts["listing_or_coordinate_change"] == 1


def test_prefix_replay_does_not_rewrite_structure_order_facts():
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
