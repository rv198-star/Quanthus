"""Exact held denominators must precede any structural-expert comparison."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, time
from hashlib import sha256
from pathlib import Path

import pytest

from quanthus.expert_strategy.held_structure_preflight import (
    PreflightError,
    compare_identity_paths,
    read_pinned_json,
    recover_population,
    screen_candidate,
)


def archive(code="AAA", *, quantity=100):
    entry = {
        "market_date": "2021-01-04",
        "surface": "entry",
        "decision_key": "entry:event-1",
        "unit_id": "unit-1",
        "filled": True,
        "final_action": "trial_entry",
        "reason_codes": ["entry"],
    }
    held = {
        "market_date": "2021-01-05",
        "surface": "held",
        "decision_key": "held:unit-1",
        "unit_id": "unit-1",
        "filled": False,
        "final_action": "no_action",
        "reason_codes": ["base_position_management_only"],
    }
    snapshot = {
        "event_id": "snapshot-1",
        "event_type": "daily_snapshot",
        "event_time": "2021-01-05T15:00:00+08:00",
        "payload": {
            "unit_id": "unit-1",
            "session_date": "2021-01-05",
            "security_code": code,
            "position_quantity": quantity,
        },
    }
    return [entry, held], [snapshot], {"entry:event-1": f"{code}@2021-01-04"}


def recover(arm="B", *, source=None):
    return recover_population(
        arm,
        *(source or archive()),
        first_date=date(2021, 1, 1),
        last_date=date(2021, 12, 31),
        decision_time=time(15),
    )


def test_complete_population_keeps_the_held_denominator_and_exact_entry_episode():
    population = recover()
    assert population.safe_summary()["held_decisions"] == 1
    assert population.safe_summary()["entry_decisions"] == 1
    observation = population.identities[0]
    assert observation.security_code == "AAA"
    assert observation.entry_key == "entry:event-1"
    assert observation.entry_date < observation.market_date
    assert population.safe_summary()["full_consumer_input_state_recovered"] is False


def test_same_unit_date_with_other_security_is_not_a_pair():
    left, right = recover(), recover("BH", source=archive("BBB"))
    result = compare_identity_paths(left, right)
    assert result["common_unit_dates"] == 1
    assert result["different_security_at_common_unit_date"] == 1
    assert result["same_entry_episode"] == 0
    assert result["qualified_full_state_pairs"] == 0


def test_same_security_date_with_different_entry_is_not_the_same_episode():
    left = recover()
    other = replace(
        left, arm="BH", identities=(replace(left.identities[0], entry_key="entry:other"),)
    )
    result = compare_identity_paths(left, other)
    assert result["same_security_different_entry_episode"] == 1
    assert result["same_entry_episode"] == 0


def test_even_same_episode_and_quantity_do_not_establish_equal_risk_inputs():
    left = recover()
    equal = compare_identity_paths(left, replace(left, arm="BH"))
    unequal = compare_identity_paths(left, recover("BH", source=archive(quantity=200)))
    assert equal["same_entry_episode"] == 1
    assert equal["qualified_full_state_pairs"] == 0
    assert unequal["same_episode_different_quantity"] == 1
    assert unequal["qualified_full_state_pairs"] == 0


def test_path_disagreement_keeps_each_arm_denominator():
    left = recover()
    extra = replace(left.identities[0], unit_id="unit-2")
    right = replace(left, arm="BH", identities=(*left.identities, extra))
    result = compare_identity_paths(left, right)
    assert result["left_unmatched_unit_dates"] == 0
    assert result["right_unmatched_unit_dates"] == 1
    assert left.safe_summary()["held_decisions"] == 1
    assert right.safe_summary()["held_decisions"] == 2


@pytest.mark.parametrize(
    "failure",
    [
        "duplicate_decision",
        "missing_snapshot",
        "duplicate_snapshot",
        "flat_snapshot",
        "wrong_security",
        "missing_entry",
        "same_day_entry",
        "wrong_entry_coordinate",
        "missing_coordinate",
        "future_snapshot",
        "unknown_time",
        "out_of_window",
    ],
)
def test_incomplete_or_ambiguous_archive_fails_whole_denominator(failure):
    decisions, events, coordinates = deepcopy(archive())
    expected = {
        "duplicate_decision": "duplicate_decision_key",
        "missing_snapshot": "held_snapshot_missing",
        "duplicate_snapshot": "ambiguous_same_day_snapshot",
        "flat_snapshot": "held_snapshot_not_positive",
        "wrong_security": "held_entry_security_mismatch",
        "missing_entry": "held_prior_entry_missing",
        "same_day_entry": "held_prior_entry_missing",
        "wrong_entry_coordinate": "entry_coordinate_mismatch",
        "missing_coordinate": "entry_coordinate_missing",
        "future_snapshot": "snapshot_after_decision_boundary",
        "unknown_time": "snapshot_time_unknown",
        "out_of_window": "decision_outside_frozen_window",
    }[failure]
    if failure == "duplicate_decision":
        decisions.append(deepcopy(decisions[-1]))
    elif failure == "missing_snapshot":
        events.clear()
    elif failure == "duplicate_snapshot":
        events.append(deepcopy(events[0]))
    elif failure == "flat_snapshot":
        events[0]["payload"]["position_quantity"] = 0
    elif failure == "wrong_security":
        events[0]["payload"]["security_code"] = "BBB"
    elif failure == "missing_entry":
        decisions[0]["filled"] = False
    elif failure == "same_day_entry":
        decisions[0]["market_date"] = "2021-01-05"
        coordinates["entry:event-1"] = "AAA@2021-01-05"
    elif failure == "wrong_entry_coordinate":
        coordinates["entry:event-1"] = "AAA@2021-01-03"
    elif failure == "missing_coordinate":
        coordinates.clear()
    elif failure == "future_snapshot":
        events[0]["event_time"] = "2021-01-05T15:00:01+08:00"
    elif failure == "unknown_time":
        events[0]["event_time"] = "2021-01-05T15:00:00"
    elif failure == "out_of_window":
        decisions[0]["market_date"] = "2025-01-01"
    with pytest.raises(PreflightError, match=expected):
        recover(source=(decisions, events, coordinates))


def test_shanghai_boundary_accepts_the_same_instant_expressed_in_utc():
    decisions, events, coordinates = archive()
    events[0]["event_time"] = "2021-01-05T07:00:00+00:00"
    assert len(recover(source=(decisions, events, coordinates)).identities) == 1


def test_snapshot_on_next_shanghai_date_cannot_be_used_for_previous_decision():
    decisions, events, coordinates = archive()
    events[0]["event_time"] = "2021-01-05T16:00:00+00:00"
    with pytest.raises(PreflightError, match="snapshot_after_decision_boundary"):
        recover(source=(decisions, events, coordinates))


def test_no_overlap_is_a_scope_stop_rather_than_zero_effect_or_negative_label():
    panel = [{"security_code": "BBB", "market_date": "2021-01-05"}]
    result = screen_candidate(recover(), panel)
    assert result["coarse_code_date_overlap"] == 0
    assert result["reason"] == "NO_COARSE_OVERLAP"
    assert "effect" not in result
    assert result["admission"] == "NOT_GRANTED"


def test_ticker_date_overlap_and_all_support_fields_still_do_not_admit_a_join():
    panel = [
        {
            "security_code": "AAA",
            "market_date": "2021-01-05",
            "unreclaimed_age_sessions": 5,
            "signed_distance_to_support": "-0.02",
            "post_break_rebound": "0.05",
        }
    ]
    result = screen_candidate(recover(), panel)
    assert result["coarse_code_date_overlap"] == 1
    assert result["missing_support_fields"] == []
    assert result["qualified_decision_fact_pairs"] == 0
    assert result["reason"] == "EXACT_SOURCE_PURPOSE_BRIDGE_REQUIRED"


def test_breakout_and_volume_facts_never_alias_support_evolution():
    result = screen_candidate(
        recover(),
        [
            {
                "security_code": "AAA",
                "market_date": "2021-01-05",
                "resistance_margin20": "0.02",
                "volume_ratio20": "1.1",
            }
        ],
    )
    assert len(result["missing_support_fields"]) == 3


def test_partial_support_columns_remain_unavailable_for_the_full_population():
    rows = [
        {"security_code": "AAA", "market_date": "2021-01-05", "unreclaimed_age_sessions": 5},
        {"security_code": "AAA", "market_date": "2021-01-06"},
    ]
    assert "unreclaimed_age_sessions" in screen_candidate(recover(), rows)["missing_support_fields"]


def test_null_support_value_does_not_establish_available_information():
    rows = [{"security_code": "AAA", "market_date": "2021-01-05", "unreclaimed_age_sessions": None}]
    assert "unreclaimed_age_sessions" in screen_candidate(recover(), rows)["missing_support_fields"]


def test_duplicate_candidate_date_does_not_silently_pick_a_revision():
    row = {"security_code": "AAA", "market_date": "2021-01-05"}
    with pytest.raises(PreflightError, match="ambiguous_candidate_code_date"):
        screen_candidate(recover(), [row, row])


def test_integrity_pin_and_read_limit_precede_json_interpretation(tmp_path: Path):
    path = tmp_path / "input.json"
    raw = b'{"safe": true}'
    path.write_bytes(raw)
    pin = sha256(raw).hexdigest()
    assert read_pinned_json(path, pin, maximum_bytes=len(raw)) == {"safe": True}
    with pytest.raises(PreflightError, match="input_size_exceeded"):
        read_pinned_json(path, pin, maximum_bytes=len(raw) - 1)
    path.write_bytes(b"corrupted, not JSON")
    with pytest.raises(PreflightError, match="input_sha256_mismatch"):
        read_pinned_json(path, pin, maximum_bytes=100)


def test_preflight_has_no_order_or_action_output_and_ignores_economic_fields():
    decisions, events, coordinates = archive()
    events[0]["payload"].update({"mark_price": "not examined", "realized_pnl": "not examined"})
    result = recover(source=(decisions, events, coordinates)).safe_summary()
    assert "mark_price" not in str(result)
    assert "realized_pnl" not in str(result)
    assert result["independent_sample_count"] is None
