from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal

from quanthus.structure_v2 import build_structure_v2
from quanthus.structure_v2_inputs import admit_daily

from .test_structure_v2_inputs import SHA, policy, row


def build(values):
    return build_structure_v2(tuple(admit_daily(item, policy()) for item in values),
                              spec_digest=SHA, calendar_ref="calendar-v1")


def sequence():
    rows = [row(index) for index in range(65)]
    rows.extend([
        row(65, "101", high=Decimal("103"), volume=None),
        row(66, "99", high=Decimal("102")),
        row(67, "100", high=Decimal("102")),
        row(68, "101", high=Decimal("103")),
        row(69, "102", high=Decimal("103")),
        row(70, "104", high=Decimal("105")),
    ])
    return rows


def test_prior_windows_exclude_t_and_same_valued_targets_remain_distinct():
    values = sequence()
    result = build(values[:66])
    latest = [item for item in result.measurements if item.market_date == values[65].market_date]
    high = [item for item in latest if item.anchor_level == Decimal("100")]
    assert len(high) == 2 and {item.scale for item in high} == {20, 60}
    assert len({item.anchor_key for item in high}) == 2
    assert len({item.identity.observation_key for item in high}) == 2
    assert {item.value for item in high} == {Decimal("0.01")}
    assert all(anchor.window_end < values[65].market_date for anchor in result.anchors)


def test_loss_equality_and_reclaim_preserve_the_same_reference():
    result = build(sequence())
    events = [item for item in result.interactions if item.scale == 20]
    assert [event.kind for event in events[:3]] == [
        "close-cross-above", "close-cross-below", "close-reclaim",
    ]
    assert len({item.anchor_revision for item in events[:3]}) == 1
    assert events[2].previous_loss_ref == events[1].revision_id
    assert [item.observation for item in result.episodes if item.scale == 20][:4] == [
        "started", "lost", "below", "reclaimed",
    ]
    assert events[3].anchor_key != events[0].anchor_key


def test_reading_extended_history_never_rewrites_earlier_facts_or_process_status():
    values = sequence()
    before = build(values[:67])
    after = build(values)
    cutoff = values[66].market_date
    assert [asdict(item) for item in before.episodes] == [
        asdict(item) for item in after.episodes if item.market_date <= cutoff
    ]
    assert [item.to_dict() for item in before.measurements] == [
        item.to_dict() for item in after.measurements if item.market_date <= cutoff
    ]
    assert before.episodes[-1].phase == "below"


def test_gap_censors_without_claiming_price_loss_and_restarts_warmup():
    values = sequence()[:66]
    values.append(row(66, quality_reason="missing_bar", close=None))
    values.append(row(67))
    result = build(values)
    assert [item.observation for item in result.episodes][-2:] == ["censored", "censored"]
    assert {item.kind for item in result.interactions} == {"close-cross-above"}
    last = [item for item in result.availability if item["market_date"] == values[-1].market_date]
    assert all(not item["available"] for item in last)


def test_action_coordinate_change_censors_even_when_prices_look_like_crash():
    values = sequence()[:66]
    values.append(row(66, "50", high=Decimal("52"), low=Decimal("49"),
                      coordinate_epoch="split-v2", reset_reason="corporate_action"))
    result = build(values)
    assert {item.phase for item in result.episodes[-2:]} == {"censored"}
    assert all(item.kind != "close-cross-below" for item in result.interactions)


def test_missing_session_index_cannot_be_hidden_by_omitting_a_row():
    values = sequence()[:66]
    values.append(row(68, "99"))
    result = build(values)
    assert {item.censor_reason for item in result.episodes[-2:]} == {"unobserved_calendar_gap"}


def test_stationary_price_cannot_reclaim_a_moving_rolling_level():
    values = sequence()[:67]  # Old frozen 100 has been lost at 99.
    values.extend(row(index, "98", high=Decimal("99"), low=Decimal("90"))
                  for index in range(67, 100))
    result = build(values)
    assert not any(item.kind == "close-reclaim" for item in result.interactions)
    assert all(item.frozen_level == Decimal("100") for item in result.episodes)


def test_source_correction_changes_revision_not_logical_target_slot():
    values = sequence()[:66]
    before = build(values)
    values[60] = replace(values[60], source_revision="corrected-source")
    after = build(values)
    first = [item for item in before.anchors if item.window_end == values[64].market_date]
    second = [item for item in after.anchors if item.window_end == values[64].market_date]
    assert {item.key for item in first} == {item.key for item in second}
    assert {item.revision_id for item in first} != {item.revision_id for item in second}


def test_business_objects_contain_no_use_or_run_metadata():
    result = build(sequence())
    for collection in (result.anchors, result.measurements, result.interactions, result.episodes):
        for item in collection:
            assert {"run_id", "consumed_at", "created_at", "used_at"}.isdisjoint(item.to_dict())
    assert build(sequence()).digest == result.digest
