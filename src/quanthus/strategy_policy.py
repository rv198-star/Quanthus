"""Strict versioned policy for the first governed P3 research candidate."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class StrategyPolicyError(ValueError):
    """Raised when a governed strategy policy is incomplete or unsafe."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise StrategyPolicyError(f"{path} must be an object")
    return value


def _sequence(value: object, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise StrategyPolicyError(f"{path} must be an array")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StrategyPolicyError(f"{path} must be a non-empty string")
    return value


def _decimal(value: object, path: str) -> Decimal:
    if not isinstance(value, str):
        raise StrategyPolicyError(f"{path} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise StrategyPolicyError(f"{path} must be a decimal string") from exc
    if not parsed.is_finite():
        raise StrategyPolicyError(f"{path} must be finite")
    return parsed


def _positive_int(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise StrategyPolicyError(f"{path} must be a positive integer")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise StrategyPolicyError(f"{path} must be boolean")
    return value


def _date(value: object, path: str) -> date:
    try:
        return date.fromisoformat(_text(value, path))
    except ValueError as exc:
        raise StrategyPolicyError(f"{path} must be an ISO date") from exc


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise StrategyPolicyError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


def _text_tuple(value: object, path: str) -> tuple[str, ...]:
    items = tuple(
        _text(item, f"{path}[{index}]") for index, item in enumerate(_sequence(value, path))
    )
    if not items or len(items) != len(set(items)):
        raise StrategyPolicyError(f"{path} must contain unique non-empty values")
    return items


@dataclass(frozen=True)
class StrategyBindings:
    feature_registry_version: str
    label_registry_version: str
    execution_policy_version: str
    p0_policy_version: str
    position_state_machine_version: str
    industry_mapping_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "feature_registry_version": self.feature_registry_version,
            "label_registry_version": self.label_registry_version,
            "execution_policy_version": self.execution_policy_version,
            "p0_policy_version": self.p0_policy_version,
            "position_state_machine_version": self.position_state_machine_version,
            "industry_mapping_version": self.industry_mapping_version,
        }


@dataclass(frozen=True)
class StrategyValidationPolicy:
    development_start: date
    development_end: date
    arbitration_start: date
    arbitration_end: date
    permanent_blind_start: date
    permanent_blind_end: date
    parameter_change_budget_after_initial_run: int

    def __post_init__(self) -> None:
        boundaries = (
            self.development_start,
            self.development_end,
            self.arbitration_start,
            self.arbitration_end,
            self.permanent_blind_start,
            self.permanent_blind_end,
        )
        if any(left > right for left, right in zip(boundaries, boundaries[1:], strict=False)):
            raise StrategyPolicyError("validation partitions must be ordered")
        if self.development_end >= self.arbitration_start:
            raise StrategyPolicyError("development and arbitration partitions must not overlap")
        if self.arbitration_end >= self.permanent_blind_start:
            raise StrategyPolicyError("arbitration and blind partitions must not overlap")
        if self.parameter_change_budget_after_initial_run < 0:
            raise StrategyPolicyError("parameter change budget must not be negative")

    def require_development_scope(self, start: date, end: date) -> None:
        if start > end or start < self.development_start or end > self.development_end:
            raise StrategyPolicyError("governed strategy tuning is restricted to development data")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "development_start": self.development_start.isoformat(),
            "development_end": self.development_end.isoformat(),
            "arbitration_start": self.arbitration_start.isoformat(),
            "arbitration_end": self.arbitration_end.isoformat(),
            "permanent_blind_start": self.permanent_blind_start.isoformat(),
            "permanent_blind_end": self.permanent_blind_end.isoformat(),
            "parameter_change_budget_after_initial_run": (
                self.parameter_change_budget_after_initial_run
            ),
        }


@dataclass(frozen=True)
class PortfolioPolicy:
    unit_count: int
    unit_notional: Decimal
    trial_position_fraction: Decimal
    sizing_cash_buffer: Decimal
    entry_lot_size: int
    maximum_active_units: int
    adds_enabled: bool
    reductions_enabled: bool
    cooldown_sessions: int

    def __post_init__(self) -> None:
        if (
            self.unit_notional <= 0
            or self.unit_notional.quantize(Decimal("0.01")) != self.unit_notional
        ):
            raise StrategyPolicyError("unit_notional must be positive and use the cash quantum")
        if not Decimal("0") < self.trial_position_fraction < Decimal("1"):
            raise StrategyPolicyError("trial_position_fraction must be in (0, 1)")
        if not Decimal("0") <= self.sizing_cash_buffer < Decimal("1"):
            raise StrategyPolicyError("sizing_cash_buffer must be in [0, 1)")
        if self.trial_position_fraction * (Decimal("1") + self.sizing_cash_buffer) >= 1:
            raise StrategyPolicyError("trial sizing and cash buffer must leave funding headroom")
        if self.maximum_active_units > self.unit_count:
            raise StrategyPolicyError("maximum_active_units cannot exceed unit_count")
        if self.adds_enabled or self.reductions_enabled:
            raise StrategyPolicyError("governed strategy v1 does not support add or reduce")

    def to_dict(self) -> dict[str, str | int | bool]:
        return {
            "unit_count": self.unit_count,
            "unit_notional": str(self.unit_notional),
            "trial_position_fraction": str(self.trial_position_fraction),
            "sizing_cash_buffer": str(self.sizing_cash_buffer),
            "entry_lot_size": self.entry_lot_size,
            "maximum_active_units": self.maximum_active_units,
            "adds_enabled": self.adds_enabled,
            "reductions_enabled": self.reductions_enabled,
            "cooldown_sessions": self.cooldown_sessions,
        }


@dataclass(frozen=True)
class EntryPolicy:
    candidate_scan_limit: int
    minimum_listing_sessions: int
    minimum_average_amount20: Decimal
    minimum_market_breadth_above_sma60: Decimal
    minimum_momentum20: Decimal
    maximum_momentum20: Decimal
    minimum_breakout20: Decimal
    maximum_breakout20: Decimal
    minimum_volume_ratio20: Decimal
    maximum_volume_ratio20: Decimal
    minimum_rsi14: Decimal
    maximum_rsi14: Decimal
    maximum_realized_volatility20: Decimal
    maximum_absolute_entry_day_return: Decimal
    minimum_aggregate_score: Decimal

    def __post_init__(self) -> None:
        ordered_pairs = (
            (self.minimum_momentum20, self.maximum_momentum20, "momentum"),
            (self.minimum_breakout20, self.maximum_breakout20, "breakout"),
            (self.minimum_volume_ratio20, self.maximum_volume_ratio20, "volume ratio"),
            (self.minimum_rsi14, self.maximum_rsi14, "RSI"),
        )
        if any(not low < high for low, high, _name in ordered_pairs):
            raise StrategyPolicyError("entry threshold pairs must be strictly ordered")
        if not Decimal("0") <= self.minimum_market_breadth_above_sma60 <= Decimal("1"):
            raise StrategyPolicyError("market breadth threshold must be in [0, 1]")
        if not Decimal("0") < self.maximum_realized_volatility20 < Decimal("1"):
            raise StrategyPolicyError("maximum realized volatility must be in (0, 1)")
        if not Decimal("0") < self.maximum_absolute_entry_day_return < Decimal("1"):
            raise StrategyPolicyError("maximum entry-day return must be in (0, 1)")
        if not Decimal("-1") <= self.minimum_aggregate_score <= Decimal("1"):
            raise StrategyPolicyError("minimum aggregate score must be in [-1, 1]")

    def to_dict(self) -> dict[str, str | int]:
        return {
            field: str(value) if isinstance(value, Decimal) else value
            for field, value in self.__dict__.items()
        }


@dataclass(frozen=True)
class ExitPolicy:
    minimum_holding_sessions: int
    maximum_holding_sessions: int
    constraint_stop_loss: Decimal
    deterioration_sessions: int
    structural_break_rule: str
    deterioration_rule: str

    def __post_init__(self) -> None:
        if self.minimum_holding_sessions >= self.maximum_holding_sessions:
            raise StrategyPolicyError("minimum hold must precede maximum hold")
        if not Decimal("0") < self.constraint_stop_loss < Decimal("1"):
            raise StrategyPolicyError("constraint_stop_loss must be in (0, 1)")
        if self.structural_break_rule != "total_return_index_below_sma60_and_sma20_not_above_sma60":
            raise StrategyPolicyError("unsupported structural_break_rule")
        if self.deterioration_rule != "total_return_index_below_sma20_or_momentum20_not_positive":
            raise StrategyPolicyError("unsupported deterioration_rule")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "minimum_holding_sessions": self.minimum_holding_sessions,
            "maximum_holding_sessions": self.maximum_holding_sessions,
            "constraint_stop_loss": str(self.constraint_stop_loss),
            "deterioration_sessions": self.deterioration_sessions,
            "structural_break_rule": self.structural_break_rule,
            "deterioration_rule": self.deterioration_rule,
        }


@dataclass(frozen=True)
class EvaluationPolicy:
    minimum_closed_trades: int
    maximum_drawdown_warning: Decimal
    minimum_capital_matched_excess: Decimal
    minimum_return_excluding_top3: Decimal
    full_capital_excess_is_gate: bool
    required_breakdowns: tuple[str, ...]

    def __post_init__(self) -> None:
        if not Decimal("0") < self.maximum_drawdown_warning < Decimal("1"):
            raise StrategyPolicyError("maximum drawdown warning must be in (0, 1)")
        if self.full_capital_excess_is_gate:
            raise StrategyPolicyError("full-capital excess is a warning, not the candidate gate")
        if self.required_breakdowns != (
            "calendar_year",
            "market_breadth_regime",
            "holding_duration",
            "exit_source",
        ):
            raise StrategyPolicyError("required_breakdowns do not match candidate v1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_closed_trades": self.minimum_closed_trades,
            "maximum_drawdown_warning": str(self.maximum_drawdown_warning),
            "minimum_capital_matched_excess": str(self.minimum_capital_matched_excess),
            "minimum_return_excluding_top3": str(self.minimum_return_excluding_top3),
            "full_capital_excess_is_gate": self.full_capital_excess_is_gate,
            "required_breakdowns": list(self.required_breakdowns),
        }


@dataclass(frozen=True)
class GovernedStrategyPolicy:
    path: Path
    schema_version: str
    strategy_id: str
    strategy_version: str
    status: str
    hypothesis: str
    non_goals: tuple[str, ...]
    bindings: StrategyBindings
    validation: StrategyValidationPolicy
    portfolio: PortfolioPolicy
    entry: EntryPolicy
    exit: ExitPolicy
    evaluation: EvaluationPolicy

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0" or self.status != "research-only":
            raise StrategyPolicyError("governed strategy must use schema 1.0.0 and research-only")
        for value in (self.strategy_id, self.strategy_version, self.hypothesis):
            if not value.strip():
                raise StrategyPolicyError("strategy identity and hypothesis must not be empty")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "status": self.status,
            "hypothesis": self.hypothesis,
            "non_goals": list(self.non_goals),
            "bindings": self.bindings.to_dict(),
            "validation": self.validation.to_dict(),
            "portfolio": self.portfolio.to_dict(),
            "entry": self.entry.to_dict(),
            "exit": self.exit.to_dict(),
            "evaluation": self.evaluation.to_dict(),
        }


def load_governed_strategy_policy(path: Path) -> GovernedStrategyPolicy:
    """Load the first governed candidate without defaults or unknown fields."""

    try:
        raw_value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StrategyPolicyError(f"cannot read governed strategy policy: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StrategyPolicyError(f"invalid governed strategy policy JSON: {exc}") from exc
    raw = _mapping(raw_value, "strategy_policy")
    _exact_keys(
        raw,
        {
            "schema_version",
            "strategy_id",
            "strategy_version",
            "status",
            "hypothesis",
            "non_goals",
            "bindings",
            "validation",
            "portfolio",
            "entry",
            "exit",
            "evaluation",
        },
        "strategy_policy",
    )
    bindings = _mapping(raw["bindings"], "bindings")
    binding_keys = {
        "feature_registry_version",
        "label_registry_version",
        "execution_policy_version",
        "p0_policy_version",
        "position_state_machine_version",
        "industry_mapping_version",
    }
    _exact_keys(bindings, binding_keys, "bindings")
    validation = _mapping(raw["validation"], "validation")
    validation_keys = {
        "development_start",
        "development_end",
        "arbitration_start",
        "arbitration_end",
        "permanent_blind_start",
        "permanent_blind_end",
        "parameter_change_budget_after_initial_run",
    }
    _exact_keys(validation, validation_keys, "validation")
    portfolio = _mapping(raw["portfolio"], "portfolio")
    portfolio_keys = {
        "unit_count",
        "unit_notional",
        "trial_position_fraction",
        "sizing_cash_buffer",
        "entry_lot_size",
        "maximum_active_units",
        "adds_enabled",
        "reductions_enabled",
        "cooldown_sessions",
    }
    _exact_keys(portfolio, portfolio_keys, "portfolio")
    entry = _mapping(raw["entry"], "entry")
    entry_keys = set(EntryPolicy.__dataclass_fields__)
    _exact_keys(entry, entry_keys, "entry")
    exit_policy = _mapping(raw["exit"], "exit")
    exit_keys = set(ExitPolicy.__dataclass_fields__)
    _exact_keys(exit_policy, exit_keys, "exit")
    evaluation = _mapping(raw["evaluation"], "evaluation")
    evaluation_keys = set(EvaluationPolicy.__dataclass_fields__)
    _exact_keys(evaluation, evaluation_keys, "evaluation")

    return GovernedStrategyPolicy(
        path=path.resolve(),
        schema_version=_text(raw["schema_version"], "schema_version"),
        strategy_id=_text(raw["strategy_id"], "strategy_id"),
        strategy_version=_text(raw["strategy_version"], "strategy_version"),
        status=_text(raw["status"], "status"),
        hypothesis=_text(raw["hypothesis"], "hypothesis"),
        non_goals=_text_tuple(raw["non_goals"], "non_goals"),
        bindings=StrategyBindings(
            **{name: _text(bindings[name], f"bindings.{name}") for name in binding_keys}
        ),
        validation=StrategyValidationPolicy(
            development_start=_date(
                validation["development_start"], "validation.development_start"
            ),
            development_end=_date(validation["development_end"], "validation.development_end"),
            arbitration_start=_date(
                validation["arbitration_start"], "validation.arbitration_start"
            ),
            arbitration_end=_date(validation["arbitration_end"], "validation.arbitration_end"),
            permanent_blind_start=_date(
                validation["permanent_blind_start"], "validation.permanent_blind_start"
            ),
            permanent_blind_end=_date(
                validation["permanent_blind_end"], "validation.permanent_blind_end"
            ),
            parameter_change_budget_after_initial_run=_positive_int(
                validation["parameter_change_budget_after_initial_run"],
                "validation.parameter_change_budget_after_initial_run",
            ),
        ),
        portfolio=PortfolioPolicy(
            unit_count=_positive_int(portfolio["unit_count"], "portfolio.unit_count"),
            unit_notional=_decimal(portfolio["unit_notional"], "portfolio.unit_notional"),
            trial_position_fraction=_decimal(
                portfolio["trial_position_fraction"], "portfolio.trial_position_fraction"
            ),
            sizing_cash_buffer=_decimal(
                portfolio["sizing_cash_buffer"], "portfolio.sizing_cash_buffer"
            ),
            entry_lot_size=_positive_int(portfolio["entry_lot_size"], "portfolio.entry_lot_size"),
            maximum_active_units=_positive_int(
                portfolio["maximum_active_units"], "portfolio.maximum_active_units"
            ),
            adds_enabled=_boolean(portfolio["adds_enabled"], "portfolio.adds_enabled"),
            reductions_enabled=_boolean(
                portfolio["reductions_enabled"], "portfolio.reductions_enabled"
            ),
            cooldown_sessions=_positive_int(
                portfolio["cooldown_sessions"], "portfolio.cooldown_sessions"
            ),
        ),
        entry=EntryPolicy(
            candidate_scan_limit=_positive_int(
                entry["candidate_scan_limit"], "entry.candidate_scan_limit"
            ),
            minimum_listing_sessions=_positive_int(
                entry["minimum_listing_sessions"], "entry.minimum_listing_sessions"
            ),
            **{
                name: _decimal(entry[name], f"entry.{name}")
                for name in entry_keys
                if name not in {"candidate_scan_limit", "minimum_listing_sessions"}
            },
        ),
        exit=ExitPolicy(
            minimum_holding_sessions=_positive_int(
                exit_policy["minimum_holding_sessions"], "exit.minimum_holding_sessions"
            ),
            maximum_holding_sessions=_positive_int(
                exit_policy["maximum_holding_sessions"], "exit.maximum_holding_sessions"
            ),
            constraint_stop_loss=_decimal(
                exit_policy["constraint_stop_loss"], "exit.constraint_stop_loss"
            ),
            deterioration_sessions=_positive_int(
                exit_policy["deterioration_sessions"], "exit.deterioration_sessions"
            ),
            structural_break_rule=_text(
                exit_policy["structural_break_rule"], "exit.structural_break_rule"
            ),
            deterioration_rule=_text(exit_policy["deterioration_rule"], "exit.deterioration_rule"),
        ),
        evaluation=EvaluationPolicy(
            minimum_closed_trades=_positive_int(
                evaluation["minimum_closed_trades"], "evaluation.minimum_closed_trades"
            ),
            maximum_drawdown_warning=_decimal(
                evaluation["maximum_drawdown_warning"],
                "evaluation.maximum_drawdown_warning",
            ),
            minimum_capital_matched_excess=_decimal(
                evaluation["minimum_capital_matched_excess"],
                "evaluation.minimum_capital_matched_excess",
            ),
            minimum_return_excluding_top3=_decimal(
                evaluation["minimum_return_excluding_top3"],
                "evaluation.minimum_return_excluding_top3",
            ),
            full_capital_excess_is_gate=_boolean(
                evaluation["full_capital_excess_is_gate"],
                "evaluation.full_capital_excess_is_gate",
            ),
            required_breakdowns=_text_tuple(
                evaluation["required_breakdowns"], "evaluation.required_breakdowns"
            ),
        ),
    )
