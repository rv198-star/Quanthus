"""Source-only, opt-in Base 2.4 consumer projections; never execute a strategy.

The seven names are familiar, but this is a versioned, lossless governance
snapshot, not a substitute for an admitted historical/PIT research bundle.
Unknown status, authority, factor checks and acquisition clocks stay unknown.
One job-local native materialization serves all seven projections and cold
validation. No legacy source path is needed by the sealed result.
"""

from __future__ import annotations

import importlib
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.pipelines.securities_warehouse.base22 import file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import (
    _tree,
    disjoint_output,
    physical_path,
)
from quanthus.pipelines.securities_warehouse.base24_input_view import TABLES, InputView

_LOG = logging.getLogger(__name__)

VERSION = "quanthus.base24-consumer-source-projection.v1"
KEYS = {
    "daily_ohlcv_none": ("dwd_listing_id", "date"),
    "daily_universe": ("dwd_listing_id", "market_date"),
    "price_limit_execution_daily": ("dwd_listing_id", "execution_date"),
    "security_code_validity": ("dwd_listing_id", "valid_from"),
    "trading_calendar": ("calendar_session_id",),
    "corporate_actions_normalized": ("action_id",),
    "corporate_action_exceptions": ("exception_id",),
}
NATIVE_KEYS = {
    "dwd_security": "security_id,effective_from",
    "dwd_listing": "listing_id,effective_from",
    "dwd_trading_venue": "venue_id,effective_from",
    "dwd_trading_calendar": "calendar_session_id",
    "dwd_security_bar": "listing_id,calendar_session_id",
    "dwd_tradability_state": "listing_id,calendar_session_id",
    "dwd_price_limit_regime": "price_limit_regime_id,effective_from",
    "dwd_listing_status_interval": "listing_status_id,effective_from",
    "dwd_security_identifier": "security_id,identifier_namespace,identifier_value,effective_from",
    "dwd_corporate_action": "corporate_action_id",
    "dwd_corporate_action_term": "corporate_action_term_id",
    "dwd_adjustment_factor": "adjustment_factor_id,effective_from",
    "dwd_quality_exception": "quality_exception_id",
    "dwd_company_industry_membership": "company_id,industry_node_id,effective_from",
    "dwd_industry_node": "industry_node_id,effective_from",
    "dwd_industry_scheme": "industry_scheme_id,effective_from",
}


class ProjectionError(ValueError):
    """A source, semantic relation or sealed output could not be verified."""


def _q(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _write(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _connection(root: Path) -> Any:
    c = importlib.import_module("duckdb").connect()
    c.execute("SET memory_limit='2GB'")
    c.execute("SET threads=2")
    c.execute("SET TimeZone='Asia/Shanghai'")
    c.execute("SET autoinstall_known_extensions=false")
    c.execute("SET autoload_known_extensions=false")
    c.execute(f"SET temp_directory={_q(root / 'sort-tmp')}")
    c.execute("SET max_temp_directory_size='4GB'")
    return c


def _assert_zero(c: Any, query: str, reason: str) -> None:
    if c.execute(query).fetchone()[0]:
        raise ProjectionError(reason)


def revision_transitions(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    """Describe a new explicit selection, never rewrite a legacy prior chain.

    This selection is qualified from complete, self-contained products, not by
    pretending an old prior digest equals a newer revision. Each date and both
    digests remain in the successor binding. A missing date is not a revision.
    """
    transitions = []
    previous = None
    for row in receipt["Daily"]:
        if previous is not None:
            prior = row.get("original_prior") or {}
            if (
                prior.get("market_date") != previous["market_date"]
                or row.get("selected_previous_core") != previous["core_manifest_sha256"]
            ):
                raise ProjectionError("DAILY_DATE_GAP_IS_NOT_REVISION")
            if prior.get("manifest_sha256") != previous["core_manifest_sha256"]:
                transitions.append(
                    {
                        "market_date": row["market_date"],
                        "prior_market_date": previous["market_date"],
                        "original_prior_manifest_sha256": prior["manifest_sha256"],
                        "selected_previous_core_sha256": previous["core_manifest_sha256"],
                        "rule": "new_explicit_selection_from_individually_verified_products",
                        "old_chain_equivalent": False,
                        "historical_PIT_or_activation_granted": False,
                    }
                )
        previous = row
    return transitions


def _months(root: Path) -> list[tuple[str, str]]:
    scope = json.loads((root / "input-view.json").read_bytes())["scope"]
    start, end = (date.fromisoformat(scope[k]) for k in ("business_start", "business_end"))
    cursor = start.replace(day=1)
    result = []
    while cursor <= end:
        following = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        result.append((cursor.isoformat(), following.isoformat()))
        cursor = following
    return result


def _window(start: str, end: str) -> str:
    return (
        f"observation_start>=TIMESTAMPTZ '{start}T00:00:00+08:00' "
        f"AND observation_start<TIMESTAMPTZ '{end}T00:00:00+08:00'"
    )


MARKET_DATES = {
    "daily_ohlcv_none": "date",
    "daily_universe": "market_date",
    "price_limit_execution_daily": "execution_date",
}


def _market(c: Any, start: str, end: str) -> dict[str, str]:
    """Own one bounded market join shared by all three monthly consumers."""
    marker = "FROM dwd_tradability_state t"
    queries = _queries()
    joins = queries["daily_ohlcv_none"].split(marker, 1)[1]
    joins = (
        (marker + joins)
        .replace(
            marker, f"FROM (SELECT * FROM dwd_tradability_state WHERE {_window(start, end)}) t"
        )
        .replace(
            "LEFT JOIN dwd_security_bar b",
            f"LEFT JOIN (SELECT * FROM dwd_security_bar WHERE {_window(start, end)}) b",
        )
    )
    _LOG.info("shared_market_month start=%s end=%s", start, end)
    # Struct fields retain their original native types and names. The same
    # immutable monthly relation feeds prices, universe and execution status.
    c.execute(f"CREATE OR REPLACE TEMP TABLE market AS SELECT t,c,l,s,v,b {joins}")
    return {name: queries[name].split(marker, 1)[0] + "FROM market" for name in MARKET_DATES}


def _write_market(c: Any, output: Path) -> None:
    pq = importlib.import_module("pyarrow.parquet")
    writers: dict[str, Any] = {}
    try:
        for a, b in _months(output):
            for name, sql in _market(c, a, b).items():
                # Ordering and Arrow batches are bounded to this month. Do not
                # concatenate all monthly plans or materialize Python rows.
                reader = c.execute(sql + " ORDER BY " + ",".join(KEYS[name])).to_arrow_reader(8192)
                if name not in writers:
                    writers[name] = pq.ParquetWriter(
                        output / "tables" / (name + ".parquet"), reader.schema, compression="zstd"
                    )
                for batch in reader:
                    writers[name].write_batch(batch)
    finally:
        for writer in writers.values():
            writer.close()


def _views(c: Any, root: Path, cutoff: str, *, build_selection: bool = False) -> dict[str, int]:
    """Explicit latest-known snapshot; no backdating to business observations.

    Byte/field-identical repeats are idempotent. Different rows at the same key
    and knowledge instant are ambiguous and fail. Knowledge_to remains an
    exclusive bound. Unknown clocks are retained physically, never admitted.
    """
    excluded = {}
    for table in TABLES:
        _LOG.info("qualify_snapshot table=%s", table)
        c.execute(
            f"CREATE VIEW raw_{table} AS SELECT DISTINCT * FROM "
            f"read_parquet({_q(root / 'native' / (table + '.parquet'))})"
        )
        visibility = (
            f"knowledge_from <= TIMESTAMPTZ {_q(cutoff)} AND "
            f"(knowledge_to IS NULL OR knowledge_to > TIMESTAMPTZ {_q(cutoff)})"
        )
        keys = NATIVE_KEYS[table]
        excluded[table] = c.execute(
            f"SELECT count(*) FROM raw_{table} WHERE NOT coalesce(({visibility}),false)"
        ).fetchone()[0]
        if table in {"dwd_security_bar", "dwd_tradability_state"}:
            queries = []
            for a, b in _months(root):
                condition = f"({visibility}) AND ({_window(a, b)})"
                _assert_zero(
                    c,
                    f"SELECT count(*) FROM (SELECT {keys},knowledge_from "
                    f"FROM raw_{table} WHERE {condition} GROUP BY ALL HAVING count(*)>1)",
                    "AMBIGUOUS_NATIVE_VERSION:" + table,
                )
                queries.append(
                    f"SELECT * FROM raw_{table} WHERE {condition} "
                    f"QUALIFY row_number() OVER (PARTITION BY {keys} "
                    "ORDER BY knowledge_from DESC)=1"
                )
            paths = []
            for (a, b), sql in zip(_months(root), queries, strict=True):
                selected = root / "selected" / (table + "-" + a + ".parquet")
                paths.append(selected)
                if build_selection:
                    c.execute(f"COPY ({sql}) TO {_q(selected)} (FORMAT PARQUET,COMPRESSION ZSTD)")
                else:
                    actual = f"SELECT * FROM read_parquet({_q(selected)}) WHERE {_window(a, b)}"
                    _assert_zero(
                        c,
                        f"SELECT count(*) FROM ((({sql}) EXCEPT ALL ({actual})) "
                        f"UNION ALL (({actual}) EXCEPT ALL ({sql})))",
                        "SELECTED_NATIVE_SEMANTICS_DIFFERS:" + table,
                    )
            values = "[" + ",".join(_q(p) for p in paths) + "]"
            c.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet({values})")
            _assert_zero(
                c,
                f"SELECT count(*) FROM (SELECT {keys} FROM {table} GROUP BY ALL HAVING count(*)>1)",
                "GLOBAL_NATIVE_KEY_DUPLICATE:" + table,
            )
        else:
            _assert_zero(
                c,
                f"SELECT count(*) FROM (SELECT {keys},knowledge_from "
                f"FROM raw_{table} WHERE {visibility} GROUP BY ALL HAVING count(*)>1)",
                "AMBIGUOUS_NATIVE_VERSION:" + table,
            )
            sql = (
                f"SELECT * FROM raw_{table} WHERE {visibility} "
                f"QUALIFY row_number() OVER (PARTITION BY {keys} "
                "ORDER BY knowledge_from DESC)=1"
            )
            c.execute(f"CREATE TEMP TABLE {table} AS {sql}")
    return excluded


def _relations(c: Any) -> None:
    for table in ("dwd_security_bar", "dwd_tradability_state"):
        _assert_zero(
            c,
            f"SELECT count(*) FROM {table} b LEFT JOIN dwd_trading_calendar c "
            "USING(calendar_session_id) WHERE c.calendar_session_id IS NULL "
            "OR c.trade_date<>CAST(b.observation_start AS DATE)",
            "CALENDAR_DATE_OR_REFERENCE:" + table,
        )
        _assert_zero(
            c,
            f"SELECT count(*) FROM {table} b WHERE NOT EXISTS "
            "(SELECT 1 FROM dwd_listing l JOIN dwd_trading_calendar c "
            "ON c.venue_id=l.venue_id WHERE l.listing_id=b.listing_id "
            "AND c.calendar_session_id=b.calendar_session_id)",
            "CALENDAR_LISTING_VENUE:" + table,
        )
    _assert_zero(
        c,
        "SELECT count(*) FROM dwd_corporate_action_term t WHERE NOT EXISTS "
        "(SELECT 1 FROM dwd_corporate_action a "
        "WHERE a.corporate_action_id=t.corporate_action_id)",
        "ACTION_TERM_ORPHAN",
    )


def _queries() -> dict[str, str]:
    # Retain native Decimal columns; the old double-valued market surface is an
    # explicitly labelled compatibility representation, never the authority.
    identity = """l.listing_code AS security_id,l.listing_code AS stock_code,
        s.security_id AS dwd_security_id,s.company_id AS dwd_company_id,
        t.listing_id AS dwd_listing_id,c.venue_id AS dwd_venue_id,
        s.instrument_type AS dwd_instrument_type,
        s.effective_from AS dwd_security_effective_from,
        l.effective_from AS dwd_listing_effective_from,
        s.knowledge_from AS dwd_security_knowledge_from,
        l.knowledge_from AS dwd_listing_knowledge_from,
        t.knowledge_from AS dwd_state_knowledge_from"""
    joins = """FROM dwd_tradability_state t
        JOIN dwd_trading_calendar c USING(calendar_session_id)
        LEFT JOIN dwd_listing l ON l.listing_id=t.listing_id AND l.venue_id=c.venue_id
          AND l.effective_from<=t.observation_start
          AND (l.effective_to IS NULL OR l.effective_to>t.observation_start)
        LEFT JOIN dwd_security s ON s.security_id=l.security_id
          AND s.effective_from<=t.observation_start
          AND (s.effective_to IS NULL OR s.effective_to>t.observation_start)
        LEFT JOIN dwd_trading_venue v ON v.venue_id=c.venue_id
          AND v.effective_from<=t.observation_start
          AND (v.effective_to IS NULL OR v.effective_to>t.observation_start)
        LEFT JOIN dwd_security_bar b ON b.listing_id=t.listing_id
          AND b.calendar_session_id=t.calendar_session_id"""
    exchange = (
        "CASE v.mic_code WHEN 'XSHG' THEN 'SSE' WHEN 'XSHE' THEN 'SZSE' "
        "WHEN 'BJSE' THEN 'BSE' ELSE v.mic_code END"
    )
    return {
        "daily_ohlcv_none": f"""SELECT {identity}, {exchange} AS identity_exchange,
            c.trade_date AS date,
            b.open::DOUBLE AS open,b.high::DOUBLE AS high,b.low::DOUBLE AS low,
            b.close::DOUBLE AS close,b.volume::DOUBLE AS volume,b.turnover::DOUBLE AS amount,
            b.open AS dwd_open_decimal,b.high AS dwd_high_decimal,b.low AS dwd_low_decimal,
            b.close AS dwd_close_decimal,b.turnover AS dwd_turnover_decimal,
            b.volume AS dwd_volume_decimal,b.knowledge_from AS dwd_bar_knowledge_from,
            b.knowledge_to AS dwd_bar_knowledge_to,
            b.observation_start,b.observation_end,b.calendar_session_id,
            b.source_record_id AS dwd_bar_source_record_id,
            b.source_revision AS dwd_bar_source_revision,
            b.source_artifact_sha256 AS dwd_bar_source_artifact_sha256,
            NULL::DOUBLE AS turnover_rate,NULL::DOUBLE AS pct_change,
            'none' AS adj_mode,'base24-native-UNADJUSTED-v1' AS adj_version,
            'dwd_security_bar' AS source,b.knowledge_from AS built_at,
            t.is_tradable,t.can_buy,t.can_sell,t.reason_code,
            b.listing_id IS NOT NULL AS bar_observed,
            l.listing_id IS NOT NULL AND s.security_id IS NOT NULL AS identity_qualified {joins}""",
        "daily_universe": f"""SELECT {identity},{exchange} AS exchange,
            c.trade_date AS market_date,CAST(l.effective_from AS DATE) AS valid_from,
            CAST(l.effective_to AS DATE) AS valid_to,
            '{VERSION}' AS mapping_version,l.source_artifact_sha256 AS mapping_digest,
            t.is_tradable,t.can_buy,t.can_sell,t.reason_code,
            l.listing_id IS NOT NULL AND s.security_id IS NOT NULL AS identity_qualified {joins}""",
        "price_limit_execution_daily": f"""SELECT {identity},{exchange} AS exchange,
            l.board_code AS board,c.trade_date AS execution_date,
            'unknown' AS st_status_label,NULL::BOOLEAN AS is_st_class,
            false AS status_known,NULL::DATE AS st_source_market_date,
            NULL::VARCHAR AS st_revision,
            CASE WHEN t.is_tradable THEN '1' WHEN NOT t.is_tradable THEN '0' END
              AS observed_tradestatus,t.source_record_id AS tradestatus_raw_shard,
            NULL::INTEGER AS eligible_session_index,NULL::BOOLEAN AS in_ipo_window,
            NULL::VARCHAR AS listing_window_rule,NULL::INTEGER AS ipo_window_sessions,
            'dwd_tradability_state' AS selected_rule_basis,
            NULL::DOUBLE AS upper_limit_pct,NULL::DOUBLE AS lower_limit_pct,
            NULL::DECIMAL(38,10) AS price_reference,'unavailable' AS price_reference_kind,
            t.upper_price AS upper_limit_price,t.lower_price AS lower_limit_price,
            NULL::DECIMAL(3,2) AS price_tick,'source_observed_only' AS rounding_rule,
            false AS regime_known,false AS entry_policy_allows,false AS is_executable,
            t.reason_code,t.knowledge_from AS knowledge_cutoff,
            NULL::VARCHAR AS ipo_interval_id,NULL::VARCHAR AS ipo_source,
            t.source_revision AS revision,t.is_tradable,t.can_buy,t.can_sell,
            t.observation_start,t.knowledge_to,t.source_artifact_sha256 {joins}""",
        "security_code_validity": """SELECT l.listing_code AS security_id,
            l.listing_code AS stock_code,l.listing_id AS dwd_listing_id,
            l.security_id AS dwd_security_id,
            l.venue_id AS dwd_venue_id,CAST(l.effective_from AS DATE) AS valid_from,
            CAST(l.effective_to AS DATE) AS valid_to,l.effective_from,l.effective_to,
            l.knowledge_from,l.knowledge_to,
            CASE (SELECT min(mic_code) FROM dwd_trading_venue v WHERE v.venue_id=l.venue_id)
              WHEN 'XSHG' THEN 'SSE' WHEN 'XSHE' THEN 'SZSE' WHEN 'BJSE' THEN 'BSE'
            END AS exchange,
            'dwd_listing' AS source,l.source_record_id AS evidence_locator,
            l.source_artifact_sha256 AS evidence_sha256,l.source_revision,
            l.knowledge_from::VARCHAR AS knowledge_cutoff,
            'base24-dated-listing-v1' AS mapping_version,
            l.source_artifact_sha256 AS mapping_digest,
            l.effective_to IS NULL AS valid_to_right_censored FROM dwd_listing l""",
        "trading_calendar": f"""SELECT {exchange} AS exchange,c.calendar_session_id,
            c.venue_id,c.trade_date,c.is_open::BIGINT AS is_open,c.session_type AS session,
            c.open_at,c.close_at,c.calendar_reason,c.knowledge_from,c.knowledge_to,
            c.source_record_id,c.source_revision,c.source_artifact_sha256,
            'dwd_trading_calendar' AS source,c.knowledge_from AS built_at
            FROM dwd_trading_calendar c JOIN dwd_trading_venue v USING(venue_id)""",
        "corporate_actions_normalized": """SELECT a.corporate_action_id AS action_id,
            a.security_id AS dwd_security_id,
            (SELECT CASE WHEN count(DISTINCT listing_code)=1 THEN min(listing_code) END
              FROM dwd_listing l WHERE l.security_id=a.security_id
              AND l.effective_from<=a.effective_at
              AND (l.effective_to IS NULL OR l.effective_to>a.effective_at))
              AS security_code,CAST(a.effective_at AS DATE) AS effective_date,
            a.knowledge_from AS known_at,a.knowledge_to,a.effective_at,a.announced_at,
            a.action_type,a.action_status,a.ex_date,a.record_date,a.payment_date,
            CASE WHEN count(t.corporate_action_term_id) FILTER
              (WHERE t.term_type='CASH_AMOUNT_PER_SHARE')=1 THEN
              max(t.amount_per_share) FILTER (WHERE t.term_type='CASH_AMOUNT_PER_SHARE') END
              AS cash_dividend_per_share,
            NULL::DECIMAL(38,10) AS bonus_share_ratio,NULL::VARCHAR AS cash_unit,
            'native_terms_preserved_separately_authority_not_inferred' AS normalization_basis,
            'unavailable' AS factor_cross_check_status,
            NULL::DECIMAL(38,12) AS factor_ratio_observed,
            NULL::DECIMAL(38,12) AS factor_ratio_expected,
            NULL::DECIMAL(38,12) AS factor_ratio_error,
            'not_performed_no_authority_claim' AS cross_check_baseline,
            'base24-source-mapping-v1' AS normalization_version,
            'base24-source-mapping-v1' AS normalization_policy_version,
            a.source_revision,a.source_record_id AS source_row_ref,
            a.source_artifact_sha256 AS evidence_digest,
            NULL::VARCHAR AS authority_source_revision,NULL::VARCHAR AS authority_source_row_ref,
            NULL::VARCHAR AS authority_evidence_digest,false AS execution_authorized,
            count(t.corporate_action_term_id) AS native_term_count
            FROM dwd_corporate_action a LEFT JOIN dwd_corporate_action_term t
              USING(corporate_action_id) GROUP BY ALL""",
        "corporate_action_exceptions": """SELECT
            'authority-'||a.corporate_action_id AS exception_id,
            a.corporate_action_id AS action_id,a.security_id AS dwd_security_id,
            (SELECT CASE WHEN count(DISTINCT listing_code)=1 THEN min(listing_code) END
              FROM dwd_listing l WHERE l.security_id=a.security_id
              AND l.effective_from<=a.effective_at
              AND (l.effective_to IS NULL OR l.effective_to>a.effective_at))
              AS security_code,CAST(a.effective_at AS DATE) AS effective_date,
            a.knowledge_from AS known_at,CAST(a.effective_at AS DATE) AS blocked_from,
            NULL::DATE AS blocked_through,'NATIVE_ACTION_AUTHORITY_NOT_CONSUMER_QUALIFIED'
              AS reason_code,'BLOCK_AFFECTED_ACTION_CONSUMPTION' AS replay_disposition,
            a.source_record_id AS source_row_ref,a.source_artifact_sha256 AS source_evidence_digest,
            []::VARCHAR[] AS authority_source_row_refs,[]::VARCHAR[] AS authority_evidence_digests,
            'base24-action-authority-v1' AS exception_ledger_version,
            'base24-source-mapping-v1' AS normalization_policy_version,
            false AS execution_authorized FROM dwd_corporate_action a""",
    }


def _semantics(c: Any, root: Path) -> dict[str, Any]:
    counts = {}
    for name, keys in KEYS.items():
        c.execute(
            f"CREATE OR REPLACE VIEW out_{name} AS SELECT * FROM "
            f"read_parquet({_q(root / 'tables' / (name + '.parquet'))})"
        )
        _assert_zero(
            c,
            f"SELECT count(*) FROM (SELECT {','.join(keys)} FROM out_{name} "
            "GROUP BY ALL HAVING count(*)>1)",
            "DUPLICATE_SEMANTIC_KEY:" + name,
        )
        counts[name] = c.execute(f"SELECT count(*) FROM out_{name}").fetchone()[0]
    if (
        counts["daily_universe"]
        != c.execute("SELECT count(*) FROM dwd_tradability_state").fetchone()[0]
    ):
        raise ProjectionError("STATE_ROWS_LOST_OR_MULTIPLIED")
    _assert_zero(
        c,
        "SELECT count(*) FROM out_daily_ohlcv_none o FULL JOIN "
        "out_daily_universe u ON o.dwd_listing_id=u.dwd_listing_id AND o.date=u.market_date "
        "WHERE o.dwd_listing_id IS NULL OR u.dwd_listing_id IS NULL",
        "UNIVERSE_BAR_CENSUS",
    )
    _assert_zero(
        c,
        "SELECT count(*) FROM out_daily_ohlcv_none WHERE date<>CAST(observation_start AS DATE)",
        "OUTPUT_CALENDAR_DATE",
    )
    _assert_zero(
        c,
        "SELECT count(*) FROM out_price_limit_execution_daily "
        "WHERE regime_known OR status_known OR is_executable OR entry_policy_allows",
        "UNQUALIFIED_EXECUTION_ESCAPED",
    )
    _assert_zero(
        c,
        "SELECT count(*) FROM out_corporate_actions_normalized "
        "WHERE execution_authorized OR authority_evidence_digest IS NOT NULL",
        "ACTION_AUTHORITY_INVENTED",
    )
    return {
        "rows": counts,
        "bar_absent_states": c.execute(
            "SELECT count(*) FROM out_daily_ohlcv_none WHERE NOT bar_observed"
        ).fetchone()[0],
        "unknown_execution_status_retained": counts["price_limit_execution_daily"],
        "action_authority_unqualified": counts["corporate_action_exceptions"],
        "dated_identity_unavailable": c.execute(
            "SELECT count(*) FROM out_daily_universe WHERE NOT identity_qualified"
        ).fetchone()[0],
        "bars_known_after_business_date": c.execute(
            "SELECT count(*) FROM out_daily_ohlcv_none WHERE "
            "CAST(dwd_bar_knowledge_from AS DATE)>date"
        ).fetchone()[0],
        "bar_unknown_knowledge": c.execute(
            "SELECT count(*) FROM raw_dwd_security_bar WHERE knowledge_from IS NULL"
        ).fetchone()[0],
        "source_bars_without_state": c.execute(
            "SELECT count(*) FROM dwd_security_bar b WHERE NOT EXISTS "
            "(SELECT 1 FROM dwd_tradability_state t WHERE t.listing_id=b.listing_id "
            "AND t.calendar_session_id=b.calendar_session_id)"
        ).fetchone()[0],
    }


def build(view: InputView, output: Path) -> dict[str, Any]:
    """Materialize real source-only consumer data without a research/runtime call."""
    pq = importlib.import_module("pyarrow.parquet")
    pa = importlib.import_module("pyarrow")
    output = physical_path(output)
    disjoint_output(output, [m.path for files in view._files.values() for m in files])
    if output.exists():
        raise ProjectionError("NEW_PROJECTION_OUTPUT_REQUIRED")
    output.mkdir()
    (output / "native").mkdir()
    (output / "tables").mkdir()
    (output / "selected").mkdir()
    _write(output / "input-view.json", {"identity": view.identity, **view.receipt()})
    for table in TABLES:
        _LOG.info("materialize_native table=%s", table)
        # Even a fully filtered table keeps its exact native schema.
        schema = pa.unify_schemas([pq.read_schema(m.path) for m in view._files[table]])
        with pq.ParquetWriter(
            output / "native" / (table + ".parquet"), schema, compression="zstd"
        ) as writer:
            for batch in view.iter_batches(table, all_versions_for_governance=True):
                # Daily permits nullable metadata where the older Base declares
                # a required field. Union nullability, keep exact value/types.
                writer.write_table(pa.Table.from_batches([batch]).select(schema.names).cast(schema))
    _write(
        output / "native-closure.json",
        {
            "kind": "job_owned_complete_native_TMP_v1",
            "knowledge_cutoff": view.scope.knowledge_cutoff.isoformat(),
            "input_view_sha256": file_sha(output / "input-view.json"),
            "files": {
                "native/" + t + ".parquet": {
                    "sha256": file_sha(output / "native" / (t + ".parquet")),
                    "bytes": (output / "native" / (t + ".parquet")).stat().st_size,
                }
                for t in TABLES
            },
        },
    )
    return _project(output)


def project_staged(output: Path, *, expected_staging_sha256: str) -> dict[str, Any]:
    """Resume only an explicitly pinned, complete job-owned native TMP.

    The caller owns the transfer into a fresh output; source directories never
    move. Existing failed tables/selection/READY are not overwritten. Reused
    native bytes retain their materializer/input identity; this projection gets
    the actual new projector identity, not a fabricated old-code claim.
    """
    output = physical_path(output)
    p = output / "native-closure.json"
    closure = json.loads(p.read_bytes())
    required = {"native/" + t + ".parquet" for t in TABLES}
    reused = closure.get("reused_selected", {})
    selected_set = {
        "selected/" + t + "-" + a + ".parquet"
        for t in ("dwd_security_bar", "dwd_tradability_state")
        for a, _ in _months(output)
    }
    if (
        file_sha(p) != expected_staging_sha256
        or closure.get("kind") != "job_owned_complete_native_TMP_v1"
        or set(closure["files"]) != required
        or file_sha(output / "input-view.json") != closure["input_view_sha256"]
        or (reused and set(reused) != selected_set)
        or _tree(output) != required | set(reused) | {"input-view.json", "native-closure.json"}
    ):
        raise ProjectionError("STAGED_NATIVE_IDENTITY_OR_PHYSICAL_SET_DIFFERS")
    for name, value in {**closure["files"], **reused}.items():
        if (
            file_sha(output / name) != value["sha256"]
            or (output / name).stat().st_size != value["bytes"]
        ):
            raise ProjectionError("STAGED_NATIVE_MEMBER_DIFFERS")
    (output / "tables").mkdir(exist_ok=True)
    (output / "selected").mkdir(exist_ok=True)
    return _project(output)


def _project(output: Path) -> dict[str, Any]:
    input_view = json.loads((output / "input-view.json").read_bytes())
    closure = json.loads((output / "native-closure.json").read_bytes())
    cutoff = closure["knowledge_cutoff"]
    c = _connection(output)
    try:
        # A retained in-Job selection is checked against the unchanged native
        # closure, then consumed without rebuilding/replacing its bytes.
        excluded = _views(
            c, output, cutoff, build_selection=not bool(closure.get("reused_selected"))
        )
        _relations(c)
        _write_market(c, output)
        for name, sql in _queries().items():
            if name in MARKET_DATES:
                continue
            _LOG.info("project_dataset name=%s", name)
            dest = output / "tables" / (name + ".parquet")
            c.execute(f"COPY ({sql}) TO {_q(dest)} (FORMAT PARQUET,COMPRESSION ZSTD)")
        summary = _semantics(c, output)
    finally:
        c.close()
    files = {
        name: {"sha256": file_sha(output / name), "bytes": (output / name).stat().st_size}
        for name in sorted(_tree(output))
        if not name.startswith("sort-tmp/")
    }
    manifest = {
        "schema_version": VERSION,
        "input_view_digest": input_view["identity"],
        "scope": input_view["scope"],
        "projector_sha256": file_sha(Path(__file__)),
        "knowledge_cutoff": cutoff,
        "version_rule": "latest_visible_knowledge_per_native_business_key",
        "files": files,
        "summary": summary,
        "excluded_visibility": excluded,
        "consumer_admission": "NOT_GRANTED",
        "execution_authorized": False,
        "legacy_equivalence": False,
        "native_versions_retained_locally": True,
    }
    _write(output / "manifest.json", manifest)
    with (output / "READY").open("x") as stream:
        stream.write(file_sha(output / "manifest.json") + "\n")
    return manifest


def verify(output: Path) -> dict[str, Any]:
    """Cold local-only validation: bytes, exact set, native relations and outputs."""
    output = physical_path(output)
    manifest = json.loads((output / "manifest.json").read_bytes())
    if (
        (output / "READY").read_text().strip() != file_sha(output / "manifest.json")
        or manifest.get("schema_version") != VERSION
        or manifest.get("consumer_admission") != "NOT_GRANTED"
        or manifest.get("execution_authorized") is not False
    ):
        raise ProjectionError("PROJECTION_IDENTITY_OR_AUTHORITY_DIFFERS")
    required = (
        {"input-view.json", "native-closure.json"}
        | {"native/" + t + ".parquet" for t in TABLES}
        | {"tables/" + t + ".parquet" for t in KEYS}
        | {
            "selected/" + t + "-" + a + ".parquet"
            for t in ("dwd_security_bar", "dwd_tradability_state")
            for a, _ in _months(output)
        }
    )
    if set(manifest["files"]) != required or _tree(output) != required | {"manifest.json", "READY"}:
        raise ProjectionError("PROJECTION_PHYSICAL_SET_DIFFERS")
    for name, entry in manifest["files"].items():
        p = physical_path(output / name)
        if p.stat().st_size != entry["bytes"] or file_sha(p) != entry["sha256"]:
            raise ProjectionError("PROJECTION_MEMBER_DIFFERS")
    c = _connection(output)
    try:
        if _views(c, output, manifest["knowledge_cutoff"]) != manifest["excluded_visibility"]:
            raise ProjectionError("VISIBILITY_SUMMARY_DIFFERS")
        _relations(c)
        if _semantics(c, output) != manifest["summary"]:
            raise ProjectionError("SEMANTIC_SUMMARY_DIFFERS")

        # Recompute every projection from the sealed native closure. EXCEPT ALL
        # checks values, multiplicities and Decimal/time semantics, not just rows.
        def compare(name: str, query: str, actual: str) -> None:
            _assert_zero(
                c,
                f"SELECT count(*) FROM ((({query}) EXCEPT ALL ({actual})) "
                f"UNION ALL (({actual}) EXCEPT ALL ({query})))",
                "PROJECTION_SEMANTICS_DIFFERS:" + name,
            )

        for name, sql in _queries().items():
            if name not in MARKET_DATES:
                compare(name, sql, f"SELECT * FROM out_{name}")
        for a, b in _months(output):
            for name, query in _market(c, a, b).items():
                field = MARKET_DATES[name]
                actual = (
                    f"SELECT * FROM out_{name} WHERE {field}>=DATE {_q(a)} AND {field}<DATE {_q(b)}"
                )
                compare(name, query, actual)
    finally:
        c.close()
    return {"manifest_sha256": file_sha(output / "manifest.json"), **manifest["summary"]}
