"""Job-local replay projection built directly from frozen DWD records."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Callable, Mapping
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

from quanthus.backtest.benchmark import BenchmarkCurve, load_benchmark_curve
from quanthus.backtest.historical_bundle import HistoricalReplayBundle
from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.industry_mapping import StaticIndustryMapping, load_static_industry_mapping
from quanthus.policy import ConfigurationError

MATERIALIZER_VERSION = "direct-dwd-job-local-v1"
DUCKDB_MEMORY_LIMIT = "6GB"
DUCKDB_LARGE_PROJECTION_MEMORY_LIMIT = "12GB"
MARKET_TIMEZONE_NAME = "Asia/Shanghai"
DATASETS = (
    "corporate_action_exceptions",
    "corporate_actions_normalized",
    "daily_ohlcv_none",
    "daily_universe",
    "price_limit_execution_daily",
    "security_code_validity",
    "trading_calendar",
)
_DAILY_PARTITIONED_DATASETS = (
    "daily_ohlcv_none",
    "daily_universe",
    "price_limit_execution_daily",
    "trading_calendar",
)
_DAILY_MERGE_PRIORITY = (
    "daily_ohlcv_none",
    "price_limit_execution_daily",
    "daily_universe",
    "trading_calendar",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _sql_path(path: Path) -> str:
    return "'" + str(path.resolve()).replace("'", "''") + "'"


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be an object")
    return value


def _verify_ready(root: Path, manifest_sha256: str, label: str) -> None:
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ConfigurationError(f"{label} READY marker is not readable") from exc
    if ready != manifest_sha256:
        raise ConfigurationError(f"{label} READY marker does not bind its manifest")


class DirectDwdFrameReader(HistoricalReplayFrameReader):
    def __init__(
        self,
        bundle: HistoricalReplayBundle,
        *,
        provider_identity: dict[str, Any],
        benchmark_path: Path,
        benchmark_sha256: str,
        industry_path: Path,
        industry_sha256: str,
    ) -> None:
        self._provider_identity = provider_identity
        self._benchmark_path = benchmark_path
        self._benchmark_sha256 = benchmark_sha256
        self._industry_path = industry_path
        self._industry_sha256 = industry_sha256
        super().__init__(bundle)

    @property
    def data_content_digest(self) -> str:
        return str(self._provider_identity["materialized_content_digest"])

    @property
    def data_manifest_sha256(self) -> str:
        return str(self._provider_identity["provider_lineage_digest"])

    def load_benchmark(self) -> BenchmarkCurve:
        if _sha256(self._benchmark_path) != self._benchmark_sha256:
            raise ConfigurationError("benchmark reference checksum differs")
        return load_benchmark_curve(self._benchmark_path)

    def load_industry_mapping(self, *, expected_version: str) -> StaticIndustryMapping:
        if _sha256(self._industry_path) != self._industry_sha256:
            raise ConfigurationError("industry reference checksum differs")
        return load_static_industry_mapping(
            self._industry_path,
            expected_version=expected_version,
        )


def _copy(connection: Any, query: str, output: Path) -> None:
    connection.execute(
        f"COPY ({query}) TO {_sql_path(output)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)"
    )


class _ProjectionConnections:
    """Own one bounded DuckDB connection at a time for DWD projections."""

    def __init__(
        self,
        duckdb: Any,
        *,
        root: Path,
        payload: Path,
        correction: Path,
        start: str,
        end: str,
    ) -> None:
        self._duckdb = duckdb
        self._root = root
        self._payload = payload
        self._correction = correction
        self._start = start
        self._end = end
        self._connection: Any | None = None
        self._spill: Path | None = None

    def open(self, projection: str, *, memory_limit: str = DUCKDB_MEMORY_LIMIT) -> Any:
        self.close()
        spill = self._root / f".duckdb-materialize-spill-{projection}"
        spill.mkdir()
        connection = self._duckdb.connect(":memory:")
        try:
            connection.execute(f"SET memory_limit='{memory_limit}'")
            connection.execute("SET threads=1")
            connection.execute("SET preserve_insertion_order=false")
            connection.execute(f"SET temp_directory={_sql_path(spill)}")
            connection.execute(
                f"""
                CREATE VIEW dwd_security AS
                SELECT * FROM read_parquet({
                    _sql_path(self._payload / "listed_company/dwd_security.parquet")
                })
                WHERE knowledge_to IS NULL;
                CREATE VIEW dwd_listing AS
                SELECT * FROM read_parquet({
                    _sql_path(self._payload / "listed_company/dwd_listing.parquet")
                })
                WHERE knowledge_to IS NULL;
                CREATE VIEW dwd_bar AS
                SELECT * FROM read_parquet(
                  {
                    _sql_path(
                        self._payload / "security_market_observation/dwd_security_bar/**/*.parquet"
                    )
                },
                  hive_partitioning=true
                )
                WHERE interval_size='1d' AND price_basis='UNADJUSTED'
                  AND knowledge_to IS NULL
                  AND CAST(observation_start AS DATE)
                      BETWEEN DATE '{self._start}' AND DATE '{self._end}';
                CREATE VIEW dwd_calendar AS
                SELECT * FROM read_parquet(
                  {
                    _sql_path(self._payload / "tradability_state/dwd_trading_calendar/**/*.parquet")
                },
                  hive_partitioning=true
                )
                WHERE knowledge_to IS NULL AND is_open
                  AND CAST(trade_date AS DATE)
                      BETWEEN DATE '{self._start}' AND DATE '{self._end}';
                CREATE VIEW correction_state AS
                SELECT * FROM read_parquet(
                  {
                    _sql_path(
                        self._correction
                        / "tradability_state/dwd_tradability_state_replacement.parquet"
                    )
                }
                )
                WHERE knowledge_to IS NULL
                  AND CAST(observation_start AS DATE)
                      BETWEEN DATE '{self._start}' AND DATE '{self._end}';
                CREATE VIEW dwd_state AS
                SELECT base.* FROM read_parquet(
                  {
                    _sql_path(
                        self._payload / "tradability_state/dwd_tradability_state/**/*.parquet"
                    )
                },
                  hive_partitioning=true
                ) base
                WHERE base.knowledge_to IS NULL
                  AND CAST(base.observation_start AS DATE)
                      BETWEEN DATE '{self._start}' AND DATE '{self._end}'
                  AND NOT EXISTS (
                    SELECT 1 FROM correction_state replacement
                    WHERE replacement.listing_id=base.listing_id
                      AND replacement.calendar_session_id=base.calendar_session_id
                  )
                UNION ALL BY NAME SELECT * FROM correction_state;
                """
            )
        except BaseException:
            connection.close()
            shutil.rmtree(spill, ignore_errors=True)
            raise
        self._connection = connection
        self._spill = spill
        return connection

    def close(self) -> None:
        connection, self._connection = self._connection, None
        spill, self._spill = self._spill, None
        if connection is not None:
            connection.close()
        if spill is not None:
            shutil.rmtree(spill, ignore_errors=True)


def _materialize(
    root: Path,
    *,
    dwd_root: Path,
    correction_root: Path,
    history_start: date,
    execution_end: date,
    progress: Callable[[str, Mapping[str, Any]], None] | None = None,
    output_start: date | None = None,
    duckdb_memory_limit: str = DUCKDB_MEMORY_LIMIT,
    duckdb_large_projection_memory_limit: str = DUCKDB_LARGE_PROJECTION_MEMORY_LIMIT,
    allow_empty_non_partitioned: bool = False,
) -> dict[str, int]:
    def report(stage: str, **details: Any) -> None:
        if progress is not None:
            progress(stage, details)

    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ConfigurationError("direct DWD replay requires the p1 dependency group") from exc

    payload = dwd_root / "payload"
    correction = correction_root / "payload"
    if output_start is None:
        output_start = history_start
    if not history_start <= output_start <= execution_end:
        raise ConfigurationError("direct DWD output scope is outside its scan scope")
    start = history_start.isoformat()
    output = output_start.isoformat()
    end = execution_end.isoformat()
    connections = _ProjectionConnections(
        duckdb,
        root=root,
        payload=payload,
        correction=correction,
        start=start,
        end=end,
    )
    connection = connections.open("daily-ohlcv", memory_limit=duckdb_memory_limit)
    try:
        report("dwd-source-scan-ready")
        directional = connection.execute(
            "SELECT count(*) FROM dwd_state WHERE can_buy <> can_sell"
        ).fetchone()
        if directional is None or int(directional[0]):
            raise ConfigurationError(
                "direct DWD contains directional tradability not expressible by the P1 adapter"
            )

        tables = root / "tables"
        tables.mkdir(parents=True)
        _copy(
            connection,
            f"""
            SELECT listing.listing_code AS security_id,
                   listing.listing_code AS stock_code,
                   security.security_id AS dwd_security_id,
                   security.company_id AS dwd_company_id,
                   listing.listing_id AS dwd_listing_id,
                   listing.venue_id AS dwd_venue_id,
                   security.instrument_type AS dwd_instrument_type,
                   security.effective_from AS dwd_security_effective_from,
                   listing.effective_from AS dwd_listing_effective_from,
                   security.knowledge_from AS dwd_security_knowledge_from,
                   listing.knowledge_from AS dwd_listing_knowledge_from,
                   state.knowledge_from AS dwd_state_knowledge_from,
                   CASE listing.venue_id
                     WHEN 'venue_bec59e31eb6bb28111a10d7e6d2c689f' THEN 'SSE'
                     WHEN 'venue_8c0bd9088a0dc88f28e5e7fa8efdce87' THEN 'SZSE'
                   END AS identity_exchange,
                   CAST(calendar.trade_date AS DATE) AS date,
                   CAST(bar.open AS DOUBLE) AS open,
                   CAST(bar.high AS DOUBLE) AS high,
                   CAST(bar.low AS DOUBLE) AS low,
                   CAST(bar.close AS DOUBLE) AS close,
                   CAST(bar.volume AS DOUBLE) AS volume,
                   CAST(bar.turnover AS DOUBLE) AS amount,
                   bar.open AS dwd_open_decimal,
                   bar.high AS dwd_high_decimal,
                   bar.low AS dwd_low_decimal,
                   bar.close AS dwd_close_decimal,
                   bar.turnover AS dwd_turnover_decimal,
                   bar.knowledge_from AS dwd_bar_knowledge_from,
                   bar.source_record_id AS dwd_bar_source_record_id,
                   bar.source_revision AS dwd_bar_source_revision,
                   bar.source_artifact_sha256 AS dwd_bar_source_artifact_sha256,
                   NULL::DOUBLE AS turnover_rate,
                   NULL::DOUBLE AS pct_change,
                   'none' AS adj_mode,
                   'dwd-unadjusted-v1' AS adj_version,
                   'dwd_security_bar' AS source,
                   bar.knowledge_from AS built_at
            FROM dwd_state state
            JOIN dwd_listing listing USING (listing_id)
            JOIN dwd_security security
              ON security.security_id=listing.security_id
             AND security.effective_from <= listing.effective_from
             AND (
               security.effective_to IS NULL
               OR (
                 listing.effective_to IS NOT NULL
                 AND listing.effective_to <= security.effective_to
               )
             )
             AND security.knowledge_from <= listing.knowledge_from
            JOIN dwd_calendar calendar
              ON calendar.calendar_session_id=state.calendar_session_id
             AND calendar.venue_id=listing.venue_id
            LEFT JOIN dwd_bar bar
              ON bar.listing_id=state.listing_id
             AND bar.calendar_session_id=state.calendar_session_id
            WHERE listing.venue_id IN (
              'venue_bec59e31eb6bb28111a10d7e6d2c689f',
              'venue_8c0bd9088a0dc88f28e5e7fa8efdce87'
            )
              AND CAST(calendar.trade_date AS DATE)
                  BETWEEN DATE '{output}' AND DATE '{end}'
            ORDER BY date, security_id
            """,
            tables / "daily_ohlcv_none.parquet",
        )
        connection = connections.open(
            "daily-universe",
            memory_limit=duckdb_large_projection_memory_limit,
        )
        _copy(
            connection,
            f"""
            SELECT listing.listing_code AS security_id,
                   listing.listing_code AS stock_code,
                   security.security_id AS dwd_security_id,
                   security.company_id AS dwd_company_id,
                   listing.listing_id AS dwd_listing_id,
                   listing.venue_id AS dwd_venue_id,
                   security.instrument_type AS dwd_instrument_type,
                   security.effective_from AS dwd_security_effective_from,
                   listing.effective_from AS dwd_listing_effective_from,
                   security.knowledge_from AS dwd_security_knowledge_from,
                   listing.knowledge_from AS dwd_listing_knowledge_from,
                   state.knowledge_from AS dwd_state_knowledge_from,
                   CASE listing.venue_id
                     WHEN 'venue_bec59e31eb6bb28111a10d7e6d2c689f' THEN 'SSE'
                     WHEN 'venue_8c0bd9088a0dc88f28e5e7fa8efdce87' THEN 'SZSE'
                   END AS exchange,
                   CAST(calendar.trade_date AS DATE) AS market_date,
                   CAST(listing.effective_from AS DATE) AS valid_from,
                   CAST(listing.effective_to AS DATE) AS valid_to,
                   'dwd-listing-current-v1' AS mapping_version,
                   listing.source_artifact_sha256 AS mapping_digest
            FROM dwd_state state
            JOIN dwd_listing listing USING (listing_id)
            JOIN dwd_security security
              ON security.security_id=listing.security_id
             AND security.effective_from <= listing.effective_from
             AND (
               security.effective_to IS NULL
               OR (
                 listing.effective_to IS NOT NULL
                 AND listing.effective_to <= security.effective_to
               )
             )
             AND security.knowledge_from <= listing.knowledge_from
            JOIN dwd_calendar calendar
              ON calendar.calendar_session_id=state.calendar_session_id
             AND calendar.venue_id=listing.venue_id
            WHERE listing.venue_id IN (
              'venue_bec59e31eb6bb28111a10d7e6d2c689f',
              'venue_8c0bd9088a0dc88f28e5e7fa8efdce87'
            )
              AND CAST(calendar.trade_date AS DATE)
                  BETWEEN DATE '{output}' AND DATE '{end}'
            ORDER BY market_date, security_id
            """,
            tables / "daily_universe.parquet",
        )
        connection = connections.open(
            "price-limit-execution",
            memory_limit=duckdb_large_projection_memory_limit,
        )
        _copy(
            connection,
            f"""
            WITH joined AS (
              SELECT listing.listing_code AS security_id,
                     listing.listing_code AS stock_code,
                     security.security_id AS dwd_security_id,
                     security.company_id AS dwd_company_id,
                     listing.listing_id AS dwd_listing_id,
                     listing.venue_id AS dwd_venue_id,
                     security.instrument_type AS dwd_instrument_type,
                     security.effective_from AS dwd_security_effective_from,
                     listing.effective_from AS dwd_listing_effective_from,
                     security.knowledge_from AS dwd_security_knowledge_from,
                     listing.knowledge_from AS dwd_listing_knowledge_from,
                     state.knowledge_from AS dwd_state_knowledge_from,
                     listing.board_code AS board,
                     CASE listing.venue_id
                       WHEN 'venue_bec59e31eb6bb28111a10d7e6d2c689f' THEN 'SSE'
                       WHEN 'venue_8c0bd9088a0dc88f28e5e7fa8efdce87' THEN 'SZSE'
                     END AS exchange,
                     CAST(calendar.trade_date AS DATE) AS execution_date,
                     state.*, bar.close,
                     lag(bar.close) OVER (
                       PARTITION BY state.listing_id ORDER BY calendar.trade_date
                     ) AS prior_close
              FROM dwd_state state
              JOIN dwd_listing listing USING (listing_id)
              JOIN dwd_security security
                ON security.security_id=listing.security_id
               AND security.effective_from <= listing.effective_from
               AND (
                 security.effective_to IS NULL
                 OR (
                   listing.effective_to IS NOT NULL
                   AND listing.effective_to <= security.effective_to
                 )
               )
               AND security.knowledge_from <= listing.knowledge_from
              JOIN dwd_calendar calendar
                ON calendar.calendar_session_id=state.calendar_session_id
               AND calendar.venue_id=listing.venue_id
              LEFT JOIN dwd_bar bar
                ON bar.listing_id=state.listing_id
               AND bar.calendar_session_id=state.calendar_session_id
              WHERE listing.venue_id IN (
                'venue_bec59e31eb6bb28111a10d7e6d2c689f',
                'venue_8c0bd9088a0dc88f28e5e7fa8efdce87'
              )
            )
            SELECT security_id, stock_code, dwd_security_id, dwd_company_id,
                   dwd_listing_id, dwd_venue_id, dwd_instrument_type,
                   dwd_security_effective_from, dwd_listing_effective_from,
                   dwd_security_knowledge_from, dwd_listing_knowledge_from,
                   dwd_state_knowledge_from, exchange, board, execution_date,
                   'unknown' AS st_status_label, NULL::BOOLEAN AS is_st_class,
                   true AS status_known, execution_date AS st_source_market_date,
                   source_revision AS st_revision,
                   CASE WHEN is_tradable THEN '1' ELSE '0' END AS observed_tradestatus,
                   source_record_id AS tradestatus_raw_shard,
                   NULL::INTEGER AS eligible_session_index,
                   false AS in_ipo_window, NULL::VARCHAR AS listing_window_rule,
                   NULL::INTEGER AS ipo_window_sessions,
                   'dwd_tradability_state' AS selected_rule_basis,
                   CASE WHEN prior_close > 0 AND upper_price IS NOT NULL
                     THEN (CAST(upper_price AS DOUBLE) / CAST(prior_close AS DOUBLE) - 1) * 100
                   END AS upper_limit_pct,
                   CASE WHEN prior_close > 0 AND lower_price IS NOT NULL
                     THEN (1 - CAST(lower_price AS DOUBLE) / CAST(prior_close AS DOUBLE)) * 100
                   END AS lower_limit_pct,
                   prior_close AS price_reference, 'prior_trading_close' AS price_reference_kind,
                   upper_price AS upper_limit_price, lower_price AS lower_limit_price,
                   CAST(0.01 AS DECIMAL(3,2)) AS price_tick,
                   'dwd_published_price' AS rounding_rule, true AS regime_known,
                   is_tradable AND can_buy AS entry_policy_allows,
                   is_tradable AND can_buy AND can_sell AS is_executable,
                   reason_code, execution_date AS knowledge_cutoff,
                   NULL::VARCHAR AS ipo_interval_id, NULL::VARCHAR AS ipo_source,
                   source_revision AS revision
            FROM joined
            WHERE execution_date BETWEEN DATE '{output}' AND DATE '{end}'
            ORDER BY execution_date, security_id
            """,
            tables / "price_limit_execution_daily.parquet",
        )
        connection = connections.open("finalize", memory_limit=duckdb_memory_limit)
        _copy(
            connection,
            """
            SELECT listing_code AS security_id, listing_code AS stock_code,
                   CASE venue_id
                     WHEN 'venue_bec59e31eb6bb28111a10d7e6d2c689f' THEN 'SSE'
                     WHEN 'venue_8c0bd9088a0dc88f28e5e7fa8efdce87' THEN 'SZSE'
                   END AS exchange,
                   CAST(effective_from AS DATE) AS valid_from,
                   CAST(effective_to AS DATE) AS valid_to,
                   'dwd_listing' AS source, source_record_id AS evidence_locator,
                   source_artifact_sha256 AS evidence_sha256,
                   source_revision, CAST(knowledge_from AS VARCHAR) AS knowledge_cutoff,
                   'dwd-listing-current-v1' AS mapping_version,
                   source_artifact_sha256 AS mapping_digest,
                   effective_to IS NULL AS valid_to_right_censored
            FROM dwd_listing
            WHERE venue_id IN (
              'venue_bec59e31eb6bb28111a10d7e6d2c689f',
              'venue_8c0bd9088a0dc88f28e5e7fa8efdce87'
            )
            ORDER BY security_id, valid_from
            """,
            tables / "security_code_validity.parquet",
        )
        _copy(
            connection,
            f"""
            SELECT CASE venue_id
                     WHEN 'venue_bec59e31eb6bb28111a10d7e6d2c689f' THEN 'SSE'
                     WHEN 'venue_8c0bd9088a0dc88f28e5e7fa8efdce87' THEN 'SZSE'
                   END AS exchange,
                   CAST(trade_date AS DATE) AS trade_date,
                   CAST(is_open AS BIGINT) AS is_open,
                   'day' AS session, 'dwd_trading_calendar' AS source,
                   knowledge_from AS built_at
            FROM dwd_calendar
            WHERE venue_id IN (
              'venue_bec59e31eb6bb28111a10d7e6d2c689f',
              'venue_8c0bd9088a0dc88f28e5e7fa8efdce87'
            )
              AND CAST(trade_date AS DATE) BETWEEN DATE '{output}' AND DATE '{end}'
            ORDER BY trade_date, exchange
            """,
            tables / "trading_calendar.parquet",
        )
        report("dwd-market-projection-ready")
        actions_source = correction / "replay/corporate_actions_normalized.parquet"
        _copy(
            connection,
            f"""
            SELECT actions.* FROM read_parquet({_sql_path(actions_source)}) actions
            WHERE actions.effective_date <= DATE '{end}'
              AND EXISTS (
                SELECT 1 FROM dwd_listing listing
                WHERE listing.listing_code=actions.security_code
                  AND listing.venue_id IN (
                    'venue_bec59e31eb6bb28111a10d7e6d2c689f',
                    'venue_8c0bd9088a0dc88f28e5e7fa8efdce87'
                  )
              )
            ORDER BY effective_date, security_code
            """,
            tables / "corporate_actions_normalized.parquet",
        )
        exceptions_source = correction / "replay/corporate_action_exceptions.parquet"
        _copy(
            connection,
            f"""
            SELECT exceptions.* FROM read_parquet({_sql_path(exceptions_source)}) exceptions
            WHERE exceptions.blocked_from <= DATE '{end}'
              AND EXISTS (
                SELECT 1 FROM dwd_listing listing
                WHERE listing.listing_code=exceptions.security_code
                  AND listing.venue_id IN (
                    'venue_bec59e31eb6bb28111a10d7e6d2c689f',
                    'venue_8c0bd9088a0dc88f28e5e7fa8efdce87'
                  )
              )
            ORDER BY blocked_from, security_code
            """,
            tables / "corporate_action_exceptions.parquet",
        )
        report("dwd-action-projection-ready")
        counts = {
            name: int(
                connection.execute(
                    f"SELECT count(*) FROM read_parquet({_sql_path(tables / f'{name}.parquet')})"
                ).fetchone()[0]
            )
            for name in DATASETS
        }
        required_datasets = (
            _DAILY_PARTITIONED_DATASETS
            if allow_empty_non_partitioned
            else tuple(
                name for name in DATASETS if name != "corporate_action_exceptions"
            )
        )
        if any(counts[name] <= 0 for name in required_datasets):
            raise ConfigurationError("direct DWD materialization produced an empty required table")
        duplicate_pairs = connection.execute(
            f"""
            SELECT
              (SELECT count(*) FROM (
                SELECT security_id,date FROM read_parquet(
                  {_sql_path(tables / "daily_ohlcv_none.parquet")}
                )
                GROUP BY ALL HAVING count(*)<>1)),
              (SELECT count(*) FROM (
                SELECT security_id,market_date FROM read_parquet(
                  {_sql_path(tables / "daily_universe.parquet")}
                )
                GROUP BY ALL HAVING count(*)<>1)),
              (SELECT count(*) FROM (
                SELECT security_id,execution_date FROM read_parquet(
                  {_sql_path(tables / "price_limit_execution_daily.parquet")}
                )
                GROUP BY ALL HAVING count(*)<>1))
            """
        ).fetchone()
        if duplicate_pairs is None or any(int(value) for value in duplicate_pairs):
            raise ConfigurationError(
                "direct DWD materialization contains duplicate daily identities"
            )
        report("dwd-projection-validated", row_count=sum(counts.values()), datasets=counts)
        return counts
    finally:
        connections.close()


def _materialize_date_partition(
    partition_id: str,
    root: str,
    dwd_root: str,
    correction_root: str,
    scan_start: date,
    output_start: date,
    output_end: date,
) -> tuple[str, dict[str, int], float]:
    started = perf_counter()
    partition_root = Path(root)
    partition_root.mkdir(parents=True, exist_ok=False)
    counts = _materialize(
        partition_root,
        dwd_root=Path(dwd_root),
        correction_root=Path(correction_root),
        history_start=scan_start,
        execution_end=output_end,
        output_start=output_start,
        duckdb_memory_limit="1500MB",
        duckdb_large_projection_memory_limit="1500MB",
        allow_empty_non_partitioned=True,
    )
    return partition_id, counts, perf_counter() - started


def _ordered_parquet_expression(paths: tuple[Path, ...]) -> str:
    if not paths:
        raise ConfigurationError("direct DWD shard merge requires input partitions")
    return "read_parquet([" + ",".join(_sql_path(path) for path in paths) + "])"


def _merge_direct_dwd_date_partitions(
    dataset: str,
    paths: tuple[str, ...],
    output: str,
    spill: str,
) -> str:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ConfigurationError("direct DWD replay requires duckdb") from exc
    spill_path = Path(spill)
    spill_path.mkdir()
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET memory_limit='1GB'")
        connection.execute("SET threads=1")
        connection.execute("SET preserve_insertion_order=true")
        connection.execute(f"SET temp_directory={_sql_path(spill_path)}")
        _copy(
            connection,
            f"SELECT * FROM {_ordered_parquet_expression(tuple(Path(path) for path in paths))}",
            Path(output),
        )
    finally:
        connection.close()
        shutil.rmtree(spill_path, ignore_errors=True)
    return dataset


def materialize_sharded_direct_dwd(
    root: Path,
    *,
    dwd_root: Path,
    correction_root: Path,
    history_start: date,
    execution_end: date,
    workers: int,
    progress: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> dict[str, int]:
    """Build the existing single-file projection through bounded date shard tasks."""

    if workers not in {1, 2, 3, 4}:
        raise ConfigurationError("direct DWD shard workers must be between one and four")
    if history_start > execution_end:
        raise ConfigurationError("direct DWD shard scope is invalid")
    root.mkdir(parents=True, exist_ok=False)
    partition_root = root / ".partitions"
    partition_root.mkdir()
    specs: list[tuple[str, Path, date, date, date]] = []
    for year in range(history_start.year, execution_end.year + 1):
        year_start = max(history_start, date(year, 1, 1))
        year_end = min(execution_end, date(year, 12, 31))
        ranges = [(year_start, year_end)]
        if year == execution_end.year and year_start < date(year, 7, 1) <= year_end:
            ranges = [
                (year_start, date(year, 6, 30)),
                (date(year, 7, 1), year_end),
            ]
        for output_start, output_end in ranges:
            scan_start = (
                output_start
                if output_start == history_start
                else max(history_start, output_start - timedelta(days=31))
            )
            partition_id = f"{output_start.isoformat()}--{output_end.isoformat()}"
            specs.append(
                (
                    partition_id,
                    partition_root / f"range-{partition_id}",
                    scan_start,
                    output_start,
                    output_end,
                )
            )
    try:
        execution_specs = tuple(
            sorted(specs, key=lambda item: item[4], reverse=True)
        )
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = tuple(
                executor.submit(
                    _materialize_date_partition,
                    partition_id,
                    str(partition_path),
                    str(dwd_root),
                    str(correction_root),
                    scan_start,
                    output_start,
                    output_end,
                )
                for (
                    partition_id,
                    partition_path,
                    scan_start,
                    output_start,
                    output_end,
                ) in execution_specs
            )
            completed = tuple(future.result() for future in futures)
        by_partition = {
            partition_id: counts for partition_id, counts, _elapsed in completed
        }
        elapsed_by_partition = {
            partition_id: round(elapsed, 3)
            for partition_id, _counts, elapsed in completed
        }
        if set(by_partition) != {partition_id for partition_id, *_rest in specs}:
            raise ConfigurationError("direct DWD shard completion set differs")
        if progress is not None:
            progress(
                "dwd-date-partitions-ready",
                {
                    "partitions": len(specs),
                    "elapsed_by_partition": elapsed_by_partition,
                },
            )

        tables = root / "tables"
        tables.mkdir()
        with ProcessPoolExecutor(max_workers=workers) as executor:
            merge_futures = tuple(
                executor.submit(
                    _merge_direct_dwd_date_partitions,
                    name,
                    tuple(
                        str(partition_path / "tables" / f"{name}.parquet")
                        for _partition_id, partition_path, *_rest in specs
                    ),
                    str(tables / f"{name}.parquet"),
                    str(root / f".duckdb-materialize-spill-merge-{name}"),
                )
                for name in _DAILY_MERGE_PRIORITY
            )
            merged = {future.result() for future in merge_futures}
        if merged != set(_DAILY_PARTITIONED_DATASETS):
            raise ConfigurationError("direct DWD shard merge completion set differs")

        final_partition = specs[-1][1] / "tables"
        for name in set(DATASETS).difference(_DAILY_PARTITIONED_DATASETS):
            shutil.copyfile(final_partition / f"{name}.parquet", tables / f"{name}.parquet")

        counts = {
            name: (
                sum(
                    by_partition[partition_id][name]
                    for partition_id, *_rest in specs
                )
                if name in _DAILY_PARTITIONED_DATASETS
                else by_partition[specs[-1][0]][name]
            )
            for name in DATASETS
        }
        if any(
            counts[name] <= 0
            for name in DATASETS
            if name != "corporate_action_exceptions"
        ):
            raise ConfigurationError(
                "merged direct DWD shards produced an empty required table"
            )
        if progress is not None:
            progress(
                "dwd-shard-merge-ready",
                {"workers": workers, "partitions": len(specs), "datasets": counts},
            )
        return counts
    finally:
        shutil.rmtree(partition_root, ignore_errors=True)


def open_direct_dwd_session(
    config: Any,
    *,
    scope_start: date,
    scope_end: date,
    progress: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> Any:
    from quanthus.backtest.data_provider import ReplayDataSession

    def report(stage: str, **details: Any) -> None:
        if progress is not None:
            progress(stage, details)

    assert config.correction_root is not None
    assert config.correction_identity is not None
    assert config.benchmark_path is not None and config.benchmark_sha256 is not None
    assert config.industry_path is not None and config.industry_sha256 is not None
    assert config.history_start is not None
    dwd_manifest_path = config.dwd_root / "manifest.json"
    correction_manifest_path = config.correction_root / "manifest.json"
    dwd_manifest = _object(dwd_manifest_path, "DWD manifest")
    correction_manifest = _object(correction_manifest_path, "DWD correction manifest")
    actual_dwd = {
        "package_id": dwd_manifest.get("package_id"),
        "through_date": dwd_manifest.get("through_date"),
        "content_digest": dwd_manifest.get("content_digest"),
        "manifest_sha256": _sha256(dwd_manifest_path),
        "relationship_to_replay_source": "direct-job-local-source",
    }
    if actual_dwd != dict(config.dwd_identity):
        raise ConfigurationError("DWD identity differs from direct provider configuration")
    actual_correction = {
        "correction_id": correction_manifest.get("correction_id"),
        "content_digest": correction_manifest.get("content_digest"),
        "manifest_sha256": _sha256(correction_manifest_path),
    }
    if actual_correction != dict(config.correction_identity):
        raise ConfigurationError("DWD correction differs from direct provider configuration")
    parent = correction_manifest.get("parent", {})
    if not isinstance(parent, dict) or (
        parent.get("package_id") != actual_dwd["package_id"]
        or parent.get("content_digest") != actual_dwd["content_digest"]
    ):
        raise ConfigurationError("DWD correction does not bind the configured Base")
    _verify_ready(config.dwd_root, str(actual_dwd["manifest_sha256"]), "DWD Base")
    _verify_ready(
        config.correction_root,
        str(actual_correction["manifest_sha256"]),
        "DWD correction",
    )
    if _sha256(config.benchmark_path) != config.benchmark_sha256:
        raise ConfigurationError("benchmark reference checksum differs")
    if _sha256(config.industry_path) != config.industry_sha256:
        raise ConfigurationError("industry reference checksum differs")
    report(
        "dwd-authority-validated",
        package_id=actual_dwd["package_id"],
        correction_id=actual_correction["correction_id"],
    )

    temporary = tempfile.TemporaryDirectory(prefix="quanthus-direct-dwd-")
    root = Path(temporary.name)
    try:
        counts = _materialize(
            root,
            dwd_root=config.dwd_root,
            correction_root=config.correction_root,
            history_start=config.history_start,
            execution_end=config.scope.execution_end,
            progress=progress,
        )
        files = {name: _sha256(root / "tables" / f"{name}.parquet") for name in DATASETS}
        created_at = datetime.fromisoformat(
            str(correction_manifest["built_at"]).replace("Z", "+00:00")
        )
        snapshot = SnapshotRef(
            market_date=date.fromisoformat(str(actual_dwd["through_date"])),
            knowledge_cutoff=datetime.combine(
                date.fromisoformat(str(actual_dwd["through_date"])),
                time(23, 59, 59),
                tzinfo=created_at.tzinfo,
            ),
            source_revision=(
                f"{MATERIALIZER_VERSION}|{actual_dwd['package_id']}|"
                f"{actual_correction['correction_id']}|retrospective-record-view"
            ),
            snapshot_id=(
                f"job-local-{actual_dwd['package_id']}-{actual_correction['correction_id']}"
            ),
        )
        manifests = tuple(
            DatasetManifest(
                dataset=name,
                version=MATERIALIZER_VERSION,
                source_revisions={
                    "dwd_package_id": str(actual_dwd["package_id"]),
                    "dwd_correction_id": str(actual_correction["correction_id"]),
                    "pit_evidence": "retrospective_dwd_record_view",
                },
                row_count=counts[name],
                partitions=(f"tables/{name}.parquet",),
                schema_hash=files[name],
                content_checksums={f"tables/{name}.parquet": files[name]},
                created_at=created_at,
                knowledge_cutoff=snapshot.knowledge_cutoff,
            )
            for name in DATASETS
        )
        bundle = HistoricalReplayBundle(
            root=root,
            snapshot=snapshot,
            universe_policy_version="dwd-current-listing-state-v1",
            datasets=manifests,
        )
        materialized_content_digest = bundle.digest
        lineage = {
            "materializer_version": MATERIALIZER_VERSION,
            "history_start": config.history_start.isoformat(),
            "dwd": actual_dwd,
            "correction": actual_correction,
            "benchmark_sha256": config.benchmark_sha256,
            "industry_sha256": config.industry_sha256,
            "materialized_content_digest": materialized_content_digest,
        }
        identity = {
            "provider_id": config.provider_id,
            "provider_version": config.provider_version,
            "provider_config_digest": config.digest,
            "mode": config.mode,
            "scope": config.scope.to_dict(),
            "dwd_authority": actual_dwd,
            "dwd_correction": actual_correction,
            "research_references": {
                "benchmark_sha256": config.benchmark_sha256,
                "industry_sha256": config.industry_sha256,
            },
            "materializer_version": MATERIALIZER_VERSION,
            "history_start": config.history_start.isoformat(),
            "materialized_rows": counts,
            "materialized_content_digest": materialized_content_digest,
            "provider_lineage_digest": _digest(lineage),
            "point_in_time": {
                "strategy_cutoff_enforced": True,
                "decision_at_close_execution_at_next_open": True,
                "record_view": "retrospective_dwd_record_view",
                "per_decision_publication_proven": False,
            },
            "dedicated_ads_required": False,
            "job_local_projection": True,
            "shared_session": True,
        }
        reader = DirectDwdFrameReader(
            bundle,
            provider_identity=identity,
            benchmark_path=config.benchmark_path,
            benchmark_sha256=config.benchmark_sha256,
            industry_path=config.industry_path,
            industry_sha256=config.industry_sha256,
        )
        return ReplayDataSession(config, reader, identity, temporary)
    except BaseException:
        temporary.cleanup()
        raise
