"""Build Issue #58 market-observation DWD objects from one sealed Base plan."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.pipelines.dwd_execution_resources import (
    configure_duckdb_threads,
    dwd_duckdb_memory_limit_mb,
)
from quanthus.pipelines.securities_warehouse.domain_base import (
    verify_imported_domain_candidate,
)
from quanthus.pipelines.securities_warehouse.listed_company import (
    ListedCompanyBuildError,
    verify_listed_company_candidate,
)
from quanthus.pipelines.securities_warehouse.trading_calendar import (
    TradingCalendarBuildError,
    _canonical_digest,
    _file_record,
    _fsync_directory,
    _fsync_file,
    _mapping,
    _object,
    _safe_file,
    _sha256,
    _text,
    _timestamp,
    _verify_manifest_package,
    _verify_ods_binding,
    _verify_plan_package,
    _write_json_atomic,
    _write_text_atomic,
    verify_trading_calendar_candidate,
)


class MarketObservationBuildError(RuntimeError):
    """Raised when Issue #58 cannot be built without ambiguity or data loss."""


@dataclass(frozen=True)
class MarketObservationBuildResult:
    root: Path
    semantic_digest: str
    row_counts: dict[str, int]
    partition_counts: dict[str, int]


@dataclass(frozen=True)
class MarketObservationCandidateVerification:
    root: Path
    manifest_sha256: str
    semantic_digest: str
    row_counts: dict[str, int]
    partition_counts: dict[str, int]


_OWNED_TABLES = (
    "dwd_security_bar",
    "dwd_security_valuation",
    "dwd_market_index",
    "dwd_index_bar",
)
_PARTITIONED_TABLES = {
    "dwd_security_bar": ("interval_size", "observation_start_year", "observation_start_month"),
    "dwd_security_valuation": ("observation_start_year", "observation_start_month"),
    "dwd_index_bar": ("interval_size", "observation_start_year", "observation_start_month"),
}
_SOURCE_IDENTITIES = {
    "daily_ohlcv_unadjusted",
    "daily_ohlcv_none_delta",
    "daily_ohlcv_none_hotfix",
    "minute_1m_research_archive",
    "minute_1m_hotfix",
    "minute_5m_formal_archive",
    "minute_5m_hotfix",
    "intraday_bars",
    "valuation_daily",
    "valuation_history_july_repair",
    "valuation_daily_delta",
    "valuation_history_hotfix",
    "index_daily",
    "index_daily_delta",
    "index_daily_metadata",
}
_OPTIONAL_SOURCE_IDENTITIES = {"base24_native5m_exact"}
_LINEAGE_FIELDS = (
    "record_source",
    "source_record_id",
    "source_revision",
    "source_artifact_sha256",
    "knowledge_from",
    "knowledge_to",
)


def _dependencies() -> tuple[Any, Any, Any]:
    try:
        import duckdb
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise MarketObservationBuildError(
            "duckdb and pyarrow are required for market-observation construction"
        ) from exc
    return duckdb, pa, pq


def _schemas() -> dict[str, Any]:
    _, pa, _ = _dependencies()
    lineage = [
        pa.field("record_source", pa.string(), nullable=False),
        pa.field("source_record_id", pa.string(), nullable=False),
        pa.field("source_revision", pa.string(), nullable=False),
        pa.field("source_artifact_sha256", pa.string(), nullable=False),
        pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("knowledge_to", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    observation = [
        pa.field("observation_start", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("observation_end", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    effective = [
        pa.field("effective_from", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("effective_to", pa.timestamp("us", tz="UTC"), nullable=True),
    ]
    decimal = pa.decimal128(38, 10)
    return {
        "dwd_security_bar": pa.schema(
            lineage
            + observation
            + [
                pa.field("listing_id", pa.string(), nullable=False),
                pa.field("calendar_session_id", pa.string(), nullable=False),
                pa.field("interval_size", pa.string(), nullable=False),
                pa.field("price_basis", pa.string(), nullable=False),
                pa.field("currency", pa.string(), nullable=False),
                pa.field("open", decimal, nullable=False),
                pa.field("high", decimal, nullable=False),
                pa.field("low", decimal, nullable=False),
                pa.field("close", decimal, nullable=False),
                pa.field("volume", decimal, nullable=True),
                pa.field("turnover", decimal, nullable=True),
                pa.field("trade_count", pa.int64(), nullable=True),
            ]
        ),
        "dwd_security_valuation": pa.schema(
            lineage
            + observation
            + [
                pa.field("security_id", pa.string(), nullable=False),
                pa.field("listing_id", pa.string(), nullable=True),
                pa.field("measure_type", pa.string(), nullable=False),
                pa.field("measure_value", decimal, nullable=True),
                pa.field("currency", pa.string(), nullable=True),
                pa.field("methodology", pa.string(), nullable=False),
                pa.field("fiscal_basis", pa.string(), nullable=True),
            ]
        ),
        "dwd_market_index": pa.schema(
            lineage
            + effective
            + [
                pa.field("index_id", pa.string(), nullable=False),
                pa.field("index_code", pa.string(), nullable=False),
                pa.field("index_name", pa.string(), nullable=False),
                pa.field("base_currency", pa.string(), nullable=False),
                pa.field("methodology_version", pa.string(), nullable=True),
            ]
        ),
        "dwd_index_bar": pa.schema(
            lineage
            + observation
            + [
                pa.field("index_id", pa.string(), nullable=False),
                pa.field("calendar_session_id", pa.string(), nullable=True),
                pa.field("interval_size", pa.string(), nullable=False),
                pa.field("currency", pa.string(), nullable=False),
                pa.field("open", decimal, nullable=False),
                pa.field("high", decimal, nullable=False),
                pa.field("low", decimal, nullable=False),
                pa.field("close", decimal, nullable=False),
                pa.field("volume", decimal, nullable=True),
                pa.field("turnover", decimal, nullable=True),
            ]
        ),
    }


def _verify_model_contract(model: Mapping[str, Any]) -> None:
    execution = _mapping(model.get("build_execution_contract"), "build execution contract")
    ownership = _mapping(execution.get("dwd_script_ownership"), "DWD script ownership")
    edges = execution.get("required_dependency_edges")
    tables = {
        raw.get("name"): raw
        for raw in model.get("tables", [])
        if isinstance(raw, Mapping) and raw.get("name") in _OWNED_TABLES
    }
    if (
        ownership.get("58") != list(_OWNED_TABLES)
        or not isinstance(edges, list)
        or [57, 58] not in edges
        or [72, 58] not in edges
        or set(tables) != set(_OWNED_TABLES)
    ):
        raise MarketObservationBuildError("model does not authorize Issue #58 ownership")
    expected = {
        "dwd_security_bar": {
            "time_profile": "market_observation",
            "business_key": ["listing_id", "observation_start", "interval_size", "price_basis"],
            "partition_by": ["interval_size", "observation_start_year", "observation_start_month"],
            "columns": {
                "listing_id": "string",
                "calendar_session_id": "string",
                "interval_size": "string",
                "price_basis": "string",
                "currency": "string",
                "open": "decimal_38_10",
                "high": "decimal_38_10",
                "low": "decimal_38_10",
                "close": "decimal_38_10",
                "volume": "decimal_38_10?",
                "turnover": "decimal_38_10?",
                "trade_count": "int64?",
            },
        },
        "dwd_security_valuation": {
            "time_profile": "market_observation",
            "business_key": ["security_id", "observation_start", "measure_type", "methodology"],
            "partition_by": ["observation_start_year", "observation_start_month"],
            "columns": {
                "security_id": "string",
                "listing_id": "string?",
                "measure_type": "string",
                "measure_value": "decimal_38_10?",
                "currency": "string?",
                "methodology": "string",
                "fiscal_basis": "string?",
            },
        },
        "dwd_market_index": {
            "time_profile": "effective_interval",
            "business_key": ["index_id", "effective_from"],
            "partition_by": [],
            "columns": {
                "index_id": "string",
                "index_code": "string",
                "index_name": "string",
                "base_currency": "string",
                "methodology_version": "string?",
            },
        },
        "dwd_index_bar": {
            "time_profile": "market_observation",
            "business_key": ["index_id", "observation_start", "interval_size"],
            "partition_by": ["interval_size", "observation_start_year", "observation_start_month"],
            "columns": {
                "index_id": "string",
                "calendar_session_id": "string?",
                "interval_size": "string",
                "currency": "string",
                "open": "decimal_38_10",
                "high": "decimal_38_10",
                "low": "decimal_38_10",
                "close": "decimal_38_10",
                "volume": "decimal_38_10?",
                "turnover": "decimal_38_10?",
            },
        },
    }
    for table, contract in expected.items():
        raw = tables[table]
        if raw.get("inherits") != ["lineage"] or any(
            raw.get(key) != value for key, value in contract.items()
        ):
            raise MarketObservationBuildError(f"model contract differs for {table}")


def _source_records(ledger: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records = []
    for raw in ledger.get("records", []):
        if not isinstance(raw, Mapping) or raw.get("record_type") != "file":
            continue
        targets = raw.get("target_tables")
        if (
            raw.get("disposition") == "modeled"
            and isinstance(targets, list)
            and set(targets).intersection(_OWNED_TABLES)
        ):
            identity = raw.get("mapping_identity")
            if identity not in _SOURCE_IDENTITIES | _OPTIONAL_SOURCE_IDENTITIES:
                raise MarketObservationBuildError(f"unexpected modeled source: {identity}")
            records.append(raw)
    identities = {record.get("mapping_identity") for record in records}
    if (
        not identities >= _SOURCE_IDENTITIES
        or identities - _SOURCE_IDENTITIES - _OPTIONAL_SOURCE_IDENTITIES
    ):
        raise MarketObservationBuildError("Issue #58 source identity inventory is incomplete")
    return sorted(records, key=lambda item: _text(item, "path"))


def _verify_sources(
    records: Sequence[Mapping[str, Any]], ods_package: Path
) -> tuple[list[dict[str, Any]], str]:
    registry = []
    for record in records:
        try:
            path = _safe_file(ods_package, record.get("path"))
        except TradingCalendarBuildError as exc:
            raise MarketObservationBuildError(str(exc)) from exc
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != record.get("size_bytes")
            or _sha256(path) != record.get("sha256")
        ):
            raise MarketObservationBuildError(
                f"ODS source failed verification: {record.get('path')}"
            )
        registry.append(
            {
                "absolute_path": str(path),
                "relative_path": _text(record, "path"),
                "dataset_id": _text(record, "dataset_id"),
                "mapping_identity": _text(record, "mapping_identity"),
                "source_sha256": _text(record, "sha256"),
                "declared_rows": int(record.get("row_count") or 0),
            }
        )
    return registry, _canonical_digest(registry)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _scan(registry: Sequence[Mapping[str, Any]], identities: set[str]) -> str:
    paths = [
        _sql_literal(str(item["absolute_path"]))
        for item in registry
        if item["mapping_identity"] in identities
    ]
    if not paths:
        raise MarketObservationBuildError(f"no source files for {sorted(identities)}")
    return f"read_parquet([{','.join(paths)}], union_by_name=true, filename=true)"


def _reference_scan(paths: Sequence[Path]) -> str:
    return f"read_parquet([{','.join(_sql_literal(str(path)) for path in paths)}])"


def _historical_alias_codes(connection: Any, listing_path: Path, cutoff: str) -> list[str]:
    """Return codes that share one PIT-valid security identity on one venue."""

    listings = _reference_scan([listing_path])
    rows = connection.execute(
        f"""WITH known AS (
          SELECT DISTINCT listing_code, security_id, venue_id
          FROM {listings}
          WHERE knowledge_from <= TIMESTAMPTZ {_sql_literal(cutoff)}
            AND (knowledge_to IS NULL OR
                 TIMESTAMPTZ {_sql_literal(cutoff)} < knowledge_to)
        )
        SELECT DISTINCT a.listing_code
        FROM known a JOIN known b
          ON b.security_id=a.security_id AND b.venue_id=a.venue_id
         AND b.listing_code<>a.listing_code
        ORDER BY a.listing_code"""
    ).fetchall()
    return [str(row[0]) for row in rows]


def _code_filter(column: str, stock_codes: Sequence[str] | None) -> str:
    if not stock_codes:
        return ""
    values = ",".join(_sql_literal(code) for code in sorted(set(stock_codes)))
    return f"{column} IN ({values})"


def _register_sources(connection: Any, registry: Sequence[Mapping[str, Any]]) -> None:
    _, pa, _ = _dependencies()
    value = pa.Table.from_pylist(list(registry))
    connection.register("source_registry_arrow", value)
    connection.execute("CREATE TEMP TABLE source_registry AS SELECT * FROM source_registry_arrow")
    connection.unregister("source_registry_arrow")


def _stable_index_id(code: str) -> str:
    value = "\x1f".join(("securities-warehouse-v1", "market_index", code)).encode()
    return f"market_index_{hashlib.sha256(value).hexdigest()[:32]}"


def _write_index_seed(
    path: Path,
    *,
    metadata_record: Mapping[str, Any],
    ods_package: Path,
    knowledge_cutoff: Any,
) -> None:
    _, pa, pq = _dependencies()
    metadata_path = _safe_file(ods_package, metadata_record.get("path"))
    metadata = _object(metadata_path)
    rows = []
    per_code = _mapping(metadata.get("per_code"), "index metadata per_code")
    for raw in metadata.get("universe", []):
        item = _mapping(raw, "index universe member")
        code = _text(item, "index_code")
        bounds = _mapping(per_code.get(code), f"index metadata {code}")
        rows.append(
            {
                "record_source": "index_daily_metadata",
                "source_record_id": f"{_text(metadata_record, 'path')}#index_code={code}",
                "source_revision": (
                    "index_daily_metadata@"
                    f"{_text(metadata_record, 'sha256').removeprefix('sha256:')[:16]}"
                ),
                "source_artifact_sha256": _text(metadata_record, "sha256"),
                "knowledge_from": knowledge_cutoff,
                "knowledge_to": None,
                "effective_from": _timestamp(
                    f"{_text(bounds, 'min')}T00:00:00+08:00", "index effective_from"
                ),
                "effective_to": None,
                "index_id": _stable_index_id(code),
                "index_code": code,
                "index_name": _text(item, "name"),
                "base_currency": "CNY",
                "methodology_version": None,
            }
        )
    if len(rows) != 3 or len({row["index_code"] for row in rows}) != len(rows):
        raise MarketObservationBuildError("index metadata must define three unique benchmarks")
    table = pa.Table.from_pylist(
        sorted(rows, key=lambda row: row["index_code"]),
        schema=_schemas()["dwd_market_index"],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table,
        path,
        compression="zstd",
        version="2.6",
        data_page_version="2.0",
        use_dictionary=True,
        write_statistics=True,
    )
    os.chmod(path, 0o640)
    _fsync_file(path)


def _bar_query(
    registry: Sequence[Mapping[str, Any]],
    listing_path: Path,
    calendar_paths: Sequence[Path],
    cutoff: str,
    *,
    minute_start: str | None = None,
    minute_end: str | None = None,
    minute_interval: str | None = None,
    stock_codes: Sequence[str] | None = None,
    historical_alias_winners_only: bool = False,
    historical_alias_all_candidates: bool = False,
) -> str:
    daily = _scan(
        registry,
        {"daily_ohlcv_unadjusted", "daily_ohlcv_none_delta", "daily_ohlcv_none_hotfix"},
    )
    minute_identities = {
        "minute_1m_research_archive",
        "minute_1m_hotfix",
        "minute_5m_formal_archive",
        "minute_5m_hotfix",
        "intraday_bars",
        "base24_native5m_exact",
    }
    if minute_interval == "1m":
        minute_identities -= {"minute_5m_formal_archive", "minute_5m_hotfix"}
    elif minute_interval == "5m":
        minute_identities -= {"minute_1m_research_archive", "minute_1m_hotfix"}
    elif minute_interval is not None:
        raise MarketObservationBuildError("minute query interval must be 1m or 5m")
    minute = _scan(registry, minute_identities)
    normalized_interval = """CASE
           WHEN r.mapping_identity IN ('minute_1m_research_archive','minute_1m_hotfix')
             THEN '1m'
           WHEN r.mapping_identity IN ('minute_5m_formal_archive','minute_5m_hotfix')
             THEN '5m'
           WHEN r.mapping_identity='base24_native5m_exact' THEN '5m'
           ELSE b.bar_size END"""
    minute_predicates = []
    code_predicate = _code_filter("b.stock_code", stock_codes)
    if code_predicate:
        minute_predicates.append(code_predicate)
    if minute_start is not None:
        minute_predicates.append(
            f"CAST(CAST(b.ts AS VARCHAR) AS TIMESTAMP) >= TIMESTAMP {_sql_literal(minute_start)}"
        )
    if minute_end is not None:
        minute_predicates.append(
            f"CAST(CAST(b.ts AS VARCHAR) AS TIMESTAMP) < TIMESTAMP {_sql_literal(minute_end)}"
        )
    if minute_interval is not None:
        minute_predicates.append(f"({normalized_interval})={_sql_literal(minute_interval)}")
    minute_where = "\n  WHERE " + " AND ".join(minute_predicates) if minute_predicates else ""
    daily_where = f"\n  WHERE {code_predicate}" if code_predicate else ""
    listings = _reference_scan([listing_path])
    calendars = _reference_scan(calendar_paths)
    if historical_alias_winners_only and historical_alias_all_candidates:
        raise MarketObservationBuildError("historical alias query mode is ambiguous")
    if (historical_alias_winners_only or historical_alias_all_candidates) and not stock_codes:
        raise MarketObservationBuildError("historical alias query requires bounded stock codes")
    resolution_ctes = f""", resolved AS (
  SELECT s.*, l.listing_id, l.venue_id, c.calendar_session_id, c.open_at, c.close_at,
         CASE s.interval_size WHEN '1d' THEN c.open_at
              WHEN '1m' THEN s.minute_end - INTERVAL 1 MINUTE
              ELSE s.minute_end - INTERVAL 5 MINUTE END AS observation_start,
         CASE s.interval_size WHEN '1d' THEN c.close_at ELSE s.minute_end END AS observation_end
  FROM source_rows s
  JOIN {listings} l ON l.listing_code=s.stock_code
  JOIN {calendars} c ON c.venue_id=l.venue_id AND c.trade_date=s.trade_date
       AND c.session_type='REGULAR_DAY' AND c.is_open
  WHERE l.effective_from <= CASE WHEN s.interval_size='1d' THEN c.open_at ELSE s.minute_end END
    AND (l.effective_to IS NULL OR
         CASE WHEN s.interval_size='1d' THEN c.open_at ELSE s.minute_end END
           < l.effective_to)
    AND l.knowledge_from <= TIMESTAMPTZ {_sql_literal(cutoff)}
    AND (l.knowledge_to IS NULL OR TIMESTAMPTZ {_sql_literal(cutoff)} < l.knowledge_to)
)"""
    if historical_alias_winners_only or historical_alias_all_candidates:
        resolved_alias_sql = """SELECT * EXCLUDE (target_listing_code)
  FROM resolved_candidates WHERE stock_code<>target_listing_code"""
        if historical_alias_winners_only:
            resolved_alias_sql = """SELECT * EXCLUDE (alias_rank, target_listing_code) FROM (
    SELECT *, row_number() OVER (
      PARTITION BY listing_id, observation_start, interval_size
      ORDER BY CASE WHEN stock_code=target_listing_code THEN 0 ELSE 1 END,
               source_priority DESC, relative_path, stock_code
    ) AS alias_rank
    FROM resolved_candidates
  )
  WHERE alias_rank=1 AND stock_code<>target_listing_code"""
        resolution_ctes = f""", code_identity AS (
  SELECT listing_code, min(security_id) AS security_id, min(venue_id) AS venue_id
  FROM {listings}
  WHERE knowledge_from <= TIMESTAMPTZ {_sql_literal(cutoff)}
    AND (knowledge_to IS NULL OR TIMESTAMPTZ {_sql_literal(cutoff)} < knowledge_to)
  GROUP BY listing_code
  HAVING count(DISTINCT security_id)=1 AND count(DISTINCT venue_id)=1
), resolved_candidates AS (
  SELECT s.*, l.listing_id, l.listing_code AS target_listing_code, l.venue_id,
         c.calendar_session_id, c.open_at, c.close_at,
         CASE s.interval_size WHEN '1d' THEN c.open_at
              WHEN '1m' THEN s.minute_end - INTERVAL 1 MINUTE
              ELSE s.minute_end - INTERVAL 5 MINUTE END AS observation_start,
         CASE s.interval_size WHEN '1d' THEN c.close_at ELSE s.minute_end END AS observation_end
  FROM source_rows s
  JOIN code_identity i ON i.listing_code=s.stock_code
  JOIN {listings} l ON l.security_id=i.security_id AND l.venue_id=i.venue_id
  JOIN {calendars} c ON c.venue_id=l.venue_id AND c.trade_date=s.trade_date
       AND c.session_type='REGULAR_DAY' AND c.is_open
  WHERE l.effective_from <= CASE WHEN s.interval_size='1d' THEN c.open_at ELSE s.minute_end END
    AND (l.effective_to IS NULL OR
         CASE WHEN s.interval_size='1d' THEN c.open_at ELSE s.minute_end END
           < l.effective_to)
    AND l.knowledge_from <= TIMESTAMPTZ {_sql_literal(cutoff)}
    AND (l.knowledge_to IS NULL OR TIMESTAMPTZ {_sql_literal(cutoff)} < l.knowledge_to)
), resolved AS (
  {resolved_alias_sql}
)"""
    return f"""
WITH daily_raw AS (
  SELECT b.*, r.relative_path, r.dataset_id, r.mapping_identity, r.source_sha256,
         CAST(CAST(b.date AS VARCHAR) AS DATE) AS trade_date,
         CASE r.mapping_identity
           WHEN 'daily_ohlcv_unadjusted' THEN 30
           WHEN 'daily_ohlcv_none_delta' THEN 20
           ELSE 10 END AS source_priority
  FROM {daily} b JOIN source_registry r ON b.filename=r.absolute_path{daily_where}
), daily_ranked AS (
  SELECT *, row_number() OVER (
    PARTITION BY stock_code, trade_date ORDER BY source_priority DESC, relative_path
  ) AS source_rank
  FROM daily_raw
), minute_raw AS (
  SELECT b.*, r.relative_path, r.dataset_id, r.mapping_identity, r.source_sha256,
         CAST(CAST(b.ts AS VARCHAR) AS TIMESTAMP) AS interval_label,
         try_cast(json_extract_string(to_json(b),'$.acquired_at') AS TIMESTAMPTZ)
           AS source_acquired_at,
         {normalized_interval} AS normalized_interval
  FROM {minute} b JOIN source_registry r ON b.filename=r.absolute_path{minute_where}
), source_rows AS (
  SELECT stock_code, trade_date,
         timezone('Asia/Shanghai', CAST(trade_date AS TIMESTAMP)) AS local_day_start,
         NULL::TIMESTAMPTZ AS minute_end,
         NULL::TIMESTAMP AS source_label,
         NULL::TIMESTAMPTZ AS source_acquired_at,
         '1d' AS interval_size,
         open, high, low, close, volume, amount,
         dataset_id AS record_source, source_priority,
         relative_path, mapping_identity, source_sha256
  FROM daily_ranked WHERE source_rank=1 AND lower(adj_mode)='none'
  UNION ALL
  SELECT stock_code, CAST(interval_label AS DATE), NULL::TIMESTAMPTZ,
         timezone('Asia/Shanghai', CASE
           WHEN normalized_interval IN ('1m','5m')
            AND CAST(interval_label AS TIME)=TIME '13:00:00'
             THEN interval_label - INTERVAL 90 MINUTE
           ELSE interval_label END) AS minute_end,
         interval_label AS source_label,
         source_acquired_at,
         normalized_interval, open, high, low, close, volume, amount,
         dataset_id,
         CASE mapping_identity
           WHEN 'minute_1m_research_archive' THEN 30
           WHEN 'minute_5m_formal_archive' THEN 30
           WHEN 'base24_native5m_exact' THEN 40
           WHEN 'intraday_bars' THEN 20 ELSE 10 END,
         relative_path, mapping_identity, source_sha256
  FROM minute_raw
){resolution_ctes}
SELECT record_source,
       relative_path || '#stock_code=' || stock_code ||
         CASE WHEN interval_size='1d' THEN '&date=' || CAST(trade_date AS VARCHAR)
              ELSE '&ts=' || strftime(
                source_label,'%Y-%m-%dT%H:%M:%S'
              ) END
         AS source_record_id,
       mapping_identity || '@' ||
         substr(replace(source_sha256,'sha256:',''),1,16) AS source_revision,
       source_sha256 AS source_artifact_sha256,
       CASE WHEN mapping_identity='base24_native5m_exact'
              THEN CASE WHEN source_acquired_at IS NULL
                     THEN error('BASE24_NATIVE5M_ACQUIRED_AT_MISSING')
                     ELSE source_acquired_at END
            ELSE TIMESTAMPTZ {_sql_literal(cutoff)} END AS knowledge_from,
       NULL::TIMESTAMPTZ AS knowledge_to,
       observation_start, observation_end, listing_id, calendar_session_id, interval_size,
       'UNADJUSTED' AS price_basis, 'CNY' AS currency,
       CAST(open AS DECIMAL(38,10)) AS open, CAST(high AS DECIMAL(38,10)) AS high,
       CAST(low AS DECIMAL(38,10)) AS low, CAST(close AS DECIMAL(38,10)) AS close,
       CAST(volume AS DECIMAL(38,10)) AS volume, CAST(amount AS DECIMAL(38,10)) AS turnover,
       NULL::BIGINT AS trade_count,
       strftime(observation_start,'%Y') AS observation_start_year,
       strftime(observation_start,'%m') AS observation_start_month
FROM resolved
"""


def _valuation_query(
    registry: Sequence[Mapping[str, Any]],
    listing_path: Path,
    cutoff: str,
    *,
    stock_codes: Sequence[str] | None = None,
    historical_alias_winners_only: bool = False,
    historical_alias_all_candidates: bool = False,
) -> str:
    sources = _scan(
        registry,
        {
            "valuation_daily",
            "valuation_history_july_repair",
            "valuation_daily_delta",
            "valuation_history_hotfix",
        },
    )
    listings = _reference_scan([listing_path])
    code_predicate = _code_filter("v.stock_code", stock_codes)
    source_where = f"\n  WHERE {code_predicate}" if code_predicate else ""
    if historical_alias_winners_only and historical_alias_all_candidates:
        raise MarketObservationBuildError("historical alias query mode is ambiguous")
    if (historical_alias_winners_only or historical_alias_all_candidates) and not stock_codes:
        raise MarketObservationBuildError("historical alias query requires bounded stock codes")
    resolution_ctes = f""", resolved AS (
  SELECT r.*, l.security_id AS resolved_security_id,
         l.listing_id AS resolved_listing_id,
         timezone('Asia/Shanghai', CAST(trade_date AS TIMESTAMP) + INTERVAL 15 HOUR)
           AS observation_start
  FROM ranked r JOIN {listings} l ON l.listing_code=r.stock_code
  WHERE source_rank=1
    AND l.effective_from <= timezone(
      'Asia/Shanghai', CAST(trade_date AS TIMESTAMP) + INTERVAL 15 HOUR
    )
    AND (l.effective_to IS NULL OR timezone(
      'Asia/Shanghai', CAST(trade_date AS TIMESTAMP) + INTERVAL 15 HOUR
    ) < l.effective_to)
    AND l.knowledge_from <= TIMESTAMPTZ {_sql_literal(cutoff)}
    AND (l.knowledge_to IS NULL OR TIMESTAMPTZ {_sql_literal(cutoff)} < l.knowledge_to)
)"""
    if historical_alias_winners_only or historical_alias_all_candidates:
        resolved_alias_sql = """SELECT * EXCLUDE (target_listing_code)
  FROM resolved_candidates WHERE stock_code<>target_listing_code"""
        if historical_alias_winners_only:
            resolved_alias_sql = """SELECT * EXCLUDE (alias_rank, target_listing_code) FROM (
    SELECT *, row_number() OVER (
      PARTITION BY resolved_security_id, observation_start
      ORDER BY CASE WHEN stock_code=target_listing_code THEN 0 ELSE 1 END,
               source_priority DESC, relative_path, stock_code
    ) AS alias_rank
    FROM resolved_candidates
  )
  WHERE alias_rank=1 AND stock_code<>target_listing_code"""
        resolution_ctes = f""", code_identity AS (
  SELECT listing_code, min(security_id) AS security_id, min(venue_id) AS venue_id
  FROM {listings}
  WHERE knowledge_from <= TIMESTAMPTZ {_sql_literal(cutoff)}
    AND (knowledge_to IS NULL OR TIMESTAMPTZ {_sql_literal(cutoff)} < knowledge_to)
  GROUP BY listing_code
  HAVING count(DISTINCT security_id)=1 AND count(DISTINCT venue_id)=1
), resolved_candidates AS (
  SELECT r.*, l.security_id AS resolved_security_id,
         l.listing_id AS resolved_listing_id,
         l.listing_code AS target_listing_code,
         timezone('Asia/Shanghai', CAST(trade_date AS TIMESTAMP) + INTERVAL 15 HOUR)
           AS observation_start
  FROM ranked r JOIN code_identity i ON i.listing_code=r.stock_code
  JOIN {listings} l ON l.security_id=i.security_id AND l.venue_id=i.venue_id
  WHERE source_rank=1
    AND l.effective_from <= timezone(
      'Asia/Shanghai', CAST(trade_date AS TIMESTAMP) + INTERVAL 15 HOUR
    )
    AND (l.effective_to IS NULL OR timezone(
      'Asia/Shanghai', CAST(trade_date AS TIMESTAMP) + INTERVAL 15 HOUR
    ) < l.effective_to)
    AND l.knowledge_from <= TIMESTAMPTZ {_sql_literal(cutoff)}
    AND (l.knowledge_to IS NULL OR TIMESTAMPTZ {_sql_literal(cutoff)} < l.knowledge_to)
), resolved AS (
  {resolved_alias_sql}
)"""
    return f"""
WITH raw AS (
  SELECT v.*, r.relative_path, r.dataset_id, r.mapping_identity, r.source_sha256,
         CAST(CAST(v.date AS VARCHAR) AS DATE) AS trade_date,
         CASE r.mapping_identity WHEN 'valuation_daily' THEN 40
              WHEN 'valuation_history_july_repair' THEN 30
              WHEN 'valuation_daily_delta' THEN 20 ELSE 10 END AS source_priority
  FROM {sources} v JOIN source_registry r ON v.filename=r.absolute_path{source_where}
), ranked AS (
  SELECT *, row_number() OVER (
    PARTITION BY stock_code, trade_date ORDER BY source_priority DESC, relative_path
  ) AS source_rank
  FROM raw
){resolution_ctes}, measures AS (
  SELECT *, unnest([
    'PE_TTM','PB_MRQ','PS_TTM','PCF_NCF_TTM','TOTAL_SHARE','TOTAL_MARKET_VALUE'
  ]) AS measure_type,
    unnest([pe_ttm,pb,ps_ttm,pcf_ttm,total_share*10000.0,total_mv*10000.0]) AS raw_value
  FROM resolved
)
SELECT dataset_id AS record_source,
       relative_path || '#stock_code=' || stock_code || '&date=' || CAST(trade_date AS VARCHAR)
         || '&measure=' || measure_type AS source_record_id,
       mapping_identity || '@' ||
         substr(replace(source_sha256,'sha256:',''),1,16) AS source_revision,
       source_sha256 AS source_artifact_sha256,
       TIMESTAMPTZ {_sql_literal(cutoff)} AS knowledge_from, NULL::TIMESTAMPTZ AS knowledge_to,
       observation_start, NULL::TIMESTAMPTZ AS observation_end,
       resolved_security_id AS security_id, resolved_listing_id AS listing_id,
       measure_type, CAST(raw_value AS DECIMAL(38,10)) AS measure_value,
       CASE WHEN measure_type='TOTAL_MARKET_VALUE' THEN 'CNY' ELSE NULL END AS currency,
       CASE measure_type WHEN 'PE_TTM' THEN 'BAOSTOCK_PE_TTM'
         WHEN 'PB_MRQ' THEN 'BAOSTOCK_PB_MRQ'
         WHEN 'PS_TTM' THEN 'BAOSTOCK_PS_TTM'
         WHEN 'PCF_NCF_TTM' THEN 'BAOSTOCK_PCF_NCF_TTM'
         WHEN 'TOTAL_SHARE' THEN 'SOURCE_TOTAL_SHARE_X10000'
         ELSE 'SOURCE_TOTAL_MV_CNY_X10000' END AS methodology,
       CASE WHEN measure_type IN ('PE_TTM','PS_TTM','PCF_NCF_TTM') THEN 'TTM'
            WHEN measure_type='PB_MRQ' THEN 'MRQ' ELSE 'POINT_IN_TIME' END AS fiscal_basis,
       strftime(observation_start,'%Y') AS observation_start_year,
       strftime(observation_start,'%m') AS observation_start_month
FROM measures
"""


def _index_bar_query(
    registry: Sequence[Mapping[str, Any]],
    index_path: Path,
    venue_path: Path,
    calendar_paths: Sequence[Path],
    cutoff: str,
) -> str:
    sources = _scan(registry, {"index_daily", "index_daily_delta"})
    indexes = _reference_scan([index_path])
    venues = _reference_scan([venue_path])
    calendars = _reference_scan(calendar_paths)
    return f"""
WITH raw AS (
  SELECT b.*, r.relative_path, r.dataset_id, r.mapping_identity, r.source_sha256,
         CAST(CAST(b.date AS VARCHAR) AS DATE) AS trade_date,
         CASE r.mapping_identity WHEN 'index_daily' THEN 20 ELSE 10 END AS source_priority
  FROM {sources} b JOIN source_registry r ON b.filename=r.absolute_path
), ranked AS (
  SELECT *, row_number() OVER (
    PARTITION BY index_code, trade_date ORDER BY source_priority DESC, relative_path
  ) AS source_rank
  FROM raw
), resolved AS (
  SELECT r.*, i.index_id, c.calendar_session_id, c.open_at, c.close_at
  FROM ranked r JOIN {indexes} i ON i.index_code=r.index_code
  JOIN {venues} v ON v.mic_code=CASE WHEN ends_with(r.index_code,'.SH') THEN 'XSHG' ELSE 'XSHE' END
  JOIN {calendars} c ON c.venue_id=v.venue_id AND c.trade_date=r.trade_date
       AND c.session_type='REGULAR_DAY' AND c.is_open
  WHERE source_rank=1
)
SELECT dataset_id AS record_source,
       relative_path || '#index_code=' || index_code || '&date=' || CAST(trade_date AS VARCHAR)
         AS source_record_id,
       mapping_identity || '@' ||
         substr(replace(source_sha256,'sha256:',''),1,16) AS source_revision,
       source_sha256 AS source_artifact_sha256,
       TIMESTAMPTZ {_sql_literal(cutoff)} AS knowledge_from, NULL::TIMESTAMPTZ AS knowledge_to,
       open_at AS observation_start, close_at AS observation_end, index_id, calendar_session_id,
       '1d' AS interval_size, 'CNY' AS currency,
       CAST(open AS DECIMAL(38,10)) AS open, CAST(high AS DECIMAL(38,10)) AS high,
       CAST(low AS DECIMAL(38,10)) AS low, CAST(close AS DECIMAL(38,10)) AS close,
       CAST(volume AS DECIMAL(38,10)) AS volume, CAST(amount AS DECIMAL(38,10)) AS turnover,
       strftime(open_at,'%Y') AS observation_start_year,
       strftime(open_at,'%m') AS observation_start_month
FROM resolved
"""


def _copy_query(
    connection: Any,
    query: str,
    root: Path,
    partition_columns: Sequence[str],
) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.parent.mkdir(parents=True, exist_ok=True)
    options = ["FORMAT PARQUET", "COMPRESSION ZSTD", "ROW_GROUP_SIZE 122880"]
    if partition_columns:
        options.extend(
            [
                f"PARTITION_BY ({','.join(partition_columns)})",
                "FILENAME_PATTERN 'part-{i}'",
            ]
        )
    sql = f"COPY ({query}) TO {_sql_literal(str(root))} ({','.join(options)})"
    connection.execute(sql)


def _configure_connection(connection: Any, scratch: Path) -> None:
    """Apply the spill-friendly market profile for the memory-constrained build node."""

    configure_duckdb_threads(connection, maximum=2)
    connection.execute("SET preserve_insertion_order=true")
    connection.execute("SET enable_progress_bar=false")
    connection.execute(f"SET memory_limit='{dwd_duckdb_memory_limit_mb(1792, minimum_mb=7168)}MB'")
    connection.execute(f"SET temp_directory={_sql_literal(str(scratch))}")


def _partition_digest(path: Path, schema: Any) -> tuple[str, int]:
    _, pa, pq = _dependencies()
    if not pq.ParquetFile(path).schema_arrow.equals(schema, check_metadata=True):
        raise MarketObservationBuildError(f"partition physical schema differs: {path}")
    digest = hashlib.sha256(str(schema).encode("utf-8"))
    count = 0
    for batch in pq.ParquetFile(path).iter_batches(batch_size=122_880):
        table = pa.Table.from_batches([batch]).select(schema.names).cast(schema)
        normalized = table.to_batches()[0]
        digest.update(normalized.serialize().to_pybytes())
        count += normalized.num_rows
    return f"sha256:{digest.hexdigest()}", count


def _normalize_outputs(table: str, raw_root: Path, work: Path) -> list[dict[str, Any]]:
    _, pa, pq = _dependencies()
    schema = _schemas()[table]
    raw_files = [raw_root] if table == "dwd_market_index" else sorted(raw_root.rglob("*.parquet"))
    if not raw_files:
        raise MarketObservationBuildError(f"{table} produced no parquet files")
    grouped: dict[Path, list[Path]] = {}
    for raw in raw_files:
        grouped.setdefault(raw.parent, []).append(raw)
    states = []
    for parent in sorted(grouped):
        partition_files = sorted(
            grouped[parent],
            key=lambda path: (
                int(path.stem.rsplit("-", 1)[-1]) if path.stem.rsplit("-", 1)[-1].isdigit() else 0,
                path.name,
            ),
        )
        final = partition_files[0] if table == "dwd_market_index" else parent / "part-00000.parquet"
        partial = final.with_suffix(".parquet.partial")
        writer = pq.ParquetWriter(
            partial,
            schema,
            compression="zstd",
            version="2.6",
            data_page_version="2.0",
            use_dictionary=True,
            write_statistics=True,
        )
        rows = 0
        try:
            for raw in partition_files:
                for batch in pq.ParquetFile(raw).iter_batches(batch_size=122_880):
                    value = pa.Table.from_batches([batch])
                    if (
                        "interval_size" in schema.names
                        and "interval_size" not in value.column_names
                    ):
                        interval = next(
                            (
                                part.removeprefix("interval_size=")
                                for part in raw.parts
                                if part.startswith("interval_size=")
                            ),
                            None,
                        )
                        if interval is None:
                            raise MarketObservationBuildError(
                                f"{table} partition is missing interval_size"
                            )
                        value = value.append_column(
                            "interval_size", pa.array([interval] * value.num_rows)
                        )
                    value = value.select(schema.names).cast(schema)
                    writer.write_table(value, row_group_size=122_880)
                    rows += value.num_rows
        finally:
            writer.close()
        for raw in partition_files:
            if raw != final:
                raw.unlink()
        os.chmod(partial, 0o640)
        _fsync_file(partial)
        os.replace(partial, final)
        _fsync_directory(final.parent)
        semantic_digest, verified_rows = _partition_digest(final, schema)
        if verified_rows != rows:
            raise MarketObservationBuildError(f"{table} partition row count changed")
        states.append(
            {
                "path": final.relative_to(work).as_posix(),
                "row_count": rows,
                "semantic_digest": semantic_digest,
                "schema_digest": _canonical_digest(str(schema)),
                "sha256": _sha256(final),
            }
        )
    return sorted(states, key=lambda item: item["path"])


def _repair_july_2026_minute_labels(
    connection: Any,
    work: Path,
    partitions: Sequence[Mapping[str, Any]],
    corrected_query: str,
    *,
    interval_size: str = "1m",
) -> list[dict[str, Any]] | None:
    """Replace one legacy July minute partition when its 13:00 label is unnormalized."""

    if interval_size not in {"1m", "5m"}:
        raise MarketObservationBuildError("minute repair interval must be 1m or 5m")

    candidates = []
    for index, raw in enumerate(partitions):
        item = dict(raw)
        parts = Path(str(item["path"])).parts
        values = {
            key: value for part in parts if "=" in part for key, value in [part.split("=", 1)]
        }
        if (
            values.get("interval_size") == interval_size
            and int(values.get("observation_start_year", "0")) == 2026
            and int(values.get("observation_start_month", "0")) == 7
        ):
            candidates.append((index, item))
    if not candidates:
        return None
    if len(candidates) != 1:
        raise MarketObservationBuildError(
            f"July 2026 {interval_size} checkpoint has multiple partitions"
        )

    index, prior = candidates[0]
    path = _safe_file(work, prior["path"])
    legacy_markers = connection.execute(
        """SELECT count(*) FROM read_parquet(?) WHERE interval_size=?
        AND source_record_id LIKE '%&ts=2026-07-%'
        AND ends_with(source_record_id,'T13:00:00')
        AND CAST(observation_end AT TIME ZONE 'Asia/Shanghai' AS TIME)<>TIME '11:30:00'""",
        [str(path), interval_size],
    ).fetchone()[0]
    if not legacy_markers:
        return None

    schema = _schemas()["dwd_security_bar"]
    partial = path.with_suffix(".parquet.partial")
    writer = None
    rows = 0
    scoped_query = f"""SELECT * EXCLUDE (
      observation_start_year, observation_start_month
    ) FROM ({corrected_query}) corrected
    WHERE interval_size={_sql_literal(interval_size)} AND observation_start_year='2026'
      AND observation_start_month='07'"""
    try:
        try:
            reader = connection.execute(scoped_query).to_arrow_reader(batch_size=122_880)
            _, pa, pq = _dependencies()
            writer = pq.ParquetWriter(
                partial,
                schema,
                compression="zstd",
                version="2.6",
                data_page_version="2.0",
                use_dictionary=True,
                write_statistics=True,
            )
            for batch in reader:
                value = pa.Table.from_batches([batch]).select(schema.names).cast(schema)
                writer.write_table(value, row_group_size=122_880)
                rows += value.num_rows
        finally:
            if writer is not None:
                writer.close()
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    if rows != int(prior["row_count"]):
        partial.unlink(missing_ok=True)
        raise MarketObservationBuildError(
            f"July 2026 {interval_size} targeted repair changed the checkpoint row count"
        )
    os.chmod(partial, 0o640)
    _fsync_file(partial)
    semantic_digest, verified_rows = _partition_digest(partial, schema)
    if verified_rows != rows:
        partial.unlink(missing_ok=True)
        raise MarketObservationBuildError(f"July 2026 {interval_size} targeted repair lost rows")
    os.replace(partial, path)
    _fsync_directory(path.parent)
    updated = list(dict(item) for item in partitions)
    updated[index] = {
        "path": path.relative_to(work).as_posix(),
        "row_count": rows,
        "semantic_digest": semantic_digest,
        "schema_digest": _canonical_digest(str(schema)),
        "sha256": _sha256(path),
    }
    return updated


def _merge_checkpoint_overlay(
    connection: Any,
    work: Path,
    partitions: Sequence[Mapping[str, Any]],
    overlay_query: str,
    *,
    table: str,
) -> list[dict[str, Any]] | None:
    """Append bounded code-history winners without rebuilding completed tables."""

    if table not in {"dwd_security_bar", "dwd_security_valuation"}:
        raise MarketObservationBuildError("historical overlay has an unsupported table")
    _, pa, pq = _dependencies()
    schema = _schemas()[table]
    overlay = connection.execute(overlay_query).to_arrow_table()
    if overlay.num_rows == 0:
        return None
    partition_columns = _PARTITIONED_TABLES[table]
    rows_by_partition: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in overlay.to_pylist():
        key = tuple(str(row[column]) for column in partition_columns)
        rows_by_partition.setdefault(key, []).append(row)

    def partition_key(item: Mapping[str, Any]) -> tuple[str, ...]:
        values = {
            key: value
            for part in Path(str(item["path"])).parts
            if "=" in part
            for key, value in [part.split("=", 1)]
        }
        return tuple(values[column] for column in partition_columns)

    states = [dict(item) for item in partitions]
    state_by_key = {partition_key(item): (index, item) for index, item in enumerate(states)}
    key_columns = {
        "dwd_security_bar": (
            "listing_id",
            "observation_start",
            "interval_size",
            "price_basis",
        ),
        "dwd_security_valuation": (
            "security_id",
            "observation_start",
            "measure_type",
            "methodology",
        ),
    }[table]
    changed = False
    for key in sorted(rows_by_partition):
        candidate = pa.Table.from_pylist(rows_by_partition[key]).select(schema.names).cast(schema)
        registered = f"historical_alias_{table}"
        connection.register(registered, candidate)
        state_entry = state_by_key.get(key)
        if state_entry is None:
            relative = Path(f"payload/security_market_observation/{table}")
            for column, value in zip(partition_columns, key, strict=True):
                relative /= f"{column}={value}"
            relative /= "part-00000.parquet"
            path = work / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            missing = connection.execute(
                f"SELECT * FROM {registered} ORDER BY {','.join(key_columns)}, source_record_id"
            ).to_arrow_table()
            prior_rows = 0
            index = len(states)
        else:
            index, state = state_entry
            path = _safe_file(work, state["path"])
            conditions = " AND ".join(f"e.{column}=o.{column}" for column in key_columns)
            missing = connection.execute(
                f"""SELECT o.* FROM {registered} o
                LEFT JOIN read_parquet(?) e ON {conditions}
                WHERE e.{key_columns[0]} IS NULL
                ORDER BY {",".join(f"o.{column}" for column in key_columns)},
                         o.source_record_id""",
                [str(path)],
            ).to_arrow_table()
            prior_rows = int(state["row_count"])
        connection.unregister(registered)
        if missing.num_rows == 0:
            continue
        partial = path.with_suffix(".parquet.partial")
        writer = pq.ParquetWriter(
            partial,
            schema,
            compression="zstd",
            version="2.6",
            data_page_version="2.0",
            use_dictionary=True,
            write_statistics=True,
        )
        try:
            if path.exists():
                for batch in pq.ParquetFile(path).iter_batches(batch_size=122_880):
                    writer.write_table(pa.Table.from_batches([batch]).select(schema.names))
            writer.write_table(missing.select(schema.names).cast(schema), row_group_size=122_880)
        finally:
            writer.close()
        duplicate = connection.execute(
            f"SELECT 1 FROM read_parquet(?) GROUP BY {','.join(key_columns)} "
            "HAVING count(*)>1 LIMIT 1",
            [str(partial)],
        ).fetchone()
        if duplicate is not None:
            partial.unlink(missing_ok=True)
            raise MarketObservationBuildError(
                f"{table} historical-code overlay creates a duplicate business key"
            )
        os.chmod(partial, 0o640)
        _fsync_file(partial)
        os.replace(partial, path)
        _fsync_directory(path.parent)
        semantic_digest, verified_rows = _partition_digest(path, schema)
        expected_rows = prior_rows + missing.num_rows
        if verified_rows != expected_rows:
            raise MarketObservationBuildError(
                f"{table} historical-code overlay changed the partition row count"
            )
        updated = {
            "path": path.relative_to(work).as_posix(),
            "row_count": verified_rows,
            "semantic_digest": semantic_digest,
            "schema_digest": _canonical_digest(str(schema)),
            "sha256": _sha256(path),
        }
        if state_entry is None:
            states.append(updated)
        else:
            states[index] = updated
        changed = True
    return sorted(states, key=lambda item: item["path"]) if changed else None


def _audit_outputs(
    connection: Any,
    work: Path,
    states: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    listing_path: Path,
    index_path: Path,
    calendar_paths: Sequence[Path],
) -> list[dict[str, Any]]:
    quality_exceptions: list[dict[str, Any]] = []
    keys = {
        "dwd_security_bar": "listing_id,observation_start,interval_size,price_basis",
        "dwd_security_valuation": "security_id,observation_start,measure_type,methodology",
        "dwd_market_index": "index_id,effective_from",
        "dwd_index_bar": "index_id,observation_start,interval_size",
    }
    for table, partitions in states.items():
        for state in partitions:
            path = work / str(state["path"])
            duplicate = connection.execute(
                f"SELECT 1 FROM read_parquet(?) GROUP BY {keys[table]} HAVING count(*)>1 LIMIT 1",
                [str(path)],
            ).fetchone()
            if duplicate is not None:
                raise MarketObservationBuildError(f"{table} contains duplicate business keys")
            missing_lineage = connection.execute(
                """SELECT count(*) FROM read_parquet(?) WHERE
                record_source IS NULL OR source_record_id IS NULL OR
                source_revision IS NULL OR source_artifact_sha256 IS NULL OR
                knowledge_from IS NULL""",
                [str(path)],
            ).fetchone()[0]
            if missing_lineage:
                raise MarketObservationBuildError(
                    f"{table} has {missing_lineage} rows without lineage"
                )
            if table in {"dwd_security_bar", "dwd_index_bar"}:
                invalid = connection.execute(
                    """SELECT count(*) FROM read_parquet(?) WHERE
                    open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR
                    volume < 0 OR turnover < 0 OR observation_end <= observation_start""",
                    [str(path)],
                ).fetchone()[0]
                if invalid:
                    raise MarketObservationBuildError(f"{table} has {invalid} invalid bars")
                invalid_envelope = connection.execute(
                    """SELECT count(*) FROM read_parquet(?) WHERE
                    high < greatest(open,close,low) OR low > least(open,close,high)""",
                    [str(path)],
                ).fetchone()[0]
                if invalid_envelope and table != "dwd_security_bar":
                    raise MarketObservationBuildError(
                        f"{table} has {invalid_envelope} invalid bars"
                    )
                if invalid_envelope:
                    accepted = connection.execute(
                        """SELECT record_source, source_record_id, source_revision,
                        source_artifact_sha256, listing_id, calendar_session_id,
                        CAST(observation_start AS VARCHAR), interval_size,
                        CAST(open AS VARCHAR), CAST(high AS VARCHAR),
                        CAST(low AS VARCHAR), CAST(close AS VARCHAR),
                        CAST(volume AS VARCHAR), CAST(turnover AS VARCHAR)
                        FROM read_parquet(?) WHERE
                        (high < greatest(open,close,low) OR low > least(open,close,high))
                        AND interval_size='1d' AND volume=0 AND turnover=0
                        ORDER BY listing_id, observation_start, source_record_id""",
                        [str(path)],
                    ).fetchall()
                    if len(accepted) != invalid_envelope:
                        raise MarketObservationBuildError(
                            f"{table} has {invalid_envelope - len(accepted)} "
                            "unaccepted invalid bars"
                        )
                    for row in accepted:
                        business_key = {
                            "listing_id": row[4],
                            "observation_start": row[6],
                            "interval_size": row[7],
                            "price_basis": "UNADJUSTED",
                        }
                        quality_exceptions.append(
                            {
                                "code": "INVALID_OHLC_ZERO_TRADE_RETAINED",
                                "affected_table": "dwd_security_bar",
                                "affected_business_key_json": json.dumps(
                                    business_key,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                                "rule_id": "DWD_SECURITY_BAR_OHLC_ENVELOPE_V1",
                                "classification": "ZERO_TRADE_INVALID_OHLC",
                                "severity": "ERROR",
                                "authority": "ODS_SOURCE_PRESERVED",
                                "evidence_digest": row[3],
                                "resolution_state": "OPEN",
                                "record_source": row[0],
                                "source_record_id": row[1],
                                "source_revision": row[2],
                                "calendar_session_id": row[5],
                                "observed_values": {
                                    "open": row[8],
                                    "high": row[9],
                                    "low": row[10],
                                    "close": row[11],
                                    "volume": row[12],
                                    "turnover": row[13],
                                },
                            }
                        )
            if table == "dwd_security_bar":
                invalid = connection.execute(
                    """SELECT count(*) FROM read_parquet(?) WHERE listing_id IS NULL OR
                    calendar_session_id IS NULL OR price_basis<>'UNADJUSTED' OR
                    interval_size NOT IN ('1d','5m','1m')""",
                    [str(path)],
                ).fetchone()[0]
                if invalid:
                    raise MarketObservationBuildError("security bars violate identity or basis")
                unresolved = connection.execute(
                    f"""SELECT count(*) FROM read_parquet(?) b
                    LEFT JOIN {_reference_scan([listing_path])} l
                      ON l.listing_id=b.listing_id
                    LEFT JOIN {_reference_scan(calendar_paths)} c
                      ON c.calendar_session_id=b.calendar_session_id
                    WHERE l.listing_id IS NULL OR c.calendar_session_id IS NULL OR
                      b.observation_start < c.open_at OR b.observation_end > c.close_at OR
                      CASE b.interval_size
                        WHEN '1d' THEN b.observation_start<>c.open_at OR
                          b.observation_end<>c.close_at
                        WHEN '1m' THEN date_diff(
                          'second',b.observation_start,b.observation_end
                        )<>60
                        ELSE date_diff(
                          'second',b.observation_start,b.observation_end
                        )<>300 END""",
                    [str(path)],
                ).fetchone()[0]
                if unresolved:
                    raise MarketObservationBuildError(
                        f"security bars have {unresolved} unresolved or misaligned intervals"
                    )
                session_invalid = connection.execute(
                    """WITH bars AS (
                      SELECT *, CAST(
                        observation_end AT TIME ZONE 'Asia/Shanghai' AS TIME
                      ) AS local_end
                      FROM read_parquet(?) WHERE interval_size IN ('1m','5m')
                    ) SELECT count(*) FROM bars WHERE
                      CASE interval_size
                        WHEN '1m' THEN NOT (
                          local_end BETWEEN TIME '09:31:00' AND TIME '11:30:00' OR
                          local_end BETWEEN TIME '13:01:00' AND TIME '15:00:00'
                        )
                        ELSE NOT (
                          (local_end BETWEEN TIME '09:35:00' AND TIME '11:30:00' OR
                           local_end BETWEEN TIME '13:05:00' AND TIME '15:00:00') AND
                          extract(minute FROM local_end)::INTEGER % 5 = 0
                        ) END""",
                    [str(path)],
                ).fetchone()[0]
                if session_invalid:
                    raise MarketObservationBuildError(
                        f"security bars have {session_invalid} invalid session labels"
                    )
            elif table == "dwd_security_valuation":
                invalid = connection.execute(
                    f"""SELECT count(*) FROM read_parquet(?) v
                    LEFT JOIN {_reference_scan([listing_path])} l
                      ON l.listing_id=v.listing_id
                    WHERE v.security_id IS NULL OR v.listing_id IS NULL OR
                      l.listing_id IS NULL OR v.measure_type IS NULL OR
                      v.methodology IS NULL""",
                    [str(path)],
                ).fetchone()[0]
                if invalid:
                    raise MarketObservationBuildError(
                        f"valuations have {invalid} unresolved identities"
                    )
            elif table == "dwd_market_index":
                invalid = connection.execute(
                    """SELECT count(*) FROM read_parquet(?) WHERE
                    index_id IS NULL OR index_code IS NULL OR index_name IS NULL OR
                    base_currency<>'CNY' OR effective_from IS NULL""",
                    [str(path)],
                ).fetchone()[0]
                if invalid:
                    raise MarketObservationBuildError("market-index definitions are invalid")
            elif table == "dwd_index_bar":
                invalid = connection.execute(
                    f"""SELECT count(*) FROM read_parquet(?) b
                    LEFT JOIN {_reference_scan([index_path])} i ON i.index_id=b.index_id
                    LEFT JOIN {_reference_scan(calendar_paths)} c
                      ON c.calendar_session_id=b.calendar_session_id
                    WHERE i.index_id IS NULL OR c.calendar_session_id IS NULL OR
                      b.interval_size<>'1d' OR b.observation_start<>c.open_at OR
                      b.observation_end<>c.close_at""",
                    [str(path)],
                ).fetchone()[0]
                if invalid:
                    raise MarketObservationBuildError(
                        f"index bars have {invalid} unresolved identities"
                    )
    return sorted(
        quality_exceptions,
        key=lambda item: (item["affected_business_key_json"], item["source_record_id"]),
    )


def _expected_row_counts(
    connection: Any,
    registry: Sequence[Mapping[str, Any]],
    *,
    folded_bar_alias_rows: int = 0,
    folded_valuation_alias_keys: int = 0,
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    daily = _scan(
        registry,
        {"daily_ohlcv_unadjusted", "daily_ohlcv_none_delta", "daily_ohlcv_none_hotfix"},
    )
    valuation = _scan(
        registry,
        {
            "valuation_daily",
            "valuation_history_july_repair",
            "valuation_daily_delta",
            "valuation_history_hotfix",
        },
    )
    indexes = _scan(registry, {"index_daily", "index_daily_delta"})
    audit_queries = {
        "daily_security_bar": f"""SELECT coalesce(sum(n),0), count(*),
          coalesce(sum(n-1),0), coalesce(sum(CASE WHEN variants>1 THEN 1 ELSE 0 END),0)
        FROM (SELECT stock_code, CAST(CAST(date AS VARCHAR) AS DATE), count(*) n,
          count(DISTINCT hash(open,high,low,close,volume,amount,adj_mode)) variants
          FROM {daily} GROUP BY 1,2)""",
        "valuation": f"""SELECT coalesce(sum(n),0), count(*),
          coalesce(sum(n-1),0), coalesce(sum(CASE WHEN variants>1 THEN 1 ELSE 0 END),0)
        FROM (SELECT stock_code, CAST(CAST(date AS VARCHAR) AS DATE), count(*) n,
          count(DISTINCT hash(pe_ttm,pb,ps_ttm,pcf_ttm,total_share,total_mv)) variants
          FROM {valuation} GROUP BY 1,2)""",
        "index_bar": f"""SELECT coalesce(sum(n),0), count(*),
          coalesce(sum(n-1),0), coalesce(sum(CASE WHEN variants>1 THEN 1 ELSE 0 END),0)
        FROM (SELECT index_code, CAST(CAST(date AS VARCHAR) AS DATE), count(*) n,
          count(DISTINCT hash(open,high,low,close,volume,amount)) variants
          FROM {indexes} GROUP BY 1,2)""",
    }
    source_key_audit: dict[str, dict[str, int]] = {}
    for name, query in audit_queries.items():
        raw_rows, selected_keys, overlap_rows, conflicting_keys = connection.execute(
            query
        ).fetchone()
        source_key_audit[name] = {
            "raw_rows": int(raw_rows),
            "selected_business_keys": int(selected_keys),
            "overlap_rows": int(overlap_rows),
            "conflicting_business_keys": int(conflicting_keys),
        }
    conflicting = sum(value["conflicting_business_keys"] for value in source_key_audit.values())
    if conflicting:
        raise MarketObservationBuildError(
            f"source precedence found {conflicting} conflicting business keys"
        )
    minute_identities = {
        "minute_1m_research_archive",
        "minute_1m_hotfix",
        "minute_5m_formal_archive",
        "minute_5m_hotfix",
        "intraday_bars",
    }
    minute_rows = sum(
        int(item["declared_rows"])
        for item in registry
        if item["mapping_identity"] in minute_identities
    )
    expected = {
        "dwd_security_bar": int(
            source_key_audit["daily_security_bar"]["selected_business_keys"]
            + minute_rows
            - folded_bar_alias_rows
        ),
        "dwd_security_valuation": int(
            (source_key_audit["valuation"]["selected_business_keys"] - folded_valuation_alias_keys)
            * 6
        ),
        "dwd_market_index": 3,
        "dwd_index_bar": source_key_audit["index_bar"]["selected_business_keys"],
    }
    return expected, source_key_audit


def _historical_alias_reconciliation(
    connection: Any,
    registry: Sequence[Mapping[str, Any]],
    listing_path: Path,
    calendar_paths: Sequence[Path],
    cutoff: str,
    alias_codes: Sequence[str],
) -> dict[str, Any]:
    if not alias_codes:
        return {
            "alias_codes": [],
            "bar": {},
            "valuation": {
                "raw_source_keys": 0,
                "direct_output_keys": 0,
                "resolved_alias_candidates": 0,
                "emitted_alias_keys": 0,
                "folded_alias_keys": 0,
            },
            "folded_bar_alias_rows": 0,
            "folded_valuation_alias_keys": 0,
        }
    daily = _scan(
        registry,
        {"daily_ohlcv_unadjusted", "daily_ohlcv_none_delta", "daily_ohlcv_none_hotfix"},
    )
    minute = _scan(
        registry,
        {
            "minute_1m_research_archive",
            "minute_1m_hotfix",
            "minute_5m_formal_archive",
            "minute_5m_hotfix",
            "intraday_bars",
        },
    )
    valuation = _scan(
        registry,
        {
            "valuation_daily",
            "valuation_history_july_repair",
            "valuation_daily_delta",
            "valuation_history_hotfix",
        },
    )
    code_filter = _code_filter("b.stock_code", alias_codes)
    raw_bar_rows = dict(
        connection.execute(
            f"""SELECT interval_size, count(*) FROM (
              SELECT '1d' AS interval_size
              FROM {daily} b WHERE {code_filter}
              GROUP BY b.stock_code, CAST(CAST(b.date AS VARCHAR) AS DATE)
              UNION ALL
              SELECT CASE
                WHEN r.mapping_identity IN
                  ('minute_1m_research_archive','minute_1m_hotfix') THEN '1m'
                WHEN r.mapping_identity IN
                  ('minute_5m_formal_archive','minute_5m_hotfix') THEN '5m'
                ELSE b.bar_size END AS interval_size
              FROM {minute} b JOIN source_registry r ON b.filename=r.absolute_path
              WHERE {code_filter}
            ) GROUP BY interval_size ORDER BY interval_size"""
        ).fetchall()
    )
    raw_valuation_keys = int(
        connection.execute(
            f"""SELECT count(*) FROM (
              SELECT b.stock_code, CAST(CAST(b.date AS VARCHAR) AS DATE)
              FROM {valuation} b WHERE {code_filter} GROUP BY 1,2
            )"""
        ).fetchone()[0]
    )

    direct_bar = _bar_query(
        registry,
        listing_path,
        calendar_paths,
        cutoff,
        stock_codes=alias_codes,
    )
    candidate_bar = _bar_query(
        registry,
        listing_path,
        calendar_paths,
        cutoff,
        stock_codes=alias_codes,
        historical_alias_all_candidates=True,
    )
    winner_bar = _bar_query(
        registry,
        listing_path,
        calendar_paths,
        cutoff,
        stock_codes=alias_codes,
        historical_alias_winners_only=True,
    )

    def bar_counts(query: str) -> dict[str, int]:
        return {
            str(interval): int(count)
            for interval, count in connection.execute(
                f"SELECT interval_size,count(*) FROM ({query}) GROUP BY interval_size"
            ).fetchall()
        }

    direct_bar_rows = bar_counts(direct_bar)
    candidate_bar_rows = bar_counts(candidate_bar)
    winner_bar_rows = bar_counts(winner_bar)
    bar_audit: dict[str, dict[str, int]] = {}
    folded_bar_alias_rows = 0
    for interval in sorted(set(raw_bar_rows) | set(direct_bar_rows) | set(candidate_bar_rows)):
        raw = int(raw_bar_rows.get(interval, 0))
        direct = int(direct_bar_rows.get(interval, 0))
        candidates = int(candidate_bar_rows.get(interval, 0))
        emitted = int(winner_bar_rows.get(interval, 0))
        if raw - direct != candidates or emitted > candidates:
            raise MarketObservationBuildError(
                f"historical code aliases do not reconcile for {interval}: "
                f"raw={raw}, direct={direct}, candidates={candidates}, emitted={emitted}"
            )
        folded = candidates - emitted
        folded_bar_alias_rows += folded
        bar_audit[interval] = {
            "raw_source_rows": raw,
            "direct_output_rows": direct,
            "resolved_alias_candidates": candidates,
            "emitted_alias_rows": emitted,
            "folded_alias_rows": folded,
        }

    direct_valuation = _valuation_query(registry, listing_path, cutoff, stock_codes=alias_codes)
    candidate_valuation = _valuation_query(
        registry,
        listing_path,
        cutoff,
        stock_codes=alias_codes,
        historical_alias_all_candidates=True,
    )
    winner_valuation = _valuation_query(
        registry,
        listing_path,
        cutoff,
        stock_codes=alias_codes,
        historical_alias_winners_only=True,
    )

    def valuation_keys(query: str) -> int:
        rows = int(connection.execute(f"SELECT count(*) FROM ({query})").fetchone()[0])
        if rows % 6:
            raise MarketObservationBuildError("historical valuation aliases lost measure grain")
        return rows // 6

    direct_valuation_keys = valuation_keys(direct_valuation)
    candidate_valuation_keys = valuation_keys(candidate_valuation)
    winner_valuation_keys = valuation_keys(winner_valuation)
    if (
        raw_valuation_keys - direct_valuation_keys != candidate_valuation_keys
        or winner_valuation_keys > candidate_valuation_keys
    ):
        raise MarketObservationBuildError(
            "historical valuation aliases do not reconcile: "
            f"raw={raw_valuation_keys}, direct={direct_valuation_keys}, "
            f"candidates={candidate_valuation_keys}, emitted={winner_valuation_keys}"
        )
    folded_valuation_alias_keys = candidate_valuation_keys - winner_valuation_keys
    return {
        "alias_codes": list(alias_codes),
        "bar": bar_audit,
        "valuation": {
            "raw_source_keys": raw_valuation_keys,
            "direct_output_keys": direct_valuation_keys,
            "resolved_alias_candidates": candidate_valuation_keys,
            "emitted_alias_keys": winner_valuation_keys,
            "folded_alias_keys": folded_valuation_alias_keys,
        },
        "folded_bar_alias_rows": folded_bar_alias_rows,
        "folded_valuation_alias_keys": folded_valuation_alias_keys,
    }


def _table_digest(partitions: Sequence[Mapping[str, Any]]) -> str:
    return _canonical_digest(
        [
            {
                "path": item["path"],
                "row_count": item["row_count"],
                "semantic_digest": item["semantic_digest"],
            }
            for item in partitions
        ]
    )


def _verify_existing_output(
    root: Path,
    *,
    run_id: str,
    input_plan_digest: str,
    model_sha256: str,
    source_set_digest: str,
    listed_company_semantic_digest: str,
    listed_company_manifest_sha256: str,
    trading_calendar_semantic_digest: str,
    trading_calendar_manifest_sha256: str,
) -> MarketObservationBuildResult:
    receipt = _object(root / "receipts/market-observation-build.json")
    checkpoint = _object(root / "checkpoint.json")
    completed = _mapping(checkpoint.get("completed_tables"), "completed tables")
    expected_files = {
        "checkpoint.json",
        "receipts/market-observation-build.json",
        "reconciliation/source-row-reconciliation.json",
        *{
            str(item["path"])
            for raw in completed.values()
            for item in _mapping(raw, "completed table").get("partitions", [])
            if isinstance(item, Mapping)
        },
    }
    manifest = _verify_manifest_package(
        root,
        expected_files=expected_files,
        expected_kind="dwd_market_observation_candidate",
    )
    identity = {
        "run_id": run_id,
        "input_plan_digest": input_plan_digest,
        "model_sha256": model_sha256,
        "source_set_digest": source_set_digest,
        "listed_company_semantic_digest": listed_company_semantic_digest,
        "listed_company_manifest_sha256": listed_company_manifest_sha256,
        "trading_calendar_semantic_digest": trading_calendar_semantic_digest,
        "trading_calendar_manifest_sha256": trading_calendar_manifest_sha256,
    }
    if any(receipt.get(key) != value for key, value in identity.items()) or (
        receipt.get("schema_version") != 1
        or receipt.get("status") != "built"
        or set(completed) != set(_OWNED_TABLES)
        or checkpoint.get("semantic_digest") != receipt.get("semantic_digest")
        or manifest.get("owned_tables") != list(_OWNED_TABLES)
        or manifest.get("semantic_digest") != receipt.get("semantic_digest")
    ):
        raise MarketObservationBuildError("market-observation candidate identity differs")
    table_digests = {}
    row_counts = {}
    partition_counts = {}
    schemas = _schemas()
    for table in _OWNED_TABLES:
        state = _mapping(completed[table], f"completed {table}")
        partitions = state.get("partitions")
        if not isinstance(partitions, list) or not partitions:
            raise MarketObservationBuildError(f"{table} checkpoint has no partitions")
        verified = []
        for raw in partitions:
            item = _mapping(raw, f"{table} partition")
            path = _safe_file(root, item.get("path"))
            digest, rows = _partition_digest(path, schemas[table])
            if (
                rows != item.get("row_count")
                or digest != item.get("semantic_digest")
                or _sha256(path) != item.get("sha256")
                or item.get("schema_digest") != _canonical_digest(str(schemas[table]))
            ):
                raise MarketObservationBuildError(f"{table} partition failed verification")
            verified.append(dict(item))
        table_digests[table] = _table_digest(verified)
        row_counts[table] = sum(int(item["row_count"]) for item in verified)
        partition_counts[table] = len(verified)
    semantic = _canonical_digest({**identity, "table_digests": table_digests})
    if (
        semantic != receipt.get("semantic_digest")
        or table_digests != receipt.get("table_digests")
        or row_counts != receipt.get("row_counts")
        or row_counts != manifest.get("row_counts")
    ):
        raise MarketObservationBuildError("market-observation semantic digest differs")
    return MarketObservationBuildResult(root.resolve(), semantic, row_counts, partition_counts)


def verify_market_observation_candidate(
    root: Path,
    *,
    run_id: str,
    input_plan_digest: str,
    model_sha256: str,
    listed_company_semantic_digest: str,
    listed_company_manifest_sha256: str,
    trading_calendar_semantic_digest: str,
    trading_calendar_manifest_sha256: str,
) -> MarketObservationCandidateVerification:
    """Deep-verify a sealed #58 candidate for a same-run downstream builder."""

    imported = verify_imported_domain_candidate(
        root,
        domain="security_market_observation",
        run_id=run_id,
        input_plan_digest=input_plan_digest,
        expected_bindings={
            "model_sha256": model_sha256,
            "listed_company_semantic_digest": listed_company_semantic_digest,
            "listed_company_manifest_sha256": listed_company_manifest_sha256,
            "trading_calendar_semantic_digest": trading_calendar_semantic_digest,
            "trading_calendar_manifest_sha256": trading_calendar_manifest_sha256,
        },
    )
    if imported is not None:
        source_manifest = _object(imported.source_root / "manifest.json")
        native = verify_market_observation_candidate(
            imported.source_root,
            run_id=imported.source_run_id,
            input_plan_digest=imported.source_plan_digest,
            model_sha256=_text(source_manifest, "model_sha256"),
            listed_company_semantic_digest=_text(source_manifest, "listed_company_semantic_digest"),
            listed_company_manifest_sha256=_text(source_manifest, "listed_company_manifest_sha256"),
            trading_calendar_semantic_digest=_text(
                source_manifest, "trading_calendar_semantic_digest"
            ),
            trading_calendar_manifest_sha256=_text(
                source_manifest, "trading_calendar_manifest_sha256"
            ),
        )
        return MarketObservationCandidateVerification(
            root=imported.root,
            manifest_sha256=imported.manifest_sha256,
            semantic_digest=native.semantic_digest,
            row_counts=native.row_counts,
            partition_counts=native.partition_counts,
        )
    receipt = _object(root / "receipts/market-observation-build.json")
    source_set_digest = _text(receipt, "source_set_digest")
    result = _verify_existing_output(
        root,
        run_id=run_id,
        input_plan_digest=input_plan_digest,
        model_sha256=model_sha256,
        source_set_digest=source_set_digest,
        listed_company_semantic_digest=listed_company_semantic_digest,
        listed_company_manifest_sha256=listed_company_manifest_sha256,
        trading_calendar_semantic_digest=trading_calendar_semantic_digest,
        trading_calendar_manifest_sha256=trading_calendar_manifest_sha256,
    )
    return MarketObservationCandidateVerification(
        root=result.root,
        manifest_sha256=_sha256(root / "manifest.json"),
        semantic_digest=result.semantic_digest,
        row_counts=result.row_counts,
        partition_counts=result.partition_counts,
    )


def _prepare_work(
    work: Path, identity: Mapping[str, str]
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    if not work.exists():
        work.mkdir(mode=0o750)
        (work / "receipts").mkdir()
        (work / "reconciliation").mkdir()
        checkpoint: dict[str, Any] = {
            "schema_version": 1,
            "kind": "dwd_market_observation_checkpoint",
            **identity,
            "completed_tables": {},
        }
        _write_json_atomic(work / "checkpoint.json", checkpoint)
        return checkpoint, {}
    if work.is_symlink() or not work.is_dir():
        raise MarketObservationBuildError("build work path must be a physical directory")
    checkpoint = _object(work / "checkpoint.json")
    if (
        checkpoint.get("schema_version") != 1
        or checkpoint.get("kind") != "dwd_market_observation_checkpoint"
        or any(checkpoint.get(key) != value for key, value in identity.items())
    ):
        raise MarketObservationBuildError("checkpoint belongs to another build")
    completed = _mapping(checkpoint.get("completed_tables"), "completed tables")
    if not set(completed).issubset(_OWNED_TABLES):
        raise MarketObservationBuildError("checkpoint contains an unowned table")
    schemas = _schemas()
    states: dict[str, list[dict[str, Any]]] = {}
    for table, raw in completed.items():
        state = _mapping(raw, f"completed {table}")
        partitions = state.get("partitions")
        if not isinstance(partitions, list) or not partitions:
            raise MarketObservationBuildError(f"checkpoint {table} has no partitions")
        verified = []
        for raw_partition in partitions:
            partition = dict(_mapping(raw_partition, f"{table} partition"))
            try:
                path = _safe_file(work, partition.get("path"))
            except TradingCalendarBuildError as exc:
                raise MarketObservationBuildError(str(exc)) from exc
            digest, rows = _partition_digest(path, schemas[table])
            if (
                rows != partition.get("row_count")
                or digest != partition.get("semantic_digest")
                or _sha256(path) != partition.get("sha256")
                or partition.get("schema_digest") != _canonical_digest(str(schemas[table]))
            ):
                raise MarketObservationBuildError(
                    f"checkpoint partition failed verification: {partition.get('path')}"
                )
            verified.append(partition)
        if state.get("table_digest") != _table_digest(verified):
            raise MarketObservationBuildError(f"checkpoint table digest differs: {table}")
        states[table] = verified
    base = work / "payload/security_market_observation"
    for table in _OWNED_TABLES:
        if table in states:
            continue
        path = base / ("dwd_market_index.parquet" if table == "dwd_market_index" else table)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    for name in (
        "manifest.json",
        "READY",
        "receipts/market-observation-build.json",
        "reconciliation/source-row-reconciliation.json",
    ):
        (work / name).unlink(missing_ok=True)
    for partial in work.rglob("*.partial"):
        partial.unlink()
    (work / "receipts").mkdir(exist_ok=True)
    (work / "reconciliation").mkdir(exist_ok=True)
    return checkpoint, states


def _create_scratch_directory(output: Path, build_identity: str, scratch_root: Path | None) -> Path:
    scratch_parent = output.parent if scratch_root is None else scratch_root
    if scratch_parent.is_symlink() or not scratch_parent.is_dir():
        raise MarketObservationBuildError("scratch root must be a physical directory")
    return Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.{build_identity}.",
            suffix=".duckdb-tmp",
            dir=scratch_parent.resolve(),
        )
    )


def _build_locked(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    trading_calendar_candidate: Path,
    output: Path,
    run_id: str,
    scratch_root: Path | None,
) -> MarketObservationBuildResult:
    try:
        plan, ledger = _verify_plan_package(input_plan)
        semantic_plan = _mapping(plan.get("plan"), "input plan")
        model = _object(model_path)
        _verify_ods_binding(ods_package, semantic_plan, model)
    except TradingCalendarBuildError as exc:
        raise MarketObservationBuildError(str(exc)) from exc
    if plan.get("run_id") != run_id or semantic_plan.get("mode") != "base":
        raise MarketObservationBuildError("builder must match one sealed Base run")
    model_sha = _sha256(model_path)
    model_identity = _mapping(semantic_plan.get("model"), "input plan model")
    if model_identity.get("sha256") != model_sha:
        raise MarketObservationBuildError("model differs from the sealed input plan")
    _verify_model_contract(model)
    cutoff = _timestamp(semantic_plan.get("knowledge_cutoff"), "knowledge cutoff")
    try:
        listed = verify_listed_company_candidate(
            listed_company_candidate,
            run_id=run_id,
            input_plan_digest=_text(plan, "plan_digest"),
            model_sha256=model_sha,
        )
    except ListedCompanyBuildError as exc:
        raise MarketObservationBuildError(f"#57 candidate verification failed: {exc}") from exc
    try:
        calendar = verify_trading_calendar_candidate(
            trading_calendar_candidate,
            run_id=run_id,
            input_plan_digest=_text(plan, "plan_digest"),
            model_sha256=model_sha,
            upstream_semantic_digest=listed.semantic_digest,
            upstream_manifest_sha256=listed.manifest_sha256,
        )
    except TradingCalendarBuildError as exc:
        raise MarketObservationBuildError(f"#72 candidate verification failed: {exc}") from exc
    records = _source_records(ledger)
    registry, source_set_digest = _verify_sources(records, ods_package)
    identity = {
        "run_id": run_id,
        "input_plan_digest": _text(plan, "plan_digest"),
        "model_sha256": model_sha,
        "source_set_digest": source_set_digest,
        "listed_company_semantic_digest": listed.semantic_digest,
        "listed_company_manifest_sha256": listed.manifest_sha256,
        "trading_calendar_semantic_digest": calendar.semantic_digest,
        "trading_calendar_manifest_sha256": calendar.manifest_sha256,
    }
    if output.exists():
        return _verify_existing_output(output, **identity)

    build_identity = _canonical_digest(identity).removeprefix("sha256:")
    work = output.parent / f".{output.name}.{build_identity}.work"
    checkpoint, states = _prepare_work(work, identity)
    duckdb, _, _ = _dependencies()
    scratch = _create_scratch_directory(output, build_identity, scratch_root)
    connection = duckdb.connect()
    _configure_connection(connection, scratch)
    _register_sources(connection, registry)
    listing_path = listed.root / "payload/listed_company/dwd_listing.parquet"
    venue_path = listed.root / "payload/listed_company/dwd_trading_venue.parquet"
    calendar_paths = sorted(
        calendar.root.glob(
            "payload/tradability_state/dwd_trading_calendar/trade_date_year=*/part-00000.parquet"
        )
    )
    alias_codes = _historical_alias_codes(connection, listing_path, cutoff.isoformat())
    try:
        index_record = next(
            record for record in records if record.get("mapping_identity") == "index_daily_metadata"
        )
        index_path = work / "payload/security_market_observation/dwd_market_index.parquet"
        if "dwd_market_index" not in states:
            _write_index_seed(
                index_path,
                metadata_record=index_record,
                ods_package=ods_package,
                knowledge_cutoff=cutoff,
            )
            states["dwd_market_index"] = _normalize_outputs("dwd_market_index", index_path, work)
            checkpoint["completed_tables"] = {
                name: {
                    "table_digest": _table_digest(partitions),
                    "partitions": partitions,
                }
                for name, partitions in states.items()
            }
            _write_json_atomic(work / "checkpoint.json", checkpoint)

        security_bar_query = _bar_query(registry, listing_path, calendar_paths, cutoff.isoformat())
        alias_bar_query = (
            _bar_query(
                registry,
                listing_path,
                calendar_paths,
                cutoff.isoformat(),
                stock_codes=alias_codes,
                historical_alias_winners_only=True,
            )
            if alias_codes
            else None
        )
        valuation_query = _valuation_query(registry, listing_path, cutoff.isoformat())
        alias_valuation_query = (
            _valuation_query(
                registry,
                listing_path,
                cutoff.isoformat(),
                stock_codes=alias_codes,
                historical_alias_winners_only=True,
            )
            if alias_codes
            else None
        )
        security_bar_build_query = security_bar_query
        valuation_build_query = valuation_query
        if alias_bar_query is not None:
            security_bar_build_query = (
                f"SELECT * FROM ({security_bar_query}) UNION ALL SELECT * FROM ({alias_bar_query})"
            )
        if alias_valuation_query is not None:
            valuation_build_query = (
                f"SELECT * FROM ({valuation_query}) UNION ALL "
                f"SELECT * FROM ({alias_valuation_query})"
            )
        table_specs = (
            (
                "dwd_index_bar",
                _index_bar_query(
                    registry, index_path, venue_path, calendar_paths, cutoff.isoformat()
                ),
            ),
            (
                "dwd_security_bar",
                security_bar_build_query,
            ),
            (
                "dwd_security_valuation",
                valuation_build_query,
            ),
        )
        for table, query in table_specs:
            if table in states:
                continue
            root = work / "payload/security_market_observation" / table
            _copy_query(connection, query, root, _PARTITIONED_TABLES[table])
            states[table] = _normalize_outputs(table, root, work)
            checkpoint["completed_tables"] = {
                name: {
                    "table_digest": _table_digest(partitions),
                    "partitions": partitions,
                }
                for name, partitions in states.items()
            }
            _write_json_atomic(work / "checkpoint.json", checkpoint)
        for minute_interval in ("1m", "5m"):
            repair_query = _bar_query(
                registry,
                listing_path,
                calendar_paths,
                cutoff.isoformat(),
                minute_start="2026-07-01 00:00:00",
                minute_end="2026-08-01 00:00:00",
                minute_interval=minute_interval,
            )
            repaired = _repair_july_2026_minute_labels(
                connection,
                work,
                states["dwd_security_bar"],
                repair_query,
                interval_size=minute_interval,
            )
            if repaired is not None:
                states["dwd_security_bar"] = repaired
                checkpoint["completed_tables"]["dwd_security_bar"] = {
                    "table_digest": _table_digest(repaired),
                    "partitions": repaired,
                }
                _write_json_atomic(work / "checkpoint.json", checkpoint)
        if alias_bar_query is not None:
            repaired = _merge_checkpoint_overlay(
                connection,
                work,
                states["dwd_security_bar"],
                alias_bar_query,
                table="dwd_security_bar",
            )
            if repaired is not None:
                states["dwd_security_bar"] = repaired
                checkpoint["completed_tables"]["dwd_security_bar"] = {
                    "table_digest": _table_digest(repaired),
                    "partitions": repaired,
                }
                _write_json_atomic(work / "checkpoint.json", checkpoint)
        if alias_valuation_query is not None:
            repaired = _merge_checkpoint_overlay(
                connection,
                work,
                states["dwd_security_valuation"],
                alias_valuation_query,
                table="dwd_security_valuation",
            )
            if repaired is not None:
                states["dwd_security_valuation"] = repaired
                checkpoint["completed_tables"]["dwd_security_valuation"] = {
                    "table_digest": _table_digest(repaired),
                    "partitions": repaired,
                }
                _write_json_atomic(work / "checkpoint.json", checkpoint)
        quality_exceptions = _audit_outputs(
            connection,
            work,
            states,
            listing_path=listing_path,
            index_path=index_path,
            calendar_paths=calendar_paths,
        )
        alias_reconciliation = _historical_alias_reconciliation(
            connection,
            registry,
            listing_path,
            calendar_paths,
            cutoff.isoformat(),
            alias_codes,
        )
        expected_row_counts, source_key_audit = _expected_row_counts(
            connection,
            registry,
            folded_bar_alias_rows=int(alias_reconciliation["folded_bar_alias_rows"]),
            folded_valuation_alias_keys=int(alias_reconciliation["folded_valuation_alias_keys"]),
        )
        actual_row_counts = {
            table: sum(int(item["row_count"]) for item in states[table]) for table in _OWNED_TABLES
        }
        if actual_row_counts != expected_row_counts:
            raise MarketObservationBuildError(
                "output rows do not reconcile to the selected source business keys: "
                f"expected={expected_row_counts}, actual={actual_row_counts}"
            )
    finally:
        connection.close()
        shutil.rmtree(scratch, ignore_errors=True)

    row_counts = {
        table: sum(int(item["row_count"]) for item in states[table]) for table in _OWNED_TABLES
    }
    partition_counts = {table: len(states[table]) for table in _OWNED_TABLES}
    table_digests = {table: _table_digest(states[table]) for table in _OWNED_TABLES}
    semantic_digest = _canonical_digest({**identity, "table_digests": table_digests})
    checkpoint["semantic_digest"] = semantic_digest
    checkpoint["completed_tables"] = {
        table: {"table_digest": table_digests[table], "partitions": states[table]}
        for table in _OWNED_TABLES
    }
    _write_json_atomic(work / "checkpoint.json", checkpoint)
    source_counts: dict[str, dict[str, int]] = {}
    for item in registry:
        value = source_counts.setdefault(
            str(item["mapping_identity"]), {"files": 0, "declared_rows": 0}
        )
        value["files"] += 1
        value["declared_rows"] += int(item["declared_rows"])
    reconciliation = {
        "schema_version": 1,
        "kind": "dwd_market_observation_source_reconciliation",
        "run_id": run_id,
        "source_set_digest": source_set_digest,
        "sources": source_counts,
        "source_key_audit": source_key_audit,
        "historical_code_alias_reconciliation": alias_reconciliation,
        "expected_output_rows": expected_row_counts,
        "output_rows": row_counts,
        "conflicting_business_keys": 0,
        "duplicate_business_keys": 0,
        "unresolved_foreign_keys": 0,
        "accepted_quality_exceptions": len(quality_exceptions),
    }
    receipt = {
        "schema_version": 1,
        "kind": "dwd_market_observation_build_receipt",
        "status": "built",
        **identity,
        "knowledge_cutoff": cutoff.isoformat(),
        "row_counts": row_counts,
        "partition_counts": partition_counts,
        "table_digests": table_digests,
        "semantic_digest": semantic_digest,
        "quality_exceptions": quality_exceptions,
        "historical_code_alias_reconciliation": alias_reconciliation,
    }
    _write_json_atomic(work / "reconciliation/source-row-reconciliation.json", reconciliation)
    _write_json_atomic(work / "receipts/market-observation-build.json", receipt)
    files = {
        "checkpoint.json",
        "reconciliation/source-row-reconciliation.json",
        "receipts/market-observation-build.json",
        *{str(item["path"]) for values in states.values() for item in values},
    }
    manifest = {
        "schema_version": 1,
        "kind": "dwd_market_observation_candidate",
        "run_id": run_id,
        "input_plan_digest": identity["input_plan_digest"],
        "semantic_digest": semantic_digest,
        "owned_tables": list(_OWNED_TABLES),
        "row_counts": row_counts,
        "partition_by": {
            table: list(_PARTITIONED_TABLES.get(table, ())) for table in _OWNED_TABLES
        },
        "files": [_file_record(work, work / relative) for relative in sorted(files)],
    }
    _write_json_atomic(work / "manifest.json", manifest)
    _write_text_atomic(work / "READY", f"{_sha256(work / 'manifest.json')}\n")
    _fsync_directory(work)
    _verify_existing_output(work, **identity)
    work.rename(output)
    _fsync_directory(output.parent)
    return _verify_existing_output(output, **identity)


def build_market_observation(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    trading_calendar_candidate: Path,
    output: Path,
    run_id: str,
    scratch_root: Path | None = None,
) -> MarketObservationBuildResult:
    """Build one immutable, idempotent Issue #58 candidate without publishing it."""

    if output.is_symlink():
        raise MarketObservationBuildError("candidate output path must not be a symbolic link")
    output.parent.mkdir(parents=True, exist_ok=True)
    normalized = output.resolve()
    lock_path = normalized.parent / f".{normalized.name}.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o640)
    except OSError as exc:
        raise MarketObservationBuildError(f"cannot open build lock: {lock_path}") from exc
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise MarketObservationBuildError("build lock must be a physical file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        return _build_locked(
            ods_package=ods_package,
            input_plan=input_plan,
            model_path=model_path,
            listed_company_candidate=listed_company_candidate,
            trading_calendar_candidate=trading_calendar_candidate,
            output=normalized,
            run_id=run_id,
            scratch_root=scratch_root,
        )
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
