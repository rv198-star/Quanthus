from copy import deepcopy
from decimal import Decimal as D

import pytest

from quanthus.expert_strategy.p3_risk_attribution import (
    observed_entry_cohorts,
    period_equity_gap,
    position_cycles,
    snapshot_wealth,
)


def test_period_gap_uses_initial_capital_and_telescopes_across_months():
    ref = [
        {"market_date": "2025-01-01", "system_equity": "100"},
        {"market_date": "2025-01-31", "system_equity": "120"},
        {"market_date": "2025-02-03", "system_equity": "60"},
    ]
    alt = [dict(r, system_equity=v) for r, v in zip(ref, ("100", "110", "88"), strict=True)]
    result = period_equity_gap(ref, alt)
    assert result["difference"] == "0.28"
    assert result["months"] == {"2025-01": "-0.1", "2025-02": "0.38"}
    assert sum((D(r["contribution"]) for r in result["daily_contributions"]), D(0)) == D("0.28")


@pytest.mark.parametrize("bad", ["duplicate", "different_date", "capital", "outside", "NaN"])
def test_unpaired_and_invalid_equity_fail_closed(bad):
    a = [
        {"market_date": "2025-01-02", "system_equity": "100"},
        {"market_date": "2025-01-03", "system_equity": "101"},
    ]
    b = deepcopy(a)
    if bad == "duplicate":
        a[1]["market_date"] = a[0]["market_date"]
    elif bad == "different_date":
        b[1]["market_date"] = "2025-01-04"
    elif bad == "capital":
        b[0]["system_equity"] = "200"
    elif bad == "outside":
        a[1]["market_date"] = b[1]["market_date"] = "2026-09-19"
    else:
        b[1]["system_equity"] = "NaN"
    with pytest.raises(ValueError, match="P3_ATTR_"):
        period_equity_gap(a, b)


def fixture():
    events, decisions = [], []

    def event(kind, day, payload):
        events.append(
            {
                "event_id": f"e{len(events)}",
                "sequence": len(events),
                "event_type": kind,
                "event_time": day + "T09:30:00+08:00",
                "payload": payload,
            }
        )

    def buy(day, signal_day, name, gross, fee):
        decisions.append(
            {
                "surface": "entry",
                "filled": True,
                "allocated": True,
                "final_action": "trial_entry",
                "formal_decision_digest": name,
                "unit_id": "u",
                "market_date": signal_day,
                "decision_key": name,
            }
        )
        event(
            "signal_accepted",
            signal_day,
            {
                "signal_id": name,
                "decision_version": "v|" + name,
                "decision_at": signal_day + "T15:00:00+08:00",
            },
        )
        event(
            "order_created",
            signal_day,
            {
                "signal_id": name,
                "order_id": name,
                "unit_id": "u",
                "security_code": "synthetic",
                "side": "buy",
            },
        )
        event(
            "fill", day, {"order_id": name, "side": "buy", "gross_amount": gross, "total_fees": fee}
        )

    def snapshot(day, cash, mv, pool, quantity):
        event(
            "daily_snapshot",
            day,
            {
                "unit_id": "u",
                "session_date": day,
                "cash": cash,
                "market_value": mv,
                "external_pool": pool,
                "position_quantity": quantity,
            },
        )

    buy("2025-01-03", "2025-01-02", "a", "60", "1")
    snapshot("2025-01-03", "39", "60", "0", 6)
    event("signal_accepted", "2025-01-06", {"signal_id": "reduce"})
    event(
        "order_created",
        "2025-01-06",
        {"signal_id": "reduce", "order_id": "reduce", "unit_id": "u", "side": "sell"},
    )
    event(
        "fill",
        "2025-01-07",
        {"order_id": "reduce", "side": "sell", "gross_amount": "33", "total_fees": "1"},
    )
    snapshot("2025-01-07", "73", "33", "0", 3)  # includes 2 cash dividend
    event("signal_accepted", "2025-01-08", {"signal_id": "exit"})
    event(
        "order_created",
        "2025-01-08",
        {"signal_id": "exit", "order_id": "exit", "unit_id": "u", "side": "sell"},
    )
    event(
        "fill",
        "2025-01-09",
        {"order_id": "exit", "side": "sell", "gross_amount": "36", "total_fees": "1"},
    )
    snapshot("2025-01-09", "100", "0", "8", 0)  # 108 total, profit transferred to pool
    buy("2025-01-13", "2025-01-10", "b", "50", "1")
    snapshot("2025-01-13", "49", "55", "8", 5)
    snapshot("2025-01-14", "49", "60", "8", 5)
    return events, decisions


def test_cycle_wealth_includes_sales_dividends_pool_and_open_terminal_position():
    events, decisions = fixture()
    result = position_cycles(events, decisions, unit_notional=D(100), expected_units=["u"])
    assert len(result) == 2
    assert result[0]["closed"] and not result[1]["closed"]
    assert [r["pnl"] for r in result] == [D(8), D(9)]
    assert [r["fees"] for r in result] == [D(3), D(1)]
    assert sum((r["pnl"] for r in result), D(0)) == D(17)
    assert result[1]["entry_cost"] == D(51)
    assert snapshot_wealth({"cash": "100", "market_value": "0", "external_pool": "8"}) == D(108)


@pytest.mark.parametrize("changed_idle_wealth", [False, True])
def test_failed_new_buy_empty_snapshot_does_not_extend_the_old_cycle(changed_idle_wealth):
    events, decisions = fixture()
    source = next(
        e
        for e in events
        if e["event_type"] == "daily_snapshot" and e["payload"]["session_date"] == "2025-01-09"
    )
    extra = deepcopy(source)
    extra["event_id"], extra["sequence"] = "idle", 999
    extra["event_time"] = "2025-01-12T09:30:00+08:00"
    extra["payload"]["session_date"] = "2025-01-12"
    if changed_idle_wealth:
        extra["payload"]["cash"] = "101"
    events.append(extra)
    if changed_idle_wealth:
        with pytest.raises(ValueError, match="UNEXPLAINED_IDLE_WEALTH"):
            position_cycles(events, decisions, unit_notional=D(100), expected_units=["u"])
    else:
        result = position_cycles(events, decisions, unit_notional=D(100), expected_units=["u"])
        assert result[0]["last_date"] == "2025-01-09"
        assert [r["pnl"] for r in result] == [D(8), D(9)]


def test_cycle_dates_use_shanghai_business_day_and_preserve_time_instants():
    events, decisions = fixture()
    buy = next(e for e in events if e["event_type"] == "fill")
    buy["event_time"] = "2025-01-02T17:30:00+00:00"
    result = position_cycles(events, decisions, unit_notional=D(100), expected_units=["u"])
    assert result[0]["entry_date"] == "2025-01-03"
    buy["event_time"] = "2025-01-03T01:30:00"
    with pytest.raises(ValueError, match="NAIVE_TIME"):
        position_cycles(events, decisions, unit_notional=D(100), expected_units=["u"])


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("event", "DUPLICATE_EVENT"),
        ("snapshot", "DUPLICATE_SNAPSHOT"),
        ("orphan", "ORPHAN_FILL"),
        ("unit", "UNIT_SET"),
        ("entry", "BUY_ENTRY_BIJECTION"),
        ("baseline", "ENTRY_BASELINE"),
    ],
)
def test_cycle_identity_and_accounting_gaps_fail_closed(mutation, reason):
    events, decisions = fixture()
    if mutation == "event":
        events.append(deepcopy(events[0]))
    elif mutation == "snapshot":
        row = deepcopy(next(e for e in events if e["event_type"] == "daily_snapshot"))
        row["event_id"] = "extra"
        events.append(row)
    elif mutation == "orphan":
        next(e for e in events if e["event_type"] == "fill")["payload"]["order_id"] = "missing"
    elif mutation == "unit":
        next(e for e in events if e["event_type"] == "order_created")["payload"]["unit_id"] = (
            "other"
        )
    elif mutation == "entry":
        decisions.pop()
    else:
        next(e for e in events if e["event_type"] == "daily_snapshot")["payload"]["cash"] = "40"
    with pytest.raises(ValueError, match=reason):
        position_cycles(events, decisions, unit_notional=D(100), expected_units=["u"])


def test_cohorts_keep_unfilled_allowed_entries_and_capital_paths_separate_from_vetoes():
    reference = [
        {"entry_key": key, "pnl": D(pnl)}
        for key, pnl in (("blocked", "20"), ("allowed", "-5"), ("both", "10"))
    ]
    compared = [{"entry_key": key, "pnl": D(pnl)} for key, pnl in (("both", "4"), ("new", "3"))]
    a = {key: {"final_action": "trial_entry"} for key in ("blocked", "allowed", "both", "new")}
    b = deepcopy(a)
    b["blocked"]["final_action"] = "no_action"
    result = observed_entry_cohorts(reference, compared, a, b, initial_capital=D(100))
    assert result["observed_return_gap"] == "-0.18"
    assert result["causal_profit_ownership"] is False
    assert {k: v["observed_gap_contribution"] for k, v in result["cohorts"].items()} == {
        "reference_buys_blocked_by_compared_entry": "-0.2",
        "reference_only_buys_still_allowed_by_compared_entry": "0.05",
        "both_buy_same_candidate": "-0.06",
        "compared_only_buys": "0.03",
    }


@pytest.mark.parametrize("mutation", ["universe", "duplicate", "blocked", "unknown"])
def test_unqualified_cohort_matches_rejected(mutation):
    a = [{"entry_key": "a", "pnl": D(1)}]
    entries = {"a": {"final_action": "trial_entry"}}
    other = deepcopy(entries)
    if mutation == "universe":
        other.clear()
    elif mutation == "duplicate":
        a.append(deepcopy(a[0]))
    elif mutation == "blocked":
        entries["a"]["final_action"] = "no_action"
    else:
        a[0]["entry_key"] = "unknown"
    with pytest.raises(ValueError, match="P3_ATTR_"):
        observed_entry_cohorts(a, [], entries, other, initial_capital=D(100))
