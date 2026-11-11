"""Frozen R1 v2 descriptive screen; no replay or trading activation."""

from __future__ import annotations

from typing import Any

SSE = "venue_bec59e31eb6bb28111a10d7e6d2c689f"
SZSE = "venue_8c0bd9088a0dc88f28e5e7fa8efdce87"
TABLES = (
    "dwd_security_bar",
    "dwd_trading_calendar",
    "dwd_tradability_state",
    "dwd_listing",
    "dwd_security",
)


def positive(column: str) -> str:
    return f"({column} IS NOT NULL AND isfinite({column}) AND {column}>0)"


def calculate(
    connection: Any, request: dict[str, Any], paths: dict[str, list[str]]
) -> dict[str, Any]:
    """Only exact verified paths reach this function. Materialize the panel once."""
    scope, filters = request["scope"], request["screen"]
    for table in TABLES:
        if not paths.get(table):
            raise ValueError(f"missing required table: {table}")
        connection.read_parquet(paths[table], hive_partitioning=True).create_view(table)
    connection.execute("SET TimeZone='Asia/Shanghai'")
    connection.execute(f"""
    CREATE TEMP TABLE calendar AS
    SELECT calendar_session_id, venue_id, CAST(trade_date AS DATE) AS trade_date,
      lead(calendar_session_id) OVER (PARTITION BY venue_id ORDER BY trade_date) AS next_session
    FROM dwd_trading_calendar
    WHERE knowledge_to IS NULL AND is_open
      AND venue_id IN ('{SSE}','{SZSE}')
      AND CAST(trade_date AS DATE) BETWEEN DATE '{scope["calendar_start"]}'
        AND DATE '{scope["execution_end"]}';
    """)
    for columns in ("calendar_session_id", "venue_id,trade_date"):
        if connection.execute(
            f"SELECT count(*) FROM (SELECT {columns} FROM calendar "
            f"GROUP BY {columns} HAVING count(*)<>1)"
        ).fetchone()[0]:
            raise ValueError("ambiguous calendar key")
    calendar_prefix = connection.execute(
        f"SELECT venue_id,count(*) FROM calendar WHERE trade_date<DATE '{scope['decision_start']}' "
        "GROUP BY venue_id ORDER BY 1"
    ).fetchall()
    if len(calendar_prefix) != 2 or any(
        x[1] < filters["min_listing_sessions"] for x in calendar_prefix
    ):
        raise ValueError("insufficient calendar prefix for exact age threshold")
    # Listing age uses all declared calendar metadata, separately from market warmup.
    connection.execute(f"""
    CREATE TEMP TABLE ages AS
    SELECT l.listing_id,l.security_id,l.venue_id,c.calendar_session_id,c.trade_date,c.next_session,
      row_number() OVER(PARTITION BY l.listing_id ORDER BY c.trade_date)
        AS listing_age_floor_sessions
    FROM dwd_listing l JOIN calendar c ON l.venue_id=c.venue_id
    WHERE l.knowledge_to IS NULL
      AND c.trade_date>=CAST(l.effective_from AS DATE)
      AND (l.effective_to IS NULL OR c.trade_date<CAST(l.effective_to AS DATE));
    CREATE TEMP VIEW bars AS
      SELECT * FROM dwd_security_bar WHERE knowledge_to IS NULL AND interval_size='1d'
      AND price_basis='UNADJUSTED'
      AND CAST(observation_start AS DATE) BETWEEN DATE '{scope["read_start"]}'
        AND DATE '{scope["execution_end"]}';
    CREATE TEMP VIEW states AS
      SELECT * FROM dwd_tradability_state WHERE knowledge_to IS NULL
      AND CAST(observation_start AS DATE) BETWEEN DATE '{scope["read_start"]}'
        AND DATE '{scope["execution_end"]}';
    CREATE TEMP TABLE panel AS
    SELECT a.*,s.instrument_type IN ('common_equity','equity') AS security_ok,
      CAST(b.open AS DOUBLE) AS op,CAST(b.close AS DOUBLE) AS px,
      CAST(b.turnover AS DOUBLE) AS amount,CAST(t.upper_price AS DOUBLE) AS upper_price,
      t.is_tradable,t.can_buy
    FROM ages a
    LEFT JOIN dwd_security s ON s.security_id=a.security_id AND s.knowledge_to IS NULL
      AND a.trade_date>=CAST(s.effective_from AS DATE)
      AND (s.effective_to IS NULL OR a.trade_date<CAST(s.effective_to AS DATE))
    LEFT JOIN bars b ON b.listing_id=a.listing_id
      AND b.calendar_session_id=a.calendar_session_id
    LEFT JOIN states t ON t.listing_id=a.listing_id
      AND t.calendar_session_id=a.calendar_session_id
    WHERE a.trade_date>=DATE '{scope["read_start"]}';
    """)
    duplicate_count = connection.execute("""
    SELECT count(*) FROM (SELECT listing_id,trade_date FROM panel
                         GROUP BY 1,2 HAVING count(*)<>1)
    """).fetchone()[0]
    if duplicate_count:
        raise ValueError("ambiguous listing/date join; no deduplication permitted")
    connection.execute(f"""
    CREATE TEMP TABLE returns AS
    SELECT *,lag(px) OVER w AS previous_close,lag(px,20) OVER w AS close20,
      CASE WHEN {positive("px")} AND {positive("lag(px) OVER w")}
        THEN px/(lag(px) OVER w)-1 END AS unadjusted_return
    FROM panel WINDOW w AS(PARTITION BY listing_id ORDER BY trade_date);
    CREATE TEMP TABLE features AS
    SELECT *,count(CASE WHEN {positive("px")} THEN 1 END) OVER history21 AS positive_closes21,
      count(unadjusted_return) OVER history20 AS returns20,
      stddev_samp(unadjusted_return) OVER history20*sqrt(252) AS return_vol20,
      count(CASE WHEN amount IS NOT NULL AND isfinite(amount) AND amount>=0
        THEN 1 END) OVER prior20 AS amounts20,
      avg(CASE WHEN amount IS NOT NULL AND isfinite(amount) AND amount>=0
        THEN amount END) OVER prior20 AS average_amount20,
      CASE WHEN {positive("px")} AND {positive("close20")} THEN px/close20-1 END AS momentum20
    FROM returns WINDOW
      history21 AS(PARTITION BY listing_id ORDER BY trade_date
        ROWS BETWEEN 20 PRECEDING AND CURRENT ROW),
      history20 AS(PARTITION BY listing_id ORDER BY trade_date
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
      prior20 AS(PARTITION BY listing_id ORDER BY trade_date
        ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING);
    CREATE TEMP TABLE labelled AS
    SELECT f.*,
      ({positive("f.px")} AND f.security_ok IS TRUE AND f.is_tradable IS TRUE) AS universe,
      (f.positive_closes21=21 AND f.returns20=20 AND f.amounts20=20
       AND f.listing_age_floor_sessions>={filters["min_listing_sessions"]}
       AND f.average_amount20>={filters["min_avg_turnover_prior20"]}
       AND f.return_vol20<={filters["max_unadjusted_close_return_vol20"]}
       AND abs(f.unadjusted_return)<={filters["max_abs_unadjusted_close_return"]}
       AND f.momentum20 BETWEEN {filters["unadjusted_momentum20_band"][0]}
         AND {filters["unadjusted_momentum20_band"][1]}) IS TRUE AS selected,
      CASE
       WHEN n.listing_id IS NULL OR n.security_ok IS NOT TRUE THEN 'missing_next_identity'
       WHEN NOT {positive("n.op")} THEN 'missing_or_invalid_open'
       WHEN n.is_tradable IS NULL OR n.can_buy IS NULL THEN 'missing_state'
       WHEN NOT n.is_tradable THEN 'suspended'
       WHEN NOT n.can_buy THEN 'cannot_buy'
       WHEN NOT {positive("n.upper_price")} THEN 'missing_or_invalid_upper'
       WHEN n.op>=n.upper_price-0.005 THEN 'upper_price_block'
       ELSE 'pass' END AS next_open_reason
    FROM features f LEFT JOIN panel n ON n.listing_id=f.listing_id
      AND n.calendar_session_id=f.next_session
    WHERE f.trade_date BETWEEN DATE '{scope["decision_start"]}'
      AND DATE '{scope["decision_end"]}';
    """)

    def summary(predicate: str) -> dict[str, Any]:
        count, passed, days, listings = connection.execute(
            "SELECT count(*),count(*) FILTER(WHERE next_open_reason='pass'),"
            f"count(DISTINCT trade_date),count(DISTINCT listing_id) FROM labelled WHERE {predicate}"
        ).fetchone()
        reasons = dict(
            connection.execute(
                f"SELECT next_open_reason,count(*) FROM labelled WHERE {predicate} "
                "GROUP BY 1 ORDER BY 1"
            ).fetchall()
        )
        if sum(reasons.values()) != count or reasons.get("pass", 0) != passed:
            raise ValueError("reason accounting differs")
        return {
            "opportunities": count,
            "price_guard_pass": passed,
            "blocked": count - passed,
            "blocked_rate": (count - passed) / count if count else None,
            "decision_days": days,
            "listings": listings,
            "reasons": reasons,
        }

    candidate = "universe AND selected"
    by_year = {
        str(year): {
            "candidate": summary(f"{candidate} AND year(trade_date)={year}"),
            "control": summary(f"universe AND year(trade_date)={year}"),
        }
        for year in (2020, 2021)
    }
    by_exchange = {
        name: {
            "candidate": summary(f"{candidate} AND venue_id='{venue}'"),
            "control": summary(f"universe AND venue_id='{venue}'"),
        }
        for name, venue in (("SSE", SSE), ("SZSE", SZSE))
    }
    total = summary(candidate)
    concentration = connection.execute(
        f"SELECT count(*) AS n FROM labelled WHERE {candidate} GROUP BY listing_id ORDER BY n DESC"
    ).fetchall()
    top1 = concentration[0][0] / total["opportunities"] if concentration else None
    top10 = (
        sum(x[0] for x in concentration[:10]) / total["opportunities"] if concentration else None
    )
    d = request["decision"]
    sufficient = (
        total["opportunities"] >= d["min_nominal"]
        and total["listings"] >= d["min_nominal_listings"]
        and top1 is not None
        and top1 <= d["max_top_listing_share"]
        and top10 is not None
        and top10 <= d["max_top10_listing_share"]
        and all(
            x["candidate"]["opportunities"] >= d["min_nominal_per_year"]
            and x["control"]["decision_days"] >= d["min_decision_days_per_year"]
            for x in by_year.values()
        )
    )
    branch = "insufficient"
    if sufficient:
        branch = (
            "material"
            if (
                total["blocked_rate"] >= d["material_block_rate"]
                or any(
                    x["candidate"]["blocked_rate"] >= d["material_year_block_rate"]
                    for x in by_year.values()
                )
            )
            else "small"
        )
    return {
        "candidate": total,
        "control": summary("universe"),
        "by_year": by_year,
        "by_exchange": by_exchange,
        "top_listing_share": top1,
        "top10_listing_share": top10,
        "sample_sufficient": sufficient,
        "decision_branch": branch,
        "decision": d["branches"][branch],
        "duplicate_listing_date_keys": duplicate_count,
        "panel_rows": connection.execute("SELECT count(*) FROM panel").fetchone()[0],
        "claim_ceiling": "Fixed retrospective raw-price screen and next-open price guard only; "
        "existing P2 constraints retained; strategy economics not evaluated.",
    }
