"""Job-local replay projection built directly from frozen DWD records."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Mapping
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from quanthus.backtest.benchmark import BenchmarkCurve, load_benchmark_curve
from quanthus.backtest.historical_bundle import HistoricalReplayBundle
from quanthus.backtest.historical_frames import HistoricalReplayFrameReader
from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest
from quanthus.industry_mapping import StaticIndustryMapping, load_static_industry_mapping
from quanthus.policy import ConfigurationError

MATERIALIZER_VERSION = "direct-dwd-job-local-v1"
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
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


def _materialize(
    root: Path,
    *,
    dwd_root: Path,
    correction_root: Path,
    history_start: date,
    execution_end: date,
    progress: Callable[[str, Mapping[str, Any]], None] | None = None,
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
    connection = duckdb.connect(":memory:")
    connection.execute("SET preserve_insertion_order=false")
    start = history_start.isoformat()
    end = execution_end.isoformat()
    try:
        connection.execute(
            f"""
            CREATE VIEW dwd_listing AS
            SELECT * FROM read_parquet({_sql_path(payload / 'listed_company/dwd_listing.parquet')})
            WHERE knowledge_to IS NULL;
            CREATE VIEW dwd_bar AS
            SELECT * FROM read_parquet(
              {_sql_path(
                  payload / 'security_market_observation/dwd_security_bar/**/*.parquet'
              )},
              hive_partitioning=true
            )
            WHERE interval_size='1d' AND price_basis='UNADJUSTED'
              AND knowledge_to IS NULL
              AND CAST(observation_start AS DATE)
                  BETWEEN DATE '{start}' AND DATE '{end}';
            CREATE VIEW dwd_calendar AS
            SELECT * FROM read_parquet(
              {_sql_path(payload / 'tradability_state/dwd_trading_calendar/**/*.parquet')},
              hive_partitioning=true
            )
            WHERE knowledge_to IS NULL AND is_open
              AND CAST(trade_date AS DATE) BETWEEN DATE '{start}' AND DATE '{end}';
            CREATE VIEW correction_state AS
            SELECT * FROM read_parquet(
              {_sql_path(
                  correction / 'tradability_state/dwd_tradability_state_replacement.parquet'
              )}
            )
            WHERE knowledge_to IS NULL
              AND CAST(observation_start AS DATE)
                  BETWEEN DATE '{start}' AND DATE '{end}';
            CREATE VIEW dwd_state AS
            SELECT base.* FROM read_parquet(
              {_sql_path(payload / 'tradability_state/dwd_tradability_state/**/*.parquet')},
              hive_partitioning=true
            ) base
            WHERE base.knowledge_to IS NULL
              AND CAST(base.observation_start AS DATE)
                  BETWEEN DATE '{start}' AND DATE '{end}'
              AND NOT EXISTS (
                SELECT 1 FROM correction_state replacement
                WHERE replacement.listing_id=base.listing_id
                  AND replacement.calendar_session_id=base.calendar_session_id
              )
            UNION ALL BY NAME SELECT * FROM correction_state;
            """
        )
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
            """
            SELECT listing.listing_code AS security_id,
                   listing.listing_code AS stock_code,
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
                   NULL::DOUBLE AS turnover_rate,
                   NULL::DOUBLE AS pct_change,
                   'none' AS adj_mode,
                   'dwd-unadjusted-v1' AS adj_version,
                   'dwd_security_bar' AS source,
                   bar.knowledge_from AS built_at
            FROM dwd_state state
            JOIN dwd_listing listing USING (listing_id)
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
            ORDER BY date, security_id
            """,
            tables / "daily_ohlcv_none.parquet",
        )
        _copy(
            connection,
            """
            SELECT listing.listing_code AS security_id,
                   listing.listing_code AS stock_code,
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
            JOIN dwd_calendar calendar
              ON calendar.calendar_session_id=state.calendar_session_id
             AND calendar.venue_id=listing.venue_id
            WHERE listing.venue_id IN (
              'venue_bec59e31eb6bb28111a10d7e6d2c689f',
              'venue_8c0bd9088a0dc88f28e5e7fa8efdce87'
            )
            ORDER BY market_date, security_id
            """,
            tables / "daily_universe.parquet",
        )
        _copy(
            connection,
            """
            WITH joined AS (
              SELECT listing.listing_code AS security_id,
                     listing.listing_code AS stock_code,
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
            SELECT security_id, stock_code, exchange, board, execution_date,
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
            FROM joined ORDER BY execution_date, security_id
            """,
            tables / "price_limit_execution_daily.parquet",
        )
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
            """
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
        if any(counts[name] <= 0 for name in DATASETS if name != "corporate_action_exceptions"):
            raise ConfigurationError("direct DWD materialization produced an empty required table")
        duplicate_pairs = connection.execute(
            f"""
            SELECT
              (SELECT count(*) FROM (
                SELECT security_id,date FROM read_parquet(
                  {_sql_path(tables / 'daily_ohlcv_none.parquet')}
                )
                GROUP BY ALL HAVING count(*)<>1)),
              (SELECT count(*) FROM (
                SELECT security_id,market_date FROM read_parquet(
                  {_sql_path(tables / 'daily_universe.parquet')}
                )
                GROUP BY ALL HAVING count(*)<>1)),
              (SELECT count(*) FROM (
                SELECT security_id,execution_date FROM read_parquet(
                  {_sql_path(tables / 'price_limit_execution_daily.parquet')}
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
        connection.close()


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
        files = {
            name: _sha256(root / "tables" / f"{name}.parquet") for name in DATASETS
        }
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
