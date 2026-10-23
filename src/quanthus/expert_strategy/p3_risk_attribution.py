"""Read-only arithmetic for the corrected retrospective risk diagnosis.

Observed cycle cohorts explain a ledger difference; they are not independent
interventions or estimates of profits that a rejected candidate would have earned.
This module neither constructs a replay nor grants access to any result partition.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

ZERO = Decimal(0)


def _number(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("P3_ATTR_NONFINITE")
    return result


def _day(value: str) -> str:
    parsed = date.fromisoformat(value)
    if not date(2025, 1, 1) <= parsed <= date(2026, 9, 18):
        raise ValueError("P3_ATTR_WINDOW")
    return parsed.isoformat()


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("P3_ATTR_NAIVE_TIME")
    return parsed


def _business_day(value: str) -> str:
    return _day(_instant(value).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat())


def snapshot_wealth(row: Mapping[str, Any]) -> Decimal:
    """Settlements move cash into/out of the pool without creating wealth."""
    return sum((_number(row[k]) for k in ("cash", "market_value", "external_pool")), ZERO)


def period_equity_gap(
    reference: Sequence[Mapping[str, Any]], compared: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Add monthly currency changes over one common initial capital denominator.

    Contributions telescope exactly to the full-window return difference. They
    are not a sum of separately compounded monthly percentage returns.
    """
    dates = [_day(row["market_date"]) for row in reference]
    if (
        len(dates) < 2
        or dates != sorted(set(dates))
        or dates != [_day(row["market_date"]) for row in compared]
    ):
        raise ValueError("P3_ATTR_UNPAIRED_DATES")
    initial = _number(reference[0]["system_equity"])
    if initial <= 0 or initial != _number(compared[0]["system_equity"]):
        raise ValueError("P3_ATTR_INITIAL_CAPITAL")
    monthly: dict[str, Decimal] = defaultdict(Decimal)
    daily = []
    for i, day in enumerate(dates[1:], 1):
        delta = (
            _number(compared[i]["system_equity"])
            - _number(compared[i - 1]["system_equity"])
            - _number(reference[i]["system_equity"])
            + _number(reference[i - 1]["system_equity"])
        )
        monthly[day[:7]] += delta
        daily.append({"market_date": day, "contribution": str(delta / initial)})
    exact = (
        _number(compared[-1]["system_equity"]) - _number(reference[-1]["system_equity"])
    ) / initial
    if sum(monthly.values(), ZERO) / initial != exact:
        raise ValueError("P3_ATTR_PERIOD_RECONCILIATION")
    return {
        "difference": str(exact),
        "months": {month: str(value / initial) for month, value in sorted(monthly.items())},
        "daily_contributions": daily,
        "reconciled": True,
        "interpretation": "observed wealth changes divided by common initial capital",
    }


def position_cycles(
    events: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    *,
    unit_notional: Decimal,
    expected_units: Sequence[str],
) -> list[dict[str, Any]]:
    """Partition actual fills/snapshots by successful new buy, retaining open cycles.

    No mark price leaves this function. Cost and PnL are internal ledger amounts;
    the caller exports aggregate or capital-normalized evidence only.
    """
    if not unit_notional.is_finite() or unit_notional <= 0:
        raise ValueError("P3_ATTR_INITIAL_CAPITAL")
    ids = [e["event_id"] for e in events]
    if len(set(ids)) != len(ids):
        raise ValueError("P3_ATTR_DUPLICATE_EVENT")
    orders: dict[str, Mapping[str, Any]] = {}
    signals: dict[str, Mapping[str, Any]] = {}
    snapshots: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    fills: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    entries: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for d in decisions:
        if d["surface"] == "entry" and d["filled"]:
            if d["final_action"] != "trial_entry" or not d["allocated"]:
                raise ValueError("P3_ATTR_INVALID_FILLED_ENTRY")
            key = (d["formal_decision_digest"], d["unit_id"], _day(d["market_date"]))
            if key in entries:
                raise ValueError("P3_ATTR_DUPLICATE_ENTRY")
            entries[key] = d
    for e in events:
        p = e["payload"]
        if e["event_type"] == "order_created":
            if p["order_id"] in orders:
                raise ValueError("P3_ATTR_DUPLICATE_ORDER")
            orders[p["order_id"]] = p
        elif e["event_type"] == "signal_accepted":
            if p["signal_id"] in signals:
                raise ValueError("P3_ATTR_DUPLICATE_SIGNAL")
            signals[p["signal_id"]] = p
        elif e["event_type"] == "daily_snapshot":
            unit, day = p["unit_id"], _day(p["session_date"])
            if day in snapshots[unit]:
                raise ValueError("P3_ATTR_DUPLICATE_SNAPSHOT")
            snapshots[unit][day] = p
    for e in events:
        if e["event_type"] == "fill":
            order_id = e["payload"]["order_id"]
            if order_id not in orders:
                raise ValueError("P3_ATTR_ORPHAN_FILL")
            order = orders[order_id]
            if order["signal_id"] not in signals:
                raise ValueError("P3_ATTR_ORPHAN_SIGNAL")
            if order["side"] != e["payload"]["side"]:
                raise ValueError("P3_ATTR_FILL_SIDE")
            fills[order["unit_id"]].append(e)
    allowed = set(expected_units)
    if len(allowed) != len(expected_units) or not (set(fills) | set(snapshots)) <= allowed:
        raise ValueError("P3_ATTR_UNIT_SET")
    cycles = []
    used_entries = set()
    for unit in sorted(allowed):
        ss = snapshots[unit]
        ff = sorted(fills[unit], key=lambda e: (_instant(e["event_time"]), e["sequence"]))
        buys = [e for e in ff if e["payload"]["side"] == "buy"]
        if ff and (not buys or ff[0]["payload"]["side"] != "buy"):
            raise ValueError("P3_ATTR_SELL_WITHOUT_BUY")
        previous_end = unit_notional
        for i, buy in enumerate(buys):
            day = _business_day(buy["event_time"])
            following = _business_day(buys[i + 1]["event_time"]) if i + 1 < len(buys) else None
            cycle_days = sorted(d for d in ss if d >= day and (following is None or d < following))
            if not cycle_days or cycle_days[0] != day:
                raise ValueError("P3_ATTR_MISSING_BUY_SNAPSHOT")
            before = [ss[d] for d in sorted(ss) if d < day]
            baseline = snapshot_wealth(before[-1]) if before else unit_notional
            if before and before[-1]["position_quantity"] != 0:
                raise ValueError("P3_ATTR_OVERLAPPING_POSITION")
            if baseline != previous_end:
                raise ValueError("P3_ATTR_UNEXPLAINED_IDLE_WEALTH")
            first = ss[day]
            closed_days = [d for d in cycle_days if ss[d]["position_quantity"] == 0]
            if closed_days:
                # A later failed buy may emit another empty-unit snapshot. It
                # belongs to idle time, not to the preceding completed holding.
                close_day = closed_days[0]
                close_wealth = snapshot_wealth(ss[close_day])
                if any(
                    ss[d]["position_quantity"] != 0 or snapshot_wealth(ss[d]) != close_wealth
                    for d in cycle_days
                    if d > close_day
                ):
                    raise ValueError("P3_ATTR_UNEXPLAINED_IDLE_WEALTH")
                cycle_days = [d for d in cycle_days if d <= close_day]
            last = ss[cycle_days[-1]]
            cost = _number(buy["payload"]["gross_amount"]) + _number(buy["payload"]["total_fees"])
            if cost <= 0 or baseline != (
                _number(first["cash"]) + cost + _number(first["external_pool"])
            ):
                raise ValueError("P3_ATTR_ENTRY_BASELINE")
            order = orders[buy["payload"]["order_id"]]
            signal = signals[order["signal_id"]]
            signal_day = _business_day(signal["decision_at"])
            digest = signal["decision_version"].rpartition("|")[2]
            entry_key = (digest, unit, signal_day)
            if entry_key not in entries or entry_key in used_entries:
                raise ValueError("P3_ATTR_BUY_ENTRY_BIJECTION")
            used_entries.add(entry_key)
            entry = entries[entry_key]
            cycle_fills = [
                e
                for e in ff
                if _instant(e["event_time"]) >= _instant(buy["event_time"])
                and (following is None or _business_day(e["event_time"]) < following)
            ]
            sells = [e for e in cycle_fills if e["payload"]["side"] == "sell"]
            closed = last["position_quantity"] == 0
            if following is not None and not closed:
                raise ValueError("P3_ATTR_OVERLAPPING_POSITION")
            if closed and (not sells or _business_day(sells[-1]["event_time"]) != cycle_days[-1]):
                raise ValueError("P3_ATTR_UNEXPLAINED_CLOSE")
            end_wealth = snapshot_wealth(last)
            pnl = end_wealth - baseline
            cycles.append(
                {
                    "unit_id": unit,
                    "entry_key": entry["decision_key"],
                    "security_id": order["security_code"],
                    "signal_date": signal_day,
                    "entry_date": day,
                    "last_date": cycle_days[-1],
                    "closed": closed,
                    "pnl": pnl,
                    "entry_cost": cost,
                    "fees": sum((_number(e["payload"]["total_fees"]) for e in cycle_fills), ZERO),
                    "holding_snapshots": sum(ss[d]["position_quantity"] > 0 for d in cycle_days),
                    "sell_fill_count": len(sells),
                    "end_wealth": end_wealth,
                    "buy_event_id": buy["event_id"],
                }
            )
            previous_end = end_wealth
    if used_entries != set(entries):
        raise ValueError("P3_ATTR_BUY_ENTRY_BIJECTION")
    return cycles


def observed_entry_cohorts(
    reference_cycles: Sequence[Mapping[str, Any]],
    compared_cycles: Sequence[Mapping[str, Any]],
    reference_entries: Mapping[str, Mapping[str, Any]],
    compared_entries: Mapping[str, Mapping[str, Any]],
    *,
    initial_capital: Decimal,
) -> dict[str, Any]:
    """Reconcile the observed gap without attributing causal ownership to vetoes."""
    if not initial_capital.is_finite() or initial_capital <= 0:
        raise ValueError("P3_ATTR_INITIAL_CAPITAL")
    if reference_entries.keys() != compared_entries.keys():
        raise ValueError("P3_ATTR_ENTRY_UNIVERSE")
    a, b = ({c["entry_key"]: c for c in rows} for rows in (reference_cycles, compared_cycles))
    if len(a) != len(reference_cycles) or len(b) != len(compared_cycles):
        raise ValueError("P3_ATTR_DUPLICATE_CYCLE_ENTRY")
    groups: dict[str, dict[str, Any]] = {
        name: {"reference_cycles": 0, "compared_cycles": 0, "pnl_gap": ZERO}
        for name in (
            "reference_buys_blocked_by_compared_entry",
            "reference_only_buys_still_allowed_by_compared_entry",
            "both_buy_same_candidate",
            "compared_only_buys",
        )
    }
    for key in sorted(a.keys() | b.keys()):
        if key not in reference_entries:
            raise ValueError("P3_ATTR_CYCLE_ENTRY_UNKNOWN")
        if key in a and reference_entries[key]["final_action"] != "trial_entry":
            raise ValueError("P3_ATTR_CYCLE_ENTRY_BLOCKED")
        if key in b and compared_entries[key]["final_action"] != "trial_entry":
            raise ValueError("P3_ATTR_CYCLE_ENTRY_BLOCKED")
        if key in a and key in b:
            group = "both_buy_same_candidate"
        elif key in a:
            group = (
                "reference_buys_blocked_by_compared_entry"
                if compared_entries[key]["final_action"] != "trial_entry"
                else "reference_only_buys_still_allowed_by_compared_entry"
            )
        else:
            group = "compared_only_buys"
        item = groups[group]
        for sign, rows, field in ((-1, a, "reference_cycles"), (1, b, "compared_cycles")):
            if key in rows:
                item[field] += 1
                item["pnl_gap"] += sign * _number(rows[key]["pnl"])
    gap = sum((_number(c["pnl"]) for c in compared_cycles), ZERO) - sum(
        (_number(c["pnl"]) for c in reference_cycles), ZERO
    )
    if sum((g["pnl_gap"] for g in groups.values()), ZERO) != gap:
        raise ValueError("P3_ATTR_COHORT_RECONCILIATION")
    return {
        "cohorts": {
            key: {
                "reference_cycles": value["reference_cycles"],
                "compared_cycles": value["compared_cycles"],
                "observed_gap_contribution": str(value["pnl_gap"] / initial_capital),
            }
            for key, value in groups.items()
        },
        "observed_return_gap": str(gap / initial_capital),
        "reconciled": True,
        "causal_profit_ownership": False,
        "limitations": (
            "Entry filtering, sizing, exits, timing and shared capital jointly change paths. "
            "A cycle observed only in one variant is not a simulated opportunity in the other. "
            "Only complete registered interventions could separate component effects."
        ),
    }
