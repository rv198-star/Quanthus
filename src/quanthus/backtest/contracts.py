"""Typed event and accounting contracts for deterministic P2 replay."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from quanthus.contracts import RunManifest, SnapshotRef

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_finite(value: Decimal, field_name: str, *, positive: bool = False) -> None:
    if not value.is_finite() or value < 0 or (positive and value <= 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{field_name} must be a finite {qualifier} decimal")


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class EventType(str, Enum):
    SIGNAL_ACCEPTED = "signal_accepted"
    ORDER_INTENT_CREATED = "order_intent_created"
    ORDER_CREATED = "order_created"
    ORDER_STATUS = "order_status"
    FILL = "fill"
    LEDGER_ENTRY = "ledger_entry"
    CORPORATE_ACTION = "corporate_action"
    UNIT_SETTLEMENT = "unit_settlement"
    DAILY_SNAPSHOT = "daily_snapshot"
    NOTIFICATION = "notification"


class NotificationDeliveryState(str, Enum):
    DELIVERED = "delivered"
    FAILED = "failed"


class DecisionDisposition(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SHADOW = "shadow"


class OpenExecutionState(str, Enum):
    EXECUTABLE = "executable"
    SUSPENDED = "suspended"
    NON_TRADING_OBSERVED = "non_trading_observed"
    BUY_BLOCKED_LIMIT_UP = "buy_blocked_limit_up"
    SELL_BLOCKED_LIMIT_DOWN = "sell_blocked_limit_down"
    MISSING_EVIDENCE = "missing_evidence"


class MarkStatus(str, Enum):
    OBSERVED_CLOSE = "observed_close"
    CARRIED_FORWARD = "carried_forward"
    CORPORATE_ACTION_REFERENCE = "corporate_action_reference"
    NO_POSITION = "no_position"


@dataclass(frozen=True)
class BacktestSignal:
    signal_id: str
    snapshot: SnapshotRef
    decision_at: datetime
    profile_id: str
    window_id: str | None
    security_code: str
    side: OrderSide
    unit_id: str
    quantity: int
    aggregate_score: Decimal
    reason_code: str
    decision_version: str
    snapshot_replay_clock_json: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "signal_id",
            "profile_id",
            "security_code",
            "unit_id",
            "reason_code",
            "decision_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.window_id is not None:
            _require_text(self.window_id, "window_id")
        if not isinstance(self.side, OrderSide):
            raise ValueError("side must be an OrderSide")
        if (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity <= 0
        ):
            raise ValueError("quantity must be a positive integer")
        if not self.aggregate_score.is_finite():
            raise ValueError("aggregate_score must be finite")
        _require_aware(self.decision_at, "decision_at")
        if self.snapshot_replay_clock_json is not None:
            from quanthus.backtest.snapshot_clock import parse_replay_clock

            clock = parse_replay_clock(self.snapshot_replay_clock_json)
            if (
                clock.market_date != self.snapshot.market_date
                or clock.knowledge_cutoff != self.snapshot.knowledge_cutoff
                or self.decision_at != clock.simulated_close
                or self.profile_id != "close-to-next-open-v1"
                or self.window_id is not None
            ):
                raise ValueError("snapshot mechanism clock does not bind signal")
            object.__setattr__(self, "snapshot_replay_clock_json", clock.to_json())
        elif self.snapshot.knowledge_cutoff > self.decision_at:
            raise ValueError("signal decision cannot precede snapshot knowledge_cutoff")
        local_decision_date = self.decision_at.astimezone(MARKET_TIMEZONE).date()
        if local_decision_date != self.snapshot.market_date:
            raise ValueError("signal decision date must equal snapshot market_date")


def rank_signals(signals: Sequence[BacktestSignal]) -> tuple[BacktestSignal, ...]:
    if not signals:
        return ()
    first = signals[0]
    if first.side is not OrderSide.BUY:
        raise ValueError("candidate ranking accepts buy signals only")
    batch_identity = (
        first.snapshot.snapshot_id,
        first.snapshot.market_date,
        first.profile_id,
        first.window_id,
    )
    signal_ids: set[str] = set()
    security_codes: set[str] = set()
    for signal in signals:
        if signal.side is not OrderSide.BUY:
            raise ValueError("candidate batch cannot mix order sides")
        if (
            signal.snapshot.snapshot_id,
            signal.snapshot.market_date,
            signal.profile_id,
            signal.window_id,
        ) != batch_identity:
            raise ValueError("candidate batch must share snapshot, date, profile, and window")
        if signal.signal_id in signal_ids:
            raise ValueError(f"duplicate candidate signal_id: {signal.signal_id}")
        if signal.security_code in security_codes:
            raise ValueError(f"duplicate candidate security_code: {signal.security_code}")
        signal_ids.add(signal.signal_id)
        security_codes.add(signal.security_code)
    return tuple(
        sorted(signals, key=lambda signal: (-signal.aggregate_score, signal.security_code))
    )


@dataclass(frozen=True)
class MarketSession:
    session_date: date
    open_at: datetime
    close_at: datetime
    security_code: str
    open_price: Decimal | None
    close_price: Decimal | None
    open_execution_state: OpenExecutionState
    source_revision: str
    snapshot_id: str
    source_can_buy: bool | None = None
    source_can_sell: bool | None = None

    @property
    def source_direction_flags(self) -> dict[str, bool]:
        if self.source_can_buy is None:
            return {}
        assert self.source_can_sell is not None
        return {"source_can_buy": self.source_can_buy, "source_can_sell": self.source_can_sell}

    def __post_init__(self) -> None:
        if (self.source_can_buy is None) != (self.source_can_sell is None):
            raise ValueError("native direction flags must be supplied together")
        if self.source_can_buy is not None and (
            type(self.source_can_buy) is not bool or type(self.source_can_sell) is not bool
        ):
            raise ValueError("native direction flags must be booleans")
        _require_aware(self.open_at, "open_at")
        _require_aware(self.close_at, "close_at")
        if self.open_at >= self.close_at:
            raise ValueError("session open_at must precede close_at")
        if self.open_at.astimezone(MARKET_TIMEZONE).date() != self.session_date:
            raise ValueError("open_at must belong to session_date in Asia/Shanghai")
        if self.close_at.astimezone(MARKET_TIMEZONE).date() != self.session_date:
            raise ValueError("close_at must belong to session_date in Asia/Shanghai")
        for field_name in ("security_code", "source_revision", "snapshot_id"):
            _require_text(getattr(self, field_name), field_name)
        if not isinstance(self.open_execution_state, OpenExecutionState):
            raise ValueError("open_execution_state must be an OpenExecutionState")
        price_bearing_states = {
            OpenExecutionState.EXECUTABLE,
            OpenExecutionState.NON_TRADING_OBSERVED,
            OpenExecutionState.BUY_BLOCKED_LIMIT_UP,
            OpenExecutionState.SELL_BLOCKED_LIMIT_DOWN,
        }
        if self.open_execution_state in price_bearing_states:
            if self.open_price is None or self.close_price is None:
                raise ValueError("a price-bearing open state requires open and close prices")
            _require_finite(self.open_price, "open_price", positive=True)
            _require_finite(self.close_price, "close_price", positive=True)
        elif self.open_price is not None or self.close_price is not None:
            raise ValueError("a no-observation open state must not invent open or close prices")


@dataclass(frozen=True)
class BacktestRunSpec:
    code_version: str
    parameter_hash: str
    data_snapshot_id: str
    feature_version: str
    rule_version: str
    run_id: str
    execution_policy_version: str
    execution_profile_id: str
    schedule_version: str
    official: bool = False
    data_content_digest: str | None = None
    data_manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "code_version",
            "parameter_hash",
            "data_snapshot_id",
            "feature_version",
            "rule_version",
            "run_id",
            "execution_policy_version",
            "execution_profile_id",
            "schedule_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        if not isinstance(self.official, bool):
            raise ValueError("official must be boolean")
        for field_name in ("data_content_digest", "data_manifest_sha256"):
            value = getattr(self, field_name)
            if value is not None:
                _require_text(value, field_name)

    def manifest(self, result_summary: Mapping[str, Any]) -> dict[str, Any]:
        base = RunManifest(
            code_version=self.code_version,
            parameter_hash=self.parameter_hash,
            data_snapshot_id=self.data_snapshot_id,
            feature_version=self.feature_version,
            rule_version=self.rule_version,
            run_id=self.run_id,
            result_summary=result_summary,
        ).to_dict()
        base.update(
            {
                "execution_policy_version": self.execution_policy_version,
                "execution_profile_id": self.execution_profile_id,
                "schedule_version": self.schedule_version,
                "official": self.official,
            }
        )
        if self.data_content_digest is not None:
            base["data_content_digest"] = self.data_content_digest
        if self.data_manifest_sha256 is not None:
            base["data_manifest_sha256"] = self.data_manifest_sha256
        return base


@dataclass(frozen=True)
class UnitState:
    unit_id: str
    target_notional: Decimal
    cash: Decimal
    security_code: str | None
    settled_quantity: int
    position_cost: Decimal
    commission_expense: Decimal
    stamp_duty_expense: Decimal
    transfer_fee_expense: Decimal
    realized_pnl: Decimal
    dividend_income: Decimal
    external_pool: Decimal
    last_mark_price: Decimal | None
    last_mark_snapshot_id: str | None

    def __post_init__(self) -> None:
        _require_text(self.unit_id, "unit_id")
        _require_finite(self.target_notional, "target_notional", positive=True)
        for value, field_name in (
            (self.cash, "cash"),
            (self.position_cost, "position_cost"),
            (self.commission_expense, "commission_expense"),
            (self.stamp_duty_expense, "stamp_duty_expense"),
            (self.transfer_fee_expense, "transfer_fee_expense"),
            (self.dividend_income, "dividend_income"),
            (self.external_pool, "external_pool"),
        ):
            _require_finite(value, field_name)
        if not self.realized_pnl.is_finite():
            raise ValueError("realized_pnl must be finite")
        if (
            not isinstance(self.settled_quantity, int)
            or isinstance(self.settled_quantity, bool)
            or self.settled_quantity < 0
        ):
            raise ValueError("settled_quantity must be a non-negative integer")
        if self.settled_quantity == 0:
            if self.security_code is not None or self.position_cost != 0:
                raise ValueError("an empty unit cannot carry security or position cost")
            if self.last_mark_price is not None or self.last_mark_snapshot_id is not None:
                raise ValueError("an empty unit cannot carry a position mark")
        else:
            _require_text(self.security_code, "security_code")
            _require_finite(self.position_cost, "position_cost", positive=True)
            if self.last_mark_price is None or self.last_mark_snapshot_id is None:
                raise ValueError("a positioned unit requires a last mark and snapshot")
            _require_finite(self.last_mark_price, "last_mark_price", positive=True)
            _require_text(self.last_mark_snapshot_id, "last_mark_snapshot_id")

    @classmethod
    def empty(cls, *, unit_id: str, cash: Decimal) -> UnitState:
        return cls(
            unit_id=unit_id,
            target_notional=cash,
            cash=cash,
            security_code=None,
            settled_quantity=0,
            position_cost=Decimal("0.00"),
            commission_expense=Decimal("0.00"),
            stamp_duty_expense=Decimal("0.00"),
            transfer_fee_expense=Decimal("0.00"),
            realized_pnl=Decimal("0.00"),
            dividend_income=Decimal("0.00"),
            external_pool=Decimal("0.00"),
            last_mark_price=None,
            last_mark_snapshot_id=None,
        )


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    intent_id: str
    signal_id: str
    profile_id: str
    window_id: str | None
    security_code: str
    side: OrderSide
    unit_id: str
    quantity: int
    eligible_session: date
    policy_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "intent_id": self.intent_id,
            "signal_id": self.signal_id,
            "profile_id": self.profile_id,
            "window_id": self.window_id,
            "security_code": self.security_code,
            "side": self.side.value,
            "unit_id": self.unit_id,
            "quantity": self.quantity,
            "eligible_session": self.eligible_session.isoformat(),
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class OrderIntentRecord:
    intent_id: str
    signal_id: str
    profile_id: str
    window_id: str | None
    security_code: str
    side: OrderSide
    unit_id: str
    quantity: int
    created_at: datetime
    eligible_at: datetime
    expires_at: datetime | None
    unfilled_policy: str
    decision_reason_code: str
    policy_version: str

    def __post_init__(self) -> None:
        for field_name in (
            "intent_id",
            "signal_id",
            "profile_id",
            "security_code",
            "unit_id",
            "unfilled_policy",
            "decision_reason_code",
            "policy_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.window_id is not None:
            _require_text(self.window_id, "window_id")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.eligible_at, "eligible_at")
        if self.created_at >= self.eligible_at:
            raise ValueError("order intent must be created before it becomes eligible")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "expires_at")
            if self.expires_at <= self.eligible_at:
                raise ValueError("order intent expiry must follow eligibility")
        if self.side is OrderSide.BUY:
            if self.expires_at is None or self.unfilled_policy != "cancel_after_open_attempt":
                raise ValueError("daily buy intent must expire and cancel after its open attempt")
        elif self.expires_at is not None or self.unfilled_policy != "carry_until_tradable":
            raise ValueError("daily sell intent must carry without an implicit expiry")

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "signal_id": self.signal_id,
            "profile_id": self.profile_id,
            "window_id": self.window_id,
            "security_code": self.security_code,
            "side": self.side.value,
            "unit_id": self.unit_id,
            "quantity": self.quantity,
            "created_at": self.created_at.isoformat(),
            "eligible_at": self.eligible_at.isoformat(),
            "expires_at": None if self.expires_at is None else self.expires_at.isoformat(),
            "unfilled_policy": self.unfilled_policy,
            "decision_reason_code": self.decision_reason_code,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class FillRecord:
    fill_id: str
    order_id: str
    filled_at: datetime
    side: OrderSide
    price: Decimal
    quantity: int
    gross_amount: Decimal
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal
    total_fees: Decimal
    open_execution_state: OpenExecutionState
    source_revision: str
    snapshot_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "filled_at": self.filled_at.isoformat(),
            "side": self.side.value,
            "price": decimal_text(self.price),
            "quantity": self.quantity,
            "gross_amount": decimal_text(self.gross_amount),
            "commission": decimal_text(self.commission),
            "stamp_duty": decimal_text(self.stamp_duty),
            "transfer_fee": decimal_text(self.transfer_fee),
            "total_fees": decimal_text(self.total_fees),
            "open_execution_state": self.open_execution_state.value,
            "source_revision": self.source_revision,
            "snapshot_id": self.snapshot_id,
        }


@dataclass(frozen=True)
class LedgerPosting:
    account: str
    amount: Decimal

    def __post_init__(self) -> None:
        _require_text(self.account, "ledger account")
        if not self.amount.is_finite():
            raise ValueError("ledger posting amount must be finite")

    def to_dict(self) -> dict[str, str]:
        return {"account": self.account, "amount": decimal_text(self.amount)}


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    reference_id: str
    reference_type: str
    unit_id: str
    security_code: str | None
    quantity_delta: int
    postings: tuple[LedgerPosting, ...]

    def __post_init__(self) -> None:
        _require_text(self.reference_id, "ledger reference_id")
        _require_text(self.reference_type, "ledger reference_type")
        if not self.postings:
            raise ValueError("ledger entry must contain postings")
        if sum((posting.amount for posting in self.postings), Decimal("0")) != 0:
            raise ValueError("ledger entry postings must balance to zero")

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "reference_id": self.reference_id,
            "reference_type": self.reference_type,
            "unit_id": self.unit_id,
            "security_code": self.security_code,
            "quantity_delta": self.quantity_delta,
            "postings": [posting.to_dict() for posting in self.postings],
        }


@dataclass(frozen=True)
class LedgerSnapshot:
    snapshot_id: str
    session_date: date
    as_of: datetime
    unit_id: str
    cash: Decimal
    security_code: str | None
    position_quantity: int
    position_cost: Decimal
    market_value: Decimal
    commission_expense: Decimal
    stamp_duty_expense: Decimal
    transfer_fee_expense: Decimal
    realized_pnl: Decimal
    dividend_income: Decimal
    external_pool: Decimal
    total_equity: Decimal
    mark_status: MarkStatus
    mark_price: Decimal | None
    mark_snapshot_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "session_date": self.session_date.isoformat(),
            "as_of": self.as_of.isoformat(),
            "unit_id": self.unit_id,
            "cash": decimal_text(self.cash),
            "security_code": self.security_code,
            "position_quantity": self.position_quantity,
            "position_cost": decimal_text(self.position_cost),
            "market_value": decimal_text(self.market_value),
            "commission_expense": decimal_text(self.commission_expense),
            "stamp_duty_expense": decimal_text(self.stamp_duty_expense),
            "transfer_fee_expense": decimal_text(self.transfer_fee_expense),
            "realized_pnl": decimal_text(self.realized_pnl),
            "dividend_income": decimal_text(self.dividend_income),
            "external_pool": decimal_text(self.external_pool),
            "total_equity": decimal_text(self.total_equity),
            "mark_status": self.mark_status.value,
            "mark_price": None if self.mark_price is None else decimal_text(self.mark_price),
            "mark_snapshot_id": self.mark_snapshot_id,
        }


@dataclass(frozen=True)
class EngineEvent:
    event_id: str
    sequence: int
    event_type: EventType
    event_time: datetime
    reason_code: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "event_type": self.event_type.value,
            "event_time": self.event_time.isoformat(),
            "reason_code": self.reason_code,
            "payload": dict(self.payload),
        }


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class NotificationEvent:
    notification_id: str
    run_id: str
    signal_id: str
    profile_id: str
    window_id: str | None
    content_hash: str
    provider: str
    attempt: int
    attempted_at: datetime
    terminal_state: NotificationDeliveryState
    reason_code: str

    def __post_init__(self) -> None:
        for field_name in (
            "notification_id",
            "run_id",
            "signal_id",
            "profile_id",
            "provider",
            "reason_code",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.window_id is not None:
            _require_text(self.window_id, "window_id")
        if not _SHA256.fullmatch(self.content_hash):
            raise ValueError("content_hash must use sha256:<64 lowercase hex characters>")
        if not isinstance(self.attempt, int) or isinstance(self.attempt, bool) or self.attempt <= 0:
            raise ValueError("attempt must be a positive integer")
        _require_aware(self.attempted_at, "attempted_at")
        if not isinstance(self.terminal_state, NotificationDeliveryState):
            raise ValueError("terminal_state must be a NotificationDeliveryState")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": EventType.NOTIFICATION.value,
            "notification_id": self.notification_id,
            "run_id": self.run_id,
            "signal_id": self.signal_id,
            "profile_id": self.profile_id,
            "window_id": self.window_id,
            "content_hash": self.content_hash,
            "provider": self.provider,
            "attempt": self.attempt,
            "attempted_at": self.attempted_at.isoformat(),
            "terminal_state": self.terminal_state.value,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class DecisionEvent:
    decision_id: str
    run_id: str
    signal_id: str | None
    snapshot: SnapshotRef
    decided_at: datetime
    profile_id: str
    window_id: str | None
    security_code: str
    unit_id: str
    lifecycle_stage: str
    aggregate_score: Decimal
    expert_outputs: Mapping[str, Mapping[str, Any]]
    vetoes: tuple[str, ...]
    constraints: Mapping[str, str]
    proposed_action: str
    final_action: str
    reason_codes: tuple[str, ...]
    policy_versions: Mapping[str, str]
    linked_order_ids: tuple[str, ...]
    disposition: DecisionDisposition

    def __post_init__(self) -> None:
        for field_name in (
            "decision_id",
            "run_id",
            "profile_id",
            "security_code",
            "unit_id",
            "lifecycle_stage",
            "proposed_action",
            "final_action",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.signal_id is not None:
            _require_text(self.signal_id, "signal_id")
        if self.window_id is not None:
            _require_text(self.window_id, "window_id")
        _require_aware(self.decided_at, "decided_at")
        if self.snapshot.knowledge_cutoff > self.decided_at:
            raise ValueError("decision cannot precede snapshot knowledge_cutoff")
        if not self.aggregate_score.is_finite():
            raise ValueError("aggregate_score must be finite")
        if not isinstance(self.disposition, DecisionDisposition):
            raise ValueError("disposition must be a DecisionDisposition")
        if self.disposition is DecisionDisposition.ACCEPTED and self.signal_id is None:
            raise ValueError("an accepted decision requires signal_id")
        if self.disposition is not DecisionDisposition.ACCEPTED and self.linked_order_ids:
            raise ValueError("a rejected or shadow decision cannot link execution orders")
        if not self.expert_outputs or any(
            not name.strip() or not isinstance(output, Mapping)
            for name, output in self.expert_outputs.items()
        ):
            raise ValueError("expert_outputs must contain named object outputs")
        if not self.reason_codes or any(not reason.strip() for reason in self.reason_codes):
            raise ValueError("reason_codes must contain non-empty values")
        for values, field_name in (
            (self.vetoes, "vetoes"),
            (self.linked_order_ids, "linked_order_ids"),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{field_name} must contain non-empty values")
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")
        for mapping, field_name in (
            (self.constraints, "constraints"),
            (self.policy_versions, "policy_versions"),
        ):
            if not mapping or any(
                not key.strip() or not value.strip() for key, value in mapping.items()
            ):
                raise ValueError(f"{field_name} must contain non-empty keys and values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": "decision",
            "decision_id": self.decision_id,
            "run_id": self.run_id,
            "signal_id": self.signal_id,
            "snapshot": {
                "market_date": self.snapshot.market_date.isoformat(),
                "knowledge_cutoff": self.snapshot.knowledge_cutoff.isoformat(),
                "source_revision": self.snapshot.source_revision,
                "snapshot_id": self.snapshot.snapshot_id,
            },
            "decided_at": self.decided_at.isoformat(),
            "profile_id": self.profile_id,
            "window_id": self.window_id,
            "security_code": self.security_code,
            "unit_id": self.unit_id,
            "lifecycle_stage": self.lifecycle_stage,
            "aggregate_score": decimal_text(self.aggregate_score),
            "expert_outputs": {
                name: dict(output) for name, output in sorted(self.expert_outputs.items())
            },
            "vetoes": list(self.vetoes),
            "constraints": dict(sorted(self.constraints.items())),
            "proposed_action": self.proposed_action,
            "final_action": self.final_action,
            "reason_codes": list(self.reason_codes),
            "policy_versions": dict(sorted(self.policy_versions.items())),
            "linked_order_ids": list(self.linked_order_ids),
            "disposition": self.disposition.value,
        }


@dataclass(frozen=True)
class CorporateAction:
    action_id: str
    security_code: str
    effective_date: date
    known_at: datetime
    cash_dividend_per_share: Decimal
    bonus_share_ratio: Decimal
    source_revision: str
    snapshot_id: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.action_id, "action_id"),
            (self.security_code, "security_code"),
            (self.source_revision, "source_revision"),
            (self.snapshot_id, "snapshot_id"),
        ):
            _require_text(value, field_name)
        _require_aware(self.known_at, "known_at")
        _require_finite(self.cash_dividend_per_share, "cash_dividend_per_share")
        _require_finite(self.bonus_share_ratio, "bonus_share_ratio")
        if self.cash_dividend_per_share == 0 and self.bonus_share_ratio == 0:
            raise ValueError("corporate action must change cash or quantity")


@dataclass(frozen=True)
class CorporateActionStep:
    action: CorporateAction
    applied_at: datetime
    post_action_mark_price: Decimal
    mark_snapshot_id: str
    snapshot_replay_clock_json: str | None = None


@dataclass(frozen=True)
class CorporateActionApplication:
    state: UnitState
    events: tuple[EngineEvent, ...]


@dataclass(frozen=True)
class PositionMarkResult:
    state: UnitState
    events: tuple[EngineEvent, ...]


@dataclass(frozen=True)
class BacktestResult:
    run: BacktestRunSpec
    events: tuple[EngineEvent, ...]
    result_summary: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.run.manifest(self.result_summary),
            "events": [event.to_dict() for event in self.events],
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
