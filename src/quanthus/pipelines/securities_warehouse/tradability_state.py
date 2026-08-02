"""Build Issue #59 governed tradability-state DWD objects."""

# ruff: noqa: E501

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quanthus.pipelines.securities_warehouse.listed_company import (
    ListedCompanyBuildError,
    verify_listed_company_candidate,
)
from quanthus.pipelines.securities_warehouse.market_observation import (
    MarketObservationBuildError,
    verify_market_observation_candidate,
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


class TradabilityStateBuildError(RuntimeError):
    """Raised when Issue #59 cannot be built without ambiguity."""


@dataclass(frozen=True)
class TradabilityStateBuildResult:
    root: Path
    semantic_digest: str
    row_counts: dict[str, int]
    partition_counts: dict[str, int]


@dataclass(frozen=True)
class TradabilityStateCandidateVerification:
    root: Path
    manifest_sha256: str
    semantic_digest: str
    row_counts: dict[str, int]
    partition_counts: dict[str, int]


_OWNED_TABLES = (
    "dwd_security_status_interval",
    "dwd_listing_status_interval",
    "dwd_price_limit_regime",
    "dwd_tradability_state",
    "dwd_observation_gap",
)
_PARTITIONED_TABLES = {
    "dwd_tradability_state": (
        "observation_start_year",
        "observation_start_month",
    ),
    "dwd_observation_gap": (
        "affected_effective_at_year",
        "affected_effective_at_month",
    ),
}
_SOURCE_IDENTITIES = {
    "daily_status_observation_delta",
    "daily_status_observation_hotfix",
    "delist_boundary_exceptions",
    "price_limit_regime",
    "source_evidence_tradability_c2_s1",
    "st_status_observation_revision",
    "st_status_pit",
    "suspensions",
    "suspensions_delta",
}
_LINEAGE_NAMES = (
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
        raise TradabilityStateBuildError(
            "duckdb and pyarrow are required for the tradability-state build"
        ) from exc
    return duckdb, pa, pq


def _schemas() -> dict[str, Any]:
    _, pa, _ = _dependencies()
    required = False
    optional = True
    lineage = [
        pa.field("record_source", pa.string(), required),
        pa.field("source_record_id", pa.string(), required),
        pa.field("source_revision", pa.string(), required),
        pa.field("source_artifact_sha256", pa.string(), required),
        pa.field("knowledge_from", pa.timestamp("us", tz="UTC"), required),
        pa.field("knowledge_to", pa.timestamp("us", tz="UTC"), optional),
    ]
    effective = [
        pa.field("effective_from", pa.timestamp("us", tz="UTC"), required),
        pa.field("effective_to", pa.timestamp("us", tz="UTC"), optional),
    ]
    observation = [
        pa.field("observation_start", pa.timestamp("us", tz="UTC"), required),
        pa.field("observation_end", pa.timestamp("us", tz="UTC"), optional),
    ]
    governance = [
        pa.field("affected_effective_at", pa.timestamp("us", tz="UTC"), required),
        pa.field("detected_at", pa.timestamp("us", tz="UTC"), required),
    ]
    return {
        "dwd_security_status_interval": pa.schema(
            lineage
            + effective
            + [
                pa.field("security_status_id", pa.string(), required),
                pa.field("security_id", pa.string(), required),
                pa.field("status_type", pa.string(), required),
                pa.field("status_value", pa.string(), required),
                pa.field("reason_code", pa.string(), optional),
            ]
        ),
        "dwd_listing_status_interval": pa.schema(
            lineage
            + effective
            + [
                pa.field("listing_status_id", pa.string(), required),
                pa.field("listing_id", pa.string(), required),
                pa.field("status_type", pa.string(), required),
                pa.field("status_value", pa.string(), required),
                pa.field("reason_code", pa.string(), optional),
            ]
        ),
        "dwd_price_limit_regime": pa.schema(
            lineage
            + effective
            + [
                pa.field("price_limit_regime_id", pa.string(), required),
                pa.field("venue_id", pa.string(), required),
                pa.field("board_code", pa.string(), optional),
                pa.field("security_status", pa.string(), optional),
                pa.field("ipo_phase", pa.string(), optional),
                pa.field("priority", pa.int32(), required),
                pa.field("listing_id", pa.string(), optional),
                pa.field("regime_type", pa.string(), required),
                pa.field("upper_limit_ratio", pa.decimal128(18, 12), optional),
                pa.field("lower_limit_ratio", pa.decimal128(18, 12), optional),
                pa.field("reference_price_method", pa.string(), required),
            ]
        ),
        "dwd_tradability_state": pa.schema(
            lineage
            + observation
            + [
                pa.field("listing_id", pa.string(), required),
                pa.field("calendar_session_id", pa.string(), required),
                pa.field("is_tradable", pa.bool_(), required),
                pa.field("can_buy", pa.bool_(), required),
                pa.field("can_sell", pa.bool_(), required),
                pa.field("reason_code", pa.string(), required),
                pa.field("upper_price", pa.decimal128(38, 10), optional),
                pa.field("lower_price", pa.decimal128(38, 10), optional),
                pa.field("input_revision_digest", pa.string(), required),
                pa.field("observation_start_year", pa.string(), required),
                pa.field("observation_start_month", pa.string(), required),
            ]
        ),
        "dwd_observation_gap": pa.schema(
            lineage
            + governance
            + [
                pa.field("gap_id", pa.string(), required),
                pa.field("listing_id", pa.string(), required),
                pa.field("calendar_session_id", pa.string(), required),
                pa.field("interval_size", pa.string(), required),
                pa.field("gap_reason", pa.string(), required),
                pa.field("is_expected", pa.bool_(), required),
                pa.field("evidence_digest", pa.string(), required),
                pa.field("affected_effective_at_year", pa.string(), required),
                pa.field("affected_effective_at_month", pa.string(), required),
            ]
        ),
    }


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _stable_id(kind: str, *parts: str) -> str:
    payload = "\x1f".join(("securities-warehouse-v1", kind, *parts)).encode()
    return f"{kind}_{hashlib.sha256(payload).hexdigest()[:32]}"


def _table_contract(model: Mapping[str, Any], table: str) -> Mapping[str, Any]:
    tables = model.get("tables")
    if not isinstance(tables, list):
        raise TradabilityStateBuildError("model tables must be an array")
    matches = [row for row in tables if isinstance(row, Mapping) and row.get("name") == table]
    if len(matches) != 1:
        raise TradabilityStateBuildError(f"model must define exactly one {table}")
    return matches[0]


def _verify_model_contract(model: Mapping[str, Any]) -> None:
    execution = _mapping(model.get("build_execution_contract"), "build execution contract")
    ownership = _mapping(execution.get("dwd_script_ownership"), "DWD ownership")
    if ownership.get("59") != list(_OWNED_TABLES):
        raise TradabilityStateBuildError("model Issue #59 ownership differs")
    schemas = _schemas()
    for table in _OWNED_TABLES:
        contract = _table_contract(model, table)
        columns = _mapping(contract.get("columns"), f"{table} columns")
        names = set(_LINEAGE_NAMES) | set(columns)
        profile = contract.get("time_profile")
        if profile == "effective_interval":
            names |= {"effective_from", "effective_to"}
        elif profile == "market_observation":
            names |= {"observation_start", "observation_end"}
        elif profile == "governance_event":
            names |= {"affected_effective_at", "detected_at"}
        names |= set(contract.get("partition_by", []))
        if names != set(schemas[table].names):
            raise TradabilityStateBuildError(f"physical schema differs from model: {table}")


def _source_records(ledger: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records = ledger.get("records")
    if not isinstance(records, list):
        raise TradabilityStateBuildError("source ledger records must be an array")
    selected = [
        row
        for row in records
        if isinstance(row, Mapping)
        and row.get("mapping_identity") in _SOURCE_IDENTITIES
        and row.get("disposition") == "modeled"
        and row.get("format") == "parquet"
    ]
    present = {str(row.get("mapping_identity")) for row in selected}
    if present != _SOURCE_IDENTITIES:
        missing = sorted(_SOURCE_IDENTITIES - present)
        raise TradabilityStateBuildError(f"required source identities differ: {missing}")
    return selected


def _verify_sources(
    records: Sequence[Mapping[str, Any]], ods_root: Path
) -> tuple[dict[str, list[dict[str, Any]]], str]:
    registry: dict[str, list[dict[str, Any]]] = {}
    identities = []
    for record in records:
        try:
            path = _safe_file(ods_root, record.get("path"))
        except TradingCalendarBuildError as exc:
            raise TradabilityStateBuildError(str(exc)) from exc
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != record.get("size_bytes")
            or _sha256(path) != record.get("sha256")
        ):
            raise TradabilityStateBuildError(f"source failed verification: {path}")
        item = dict(record)
        item["physical_path"] = str(path)
        registry.setdefault(str(record["mapping_identity"]), []).append(item)
        identities.append(
            {
                "mapping_identity": record["mapping_identity"],
                "path": record["path"],
                "sha256": record["sha256"],
                "row_count": record.get("row_count"),
            }
        )
    for values in registry.values():
        values.sort(key=lambda row: str(row["path"]))
    return registry, _canonical_digest(sorted(identities, key=lambda row: str(row["path"])))


def _register_sources(connection: Any, registry: Mapping[str, list[dict[str, Any]]]) -> None:
    for identity, records in registry.items():
        selects = []
        for record in records:
            selects.append(
                "SELECT *, "
                f"{_sql_literal(str(record['dataset_id']))} AS __dataset_id, "
                f"{_sql_literal(str(record['path']))} AS __relative_path, "
                f"{_sql_literal(str(record['sha256']))} AS __source_sha256 "
                f"FROM read_parquet({_sql_literal(str(record['physical_path']))})"
            )
        connection.execute(f"CREATE VIEW src_{identity} AS " + " UNION ALL BY NAME ".join(selects))


def _read_parquet_sql(paths: Sequence[Path]) -> str:
    if not paths:
        raise TradabilityStateBuildError("candidate predecessor has no parquet parts")
    return (
        f"read_parquet([{','.join(_sql_literal(str(path)) for path in paths)}], union_by_name=true)"
    )


def _utc_date_sql(expression: str) -> str:
    return f"timezone('Asia/Shanghai', CAST({expression} AS TIMESTAMP))"


def _prepare_relations(
    connection: Any,
    *,
    listed_root: Path,
    calendar_root: Path,
    market_root: Path,
    cutoff: str,
) -> None:
    listing_path = listed_root / "payload/listed_company/dwd_listing.parquet"
    security_path = listed_root / "payload/listed_company/dwd_security.parquet"
    venue_path = listed_root / "payload/listed_company/dwd_trading_venue.parquet"
    calendar_paths = sorted(
        calendar_root.glob(
            "payload/tradability_state/dwd_trading_calendar/trade_date_year=*/part-00000.parquet"
        )
    )
    bar_paths = sorted(
        market_root.glob(
            "payload/security_market_observation/dwd_security_bar/interval_size=*/"
            "observation_start_year=*/observation_start_month=*/part-00000.parquet"
        )
    )
    connection.execute(
        f"CREATE VIEW ref_listing AS SELECT * FROM read_parquet({_sql_literal(str(listing_path))})"
    )
    connection.execute(
        f"CREATE VIEW ref_security AS SELECT * FROM read_parquet({_sql_literal(str(security_path))})"
    )
    connection.execute(
        f"CREATE VIEW ref_venue AS SELECT * FROM read_parquet({_sql_literal(str(venue_path))})"
    )
    connection.execute(
        f"CREATE VIEW ref_calendar AS SELECT * FROM {_read_parquet_sql(calendar_paths)}"
    )
    connection.execute(f"CREATE VIEW ref_bar AS SELECT * FROM {_read_parquet_sql(bar_paths)}")
    connection.execute(
        """
        CREATE VIEW code_listing_resolution AS
        SELECT c.listing_code AS source_code,l.*
        FROM (SELECT listing_code,security_id FROM ref_listing GROUP BY 1,2) c
        JOIN ref_listing l USING (security_id)
        """
    )
    connection.execute(
        f"CREATE OR REPLACE MACRO build_cutoff() AS TIMESTAMPTZ {_sql_literal(cutoff)}"
    )


def _daily_status_relation_query() -> str:
    return """
WITH raw AS (
  SELECT stock_code, try_cast(date AS DATE) AS status_date,
         CAST(isST AS VARCHAR) AS is_st, CAST(tradestatus AS VARCHAR) AS trade_status,
         __dataset_id, __relative_path, __source_sha256, 30 AS source_priority
  FROM src_daily_status_observation_delta
  UNION ALL BY NAME
  SELECT stock_code, try_cast(date AS DATE) AS status_date,
         CAST(isST AS VARCHAR) AS is_st, CAST(tradestatus AS VARCHAR) AS trade_status,
         __dataset_id, __relative_path, __source_sha256, 20 AS source_priority
  FROM src_st_status_observation_revision
  UNION ALL BY NAME
  SELECT stock_code, try_cast(coalesce(market_date,date) AS DATE) AS status_date,
         CAST(isST AS VARCHAR) AS is_st, CAST(tradestatus AS VARCHAR) AS trade_status,
         __dataset_id, __relative_path, __source_sha256, 10 AS source_priority
  FROM src_daily_status_observation_hotfix
), valid AS (
  SELECT * FROM raw
  WHERE status_date IS NOT NULL AND stock_code IS NOT NULL
    AND is_st IN ('0','1') AND trade_status IN ('0','1')
), ranked AS (
  SELECT *, row_number() OVER (
    PARTITION BY stock_code,status_date
    ORDER BY source_priority DESC,__relative_path
  ) AS source_rank
  FROM valid
)
SELECT * EXCLUDE(source_rank) FROM ranked WHERE source_rank=1
"""


def _daily_status_mapped_query() -> str:
    return f"""
SELECT d.*,r.security_id,r.listing_id,r.effective_to AS listing_effective_to,
       row_number() OVER (
         PARTITION BY d.stock_code,d.status_date
         ORDER BY CASE WHEN r.listing_code=d.stock_code THEN 0 ELSE 1 END,
                  r.effective_from DESC
       ) AS mapping_rank,
       count(*) OVER (PARTITION BY d.stock_code,d.status_date) AS mapping_count
FROM daily_status_resolved d
JOIN code_listing_resolution r ON r.source_code=d.stock_code
 AND {_utc_date_sql("d.status_date")} >= r.effective_from
 AND (r.effective_to IS NULL OR {_utc_date_sql("d.status_date")} < r.effective_to)
"""


def _security_status_query() -> str:
    cutoff = "build_cutoff()"
    return f"""
WITH canonical AS (
  SELECT s.*, l.security_id, l.listing_id,
         {_utc_date_sql("s.effective_start")} AS start_at,
         CASE WHEN s.effective_end IS NULL THEN NULL
              ELSE {_utc_date_sql("s.effective_end + INTERVAL '1 day'")} END AS end_at,
         row_number() OVER (
           PARTITION BY s.interval_id
           ORDER BY CASE WHEN l.listing_code=s.stock_code THEN 0 ELSE 1 END,
                    l.effective_from DESC
         ) AS mapping_rank,
         count(*) OVER (PARTITION BY s.interval_id) AS mapping_count
  FROM src_st_status_pit s
  JOIN code_listing_resolution l ON l.source_code=s.stock_code
   AND l.venue_id=(SELECT venue_id FROM ref_venue WHERE mic_code=
        CASE s.exchange WHEN 'SSE' THEN 'XSHG' WHEN 'SZSE' THEN 'XSHE' ELSE 'BJSE' END)
   AND {_utc_date_sql("s.effective_start")} >= l.effective_from
   AND (l.effective_to IS NULL OR {_utc_date_sql("s.effective_start")} < l.effective_to)
), daily_changes AS (
  SELECT *, CASE WHEN lag(is_st) OVER w=is_st AND lag(trade_status) OVER w=trade_status
                  THEN 0 ELSE 1 END AS changed
  FROM daily_status_mapped WHERE mapping_rank=1
  WINDOW w AS (PARTITION BY security_id ORDER BY status_date)
), daily_groups AS (
  SELECT *, sum(changed) OVER (
    PARTITION BY security_id ORDER BY status_date ROWS UNBOUNDED PRECEDING
  ) AS status_group
  FROM daily_changes
), provider AS (
  SELECT security_id, status_group, min(status_date) AS start_date,
         lead(min(status_date)) OVER (PARTITION BY security_id ORDER BY min(status_date)) AS next_date,
         any_value(is_st) AS is_st, any_value(trade_status) AS trade_status,
         arg_min(__dataset_id,status_date) AS dataset_id,
         arg_min(__relative_path,status_date) AS relative_path,
         arg_min(__source_sha256,status_date) AS source_sha256,
         max(listing_effective_to) AS listing_effective_to
  FROM daily_groups GROUP BY security_id,status_group
), rows AS (
  SELECT 'st_status_pit' AS record_source,
         __relative_path || '#interval_id=' || interval_id AS source_record_id,
         coalesce(source_revision,'st-status-pit') AS source_revision,
         __source_sha256 AS source_artifact_sha256,
         {cutoff} AS knowledge_from, NULL::TIMESTAMPTZ AS knowledge_to,
         start_at AS effective_from, end_at AS effective_to,
         'security_status_' || substr(sha256('SPECIAL_TREATMENT' || chr(31) || security_id || chr(31) || CAST(start_at AS VARCHAR)),1,32) AS security_status_id,
         security_id, 'SPECIAL_TREATMENT' AS status_type,
         CASE WHEN status_label='unknown' THEN 'UNKNOWN'
              WHEN is_st=1 THEN upper(status_label) ELSE 'NORMAL' END AS status_value,
         CASE WHEN confidence='not_proven' THEN 'BASELINE_NOT_PROVEN' ELSE event_kind END AS reason_code
  FROM canonical WHERE mapping_rank=1
  UNION ALL BY NAME
  SELECT dataset_id AS record_source,
         relative_path || '#security_id=' || security_id || '&start=' || CAST(start_date AS VARCHAR) AS source_record_id,
         'provider-daily-status-v1' AS source_revision,
         source_sha256 AS source_artifact_sha256,
         {cutoff} AS knowledge_from, NULL::TIMESTAMPTZ AS knowledge_to,
         {_utc_date_sql("start_date")} AS effective_from,
         coalesce({_utc_date_sql("next_date")}, listing_effective_to) AS effective_to,
         'security_status_' || substr(sha256('PROVIDER_DAILY_STATE' || chr(31) || security_id || chr(31) || CAST(start_date AS VARCHAR)),1,32) AS security_status_id,
         security_id, 'PROVIDER_DAILY_STATE' AS status_type,
         CASE WHEN is_st='1' THEN 'ST' ELSE 'NORMAL' END AS status_value,
         CASE WHEN trade_status='0' THEN 'PROVIDER_NOT_TRADING' ELSE 'PROVIDER_TRADING' END AS reason_code
  FROM provider
)
SELECT * FROM rows ORDER BY security_id,status_type,effective_from,knowledge_from
"""


def _listing_status_query() -> str:
    cutoff = "build_cutoff()"
    return f"""
WITH suspension_raw AS (
  SELECT * REPLACE(
    try_cast(suspend_start AS DATE) AS suspend_start,
    try_cast(suspend_end AS DATE) AS suspend_end,
    try_cast(knowledge_cutoff AS DATE) AS knowledge_cutoff
  ), 20 AS source_priority FROM src_suspensions_delta
  UNION ALL BY NAME
  SELECT * REPLACE(
    try_cast(suspend_start AS DATE) AS suspend_start,
    try_cast(suspend_end AS DATE) AS suspend_end,
    try_cast(knowledge_cutoff AS DATE) AS knowledge_cutoff
  ), 10 AS source_priority FROM src_suspensions
), suspension_ranked AS (
  SELECT *, row_number() OVER (
    PARTITION BY suspension_id ORDER BY source_priority DESC,__relative_path
  ) AS source_rank
  FROM suspension_raw
), suspension_mapped AS (
  SELECT s.*, l.listing_id,
         row_number() OVER (
           PARTITION BY s.suspension_id
           ORDER BY CASE WHEN l.listing_code=s.stock_code THEN 0 ELSE 1 END,
                    l.effective_from DESC
         ) AS mapping_rank,
         count(*) OVER (PARTITION BY s.suspension_id) AS mapping_count
  FROM suspension_ranked s
  JOIN code_listing_resolution l ON l.source_code=s.stock_code
   AND {_utc_date_sql("s.suspend_start")} >= l.effective_from
   AND (l.effective_to IS NULL OR {_utc_date_sql("s.suspend_start")} < l.effective_to)
  WHERE source_rank=1
), rows AS (
  SELECT 'dwd_listing' AS record_source,
         source_record_id AS source_record_id,
         source_revision, source_artifact_sha256,
         {cutoff} AS knowledge_from, NULL::TIMESTAMPTZ AS knowledge_to,
         effective_from, effective_to,
         'listing_status_' || substr(sha256('LISTING_LIFECYCLE' || chr(31) || listing_id || chr(31) || CAST(effective_from AS VARCHAR)),1,32) AS listing_status_id,
         listing_id, 'LISTING_LIFECYCLE' AS status_type, 'LISTED' AS status_value,
         'ACCEPTED_LISTING_INTERVAL' AS reason_code
  FROM ref_listing
  UNION ALL BY NAME
  SELECT __dataset_id AS record_source,
         __relative_path || '#suspension_id=' || suspension_id AS source_record_id,
         coalesce(CAST(knowledge_cutoff AS VARCHAR),'suspension-source-v1') AS source_revision,
         __source_sha256 AS source_artifact_sha256,
         {cutoff} AS knowledge_from, NULL::TIMESTAMPTZ AS knowledge_to,
         {_utc_date_sql("suspend_start")} AS effective_from,
         CASE WHEN suspend_end IS NULL THEN NULL
              ELSE {_utc_date_sql("suspend_end + INTERVAL '1 day'")} END AS effective_to,
         'listing_status_' || substr(sha256('SUSPENSION' || chr(31) || listing_id || chr(31) || CAST(suspend_start AS VARCHAR)),1,32) AS listing_status_id,
         listing_id, 'SUSPENSION' AS status_type, 'SUSPENDED' AS status_value,
         coalesce(nullif(reason,''),'SOURCE_REPORTED_SUSPENSION') AS reason_code
  FROM suspension_mapped WHERE mapping_rank=1
)
SELECT * FROM rows ORDER BY listing_id,status_type,effective_from,knowledge_from
"""


def _price_regime_internal_query() -> str:
    cutoff = "build_cutoff()"
    return f"""
WITH mapped AS (
  SELECT r.*, v.venue_id, l.listing_id,
         row_number() OVER (
           PARTITION BY r.interval_id ORDER BY l.effective_from DESC NULLS LAST
         ) AS mapping_rank,
         count(l.listing_id) OVER (PARTITION BY r.interval_id) AS listing_matches
  FROM src_price_limit_regime r
  JOIN ref_venue v ON v.mic_code=CASE r.exchange
    WHEN 'SSE' THEN 'XSHG' WHEN 'SZSE' THEN 'XSHE' ELSE 'BJSE' END
  LEFT JOIN code_listing_resolution l ON r.stock_code IS NOT NULL
   AND l.source_code=r.stock_code AND l.venue_id=v.venue_id
   AND {_utc_date_sql("r.effective_start")} >= l.effective_from
   AND (l.effective_to IS NULL OR {_utc_date_sql("r.effective_start")} < l.effective_to)
), normalized AS (
  SELECT *,
    CASE
      WHEN rule_basis='ST' AND board='ST_overlay_ChiNext' THEN 'ChiNext'
      WHEN rule_basis='ST' THEN NULL
      ELSE board
    END AS normalized_board,
    CASE WHEN rule_basis='ST' THEN 'ST' ELSE NULL END AS normalized_status,
    CASE WHEN rule_basis='IPO_window' THEN 'INITIAL_LISTING_WINDOW' ELSE NULL END AS normalized_ipo,
    {_utc_date_sql("effective_start")} AS start_at,
    CASE WHEN effective_end IS NULL THEN NULL
         ELSE {_utc_date_sql("effective_end + INTERVAL '1 day'")} END AS end_at
  FROM mapped WHERE mapping_rank=1
)
SELECT __dataset_id AS record_source,
       __relative_path || '#interval_id=' || interval_id AS source_record_id,
       coalesce(source_revision,'price-limit-regime-v1') AS source_revision,
       __source_sha256 AS source_artifact_sha256,
       {cutoff} AS knowledge_from, NULL::TIMESTAMPTZ AS knowledge_to,
       start_at AS effective_from, end_at AS effective_to,
       'price_limit_regime_' || substr(sha256(interval_id),1,32) AS price_limit_regime_id,
       venue_id, normalized_board AS board_code,
       normalized_status AS security_status, normalized_ipo AS ipo_phase,
       CAST(priority AS INTEGER) AS priority, listing_id,
       CASE WHEN upper_limit_pct IS NULL AND lower_limit_pct IS NULL
            THEN 'NO_LIMIT' ELSE 'PERCENT_LIMIT' END AS regime_type,
       CAST(upper_limit_pct / 100.0 AS DECIMAL(18,12)) AS upper_limit_ratio,
       CAST(lower_limit_pct / 100.0 AS DECIMAL(18,12)) AS lower_limit_ratio,
       'PREVIOUS_CLOSE_' || upper(replace(coalesce(rounding_rule,'ROUND_TO_TICK'),'-','_')) AS reference_price_method,
       CAST(coalesce(no_limit_days,0) AS INTEGER) AS no_limit_days,
       rule_basis,stock_code AS source_stock_code,listing_matches
FROM normalized
"""


def _price_regime_output_query() -> str:
    return """
SELECT * EXCLUDE(no_limit_days,rule_basis,source_stock_code,listing_matches)
FROM price_regime_internal
ORDER BY venue_id,priority,price_limit_regime_id,effective_from
"""


def _state_base_query() -> str:
    return """
WITH listing_sessions AS (
  SELECT l.listing_id,l.security_id,l.venue_id,l.listing_code,l.board_code,
         c.calendar_session_id,c.trade_date,c.open_at,c.close_at,
         row_number() OVER (PARTITION BY l.listing_id ORDER BY c.trade_date) AS listing_day_number
  FROM ref_listing l JOIN ref_calendar c ON c.venue_id=l.venue_id
   AND c.session_type='REGULAR_DAY' AND c.is_open
   AND c.open_at >= l.effective_from
   AND (l.effective_to IS NULL OR c.open_at < l.effective_to)
), daily_observed AS (
  SELECT listing_id,calendar_session_id,close,source_revision,source_artifact_sha256
  FROM ref_bar WHERE interval_size='1d'
), enriched AS (
  SELECT s.*, b.close AS observed_close,
         b.source_revision AS bar_revision,
         b.source_artifact_sha256 AS bar_sha256,
         cs.security_status_id AS canonical_status_id,
         cs.status_value AS canonical_status_value,
         pd.is_st AS provider_is_st,
         pd.trade_status AS provider_trade_status,
         pd.__source_sha256 AS provider_status_sha256,
         sp.listing_status_id AS suspension_status_id,
         last_value(b.close IGNORE NULLS) OVER (
           PARTITION BY s.listing_id ORDER BY s.trade_date
           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
         ) AS previous_close
  FROM listing_sessions s
  LEFT JOIN daily_observed b USING (listing_id,calendar_session_id)
  LEFT JOIN LATERAL (
    SELECT security_status_id,status_value FROM security_status_rows x
    WHERE x.security_id=s.security_id AND x.status_type='SPECIAL_TREATMENT'
      AND s.open_at >= x.effective_from
      AND (x.effective_to IS NULL OR s.open_at < x.effective_to)
    ORDER BY CASE WHEN x.status_value='UNKNOWN' THEN 1 ELSE 0 END,
             x.effective_from DESC LIMIT 1
  ) cs ON true
  LEFT JOIN LATERAL (
    SELECT d.is_st,d.trade_status,d.__source_sha256
    FROM daily_status_mapped d
    WHERE d.listing_id=s.listing_id AND d.status_date=s.trade_date
      AND d.mapping_rank=1
    ORDER BY d.source_priority DESC LIMIT 1
  ) pd ON true
  LEFT JOIN LATERAL (
    SELECT listing_status_id FROM listing_status_rows x
    WHERE x.listing_id=s.listing_id AND x.status_type='SUSPENSION'
      AND s.open_at >= x.effective_from
      AND (x.effective_to IS NULL OR s.open_at < x.effective_to)
    ORDER BY x.effective_from DESC LIMIT 1
  ) sp ON true
), classified AS (
  SELECT *, CASE
    WHEN canonical_status_value IN ('ST','DELISTING_RISK') THEN 'ST'
    WHEN canonical_status_value='NORMAL' THEN 'NORMAL'
    WHEN provider_is_st='1' THEN 'ST'
    ELSE 'NORMAL' END AS resolved_security_status
  FROM enriched
)
SELECT * FROM classified
"""


def _state_rule_candidates_query() -> str:
    return """
SELECT s.*, r.price_limit_regime_id,r.priority,r.regime_type,
       r.upper_limit_ratio,r.lower_limit_ratio,r.reference_price_method,
       r.source_revision AS regime_revision,
       r.source_artifact_sha256 AS regime_sha256,
       min(r.priority) OVER (PARTITION BY s.listing_id,s.calendar_session_id) AS best_priority
FROM state_base s
LEFT JOIN price_regime_internal r ON r.venue_id=s.venue_id
 AND s.open_at >= r.effective_from
 AND (r.effective_to IS NULL OR s.open_at < r.effective_to)
 AND (r.listing_id IS NULL OR r.listing_id=s.listing_id)
 AND (r.board_code IS NULL OR r.board_code=s.board_code)
 AND (r.security_status IS NULL OR r.security_status=s.resolved_security_status)
 AND (r.ipo_phase IS NULL OR (
       r.ipo_phase='INITIAL_LISTING_WINDOW'
       AND r.listing_id=s.listing_id
       AND s.listing_day_number <= r.no_limit_days
 ))
"""


def _state_resolved_query() -> str:
    return """
SELECT * EXCLUDE(candidate_rank) FROM (
  SELECT *, row_number() OVER (
    PARTITION BY listing_id,calendar_session_id
    ORDER BY priority ASC NULLS LAST, price_limit_regime_id
  ) AS candidate_rank
  FROM state_rule_candidates
  WHERE price_limit_regime_id IS NULL OR priority=best_priority
) WHERE candidate_rank=1
"""


def _tradability_state_query(*, predecessor_digest: str, market_manifest_sha256: str) -> str:
    predecessor = _sql_literal(predecessor_digest)
    artifact = _sql_literal(market_manifest_sha256)
    return f"""
SELECT 'governed_tradability_state_v1' AS record_source,
       'listing_id=' || listing_id || '&calendar_session_id=' || calendar_session_id AS source_record_id,
       {predecessor} AS source_revision,
       {artifact} AS source_artifact_sha256,
       build_cutoff() AS knowledge_from, NULL::TIMESTAMPTZ AS knowledge_to,
       open_at AS observation_start, close_at AS observation_end,
       listing_id,calendar_session_id,
       CASE WHEN suspension_status_id IS NOT NULL OR provider_trade_status='0'
            THEN false ELSE true END AS is_tradable,
       CASE WHEN suspension_status_id IS NOT NULL OR provider_trade_status='0'
            THEN false ELSE true END AS can_buy,
       CASE WHEN suspension_status_id IS NOT NULL OR provider_trade_status='0'
            THEN false ELSE true END AS can_sell,
       CASE
         WHEN suspension_status_id IS NOT NULL THEN 'SUSPENDED'
         WHEN provider_trade_status='0' THEN 'PROVIDER_NOT_TRADING'
         WHEN observed_close IS NULL THEN 'TRADABLE_OBSERVATION_MISSING'
         ELSE 'TRADABLE_OBSERVED'
       END AS reason_code,
       CASE WHEN previous_close IS NULL OR upper_limit_ratio IS NULL
                 OR suspension_status_id IS NOT NULL OR provider_trade_status='0'
            THEN NULL
            ELSE CAST(round(previous_close * (1 + upper_limit_ratio),2) AS DECIMAL(38,10))
       END AS upper_price,
       CASE WHEN previous_close IS NULL OR lower_limit_ratio IS NULL
                 OR suspension_status_id IS NOT NULL OR provider_trade_status='0'
            THEN NULL
            ELSE CAST(round(previous_close * (1 - lower_limit_ratio),2) AS DECIMAL(38,10))
       END AS lower_price,
       'sha256:' || sha256(concat_ws(chr(31),
          {predecessor},listing_id,calendar_session_id,
          coalesce(canonical_status_id,''),coalesce(provider_status_sha256,''),
          coalesce(suspension_status_id,''),coalesce(price_limit_regime_id,''),
          coalesce(bar_sha256,''))) AS input_revision_digest,
       strftime(open_at,'%Y') AS observation_start_year,
       strftime(open_at,'%m') AS observation_start_month
FROM state_resolved
ORDER BY observation_start,listing_id
"""


def _observed_session_query() -> str:
    return """
SELECT listing_id,calendar_session_id,interval_size,
       min(observation_start) AS first_observation,
       max(observation_start) AS last_observation,
       count(*) AS observation_rows
FROM ref_bar
WHERE interval_size IN ('1d','1m','5m')
GROUP BY listing_id,calendar_session_id,interval_size
"""


def _coverage_bounds_query() -> str:
    return """
SELECT listing_id,interval_size,
       min(c.open_at) AS coverage_from,max(c.open_at) AS coverage_to
FROM observed_session o JOIN ref_calendar c USING (calendar_session_id)
GROUP BY listing_id,interval_size
"""


def _gap_evidence_query() -> str:
    return f"""
WITH source_rows AS (
  SELECT stock_code,trade_date,status,reason,__source_sha256,__relative_path
  FROM src_source_evidence_tradability_c2_s1
), mapped AS (
  SELECT e.*,l.listing_id,
         row_number() OVER (
           PARTITION BY e.stock_code,e.trade_date ORDER BY l.effective_from DESC
         ) AS mapping_rank
  FROM source_rows e JOIN code_listing_resolution l ON l.source_code=e.stock_code
   AND {_utc_date_sql("e.trade_date")} >= l.effective_from
   AND (l.effective_to IS NULL OR {_utc_date_sql("e.trade_date")} < l.effective_to)
)
SELECT * EXCLUDE(mapping_rank) FROM mapped WHERE mapping_rank=1
"""


def _delist_exception_query() -> str:
    return f"""
WITH mapped AS (
  SELECT e.*,l.listing_id,
         row_number() OVER (
           PARTITION BY e.stock_code,e.market_date ORDER BY l.effective_from DESC
         ) AS mapping_rank
  FROM src_delist_boundary_exceptions e JOIN code_listing_resolution l
    ON l.source_code=e.stock_code
   AND {_utc_date_sql("e.market_date")} >= l.effective_from
   AND (l.effective_to IS NULL OR {_utc_date_sql("e.market_date")} < l.effective_to)
)
SELECT * EXCLUDE(mapping_rank) FROM mapped WHERE mapping_rank=1
"""


def _observation_gap_query(*, predecessor_digest: str, market_manifest_sha256: str) -> str:
    predecessor = _sql_literal(predecessor_digest)
    artifact = _sql_literal(market_manifest_sha256)
    return f"""
WITH expected AS (
  SELECT s.listing_id,s.calendar_session_id,s.observation_start,s.reason_code,
         '1d' AS interval_size
  FROM built_state s JOIN ref_listing l USING (listing_id)
  JOIN ref_venue v USING (venue_id)
  LEFT JOIN coverage_bounds b ON b.listing_id=s.listing_id AND b.interval_size='1d'
  WHERE v.mic_code IN ('XSHG','XSHE')
     OR (b.coverage_from IS NOT NULL AND s.observation_start BETWEEN b.coverage_from AND b.coverage_to)
  UNION ALL BY NAME
  SELECT s.listing_id,s.calendar_session_id,s.observation_start,s.reason_code,
         b.interval_size
  FROM built_state s JOIN coverage_bounds b ON b.listing_id=s.listing_id
   AND b.interval_size IN ('1m','5m')
   AND s.observation_start BETWEEN b.coverage_from AND b.coverage_to
), missing AS (
  SELECT e.* FROM expected e LEFT JOIN observed_session o
    ON o.listing_id=e.listing_id AND o.calendar_session_id=e.calendar_session_id
   AND o.interval_size=e.interval_size
  WHERE o.listing_id IS NULL
), classified AS (
  SELECT m.*,g.status AS evidence_status,g.reason AS evidence_reason,
         g.__source_sha256 AS evidence_sha256,
         d.reason_code AS delist_reason,d.__source_sha256 AS delist_sha256,
         CASE
           WHEN d.listing_id IS NOT NULL THEN 'DELIST_DAY_BOUNDARY_NO_BAR'
           WHEN m.reason_code='SUSPENDED' THEN 'SUSPENSION'
           WHEN m.reason_code='PROVIDER_NOT_TRADING' THEN 'PROVIDER_NOT_TRADING'
           WHEN g.status='not_executable' THEN 'OBSERVATION_NOT_EXECUTABLE'
           WHEN g.status='suspended' THEN 'SUSPENSION'
           ELSE 'SOURCE_OBSERVATION_MISSING'
         END AS classified_reason,
         CASE WHEN d.listing_id IS NOT NULL OR m.reason_code IN ('SUSPENDED','PROVIDER_NOT_TRADING')
                    OR g.status='suspended'
              THEN true ELSE false END AS expected_gap
  FROM missing m
  LEFT JOIN gap_evidence g ON g.listing_id=m.listing_id
    AND g.trade_date=CAST(m.observation_start AT TIME ZONE 'Asia/Shanghai' AS DATE)
    AND m.interval_size='1d'
  LEFT JOIN delist_exception d ON d.listing_id=m.listing_id
    AND d.market_date=CAST(m.observation_start AT TIME ZONE 'Asia/Shanghai' AS DATE)
    AND m.interval_size='1d'
)
SELECT 'governed_observation_gap_v1' AS record_source,
       'listing_id=' || listing_id || '&calendar_session_id=' || calendar_session_id
         || '&interval_size=' || interval_size AS source_record_id,
       {predecessor} AS source_revision,{artifact} AS source_artifact_sha256,
       build_cutoff() AS knowledge_from,NULL::TIMESTAMPTZ AS knowledge_to,
       observation_start AS affected_effective_at,build_cutoff() AS detected_at,
       'gap_' || substr(sha256(listing_id || chr(31) || calendar_session_id || chr(31)
         || interval_size || chr(31) || CAST(observation_start AS VARCHAR)),1,32) AS gap_id,
       listing_id,calendar_session_id,interval_size,
       classified_reason AS gap_reason,expected_gap AS is_expected,
       'sha256:' || sha256(concat_ws(chr(31),{predecessor},listing_id,
         calendar_session_id,interval_size,classified_reason,
         coalesce(evidence_sha256,''),coalesce(delist_sha256,''))) AS evidence_digest,
       strftime(observation_start,'%Y') AS affected_effective_at_year,
       strftime(observation_start,'%m') AS affected_effective_at_month
FROM classified
ORDER BY affected_effective_at,listing_id,interval_size
"""


def _copy_query(
    connection: Any,
    query: str,
    output: Path,
    partition_columns: Sequence[str],
) -> None:
    if output.exists():
        if output.is_dir():
            shutil.rmtree(output)
        else:
            output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    options = ["FORMAT PARQUET", "COMPRESSION ZSTD", "ROW_GROUP_SIZE 122880"]
    if partition_columns:
        options.extend(
            [
                f"PARTITION_BY ({','.join(partition_columns)})",
                "FILENAME_PATTERN 'part-{i}'",
            ]
        )
    connection.execute(f"COPY ({query}) TO {_sql_literal(str(output))} ({','.join(options)})")


def _partition_digest(path: Path, schema: Any) -> tuple[str, int]:
    _, pa, pq = _dependencies()
    if not pq.ParquetFile(path).schema_arrow.equals(schema, check_metadata=True):
        raise TradabilityStateBuildError(f"partition physical schema differs: {path}")
    digest = hashlib.sha256(str(schema).encode("utf-8"))
    rows = 0
    for batch in pq.ParquetFile(path).iter_batches(batch_size=122_880):
        table = pa.Table.from_batches([batch]).select(schema.names).cast(schema)
        normalized = table.to_batches()[0]
        digest.update(normalized.serialize().to_pybytes())
        rows += normalized.num_rows
    return f"sha256:{digest.hexdigest()}", rows


def _partition_values(path: Path, columns: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in path.parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key in columns:
            values[key] = value
    if set(values) != set(columns):
        raise TradabilityStateBuildError(f"partition path is incomplete: {path}")
    return values


def _normalize_output(
    table: str,
    raw_root: Path,
    final_root: Path,
) -> list[dict[str, Any]]:
    _, pa, pq = _dependencies()
    schema = _schemas()[table]
    partition_columns = _PARTITIONED_TABLES.get(table, ())
    raw_files = sorted(raw_root.rglob("*.parquet")) if raw_root.is_dir() else [raw_root]
    if not raw_files or any(not path.is_file() for path in raw_files):
        raise TradabilityStateBuildError(f"{table} produced no parquet files")
    groups: dict[Path, list[Path]] = {}
    for path in raw_files:
        key = path.parent.relative_to(raw_root) if raw_root.is_dir() else Path(".")
        groups.setdefault(key, []).append(path)
    states: list[dict[str, Any]] = []
    for relative_parent in sorted(groups):
        final = (
            final_root / relative_parent / "part-00000.parquet" if partition_columns else final_root
        )
        final.parent.mkdir(parents=True, exist_ok=True)
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
        row_count = 0
        try:
            partition_values = (
                _partition_values(relative_parent, partition_columns) if partition_columns else {}
            )
            for raw in sorted(groups[relative_parent]):
                for batch in pq.ParquetFile(raw).iter_batches(batch_size=122_880):
                    value = pa.Table.from_batches([batch])
                    for column in partition_columns:
                        if column not in value.column_names:
                            value = value.append_column(
                                column,
                                pa.array([partition_values[column]] * value.num_rows),
                            )
                    value = value.select(schema.names).cast(schema)
                    writer.write_table(value, row_group_size=122_880)
                    row_count += value.num_rows
        finally:
            writer.close()
        os.chmod(partial, 0o640)
        _fsync_file(partial)
        os.replace(partial, final)
        _fsync_directory(final.parent)
        semantic_digest, verified_rows = _partition_digest(final, schema)
        if verified_rows != row_count:
            raise TradabilityStateBuildError(f"{table} row count changed during write")
        states.append(
            {
                "path": final.as_posix(),
                "row_count": row_count,
                "semantic_digest": semantic_digest,
                "schema_digest": _canonical_digest(str(schema)),
                "sha256": _sha256(final),
            }
        )
    return states


def _relative_states(work: Path, states: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for raw in states:
        item = dict(raw)
        item["path"] = Path(str(item["path"])).relative_to(work).as_posix()
        result.append(item)
    return result


def _table_digest(partitions: Sequence[Mapping[str, Any]]) -> str:
    return _canonical_digest(
        [
            {
                "path": row["path"],
                "row_count": row["row_count"],
                "semantic_digest": row["semantic_digest"],
                "schema_digest": row["schema_digest"],
            }
            for row in sorted(partitions, key=lambda value: str(value["path"]))
        ]
    )


def _output_path(work: Path, table: str) -> Path:
    base = work / "payload/tradability_state"
    return base / table if table in _PARTITIONED_TABLES else base / f"{table}.parquet"


def _read_output_sql(work: Path, table: str) -> str:
    output = _output_path(work, table)
    paths = sorted(output.rglob("*.parquet")) if output.is_dir() else [output]
    return _read_parquet_sql(paths)


def _verify_partitions(
    root: Path, table: str, partitions: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    schema = _schemas()[table]
    verified = []
    for raw in partitions:
        item = dict(raw)
        try:
            path = _safe_file(root, item.get("path"))
        except TradingCalendarBuildError as exc:
            raise TradabilityStateBuildError(str(exc)) from exc
        digest, rows = _partition_digest(path, schema)
        if (
            rows != item.get("row_count")
            or digest != item.get("semantic_digest")
            or _sha256(path) != item.get("sha256")
            or item.get("schema_digest") != _canonical_digest(str(schema))
        ):
            raise TradabilityStateBuildError(f"{table} partition failed verification")
        verified.append(item)
    if not verified:
        raise TradabilityStateBuildError(f"{table} has no partitions")
    return verified


def _write_checkpoint(
    work: Path,
    checkpoint: dict[str, Any],
    table: str,
    partitions: list[dict[str, Any]],
) -> None:
    checkpoint["completed_tables"][table] = {
        "partitions": partitions,
        "table_digest": _table_digest(partitions),
    }
    _write_json_atomic(work / "checkpoint.json", checkpoint)


def _prepare_work(
    work: Path, identity: Mapping[str, str]
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    if not work.exists():
        work.mkdir(mode=0o750)
        (work / "receipts").mkdir()
        (work / "reconciliation").mkdir()
        checkpoint: dict[str, Any] = {
            "schema_version": 1,
            "kind": "dwd_tradability_state_checkpoint",
            **identity,
            "completed_tables": {},
        }
        _write_json_atomic(work / "checkpoint.json", checkpoint)
        return checkpoint, {}
    if work.is_symlink() or not work.is_dir():
        raise TradabilityStateBuildError("build work path must be a physical directory")
    checkpoint = _object(work / "checkpoint.json")
    if (
        checkpoint.get("schema_version") != 1
        or checkpoint.get("kind") != "dwd_tradability_state_checkpoint"
        or any(checkpoint.get(key) != value for key, value in identity.items())
    ):
        raise TradabilityStateBuildError("checkpoint belongs to another build")
    completed = _mapping(checkpoint.get("completed_tables"), "completed tables")
    if not set(completed).issubset(_OWNED_TABLES):
        raise TradabilityStateBuildError("checkpoint contains an unowned table")
    states: dict[str, list[dict[str, Any]]] = {}
    for table, raw in completed.items():
        state = _mapping(raw, f"completed {table}")
        partitions = state.get("partitions")
        if not isinstance(partitions, list):
            raise TradabilityStateBuildError(f"checkpoint {table} has no partitions")
        verified = _verify_partitions(work, table, partitions)
        if state.get("table_digest") != _table_digest(verified):
            raise TradabilityStateBuildError(f"checkpoint table digest differs: {table}")
        states[table] = verified
    for name in (
        "manifest.json",
        "READY",
        "receipts/tradability-state-build.json",
        "reconciliation/source-row-reconciliation.json",
    ):
        (work / name).unlink(missing_ok=True)
    for partial in work.rglob("*.partial"):
        partial.unlink()
    return checkpoint, states


def _build_table(
    connection: Any,
    *,
    work: Path,
    scratch: Path,
    table: str,
    query: str,
) -> list[dict[str, Any]]:
    raw = scratch / f"raw-{table}"
    partition_columns = _PARTITIONED_TABLES.get(table, ())
    raw_target = raw if partition_columns else raw.with_suffix(".parquet")
    _copy_query(connection, query, raw_target, partition_columns)
    states = _normalize_output(table, raw_target, _output_path(work, table))
    return _relative_states(work, states)


def _validate_source_resolution(connection: Any) -> dict[str, int]:
    checks = {
        "st_status_rows": "SELECT count(*) FROM src_st_status_pit",
        "st_status_unresolved_in_scope": f"""
          SELECT count(*) FROM src_st_status_pit s WHERE
          (s.stock_code LIKE '00%' OR s.stock_code LIKE '30%' OR
           s.stock_code LIKE '60%' OR s.stock_code LIKE '68%')
          AND NOT EXISTS (SELECT 1 FROM code_listing_resolution l
            WHERE l.source_code=s.stock_code
            AND {_utc_date_sql("s.effective_start")} >= l.effective_from
            AND (l.effective_to IS NULL OR {_utc_date_sql("s.effective_start")} < l.effective_to))
        """,
        "daily_status_keys": "SELECT count(*) FROM daily_status_resolved",
        "daily_status_unresolved": """
          SELECT count(*) FROM daily_status_resolved d WHERE NOT EXISTS (
            SELECT 1 FROM daily_status_mapped m
            WHERE m.stock_code=d.stock_code AND m.status_date=d.status_date)
        """,
        "suspension_rows": "SELECT count(*) FROM (SELECT suspension_id FROM src_suspensions UNION SELECT suspension_id FROM src_suspensions_delta)",
        "suspension_unresolved_in_scope": f"""
          SELECT count(*) FROM (
            SELECT stock_code,suspend_start FROM src_suspensions
            UNION ALL BY NAME SELECT stock_code,suspend_start FROM src_suspensions_delta
          ) s WHERE
          (s.stock_code LIKE '00%' OR s.stock_code LIKE '30%' OR
           s.stock_code LIKE '60%' OR s.stock_code LIKE '68%')
          AND NOT EXISTS (SELECT 1 FROM code_listing_resolution l
            WHERE l.source_code=s.stock_code
            AND {_utc_date_sql("s.suspend_start")} >= l.effective_from
            AND (l.effective_to IS NULL OR {_utc_date_sql("s.suspend_start")} < l.effective_to))
        """,
        "price_regime_rows": "SELECT count(*) FROM src_price_limit_regime",
        "price_override_unresolved": "SELECT count(*) FROM price_regime_internal WHERE listing_matches=0 AND source_stock_code IS NOT NULL",
    }
    result = {name: int(connection.execute(query).fetchone()[0]) for name, query in checks.items()}
    blocking = (
        result["st_status_unresolved_in_scope"]
        + result["daily_status_unresolved"]
        + result["suspension_unresolved_in_scope"]
        + result["price_override_unresolved"]
    )
    if blocking:
        raise TradabilityStateBuildError(f"in-scope source identities did not resolve: {result}")
    return result


def _audit_outputs(connection: Any, work: Path) -> dict[str, Any]:
    keys = {
        "dwd_security_status_interval": ("security_id,status_type,effective_from,knowledge_from"),
        "dwd_listing_status_interval": ("listing_id,status_type,effective_from,knowledge_from"),
        "dwd_price_limit_regime": ("price_limit_regime_id,effective_from,knowledge_from"),
        "dwd_tradability_state": (
            "listing_id,calendar_session_id,observation_start,knowledge_from"
        ),
        "dwd_observation_gap": (
            "listing_id,calendar_session_id,interval_size,affected_effective_at,knowledge_from"
        ),
    }
    duplicates: dict[str, int] = {}
    null_keys: dict[str, int] = {}
    for table, key in keys.items():
        source = _read_output_sql(work, table)
        duplicates[table] = int(
            connection.execute(
                f"SELECT count(*) FROM (SELECT {key},count(*) n FROM {source} GROUP BY {key} HAVING n>1)"
            ).fetchone()[0]
        )
        columns = key.split(",")
        null_keys[table] = int(
            connection.execute(
                f"SELECT count(*) FROM {source} WHERE "
                + " OR ".join(f"{column} IS NULL" for column in columns)
            ).fetchone()[0]
        )
    if any(duplicates.values()) or any(null_keys.values()):
        raise TradabilityStateBuildError(
            f"output key audit failed: duplicates={duplicates}, nulls={null_keys}"
        )
    state_rows = int(
        connection.execute(
            f"SELECT count(*) FROM {_read_output_sql(work, 'dwd_tradability_state')}"
        ).fetchone()[0]
    )
    expected_state_rows = int(connection.execute("SELECT count(*) FROM state_base").fetchone()[0])
    if state_rows != expected_state_rows:
        raise TradabilityStateBuildError("tradability state did not cover every listing session")
    gap_reason_counts = {
        str(reason): int(count)
        for reason, count in connection.execute(
            f"SELECT gap_reason,count(*) FROM {_read_output_sql(work, 'dwd_observation_gap')} GROUP BY gap_reason"
        ).fetchall()
    }
    if any(not reason or "UNKNOWN" in reason for reason in gap_reason_counts):
        raise TradabilityStateBuildError("observation gap has an unknown reason")
    return {
        "duplicate_business_keys": duplicates,
        "null_row_keys": null_keys,
        "expected_tradability_rows": expected_state_rows,
        "actual_tradability_rows": state_rows,
        "gap_reason_counts": gap_reason_counts,
    }


def _create_scratch_directory(output: Path, build_identity: str, scratch_root: Path | None) -> Path:
    parent = output.parent if scratch_root is None else scratch_root
    if parent.is_symlink() or not parent.is_dir():
        raise TradabilityStateBuildError("scratch root must be a physical directory")
    return Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.{build_identity}.",
            suffix=".duckdb-tmp",
            dir=parent.resolve(),
        )
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
    market_observation_semantic_digest: str,
    market_observation_manifest_sha256: str,
) -> TradabilityStateBuildResult:
    identity = {
        "run_id": run_id,
        "input_plan_digest": input_plan_digest,
        "model_sha256": model_sha256,
        "source_set_digest": source_set_digest,
        "listed_company_semantic_digest": listed_company_semantic_digest,
        "listed_company_manifest_sha256": listed_company_manifest_sha256,
        "trading_calendar_semantic_digest": trading_calendar_semantic_digest,
        "trading_calendar_manifest_sha256": trading_calendar_manifest_sha256,
        "market_observation_semantic_digest": market_observation_semantic_digest,
        "market_observation_manifest_sha256": market_observation_manifest_sha256,
    }
    receipt = _object(root / "receipts/tradability-state-build.json")
    checkpoint = _object(root / "checkpoint.json")
    completed = _mapping(checkpoint.get("completed_tables"), "completed tables")
    expected_files = {
        "checkpoint.json",
        "receipts/tradability-state-build.json",
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
        expected_kind="dwd_tradability_state_candidate",
    )
    if (
        any(receipt.get(key) != value for key, value in identity.items())
        or receipt.get("schema_version") != 1
        or receipt.get("status") != "built"
        or set(completed) != set(_OWNED_TABLES)
        or manifest.get("owned_tables") != list(_OWNED_TABLES)
        or checkpoint.get("semantic_digest") != receipt.get("semantic_digest")
        or manifest.get("semantic_digest") != receipt.get("semantic_digest")
    ):
        raise TradabilityStateBuildError("tradability-state candidate identity differs")
    table_digests: dict[str, str] = {}
    row_counts: dict[str, int] = {}
    partition_counts: dict[str, int] = {}
    for table in _OWNED_TABLES:
        state = _mapping(completed[table], f"completed {table}")
        partitions = state.get("partitions")
        if not isinstance(partitions, list):
            raise TradabilityStateBuildError(f"{table} checkpoint has no partitions")
        verified = _verify_partitions(root, table, partitions)
        table_digests[table] = _table_digest(verified)
        row_counts[table] = sum(int(item["row_count"]) for item in verified)
        partition_counts[table] = len(verified)
        if state.get("table_digest") != table_digests[table]:
            raise TradabilityStateBuildError(f"{table} table digest differs")
    semantic = _canonical_digest({**identity, "table_digests": table_digests})
    if (
        semantic != receipt.get("semantic_digest")
        or table_digests != receipt.get("table_digests")
        or row_counts != receipt.get("row_counts")
        or row_counts != manifest.get("row_counts")
    ):
        raise TradabilityStateBuildError("tradability-state semantic digest differs")
    return TradabilityStateBuildResult(root.resolve(), semantic, row_counts, partition_counts)


def verify_tradability_state_candidate(
    root: Path,
    *,
    run_id: str,
    input_plan_digest: str,
    model_sha256: str,
    listed_company_semantic_digest: str,
    listed_company_manifest_sha256: str,
    trading_calendar_semantic_digest: str,
    trading_calendar_manifest_sha256: str,
    market_observation_semantic_digest: str,
    market_observation_manifest_sha256: str,
) -> TradabilityStateCandidateVerification:
    """Deep-verify a sealed #59 candidate for downstream same-run builders."""

    receipt = _object(root / "receipts/tradability-state-build.json")
    result = _verify_existing_output(
        root,
        run_id=run_id,
        input_plan_digest=input_plan_digest,
        model_sha256=model_sha256,
        source_set_digest=_text(receipt, "source_set_digest"),
        listed_company_semantic_digest=listed_company_semantic_digest,
        listed_company_manifest_sha256=listed_company_manifest_sha256,
        trading_calendar_semantic_digest=trading_calendar_semantic_digest,
        trading_calendar_manifest_sha256=trading_calendar_manifest_sha256,
        market_observation_semantic_digest=market_observation_semantic_digest,
        market_observation_manifest_sha256=market_observation_manifest_sha256,
    )
    return TradabilityStateCandidateVerification(
        root=result.root,
        manifest_sha256=_sha256(root / "manifest.json"),
        semantic_digest=result.semantic_digest,
        row_counts=result.row_counts,
        partition_counts=result.partition_counts,
    )


def _finalize(
    work: Path,
    *,
    identity: Mapping[str, str],
    checkpoint: dict[str, Any],
    states: Mapping[str, list[dict[str, Any]]],
    reconciliation: Mapping[str, Any],
) -> TradabilityStateBuildResult:
    table_digests = {table: _table_digest(states[table]) for table in _OWNED_TABLES}
    row_counts = {
        table: sum(int(item["row_count"]) for item in states[table]) for table in _OWNED_TABLES
    }
    partition_counts = {table: len(states[table]) for table in _OWNED_TABLES}
    semantic = _canonical_digest({**identity, "table_digests": table_digests})
    checkpoint["semantic_digest"] = semantic
    _write_json_atomic(work / "checkpoint.json", checkpoint)
    receipt = {
        "schema_version": 1,
        "kind": "dwd_tradability_state_build_receipt",
        "status": "built",
        **identity,
        "semantic_digest": semantic,
        "table_digests": table_digests,
        "row_counts": row_counts,
        "partition_counts": partition_counts,
    }
    _write_json_atomic(work / "receipts/tradability-state-build.json", receipt)
    _write_json_atomic(
        work / "reconciliation/source-row-reconciliation.json",
        {
            "schema_version": 1,
            "kind": "dwd_tradability_state_source_reconciliation",
            "run_id": identity["run_id"],
            "source_set_digest": identity["source_set_digest"],
            "output_rows": row_counts,
            **dict(reconciliation),
        },
    )
    files = {
        "checkpoint.json",
        "receipts/tradability-state-build.json",
        "reconciliation/source-row-reconciliation.json",
        *{str(item["path"]) for values in states.values() for item in values},
    }
    manifest = {
        "schema_version": 1,
        "kind": "dwd_tradability_state_candidate",
        **identity,
        "owned_tables": list(_OWNED_TABLES),
        "row_counts": row_counts,
        "partition_counts": partition_counts,
        "semantic_digest": semantic,
        "files": [_file_record(work, work / relative) for relative in sorted(files)],
    }
    _write_json_atomic(work / "manifest.json", manifest)
    _write_text_atomic(work / "READY", _sha256(work / "manifest.json") + "\n")
    return TradabilityStateBuildResult(work.resolve(), semantic, row_counts, partition_counts)


def _build_locked(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    trading_calendar_candidate: Path,
    market_observation_candidate: Path,
    output: Path,
    run_id: str,
    scratch_root: Path | None,
) -> TradabilityStateBuildResult:
    try:
        plan, ledger = _verify_plan_package(input_plan)
        semantic_plan = _mapping(plan.get("plan"), "input plan")
        model = _object(model_path)
        _verify_ods_binding(ods_package, semantic_plan, model)
    except TradingCalendarBuildError as exc:
        raise TradabilityStateBuildError(str(exc)) from exc
    if plan.get("run_id") != run_id or semantic_plan.get("mode") != "base":
        raise TradabilityStateBuildError("builder must match one sealed Base run")
    model_sha = _sha256(model_path)
    model_identity = _mapping(semantic_plan.get("model"), "input plan model")
    if model_identity.get("sha256") != model_sha:
        raise TradabilityStateBuildError("model differs from the sealed input plan")
    _verify_model_contract(model)
    cutoff = _timestamp(semantic_plan.get("knowledge_cutoff"), "knowledge cutoff")
    plan_digest = _text(plan, "plan_digest")
    try:
        listed = verify_listed_company_candidate(
            listed_company_candidate,
            run_id=run_id,
            input_plan_digest=plan_digest,
            model_sha256=model_sha,
        )
        calendar = verify_trading_calendar_candidate(
            trading_calendar_candidate,
            run_id=run_id,
            input_plan_digest=plan_digest,
            model_sha256=model_sha,
            upstream_semantic_digest=listed.semantic_digest,
            upstream_manifest_sha256=listed.manifest_sha256,
        )
        market = verify_market_observation_candidate(
            market_observation_candidate,
            run_id=run_id,
            input_plan_digest=plan_digest,
            model_sha256=model_sha,
            listed_company_semantic_digest=listed.semantic_digest,
            listed_company_manifest_sha256=listed.manifest_sha256,
            trading_calendar_semantic_digest=calendar.semantic_digest,
            trading_calendar_manifest_sha256=calendar.manifest_sha256,
        )
    except (ListedCompanyBuildError, TradingCalendarBuildError, MarketObservationBuildError) as exc:
        raise TradabilityStateBuildError(
            f"same-run predecessor failed verification: {exc}"
        ) from exc
    records = _source_records(ledger)
    registry, source_set_digest = _verify_sources(records, ods_package)
    identity = {
        "run_id": run_id,
        "input_plan_digest": plan_digest,
        "model_sha256": model_sha,
        "source_set_digest": source_set_digest,
        "listed_company_semantic_digest": listed.semantic_digest,
        "listed_company_manifest_sha256": listed.manifest_sha256,
        "trading_calendar_semantic_digest": calendar.semantic_digest,
        "trading_calendar_manifest_sha256": calendar.manifest_sha256,
        "market_observation_semantic_digest": market.semantic_digest,
        "market_observation_manifest_sha256": market.manifest_sha256,
    }
    if output.exists():
        return _verify_existing_output(output, **identity)
    build_identity = _canonical_digest(identity).removeprefix("sha256:")
    work = output.parent / f".{output.name}.{build_identity}.work"
    checkpoint, states = _prepare_work(work, identity)
    duckdb, _, _ = _dependencies()
    scratch = _create_scratch_directory(output, build_identity, scratch_root)
    connection = duckdb.connect()
    connection.execute("SET threads=1")
    connection.execute("SET preserve_insertion_order=true")
    connection.execute("SET enable_progress_bar=false")
    connection.execute("SET memory_limit='1600MB'")
    connection.execute(f"SET temp_directory={_sql_literal(str(scratch))}")
    predecessor_digest = _canonical_digest(
        {
            "listed": [listed.semantic_digest, listed.manifest_sha256],
            "calendar": [calendar.semantic_digest, calendar.manifest_sha256],
            "market": [market.semantic_digest, market.manifest_sha256],
            "policy": model_sha,
        }
    )
    try:
        _register_sources(connection, registry)
        _prepare_relations(
            connection,
            listed_root=listed.root,
            calendar_root=calendar.root,
            market_root=market.root,
            cutoff=cutoff.isoformat(),
        )
        connection.execute(
            "CREATE TEMP VIEW daily_status_resolved AS " + _daily_status_relation_query()
        )
        connection.execute(
            "CREATE TEMP VIEW daily_status_mapped AS " + _daily_status_mapped_query()
        )
        overlap_conflicts = int(
            connection.execute(
                """
                SELECT count(*) FROM (
                  SELECT d.stock_code,try_cast(d.date AS DATE) status_date
                  FROM src_daily_status_observation_delta d
                  JOIN src_daily_status_observation_hotfix h
                    ON h.stock_code=d.stock_code
                   AND try_cast(coalesce(h.market_date,h.date) AS DATE)=try_cast(d.date AS DATE)
                  WHERE CAST(d.isST AS VARCHAR)<>CAST(h.isST AS VARCHAR)
                     OR CAST(d.tradestatus AS VARCHAR)<>CAST(h.tradestatus AS VARCHAR)
                )
                """
            ).fetchone()[0]
        )
        if overlap_conflicts:
            raise TradabilityStateBuildError("daily status parent/hotfix overlap values differ")
        connection.execute(
            "CREATE TEMP VIEW price_regime_internal AS " + _price_regime_internal_query()
        )
        source_resolution = _validate_source_resolution(connection)

        build_queries = {
            "dwd_security_status_interval": _security_status_query(),
            "dwd_listing_status_interval": _listing_status_query(),
            "dwd_price_limit_regime": _price_regime_output_query(),
        }
        for table, query in build_queries.items():
            if table not in states:
                partitions = _build_table(
                    connection,
                    work=work,
                    scratch=scratch,
                    table=table,
                    query=query,
                )
                states[table] = partitions
                _write_checkpoint(work, checkpoint, table, partitions)
        connection.execute(
            "CREATE TEMP VIEW security_status_rows AS SELECT * FROM "
            + _read_output_sql(work, "dwd_security_status_interval")
        )
        connection.execute(
            "CREATE TEMP VIEW listing_status_rows AS SELECT * FROM "
            + _read_output_sql(work, "dwd_listing_status_interval")
        )
        connection.execute("CREATE TEMP VIEW state_base AS " + _state_base_query())
        connection.execute(
            "CREATE TEMP VIEW state_rule_candidates AS " + _state_rule_candidates_query()
        )
        ambiguous_rules = int(
            connection.execute(
                """
                SELECT count(*) FROM (
                  SELECT listing_id,calendar_session_id,priority,count(*) n
                  FROM state_rule_candidates
                  WHERE price_limit_regime_id IS NOT NULL AND priority=best_priority
                  GROUP BY listing_id,calendar_session_id,priority HAVING n>1
                )
                """
            ).fetchone()[0]
        )
        if ambiguous_rules:
            raise TradabilityStateBuildError(
                f"equal-priority applicable price rules are ambiguous: {ambiguous_rules}"
            )
        connection.execute("CREATE TEMP VIEW state_resolved AS " + _state_resolved_query())
        missing_post_rule = int(
            connection.execute(
                """
                SELECT count(*) FROM state_resolved s JOIN ref_venue v USING (venue_id)
                WHERE price_limit_regime_id IS NULL
                  AND trade_date >= DATE '1996-12-16'
                  AND v.mic_code IN ('XSHG','XSHE')
                """
            ).fetchone()[0]
        )
        if missing_post_rule:
            raise TradabilityStateBuildError(
                f"SSE/SZSE sessions have no applicable price rule: {missing_post_rule}"
            )
        if "dwd_tradability_state" not in states:
            partitions = _build_table(
                connection,
                work=work,
                scratch=scratch,
                table="dwd_tradability_state",
                query=_tradability_state_query(
                    predecessor_digest=predecessor_digest,
                    market_manifest_sha256=market.manifest_sha256,
                ),
            )
            states["dwd_tradability_state"] = partitions
            _write_checkpoint(work, checkpoint, "dwd_tradability_state", partitions)
        connection.execute(
            "CREATE TEMP VIEW built_state AS SELECT * FROM "
            + _read_output_sql(work, "dwd_tradability_state")
        )
        if "dwd_observation_gap" not in states:
            connection.execute("CREATE TEMP TABLE observed_session AS " + _observed_session_query())
            connection.execute("CREATE TEMP TABLE coverage_bounds AS " + _coverage_bounds_query())
            connection.execute("CREATE TEMP VIEW gap_evidence AS " + _gap_evidence_query())
            connection.execute("CREATE TEMP VIEW delist_exception AS " + _delist_exception_query())
            partitions = _build_table(
                connection,
                work=work,
                scratch=scratch,
                table="dwd_observation_gap",
                query=_observation_gap_query(
                    predecessor_digest=predecessor_digest,
                    market_manifest_sha256=market.manifest_sha256,
                ),
            )
            states["dwd_observation_gap"] = partitions
            _write_checkpoint(work, checkpoint, "dwd_observation_gap", partitions)
        audit = _audit_outputs(connection, work)
        reconciliation = {
            "source_resolution": source_resolution,
            "daily_status_overlap_conflicts": overlap_conflicts,
            "equal_priority_rule_ambiguities": ambiguous_rules,
            "missing_post_1996_sse_szse_rules": missing_post_rule,
            **audit,
        }
        _finalize(
            work,
            identity=identity,
            checkpoint=checkpoint,
            states=states,
            reconciliation=reconciliation,
        )
        _verify_existing_output(work, **identity)
        os.replace(work, output)
        _fsync_directory(output.parent)
    finally:
        connection.close()
        shutil.rmtree(scratch, ignore_errors=True)
    return _verify_existing_output(output, **identity)


def build_tradability_state(
    *,
    ods_package: Path,
    input_plan: Path,
    model_path: Path,
    listed_company_candidate: Path,
    trading_calendar_candidate: Path,
    market_observation_candidate: Path,
    output: Path,
    run_id: str,
    scratch_root: Path | None = None,
) -> TradabilityStateBuildResult:
    """Build one immutable, idempotent Issue #59 candidate without publishing it."""

    if output.is_symlink():
        raise TradabilityStateBuildError("candidate output path must not be symbolic")
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
        raise TradabilityStateBuildError(f"cannot open build lock: {lock_path}") from exc
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TradabilityStateBuildError("build lock must be a physical file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        return _build_locked(
            ods_package=ods_package,
            input_plan=input_plan,
            model_path=model_path,
            listed_company_candidate=listed_company_candidate,
            trading_calendar_candidate=trading_calendar_candidate,
            market_observation_candidate=market_observation_candidate,
            output=normalized,
            run_id=run_id,
            scratch_root=scratch_root,
        )
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
