"""Versioned execution-policy contract for the P2 backtest engine."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import time
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any


class ExecutionPolicyError(ValueError):
    """Raised when an execution policy is incomplete or internally inconsistent."""


class ProfileKind(str, Enum):
    CLOSE_TO_NEXT_OPEN = "close_to_next_open"
    TWO_WINDOW = "two_window"


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ExecutionPolicyError(f"{path} must be an object")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ExecutionPolicyError(f"{path} must be an array")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionPolicyError(f"{path} must be a non-empty string")
    return value


def _positive_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ExecutionPolicyError(f"{path} must be a positive integer")
    return value


def _decimal(value: Any, path: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise ExecutionPolicyError(f"{path} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ExecutionPolicyError(f"{path} must be a decimal string") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        qualifier = "positive" if positive else "non-negative"
        raise ExecutionPolicyError(f"{path} must be a finite {qualifier} decimal")
    return parsed


def _local_time(value: Any, path: str) -> time:
    raw = _string(value, path)
    try:
        parsed = time.fromisoformat(raw)
    except ValueError as exc:
        raise ExecutionPolicyError(f"{path} must be an ISO local time") from exc
    if parsed.tzinfo is not None:
        raise ExecutionPolicyError(f"{path} must not contain a timezone offset")
    return parsed


@dataclass(frozen=True)
class WindowDefinition:
    window_id: str
    start: time
    end: time
    decision_cutoff: time
    first_eligible_execution: time
    expiry: time

    def __post_init__(self) -> None:
        if not self.window_id.strip():
            raise ExecutionPolicyError("window_id must not be empty")
        if self.start >= self.end:
            raise ExecutionPolicyError(f"window {self.window_id} start must precede end")
        if not (
            self.start
            <= self.decision_cutoff
            < self.first_eligible_execution
            < self.expiry
            <= self.end
        ):
            raise ExecutionPolicyError(
                f"window {self.window_id} must order start, cutoff, execution, expiry, and end"
            )


@dataclass(frozen=True)
class ExecutionProfile:
    profile_id: str
    kind: ProfileKind
    active: bool
    schedule_version: str
    windows: tuple[WindowDefinition, ...]

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or not self.schedule_version.strip():
            raise ExecutionPolicyError("profile_id and schedule_version must not be empty")
        if self.kind is ProfileKind.CLOSE_TO_NEXT_OPEN and self.windows:
            raise ExecutionPolicyError("close_to_next_open profile must not declare windows")
        if self.kind is ProfileKind.TWO_WINDOW:
            if len(self.windows) != 2:
                raise ExecutionPolicyError("two_window profile must declare exactly two windows")
            ordered = sorted(self.windows, key=lambda item: (item.start, item.window_id))
            if len({window.window_id for window in ordered}) != 2:
                raise ExecutionPolicyError("two_window window ids must be unique")
            if ordered[0].end > ordered[1].start:
                raise ExecutionPolicyError("two_window windows must not overlap")


@dataclass(frozen=True)
class MoneyPolicy:
    price_tick: Decimal
    cash_quantum: Decimal
    rounding: str

    def __post_init__(self) -> None:
        if self.price_tick <= 0 or self.cash_quantum <= 0:
            raise ExecutionPolicyError("money quantums must be positive")
        if self.rounding != "ROUND_HALF_UP":
            raise ExecutionPolicyError("money.rounding must be ROUND_HALF_UP")


@dataclass(frozen=True)
class BuyCostPolicy:
    slippage_bps: Decimal
    commission_rate: Decimal
    minimum_commission: Decimal

    def __post_init__(self) -> None:
        if self.slippage_bps < 0 or self.minimum_commission < 0:
            raise ExecutionPolicyError("buy costs must not be negative")
        if not (Decimal("0") <= self.commission_rate < Decimal("1")):
            raise ExecutionPolicyError("buy commission_rate must be in [0, 1)")


@dataclass(frozen=True)
class SellCostPolicy:
    slippage_bps: Decimal
    commission_rate: Decimal
    minimum_commission: Decimal
    stamp_duty_rate: Decimal
    transfer_fee_rate: Decimal

    def __post_init__(self) -> None:
        if self.slippage_bps < 0 or self.minimum_commission < 0:
            raise ExecutionPolicyError("sell costs must not be negative")
        for value, field_name in (
            (self.commission_rate, "commission_rate"),
            (self.stamp_duty_rate, "stamp_duty_rate"),
            (self.transfer_fee_rate, "transfer_fee_rate"),
        ):
            if not (Decimal("0") <= value < Decimal("1")):
                raise ExecutionPolicyError(f"sell {field_name} must be in [0, 1)")


@dataclass(frozen=True)
class ExecutionPolicy:
    path: Path
    schema_version: str
    policy_version: str
    status: str
    scope: str
    basis: Mapping[str, str]
    currency: str
    timezone: str
    money: MoneyPolicy
    entry_lot_size: int
    partial_fill: str
    buy_cost: BuyCostPolicy
    sell_cost: SellCostPolicy
    profiles: tuple[ExecutionProfile, ...]

    def __post_init__(self) -> None:
        if self.status not in {"frozen", "synthetic-test-only"}:
            raise ExecutionPolicyError("status must be frozen or synthetic-test-only")
        allowed_scope = {
            "frozen": "historical-and-paper-research-only",
            "synthetic-test-only": "synthetic-acceptance-only",
        }
        if self.scope != allowed_scope[self.status]:
            raise ExecutionPolicyError("execution policy scope does not match its status")
        required_basis = {"commission", "stamp_duty", "transfer_fee", "slippage"}
        if set(self.basis) != required_basis or any(
            not value.strip() for value in self.basis.values()
        ):
            raise ExecutionPolicyError(
                "execution policy basis must explain commission, tax, transfer fee, and slippage"
            )
        if self.currency != "CNY" or self.timezone != "Asia/Shanghai":
            raise ExecutionPolicyError("the first P2 policy supports CNY and Asia/Shanghai only")
        if self.partial_fill != "unsupported":
            raise ExecutionPolicyError("the first P2 policy must reject partial-fill simulation")
        if len({profile.profile_id for profile in self.profiles}) != len(self.profiles):
            raise ExecutionPolicyError("execution profile ids must be unique")
        active = [profile for profile in self.profiles if profile.active]
        if len(active) != 1:
            raise ExecutionPolicyError("execution policy must declare exactly one active profile")

    @property
    def active_profile(self) -> ExecutionProfile:
        return next(profile for profile in self.profiles if profile.active)

    @property
    def official(self) -> bool:
        return self.status == "frozen"

    def profile(self, profile_id: str) -> ExecutionProfile:
        matches = [profile for profile in self.profiles if profile.profile_id == profile_id]
        if len(matches) != 1:
            raise ExecutionPolicyError(f"unknown execution profile: {profile_id}")
        return matches[0]


def _window(value: Any, path: str) -> WindowDefinition:
    raw = _mapping(value, path)
    return WindowDefinition(
        window_id=_string(raw.get("window_id"), f"{path}.window_id"),
        start=_local_time(raw.get("start"), f"{path}.start"),
        end=_local_time(raw.get("end"), f"{path}.end"),
        decision_cutoff=_local_time(raw.get("decision_cutoff"), f"{path}.decision_cutoff"),
        first_eligible_execution=_local_time(
            raw.get("first_eligible_execution"), f"{path}.first_eligible_execution"
        ),
        expiry=_local_time(raw.get("expiry"), f"{path}.expiry"),
    )


def _profile(value: Any, path: str) -> ExecutionProfile:
    raw = _mapping(value, path)
    try:
        kind = ProfileKind(_string(raw.get("kind"), f"{path}.kind"))
    except ValueError as exc:
        raise ExecutionPolicyError(f"{path}.kind is unsupported") from exc
    active = raw.get("active")
    if not isinstance(active, bool):
        raise ExecutionPolicyError(f"{path}.active must be boolean")
    windows = tuple(
        _window(item, f"{path}.windows[{index}]")
        for index, item in enumerate(_sequence(raw.get("windows"), f"{path}.windows"))
    )
    return ExecutionProfile(
        profile_id=_string(raw.get("profile_id"), f"{path}.profile_id"),
        kind=kind,
        active=active,
        schedule_version=_string(raw.get("schedule_version"), f"{path}.schedule_version"),
        windows=windows,
    )


def load_execution_policy(path: Path) -> ExecutionPolicy:
    """Load a strict execution policy without supplying implicit defaults."""

    resolved = path.resolve()
    try:
        raw_value = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExecutionPolicyError(f"execution policy does not exist: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise ExecutionPolicyError(f"invalid execution policy JSON: {exc}") from exc
    raw = _mapping(raw_value, "execution_policy")
    basis = _mapping(raw.get("basis"), "basis")
    money = _mapping(raw.get("money"), "money")
    orders = _mapping(raw.get("orders"), "orders")
    costs = _mapping(raw.get("costs"), "costs")
    buy = _mapping(costs.get("buy"), "costs.buy")
    sell = _mapping(costs.get("sell"), "costs.sell")
    profiles = tuple(
        _profile(item, f"profiles[{index}]")
        for index, item in enumerate(_sequence(raw.get("profiles"), "profiles"))
    )
    if not profiles:
        raise ExecutionPolicyError("profiles must not be empty")
    return ExecutionPolicy(
        path=resolved,
        schema_version=_string(raw.get("schema_version"), "schema_version"),
        policy_version=_string(raw.get("policy_version"), "policy_version"),
        status=_string(raw.get("status"), "status"),
        scope=_string(raw.get("scope"), "scope"),
        basis={
            key: _string(basis.get(key), f"basis.{key}")
            for key in ("commission", "stamp_duty", "transfer_fee", "slippage")
        },
        currency=_string(raw.get("currency"), "currency"),
        timezone=_string(raw.get("timezone"), "timezone"),
        money=MoneyPolicy(
            price_tick=_decimal(money.get("price_tick"), "money.price_tick", positive=True),
            cash_quantum=_decimal(money.get("cash_quantum"), "money.cash_quantum", positive=True),
            rounding=_string(money.get("rounding"), "money.rounding"),
        ),
        entry_lot_size=_positive_int(orders.get("entry_lot_size"), "orders.entry_lot_size"),
        partial_fill=_string(orders.get("partial_fill"), "orders.partial_fill"),
        buy_cost=BuyCostPolicy(
            slippage_bps=_decimal(buy.get("slippage_bps"), "costs.buy.slippage_bps"),
            commission_rate=_decimal(buy.get("commission_rate"), "costs.buy.commission_rate"),
            minimum_commission=_decimal(
                buy.get("minimum_commission"), "costs.buy.minimum_commission"
            ),
        ),
        sell_cost=SellCostPolicy(
            slippage_bps=_decimal(sell.get("slippage_bps"), "costs.sell.slippage_bps"),
            commission_rate=_decimal(sell.get("commission_rate"), "costs.sell.commission_rate"),
            minimum_commission=_decimal(
                sell.get("minimum_commission"), "costs.sell.minimum_commission"
            ),
            stamp_duty_rate=_decimal(sell.get("stamp_duty_rate"), "costs.sell.stamp_duty_rate"),
            transfer_fee_rate=_decimal(
                sell.get("transfer_fee_rate"), "costs.sell.transfer_fee_rate"
            ),
        ),
        profiles=profiles,
    )
