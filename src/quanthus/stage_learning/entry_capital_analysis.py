"""Fixed-capital paired diagnostics, separate from state recognition quality."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any


def issuer_contributions(
    events: list[dict[str, Any]],
    ledgers: list[dict[str, Any]],
    *,
    initial: Decimal,
    ending: Decimal,
) -> dict[str, Decimal]:
    """Attribute actual wealth, not a counterfactual portfolio with an issuer removed."""
    result: dict[str, Decimal] = defaultdict(Decimal)
    for event in events:
        if event["event_type"] != "ledger_entry":
            continue
        row = event["payload"]
        change = sum(
            (
                Decimal(p["amount"])
                for p in row["postings"]
                if p["account"] in {"cash", "external_pool"}
            ),
            Decimal(0),
        )
        if change:
            if not row.get("security_code"):
                raise ValueError("ENTRY_ATTRIBUTION_MISSING_ISSUER")
            result[row["security_code"]] += change
    for row in ledgers:
        if row["settled_quantity"]:
            if not row.get("security_code") or row.get("last_mark_price") is None:
                raise ValueError("ENTRY_ATTRIBUTION_MISSING_MARK")
            result[row["security_code"]] += (
                Decimal(str(row["last_mark_price"])) * row["settled_quantity"]
            )
    if sum(result.values(), Decimal(0)) != ending - initial:
        raise ValueError("ENTRY_ISSUER_WEALTH_NOT_RECONCILED")
    return dict(result)


def metrics(equity: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    values = [Decimal(r["system_equity"]) for r in equity]
    if len(values) < 6 or values[0] != 500000 or min(values) <= 0:
        raise ValueError("ENTRY_CAPITAL_EQUITY_SCOPE")
    peak, drawdown = values[0], Decimal(0)
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak)
    fills = [e["payload"] for e in events if e["event_type"] == "fill"]
    return {
        "net_total_return": float(values[-1] / values[0] - 1),
        "maximum_drawdown": float(drawdown),
        "fill_count": len(fills),
        "fees": str(sum((Decimal(r["total_fees"]) for r in fills), Decimal(0))),
        "turnover": float(sum((Decimal(r["gross_amount"]) for r in fills), Decimal(0)) / values[0]),
        "sessions": len(values) - 1,
    }


def compare(
    arms: dict[str, dict[str, Any]],
    *,
    replicates: int = 10000,
    seed: int = 20210331,
    block: int = 5,
) -> dict[str, Any]:
    import numpy as np

    if set(arms) != {"C0", "C1", "C2"} or (replicates, seed, block) != (10000, 20210331, 5):
        raise ValueError("ENTRY_CAPITAL_FIXED_COMPARISON")
    dates = [r["market_date"] for r in arms["C0"]["equity"]]
    if len(set(dates)) != len(dates) or dates != sorted(dates):
        raise ValueError("ENTRY_CAPITAL_DATE_KEYS")
    values, increments, contributions, observed = {}, {}, {}, {}
    for arm, payload in arms.items():
        if dates != [r["market_date"] for r in payload["equity"]]:
            raise ValueError("ENTRY_CAPITAL_UNPAIRED_SESSIONS")
        reconciliation = payload["reconciliation"]
        vector = [Decimal(r["system_equity"]) for r in payload["equity"]]
        if (
            not reconciliation["reconciled"]
            or reconciliation["sealed_partition_access"]
            or Decimal(reconciliation["initial_equity"]) != vector[0]
            or Decimal(reconciliation["ending_equity"]) != vector[-1]
            or Decimal(reconciliation["unit_wealth"]) != vector[-1]
        ):
            raise ValueError("ENTRY_CAPITAL_UNRECONCILED")
        ledger_wealth = sum(
            (
                Decimal(r["cash"])
                + Decimal(r["external_pool"])
                + Decimal(str(r["last_mark_price"] or 0)) * r["settled_quantity"]
                for r in payload["ledgers"]
            ),
            Decimal(0),
        )
        if ledger_wealth != vector[-1]:
            raise ValueError("ENTRY_CAPITAL_FINAL_LEDGER_MISMATCH")
        values[arm] = vector
        increments[arm] = [b - a for a, b in zip(vector, vector[1:], strict=False)]
        contributions[arm] = issuer_contributions(
            payload["events"], payload["ledgers"], initial=vector[0], ending=vector[-1]
        )
        observed[arm] = metrics(payload["equity"], payload["events"])
    pairs = {}
    for control in ("C0", "C1"):
        changes = np.array(
            [
                float((a - b) / Decimal(500000))
                for a, b in zip(increments["C2"], increments[control], strict=True)
            ]
        )
        full = len(changes) // block
        sums = changes[: full * block].reshape(full, block).sum(axis=1)
        # Preserve the common horizon and all actual sessions. The terminal
        # partial block is held fixed; these are descriptive intervals only.
        remainder = float(changes[full * block :].sum())
        generator = np.random.default_rng(seed)
        boot = sums[generator.integers(0, full, size=(replicates, full))].sum(axis=1) + remainder
        low, high = np.quantile(boot, [0.025, 0.975])
        total = sum(increments["C2"], Decimal(0)) - sum(increments[control], Decimal(0))
        issuers = sorted(set(contributions["C2"]) | set(contributions[control]))
        issuer_deltas = [
            contributions["C2"].get(i, Decimal(0)) - contributions[control].get(i, Decimal(0))
            for i in issuers
        ]
        positive_issuers = [d for d in issuer_deltas if d > 0]
        worst_without_issuer = min((total - d for d in issuer_deltas), default=total)
        session_deltas = [a - b for a, b in zip(increments["C2"], increments[control], strict=True)]
        worst_without_session = min((total - d for d in session_deltas), default=total)
        pairs[control] = {
            "net_return_delta": float(total / Decimal(500000)),
            "paired_95_interval": [float(low), float(high)],
            "interval_strictly_positive": bool(low > 0),
            "drawdown_not_worse": observed["C2"]["maximum_drawdown"]
            <= observed[control]["maximum_drawdown"],
            "attributed_issuers": len(issuers),
            "positive_delta_issuers": len(positive_issuers),
            "worst_leave_one_issuer_contribution_delta": float(
                worst_without_issuer / Decimal(500000)
            ),
            "worst_leave_one_equity_session_delta": float(worst_without_session / Decimal(500000)),
            "advantage_survives_each_issuer_and_session_removal": (
                worst_without_issuer > 0 and worst_without_session > 0
            ),
        }
    passed = all(
        r["net_return_delta"] > 0
        and r["drawdown_not_worse"]
        and r["advantage_survives_each_issuer_and_session_removal"]
        and r["interval_strictly_positive"]
        for r in pairs.values()
    )
    return {
        "arms": observed,
        "C2_minus_control": pairs,
        "value_decision": "SCOPED_RESEARCH_PROPOSAL_ONLY" if passed else "NO_PASS_FIXED_VALUE_GATE",
        "consumer_admission": "NOT_GRANTED",
        "historical_PIT_claim": False,
        "bootstrap": {
            "block_sessions": block,
            "full_blocks": (len(dates) - 1) // block,
            "terminal_sessions_held_fixed": (len(dates) - 1) % block,
            "replicates": replicates,
            "seed": seed,
            "role": "descriptive; partial-block implementation was not separately preregistered",
        },
        "issuer_sensitivity": "Accounting attribution only, not capital reallocation reruns",
        "scope": {
            "first": dates[0],
            "last": dates[-1],
            "opportunities": 87,
            "companies": 66,
            "training_rows": 20,
            "training_positive": 4,
        },
    }
