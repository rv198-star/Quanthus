from __future__ import annotations

import pytest

from quanthus.stage_learning import range_envelope_prototype as api

ORDERLY_HIGH_CHURN = [
    10.00, 10.10, 9.90, 10.08, 9.92, 10.06, 9.94, 10.04, 9.96, 10.02,
    9.98, 10.01, 9.99, 10.00, 9.98, 10.01, 9.99, 10.00, 9.99, 10.00, 10.00,
]
ONE_LEG = [
    10.00, 10.05, 9.95, 10.02, 9.98, 10.04, 9.96, 10.03, 9.97, 10.01,
    12.00, 12.00, 12.00, 12.00, 12.00, 12.00, 12.00, 12.00, 12.00, 12.00, 12.00,
]
FAILED_RESOLUTION = [
    10.00, 10.10, 9.90, 10.05, 9.95, 10.08, 9.92, 10.04, 9.96, 10.02,
    10.40, 10.00, 9.60, 10.00, 10.50, 10.00, 9.50, 10.00, 10.45, 10.00, 9.55,
]
STABLE_NARROW = [
    9.95, 10.05, 10.00, 10.02, 9.98, 9.95, 10.05, 10.00, 10.02, 9.98,
    9.95, 10.05, 10.00, 10.02, 9.98, 9.95, 10.05, 10.00, 10.02, 9.98, 10.00,
]


def descriptors(path):
    return api.assess_close_envelope(path).to_dict()["descriptors"]


def test_orderly_high_churn_is_not_mistaken_for_failed_envelope_resolution() -> None:
    result = api.assess_close_envelope(ORDERLY_HIGH_CHURN).to_dict()
    assert result["status"] == "AVAILABLE"
    assert result["descriptors"]["escape_count"] == 0
    assert result["claims"] == {
        "compact_disorder_truth": None,
        "orderly_consolidation_truth": None,
        "relocation_truth": None,
        "numeric_threshold_selected": False,
        "registered_fact": False,
    }


def test_one_leg_repricing_then_hold_is_persistent_one_side_not_failed_resolution() -> None:
    result = api.assess_close_envelope(ONE_LEG).to_dict()
    observed = result["descriptors"]
    assert observed["escape_count"] == 1
    assert observed["resolved_escape_count"] == 0
    assert observed["unresolved_escape_count"] == 1
    assert observed["unresolved_escape_sides"] == ["UPPER"]
    assert observed["single_unresolved_side_only"] is True


def test_compact_failed_resolution_expresses_alternating_resolved_escape_trace() -> None:
    result = api.assess_close_envelope(FAILED_RESOLUTION).to_dict()
    observed = result["descriptors"]
    assert observed["escape_count"] == 4
    assert observed["resolved_escape_count"] == 4
    assert observed["resolved_escape_side_sequence"] == ["UPPER", "LOWER", "UPPER", "LOWER"]
    assert observed["resolved_escape_side_alternation_count"] == 3
    assert observed["resolved_escape_both_sides"] is True
    assert [event["reentry_lag_sessions"] for event in result["events"]] == [1, 1, 1, 1]


def test_stable_narrow_noise_has_no_escape_trace() -> None:
    observed = descriptors(STABLE_NARROW)
    assert observed["escape_count"] == 0
    assert observed["resolved_escape_both_sides"] is False


def test_missing_close_and_insufficient_history_fail_closed() -> None:
    unavailable = list(ORDERLY_HIGH_CHURN)
    unavailable[10] = None
    assert api.assess_close_envelope(unavailable).to_dict()["status"] == "UNAVAILABLE"
    insufficient = api.assess_close_envelope(ORDERLY_HIGH_CHURN[:20]).to_dict()
    assert insufficient["status"] == "INSUFFICIENT_HISTORY"
    assert insufficient["descriptors"]["escape_count"] == 0


def test_profile_is_exact_and_values_must_be_positive() -> None:
    with pytest.raises(api.RangeEnvelopePrototypeError, match="exact_21"):
        api.assess_close_envelope([*ORDERLY_HIGH_CHURN, 10.0])
    invalid = list(ORDERLY_HIGH_CHURN)
    invalid[-1] = 0
    with pytest.raises(api.RangeEnvelopePrototypeError, match="close_invalid"):
        api.assess_close_envelope(invalid)


def test_equality_with_prior_boundary_is_inside_not_escape() -> None:
    path = [10, 11, 9, 10, 10, 10, 10, 10, 10, 10, 11, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10]
    result = api.assess_close_envelope(path).to_dict()
    assert result["events"] == []
