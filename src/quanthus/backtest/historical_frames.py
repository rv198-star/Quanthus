"""Continuous point-in-time frame reader for immutable historical replay bundles."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, overload

from quanthus.backtest.contracts import (
    MARKET_TIMEZONE,
    BacktestRunSpec,
    CorporateAction,
    CorporateActionStep,
    MarketSession,
)
from quanthus.backtest.historical_bundle import (
    HistoricalReplayBundle,
    load_historical_replay_bundle,
)
from quanthus.backtest.inputs import (
    CoverageStatus,
    P1DailyExecutionEvidence,
    market_session_from_p1,
)
from quanthus.backtest.strategy_replay import (
    PITDailyBar,
    StrategyReplayFrame,
    StrategyReplaySpec,
)
from quanthus.contracts import SnapshotRef
from quanthus.corporate_action_normalization import CorporateActionNormalizationPolicy

FRAME_READER_VERSION = "1.1.0"
PRICE_TICK = Decimal("0.01")

_VIEW_DATASETS = {
    "replay_actions": "corporate_actions_normalized",
    "replay_action_exceptions": "corporate_action_exceptions",
    "replay_bars": "daily_ohlcv_none",
    "replay_universe": "daily_universe",
    "replay_limits": "price_limit_execution_daily",
    "replay_validity": "security_code_validity",
    "replay_calendar": "trading_calendar",
}

_REQUIRED_COLUMNS = {
    "replay_actions": {
        "action_id",
        "security_code",
        "effective_date",
        "known_at",
        "cash_dividend_per_share",
        "bonus_share_ratio",
        "source_revision",
        "source_snapshot_id",
        "evidence_digest",
        "execution_authorized",
    },
    "replay_action_exceptions": {
        "exception_id",
        "security_code",
        "blocked_from",
        "blocked_through",
        "reason_code",
        "replay_disposition",
        "execution_authorized",
    },
    "replay_bars": {
        "security_id",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    },
    "replay_universe": {"security_id", "exchange", "market_date"},
    "replay_limits": {
        "security_id",
        "exchange",
        "execution_date",
        "observed_tradestatus",
        "entry_policy_allows",
        "is_executable",
        "reason_code",
        "upper_limit_pct",
        "lower_limit_pct",
        "upper_limit_price",
        "lower_limit_price",
    },
    "replay_validity": {
        "security_id",
        "stock_code",
        "exchange",
        "valid_from",
        "valid_to",
    },
    "replay_calendar": {"exchange", "trade_date", "is_open"},
}


class HistoricalFrameReaderError(ValueError):
    """Raised when a verified bundle cannot produce safe continuous replay frames."""


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


def _read_parquet_expression(paths: Sequence[Path]) -> str:
    if not paths:
        raise HistoricalFrameReaderError("historical replay dataset has no parts")
    values = ",".join(_sql_string(str(path.resolve())) for path in paths)
    return f"read_parquet([{values}])"


def _decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        return None
    return parsed


def _market_time(market_date: date, value: time) -> datetime:
    return datetime.combine(market_date, value, tzinfo=MARKET_TIMEZONE)


def _data_valid(row: dict[str, Any]) -> bool:
    values = tuple(
        _decimal(row[name])
        for name in ("open_price", "high_price", "low_price", "close_price", "volume", "amount")
    )
    if any(value is None for value in values):
        return False
    open_price, high_price, low_price, close_price, volume, amount = values
    assert open_price is not None
    assert high_price is not None
    assert low_price is not None
    assert close_price is not None
    assert volume is not None
    assert amount is not None
    return (
        min(open_price, high_price, low_price, close_price, volume, amount) >= 0
        and low_price <= open_price <= high_price
        and low_price <= close_price <= high_price
    )


def _coverage(row: dict[str, Any]) -> CoverageStatus:
    if str(row.get("observed_tradestatus") or "") == "0":
        return CoverageStatus.SUSPENDED
    if row.get("bar_security_id") is None:
        return CoverageStatus.MISSING
    return CoverageStatus.TRADING


def _bar_from_row(row: dict[str, Any]) -> PITDailyBar:
    coverage = _coverage(row)
    valid = _data_valid(row)
    volume = _decimal(row.get("volume"))
    amount = _decimal(row.get("amount"))
    positive_prices = all(
        value is not None and value > 0
        for value in (
            _decimal(row.get("open_price")),
            _decimal(row.get("high_price")),
            _decimal(row.get("low_price")),
            _decimal(row.get("close_price")),
        )
    )
    liquid = volume is not None and amount is not None and volume > 0 and amount > 0
    block_reason_raw = row.get("corporate_action_block_reason")
    block_reason = None if block_reason_raw is None else str(block_reason_raw)
    executable = (
        coverage is CoverageStatus.TRADING
        and valid
        and positive_prices
        and liquid
        and bool(row.get("source_is_executable"))
        and block_reason is None
    )
    entry_allowed = bool(row.get("source_entry_policy_allows")) and block_reason is None
    if block_reason == "unexplained_price_transition":
        reason_code = block_reason
    elif block_reason is not None:
        reason_code = f"corporate_action_exception:{block_reason}"
    elif row.get("execution_security_id") is None:
        reason_code = "execution_evidence_missing"
    elif coverage is CoverageStatus.SUSPENDED:
        reason_code = "session_not_tradable"
    elif coverage is CoverageStatus.MISSING:
        reason_code = "bar_missing_without_suspension"
    elif not valid or not positive_prices:
        reason_code = "invalid_bar"
    elif not liquid:
        reason_code = "zero_liquidity"
    else:
        reason_code = str(row["source_reason_code"])
    return PITDailyBar(
        security_code=str(row["security_id"]),
        exchange=str(row["exchange"]),
        market_date=row["market_date"],
        coverage_status=coverage,
        data_valid=valid and positive_prices,
        is_executable=executable,
        entry_policy_allows=entry_allowed,
        reason_code=reason_code,
        open_price=_decimal(row.get("open_price")),
        high_price=_decimal(row.get("high_price")),
        low_price=_decimal(row.get("low_price")),
        close_price=_decimal(row.get("close_price")),
        volume=volume,
        amount=amount,
        upper_limit_price=_decimal(row.get("upper_limit_price")),
        lower_limit_price=_decimal(row.get("lower_limit_price")),
        corporate_action_block_reason=block_reason,
    )


@dataclass(frozen=True)
class HistoricalFrameAuditReport:
    bundle_snapshot_id: str
    bundle_digest: str
    reader_version: str
    scope_start: date
    scope_end: date
    first_decision_date: date
    last_decision_date: date
    first_execution_date: date
    last_execution_date: date
    frame_count: int
    decision_observation_count: int
    execution_evidence_count: int
    missing_execution_evidence_count: int
    security_count: int
    suspended_evidence_count: int
    missing_bar_count: int
    invalid_bar_count: int
    non_executable_count: int
    corporate_action_count: int
    corporate_action_blocked_pair_count: int
    unexplained_price_transition_count: int

    @property
    def ready_for_strategy_replay(self) -> bool:
        return self.frame_count > 0 and self.execution_evidence_count > 0

    @property
    def execution_authorized(self) -> bool:
        return False

    def payload_dict(self) -> dict[str, Any]:
        return {
            "bundle_snapshot_id": self.bundle_snapshot_id,
            "bundle_digest": self.bundle_digest,
            "reader_version": self.reader_version,
            "scope": {
                "start": self.scope_start.isoformat(),
                "end": self.scope_end.isoformat(),
            },
            "first_decision_date": self.first_decision_date.isoformat(),
            "last_decision_date": self.last_decision_date.isoformat(),
            "first_execution_date": self.first_execution_date.isoformat(),
            "last_execution_date": self.last_execution_date.isoformat(),
            "counts": {
                "frames": self.frame_count,
                "decision_observations": self.decision_observation_count,
                "execution_evidence": self.execution_evidence_count,
                "missing_execution_evidence": self.missing_execution_evidence_count,
                "securities": self.security_count,
                "suspended_evidence": self.suspended_evidence_count,
                "missing_bars": self.missing_bar_count,
                "invalid_bars": self.invalid_bar_count,
                "non_executable": self.non_executable_count,
                "corporate_actions": self.corporate_action_count,
                "corporate_action_blocked_pairs": self.corporate_action_blocked_pair_count,
                "unexplained_price_transitions": self.unexplained_price_transition_count,
            },
            "ready_for_strategy_replay": self.ready_for_strategy_replay,
            "execution_authorized": self.execution_authorized,
        }

    @property
    def digest(self) -> str:
        return _digest(self.payload_dict())

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload_dict(), "digest": self.digest}


class PITBundleMarketView:
    """Lazy decision-date view whose public queries are bounded at one T close."""

    def __init__(self, reader: HistoricalReplayFrameReader, snapshot: SnapshotRef) -> None:
        self._reader = reader
        self._snapshot = snapshot
        self._runner_only_access_count = reader.runner_only_access_count
        self._bars: tuple[PITDailyBar, ...] | None = None
        self._bar_index: dict[str, PITDailyBar] = {}
        self._history: dict[tuple[str, int], tuple[PITDailyBar, ...]] = {}

    @property
    def snapshot(self) -> SnapshotRef:
        return self._snapshot

    def current_bars(self) -> tuple[PITDailyBar, ...]:
        if self._bars is None:
            self._bars = self._reader._bars_for_date(self.snapshot.market_date)
            self._bar_index = {item.security_code: item for item in self._bars}
        return self._bars

    def securities(self) -> tuple[str, ...]:
        return tuple(item.security_code for item in self.current_bars())

    def bar(self, security_code: str) -> PITDailyBar:
        if not security_code.strip():
            raise HistoricalFrameReaderError("security_code must not be empty")
        if self._bars is not None:
            try:
                return self._bar_index[security_code]
            except KeyError as exc:
                raise KeyError(
                    f"security is outside the decision universe: {security_code}"
                ) from exc
        rows = self._reader._bars_for_date(self.snapshot.market_date, security_code)
        if len(rows) != 1:
            raise KeyError(f"security is outside the decision universe: {security_code}")
        self._bar_index[security_code] = rows[0]
        return rows[0]

    def history(self, security_code: str, sessions: int) -> tuple[PITDailyBar, ...]:
        if not security_code.strip():
            raise HistoricalFrameReaderError("security_code must not be empty")
        if not isinstance(sessions, int) or isinstance(sessions, bool) or sessions <= 0:
            raise HistoricalFrameReaderError("history sessions must be a positive integer")
        key = (security_code, sessions)
        if key not in self._history:
            self._history[key] = self._reader._history(
                security_code,
                self.snapshot.market_date,
                sessions,
            )
        return self._history[key]

    def assert_decision_capability(self) -> None:
        """Fail when strategy code reached execution-only reader methods during decide()."""

        if self._reader.runner_only_access_count != self._runner_only_access_count:
            raise HistoricalFrameReaderError(
                "strategy decision attempted to access runner-only replay evidence"
            )


class _HistoricalFrameSource:
    def __init__(self, reader: HistoricalReplayFrameReader, execution_date: date) -> None:
        self._reader = reader
        self._execution_date = execution_date
        self._sessions: dict[str, MarketSession] = {}
        self._blocks: dict[str, str | None] = {}

    def session(self, security_code: str) -> MarketSession:
        if security_code not in self._sessions:
            self._sessions[security_code] = self._reader._session(
                security_code,
                self._execution_date,
            )
        return self._sessions[security_code]

    def block_reason(self, security_code: str) -> str | None:
        if security_code not in self._blocks:
            self._blocks[security_code] = self._reader._block_reason(
                security_code,
                self._execution_date,
            )
        return self._blocks[security_code]

    def corporate_actions(
        self,
        security_code: str,
        prior_mark_price: Decimal,
    ) -> tuple[CorporateActionStep, ...]:
        return self._reader._corporate_actions(
            security_code,
            self._execution_date,
            prior_mark_price,
        )


class HistoricalReplayFrameSequence(Sequence[StrategyReplayFrame]):
    """Immutable lazy sequence of consecutive decision-to-execution frames."""

    def __init__(
        self,
        reader: HistoricalReplayFrameReader,
        pairs: tuple[tuple[date, date], ...],
    ) -> None:
        self._reader = reader
        self._pairs = pairs

    def __len__(self) -> int:
        return len(self._pairs)

    @overload
    def __getitem__(self, index: int) -> StrategyReplayFrame: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[StrategyReplayFrame, ...]: ...

    def __getitem__(
        self,
        index: int | slice,
    ) -> StrategyReplayFrame | tuple[StrategyReplayFrame, ...]:
        if isinstance(index, slice):
            return tuple(self._reader._frame(*pair) for pair in self._pairs[index])
        return self._reader._frame(*self._pairs[index])

    def __iter__(self) -> Iterator[StrategyReplayFrame]:
        return (self._reader._frame(*pair) for pair in self._pairs)


class HistoricalReplayFrameReader:
    """Read verified Parquet data without exposing T+1 facts to strategy code."""

    def __init__(self, bundle: HistoricalReplayBundle) -> None:
        self.bundle = bundle
        try:
            import duckdb
        except ModuleNotFoundError as exc:
            raise HistoricalFrameReaderError(
                "P1 dependencies are missing; run `make bootstrap-p1`"
            ) from exc
        self._connection: Any | None = duckdb.connect(":memory:")
        try:
            self._create_views()
            self._validate_sources()
        except BaseException:
            self.close()
            raise

    @property
    def data_content_digest(self) -> str:
        return self.bundle.digest

    @property
    def data_manifest_sha256(self) -> str | None:
        return None

    @property
    def runner_only_access_count(self) -> int:
        return 0

    def __enter__(self) -> HistoricalReplayFrameReader:
        self._ensure_open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _ensure_open(self) -> Any:
        if self._connection is None:
            raise HistoricalFrameReaderError("historical frame reader is closed")
        return self._connection

    def _rows(self, query: str, parameters: Sequence[object] = ()) -> list[dict[str, Any]]:
        cursor = self._ensure_open().execute(query, list(parameters))
        names = [str(item[0]) for item in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def _scalar(self, query: str, parameters: Sequence[object] = ()) -> Any:
        row = self._ensure_open().execute(query, list(parameters)).fetchone()
        if row is None:
            raise HistoricalFrameReaderError("historical frame query returned no result")
        return row[0]

    def _create_views(self) -> None:
        connection = self._ensure_open()
        for view, dataset in _VIEW_DATASETS.items():
            paths = self.bundle.partition_paths(dataset)
            connection.execute(
                f"CREATE VIEW {view} AS SELECT * FROM {_read_parquet_expression(paths)}"
            )
        connection.execute(
            """
            CREATE VIEW replay_identity_code_map AS
            SELECT stock_code, min(security_id) AS security_id
            FROM replay_validity
            GROUP BY stock_code
            HAVING count(DISTINCT security_id) = 1
            """
        )
        for source, mapped in (
            ("replay_actions", "replay_actions_mapped"),
            ("replay_action_exceptions", "replay_action_exceptions_mapped"),
        ):
            columns = {
                str(row[0]) for row in connection.execute(f"DESCRIBE {source}").fetchall()
            }
            if "security_id" in columns:
                connection.execute(f"CREATE VIEW {mapped} AS SELECT * FROM {source}")
            else:
                connection.execute(
                    f"""
                    CREATE VIEW {mapped} AS
                    SELECT source.*, identity_map.security_id
                    FROM {source} source
                    JOIN replay_identity_code_map identity_map
                      ON identity_map.stock_code = source.security_code
                    """
                )
        connection.execute(
            """
            CREATE TEMP TABLE replay_unexplained_price_transitions AS
            WITH bars_with_prior AS (
                SELECT *, lag(close) OVER (
                    PARTITION BY security_id ORDER BY date
                ) AS prior_close
                FROM replay_bars
            )
            SELECT DISTINCT universe.security_id,
                   universe.market_date,
                   'unexplained_price_transition' AS reason_code
            FROM replay_universe universe
            JOIN bars_with_prior bars
              ON bars.security_id = universe.security_id
             AND bars.date = universe.market_date
            JOIN replay_limits limits
              ON limits.security_id = universe.security_id
             AND limits.execution_date = universe.market_date
            LEFT JOIN replay_actions_mapped actions
              ON actions.security_id = universe.security_id
             AND actions.effective_date = universe.market_date
            WHERE (
                actions.security_id IS NULL
                AND limits.lower_limit_price IS NOT NULL
                AND limits.upper_limit_price IS NOT NULL
                AND (
                       bars.close < CAST(limits.lower_limit_price AS DOUBLE) - 0.011
                    OR bars.close > CAST(limits.upper_limit_price AS DOUBLE) + 0.011
                )
            ) OR (
                actions.security_id IS NOT NULL
                AND bars.prior_close > 0
                AND limits.upper_limit_pct IS NOT NULL
                AND limits.lower_limit_pct IS NOT NULL
                AND (
                    (
                        bars.close * (1 + CAST(actions.bonus_share_ratio AS DOUBLE))
                        + CAST(actions.cash_dividend_per_share AS DOUBLE)
                    ) / bars.prior_close
                        > 1 + CAST(limits.upper_limit_pct AS DOUBLE) / 100 + 0.005
                    OR (
                        bars.close * (1 + CAST(actions.bonus_share_ratio AS DOUBLE))
                        + CAST(actions.cash_dividend_per_share AS DOUBLE)
                    ) / bars.prior_close
                        < 1 - CAST(limits.lower_limit_pct AS DOUBLE) / 100 - 0.005
                )
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX replay_unexplained_transition_key ON "
            "replay_unexplained_price_transitions (security_id, market_date)"
        )

    def _validate_sources(self) -> None:
        connection = self._ensure_open()
        for view, required in _REQUIRED_COLUMNS.items():
            available = {str(row[0]) for row in connection.execute(f"DESCRIBE {view}").fetchall()}
            missing = sorted(required - available)
            if missing:
                raise HistoricalFrameReaderError(
                    f"{view} is missing required columns: {', '.join(missing)}"
                )
        for view in ("replay_actions", "replay_action_exceptions"):
            available = {
                str(row[0]) for row in connection.execute(f"DESCRIBE {view}").fetchall()
            }
            if "security_id" in available:
                join = """
                    LEFT JOIN (
                        SELECT DISTINCT security_id
                        FROM replay_validity
                    ) identity_map
                      ON identity_map.security_id = source.security_id
                    WHERE identity_map.security_id IS NULL
                """
            else:
                join = """
                    LEFT JOIN (
                        SELECT stock_code, count(DISTINCT security_id) AS identity_count
                        FROM replay_validity
                        GROUP BY stock_code
                    ) identity_map
                      ON identity_map.stock_code = source.security_code
                    WHERE coalesce(identity_map.identity_count, 0) <> 1
                """
            unmapped_or_ambiguous = int(
                self._scalar(
                    f"""
                    SELECT count(*)
                    FROM {view} source
                    {join}
                    """
                )
            )
            if unmapped_or_ambiguous:
                raise HistoricalFrameReaderError(
                    f"{view} contains unmapped or ambiguous security identities"
                )
        duplicate_universe_pairs = int(
            self._scalar(
                """
                SELECT count(*) FROM (
                    SELECT security_id, market_date
                    FROM replay_universe
                    GROUP BY security_id, market_date
                    HAVING count(*) <> 1
                )
                """
            )
        )
        duplicate_execution_pairs = int(
            self._scalar(
                """
                SELECT count(*) FROM (
                    SELECT security_id, execution_date
                    FROM replay_limits
                    GROUP BY security_id, execution_date
                    HAVING count(*) <> 1
                )
                """
            )
        )
        execution_outside_universe = int(
            self._scalar(
                """
                SELECT count(*)
                FROM replay_limits limits
                LEFT JOIN replay_universe universe
                  ON universe.security_id = limits.security_id
                 AND universe.market_date = limits.execution_date
                WHERE universe.security_id IS NULL
                """
            )
        )
        duplicate_bar_pairs = int(
            self._scalar(
                """
                SELECT count(*) FROM (
                    SELECT security_id, date
                    FROM replay_bars
                    GROUP BY security_id, date
                    HAVING count(*) <> 1
                )
                """
            )
        )
        exchange_mismatches = int(
            self._scalar(
                """
                SELECT count(*)
                FROM replay_limits limits
                JOIN replay_universe universe
                  ON universe.security_id = limits.security_id
                 AND universe.market_date = limits.execution_date
                WHERE universe.exchange <> limits.exchange
                """
            )
        )
        if (
            duplicate_universe_pairs
            or duplicate_execution_pairs
            or duplicate_bar_pairs
            or execution_outside_universe
            or exchange_mismatches
        ):
            raise HistoricalFrameReaderError(
                "daily universe, bars, and execution evidence violate identity bounds"
            )
        invalid_actions = int(
            self._scalar(
                """
                SELECT count(*)
                FROM replay_actions
                WHERE action_id IS NULL
                   OR security_code IS NULL
                   OR effective_date IS NULL
                   OR known_at IS NULL
                   OR cash_dividend_per_share IS NULL
                   OR bonus_share_ratio IS NULL
                   OR source_revision IS NULL
                   OR source_snapshot_id IS NULL
                   OR evidence_digest IS NULL
                   OR execution_authorized IS NULL
                   OR execution_authorized
                   OR CAST(known_at AS DATE) >= effective_date
                   OR cash_dividend_per_share < 0
                   OR bonus_share_ratio < 0
                   OR (cash_dividend_per_share = 0 AND bonus_share_ratio = 0)
                """
            )
        )
        duplicate_actions = int(
            self._scalar(
                """
                SELECT count(*) FROM (
                    SELECT action_id
                    FROM replay_actions
                    GROUP BY action_id
                    HAVING count(*) <> 1
                )
                """
            )
        )
        duplicate_action_dates = int(
            self._scalar(
                """
                SELECT count(*) FROM (
                    SELECT security_id, effective_date
                    FROM replay_actions_mapped
                    GROUP BY security_id, effective_date
                    HAVING count(*) <> 1
                )
                """
            )
        )
        if invalid_actions or duplicate_actions or duplicate_action_dates:
            raise HistoricalFrameReaderError("normalized corporate actions violate replay bounds")
        invalid_exceptions = int(
            self._scalar(
                """
                SELECT count(*)
                FROM replay_action_exceptions
                WHERE exception_id IS NULL
                   OR security_code IS NULL
                   OR blocked_from IS NULL
                   OR reason_code IS NULL
                   OR replay_disposition IS NULL
                   OR execution_authorized IS NULL
                   OR execution_authorized
                   OR replay_disposition <> 'security_fail_closed'
                   OR (blocked_through IS NOT NULL AND blocked_through < blocked_from)
                """
            )
        )
        duplicate_exceptions = int(
            self._scalar(
                """
                SELECT count(*) FROM (
                    SELECT exception_id
                    FROM replay_action_exceptions
                    GROUP BY exception_id
                    HAVING count(*) <> 1
                )
                """
            )
        )
        if invalid_exceptions or duplicate_exceptions:
            raise HistoricalFrameReaderError("corporate-action exceptions violate replay bounds")

    def _snapshot(self, market_date: date, *, role: str) -> SnapshotRef:
        source_revision = (
            f"{self.bundle.snapshot.source_revision}|frame-reader={FRAME_READER_VERSION}"
            f"|bundle={self.bundle.digest}"
        )
        return SnapshotRef(
            market_date=market_date,
            knowledge_cutoff=_market_time(market_date, time(15, 0)),
            source_revision=source_revision,
            snapshot_id=(
                f"{self.bundle.snapshot.snapshot_id}.{role}.{market_date.strftime('%Y%m%d')}"
            ),
        )

    def _date_pairs(self, scope_start: date, scope_end: date) -> tuple[tuple[date, date], ...]:
        if scope_start > scope_end:
            raise HistoricalFrameReaderError("scope_start must not follow scope_end")
        decision_rows = self._rows(
            """
            SELECT trade_date
            FROM replay_calendar
            WHERE exchange IN ('SSE', 'SZSE')
              AND is_open = 1
              AND trade_date BETWEEN ? AND ?
            GROUP BY trade_date
            HAVING count(*) = 2 AND count(DISTINCT exchange) = 2
            ORDER BY trade_date
            """,
            (scope_start, scope_end),
        )
        decisions = tuple(row["trade_date"] for row in decision_rows)
        if not decisions:
            raise HistoricalFrameReaderError("scope contains no complete SSE/SZSE decision session")
        next_row = self._rows(
            """
            SELECT trade_date
            FROM replay_calendar
            WHERE exchange IN ('SSE', 'SZSE')
              AND is_open = 1
              AND trade_date > ?
            GROUP BY trade_date
            HAVING count(*) = 2 AND count(DISTINCT exchange) = 2
            ORDER BY trade_date
            LIMIT 1
            """,
            (decisions[-1],),
        )
        if len(next_row) != 1:
            raise HistoricalFrameReaderError(
                "scope has no following complete execution session in the bundle"
            )
        execution_dates = decisions[1:] + (next_row[0]["trade_date"],)
        pairs = tuple(zip(decisions, execution_dates, strict=True))
        calendar_rows = self._rows(
            """
            SELECT trade_date, count(*) AS row_count,
                   count(DISTINCT exchange) AS exchange_count,
                   min(is_open) AS minimum_open, max(is_open) AS maximum_open
            FROM replay_calendar
            WHERE exchange IN ('SSE', 'SZSE')
              AND trade_date BETWEEN ? AND ?
            GROUP BY trade_date
            ORDER BY trade_date
            """,
            (decisions[0], execution_dates[-1]),
        )
        if any(
            int(row["row_count"]) != 2
            or int(row["exchange_count"]) != 2
            or int(row["minimum_open"]) != int(row["maximum_open"])
            for row in calendar_rows
        ):
            raise HistoricalFrameReaderError(
                "SSE/SZSE calendar evidence is incomplete or divergent"
            )
        required_dates = set(decisions) | set(execution_dates)
        universe_dates = {
            row["market_date"]
            for row in self._rows(
                """
                SELECT DISTINCT market_date
                FROM replay_universe
                WHERE market_date BETWEEN ? AND ?
                """,
                (decisions[0], execution_dates[-1]),
            )
        }
        missing_universe_dates = sorted(required_dates - universe_dates)
        if missing_universe_dates:
            raise HistoricalFrameReaderError(
                "daily universe has no rows for required sessions: "
                + ", ".join(item.isoformat() for item in missing_universe_dates)
            )
        return pairs

    def frames(
        self,
        scope_start: date,
        scope_end: date,
    ) -> HistoricalReplayFrameSequence:
        self._ensure_open()
        return HistoricalReplayFrameSequence(self, self._date_pairs(scope_start, scope_end))

    def build_replay_spec(
        self,
        *,
        run: BacktestRunSpec,
        unit_id: str,
        initial_cash: Decimal,
        scope_start: date,
        scope_end: date,
    ) -> StrategyReplaySpec:
        if run.data_snapshot_id != self.bundle.snapshot.snapshot_id:
            raise HistoricalFrameReaderError(
                "run data_snapshot_id must equal the immutable historical bundle snapshot id"
            )
        return StrategyReplaySpec(
            run=run,
            unit_id=unit_id,
            initial_cash=initial_cash,
            frames=self.frames(scope_start, scope_end),
        )

    def _frame(self, decision_date: date, execution_date: date) -> StrategyReplayFrame:
        decision_snapshot = self._snapshot(decision_date, role="decision")
        return StrategyReplayFrame(
            snapshot=decision_snapshot,
            decision_at=decision_snapshot.knowledge_cutoff,
            execution_date=execution_date,
            execution_sessions=(),
            decision_view=PITBundleMarketView(self, decision_snapshot),
            source=_HistoricalFrameSource(self, execution_date),
        )

    def _bar_rows(
        self,
        market_date: date,
        security_code: str | None = None,
        *,
        before_or_at: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        comparison = "<=" if before_or_at else "="
        security_filter = "" if security_code is None else "AND universe.security_id = ?"
        limit_clause = "" if limit is None else "LIMIT ?"
        parameters: list[object] = [market_date]
        if security_code is not None:
            parameters.append(security_code)
        if limit is not None:
            parameters.append(limit)
        return self._rows(
            f"""
            SELECT universe.security_id,
                   universe.exchange,
                   universe.market_date,
                   limits.security_id AS execution_security_id,
                   bars.security_id AS bar_security_id,
                   bars.open AS open_price,
                   bars.high AS high_price,
                   bars.low AS low_price,
                   bars.close AS close_price,
                   bars.volume,
                   bars.amount,
                   limits.observed_tradestatus,
                   limits.entry_policy_allows AS source_entry_policy_allows,
                   limits.is_executable AS source_is_executable,
                   limits.reason_code AS source_reason_code,
                   limits.upper_limit_price,
                   limits.lower_limit_price,
                   (
                       SELECT string_agg(reason_code, ',' ORDER BY reason_code)
                       FROM (
                           SELECT reason_code
                           FROM replay_action_exceptions_mapped exceptions
                           WHERE exceptions.security_id = universe.security_id
                             AND exceptions.blocked_from <= universe.market_date
                             AND (
                                    exceptions.blocked_through IS NULL
                                 OR exceptions.blocked_through >= universe.market_date
                             )
                           UNION ALL
                           SELECT reason_code
                           FROM replay_unexplained_price_transitions transitions
                           WHERE transitions.security_id = universe.security_id
                             AND transitions.market_date = universe.market_date
                       ) blocks
                   ) AS corporate_action_block_reason
            FROM replay_universe universe
            LEFT JOIN replay_limits limits
              ON limits.security_id = universe.security_id
             AND limits.execution_date = universe.market_date
            LEFT JOIN replay_bars bars
              ON bars.security_id = universe.security_id
             AND bars.date = universe.market_date
            WHERE universe.market_date {comparison} ?
              {security_filter}
            ORDER BY universe.market_date DESC, universe.security_id
            {limit_clause}
            """,
            parameters,
        )

    def _bars_for_date(
        self,
        market_date: date,
        security_code: str | None = None,
    ) -> tuple[PITDailyBar, ...]:
        rows = self._bar_rows(market_date, security_code)
        bars = tuple(_bar_from_row(row) for row in rows)
        return tuple(sorted(bars, key=lambda item: item.security_code))

    def _history(
        self,
        security_code: str,
        market_date: date,
        sessions: int,
    ) -> tuple[PITDailyBar, ...]:
        rows = self._bar_rows(
            market_date,
            security_code,
            before_or_at=True,
            limit=sessions,
        )
        bars = tuple(_bar_from_row(row) for row in reversed(rows))
        if any(item.market_date > market_date for item in bars):
            raise HistoricalFrameReaderError("decision history exposed future evidence")
        return bars

    def _block_reason(self, security_code: str, market_date: date) -> str | None:
        rows = self._rows(
            """
            SELECT string_agg(reason_code, ',' ORDER BY reason_code) AS reason_codes
            FROM (
                SELECT reason_code
                FROM replay_action_exceptions_mapped
                WHERE security_id = ?
                  AND blocked_from <= ?
                  AND (blocked_through IS NULL OR blocked_through >= ?)
                UNION ALL
                SELECT reason_code
                FROM replay_unexplained_price_transitions
                WHERE security_id = ? AND market_date = ?
            ) blocks
            """,
            (security_code, market_date, market_date, security_code, market_date),
        )
        value = rows[0]["reason_codes"]
        return None if value is None else str(value)

    def _missing_coverage(self, security_code: str, market_date: date) -> CoverageStatus:
        rows = self._rows(
            """
            SELECT min(CAST(valid_from AS DATE)) AS valid_from,
                   max(CAST(valid_to AS DATE)) AS valid_to,
                   count(*) AS interval_count
            FROM replay_validity
            WHERE security_id = ?
            """,
            (security_code,),
        )
        row = rows[0]
        if int(row["interval_count"]) == 0:
            return CoverageStatus.MISSING
        if row["valid_from"] is not None and market_date < row["valid_from"]:
            return CoverageStatus.NOT_LISTED
        if row["valid_to"] is not None and market_date > row["valid_to"]:
            return CoverageStatus.DELISTED
        return CoverageStatus.MISSING

    def _session(self, security_code: str, execution_date: date) -> MarketSession:
        bars = self._bars_for_date(execution_date, security_code)
        snapshot = self._snapshot(execution_date, role="execution")
        if not bars:
            coverage = self._missing_coverage(security_code, execution_date)
            evidence = P1DailyExecutionEvidence(
                snapshot=snapshot,
                security_code=security_code,
                open_at=_market_time(execution_date, time(9, 30)),
                close_at=_market_time(execution_date, time(15, 0)),
                coverage_status=coverage,
                data_valid=False,
                is_executable=False,
                entry_policy_allows=False,
                reason_code=f"{coverage.value}_or_out_of_scope",
                open_price=None,
                high_price=None,
                low_price=None,
                close_price=None,
                upper_limit_price=None,
                lower_limit_price=None,
            )
            return market_session_from_p1(evidence)
        bar = bars[0]
        keep_prices = bar.coverage_status is CoverageStatus.TRADING and bar.data_valid
        evidence = P1DailyExecutionEvidence(
            snapshot=snapshot,
            security_code=security_code,
            open_at=_market_time(execution_date, time(9, 30)),
            close_at=_market_time(execution_date, time(15, 0)),
            coverage_status=bar.coverage_status,
            data_valid=bar.data_valid,
            is_executable=bar.is_executable,
            entry_policy_allows=bar.entry_policy_allows,
            reason_code=bar.reason_code,
            open_price=bar.open_price if keep_prices else None,
            high_price=bar.high_price if keep_prices else None,
            low_price=bar.low_price if keep_prices else None,
            close_price=bar.close_price if keep_prices else None,
            upper_limit_price=bar.upper_limit_price,
            lower_limit_price=bar.lower_limit_price,
        )
        return market_session_from_p1(evidence)

    def _corporate_actions(
        self,
        security_code: str,
        execution_date: date,
        prior_mark_price: Decimal,
    ) -> tuple[CorporateActionStep, ...]:
        block_reason = self._block_reason(security_code, execution_date)
        if block_reason is not None:
            raise HistoricalFrameReaderError(
                "held security replay evidence is blocked: " + block_reason
            )
        rows = self._rows(
            """
            SELECT action_id, known_at, cash_dividend_per_share, bonus_share_ratio,
                   source_revision, source_snapshot_id, evidence_digest
            FROM replay_actions_mapped
            WHERE security_id = ? AND effective_date = ?
            ORDER BY action_id
            """,
            (security_code, execution_date),
        )
        mark = prior_mark_price
        steps: list[CorporateActionStep] = []
        applied_at = _market_time(execution_date, time(9, 0))
        action_snapshot = self._snapshot(execution_date, role="execution")
        for row in rows:
            cash = Decimal(str(row["cash_dividend_per_share"]))
            bonus = Decimal(str(row["bonus_share_ratio"]))
            denominator = Decimal("1") + bonus
            theoretical_mark = (mark - cash) / denominator
            post_action_mark = theoretical_mark.quantize(PRICE_TICK, rounding=ROUND_HALF_UP)
            if post_action_mark <= 0:
                raise HistoricalFrameReaderError(
                    f"corporate action produces a non-positive mark: {row['action_id']}"
                )
            known_at = row["known_at"]
            if known_at > applied_at:
                raise HistoricalFrameReaderError(
                    f"corporate action is not PIT-visible before execution: {row['action_id']}"
                )
            action = CorporateAction(
                action_id=str(row["action_id"]),
                security_code=security_code,
                effective_date=execution_date,
                known_at=known_at,
                cash_dividend_per_share=cash,
                bonus_share_ratio=bonus,
                source_revision=(
                    f"{row['source_revision']}|evidence={row['evidence_digest']}"
                ),
                snapshot_id=str(row["source_snapshot_id"]),
            )
            steps.append(
                CorporateActionStep(
                    action=action,
                    applied_at=applied_at,
                    post_action_mark_price=post_action_mark,
                    mark_snapshot_id=(
                        f"{action_snapshot.snapshot_id}.action.{row['action_id']}"
                    ),
                )
            )
            mark = post_action_mark
        return tuple(steps)

    def audit(self, scope_start: date, scope_end: date) -> HistoricalFrameAuditReport:
        pairs = self._date_pairs(scope_start, scope_end)
        first_decision = pairs[0][0]
        last_decision = pairs[-1][0]
        first_execution = pairs[0][1]
        last_execution = pairs[-1][1]
        counts = self._rows(
            """
            WITH scoped AS (
                SELECT universe.security_id,
                       universe.market_date,
                       limits.security_id AS execution_security_id,
                       limits.observed_tradestatus,
                       limits.is_executable,
                       EXISTS (
                           SELECT 1
                           FROM replay_action_exceptions_mapped exceptions
                           WHERE exceptions.security_id = universe.security_id
                             AND exceptions.blocked_from <= universe.market_date
                             AND (
                                    exceptions.blocked_through IS NULL
                                 OR exceptions.blocked_through >= universe.market_date
                             )
                       ) OR EXISTS (
                           SELECT 1
                           FROM replay_unexplained_price_transitions transitions
                           WHERE transitions.security_id = universe.security_id
                             AND transitions.market_date = universe.market_date
                       ) AS corporate_action_blocked,
                       bars.security_id AS bar_security_id,
                       bars.open, bars.high, bars.low, bars.close, bars.volume, bars.amount
                FROM replay_universe universe
                LEFT JOIN replay_limits limits
                  ON limits.security_id = universe.security_id
                 AND limits.execution_date = universe.market_date
                LEFT JOIN replay_bars bars
                  ON bars.security_id = universe.security_id
                 AND bars.date = universe.market_date
                WHERE universe.market_date BETWEEN ? AND ?
            )
            SELECT count(execution_security_id) AS evidence_count,
                   count(*) FILTER (
                       WHERE execution_security_id IS NULL
                   ) AS missing_execution_evidence_count,
                   count(DISTINCT security_id) AS security_count,
                   count(*) FILTER (WHERE observed_tradestatus = '0') AS suspended_count,
                   count(*) FILTER (WHERE bar_security_id IS NULL) AS missing_bar_count,
                   count(*) FILTER (
                       WHERE bar_security_id IS NOT NULL AND (
                           open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
                           OR volume IS NULL OR amount IS NULL
                           OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
                           OR volume < 0 OR amount < 0
                           OR low > high OR open < low OR open > high
                           OR close < low OR close > high
                       )
                   ) AS invalid_bar_count,
                   count(*) FILTER (
                       WHERE NOT coalesce(is_executable, false)
                          OR corporate_action_blocked
                   ) AS non_executable_count
            FROM scoped
            """,
            (first_execution, last_execution),
        )[0]
        decision_observations = int(
            self._scalar(
                """
                SELECT count(*) FROM replay_universe
                WHERE market_date BETWEEN ? AND ?
                """,
                (first_decision, last_decision),
            )
        )
        action_count = int(
            self._scalar(
                """
                SELECT count(*) FROM replay_actions_mapped
                WHERE effective_date BETWEEN ? AND ?
                """,
                (first_execution, last_execution),
            )
        )
        blocked_pairs = int(
            self._scalar(
                """
                SELECT count(DISTINCT (universe.security_id, universe.market_date))
                FROM replay_universe universe
                JOIN replay_action_exceptions_mapped exceptions
                  ON exceptions.security_id = universe.security_id
                 AND exceptions.blocked_from <= universe.market_date
                 AND (
                        exceptions.blocked_through IS NULL
                     OR exceptions.blocked_through >= universe.market_date
                 )
                WHERE universe.market_date BETWEEN ? AND ?
                """,
                (first_execution, last_execution),
            )
        )
        unexplained_transitions = int(
            self._scalar(
                """
                SELECT count(*)
                FROM replay_unexplained_price_transitions
                WHERE market_date BETWEEN ? AND ?
                """,
                (first_execution, last_execution),
            )
        )
        report = HistoricalFrameAuditReport(
            bundle_snapshot_id=self.bundle.snapshot.snapshot_id,
            bundle_digest=self.bundle.digest,
            reader_version=FRAME_READER_VERSION,
            scope_start=scope_start,
            scope_end=scope_end,
            first_decision_date=first_decision,
            last_decision_date=last_decision,
            first_execution_date=first_execution,
            last_execution_date=last_execution,
            frame_count=len(pairs),
            decision_observation_count=decision_observations,
            execution_evidence_count=int(counts["evidence_count"]),
            missing_execution_evidence_count=int(
                counts["missing_execution_evidence_count"]
            ),
            security_count=int(counts["security_count"]),
            suspended_evidence_count=int(counts["suspended_count"]),
            missing_bar_count=int(counts["missing_bar_count"]),
            invalid_bar_count=int(counts["invalid_bar_count"]),
            non_executable_count=int(counts["non_executable_count"]),
            corporate_action_count=action_count,
            corporate_action_blocked_pair_count=blocked_pairs,
            unexplained_price_transition_count=unexplained_transitions,
        )
        if not report.ready_for_strategy_replay:
            raise HistoricalFrameReaderError("historical frame audit produced no replayable input")
        first_frame = self._frame(*pairs[0])
        if first_frame.decision_view is None or not first_frame.decision_view.securities():
            raise HistoricalFrameReaderError("first decision frame has an empty market view")
        return report


def load_historical_frame_reader(
    snapshot_dir: Path,
    policy: CorporateActionNormalizationPolicy,
) -> HistoricalReplayFrameReader:
    """Load the bundle gate and open its continuous frame reader."""

    return HistoricalReplayFrameReader(load_historical_replay_bundle(snapshot_dir, policy))
