"""Versioned daily indicators and simple research-only baseline strategies."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from quanthus.backtest.contracts import BacktestRunSpec, UnitState, decimal_text
from quanthus.backtest.engine import BacktestEngine, BacktestInputError
from quanthus.backtest.historical_bundle import HistoricalReplayBundle
from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.backtest.strategy_replay import (
    ReplayAction,
    ReplayDecision,
    StrategyReplayContext,
    StrategyReplayResult,
    run_strategy_replay,
)
from quanthus.feature_registry import FeatureRegistry

FEATURE_EVALUATOR_VERSION = "1.0.0"
BASELINE_RUNNER_VERSION = "1.0.0"
_STRATEGY_KINDS = {"ma_trend", "breakout20", "oversold_rebound"}


class BaselineResearchError(ValueError):
    """Raised when baseline research inputs violate their frozen contract."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise BaselineResearchError(f"{path} must be an object")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise BaselineResearchError(f"{path} must be an array")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BaselineResearchError(f"{path} must be a non-empty string")
    return value


def _decimal(value: Any, path: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise BaselineResearchError(f"{path} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise BaselineResearchError(f"{path} must be a decimal string") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        qualifier = "positive" if positive else "non-negative"
        raise BaselineResearchError(f"{path} must be a finite {qualifier} decimal")
    return parsed


def _positive_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise BaselineResearchError(f"{path} must be a positive integer")
    return value


def _date(value: Any, path: str) -> date:
    raw = _text(value, path)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise BaselineResearchError(f"{path} must be an ISO date") from exc


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise BaselineResearchError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


@dataclass(frozen=True)
class BaselineSharedPolicy:
    initial_cash: Decimal
    entry_lot_size: int
    sizing_cash_buffer: Decimal
    minimum_listing_sessions: int
    minimum_average_amount_20: Decimal
    continuity_scope_policy: str

    def __post_init__(self) -> None:
        if self.initial_cash <= 0 or self.initial_cash != self.initial_cash.quantize(
            Decimal("0.01")
        ):
            raise BaselineResearchError("initial_cash must be positive and use the cash quantum")
        if self.entry_lot_size <= 0 or self.minimum_listing_sessions <= 0:
            raise BaselineResearchError("lot size and listing sessions must be positive")
        if not Decimal("0") <= self.sizing_cash_buffer < Decimal("1"):
            raise BaselineResearchError("sizing_cash_buffer must be in [0, 1)")
        if self.minimum_average_amount_20 < 0:
            raise BaselineResearchError("minimum_average_amount_20 must not be negative")
        if (
            self.continuity_scope_policy
            != "exclude_security_with_any_unresolved_continuity_evidence"
        ):
            raise BaselineResearchError("unsupported continuity_scope_policy")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "initial_cash": decimal_text(self.initial_cash),
            "entry_lot_size": self.entry_lot_size,
            "sizing_cash_buffer": decimal_text(self.sizing_cash_buffer),
            "minimum_listing_sessions": self.minimum_listing_sessions,
            "minimum_average_amount_20": decimal_text(self.minimum_average_amount_20),
            "continuity_scope_policy": self.continuity_scope_policy,
        }


@dataclass(frozen=True)
class BaselineValidationPolicy:
    development_start: date
    development_end: date
    arbitration_start: date
    arbitration_end: date
    permanent_blind_start: date
    permanent_blind_end: date

    def __post_init__(self) -> None:
        ordered = (
            self.development_start,
            self.development_end,
            self.arbitration_start,
            self.arbitration_end,
            self.permanent_blind_start,
            self.permanent_blind_end,
        )
        if any(
            current > following for current, following in zip(ordered, ordered[1:], strict=False)
        ):
            raise BaselineResearchError("validation periods must be ordered and non-overlapping")

    def require_development_scope(self, scope_start: date, scope_end: date) -> None:
        if scope_start > scope_end:
            raise BaselineResearchError("scope_start must not follow scope_end")
        if scope_start < self.development_start or scope_end > self.development_end:
            raise BaselineResearchError(
                "baseline parameter development is restricted to the frozen development period"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "development_start": self.development_start.isoformat(),
            "development_end": self.development_end.isoformat(),
            "arbitration_start": self.arbitration_start.isoformat(),
            "arbitration_end": self.arbitration_end.isoformat(),
            "permanent_blind_start": self.permanent_blind_start.isoformat(),
            "permanent_blind_end": self.permanent_blind_end.isoformat(),
        }


@dataclass(frozen=True)
class BaselineStrategyDefinition:
    strategy_id: str
    strategy_version: str
    kind: str
    parameters: Mapping[str, Decimal | int]

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise BaselineResearchError("strategy id and version must not be empty")
        if self.kind not in _STRATEGY_KINDS:
            raise BaselineResearchError(f"unsupported baseline strategy kind: {self.kind}")
        expected = {
            "ma_trend": {"maximum_holding_sessions", "minimum_momentum20"},
            "breakout20": {"maximum_holding_sessions", "minimum_volume_ratio20"},
            "oversold_rebound": {
                "entry_rsi_ceiling",
                "exit_rsi_floor",
                "maximum_holding_sessions",
            },
        }[self.kind]
        _exact_keys(self.parameters, expected, f"strategy {self.strategy_id} parameters")
        maximum_holding = self.parameters["maximum_holding_sessions"]
        if not isinstance(maximum_holding, int) or maximum_holding <= 0:
            raise BaselineResearchError("maximum_holding_sessions must be positive")
        if self.kind == "oversold_rebound":
            entry = self.parameters["entry_rsi_ceiling"]
            exit_value = self.parameters["exit_rsi_floor"]
            if not isinstance(entry, Decimal) or not isinstance(exit_value, Decimal):
                raise BaselineResearchError("RSI thresholds must be decimals")
            if not Decimal("0") <= entry < exit_value <= Decimal("100"):
                raise BaselineResearchError("RSI thresholds must satisfy 0 <= entry < exit <= 100")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def decimal_parameter(self, name: str) -> Decimal:
        value = self.parameters[name]
        if not isinstance(value, Decimal):
            raise BaselineResearchError(f"strategy parameter is not decimal: {name}")
        return value

    def int_parameter(self, name: str) -> int:
        value = self.parameters[name]
        if not isinstance(value, int):
            raise BaselineResearchError(f"strategy parameter is not integer: {name}")
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "kind": self.kind,
            "parameters": {
                key: decimal_text(value) if isinstance(value, Decimal) else value
                for key, value in sorted(self.parameters.items())
            },
        }


@dataclass(frozen=True)
class BaselineStrategyCatalogue:
    path: Path
    schema_version: str
    catalogue_version: str
    status: str
    feature_registry_version: str
    shared: BaselineSharedPolicy
    validation: BaselineValidationPolicy
    strategies: tuple[BaselineStrategyDefinition, ...]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0" or self.status != "research-only":
            raise BaselineResearchError("baseline catalogue must be schema 1.0.0 research-only")
        if not self.catalogue_version.strip() or not self.feature_registry_version.strip():
            raise BaselineResearchError("catalogue and feature registry versions must not be empty")
        if not self.strategies:
            raise BaselineResearchError("baseline catalogue must define strategies")
        ids = [item.strategy_id for item in self.strategies]
        if len(ids) != len(set(ids)):
            raise BaselineResearchError("baseline strategy ids must be unique")
        kinds = [item.kind for item in self.strategies]
        if set(kinds) != _STRATEGY_KINDS or len(kinds) != len(set(kinds)):
            raise BaselineResearchError("catalogue must define exactly one strategy of each kind")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "catalogue_version": self.catalogue_version,
            "status": self.status,
            "feature_registry_version": self.feature_registry_version,
            "shared": self.shared.to_dict(),
            "validation": self.validation.to_dict(),
            "strategies": [item.to_dict() for item in self.strategies],
        }


def _strategy_definition(value: Any, path: str) -> BaselineStrategyDefinition:
    raw = _mapping(value, path)
    _exact_keys(raw, {"strategy_id", "strategy_version", "kind", "parameters"}, path)
    kind = _text(raw.get("kind"), f"{path}.kind")
    parameters = _mapping(raw.get("parameters"), f"{path}.parameters")
    expected = {
        "ma_trend": {"maximum_holding_sessions", "minimum_momentum20"},
        "breakout20": {"maximum_holding_sessions", "minimum_volume_ratio20"},
        "oversold_rebound": {
            "entry_rsi_ceiling",
            "exit_rsi_floor",
            "maximum_holding_sessions",
        },
    }.get(kind)
    if expected is None:
        raise BaselineResearchError(f"{path}.kind is unsupported: {kind}")
    _exact_keys(parameters, expected, f"{path}.parameters")
    parsed: dict[str, Decimal | int] = {}
    for name in sorted(expected):
        raw_parameter = _text(parameters.get(name), f"{path}.parameters.{name}")
        if name == "maximum_holding_sessions":
            try:
                parsed[name] = int(raw_parameter)
            except ValueError as exc:
                raise BaselineResearchError(
                    f"{path}.parameters.{name} must be an integer string"
                ) from exc
        else:
            parsed[name] = _decimal(raw_parameter, f"{path}.parameters.{name}")
    return BaselineStrategyDefinition(
        strategy_id=_text(raw.get("strategy_id"), f"{path}.strategy_id"),
        strategy_version=_text(raw.get("strategy_version"), f"{path}.strategy_version"),
        kind=kind,
        parameters=parsed,
    )


def load_baseline_strategy_catalogue(path: Path) -> BaselineStrategyCatalogue:
    """Load the research catalogue without supplying defaults."""

    resolved = path.resolve()
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BaselineResearchError(f"baseline catalogue does not exist: {resolved}") from exc
    except json.JSONDecodeError as exc:
        raise BaselineResearchError(f"invalid baseline catalogue JSON: {exc}") from exc
    raw = _mapping(value, "baseline_catalogue")
    _exact_keys(
        raw,
        {
            "schema_version",
            "catalogue_version",
            "status",
            "feature_registry_version",
            "shared",
            "validation",
            "strategies",
        },
        "baseline_catalogue",
    )
    shared = _mapping(raw.get("shared"), "shared")
    _exact_keys(
        shared,
        {
            "initial_cash",
            "entry_lot_size",
            "sizing_cash_buffer",
            "minimum_listing_sessions",
            "minimum_average_amount_20",
            "continuity_scope_policy",
        },
        "shared",
    )
    validation = _mapping(raw.get("validation"), "validation")
    validation_keys = {
        "development_start",
        "development_end",
        "arbitration_start",
        "arbitration_end",
        "permanent_blind_start",
        "permanent_blind_end",
    }
    _exact_keys(validation, validation_keys, "validation")
    return BaselineStrategyCatalogue(
        path=resolved,
        schema_version=_text(raw.get("schema_version"), "schema_version"),
        catalogue_version=_text(raw.get("catalogue_version"), "catalogue_version"),
        status=_text(raw.get("status"), "status"),
        feature_registry_version=_text(
            raw.get("feature_registry_version"), "feature_registry_version"
        ),
        shared=BaselineSharedPolicy(
            initial_cash=_decimal(shared.get("initial_cash"), "shared.initial_cash", positive=True),
            entry_lot_size=_positive_int(shared.get("entry_lot_size"), "shared.entry_lot_size"),
            sizing_cash_buffer=_decimal(
                shared.get("sizing_cash_buffer"), "shared.sizing_cash_buffer"
            ),
            minimum_listing_sessions=_positive_int(
                shared.get("minimum_listing_sessions"), "shared.minimum_listing_sessions"
            ),
            minimum_average_amount_20=_decimal(
                shared.get("minimum_average_amount_20"),
                "shared.minimum_average_amount_20",
            ),
            continuity_scope_policy=_text(
                shared.get("continuity_scope_policy"),
                "shared.continuity_scope_policy",
            ),
        ),
        validation=BaselineValidationPolicy(
            **{name: _date(validation.get(name), f"validation.{name}") for name in validation_keys}
        ),
        strategies=tuple(
            _strategy_definition(item, f"strategies[{index}]")
            for index, item in enumerate(_sequence(raw.get("strategies"), "strategies"))
        ),
    )


@dataclass(frozen=True)
class BaselineFeatureRow:
    market_date: date
    security_id: str
    close_price: Decimal
    total_return_index: Decimal | None
    sma20: Decimal | None
    sma60: Decimal | None
    momentum20: Decimal | None
    prior_high20: Decimal | None
    rsi14: Decimal | None
    rsi14_previous: Decimal | None
    volume_ratio20: Decimal | None
    realized_volatility20: Decimal | None
    daily_total_return: Decimal | None
    average_amount20: Decimal | None
    listing_sessions: int
    feature_ready: bool
    entry_eligible: bool

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> BaselineFeatureRow:
        def optional(name: str) -> Decimal | None:
            value = row[name]
            return None if value is None else Decimal(str(value))

        return cls(
            market_date=row["market_date"],
            security_id=str(row["security_id"]),
            close_price=Decimal(str(row["close_price"])),
            total_return_index=optional("total_return_index"),
            sma20=optional("sma20"),
            sma60=optional("sma60"),
            momentum20=optional("momentum20"),
            prior_high20=optional("prior_high20"),
            rsi14=optional("rsi14"),
            rsi14_previous=optional("rsi14_previous"),
            volume_ratio20=optional("volume_ratio20"),
            realized_volatility20=optional("realized_volatility20"),
            daily_total_return=optional("daily_total_return"),
            average_amount20=optional("average_amount20"),
            listing_sessions=int(row["listing_sessions"]),
            feature_ready=bool(row["feature_ready"]),
            entry_eligible=bool(row["entry_eligible"]),
        )


@dataclass(frozen=True)
class BaselineCandidate:
    feature: BaselineFeatureRow
    score: Decimal


@dataclass(frozen=True)
class BaselineFeaturePanelReport:
    evaluator_version: str
    feature_registry_version: str
    feature_registry_digest: str
    bundle_snapshot_id: str
    bundle_digest: str
    scope_start: date
    scope_end: date
    row_count: int
    feature_ready_count: int
    entry_eligible_count: int
    exception_active_count: int
    unexplained_transition_count: int
    continuity_excluded_security_count: int

    @property
    def digest(self) -> str:
        return _digest(self.payload_dict())

    def payload_dict(self) -> dict[str, Any]:
        return {
            "evaluator_version": self.evaluator_version,
            "feature_registry_version": self.feature_registry_version,
            "feature_registry_digest": self.feature_registry_digest,
            "bundle_snapshot_id": self.bundle_snapshot_id,
            "bundle_digest": self.bundle_digest,
            "scope": {"start": self.scope_start.isoformat(), "end": self.scope_end.isoformat()},
            "counts": {
                "rows": self.row_count,
                "feature_ready": self.feature_ready_count,
                "entry_eligible": self.entry_eligible_count,
                "exception_active": self.exception_active_count,
                "unexplained_price_transition": self.unexplained_transition_count,
                "continuity_excluded_securities": self.continuity_excluded_security_count,
            },
            "data_quality_scope_selection_bias": True,
            "execution_authorized": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload_dict(), "digest": self.digest}


class BaselineFeaturePanel:
    """A date-sorted Parquet panel evaluated once and queried by cross-section."""

    _OUTPUT_COLUMNS = (
        "market_date",
        "security_id",
        "close_price",
        "total_return_index",
        "sma20",
        "sma60",
        "momentum20",
        "prior_high20",
        "rsi14",
        "rsi14_previous",
        "volume_ratio20",
        "realized_volatility20",
        "daily_total_return",
        "average_amount20",
        "listing_sessions",
        "feature_ready",
        "entry_eligible",
        "exception_active",
        "unexplained_price_transition",
        "scope_continuity_excluded",
    )

    def __init__(
        self,
        path: Path,
        report: BaselineFeaturePanelReport,
    ) -> None:
        self.path = path.resolve()
        self.report = report
        try:
            import duckdb
        except ModuleNotFoundError as exc:
            raise BaselineResearchError(
                "P1 dependencies are required for baseline research"
            ) from exc
        self._connection: Any | None = duckdb.connect(":memory:")
        self._connection.execute(
            "CREATE TABLE feature_panel AS SELECT * FROM read_parquet("
            f"{_sql_string(str(self.path))})"
        )
        self._connection.execute(
            "CREATE UNIQUE INDEX feature_panel_key ON "
            "feature_panel (market_date, security_id)"
        )

    def __enter__(self) -> BaselineFeaturePanel:
        if self._connection is None:
            raise BaselineResearchError("baseline feature panel is closed")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _rows(self, query: str, parameters: Sequence[object]) -> list[dict[str, Any]]:
        if self._connection is None:
            raise BaselineResearchError("baseline feature panel is closed")
        cursor = self._connection.execute(query, list(parameters))
        names = [str(item[0]) for item in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def feature(self, market_date: date, security_id: str) -> BaselineFeatureRow | None:
        rows = self._rows(
            "SELECT * EXCLUDE ("
            "exception_active, unexplained_price_transition, scope_continuity_excluded"
            ") "
            "FROM feature_panel "
            "WHERE market_date = ? AND security_id = ?",
            (market_date, security_id),
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise BaselineResearchError("feature panel contains duplicate security/date rows")
        return BaselineFeatureRow.from_mapping(rows[0])

    def best_candidate(
        self,
        market_date: date,
        definition: BaselineStrategyDefinition,
        shared: BaselineSharedPolicy,
    ) -> BaselineCandidate | None:
        common = """
            market_date = ?
            AND feature_ready
            AND entry_eligible
            AND listing_sessions >= ?
            AND average_amount20 >= ?
        """
        parameters: list[object] = [
            market_date,
            shared.minimum_listing_sessions,
            shared.minimum_average_amount_20,
        ]
        if definition.kind == "ma_trend":
            predicate = """
                AND total_return_index > sma20
                AND sma20 > sma60
                AND momentum20 >= ?
            """
            parameters.append(definition.decimal_parameter("minimum_momentum20"))
            score = "momentum20"
            ordering = "score DESC, realized_volatility20 ASC, security_id ASC"
        elif definition.kind == "breakout20":
            predicate = """
                AND total_return_index > prior_high20
                AND volume_ratio20 >= ?
            """
            parameters.append(definition.decimal_parameter("minimum_volume_ratio20"))
            score = "total_return_index / prior_high20 - 1"
            ordering = "score DESC, volume_ratio20 DESC, security_id ASC"
        else:
            predicate = """
                AND rsi14_previous <= ?
                AND rsi14 > ?
                AND daily_total_return > 0
            """
            ceiling = definition.decimal_parameter("entry_rsi_ceiling")
            parameters.extend((ceiling, ceiling))
            score = "100.0 - rsi14_previous"
            ordering = "score DESC, daily_total_return DESC, security_id ASC"
        rows = self._rows(
            f"""
            SELECT * EXCLUDE (
                exception_active,
                unexplained_price_transition,
                scope_continuity_excluded
            ), {score} AS score
            FROM feature_panel
            WHERE {common} {predicate}
            ORDER BY {ordering}
            LIMIT 1
            """,
            parameters,
        )
        if not rows:
            return None
        row = rows[0]
        return BaselineCandidate(
            feature=BaselineFeatureRow.from_mapping(row),
            score=Decimal(str(row["score"])),
        )

    def candidates(
        self,
        definition: BaselineStrategyDefinition,
        shared: BaselineSharedPolicy,
    ) -> Mapping[date, BaselineCandidate]:
        """Rank every daily cross-section once for one strategy version."""

        common = """
            market_date BETWEEN ? AND ?
            AND feature_ready
            AND entry_eligible
            AND listing_sessions >= ?
            AND average_amount20 >= ?
        """
        parameters: list[object] = [
            self.report.scope_start,
            self.report.scope_end,
            shared.minimum_listing_sessions,
            shared.minimum_average_amount_20,
        ]
        if definition.kind == "ma_trend":
            predicate = """
                AND total_return_index > sma20
                AND sma20 > sma60
                AND momentum20 >= ?
            """
            parameters.append(definition.decimal_parameter("minimum_momentum20"))
            score = "momentum20"
            ordering = "score DESC, realized_volatility20 ASC, security_id ASC"
        elif definition.kind == "breakout20":
            predicate = """
                AND total_return_index > prior_high20
                AND volume_ratio20 >= ?
            """
            parameters.append(definition.decimal_parameter("minimum_volume_ratio20"))
            score = "total_return_index / prior_high20 - 1"
            ordering = "score DESC, volume_ratio20 DESC, security_id ASC"
        else:
            predicate = """
                AND rsi14_previous <= ?
                AND rsi14 > ?
                AND daily_total_return > 0
            """
            ceiling = definition.decimal_parameter("entry_rsi_ceiling")
            parameters.extend((ceiling, ceiling))
            score = "100.0 - rsi14_previous"
            ordering = "score DESC, daily_total_return DESC, security_id ASC"
        rows = self._rows(
            f"""
            WITH eligible AS (
                SELECT *, {score} AS score
                FROM feature_panel
                WHERE {common} {predicate}
            ), ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY market_date ORDER BY {ordering}
                ) AS candidate_rank
                FROM eligible
            )
            SELECT * EXCLUDE (
                exception_active,
                unexplained_price_transition,
                scope_continuity_excluded,
                candidate_rank
            )
            FROM ranked
            WHERE candidate_rank = 1
            ORDER BY market_date
            """,
            parameters,
        )
        return {
            row["market_date"]: BaselineCandidate(
                feature=BaselineFeatureRow.from_mapping(row),
                score=Decimal(str(row["score"])),
            )
            for row in rows
        }

    @classmethod
    def build(
        cls,
        bundle: HistoricalReplayBundle,
        registry: FeatureRegistry,
        *,
        scope_start: date,
        scope_end: date,
        output: Path,
    ) -> BaselineFeaturePanel:
        """Build the PIT total-return panel with one vectorized historical scan."""

        if scope_start > scope_end:
            raise BaselineResearchError("scope_start must not follow scope_end")
        required_features = {
            "price.total_return_index",
            "price.sma20",
            "price.sma60",
            "price.momentum20",
            "price.prior_high20",
            "price.rsi14",
            "liquidity.volume_ratio20",
            "risk.realized_volatility20",
        }
        active_features = {
            definition.feature_id
            for definition in registry.definitions
            if definition.lifecycle.value == "active"
        }
        if active_features != required_features:
            raise BaselineResearchError("feature registry does not match the baseline evaluator")
        try:
            import duckdb
        except ModuleNotFoundError as exc:
            raise BaselineResearchError(
                "P1 dependencies are required for baseline research"
            ) from exc

        def dataset_path(name: str) -> Path:
            paths = bundle.partition_paths(name)
            if len(paths) != 1:
                raise BaselineResearchError(f"baseline evaluator requires one partition for {name}")
            return paths[0]

        paths = {
            name: dataset_path(name)
            for name in (
                "corporate_action_exceptions",
                "corporate_actions_normalized",
                "daily_ohlcv_none",
                "daily_universe",
                "price_limit_execution_daily",
                "security_code_validity",
            )
        }
        resolved = output.resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        temporary = resolved.with_name(f".{resolved.name}.{uuid.uuid4().hex}.tmp")
        connection = duckdb.connect(":memory:")
        try:
            for name, path in paths.items():
                connection.execute(
                    f"CREATE VIEW source_{name} AS "
                    f"SELECT * FROM read_parquet({_sql_string(str(path))})"
                )
            connection.execute(
                """
                CREATE VIEW feature_identity_map AS
                SELECT stock_code, min(security_id) AS security_id
                FROM source_security_code_validity
                GROUP BY stock_code
                HAVING count(DISTINCT security_id) = 1
                """
            )
            ambiguous_row = connection.execute(
                """
                    SELECT count(*)
                    FROM (
                        SELECT stock_code
                        FROM source_security_code_validity
                        GROUP BY stock_code
                        HAVING count(DISTINCT security_id) <> 1
                    )
                    """
            ).fetchone()
            if ambiguous_row is None:
                raise BaselineResearchError("identity ambiguity query returned no row")
            ambiguous = int(ambiguous_row[0])
            if ambiguous:
                raise BaselineResearchError("feature inputs contain ambiguous code identities")
            _validate_feature_sources(connection)
            query = _feature_panel_query(scope_start, scope_end)
            connection.execute(
                f"COPY ({query}) TO {_sql_string(str(temporary))} "
                "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)"
            )
            os.replace(temporary, resolved)
            counts = connection.execute(
                f"""
                SELECT count(*) AS row_count,
                       count(*) FILTER (WHERE feature_ready) AS feature_ready_count,
                       count(*) FILTER (WHERE entry_eligible) AS entry_eligible_count,
                       count(*) FILTER (WHERE exception_active) AS exception_active_count,
                       count(*) FILTER (
                           WHERE unexplained_price_transition
                       ) AS unexplained_transition_count,
                       count(DISTINCT security_id) FILTER (
                           WHERE scope_continuity_excluded
                       ) AS continuity_excluded_security_count
                FROM read_parquet({_sql_string(str(resolved))})
                """
            ).fetchone()
            assert counts is not None
        finally:
            connection.close()
            if temporary.exists():
                temporary.unlink()
        report = BaselineFeaturePanelReport(
            evaluator_version=FEATURE_EVALUATOR_VERSION,
            feature_registry_version=registry.registry_version,
            feature_registry_digest=registry.digest,
            bundle_snapshot_id=bundle.snapshot.snapshot_id,
            bundle_digest=bundle.digest,
            scope_start=scope_start,
            scope_end=scope_end,
            row_count=int(counts[0]),
            feature_ready_count=int(counts[1]),
            entry_eligible_count=int(counts[2]),
            exception_active_count=int(counts[3]),
            unexplained_transition_count=int(counts[4]),
            continuity_excluded_security_count=int(counts[5]),
        )
        if report.row_count == 0 or report.feature_ready_count == 0:
            raise BaselineResearchError("feature panel contains no research-ready observations")
        return cls(resolved, report)


def _validate_feature_sources(connection: Any) -> None:
    checks = {
        "unmapped action identity": """
            SELECT count(*)
            FROM source_corporate_actions_normalized actions
            LEFT JOIN feature_identity_map identities
              ON identities.stock_code = actions.security_code
            WHERE identities.security_id IS NULL
        """,
        "invalid normalized action": """
            SELECT count(*)
            FROM source_corporate_actions_normalized
            WHERE security_code IS NULL OR effective_date IS NULL OR known_at IS NULL
               OR cash_dividend_per_share IS NULL OR bonus_share_ratio IS NULL
               OR CAST(known_at AS DATE) >= CAST(effective_date AS DATE)
               OR cash_dividend_per_share < 0 OR bonus_share_ratio < 0
               OR (cash_dividend_per_share = 0 AND bonus_share_ratio = 0)
        """,
        "duplicate normalized action date": """
            SELECT count(*) FROM (
                SELECT identities.security_id, actions.effective_date
                FROM source_corporate_actions_normalized actions
                JOIN feature_identity_map identities
                  ON identities.stock_code = actions.security_code
                GROUP BY identities.security_id, actions.effective_date
                HAVING count(*) <> 1
            )
        """,
        "unmapped exception identity": """
            SELECT count(*)
            FROM source_corporate_action_exceptions exceptions
            LEFT JOIN feature_identity_map identities
              ON identities.stock_code = exceptions.security_code
            WHERE identities.security_id IS NULL
        """,
        "invalid exception interval": """
            SELECT count(*)
            FROM source_corporate_action_exceptions
            WHERE security_code IS NULL OR blocked_from IS NULL
               OR (blocked_through IS NOT NULL AND blocked_through < blocked_from)
        """,
        "duplicate universe row": """
            SELECT count(*) FROM (
                SELECT security_id, market_date
                FROM source_daily_universe
                GROUP BY security_id, market_date
                HAVING count(*) <> 1
            )
        """,
        "duplicate bar row": """
            SELECT count(*) FROM (
                SELECT security_id, date
                FROM source_daily_ohlcv_none
                GROUP BY security_id, date
                HAVING count(*) <> 1
            )
        """,
        "duplicate execution row": """
            SELECT count(*) FROM (
                SELECT security_id, execution_date
                FROM source_price_limit_execution_daily
                GROUP BY security_id, execution_date
                HAVING count(*) <> 1
            )
        """,
    }
    for label, query in checks.items():
        row = connection.execute(query).fetchone()
        if row is None:
            raise BaselineResearchError(f"{label} audit returned no row")
        if int(row[0]):
            raise BaselineResearchError(f"feature inputs contain {label}")


def _feature_panel_query(scope_start: date, scope_end: date) -> str:
    start = _sql_string(scope_start.isoformat())
    end = _sql_string(scope_end.isoformat())
    return f"""
        WITH mapped_actions AS (
            SELECT identity_map.security_id,
                   CAST(actions.effective_date AS DATE) AS effective_date,
                   CAST(actions.cash_dividend_per_share AS DOUBLE) AS cash_dividend_per_share,
                   CAST(actions.bonus_share_ratio AS DOUBLE) AS bonus_share_ratio
            FROM source_corporate_actions_normalized actions
            JOIN feature_identity_map identity_map
              ON identity_map.stock_code = actions.security_code
            WHERE CAST(actions.known_at AS DATE) < CAST(actions.effective_date AS DATE)
        ),
        mapped_exceptions AS (
            SELECT identity_map.security_id,
                   CAST(exceptions.blocked_from AS DATE) AS blocked_from,
                   CAST(exceptions.blocked_through AS DATE) AS blocked_through
            FROM source_corporate_action_exceptions exceptions
            JOIN feature_identity_map identity_map
              ON identity_map.stock_code = exceptions.security_code
        ),
        base AS (
            SELECT universe.security_id,
                   CAST(universe.market_date AS DATE) AS market_date,
                   CAST(bars.close AS DOUBLE) AS close_price,
                   CAST(bars.volume AS DOUBLE) AS volume,
                   CAST(bars.amount AS DOUBLE) AS amount,
                   coalesce(actions.cash_dividend_per_share, 0.0) AS cash_dividend_per_share,
                   coalesce(actions.bonus_share_ratio, 0.0) AS bonus_share_ratio,
                   actions.security_id IS NOT NULL AS has_normalized_action,
                   row_number() OVER (
                       PARTITION BY universe.security_id ORDER BY universe.market_date
                   ) AS listing_sessions,
                   bars.security_id IS NOT NULL
                       AND bars.open IS NOT NULL AND bars.high IS NOT NULL
                       AND bars.low IS NOT NULL AND bars.close IS NOT NULL
                       AND bars.volume IS NOT NULL AND bars.amount IS NOT NULL
                       AND bars.open > 0 AND bars.high > 0
                       AND bars.low > 0 AND bars.close > 0
                       AND bars.volume >= 0 AND bars.amount >= 0
                       AND bars.low <= bars.open AND bars.open <= bars.high
                       AND bars.low <= bars.close AND bars.close <= bars.high
                       AS bar_valid,
                   coalesce(limits.entry_policy_allows, false) AS entry_policy_allows,
                   coalesce(limits.is_executable, false) AS is_executable,
                   CAST(limits.upper_limit_pct AS DOUBLE) AS upper_limit_pct,
                   CAST(limits.lower_limit_pct AS DOUBLE) AS lower_limit_pct,
                   actions.security_id IS NULL
                       AND limits.lower_limit_price IS NOT NULL
                       AND limits.upper_limit_price IS NOT NULL
                       AND (
                              bars.close < CAST(limits.lower_limit_price AS DOUBLE) - 0.011
                           OR bars.close > CAST(limits.upper_limit_price AS DOUBLE) + 0.011
                       ) AS raw_price_transition_unexplained,
                   EXISTS (
                       SELECT 1
                       FROM mapped_exceptions exceptions
                       WHERE exceptions.security_id = universe.security_id
                         AND exceptions.blocked_from <= universe.market_date
                         AND (
                                exceptions.blocked_through IS NULL
                             OR exceptions.blocked_through >= universe.market_date
                         )
                   ) AS exception_active
            FROM source_daily_universe universe
            LEFT JOIN source_daily_ohlcv_none bars
              ON bars.security_id = universe.security_id
             AND CAST(bars.date AS DATE) = CAST(universe.market_date AS DATE)
            LEFT JOIN source_price_limit_execution_daily limits
              ON limits.security_id = universe.security_id
             AND CAST(limits.execution_date AS DATE) = CAST(universe.market_date AS DATE)
            LEFT JOIN mapped_actions actions
              ON actions.security_id = universe.security_id
             AND actions.effective_date = CAST(universe.market_date AS DATE)
            WHERE universe.market_date <= DATE {end}
        ),
        lagged AS (
            SELECT *,
                   lag(close_price) OVER security_order AS prior_close_price,
                   lag(bar_valid) OVER security_order AS prior_bar_valid
            FROM base
            WINDOW security_order AS (
                PARTITION BY security_id ORDER BY market_date
            )
        ),
        candidate_returns AS (
            SELECT *,
                   CASE
                       WHEN bar_valid AND prior_bar_valid AND NOT exception_active
                        AND prior_close_price > 0
                        AND close_price * (1 + bonus_share_ratio) + cash_dividend_per_share > 0
                       THEN (close_price * (1 + bonus_share_ratio)
                           + cash_dividend_per_share) / prior_close_price
                   END AS candidate_gross_return
            FROM lagged
        ),
        classified_returns AS (
            SELECT *,
                   raw_price_transition_unexplained
                       OR (
                           has_normalized_action
                           AND candidate_gross_return IS NOT NULL
                           AND upper_limit_pct IS NOT NULL
                           AND lower_limit_pct IS NOT NULL
                           AND (
                                  candidate_gross_return > 1 + upper_limit_pct / 100 + 0.005
                               OR candidate_gross_return < 1 - lower_limit_pct / 100 - 0.005
                           )
                       ) AS unexplained_price_transition
            FROM candidate_returns
        ),
        scope_classified_returns AS (
            SELECT *, bool_or(
                       (unexplained_price_transition OR exception_active)
                       AND market_date >= DATE {start}
                   ) OVER (PARTITION BY security_id) AS scope_continuity_excluded
            FROM classified_returns
        ),
        returns AS (
            SELECT *,
                   CASE
                       WHEN candidate_gross_return IS NOT NULL
                        AND NOT unexplained_price_transition
                       THEN ln(candidate_gross_return)
                   END AS daily_log_return
            FROM scope_classified_returns
        ),
        indexed AS (
            SELECT *,
                   100.0 * exp(
                       sum(coalesce(daily_log_return, 0.0)) OVER (
                           PARTITION BY security_id ORDER BY market_date
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                       )
                   ) AS total_return_index,
                   CASE
                       WHEN daily_log_return IS NOT NULL THEN exp(daily_log_return) - 1
                   END AS daily_total_return
            FROM returns
        ),
        rolling AS (
            SELECT *,
                   avg(total_return_index) OVER (
                       PARTITION BY security_id ORDER BY market_date
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                   ) AS sma20,
                   avg(total_return_index) OVER (
                       PARTITION BY security_id ORDER BY market_date
                       ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                   ) AS sma60,
                   total_return_index / lag(total_return_index, 20) OVER (
                       PARTITION BY security_id ORDER BY market_date
                   ) - 1 AS momentum20,
                   max(total_return_index) OVER (
                       PARTITION BY security_id ORDER BY market_date
                       ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                   ) AS prior_high20,
                   volume / nullif(avg(volume) OVER (
                       PARTITION BY security_id ORDER BY market_date
                       ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
                   ), 0) AS volume_ratio20,
                   avg(amount) OVER (
                       PARTITION BY security_id ORDER BY market_date
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                   ) AS average_amount20,
                   stddev_samp(daily_total_return) OVER (
                       PARTITION BY security_id ORDER BY market_date
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                   ) * sqrt(252.0) AS realized_volatility20,
                   avg(greatest(daily_total_return, 0.0)) OVER (
                       PARTITION BY security_id ORDER BY market_date
                       ROWS BETWEEN 13 PRECEDING AND CURRENT ROW
                   ) AS average_gain14,
                   avg(greatest(-daily_total_return, 0.0)) OVER (
                       PARTITION BY security_id ORDER BY market_date
                       ROWS BETWEEN 13 PRECEDING AND CURRENT ROW
                   ) AS average_loss14,
                   count(*) FILTER (WHERE bar_valid) OVER (
                       PARTITION BY security_id ORDER BY market_date
                       ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                   ) AS valid_bars60,
                   count(daily_log_return) OVER (
                       PARTITION BY security_id ORDER BY market_date
                       ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                   ) AS valid_returns60,
                   count(*) FILTER (WHERE exception_active) OVER (
                       PARTITION BY security_id ORDER BY market_date
                       ROWS BETWEEN 60 PRECEDING AND CURRENT ROW
                   ) AS exception_count61
            FROM indexed
        ),
        with_rsi AS (
            SELECT *,
                   CASE
                       WHEN average_gain14 + average_loss14 = 0 THEN 50.0
                       ELSE 100.0 * average_gain14 / (average_gain14 + average_loss14)
                   END AS rsi14
            FROM rolling
        ),
        final AS (
            SELECT *,
                   lag(rsi14) OVER (
                       PARTITION BY security_id ORDER BY market_date
                   ) AS rsi14_previous,
                   listing_sessions >= 61
                       AND valid_bars60 = 60
                       AND valid_returns60 = 60
                       AND exception_count61 = 0
                       AND sma20 IS NOT NULL AND sma60 IS NOT NULL
                       AND momentum20 IS NOT NULL AND prior_high20 IS NOT NULL
                       AND rsi14 IS NOT NULL AND volume_ratio20 IS NOT NULL
                       AND realized_volatility20 IS NOT NULL
                       AS feature_ready,
                   bar_valid AND NOT unexplained_price_transition
                       AND NOT scope_continuity_excluded
                       AND entry_policy_allows AND is_executable
                       AND NOT exception_active AS entry_eligible
            FROM with_rsi
        )
        SELECT market_date,
               security_id,
               close_price,
               total_return_index,
               sma20,
               sma60,
               momentum20,
               prior_high20,
               rsi14,
               rsi14_previous,
               volume_ratio20,
               realized_volatility20,
               daily_total_return,
               average_amount20,
               CAST(listing_sessions AS INTEGER) AS listing_sessions,
               feature_ready,
               entry_eligible,
               exception_active,
               unexplained_price_transition,
               scope_continuity_excluded
        FROM final
        WHERE market_date BETWEEN DATE {start} AND DATE {end}
        ORDER BY market_date, security_id
    """


class BaselineHistoricalStrategy:
    """One fixed-unit, full-entry/full-exit strategy over a shared feature panel."""

    def __init__(
        self,
        definition: BaselineStrategyDefinition,
        shared: BaselineSharedPolicy,
        panel: BaselineFeaturePanel,
    ) -> None:
        self.strategy_id = definition.strategy_id
        self.strategy_version = definition.strategy_version
        self.definition = definition
        self.shared = shared
        self.panel = panel
        self._candidates = panel.candidates(definition, shared)
        self._held_security: str | None = None
        self._holding_sessions = 0

    def _observe_position(self, state: UnitState) -> None:
        if state.security_code is None:
            self._held_security = None
            self._holding_sessions = 0
        elif state.security_code != self._held_security:
            self._held_security = state.security_code
            self._holding_sessions = 1
        else:
            self._holding_sessions += 1

    def _entry_quantity(self, cash: Decimal, close_price: Decimal) -> int:
        buffered_price = close_price * (Decimal("1") + self.shared.sizing_cash_buffer)
        lots = (cash / (buffered_price * self.shared.entry_lot_size)).to_integral_value(
            rounding=ROUND_FLOOR
        )
        return int(lots) * self.shared.entry_lot_size

    def _exit_reason(self, feature: BaselineFeatureRow | None) -> str | None:
        if self._holding_sessions >= self.definition.int_parameter("maximum_holding_sessions"):
            return "maximum_holding_sessions"
        if feature is None or not feature.feature_ready:
            return None
        if self.definition.kind == "ma_trend":
            assert feature.total_return_index is not None
            assert feature.sma20 is not None
            assert feature.sma60 is not None
            if feature.total_return_index < feature.sma20:
                return "trend_close_below_sma20"
            if feature.sma20 <= feature.sma60:
                return "trend_fast_sma_not_above_slow"
        elif self.definition.kind == "breakout20":
            assert feature.total_return_index is not None
            assert feature.sma20 is not None
            if feature.total_return_index < feature.sma20:
                return "breakout_close_below_sma20"
        else:
            assert feature.rsi14 is not None
            if feature.rsi14 >= self.definition.decimal_parameter("exit_rsi_floor"):
                return "rebound_rsi_target"
        return None

    def decide(self, context: StrategyReplayContext) -> ReplayDecision:
        self._observe_position(context.unit_state)
        if context.unit_state.security_code is not None:
            feature = self.panel.feature(
                context.snapshot.market_date,
                context.unit_state.security_code,
            )
            reason = self._exit_reason(feature)
            if reason is not None:
                return ReplayDecision(ReplayAction.EXIT, reason)
            return ReplayDecision(ReplayAction.NO_ACTION, "hold")

        candidate = self._candidates.get(context.snapshot.market_date)
        if candidate is None:
            return ReplayDecision(ReplayAction.NO_ACTION, "no_candidate")
        if context.market_view is None:
            raise BacktestInputError("baseline strategy requires a T-close market view")
        current_bar = context.market_view.bar(candidate.feature.security_id)
        if (
            not current_bar.data_valid
            or not current_bar.is_executable
            or not current_bar.entry_policy_allows
        ):
            raise BacktestInputError("feature candidate disagrees with the T-close market view")
        quantity = self._entry_quantity(context.unit_state.cash, candidate.feature.close_price)
        if quantity <= 0:
            return ReplayDecision(ReplayAction.NO_ACTION, "insufficient_cash_for_entry_lot")
        return ReplayDecision(
            ReplayAction.ENTER,
            f"{self.definition.kind}_entry",
            candidate.score,
            candidate.feature.security_id,
            quantity,
        )


@dataclass(frozen=True)
class BaselineSuiteResult:
    runner_version: str
    catalogue_version: str
    catalogue_digest: str
    feature_panel: BaselineFeaturePanelReport
    scope_start: date
    scope_end: date
    strategies: tuple[StrategyReplayResult, ...]

    @property
    def digest(self) -> str:
        return _digest(self.payload_dict())

    def payload_dict(self) -> dict[str, Any]:
        return {
            "runner_version": self.runner_version,
            "catalogue_version": self.catalogue_version,
            "catalogue_digest": self.catalogue_digest,
            "feature_panel": self.feature_panel.to_dict(),
            "scope": {"start": self.scope_start.isoformat(), "end": self.scope_end.isoformat()},
            "cash_baseline_total_return": "0",
            "strategies": [
                {
                    "strategy_id": result.strategy_id,
                    "strategy_version": result.strategy_version,
                    "run_id": result.backtest.run.run_id,
                    "result_digest": (
                        "sha256:"
                        + hashlib.sha256(result.canonical_bytes()).hexdigest()
                    ),
                    "performance": result.performance.to_dict(),
                    "decision_count": len(result.decisions),
                    "trade_count": len(result.trades),
                    "pending_exit": result.pending_exit,
                    "ending_position": result.final_state.security_code,
                }
                for result in self.strategies
            ],
            "research_only": True,
            "execution_authorized": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload_dict(), "digest": self.digest}


def run_baseline_strategy_suite(
    reader: HistoricalReplayFrameReader,
    engine: BacktestEngine,
    catalogue: BaselineStrategyCatalogue,
    registry: FeatureRegistry,
    panel: BaselineFeaturePanel,
    *,
    scope_start: date,
    scope_end: date,
) -> BaselineSuiteResult:
    """Run all three frozen baselines only inside the development partition."""

    catalogue.validation.require_development_scope(scope_start, scope_end)
    if catalogue.feature_registry_version != registry.registry_version:
        raise BaselineResearchError("catalogue and feature registry versions do not match")
    if panel.report.feature_registry_digest != registry.digest:
        raise BaselineResearchError("feature panel does not bind the loaded feature registry")
    if panel.report.bundle_digest != reader.bundle.digest:
        raise BaselineResearchError("feature panel does not bind the replay bundle")
    results: list[StrategyReplayResult] = []
    for definition in catalogue.strategies:
        run = BacktestRunSpec(
            code_version=f"baseline-runner-{BASELINE_RUNNER_VERSION}",
            parameter_hash=definition.digest,
            data_snapshot_id=reader.bundle.snapshot.snapshot_id,
            feature_version=(
                f"{registry.registry_version}|{registry.digest}|panel={panel.report.digest}"
            ),
            rule_version=definition.strategy_version,
            run_id=(
                f"{definition.strategy_id}.{definition.strategy_version}."
                f"{scope_start.strftime('%Y%m%d')}-{scope_end.strftime('%Y%m%d')}"
            ),
            execution_policy_version=engine.policy.policy_version,
            execution_profile_id=engine.policy.active_profile.profile_id,
            schedule_version=engine.policy.active_profile.schedule_version,
            official=False,
        )
        spec = reader.build_replay_spec(
            run=run,
            unit_id=f"unit-{definition.kind}",
            initial_cash=catalogue.shared.initial_cash,
            scope_start=scope_start,
            scope_end=scope_end,
        )
        if spec.frames[-1].execution_date > catalogue.validation.development_end:
            raise BaselineResearchError(
                "the final development decision would execute outside the development period"
            )
        if panel.report.scope_end < spec.frames[-1].execution_date:
            raise BaselineResearchError(
                "feature-panel continuity scope must include the final execution date"
            )
        results.append(
            run_strategy_replay(
                engine,
                BaselineHistoricalStrategy(definition, catalogue.shared, panel),
                spec,
            )
        )
    return BaselineSuiteResult(
        runner_version=BASELINE_RUNNER_VERSION,
        catalogue_version=catalogue.catalogue_version,
        catalogue_digest=catalogue.digest,
        feature_panel=panel.report,
        scope_start=scope_start,
        scope_end=scope_end,
        strategies=tuple(results),
    )
