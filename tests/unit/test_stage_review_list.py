"""Blinding, full-universe accounting and security/day review costs."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from quanthus.stage_learning.review_list import (
    ReviewListError,
    blind_packet,
    compare_review_use,
    review_lists,
    review_use,
    validate_judgements,
)


def packet():
    start = date(2021, 4, 1)
    return {"market_date": (start + timedelta(days=60)).isoformat(), "anchor_scale": 60,
            "engineering_candidate_state": "SECRET", "sampling_stratum": "SECRET",
            "frozen_reference": {"frozen_high": "SECRET"},
            "t_only_process_facts": {"phase": "SECRET", "hold_count": 3},
            "history": [{"market_date": (start + timedelta(days=i)).isoformat(),
                         "admitted": True, "reason": "available", "reset_reason": None,
                         "coordinate_epoch": "same", "high": "11", "low": "9",
                         "close": "10", "volume": "100"} for i in range(61)]}


def opportunity(i, security=None, scale=20, day="2021-06-01"):
    return {"context_id": f"c{i}", "security_id": security or f"s{i}", "scale": scale,
            "market_date": day, "episode_id": f"e{i}"}


def test_blind_whitelist_removes_candidate_state_and_identity():
    value = blind_packet(packet(), "P01")
    assert "SECRET" not in str(value)
    assert value["scale_history_sufficient"] is True
    assert value["observations"][-1]["close"] == "1.0000000000"
    assert value["observations"][-1]["offset"] == 0
    assert value["contiguous_prior_sessions"] == 60


@pytest.mark.parametrize("mutation", ["future", "duplicates", "scope", "nonfinite"])
def test_blind_scope_and_numeric_safety(mutation):
    value = packet()
    if mutation == "future":
        value["history"][-1]["market_date"] = "2021-12-31"
    elif mutation == "duplicates":
        value["history"][1]["market_date"] = value["history"][0]["market_date"]
    elif mutation == "scope":
        value["market_date"] = "2025-06-01"
    else:
        value["history"][0]["volume"] = "NaN"
    with pytest.raises(ReviewListError):
        blind_packet(value, "P01")


@pytest.mark.parametrize("break_type", ["unadmitted", "reset", "coordinate"])
def test_short_or_broken_history_cannot_claim_full_scale(break_type):
    value = packet()
    if break_type == "unadmitted":
        value["history"][30]["admitted"] = False
    elif break_type == "reset":
        value["history"][30]["reset_reason"] = "coordinate_change"
    else:
        for row in value["history"][30:]:
            row["coordinate_epoch"] = "different"
    result = blind_packet(value, "P01")
    assert result["contiguous_prior_sessions"] < 60
    assert result["scale_history_sufficient"] is False


def test_exact_citation_binding_and_unknown_is_not_a_negative():
    value = blind_packet(packet(), "P01")
    review = {"case_id": "P01", "packet_sha256": value["packet_sha256"],
              "kind": "unjudgeable", "stage_codes": [], "evidence": [],
              "reason": "missing defensible region", "counterevidence": "history exists",
              "review_worthiness": "unknown"}
    validate_judgements([value], [review])
    review.update(kind="definite", stage_codes=[3], evidence=[
        {"offset": 0, "field": "close", "value": "1.0000000000"},
        {"offset": -1, "field": "close", "value": "1.0000000000"}])
    validate_judgements([value], [review])
    bad = deepcopy(review)
    bad["evidence"][0]["value"] = "99"
    with pytest.raises(ReviewListError, match="UNBOUND_CITATION"):
        validate_judgements([value], [bad])
    bad = deepcopy(review)
    bad["packet_sha256"] = "different"
    with pytest.raises(ReviewListError, match="REVIEW_INPUT_IDENTITY"):
        validate_judgements([value], [bad])
    bad_packet = deepcopy(value)
    bad_packet["observations"][0]["close"] = "5"
    with pytest.raises(ReviewListError, match="PACKET_CONTENT_IDENTITY"):
        validate_judgements([bad_packet], [review])


def test_same_security_cross_scale_and_episode_spend_one_slot():
    rows = [opportunity(1, "same"), opportunity(2, "same", 60), opportunity(3)]
    result = review_lists(rows, {"c1": 3, "c2": 4, "c3": None},
                          frozen_context_ids=["c1", "c2", "c3"], seed="fixed")
    assert result["total_contexts"] == 3
    assert result["total_units"] == 2
    assert result["actual_review_cost_units"] == 1
    day = result["days"][0]
    assert day["unused_slots"] == 4 and day["unknown_contexts"] == 1
    assert len(day["selected"][0]["assertions"]) == 2
    assert result["consumer_test_authorized"] is False


def test_full_universe_required_and_duplicate_context_fails():
    row = opportunity(1)
    with pytest.raises(ReviewListError, match="UNIVERSE"):
        review_lists([row], {"c1": 3}, frozen_context_ids=["c1", "c2"], seed="x")
    with pytest.raises(ReviewListError, match="UNIVERSE"):
        review_lists([row, row], {"c1": 3}, frozen_context_ids=["c1"], seed="x")
    with pytest.raises(ReviewListError, match="UNIVERSE"):
        review_lists([row], {}, frozen_context_ids=["c1"], seed="x")


@pytest.mark.parametrize("assertion", [True, 3.0, "3", 5])
def test_assertion_types_cannot_alias_stage_codes(assertion):
    with pytest.raises(ReviewListError, match="INVALID_ASSERTION"):
        review_lists([opportunity(1)], {"c1": assertion},
                     frozen_context_ids=["c1"], seed="x")


def test_protected_year_cannot_enter_this_research_list():
    with pytest.raises(ReviewListError, match="FROZEN_RESEARCH_YEAR"):
        review_lists([opportunity(1, day="2025-06-01")], {"c1": 3},
                     frozen_context_ids=["c1"], seed="x")


def test_top5_binding_and_hash_order_are_deterministic():
    rows = [opportunity(i) for i in range(7)]
    predictions = {r["context_id"]: 3 for r in rows}
    ids = list(predictions)
    result = review_lists(rows, predictions, frozen_context_ids=ids, seed="fixed")
    assert result["days"][0]["selection_budget_binding"] is True
    assert result["actual_review_cost_units"] == 5
    assert result == review_lists(rows[::-1], predictions, frozen_context_ids=ids, seed="fixed")


def test_wrong_three_four_and_unknown_misses_are_counted():
    rows = [opportunity(1), opportunity(2), opportunity(3), opportunity(4)]
    predictions = {"c1": 3, "c2": None, "c3": 4, "c4": 4}
    references = {"c1": {"kind": "definite", "stage_codes": [4]},
                  "c2": {"kind": "definite", "stage_codes": [3]},
                  "c3": {"kind": "ambiguous_set", "stage_codes": [3, 4]},
                  "c4": {"kind": "definite", "stage_codes": []}}
    result = review_use(rows, predictions, references,
                        frozen_context_ids=list(predictions), seed="fixed")
    assert result["wrong_stage_assertions"] == 1
    assert result["negative_alert_assertions"] == 1
    assert result["false_alert_units"] == 2
    assert result["ambiguous_unique_assertions"] == 1
    assert result["missed_positive_units"] == 2
    assert result["days"][0]["unknown_contexts"] == 1


def test_a_unit_can_contain_both_match_and_error_without_erasing_error():
    rows = [opportunity(1, "same"), opportunity(2, "same", 60)]
    refs = {"c1": {"kind": "definite", "stage_codes": [3]},
            "c2": {"kind": "definite", "stage_codes": [3]}}
    result = review_use(rows, {"c1": 3, "c2": 4}, refs,
                        frozen_context_ids=list(refs), seed="x")
    assert result["selected_matched_positive_units"] == 1
    assert result["false_alert_units"] == 1
    assert result["actual_review_cost_units"] == 1


def test_state_correctness_does_not_manufacture_review_worthiness():
    rows = [opportunity(1), opportunity(2)]
    refs = {"c1": {"kind": "definite", "stage_codes": [3], "review_worthiness": "no"},
            "c2": {"kind": "definite", "stage_codes": [], "review_worthiness": "yes"}}
    result = review_use(rows, {"c1": 3, "c2": 3}, refs,
                        frozen_context_ids=list(refs), seed="x")
    assert result["selected_matched_positive_units"] == 1
    assert result["false_alert_units"] == 1
    assert result["selected_assertion_review_worthiness"] == {
        "yes": 1, "no": 1, "unknown": 0, "not_supplied": 0,
    }


def test_equal_maximum_but_unequal_actual_cost_cannot_claim_same_cost_benefit():
    rows = [opportunity(1), opportunity(2), opportunity(3, "s1", 60),
            opportunity(4, "s2", 60)]
    refs = {"c1": {"kind": "definite", "stage_codes": [3]},
            "c2": {"kind": "definite", "stage_codes": []},
            "c3": {"kind": "unjudgeable", "stage_codes": []},
            "c4": {"kind": "unjudgeable", "stage_codes": []}}
    arms = {"frozen_R1": {"c1": 3, "c2": None, "c3": None, "c4": None},
            "frozen_R0": {"c1": 3, "c2": 3, "c3": None, "c4": None}}
    result = compare_review_use(rows, arms, refs,
                               frozen_context_ids=list(refs), seed="x",
                               frozen_opportunity_grid=[(r["security_id"], r["market_date"],
                                                         r["scale"]) for r in rows])
    assert result["same_actual_review_cost"] is False
    assert result["bounded_use_improvement"] is False


def test_equal_cost_real_improvement_is_bounded_and_tie_is_not_benefit():
    rows = [opportunity(i, f"s{i % 3}", 20 if i < 3 else 60) for i in range(6)]
    refs = {f"c{i}": {"kind": "definite", "stage_codes": [3] if i == 0 else []}
            for i in range(6)}
    arms = {"frozen_R1": {f"c{i}": 3 if i == 0 else None for i in range(6)},
            "frozen_R0": {f"c{i}": 3 if i == 1 else None for i in range(6)}}
    grid = [(r["security_id"], r["market_date"], r["scale"]) for r in rows]
    result = compare_review_use(rows, arms, refs, frozen_context_ids=list(refs), seed="x",
                               frozen_opportunity_grid=grid)
    assert result["bounded_use_improvement"] is True
    assert result["reference_quality_or_consumer_admission_proven"] is False
    arms["frozen_R0"] = arms["frozen_R1"]
    tie = compare_review_use(rows, arms, refs, frozen_context_ids=list(refs), seed="x",
                             frozen_opportunity_grid=grid)
    assert tie["bounded_use_improvement"] is False


def test_quality_sample_cannot_substitute_for_full_daily_grid():
    rows = [opportunity(1)]
    refs = {"c1": {"kind": "definite", "stage_codes": [3]}}
    arms = {"frozen_R1": {"c1": 3}, "frozen_R0": {"c1": 3}}
    with pytest.raises(ReviewListError, match="DAILY_GRID_NOT_COMPLETE"):
        compare_review_use(rows, arms, refs, frozen_context_ids=["c1"], seed="x",
                           frozen_opportunity_grid=[("s1", "2021-06-01", 20),
                                                    ("s1", "2021-06-01", 60)])
