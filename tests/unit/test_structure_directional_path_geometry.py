from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal

from quanthus.structure_directional_path_geometry import (
    CONTRACT_DIGEST,
    COUNTER_DIRECTION_EXCURSION_SHARE,
    RANGE_TRAVERSAL,
    build_directional_path_geometry_facts,
    counter_direction_excursion_share,
    directional_path_geometry_counts,
    load_contract,
    range_traversal,
)
from quanthus.structure_v2_inputs import admit_daily

from .test_structure_v2_inputs import policy, row


def path(values: list[str]):
    return [
        row(
            index,
            close=value,
            high=Decimal(value) + Decimal("1"),
            low=Decimal(value) - Decimal("1"),
        )
        for index, value in enumerate(values)
    ]


def build(values):
    admissions = tuple(admit_daily(item, policy()) for item in values)
    return build_directional_path_geometry_facts(
        admissions,
        report_start=values[0].market_date,
        report_end=values[-1].market_date,
    )


def latest(facts, day):
    return {
        item.concept_id: item
        for item in facts.measurements
        if item.market_date == day
    }


def test_frozen_fact_contract_has_no_runtime_or_stage_authority():
    contract = load_contract()
    assert contract["authority"]["market_data_read_authorized"] is False
    assert contract["authority"]["runtime_job_submission_authorized"] is False
    assert contract["authority"]["parameter_search_authorized"] is False
    assert contract["semantics"]["stage_projection_forbidden"] is True
    from quanthus.structure_v2_inputs import digest

    assert digest(contract) == CONTRACT_DIGEST


def test_monotonic_paths_have_full_traversal_and_no_counter_excursion():
    up = path([str(100 + index) for index in range(21)])
    down = path([str(120 - index) for index in range(21)])
    assert range_traversal(up) == Decimal(1)
    assert counter_direction_excursion_share(up) == Decimal(0)
    assert range_traversal(down) == Decimal(1)
    assert counter_direction_excursion_share(down) == Decimal(0)


def test_full_reversal_path_proves_traversal_alone_is_insufficient():
    values = [
        100, 102, 104, 106, 108, 110, 108, 106, 104, 102, 100,
        102, 104, 106, 108, 110, 110, 110, 110, 110, 110,
    ]
    rows = path([str(value) for value in values])
    assert range_traversal(rows) == Decimal(1)
    assert counter_direction_excursion_share(rows) == Decimal(1)


def test_zero_net_direction_keeps_traversal_but_marks_counter_measurement_unavailable():
    closes = ["100", "110", "100"]
    closes.extend("100" for _ in range(18))
    rows = path(closes)
    assert range_traversal(rows) == Decimal(0)
    assert counter_direction_excursion_share(rows) is None
    facts = build(rows)
    observed = latest(facts, rows[-1].market_date)
    counter = observed[COUNTER_DIRECTION_EXCURSION_SHARE]
    assert counter.value is None
    assert counter.availability_reason == "zero_net_direction"
    assert observed[RANGE_TRAVERSAL].value == Decimal(0)
    assert directional_path_geometry_counts(facts)["unavailable_measurement_count"] == 1


def test_one_step_repricing_then_flat_is_coherent_daily_geometry():
    closes = ["100", "110"] + ["110"] * 19
    rows = path(closes)
    assert range_traversal(rows) == Decimal(1)
    assert counter_direction_excursion_share(rows) == Decimal(0)


def test_values_are_scale_invariant():
    base = path([str(value) for value in range(100, 121)])
    scaled = path([str(value * 10) for value in range(100, 121)])
    assert range_traversal(base) == range_traversal(scaled)
    assert counter_direction_excursion_share(base) == counter_direction_excursion_share(scaled)


def test_fact_family_contains_no_stage_threshold_or_runtime_clocks():
    facts = build(path([str(100 + (index % 5)) for index in range(30)]))
    for item in facts.measurements:
        payload = item.to_dict()
        text = str(payload).lower()
        assert "stage_0" not in text
        assert "stage_2" not in text
        assert "threshold" not in text
        assert {"run_id", "consumed_at", "used_at", "created_at"}.isdisjoint(payload)


def test_nonadmitted_listing_and_hidden_gap_reset_and_rewarm():
    values = path([str(100 + index) for index in range(25)])
    values.append(row(25, close=None, quality_reason="missing_bar"))
    values.extend(
        row(
            index,
            str(130 + index - 26),
            high=Decimal(131 + index - 26),
            low=Decimal(129 + index - 26),
        )
        for index in range(26, 47)
    )
    facts = build(values)
    missing_day = values[25].market_date
    missing = next(item for item in facts.availability if item.market_date == missing_day)
    assert not missing.available and missing.reason == "missing_bar"
    assert facts.reset_counts["nonadmitted"] >= 1

    changed = path([str(100 + index) for index in range(21)])
    changed[-1] = replace(
        changed[-1], listing_id="listing-B", coordinate_epoch="coordinate-B"
    )
    changed_facts = build(changed)
    current = next(
        item for item in changed_facts.availability if item.market_date == changed[-1].market_date
    )
    assert not current.available
    assert current.reason == "insufficient_contiguous_history"
    assert changed_facts.reset_counts["listing_or_coordinate_change"] == 1

    gap = path([str(100 + index) for index in range(21)])
    gap[-1] = replace(gap[-1], session_index=99)
    gap_facts = build(gap)
    assert gap_facts.reset_counts["hidden_session_gap"] == 1
    assert not gap_facts.availability[-1].available


def test_prefix_replay_does_not_rewrite_earlier_path_facts():
    values = path([str(100 + (index % 7)) for index in range(50)])
    before = build(values[:35])
    after = build(values)
    cutoff = values[34].market_date
    assert [item.to_dict() for item in before.measurements] == [
        item.to_dict() for item in after.measurements if item.market_date <= cutoff
    ]
    assert [asdict(item) for item in before.availability] == [
        asdict(item) for item in after.availability if item.market_date <= cutoff
    ]
