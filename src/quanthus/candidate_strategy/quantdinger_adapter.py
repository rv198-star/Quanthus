"""Signal-only adapter for the frozen QuantDinger bullish-three-lines template."""

from __future__ import annotations

from decimal import Decimal

from quanthus.backtest.strategy_replay import (
    ReplayAction,
    ReplayDecision,
    StrategyReplayContext,
)
from quanthus.candidate_strategy.contract import CandidateManifest


class QuantDingerCandidateAdapterError(ValueError):
    """Raised when PIT input cannot support a deterministic candidate decision."""


class QuantDingerBullishThreeLinesAdapter:
    """Re-express one upstream signal contract without importing its runtime or broker."""

    def __init__(self, manifest: CandidateManifest) -> None:
        self.manifest = manifest
        self.strategy_id = manifest.candidate_id
        self.strategy_version = manifest.candidate_version

    def decide(self, context: StrategyReplayContext) -> ReplayDecision:
        market = context.market_view
        if market is None:
            raise QuantDingerCandidateAdapterError("candidate requires a PIT market view")
        if self.manifest.security_code not in market.securities():
            raise QuantDingerCandidateAdapterError(
                "candidate security is absent from the current PIT universe"
            )
        history = market.history(self.manifest.security_code, self.manifest.long_period + 1)
        if any(bar.market_date > context.snapshot.market_date for bar in history):
            raise QuantDingerCandidateAdapterError("candidate history exposes a future session")
        if len(history) < self.manifest.long_period + 1:
            return ReplayDecision(ReplayAction.NO_ACTION, "candidate_warmup_incomplete")
        if any(
            bar.close_price is None
            or not bar.data_valid
            or bar.coverage_status.value != "trading"
            for bar in history
        ):
            return ReplayDecision(ReplayAction.NO_ACTION, "candidate_data_blocked")

        current = history[-1]
        if current.market_date != context.snapshot.market_date or current.open_price is None:
            raise QuantDingerCandidateAdapterError("candidate current bar is not PIT-aligned")
        prior_closes = tuple(bar.close_price for bar in history[:-1])
        assert all(value is not None for value in prior_closes)
        closes = tuple(value for value in prior_closes if value is not None)
        averages = tuple(
            sum(closes[-period:], Decimal("0")) / Decimal(period)
            for period in (
                self.manifest.short_period,
                self.manifest.mid_period,
                self.manifest.long_period,
            )
        )
        open_price = current.open_price
        close_price = current.close_price
        assert close_price is not None
        body_pct = (close_price - open_price) / open_price
        crossed = (
            body_pct >= self.manifest.min_body_pct
            and open_price <= min(averages)
            and close_price >= max(averages)
        )
        positioned = context.unit_state.settled_quantity > 0
        if crossed and not positioned:
            return ReplayDecision(
                ReplayAction.ENTER,
                "quantdinger_bullish_three_lines_entry",
                Decimal("1"),
                self.manifest.security_code,
                target_percent=self.manifest.target_pct,
            )
        if positioned and close_price < averages[-1]:
            return ReplayDecision(
                ReplayAction.EXIT,
                "quantdinger_bullish_three_lines_exit",
                Decimal("1"),
            )
        return ReplayDecision(ReplayAction.NO_ACTION, "quantdinger_bullish_three_lines_hold")


def build_quantdinger_adapter(
    manifest: CandidateManifest,
) -> QuantDingerBullishThreeLinesAdapter:
    if manifest.adapter_template != "bullish-three-lines":
        raise QuantDingerCandidateAdapterError("unsupported QuantDinger adapter template")
    return QuantDingerBullishThreeLinesAdapter(manifest)
