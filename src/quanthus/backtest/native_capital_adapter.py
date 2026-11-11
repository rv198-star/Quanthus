"""Small native-input handshake; source clocks survive every adaptation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from quanthus.backtest.contracts import (
    MARKET_TIMEZONE,
    CorporateAction,
    MarketSession,
    OpenExecutionState,
)
from quanthus.pipelines.securities_warehouse.base24_p6_purpose import PurposeError, action_terms


class NativeAdapterError(ValueError):
    """An affected coordinate remains unqualified, without repairing its source."""


@dataclass(frozen=True)
class SourceClock:
    knowledge_from: datetime
    business_at: datetime
    announced_at: datetime | None

    def __post_init__(self) -> None:
        for value in (self.knowledge_from, self.business_at, self.announced_at):
            if value is not None and (
                not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
            ):
                raise NativeAdapterError("AWARE_SOURCE_CLOCK_REQUIRED")

    def source_pit_available(self, cutoff: datetime) -> bool:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise NativeAdapterError("AWARE_CUTOFF_REQUIRED")
        return self.knowledge_from <= cutoff


@dataclass(frozen=True)
class NativeActionTerms:
    action_id: str
    security_id: str
    ex_date: date
    source_clock: SourceClock
    cash_per_share: Decimal
    shares_per_share: Decimal
    term_ids: tuple[str, ...]
    term_source_clocks: tuple[SourceClock, ...]

    def source_pit_action(
        self, *, cutoff: datetime, source_revision: str, snapshot_id: str
    ) -> CorporateAction:
        clocks = (self.source_clock, *self.term_source_clocks)
        if not all(clock.source_pit_available(cutoff) for clock in clocks):
            raise NativeAdapterError("SOURCE_CLOCK_AFTER_CUTOFF")
        return CorporateAction(
            self.action_id,
            self.security_id,
            self.ex_date,
            max(clock.knowledge_from for clock in clocks),
            self.cash_per_share,
            self.shares_per_share,
            source_revision,
            snapshot_id,
        )


def qualify_action(header: dict[str, Any], terms: list[dict[str, Any]]) -> NativeActionTerms:
    if header["action_status"] != "IMPLEMENTED":
        raise NativeAdapterError("ACTION_NOT_IMPLEMENTED")
    effective = header["effective_at"]
    ex = header.get("ex_date")
    if not isinstance(effective, datetime) or not isinstance(ex, date) or isinstance(ex, datetime):
        raise NativeAdapterError("ACTION_BUSINESS_DATE_UNKNOWN")
    if effective.tzinfo is None or effective.utcoffset() is None:
        raise NativeAdapterError("AWARE_SOURCE_CLOCK_REQUIRED")
    if effective.astimezone(MARKET_TIMEZONE).date() != ex:
        raise NativeAdapterError("ACTION_EFFECTIVE_EX_DATE_MISMATCH")
    known = header.get("knowledge_from")
    if not isinstance(known, datetime):
        raise NativeAdapterError("SOURCE_KNOWLEDGE_UNKNOWN")
    term_clocks = []
    for term in terms:
        if term["corporate_action_id"] != header["corporate_action_id"]:
            raise NativeAdapterError("ACTION_TERM_IDENTITY_MISMATCH")
        if term.get("effective_at") != effective:
            raise NativeAdapterError("ACTION_TERM_DATE_MISMATCH")
        term_known = term.get("knowledge_from")
        if not isinstance(term_known, datetime):
            raise NativeAdapterError("SOURCE_TERM_KNOWLEDGE_UNKNOWN")
        term_clocks.append(SourceClock(term_known, effective, term.get("announced_at")))
    ids = tuple(term["corporate_action_term_id"] for term in terms)
    if len(set(ids)) != len(ids):
        raise NativeAdapterError("DUPLICATE_ACTION_TERM_ID")
    try:
        cash, shares = action_terms(header["action_type"], terms)
    except PurposeError as exc:
        raise NativeAdapterError(str(exc)) from exc
    return NativeActionTerms(
        header["corporate_action_id"],
        header["security_id"],
        ex,
        SourceClock(known, effective, header.get("announced_at")),
        cash,
        shares,
        ids,
        tuple(term_clocks),
    )


def native_market_session(
    row: dict[str, Any],
    *,
    security_id: str,
    side: str,
    open_at: datetime,
    close_at: datetime,
    source_revision: str,
    snapshot_id: str,
) -> MarketSession:
    """A side-specific source gate; P2 alone subsequently owns matching and fills."""
    if side not in {"buy", "sell"}:
        raise NativeAdapterError("SIDE_REQUIRED")
    flags = [row.get(name) for name in ("is_tradable", "can_buy", "can_sell")]
    if any(type(value) is not bool for value in flags):
        raise NativeAdapterError("NATIVE_STATE_UNKNOWN")
    reason = row.get("reason_code")
    if reason == "TRADABLE_OBSERVATION_MISSING":
        raise NativeAdapterError("DATED_TRADABILITY_OBSERVATION_MISSING")
    if reason == "SUSPENDED" and not row["is_tradable"]:
        state = OpenExecutionState.SUSPENDED
        op = close = None
    else:
        if reason != "TRADABLE_OBSERVED" or not row["is_tradable"]:
            raise NativeAdapterError("NATIVE_REASON_UNQUALIFIED")
        if not row["can_" + side]:
            raise NativeAdapterError("NATIVE_" + side.upper() + "_BLOCKED")
        op, close, upper, lower = [
            row.get(x) for x in ("open", "close", "upper_price", "lower_price")
        ]
        if any(
            not isinstance(x, Decimal) or not x.is_finite() or x <= 0
            for x in (op, close, upper, lower)
        ):
            raise NativeAdapterError("NATIVE_PRICE_OR_BOUND_UNKNOWN")
        assert isinstance(op, Decimal) and isinstance(close, Decimal)
        assert isinstance(upper, Decimal) and isinstance(lower, Decimal)
        if not lower <= op <= upper or lower >= upper:
            raise NativeAdapterError("NATIVE_PRICE_BOUND_CONFLICT")
        state = (
            OpenExecutionState.BUY_BLOCKED_LIMIT_UP
            if op == upper
            else OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN
            if op == lower
            else OpenExecutionState.EXECUTABLE
        )
    return MarketSession(
        open_at.astimezone(MARKET_TIMEZONE).date(),
        open_at,
        close_at,
        security_id,
        op,
        close,
        state,
        source_revision,
        snapshot_id,
    )
