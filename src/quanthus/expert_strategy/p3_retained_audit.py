"""Input-only equivalence and coverage audit for the frozen retained P3 projection.

One Reader owns the database and transition TMP. This module materializes each shared
relation once; daily/yearly ledgers reuse it. It never emits a replacement projection.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from quanthus.backtest.historical_frames import (
    FRAME_READER_VERSION,
    HistoricalFrameAuditReport,
    _read_parquet_expression,
)
from quanthus.expert_evaluation.serialization import digest_value

VENUES = {
    "venue_bec59e31eb6bb28111a10d7e6d2c689f": "SSE",
    "venue_8c0bd9088a0dc88f28e5e7fa8efdce87": "SZSE",
}

# Exactly the producer's fields and types: no floating conversion or tolerance.
PROJECTION = {
    "dwd_security_id": "u.dwd_security_id",
    "dwd_listing_id": "u.dwd_listing_id",
    "listing_code": "u.stock_code",
    "dwd_company_id": "u.dwd_company_id",
    "venue_id": "u.exchange",
    "instrument_type": "u.dwd_instrument_type",
    "session_date": "u.market_date",
    "listing_sessions": "u.listing_sessions",
    "open_price": "b.dwd_open_decimal",
    "high_price": "b.dwd_high_decimal",
    "low_price": "b.dwd_low_decimal",
    "close_price": "b.dwd_close_decimal",
    "turnover": "b.dwd_turnover_decimal",
    "is_tradable": "CASE l.observed_tradestatus WHEN '1' THEN true WHEN '0' THEN false END",
    "adjustment_effective": "f.factor_boundary_count IS NOT NULL",
    "factor_effective_from": "f.factor_effective_from",
    "factor_boundary_count": "f.factor_boundary_count",
    "dwd_bar_knowledge_from": "b.dwd_bar_knowledge_from",
    "dwd_security_knowledge_from": "u.dwd_security_knowledge_from",
    "dwd_listing_knowledge_from": "u.dwd_listing_knowledge_from",
    "dwd_state_knowledge_from": "l.dwd_state_knowledge_from",
    "dwd_factor_knowledge_from": "f.dwd_factor_knowledge_from",
    "dwd_bar_source_record_id": "b.dwd_bar_source_record_id",
    "dwd_bar_source_revision": "b.dwd_bar_source_revision",
    "dwd_bar_source_artifact_sha256": "b.dwd_bar_source_artifact_sha256",
    "dwd_security_effective_from": "u.dwd_security_effective_from",
    "dwd_listing_effective_from": "u.dwd_listing_effective_from",
}


def rows(connection: Any, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, list(args))
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def count(connection: Any, sql: str) -> int:
    return int(connection.execute(sql).fetchone()[0])


def require_zero(connection: Any, name: str, sql: str) -> None:
    observed = count(connection, sql)
    if observed:
        raise ValueError(f"{name}:{observed}")


def calendar_audit(
    connection: Any, base_paths: list[Path], daily_paths: list[Path], scope: dict[str, Any]
) -> dict[str, Any]:
    """Compare external authoritative sessions, including whole dates absent on both venues."""
    before = date.fromisoformat(scope["decision_start"])
    end = date.fromisoformat(scope["execution_end"])
    through = date.fromisoformat(scope["base_through"])
    venue_sql = ",".join("'" + v + "'" for v in VENUES)
    # Base has future calendar metadata; the original projection uses only the bound Base prefix.
    connection.execute(
        f"""CREATE TEMP TABLE retained_authoritative_calendar AS
        SELECT venue_id, CAST(trade_date AS DATE) AS trade_date, is_open
        FROM {_read_parquet_expression(base_paths)}
        WHERE knowledge_to IS NULL AND venue_id IN ({venue_sql}) AND trade_date <= ?
        UNION ALL
        SELECT venue_id, CAST(trade_date AS DATE), is_open
        FROM {_read_parquet_expression(daily_paths)}
        WHERE venue_id IN ({venue_sql}) AND trade_date > ? AND trade_date <= ?""",
        [through, through, end],
    )
    require_zero(
        connection,
        "AUTHORITATIVE_CALENDAR_DUPLICATE_OR_NULL",
        """
        SELECT count(*) FROM (
          SELECT venue_id,trade_date FROM retained_authoritative_calendar
          GROUP BY ALL HAVING count(*)<>1 OR trade_date IS NULL OR bool_or(is_open IS NULL)
        )""",
    )
    require_zero(
        connection,
        "AUTHORITATIVE_CALENDAR_VENUE_DIVERGENCE",
        """
        SELECT count(*) FROM (
          SELECT trade_date FROM retained_authoritative_calendar GROUP BY trade_date
          HAVING count(DISTINCT venue_id)<>2 OR min(is_open)<>max(is_open)
        )""",
    )
    warmup = [
        r[0]
        for r in connection.execute(
            """
        SELECT trade_date FROM retained_authoritative_calendar WHERE is_open AND trade_date < ?
        GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?
        """,
            [before, scope["warmup_open_sessions"]],
        ).fetchall()
    ][::-1]
    if (
        len(warmup) != scope["warmup_open_sessions"]
        or warmup[0].isoformat() != scope["history_start"]
    ):
        raise ValueError("EXACT_WARMUP_SET_UNAVAILABLE")
    connection.execute(
        """CREATE TEMP TABLE retained_expected_dates AS
        SELECT trade_date AS market_date FROM retained_authoritative_calendar
        WHERE is_open AND trade_date BETWEEN ? AND ? GROUP BY trade_date
        """,
        [warmup[0], end],
    )
    require_zero(
        connection,
        "RETAINED_CALENDAR_DUPLICATE_OR_UNQUALIFIED",
        """
        SELECT count(*) FROM (
          SELECT exchange,trade_date FROM replay_calendar GROUP BY ALL
          HAVING count(*)<>1 OR exchange NOT IN ('SSE','SZSE')
            OR trade_date IS NULL OR bool_or(is_open IS NULL)
        )""",
    )
    require_zero(
        connection,
        "RETAINED_OPEN_CALENDAR_SET_DIFFERS",
        """
        SELECT count(*) FROM (
          (SELECT exchange,trade_date FROM replay_calendar WHERE is_open=1
           EXCEPT SELECT v.exchange,d.market_date FROM retained_expected_dates d
           CROSS JOIN (VALUES ('SSE'),('SZSE')) v(exchange))
          UNION ALL
          (SELECT v.exchange,d.market_date FROM retained_expected_dates d
           CROSS JOIN (VALUES ('SSE'),('SZSE')) v(exchange)
           EXCEPT SELECT exchange,trade_date FROM replay_calendar WHERE is_open=1)
        )""",
    )
    dates = [
        r[0]
        for r in connection.execute(
            "SELECT market_date FROM retained_expected_dates ORDER BY market_date"
        ).fetchall()
    ]
    decisions = [d for d in dates if before <= d <= date.fromisoformat(scope["decision_end"])]
    pairs = [(d, dates[dates.index(d) + 1]) for d in decisions if dates.index(d) + 1 < len(dates)]
    if len(pairs) != len(decisions) or not pairs or pairs[-1][1] != end:
        raise ValueError("EXACT_EXECUTION_CALENDAR_UNAVAILABLE")
    connection.execute(
        "CREATE TEMP TABLE retained_date_pairs(decision_date DATE, execution_date DATE)"
    )
    connection.executemany("INSERT INTO retained_date_pairs VALUES (?,?)", pairs)
    payload = {
        "warmup_dates": [d.isoformat() for d in warmup],
        "open_dates": [d.isoformat() for d in dates],
        "date_pairs": [[a.isoformat(), b.isoformat()] for a, b in pairs],
        "external_calendar_set_equal": True,
        "warmup_set_equal": True,
    }
    return {**payload, "digest": digest_value(payload)}


def names_for_projection() -> list[str]:
    return ["extra_candidate", "missing_candidate", *("diff_" + f for f in PROJECTION)]


def projection_audit(connection: Any, candidate: Path, factor: Path) -> dict[str, Any]:
    """Full relational equality against the original producer, without another Parquet build."""
    connection.execute(
        f"CREATE VIEW retained_candidate AS SELECT * FROM {_read_parquet_expression([candidate])}"
    )
    actual_columns = {r[0] for r in connection.execute("DESCRIBE retained_candidate").fetchall()}
    if actual_columns != set(PROJECTION):
        raise ValueError("RETAINED_PROJECTION_SCHEMA_DIFFERS")
    for table, columns in (
        ("replay_universe", "dwd_security_id,dwd_listing_id,market_date"),
        ("retained_candidate", "dwd_security_id,dwd_listing_id,session_date"),
    ):
        require_zero(
            connection,
            "STABLE_IDENTITY_DUPLICATE",
            f"""
            SELECT count(*) FROM (SELECT {columns} FROM {table}
            GROUP BY ALL HAVING count(*)<>1)""",
        )
        nulls = " OR ".join(f"{c} IS NULL" for c in columns.split(","))
        require_zero(
            connection, "STABLE_IDENTITY_NULL", f"SELECT count(*) FROM {table} WHERE {nulls}"
        )
    for table, column in (
        ("replay_universe", "market_date"),
        ("retained_candidate", "session_date"),
    ):
        require_zero(
            connection,
            "PROJECTION_DATE_OUTSIDE_EXACT_CALENDAR",
            f"""
            SELECT count(*) FROM {table} t WHERE NOT EXISTS
            (SELECT 1 FROM retained_expected_dates d WHERE d.market_date=t.{column})""",
        )
    for table, column in (("replay_bars", "date"), ("replay_limits", "execution_date")):
        # Both the old code key and the stable namespace must agree before comparing values.
        mismatch = " OR ".join(
            f"x.{c} IS DISTINCT FROM u.{c}"
            for c in ("dwd_security_id", "dwd_listing_id", "dwd_company_id", "dwd_venue_id")
        )
        require_zero(
            connection,
            "SOURCE_STABLE_IDENTITY_DIFFERS",
            f"""
            SELECT count(*) FROM {table} x JOIN replay_universe u
            ON u.security_id=x.security_id AND u.market_date=x.{column}
            WHERE {mismatch}""",
        )
    connection.execute("""CREATE TEMP TABLE retained_ranked_universe AS
        SELECT *, row_number() OVER (PARTITION BY dwd_listing_id ORDER BY market_date)
        AS listing_sessions FROM replay_universe""")
    connection.execute(f"""CREATE TEMP TABLE retained_factor_boundary AS
        SELECT security_id,
          CAST(timezone('Asia/Shanghai',effective_from) AS DATE) AS effective_date,
          min(effective_from) AS factor_effective_from,
          max(knowledge_from) AS dwd_factor_knowledge_from, count(*) AS factor_boundary_count
        FROM {_read_parquet_expression([factor])} WHERE knowledge_to IS NULL GROUP BY ALL""")
    differences = ",\n".join(
        f"c.{field} IS DISTINCT FROM ({expression}) AS diff_{field}"
        for field, expression in PROJECTION.items()
    )
    # This single boolean/identity TMP feeds all checks and ledgers; values are not copied out.
    connection.execute(f"""CREATE TEMP TABLE retained_projection_comparison AS
        SELECT coalesce(u.market_date,c.session_date) AS market_date,
          coalesce(u.dwd_security_id,c.dwd_security_id) AS dwd_security_id,
          u.dwd_listing_id, u.security_id AS security_code,
          u.dwd_security_id IS NULL AS extra_candidate,
          c.dwd_security_id IS NULL AS missing_candidate,
          {differences},
          {_coverage_expressions()}
        FROM retained_ranked_universe u FULL JOIN retained_candidate c
          ON c.dwd_security_id=u.dwd_security_id AND c.dwd_listing_id=u.dwd_listing_id
          AND c.session_date=u.market_date
        LEFT JOIN replay_bars b ON b.security_id=u.security_id AND b.date=u.market_date
        LEFT JOIN replay_limits l ON l.security_id=u.security_id AND l.execution_date=u.market_date
        LEFT JOIN retained_factor_boundary f
          ON f.security_id=u.dwd_security_id AND f.effective_date=u.market_date
        LEFT JOIN replay_unexplained_price_transitions t
          ON t.security_id=u.security_id AND t.market_date=u.market_date""")
    identity = {"market_date", "dwd_security_id", "dwd_listing_id", "security_code"}
    flags = [
        r[0]
        for r in connection.execute("DESCRIBE retained_projection_comparison").fetchall()
        if r[0] not in identity | set(names_for_projection())
    ]
    connection.execute(
        "CREATE VIEW retained_coverage AS SELECT "
        + ",".join(["market_date", "dwd_security_id", "dwd_listing_id", "security_code", *flags])
        + " FROM retained_projection_comparison"
    )
    names = names_for_projection()
    counts = rows(
        connection,
        "SELECT "
        + ",".join(f"count(*) FILTER (WHERE {n}) AS {n}" for n in names)
        + " FROM retained_projection_comparison",
    )[0]
    if any(counts.values()):
        raise ValueError("RETAINED_PROJECTION_DIFFERS:" + str(counts))
    return {
        "row_count": count(connection, "SELECT count(*) FROM retained_candidate"),
        "compared_field_count": len(PROJECTION),
        "mismatch_counts": counts,
        "candidate_rebuilt": False,
        "exact_decimal_comparison": True,
    }


def _coverage_expressions() -> str:
    values = ("open", "high", "low", "close", "volume", "amount")
    invalid = " OR ".join(f"b.{v} IS NULL OR NOT isfinite(b.{v})" for v in values)
    invalid += (
        " OR b.open<=0 OR b.high<=0 OR b.low<=0 OR b.close<=0 OR b.volume<0"
        " OR b.amount<0 OR b.low>b.high OR b.open<b.low OR b.open>b.high"
        " OR b.close<b.low OR b.close>b.high"
    )
    late = ",\n".join(
        f"c.{name} > timezone('Asia/Shanghai',c.session_date::TIMESTAMP + INTERVAL 15 HOUR)"
        f" AS late_{name}"
        for name in (
            "dwd_bar_knowledge_from",
            "dwd_listing_knowledge_from",
            "dwd_security_knowledge_from",
            "dwd_state_knowledge_from",
            "dwd_factor_knowledge_from",
        )
    )
    return f"""
          b.security_id IS NULL AS missing_bar,
          b.security_id IS NOT NULL AND ({invalid}) AS invalid_bar,
          l.security_id IS NULL AS missing_limits,
          l.observed_tradestatus='0' AS suspended,
          l.reason_code='ZERO_ACTIVITY_OBSERVATION' AS zero_activity,
          NOT coalesce(l.is_executable,false) AS source_non_executable,
          EXISTS (SELECT 1 FROM replay_action_exceptions_mapped e
            WHERE e.security_id=u.security_id AND e.blocked_from<=u.market_date
              AND (e.blocked_through IS NULL OR e.blocked_through>=u.market_date))
            AS corporate_action_blocked,
          t.security_id IS NOT NULL AS unexplained_price_transition,
          {late}
    """


def coverage_ledger(connection: Any) -> dict[str, Any]:
    """Aggregate the single shared relation; never repeat the source JOIN."""
    flags = [r[0] for r in connection.execute("DESCRIBE retained_coverage").fetchall()][4:]
    aggregates = ",".join(f"count(*) FILTER (WHERE a.{f}) AS {f}" for f in flags)
    daily = rows(
        connection,
        f"""
        SELECT d.market_date, count(a.dwd_security_id) AS universe_pairs,
          count(DISTINCT a.dwd_security_id) AS securities,{aggregates}
        FROM retained_expected_dates d LEFT JOIN retained_coverage a USING(market_date)
        GROUP BY d.market_date ORDER BY d.market_date""",
    )
    if any(r["universe_pairs"] == 0 for r in daily):
        raise ValueError("EMPTY_AUTHORITATIVE_OPEN_SESSION")
    yearly = rows(
        connection,
        f"""
        SELECT year(d.market_date) AS year, count(DISTINCT d.market_date) AS open_dates,
          count(a.dwd_security_id) AS universe_pairs,
          count(DISTINCT a.dwd_security_id) AS securities,{aggregates}
        FROM retained_expected_dates d LEFT JOIN retained_coverage a USING(market_date)
        GROUP BY year(d.market_date) ORDER BY year""",
    )
    pairs = rows(
        connection,
        """
        SELECT p.decision_date,p.execution_date,d.universe_pairs AS decision_universe_pairs,
          e.universe_pairs AS execution_universe_pairs
        FROM retained_date_pairs p
        JOIN (SELECT market_date,count(*) AS universe_pairs FROM retained_coverage GROUP BY 1) d
          ON d.market_date=p.decision_date
        JOIN (SELECT market_date,count(*) AS universe_pairs FROM retained_coverage GROUP BY 1) e
          ON e.market_date=p.execution_date ORDER BY p.decision_date""",
    )
    for collection in (daily, pairs):
        for row in collection:
            for key, value in row.items():
                if isinstance(value, date):
                    row[key] = value.isoformat()
    return {
        "daily": daily,
        "yearly": yearly,
        "decision_execution_denominators": pairs,
        "reason_counts_overlap": True,
        "raw_knowledge_times_preserved": True,
        "visibility_policy": "p3-strat-003-recent-history-effective-view-v1",
        "missing_data_disposition": "block_new_entry_no_forced_sale",
        "target_maturity": "NOT_EVALUATED_INPUT_ONLY_NO_LABELS",
    }


def replay_report(reader: Any, start: date, end: date) -> HistoricalFrameAuditReport:
    """Use the Reader's session contract and the shared evidence without another source JOIN."""
    pairs = reader._date_pairs(start, end)
    connection = reader._ensure_open()
    first, last = pairs[0][1], pairs[-1][1]
    observed = rows(
        connection,
        """
      SELECT count(*) FILTER(WHERE NOT missing_limits) AS evidence,
        count(*) FILTER(WHERE missing_limits) AS missing_evidence,
        count(DISTINCT security_code) AS securities,
        count(*) FILTER(WHERE suspended) AS suspended,
        count(*) FILTER(WHERE missing_bar) AS missing_bar,
        count(*) FILTER(WHERE invalid_bar) AS invalid_bar,
        count(*) FILTER(WHERE source_non_executable OR corporate_action_blocked
          OR unexplained_price_transition) AS non_executable,
        count(*) FILTER(WHERE corporate_action_blocked) AS blocked,
        count(*) FILTER(WHERE unexplained_price_transition) AS transitions
      FROM retained_coverage WHERE market_date BETWEEN ? AND ?
      """,
        (first, last),
    )[0]
    decision_count = connection.execute(
        """
      SELECT count(*) FROM retained_coverage WHERE market_date BETWEEN ? AND ?
      """,
        [pairs[0][0], pairs[-1][0]],
    ).fetchone()[0]
    action_count = connection.execute(
        """
      SELECT count(*) FROM replay_actions_mapped WHERE effective_date BETWEEN ? AND ?
      """,
        [first, last],
    ).fetchone()[0]
    report = HistoricalFrameAuditReport(
        bundle_snapshot_id=reader.bundle.snapshot.snapshot_id,
        bundle_digest=reader.bundle.digest,
        reader_version=FRAME_READER_VERSION,
        scope_start=start,
        scope_end=end,
        first_decision_date=pairs[0][0],
        last_decision_date=pairs[-1][0],
        first_execution_date=first,
        last_execution_date=last,
        frame_count=len(pairs),
        decision_observation_count=decision_count,
        execution_evidence_count=observed["evidence"],
        missing_execution_evidence_count=observed["missing_evidence"],
        security_count=observed["securities"],
        suspended_evidence_count=observed["suspended"],
        missing_bar_count=observed["missing_bar"],
        invalid_bar_count=observed["invalid_bar"],
        non_executable_count=observed["non_executable"],
        corporate_action_count=action_count,
        corporate_action_blocked_pair_count=observed["blocked"],
        unexplained_price_transition_count=observed["transitions"],
    )
    if not report.ready_for_strategy_replay:
        raise ValueError("RETAINED_REPLAY_INPUT_EMPTY")
    frame = reader._frame(*pairs[0])
    if frame.decision_view is None or not frame.decision_view.securities():
        raise ValueError("RETAINED_FIRST_DECISION_EMPTY")
    return report
