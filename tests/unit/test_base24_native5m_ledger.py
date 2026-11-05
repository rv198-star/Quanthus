"""Base 2.4 native-5m ledger keeps the frozen denominator and evidence gaps."""

from datetime import date

import pytest

from quanthus.pipelines.securities_warehouse.base24_native5m_ledger import (
    Native5mLedgerError,
    classify_security_day,
    summarize_classifications,
)


def evidence(**updates):
    value = {
        "stock_code": "000001",
        "business_date": "2020-01-02",
        "source_sha256": "a" * 64,
        "acquired_at": "2026-10-16T08:00:00+00:00",
    }
    value.update(updates)
    return value


def classify(**updates):
    values = {
        "venue": "SZSE",
        "stock_code": "000001",
        "business_date": date(2020, 1, 2),
        "row_count": 48,
        "distinct_buckets": 48,
        "invalid_buckets": 0,
        "invalid_values": 0,
        "status_evidence": (),
        "daily_evidence": (),
    }
    values.update(updates)
    return classify_security_day(**values)


def test_exact_native_grid_is_r2_without_needing_no_data_evidence():
    result = classify()
    assert result["disposition"] == "OBSERVED"
    assert result["rating"] == "R2"
    assert result["reason"] == "native_48_bucket_grid"


def test_partial_or_invalid_observation_stays_in_denominator_as_r3():
    assert classify(row_count=47, distinct_buckets=47)["reason"] == "observed_bucket_gap"
    assert classify(invalid_values=1)["reason"] == "observed_semantic_defect"
    assert classify(row_count=0, distinct_buckets=0)["rating"] == "R3"


def test_empty_day_requires_hashed_clocked_same_day_status_and_daily_corroboration():
    status_only = classify(
        row_count=0,
        distinct_buckets=0,
        status_evidence=(evidence(tradestatus="0"),),
    )
    # Base 2.5: Baostock reported tradestatus=0 on 688065/2023-06-15 while the
    # frozen daily row carried volume 353426 and amount 19589785.99. A status
    # flag without daily corroboration must fail closed.
    assert status_only["disposition"] == "UNRESOLVED"
    assert status_only["rating"] == "R3"
    assert status_only["reason"] == "status_without_daily_corroboration"
    corroborated = classify(
        row_count=0,
        distinct_buckets=0,
        status_evidence=(evidence(tradestatus="0"),),
        daily_evidence=(evidence(volume="0", amount="0.00"),),
    )
    assert corroborated["disposition"] == "JUSTIFIED_NO_DATA"
    assert corroborated["rating"] is None
    assert corroborated["reason"] == "daily_status_not_tradable"
    bad = evidence(tradestatus="0", acquired_at=None)
    with pytest.raises(Native5mLedgerError, match="EVIDENCE_IDENTITY"):
        classify(row_count=0, distinct_buckets=0, status_evidence=(bad,))


def test_same_day_zero_activity_is_justified_but_positive_activity_is_r3():
    zero = classify(
        row_count=0,
        distinct_buckets=0,
        daily_evidence=(evidence(volume="0", amount="0.00"),),
    )
    assert zero["reason"] == "zero_turnover_daily_bar"
    positive = classify(
        row_count=0,
        distinct_buckets=0,
        daily_evidence=(evidence(volume="1", amount="2"),),
    )
    assert positive["reason"] == "missing_despite_daily_activity"
    assert positive["rating"] == "R3"


def test_status_or_daily_revision_conflicts_block_only_the_affected_unit():
    status = classify(
        row_count=0,
        distinct_buckets=0,
        status_evidence=(evidence(tradestatus="0"), evidence(tradestatus="1")),
    )
    assert (status["rating"], status["reason"]) == ("R3", "status_evidence_conflict")
    daily = classify(
        row_count=0,
        distinct_buckets=0,
        daily_evidence=(
            evidence(volume="0", amount="0"),
            evidence(volume="1", amount="1"),
        ),
    )
    assert (daily["rating"], daily["reason"]) == ("R3", "daily_evidence_conflict")


def test_status_conflicts_are_disclosed_without_discarding_complete_bars():
    status = (evidence(tradestatus="0"),)
    observed = classify(status_evidence=status)
    assert observed["rating"] == "R2"
    assert observed["evidence_warning"] == "status_observation_conflict"
    empty = classify(
        row_count=0,
        distinct_buckets=0,
        status_evidence=status,
        daily_evidence=(evidence(volume="1", amount="1"),),
    )
    assert (empty["rating"], empty["reason"]) == ("R3", "status_daily_conflict")


def test_summary_retains_r3_in_denominator_and_rejects_duplicate_keys():
    observed = classify()
    missing = classify(stock_code="000002", row_count=0, distinct_buckets=0)
    summary = summarize_classifications([observed, missing])
    assert summary["required_security_days"] == 2
    assert summary["observed_R2_security_days"] == 1
    assert summary["R3_security_days"] == 1
    assert not summary["default_native5m_product_pass"]
    with pytest.raises(Native5mLedgerError, match="DUPLICATE_REQUIRED_KEY"):
        summarize_classifications([observed, observed])
    with pytest.raises(Native5mLedgerError, match="REQUIRED_KEY_ORDER"):
        summarize_classifications([missing, observed])
