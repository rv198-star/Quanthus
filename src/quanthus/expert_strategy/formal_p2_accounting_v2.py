"""Additive correction of position-cycle accounting, qualified on synthetic data only.

No registry or real-run entry point selects this adapter. Real result execution
requires a separately bound successor admission; sealed v1 runs stay replayable.
"""

from __future__ import annotations

from decimal import Decimal

from quanthus.backtest.contracts import EngineEvent, UnitState
from quanthus.backtest.portfolio_replay import _wealth
from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime, _rate, _UnitRuntime

VERSION = "formal-p2-position-accounting-2.0.0"


class AccountingCorrectedReplayRuntime(FormalP2ReplayRuntime):
    """Unadmitted successor with pure hooks exercised by synthetic unit probes."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        # Actual source access is intentionally not wired to old registry receipts.
        # Unit probes allocate the instance without opening a provider or a Run.
        raise ValueError("FORMAL_ACCOUNTING_V2_REAL_RUN_NOT_ADMITTED")

    def _code_version(self) -> str:
        return VERSION

    def _record_entry(self, runtime: _UnitRuntime, before: UnitState, fill: EngineEvent) -> None:
        if before.settled_quantity != 0:
            raise ValueError("FORMAL_ACCOUNTING_V2_ENTRY_REQUIRES_EMPTY_POSITION")
        super()._record_entry(runtime, before, fill)
        runtime.entry_wealth = _wealth(before)
        runtime.strategy_reduction_count = 0
        runtime.deterioration_streak = 0
        runtime.cooldown_sessions = 0

    def _marked_return(self, runtime: _UnitRuntime) -> Decimal | None:
        if (
            runtime.entry_wealth is None
            or runtime.ledger.last_mark_price is None
            or runtime.entry_gross <= 0
        ):
            return None
        return _rate(
            (_wealth(runtime.ledger) - runtime.entry_wealth)
            / (runtime.entry_gross + runtime.entry_fees)
        )
