"""Synthetic two-window intent boundaries without activating intraday matching."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from quanthus.backtest.contracts import MARKET_TIMEZONE, BacktestSignal, OrderSide
from quanthus.backtest.policy import ExecutionProfile, ProfileKind


class WindowIntentState(str, Enum):
    PENDING = "pending"
    ELIGIBLE = "eligible"
    EXPIRED = "expired"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _stable_intent_id(*parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return f"intent_{hashlib.sha256(payload).hexdigest()[:24]}"


@dataclass(frozen=True)
class WindowOrderIntent:
    intent_id: str
    signal_id: str
    profile_id: str
    schedule_version: str
    window_id: str
    market_date: date
    decision_cutoff: datetime
    created_at: datetime
    eligible_at: datetime
    expires_at: datetime
    snapshot_id: str
    security_code: str
    side: OrderSide
    unit_id: str
    quantity: int

    def state_at(self, observed_at: datetime) -> WindowIntentState:
        _require_aware(observed_at, "observed_at")
        if observed_at < self.eligible_at:
            return WindowIntentState.PENDING
        if observed_at < self.expires_at:
            return WindowIntentState.ELIGIBLE
        return WindowIntentState.EXPIRED

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "signal_id": self.signal_id,
            "profile_id": self.profile_id,
            "schedule_version": self.schedule_version,
            "window_id": self.window_id,
            "market_date": self.market_date.isoformat(),
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "created_at": self.created_at.isoformat(),
            "eligible_at": self.eligible_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "snapshot_id": self.snapshot_id,
            "security_code": self.security_code,
            "side": self.side.value,
            "unit_id": self.unit_id,
            "quantity": self.quantity,
        }


def build_window_order_intent(
    signal: BacktestSignal, profile: ExecutionProfile
) -> WindowOrderIntent:
    """Bind one PIT-valid signal to an explicit half-open synthetic window policy."""

    if profile.kind is not ProfileKind.TWO_WINDOW:
        raise ValueError("window intents require a two_window execution profile")
    if signal.profile_id != profile.profile_id:
        raise ValueError("signal and window profile identities must match")
    if signal.window_id is None:
        raise ValueError("a window signal must declare window_id")
    matches = [window for window in profile.windows if window.window_id == signal.window_id]
    if len(matches) != 1:
        raise ValueError(f"unknown window_id for profile: {signal.window_id}")
    window = matches[0]
    market_date = signal.snapshot.market_date
    start_at = datetime.combine(market_date, window.start, MARKET_TIMEZONE)
    decision_cutoff = datetime.combine(market_date, window.decision_cutoff, MARKET_TIMEZONE)
    eligible_at = datetime.combine(market_date, window.first_eligible_execution, MARKET_TIMEZONE)
    expires_at = datetime.combine(market_date, window.expiry, MARKET_TIMEZONE)
    end_at = datetime.combine(market_date, window.end, MARKET_TIMEZONE)

    if not start_at <= signal.decision_at < end_at:
        raise ValueError("window signal decision must occur inside its half-open action window")
    if signal.snapshot.knowledge_cutoff > decision_cutoff:
        raise ValueError("window signal snapshot exceeds its decision cutoff")
    if signal.decision_at < decision_cutoff:
        raise ValueError("window signal cannot be created before its configured decision cutoff")
    if signal.decision_at >= eligible_at:
        raise ValueError("window signal must be persisted before first eligible execution")

    return WindowOrderIntent(
        intent_id=_stable_intent_id(
            signal.signal_id,
            profile.profile_id,
            profile.schedule_version,
            signal.window_id,
            decision_cutoff.isoformat(),
            eligible_at.isoformat(),
            expires_at.isoformat(),
        ),
        signal_id=signal.signal_id,
        profile_id=profile.profile_id,
        schedule_version=profile.schedule_version,
        window_id=signal.window_id,
        market_date=market_date,
        decision_cutoff=decision_cutoff,
        created_at=signal.decision_at,
        eligible_at=eligible_at,
        expires_at=expires_at,
        snapshot_id=signal.snapshot.snapshot_id,
        security_code=signal.security_code,
        side=signal.side,
        unit_id=signal.unit_id,
        quantity=signal.quantity,
    )
