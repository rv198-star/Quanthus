from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pyarrow as pa
import pytest

from quanthus.pipelines.securities_warehouse.base22_august import dividend_date, project_dividend
from quanthus.pipelines.securities_warehouse.base22_qualification import (
    rolling_revision_contract,
    tail_repair_evidence,
)
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError


def event(**changes):
    return {
        "stock_code": "synthetic",
        "ann_id": "event",
        "title": "original",
        "announce_date": "2026-08-01",
        "acquired_at": "2026-08-01T20:00:00+08:00",
        "artifact_sha256": "hash",
        "release_id": "release",
        **changes,
    }


def test_calendar_date_serialization_does_not_truncate_timed_observations():
    assert dividend_date("2026-07-25T00:00:00") == date(2026, 7, 25)
    with pytest.raises(SourceAssessmentError):
        dividend_date("2026-07-25T12:30:00")


def test_equal_instant_conflicting_event_bodies_fail_across_timezone_spelling():
    with pytest.raises(SourceAssessmentError, match="EVENT_EQUAL_CLOCK_CONFLICT"):
        rolling_revision_contract(
            [event(), event(acquired_at="2026-08-01T12:00:00+00:00", title="revised")]
        )


@pytest.mark.parametrize(
    "change", ["same-body", "revision", "equal-clock-conflict", "unknown-clock"]
)
def test_rolling_revisions_preserve_true_acquisitions(change):
    first = event()
    second = event(acquired_at="2026-08-03T20:00:00+08:00", release_id="later")
    if change in {"revision", "equal-clock-conflict"}:
        second["title"] = "revised"
    if change == "equal-clock-conflict":
        second["acquired_at"] = "2026-08-01T20:00:00+08:00"
    if change == "unknown-clock":
        second["acquired_at"] = None
    if change in {"unknown-clock", "equal-clock-conflict"}:
        with pytest.raises(SourceAssessmentError):
            rolling_revision_contract([first, second])
    else:
        r = rolling_revision_contract([first, second])
        assert r["key_count"] == 1
        assert r["body_version_count"] == (2 if change == "revision" else 1)
        assert sum(len(v["observations"]) for v in r["keys"][0]["versions"]) == 2
        assert "original" not in str(r) and "revised" not in str(r)


@pytest.mark.parametrize("fault", [None, "unknown-clock", "september", "scale"])
def test_dividend_separate_handler_preserves_decimal_acquisition_and_date_precision(fault):
    row = {
        "stock_code": "synthetic",
        "announce_date": "2026-08-01",
        "ex_dividend_date": "2026-08-03",
        "dividend_per_share": "1.0000000001",
        "bonus_shares": "0",
        "transfer_shares": None,
        "progress": "done",
        "provider_acquired_at": "2026-08-01T03:00:00+00:00",
        "acquired_at": "2026-08-01T05:00:00+00:00",
    }
    if fault == "unknown-clock":
        row["provider_acquired_at"] = None
    if fault == "september":
        row["ex_dividend_date"] = "2026-09-01"
    if fault == "scale":
        row["dividend_per_share"] = "1.00000000001"
    kwargs = {
        "source_sha256": "hash",
        "source_revision": "new",
        "company_id": "company",
        "security_id": "security",
        "parent_knowledge_from": datetime(2026, 7, 31, tzinfo=timezone.utc),
        "vocabulary": {
            "action_types": {"cash_div": "CASH_DIVIDEND"},
            "action_statuses": {"done": "IMPLEMENTED"},
            "term_types": {
                "cash_per_share": "CASH",
                "bonus_shares": "BONUS",
                "transfer_shares": "TRANSFER",
            },
            "share_quantity_unit": "per_ten_shares",
        },
    }
    before = dict(row)
    if fault in {"unknown-clock", "scale"}:
        with pytest.raises((SourceAssessmentError, ValueError, RuntimeError)):
            project_dividend(row, **kwargs)
    else:
        out, metrics = project_dividend(row, **kwargs)
        if fault == "september":
            assert not out
        else:
            action = out["dwd_corporate_action"][0]
            assert action["announced_at"] is None
            assert action["knowledge_from"] == datetime(2026, 8, 1, 5, tzinfo=timezone.utc)
            assert out["dwd_corporate_action_term"][0]["amount_per_share"] == Decimal(
                "1.0000000001"
            )
            assert metrics["fields"] == {
                "dividend_per_share": "nonzero",
                "bonus_shares": "zero",
                "transfer_shares": "null",
            }
    assert row == before


def test_exact_tail_new_revision_does_not_claim_old_acceptance_or_flip_state():
    day = date(2026, 7, 24)
    clock = datetime(2026, 10, 4, tzinfo=timezone.utc)
    raw = pa.Table.from_pylist(
        [
            {
                "stock_code": "synthetic",
                "trade_date": day,
                "tradestatus": "0",
                "isST": "0",
                **{
                    k: Decimal("0") if k in {"volume", "amount"} else Decimal("2")
                    for k in ["open", "high", "low", "close", "volume", "amount"]
                },
            }
        ]
    )
    old = {
        "listing_id": "listing",
        "interval_size": "1d",
        "observation_start": datetime(2026, 7, 24, tzinfo=timezone.utc),
        "knowledge_from": datetime(2026, 7, 31, tzinfo=timezone.utc),
        "record_source": "old",
        "source_revision": "v1",
        "source_artifact_sha256": "hash",
        **{
            k: Decimal("0") if k in {"volume", "turnover"} else Decimal("1")
            for k in ["open", "high", "low", "close", "volume", "turnover"]
        },
    }
    state = {
        "listing_id": "listing",
        "calendar_session_id": "s",
        "knowledge_from": clock,
        "observation_start": old["observation_start"],
        "is_tradable": True,
        "reason_code": "TRADABLE",
    }
    gap = {
        "affected_effective_at": old["observation_start"],
        "gap_reason": "SOURCE_OBSERVATION_MISSING",
    }
    r = tail_repair_evidence(
        raw=raw,
        raw_sha256="raw",
        listing_id="listing",
        existing=[old],
        states=[state],
        gaps=[gap],
        observed_at=clock,
        version_key=["listing_id", "observation_start", "knowledge_from"],
        input_closure={
            key: "sha256:" + "a" * 64
            for key in [
                "original_ods_manifest",
                "original_dwd_manifest",
                "listing_members",
                "bar_members",
                "state_members",
                "gap_members",
                "contract",
                "price_basis_policy",
                "evaluator",
            ]
        },
    )
    assert r["state_mutations"] == 0 and r["conflicting_existing_versions"] == 1
    assert (
        r["provider_acquisition"] == "UNAVAILABLE"
        and r["historical_accepted_child"] == "NOT_ASSERTED"
    )
    assert r["nontrading_or_zero_activity"][0]["retained_states"][0]["is_tradable"]
    assert (
        r["allowed_operations"][0]["operation"]
        == "RETAIN_OLD_VALUE_VERSION_APPEND_NEW_VALUE_VERSION"
    )
    assert r["historical_measurement"]["three_security_total"] == 2025
    assert "Decimal" not in str(r)


@pytest.mark.parametrize("fault", ["missing", "non-hash"])
def test_tail_new_revision_refuses_incomplete_identity_closure(fault):
    closure = {
        key: "sha256:" + "a" * 64
        for key in [
            "original_ods_manifest",
            "original_dwd_manifest",
            "listing_members",
            "bar_members",
            "state_members",
            "gap_members",
            "contract",
            "price_basis_policy",
            "evaluator",
        ]
    }
    if fault == "missing":
        closure.pop("bar_members")
    else:
        closure["evaluator"] = "unpinned"
    with pytest.raises(SourceAssessmentError, match="QUALIFICATION_INPUT_CLOSURE_INVALID"):
        tail_repair_evidence(
            raw=pa.table({}),
            raw_sha256="hash",
            listing_id="listing",
            existing=[],
            states=[],
            gaps=[],
            observed_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
            version_key=["listing_id", "observation_start", "knowledge_from"],
            input_closure=closure,
        )
