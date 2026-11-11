from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.backtest.contracts import OpenExecutionState
from quanthus.backtest.native_capital_adapter import (
    NativeAdapterError,
    native_market_session,
    qualify_action,
)


def specimen():
    effective = datetime(2020, 6, 1, 15, 59, tzinfo=timezone.utc)
    header = {
        "corporate_action_id": "A",
        "security_id": "S",
        "action_status": "IMPLEMENTED",
        "action_type": "CASH_DIVIDEND",
        "effective_at": effective,
        "ex_date": date(2020, 6, 1),
        "knowledge_from": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "announced_at": None,
    }
    term = {
        "corporate_action_term_id": "T",
        "corporate_action_id": "A",
        "effective_at": effective,
        "term_type": "CASH_AMOUNT_PER_SHARE",
        "knowledge_from": datetime(2026, 10, 2, tzinfo=timezone.utc),
        "currency": "CNY",
        "amount_per_share": Decimal("0.2"),
    }
    return header, term


def test_native_cash_unit_and_late_source_clock_preserved():
    h, t = specimen()
    payload = qualify_action(h, [t])
    assert payload.cash_per_share == Decimal("0.2") and payload.shares_per_share == 0
    assert payload.source_clock.knowledge_from == h["knowledge_from"] and h["announced_at"] is None
    with pytest.raises(NativeAdapterError, match="SOURCE_CLOCK_AFTER_CUTOFF"):
        payload.source_pit_action(
            cutoff=datetime(2020, 6, 1, tzinfo=timezone.utc), source_revision="R", snapshot_id="SN"
        )
    action = payload.source_pit_action(
        cutoff=datetime(2026, 11, 1, tzinfo=timezone.utc), source_revision="R", snapshot_id="SN"
    )
    assert action.known_at == t["knowledge_from"]
    assert payload.term_source_clocks[0].knowledge_from == t["knowledge_from"]
    with pytest.raises(NativeAdapterError, match="SOURCE_CLOCK_AFTER_CUTOFF"):
        payload.source_pit_action(cutoff=h["knowledge_from"], source_revision="R", snapshot_id="SN")


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("unknown_cash", "ACTION_VALUE"),
        ("currency", "ACTION_CURRENCY"),
        ("special", "ACTION_TERM"),
        ("foreign", "IDENTITY_MISMATCH"),
        ("date", "DATE_MISMATCH"),
        ("duplicate", "DUPLICATE_ACTION_TERM_ID"),
    ],
)
def test_ambiguous_action_is_held_without_zero_fill(mutation, reason):
    h, t = specimen()
    terms = [t]
    if mutation == "unknown_cash":
        t["amount_per_share"] = None
    if mutation == "currency":
        t["currency"] = None
    if mutation == "special":
        t["term_type"] = "SOURCE_DECLARED_NONUNIFORM_CASH_TAX_UNAVAILABLE_PUBLIC"
    if mutation == "foreign":
        t["corporate_action_id"] = "B"
    if mutation == "date":
        t["effective_at"] = datetime(2020, 6, 2, tzinfo=timezone.utc)
    if mutation == "duplicate":
        terms.append(dict(t))
    with pytest.raises(NativeAdapterError, match=reason):
        qualify_action(h, terms)


def test_share_ratios_are_explicit_and_additive():
    h, t = specimen()
    h["action_type"] = "MIXED_DISTRIBUTION"
    shares = [
        {
            "corporate_action_term_id": kind,
            "corporate_action_id": "A",
            "effective_at": h["effective_at"],
            "knowledge_from": t["knowledge_from"],
            "term_type": kind,
            "ratio_numerator": Decimal(n),
            "ratio_denominator": Decimal(10),
        }
        for kind, n in [("BONUS_SHARES_PER_SHARE", 1), ("TRANSFER_SHARES_PER_SHARE", 2)]
    ]
    assert qualify_action(h, [t, *shares]).shares_per_share == Decimal("0.3")
    shares[0]["ratio_denominator"] = Decimal(0)
    with pytest.raises(NativeAdapterError, match="ACTION_RATIO"):
        qualify_action(h, [t, *shares])


def test_native_side_gate_and_missing_observation():
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Shanghai")
    opening = datetime(2020, 6, 1, 9, 30, tzinfo=zone)
    closing = opening.replace(hour=15, minute=0)
    q = dict(
        is_tradable=True,
        can_buy=False,
        can_sell=True,
        reason_code="TRADABLE_OBSERVED",
        open=Decimal(10),
        close=Decimal(10),
        upper_price=Decimal(11),
        lower_price=Decimal(9),
    )

    def session(side):
        return native_market_session(
            q,
            security_id="S",
            side=side,
            open_at=opening,
            close_at=closing,
            source_revision="R",
            snapshot_id="SN",
        )

    with pytest.raises(NativeAdapterError, match="BUY_BLOCKED"):
        session("buy")
    assert session("sell").open_execution_state == OpenExecutionState.EXECUTABLE
    q["can_buy"] = True
    q["reason_code"] = "TRADABLE_OBSERVATION_MISSING"
    with pytest.raises(NativeAdapterError, match="OBSERVATION_MISSING"):
        session("buy")
    q["reason_code"] = "TRADABLE_OBSERVED"
    q["open"] = Decimal(11)
    assert session("sell").open_execution_state == OpenExecutionState.BUY_BLOCKED_LIMIT_UP
