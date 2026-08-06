"""Fail-closed adapter from PIT-resolved P1 daily facts to P2 market sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from quanthus.backtest.contracts import (
    MARKET_TIMEZONE,
    MarketSession,
    OpenExecutionState,
)
from quanthus.contracts import SnapshotRef


class CoverageStatus(str, Enum):
    TRADING = "trading"
    SUSPENDED = "suspended"
    MISSING = "missing"
    NOT_LISTED = "not_listed"
    DELISTED = "delisted"


def buy_open_rejection_reason(state: OpenExecutionState) -> str | None:
    """Return the accepted P2 no-fill reason for one buy-side open state."""

    if not isinstance(state, OpenExecutionState):
        raise ValueError("state must be an OpenExecutionState")
    return {
        OpenExecutionState.SUSPENDED: "security_suspended",
        OpenExecutionState.BUY_BLOCKED_LIMIT_UP: "buy_blocked_limit_up",
        OpenExecutionState.MISSING_EVIDENCE: "missing_execution_evidence",
    }.get(state)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_optional_price(value: Decimal | None, field_name: str) -> None:
    if value is not None and (not value.is_finite() or value <= 0):
        raise ValueError(f"{field_name} must be a finite positive decimal when present")


@dataclass(frozen=True)
class P1DailyExecutionEvidence:
    snapshot: SnapshotRef
    security_code: str
    open_at: datetime
    close_at: datetime
    coverage_status: CoverageStatus
    data_valid: bool
    is_executable: bool
    entry_policy_allows: bool
    reason_code: str
    open_price: Decimal | None
    high_price: Decimal | None
    low_price: Decimal | None
    close_price: Decimal | None
    upper_limit_price: Decimal | None
    lower_limit_price: Decimal | None

    def __post_init__(self) -> None:
        for text_value, field_name in (
            (self.security_code, "security_code"),
            (self.reason_code, "reason_code"),
        ):
            if not text_value.strip():
                raise ValueError(f"{field_name} must not be empty")
        _require_aware(self.open_at, "open_at")
        _require_aware(self.close_at, "close_at")
        if self.open_at >= self.close_at:
            raise ValueError("open_at must precede close_at")
        if self.open_at.astimezone(MARKET_TIMEZONE).date() != self.snapshot.market_date:
            raise ValueError("open_at must match the P1 snapshot market date")
        if self.close_at.astimezone(MARKET_TIMEZONE).date() != self.snapshot.market_date:
            raise ValueError("close_at must match the P1 snapshot market date")
        if not isinstance(self.coverage_status, CoverageStatus):
            raise ValueError("coverage_status must be a CoverageStatus")
        for boolean_value, field_name in (
            (self.data_valid, "data_valid"),
            (self.is_executable, "is_executable"),
            (self.entry_policy_allows, "entry_policy_allows"),
        ):
            if not isinstance(boolean_value, bool):
                raise ValueError(f"{field_name} must be boolean")
        for price_value, field_name in (
            (self.open_price, "open_price"),
            (self.high_price, "high_price"),
            (self.low_price, "low_price"),
            (self.close_price, "close_price"),
            (self.upper_limit_price, "upper_limit_price"),
            (self.lower_limit_price, "lower_limit_price"),
        ):
            _require_optional_price(price_value, field_name)
        if (
            self.upper_limit_price is not None
            and self.lower_limit_price is not None
            and self.upper_limit_price <= self.lower_limit_price
        ):
            raise ValueError("upper_limit_price must exceed lower_limit_price")
        if self.coverage_status is CoverageStatus.TRADING and self.data_valid:
            prices = (self.open_price, self.high_price, self.low_price, self.close_price)
            if any(value is None for value in prices):
                raise ValueError("a valid trading row requires complete OHLC prices")
            assert self.open_price is not None
            assert self.high_price is not None
            assert self.low_price is not None
            assert self.close_price is not None
            if not (
                self.low_price <= self.open_price <= self.high_price
                and self.low_price <= self.close_price <= self.high_price
            ):
                raise ValueError("P1 execution evidence violates OHLC ordering")


def market_session_from_p1(evidence: P1DailyExecutionEvidence) -> MarketSession:
    """Map P1 evidence to one conservative open state without strategy inference."""

    no_observation = evidence.coverage_status in {
        CoverageStatus.SUSPENDED,
        CoverageStatus.MISSING,
        CoverageStatus.NOT_LISTED,
        CoverageStatus.DELISTED,
    }
    if evidence.coverage_status is CoverageStatus.SUSPENDED:
        state = OpenExecutionState.SUSPENDED
    elif (
        no_observation
        or not evidence.data_valid
        or not evidence.is_executable
        or not evidence.entry_policy_allows
    ):
        state = OpenExecutionState.MISSING_EVIDENCE
    else:
        if evidence.open_price is None or evidence.close_price is None:
            raise ValueError("executable P1 evidence requires open and close prices")
        at_upper_limit = (
            evidence.upper_limit_price is not None
            and evidence.open_price == evidence.upper_limit_price
        )
        at_lower_limit = (
            evidence.lower_limit_price is not None
            and evidence.open_price == evidence.lower_limit_price
        )
        if at_upper_limit and at_lower_limit:
            raise ValueError("open price cannot equal both upper and lower limits")
        if at_upper_limit:
            state = OpenExecutionState.BUY_BLOCKED_LIMIT_UP
        elif at_lower_limit:
            state = OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN
        else:
            state = OpenExecutionState.EXECUTABLE

    price_bearing = state in {
        OpenExecutionState.EXECUTABLE,
        OpenExecutionState.BUY_BLOCKED_LIMIT_UP,
        OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN,
    }
    return MarketSession(
        session_date=evidence.snapshot.market_date,
        open_at=evidence.open_at,
        close_at=evidence.close_at,
        security_code=evidence.security_code,
        open_price=evidence.open_price if price_bearing else None,
        close_price=evidence.close_price if price_bearing else None,
        open_execution_state=state,
        source_revision=evidence.snapshot.source_revision,
        snapshot_id=evidence.snapshot.snapshot_id,
    )
