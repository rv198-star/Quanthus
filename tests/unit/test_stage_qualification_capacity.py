"""Allocation diagnosis must expose competition without using semantic results."""
from dataclasses import replace
from datetime import date

import pytest

from quanthus.stage_learning.qualification import (
    StageQualificationError,
    qualification_capacity_preflight,
    select_qualification_contexts,
)
from tests.unit.test_stage_qualification import _qualification_population


def sampling():
    return {
        "sampling_seed": "capacity-regression",
        "total_contexts": 5,
        "strata": {"started_activity": 1, "early_held": 1,
                   "near_anchor_loss_negative": 1, "reclaimed_ambiguity": 1,
                   "mature_held": 1},
        "started_activity_buckets": {"missing_or_lt_1": 1, "1_to_1_25": 0,
                                     "1_25_to_1_5": 0, "gte_1_5": 0},
        "early_held_per_hold_count": {"1": 1, "2": 0, "3": 0},
    }


def overlap_population():
    rows, _ = _qualification_population()
    start = rows[0]
    return [
        start,
        replace(start, observation="held", hold_count=1, market_date=date(2022, 6, 2)),
        replace(start, observation="lost", phase="below", market_date=date(2022, 6, 3)),
        replace(start, observation="reclaimed", phase="reclaimed",
                market_date=date(2022, 6, 6)),
        replace(start, observation="held", hold_count=40, market_date=date(2022, 8, 1)),
    ]


def preflight(rows, **kwargs):
    return qualification_capacity_preflight(
        rows, (), report_start=date(2022, 1, 1), report_end=date(2022, 12, 31),
        sampling=sampling(), minimum_unique_episodes=1, **kwargs,
    )


def test_same_episode_cannot_supply_five_strata():
    result = preflight(overlap_population())
    assert result["eligible_unique_episodes"] == 1
    assert result["selected_unique_episodes"] == 1
    assert result["allocation"][0]["selected"] == 1
    for group in result["allocation"][1:]:
        assert group["eligible_unique_episodes"] == 1
        assert group["occupied_by_earlier_strata"] == 1
        assert group["available_after_prior_strata"] == 0
        assert group["selected"] == 0
    assert result["semantic_sufficiency_proven"] is False


def test_prospective_rare_first_order_reserves_episode_without_changing_legacy():
    rows = overlap_population()
    order = ["mature_held", "reclaimed_ambiguity", "started_activity",
             "early_held", "near_anchor_loss_negative"]
    rare = preflight(rows, selection_order=order)
    assert rare["allocation"][0]["stratum"] == "mature_held"
    assert rare["allocation"][0]["selected"] == 1
    # This API diagnoses a proposed order; it does not mutate a frozen sampler.
    original = select_qualification_contexts(
        rows, (), report_start=date(2022, 1, 1), report_end=date(2022, 12, 31),
        sampling=sampling(), target_caps=True,
    )
    assert original[0].sampling_stratum == "qualification_started_activity"


def test_exposed_episode_is_unavailable_at_every_date_and_scale():
    rows = overlap_population()
    rows.append(replace(rows[-1], scale=60))
    result = preflight(rows, excluded_episode_ids=[rows[0].episode_id])
    assert result["excluded_unique_episodes"] == 1
    assert result["unconsumed_unique_episodes"] == 0
    assert result["selected_unique_episodes"] == 0
    assert result["capacity_passed"] is False
    assert "UNCONSUMED_EPISODE_CAPACITY_BELOW_MINIMUM" in result["reason_codes"]


def test_capacity_matches_legacy_target_cap_allocation():
    rows, activity = _qualification_population()
    config = sampling()
    result = qualification_capacity_preflight(
        rows, activity, report_start=date(2022, 1, 1), report_end=date(2022, 12, 31),
        sampling=config, minimum_unique_episodes=5,
    )
    contexts = select_qualification_contexts(
        rows, activity, report_start=date(2022, 1, 1), report_end=date(2022, 12, 31),
        sampling=config, target_caps=True,
    )
    assert result["selected_unique_episodes"] == len(contexts) == 5
    assert result["capacity_passed"]
    assert result["semantic_sufficiency_proven"] is False
    assert [r["selected"] for r in result["allocation"]] == [1, 1, 1, 1, 1]


@pytest.mark.parametrize("order", [[], ["started_activity"] * 5,
                                  ["started_activity", "unknown"], "started_activity"])
def test_invalid_or_incomplete_order_fails(order):
    with pytest.raises(StageQualificationError, match="allocation order"):
        preflight(overlap_population(), selection_order=order)


def test_empty_population_reports_shortfall_not_pass():
    result = preflight([])
    assert not result["capacity_passed"]
    assert result["eligible_unique_episodes"] == 0
    assert len(result["allocation"]) == 5


@pytest.mark.parametrize("minimum", [0, -1, True, 1.5])
def test_invalid_minimum_rejected(minimum):
    with pytest.raises(StageQualificationError, match="capacity minimum"):
        qualification_capacity_preflight(
            [], (), report_start=date(2022, 1, 1), report_end=date(2022, 12, 31),
            sampling=sampling(), minimum_unique_episodes=minimum,
        )


def test_history_minimum_counts_prior_sessions_and_fails_closed_when_missing():
    rows = overlap_population()
    key = (rows[0].security_code, rows[0].market_date)
    kwargs = {"minimum_prior_sessions_by_scale": {rows[0].scale: 20}}
    # The supplied counts exclude T; nineteen prior bars plus T is too short.
    result = preflight(rows[:1], prior_history_sessions={key: 19}, **kwargs)
    assert not result["capacity_passed"]
    assert result["history_ineligible_observations"] == 1
    assert result["eligible_unique_episodes"] == 0
    result = preflight(rows[:1], prior_history_sessions={key: 20}, **kwargs)
    assert result["capacity_passed"]
    result = preflight(rows[:1], prior_history_sessions={}, **kwargs)
    assert not result["capacity_passed"]
    assert result["history_missing_observations"] == 1


def test_history_requirement_is_scale_specific_and_does_not_drop_60_scale():
    row = overlap_population()[0]
    rows = [replace(row, scale=20), replace(row, scale=60, episode_id="scale60")]
    result = preflight(
        rows, prior_history_sessions={(row.security_code, row.market_date): 20},
        minimum_prior_sessions_by_scale={20: 20, 60: 60},
    )
    assert result["eligible_unique_episodes"] == 1
    assert result["history_ineligible_observations"] == 1
    assert result["required_prior_sessions_by_scale"] == {"20": 20, "60": 60}


@pytest.mark.parametrize("kwargs", [
    {"prior_history_sessions": {}},
    {"minimum_prior_sessions_by_scale": {20: 20}},
    {"prior_history_sessions": {}, "minimum_prior_sessions_by_scale": {}},
    {"prior_history_sessions": {}, "minimum_prior_sessions_by_scale": {20: True}},
])
def test_partial_or_invalid_history_policy_is_rejected(kwargs):
    with pytest.raises(StageQualificationError, match="history"):
        preflight(overlap_population(), **kwargs)


def test_unknown_scale_cannot_silently_bypass_history_policy():
    row = overlap_population()[0]
    with pytest.raises(StageQualificationError, match="history"):
        preflight([replace(row, scale=60)], prior_history_sessions={},
                  minimum_prior_sessions_by_scale={20: 20})
