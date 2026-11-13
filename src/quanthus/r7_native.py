"""Fixed, action-free native input adapter for R7 engineering review only."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from quanthus.backtest.baseline_research import _feature_panel_query
from quanthus.backtest.native_capital_qualification import member_table


def require(condition: Any, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def file_sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return "sha256:" + value.hexdigest()


def verified_members(root: Path, project: Path, request: dict[str, Any]) -> list[dict[str, Any]]:
    require(root.is_dir() and root.absolute() == root.resolve(), "R7_PHYSICAL_SOURCE")
    closure_path = project / request["closure"]["path"]
    require(file_sha(closure_path) == request["closure"]["sha256"], "R7_CLOSURE_HASH")
    members: list[dict[str, Any]] = json.loads(closure_path.read_bytes())["members"]
    require(len(members) == request["closure"]["exact_members"], "R7_MEMBER_COUNT")
    require(
        sum(item["size_bytes"] for item in members)
        == request["closure"]["exact_bytes"]
        <= request["runtime"]["max_input_bytes"],
        "R7_INPUT_BYTES",
    )
    for item in members:
        member_table(item["path"])
        path = root / "DWD" / item["path"]
        require(path.resolve() == path.absolute() and path.is_file(), "R7_SOURCE_MEMBER")
        require(path.stat().st_size == item["size_bytes"], "R7_SOURCE_SIZE")
        require(file_sha(path) == item["sha256"], "R7_SOURCE_HASH")
    return members


def materialize(
    source: Path, project: Path, request: dict[str, Any], scratch: Path
) -> dict[str, Any]:
    """Verify the whole closure before opening any native market row."""
    import duckdb

    members = verified_members(source, project, request)
    scope = request["scope"]
    start, first, end = (
        date.fromisoformat(scope[key]) for key in ("read_start", "report_start", "report_end")
    )
    require(start == date(2020, 1, 2) and first == date(2020, 4, 1), "R7_FIXED_START")
    require(end == date(2020, 4, 30), "R7_FIXED_END")
    require(scope["securities"] == ["000001", "300750", "600519"], "R7_FIXED_SECURITIES")
    codes = ",".join("'" + code + "'" for code in scope["securities"])
    db = duckdb.connect()
    db.execute("SET TimeZone='Asia/Shanghai'")
    db.execute(f"SET threads={request['runtime']['threads']}")
    db.execute(f"SET memory_limit='{request['runtime']['memory_bytes']}B'")
    db.execute("SET temp_directory=?", [str(scratch)])
    db.execute(f"SET max_temp_directory_size='{request['runtime']['scratch_bytes']}B'")
    db.execute("SET autoinstall_known_extensions=false")
    db.execute("SET autoload_known_extensions=false")
    tables: dict[str, list[str]] = {}
    for item in members:
        table = member_table(item["path"])
        if table:
            tables.setdefault(table, []).append(str(source / "DWD" / item["path"]))
    needed = {
        "dwd_listing",
        "dwd_security",
        "dwd_security_bar",
        "dwd_tradability_state",
        "dwd_trading_calendar",
        "dwd_corporate_action",
        "dwd_quality_exception",
    }
    cutoff = request["source_knowledge_cutoff"]
    for table in sorted(needed):
        db.read_parquet(tables[table], hive_partitioning=True).create_view("raw_" + table)
        db.execute(
            f"CREATE VIEW {table} AS SELECT * FROM raw_{table} "
            f"WHERE knowledge_from<=TIMESTAMPTZ '{cutoff}' AND knowledge_to IS NULL"
        )
    db.execute(f"""
        CREATE TEMP TABLE identities AS
        SELECT * FROM dwd_listing WHERE listing_code IN ({codes})
          AND CAST(effective_from AS DATE)<=DATE '{end}'
          AND (effective_to IS NULL OR CAST(effective_to AS DATE)>DATE '{start}');
    """)
    identities = _rows(db, "SELECT * FROM identities ORDER BY listing_code,effective_from")
    require(
        {row["listing_code"] for row in identities} == set(scope["securities"]),
        "R7_LISTING_COVERAGE",
    )
    require(len(identities) == 3, "R7_AMBIGUOUS_LISTING")
    require(len({row["security_id"] for row in identities}) == 3, "R7_SECURITY_IDENTITY")
    for identity in identities:
        count = db.execute(
            "SELECT count(*) FROM dwd_security WHERE security_id=? "
            "AND CAST(effective_from AS DATE)<=? "
            "AND (effective_to IS NULL OR CAST(effective_to AS DATE)>?)",
            [identity["security_id"], start, end],
        ).fetchone()[0]
        require(count == 1, "R7_SECURITY_METADATA_COVERAGE")
    holds: dict[str, list[str]] = {}
    action_counts: dict[str, int] = {}
    quality = _rows(
        db,
        f"""
        SELECT * FROM dwd_quality_exception
        WHERE CAST(affected_effective_at AS DATE)<=DATE '{end}'
          AND resolution_state NOT IN ('RESOLVED','CLOSED')
    """,
    )
    for identity in identities:
        security, code, listing = (
            identity[k] for k in ("security_id", "listing_code", "listing_id")
        )
        actions = _rows(
            db,
            """
            SELECT corporate_action_id,ex_date FROM dwd_corporate_action
            WHERE security_id=? AND action_status='IMPLEMENTED'
              AND (ex_date IS NULL OR ex_date BETWEEN ? AND ?)
        """,
            [security, start, end],
        )
        reasons = []
        if actions:
            reasons.append("native_action_or_unknown_date_requires_qualified_adapter")
        action_counts[code] = len(actions)
        for row in quality:
            key = json.loads(row["affected_business_key_json"])
            values = {str(value) for value in key.values()}
            if {code, security, listing} & values:
                reasons.append("unresolved_native_quality:" + row["quality_exception_id"])
        holds[code] = sorted(set(reasons))
    db.execute(f"""
        CREATE TEMP TABLE calendar AS
        SELECT c.* FROM dwd_trading_calendar c
        WHERE c.is_open AND CAST(c.trade_date AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'
          AND c.venue_id IN (SELECT venue_id FROM identities);
    """)
    _unique(db, "SELECT venue_id,trade_date FROM calendar", "R7_CALENDAR_DUPLICATE")
    _unique(db, "SELECT calendar_session_id FROM calendar", "R7_SESSION_ID_DUPLICATE")
    sessions = _rows(db, "SELECT venue_id,trade_date FROM calendar ORDER BY venue_id,trade_date")
    by_venue: dict[str, set[date]] = {}
    for session in sessions:
        by_venue.setdefault(session["venue_id"], set()).add(session["trade_date"])
    require(set(by_venue) == {row["venue_id"] for row in identities}, "R7_CALENDAR_VENUE_GAP")
    require(
        all(days == next(iter(by_venue.values())) for days in by_venue.values()),
        "R7_CALENDAR_SESSION_GAP",
    )
    db.execute("""
        CREATE TEMP TABLE unqualified_panel AS
        SELECT i.listing_code AS code,i.security_id,i.listing_id,i.venue_id,
               CAST(c.trade_date AS DATE) market_date,c.calendar_session_id,c.close_at,
               b.open,b.high,b.low,b.close,b.volume,b.turnover,
               b.source_record_id bar_source_record_id,b.source_artifact_sha256 bar_source_sha256,
               b.knowledge_from bar_knowledge_from,b.knowledge_to bar_knowledge_to,
               b.currency,b.price_basis,b.observation_start,b.observation_end,
               t.is_tradable,t.can_buy,t.can_sell,t.upper_price,t.lower_price,
               t.source_artifact_sha256 state_source_sha256,t.knowledge_from state_knowledge_from,
               t.observation_start state_observation_start,t.observation_end state_observation_end
        FROM identities i JOIN calendar c ON i.venue_id=c.venue_id
          AND CAST(c.trade_date AS DATE)>=CAST(i.effective_from AS DATE)
          AND (i.effective_to IS NULL OR CAST(c.trade_date AS DATE)<CAST(i.effective_to AS DATE))
        LEFT JOIN dwd_security_bar b ON b.listing_id=i.listing_id
          AND b.calendar_session_id=c.calendar_session_id AND b.interval_size='1d'
          AND b.price_basis='UNADJUSTED'
        LEFT JOIN dwd_tradability_state t ON t.listing_id=i.listing_id
          AND t.calendar_session_id=c.calendar_session_id;
    """)
    db.execute("""
        CREATE TEMP TABLE native_panel AS SELECT *,
          (currency='CNY' AND price_basis='UNADJUSTED' AND close_at IS NOT NULL
           AND CAST(close_at AS DATE)=market_date
           AND CAST(observation_start AS DATE)=market_date
           AND CAST(observation_end AS DATE)=market_date
           AND observation_start<=observation_end AND observation_end<=close_at
           AND isfinite(open) AND isfinite(high) AND isfinite(low) AND isfinite(close)
           AND isfinite(volume) AND isfinite(turnover)
           AND open>0 AND low>0 AND high>=open AND open>=low
           AND low<=close AND close<=high AND volume>=0 AND turnover>=0) IS TRUE
           AS bar_admitted,
          (CAST(state_observation_start AS DATE)=market_date
           AND CAST(state_observation_end AS DATE)=market_date
           AND state_observation_start<=state_observation_end
           AND state_observation_end<=close_at) IS TRUE AS state_admitted
        FROM unqualified_panel;
    """)
    _unique(db, "SELECT code,market_date FROM native_panel", "R7_NATIVE_DUPLICATE")
    db.execute("""
        CREATE TEMP VIEW source_daily_universe AS
        SELECT security_id,market_date FROM native_panel;
        CREATE TEMP VIEW source_daily_ohlcv_none AS
        SELECT security_id,market_date AS date,open,high,low,close,volume,turnover AS amount
        FROM native_panel WHERE bar_admitted;
        CREATE TEMP VIEW source_price_limit_execution_daily AS
        SELECT security_id,market_date AS execution_date,
               coalesce(is_tradable AND can_buy AND state_admitted,false) entry_policy_allows,
               coalesce(is_tradable AND state_admitted,false) is_executable,
               NULL::DOUBLE upper_limit_pct,NULL::DOUBLE lower_limit_pct,
               upper_price AS upper_limit_price,lower_price AS lower_limit_price
        FROM native_panel;
        CREATE TEMP TABLE source_corporate_actions_normalized_mapped (
            security_id VARCHAR,effective_date DATE,cash_dividend_per_share DOUBLE,
            bonus_share_ratio DOUBLE,known_at TIMESTAMPTZ);
        CREATE TEMP TABLE source_corporate_action_exceptions_mapped (
            security_id VARCHAR,blocked_from DATE,blocked_through DATE);
    """)
    for identity in identities:
        if holds[identity["listing_code"]]:
            db.execute(
                "INSERT INTO source_corporate_action_exceptions_mapped VALUES (?,?,NULL)",
                [identity["security_id"], start],
            )
    features = _rows(db, _feature_panel_query(start, end))
    native = _rows(db, "SELECT * FROM native_panel ORDER BY code,market_date")
    db.close()
    return {
        "native": native,
        "features": features,
        "identities": identities,
        "holds": holds,
        "action_counts": action_counts,
        "members": members,
    }


def _rows(db: Any, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    result = db.execute(sql, params or [])
    names = [item[0] for item in result.description]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def _unique(db: Any, sql: str, reason: str) -> None:
    count = db.execute(
        f"SELECT count(*) FROM (SELECT * FROM ({sql}) x GROUP BY ALL HAVING count(*)>1)"
    ).fetchone()[0]
    require(count == 0, reason)
