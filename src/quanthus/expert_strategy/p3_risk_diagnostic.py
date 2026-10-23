"""Pure diagnostics for admitted P3 traces; never a strategy execution adapter.

The wealth comparison is an accounting counterexample, not an activated replacement
return policy. Frozen replay outputs and expert mappings must remain unchanged.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from quanthus.expert_strategy.position_risk import PositionRiskPolicy

RATE = Decimal("0.00000001")


def classify_entry_veto(
    volatility: Decimal | None, rsi: Decimal | None, policy: PositionRiskPolicy
) -> str:
    """Explain the existing strict-above entry thresholds in their declared units."""
    if volatility is None or rsi is None:
        return "unavailable"
    if (
        not volatility.is_finite()
        or not rsi.is_finite()
        or volatility < 0
        or not Decimal(0) <= rsi <= Decimal(100)
    ):
        raise ValueError("P3_RISK_DIAGNOSTIC_FEATURE_UNIT_OR_VALUE")
    high_volatility = volatility > policy.maximum_entry_volatility
    high_rsi = rsi > policy.maximum_entry_rsi
    if high_volatility and high_rsi:
        return "volatility_and_rsi"
    if high_volatility:
        return "volatility_only"
    return "rsi_only" if high_rsi else "clear"


def marked_return_diagnostic(
    *,
    entry_gross: Decimal,
    entry_fees: Decimal,
    entry_wealth: Decimal,
    cash: Decimal,
    market_value: Decimal,
    external_pool: Decimal,
    stop_loss: Decimal,
) -> dict[str, Decimal | bool]:
    """Compare the v1 input with closed-ledger wealth since this position's entry.

    Entry wealth is captured BEFORE the buy, including the external pool. P2 cash
    already contains sale proceeds, dividends and fees. Adding those flows again
    would double count. The entry denominator stays gross buy amount plus buy fees;
    unrelated prior positions' gains/fees are excluded by the wealth baseline.
    """
    values = (entry_gross, entry_fees, entry_wealth, cash, market_value, external_pool, stop_loss)
    if any(not v.is_finite() for v in values) or (
        entry_gross <= 0 or entry_fees < 0 or market_value < 0 or not 0 < stop_loss < 1
    ):
        raise ValueError("P3_RISK_DIAGNOSTIC_ACCOUNTING_INPUT")
    cost = entry_gross + entry_fees
    legacy = ((market_value - cost) / cost).quantize(RATE, rounding=ROUND_HALF_UP)
    wealth = ((cash + market_value + external_pool - entry_wealth) / cost).quantize(
        RATE, rounding=ROUND_HALF_UP
    )
    return {
        "v1_marked_return": legacy,
        "wealth_return_counterexample": wealth,
        "different": legacy != wealth,
        "v1_stop": legacy <= -stop_loss,
        "wealth_stop": wealth <= -stop_loss,
        "v1_only_stop": legacy <= -stop_loss < wealth,
    }
