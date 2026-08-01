"""Build Issue #58 market-observation DWD objects from one sealed Base plan."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
            if identity not in _SOURCE_IDENTITIES:
                raise MarketObservationBuildError(f"unexpected modeled source: {identity}")
            records.append(raw)
    identities = {record.get("mapping_identity") for record in records}
    if identities != _SOURCE_IDENTITIES:
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
) -> str:
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
    listings = _reference_scan([listing_path])
    calendars = _reference_scan(calendar_paths)
    return f"""
WITH daily_raw AS (
  SELECT b.*, r.relative_path, r.dataset_id, r.mapping_identity, r.source_sha256,
         CAST(CAST(b.date AS VARCHAR) AS DATE) AS trade_date,
         CASE r.mapping_identity
           WHEN 'daily_ohlcv_unadjusted' THEN 30
           WHEN 'daily_ohlcv_none_delta' THEN 20
           ELSE 10 END AS source_priority
  FROM {daily} b JOIN source_registry r ON b.filename=r.absolute_path
), daily_ranked AS (
  SELECT *, row_number() OVER (
    PARTITION BY stock_code, trade_date ORDER BY source_priority DESC, relative_path
  ) AS source_rank
  FROM daily_raw
), minute_raw AS (
  SELECT b.*, r.relative_path, r.dataset_id, r.mapping_identity, r.source_sha256,
         CAST(CAST(b.ts AS VARCHAR) AS TIMESTAMP) AS interval_label,
         CASE WHEN r.mapping_identity IN ('minute_1m_research_archive','minute_1m_hotfix')
              THEN '1m'
              WHEN r.mapping_identity IN ('minute_5m_formal_archive','minute_5m_hotfix')
              THEN '5m'
              ELSE b.bar_size END AS normalized_interval
  FROM {minute} b JOIN source_registry r ON b.filename=r.absolute_path
), source_rows AS (
  SELECT stock_code, trade_date,
         timezone('Asia/Shanghai', CAST(trade_date AS TIMESTAMP)) AS local_day_start,
         NULL::TIMESTAMPTZ AS minute_end,
         '1d' AS interval_size,
         open, high, low, close, volume, amount,
         dataset_id AS record_source,
         relative_path, mapping_identity, source_sha256
  FROM daily_ranked WHERE source_rank=1 AND lower(adj_mode)='none'
  UNION ALL
  SELECT stock_code, CAST(interval_label AS DATE), NULL::TIMESTAMPTZ,
         timezone('Asia/Shanghai', interval_label) AS minute_end,
         normalized_interval, open, high, low, close, volume, amount,
         dataset_id, relative_path, mapping_identity, source_sha256
  FROM minute_raw
), resolved AS (
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
)
SELECT record_source,
       relative_path || '#stock_code=' || stock_code ||
         CASE WHEN interval_size='1d' THEN '&date=' || CAST(trade_date AS VARCHAR)
              ELSE '&ts=' || strftime(
                minute_end AT TIME ZONE 'Asia/Shanghai','%Y-%m-%dT%H:%M:%S'
              ) END
         AS source_record_id,
       mapping_identity || '@' ||
         substr(replace(source_sha256,'sha256:',''),1,16) AS source_revision,
       source_sha256 AS source_artifact_sha256,
       TIMESTAMPTZ {_sql_literal(cutoff)} AS knowledge_from, NULL::TIMESTAMPTZ AS knowledge_to,
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
    registry: Sequence[Mapping[str, Any]], listing_path: Path, cutoff: str
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
    return f"""
WITH raw AS (
  SELECT v.*, r.relative_path, r.dataset_id, r.mapping_identity, r.source_sha256,
         CAST(CAST(v.date AS VARCHAR) AS DATE) AS trade_date,
         CASE r.mapping_identity WHEN 'valuation_daily' THEN 40
              WHEN 'valuation_history_july_repair' THEN 30
              WHEN 'valuation_daily_delta' THEN 20 ELSE 10 END AS source_priority
  FROM {sources} v JOIN source_registry r ON v.filename=r.absolute_path
), ranked AS (
  SELECT *, row_number() OVER (
    PARTITION BY stock_code, trade_date ORDER BY source_priority DESC, relative_path
  ) AS source_rank
  FROM raw
), resolved AS (
  SELECT r.*, l.security_id, l.listing_id,
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
), measures AS (
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
       observation_start, NULL::TIMESTAMPTZ AS observation_end, security_id, listing_id,
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
    raw_files = (
        [raw_root]
        if table == "dwd_market_index"
        else sorted(raw_root.rglob("*.parquet"))
    )
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
                int(path.stem.rsplit("-", 1)[-1])
                if path.stem.rsplit("-", 1)[-1].isdigit()
                else 0,
                path.name,
            ),
        )
        final = (
            partition_files[0]
            if table == "dwd_market_index"
            else parent / "part-00000.parquet"
        )
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


def _audit_outputs(
    connection: Any,
    work: Path,
    states: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    listing_path: Path,
    index_path: Path,
    calendar_paths: Sequence[Path],
) -> None:
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
                    high < greatest(open,close,low) OR low > least(open,close,high) OR
                    volume < 0 OR turnover < 0 OR observation_end <= observation_start""",
                    [str(path)],
                ).fetchone()[0]
                if invalid:
                    raise MarketObservationBuildError(f"{table} has {invalid} invalid bars")
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


def _expected_row_counts(
    connection: Any, registry: Sequence[Mapping[str, Any]]
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
    conflicting = sum(
        value["conflicting_business_keys"] for value in source_key_audit.values()
    )
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
        ),
        "dwd_security_valuation": int(
            source_key_audit["valuation"]["selected_business_keys"] * 6
        ),
        "dwd_market_index": 3,
        "dwd_index_bar": source_key_audit["index_bar"]["selected_business_keys"],
    }
    return expected, source_key_audit


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
                or partition.get("schema_digest")
                != _canonical_digest(str(schemas[table]))
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
        path = base / (
            "dwd_market_index.parquet" if table == "dwd_market_index" else table
        )
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


def _build_locked(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    trading_calendar_candidate: Path,
    output: Path,
    run_id: str,
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
    scratch = output.parent / f".{output.name}.{build_identity}.duckdb-tmp"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    connection = duckdb.connect()
    connection.execute("SET threads=1")
    connection.execute("SET preserve_insertion_order=true")
    connection.execute("SET enable_progress_bar=false")
    connection.execute("SET memory_limit='1400MB'")
    connection.execute(f"SET temp_directory={_sql_literal(str(scratch))}")
    _register_sources(connection, registry)
    listing_path = listed.root / "payload/listed_company/dwd_listing.parquet"
    venue_path = listed.root / "payload/listed_company/dwd_trading_venue.parquet"
    calendar_paths = sorted(
        calendar.root.glob(
            "payload/tradability_state/dwd_trading_calendar/trade_date_year=*/part-00000.parquet"
        )
    )
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
            states["dwd_market_index"] = _normalize_outputs(
                "dwd_market_index", index_path, work
            )
            checkpoint["completed_tables"] = {
                name: {
                    "table_digest": _table_digest(partitions),
                    "partitions": partitions,
                }
                for name, partitions in states.items()
            }
            _write_json_atomic(work / "checkpoint.json", checkpoint)

        table_specs = (
            (
                "dwd_index_bar",
                _index_bar_query(
                    registry, index_path, venue_path, calendar_paths, cutoff.isoformat()
                ),
            ),
            (
                "dwd_security_bar",
                _bar_query(registry, listing_path, calendar_paths, cutoff.isoformat()),
            ),
            (
                "dwd_security_valuation",
                _valuation_query(registry, listing_path, cutoff.isoformat()),
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
        _audit_outputs(
            connection,
            work,
            states,
            listing_path=listing_path,
            index_path=index_path,
            calendar_paths=calendar_paths,
        )
        expected_row_counts, source_key_audit = _expected_row_counts(
            connection, registry
        )
        actual_row_counts = {
            table: sum(int(item["row_count"]) for item in states[table])
            for table in _OWNED_TABLES
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
        table: sum(int(item["row_count"]) for item in states[table])
        for table in _OWNED_TABLES
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
        "expected_output_rows": expected_row_counts,
        "output_rows": row_counts,
        "conflicting_business_keys": 0,
        "duplicate_business_keys": 0,
        "unresolved_foreign_keys": 0,
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
        "quality_exceptions": [],
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
        )
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
