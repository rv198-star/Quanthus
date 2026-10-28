from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.expert_strategy.held_structure_inputs import historical_daily_inputs
from quanthus.structure_v2_inputs import InputPolicy, admit_daily

SHA = "sha256:" + "a" * 64
DAYS = [date(2020, 1, i) for i in (2, 3, 6, 7)]
CUTOFF = datetime(2026, 10, 25, tzinfo=timezone.utc)


def arguments():
    return dict(
        code="000001",
        validity={
            "stock_code": "000001",
            "security_id": "000001",
            "exchange": "SZSE",
            "valid_from": "2011-01-04",
            "valid_to": date(2021, 12, 31),
        },
        bars=[
            {
                "stock_code": "000001",
                "security_id": "000001",
                "identity_exchange": "SZSE",
                "adj_mode": "none",
                "date": day,
                "high": 1.23,
                "low": 1.01,
                "close": 1.12,
                "volume": 100.0,
                "built_at": CUTOFF,
            }
            for day in DAYS
        ],
        sessions=DAYS,
        through=DAYS[-1],
        source_cutoff=CUTOFF,
        raw_member_sha256=SHA,
        calendar_member_sha256=SHA,
        action_closure_sha256=SHA,
        action_boundaries={},
    )


def test_native_identity_decimal_convention_and_real_clock_are_retained():
    rows = historical_daily_inputs(**arguments())
    assert rows[0].close == Decimal("1.12")
    assert rows[0].high == Decimal("1.23")
    assert rows[0].cutoff == CUTOFF
    assert rows[0].dwd_known_from == "unavailable"
    assert rows[0].listing_id.startswith("accepted-validity-interval:sha256:")
    assert rows[0].result_as_of.date() == DAYS[0]
    assert rows[0].result_as_of.hour == 15
    args = arguments()
    args["bars"][0]["built_at"] = CUTOFF.replace(day=24)
    changed = historical_daily_inputs(**args)
    assert changed[0].dwd_known_from == "unavailable"
    assert changed[0].source_record_id != rows[0].source_record_id


def test_missing_session_is_enumerated_and_cannot_be_flat_filled():
    args = arguments()
    del args["bars"][1]
    rows = historical_daily_inputs(**args)
    assert len(rows) == len(DAYS)
    assert rows[1].close is None and rows[1].quality_reason == "missing_raw_bar"
    assert rows[2].session_index == rows[1].session_index + 1
    assert not admit_daily(rows[1], InputPolicy(SHA, ("000001",), "calendar")).admitted


def test_action_epoch_changes_only_at_the_declared_effective_boundary():
    args = arguments()
    args["action_boundaries"] = {DAYS[2]: (SHA,)}
    rows = historical_daily_inputs(**args)
    assert rows[0].coordinate_epoch == rows[1].coordinate_epoch
    assert rows[2].coordinate_epoch == rows[3].coordinate_epoch
    assert rows[1].coordinate_epoch != rows[2].coordinate_epoch
    assert rows[2].reset_reason == "declared_action_boundary"
    args["action_boundaries"][date(2020, 1, 8)] = ("future",)
    assert historical_daily_inputs(**args) == rows


@pytest.mark.parametrize(
    "field,value",
    [
        ("identity_exchange", "SSE"),
        ("security_id", "000002"),
        ("stock_code", "000002"),
        ("adj_mode", "qfq"),
        ("date", date(2020, 1, 4)),
    ],
)
def test_wrong_venue_listing_basis_or_non_session_is_rejected(field, value):
    args = arguments()
    args["bars"][0][field] = value
    with pytest.raises(ValueError, match="HELD_RAW_DATE_VENUE_LISTING_OR_DUPLICATE"):
        historical_daily_inputs(**args)


def test_duplicate_date_is_rejected():
    args = arguments()
    args["bars"].append(args["bars"][0])
    with pytest.raises(ValueError):
        historical_daily_inputs(**args)


def test_zero_activity_and_retained_action_exceptions_remain_unknown():
    args = arguments()
    args["bars"][0]["volume"] = 0.0
    args["blocked_intervals"] = ((DAYS[1], DAYS[2], "exception-original"),)
    rows = historical_daily_inputs(**args)
    assert rows[0].quality_reason == "zero_activity_not_observed_trading"
    assert rows[1].quality_reason == rows[2].quality_reason == "retained_action_exception"


def test_post_development_scope_is_rejected():
    args = arguments()
    args["through"] = date(2022, 1, 4)
    with pytest.raises(ValueError, match="HELD_SOURCE_IDENTITY_OR_SCOPE"):
        historical_daily_inputs(**args)


def test_declared_right_censored_listing_preserves_unknown_end():
    args = arguments()
    args["validity"]["valid_to"] = None
    args["validity"]["valid_to_right_censored"] = True
    rows = historical_daily_inputs(**args)
    assert len(rows) == len(DAYS)
    assert args["validity"]["valid_to"] is None
    args["validity"]["valid_to_right_censored"] = False
    assert len(historical_daily_inputs(**args)) == len(DAYS)


def test_known_listing_end_is_enforced():
    args = arguments()
    args["validity"]["valid_to"] = DAYS[1]
    with pytest.raises(ValueError, match="HELD_RAW_DATE_VENUE_LISTING_OR_DUPLICATE"):
        historical_daily_inputs(**args)
