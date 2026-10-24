"""P10 source-only triage of the retained historical replay's existing guards.

No classification grants consumer admission or proves a price wrong. In
particular, a provider return or adjustment factor cannot supply an entitlement,
subscription cash flow, missing acquisition time, or an optional-action policy.
The existing replay guard, exceptions, prices and feature eligibility are untouched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

VERSION = "p10-historical-held-source-classification-v1"
START, END = date(2012, 1, 4), date(2021, 12, 31)


def classify_facts(row: Mapping[str, Any]) -> str:
    """Classify already guarded facts, with unknown references kept unknown."""
    required = (
        "active_exception",
        "action_present",
        "missing_reference",
        "observed_trading",
        "reported_change_matches_previous_bar",
        "reported_change_within_regime",
        "ipo_window",
    )
    if any(k not in row or (row[k] is not None and type(row[k]) is not bool) for k in required):
        raise ValueError("HELD_SOURCE_FACTS_INVALID")
    if row["active_exception"] is True:
        return "EXISTING_EXCEPTION_REMAINS_BLOCKED"
    if row["active_exception"] is None or row["action_present"] is None:
        return "REFERENCE_EVIDENCE_UNAVAILABLE"
    if row["action_present"] is True:
        return "NORMALIZED_ACTION_RESIDUAL_CONFLICT"
    if row["missing_reference"] is not False or row["observed_trading"] is None:
        return "REFERENCE_EVIDENCE_UNAVAILABLE"
    if row["observed_trading"] is False:
        return "NONTRADING_MARK_REFERENCE_CONFLICT"
    matches, within = (
        row["reported_change_matches_previous_bar"],
        row["reported_change_within_regime"],
    )
    if matches is None or within is None:
        return "REFERENCE_EVIDENCE_UNAVAILABLE"
    if matches is False:
        return (
            "SOURCE_REFERENCE_RESET_WITHOUT_NORMALIZED_ACTION"
            if within
            else "SOURCE_RETURN_REFERENCE_DISAGREEMENT"
        )
    if row["ipo_window"] is None:
        return "REFERENCE_EVIDENCE_UNAVAILABLE"
    return (
        "IPO_RULE_APPLICABILITY_UNQUALIFIED"
        if row["ipo_window"]
        else "OBSERVED_PRICE_OUTSIDE_DECLARED_REGIME"
    )


def guarded_source_census(
    connection: Any, candidates: Sequence[tuple[str, date]]
) -> list[dict[str, Any]]:
    """Use validated replay views; emit keys, booleans and hashes, never prices.

    Candidate-before-guard is only a possible exposure bound, not an actual held
    path or a newly accepted cohort. Read the entire retained price prefix once
    for lag references, then classify only the existing 2012--2021 guard keys.
    These temporary tables belong to this call and never become published data.
    """
    if len(candidates) > 200_000 or any(
        not isinstance(sid, str) or not sid or type(day) is not date or not START <= day <= END
        for sid, day in candidates
    ):
        raise ValueError("HELD_SOURCE_CANDIDATE_SCOPE")
    if len(set(candidates)) != len(candidates):
        raise ValueError("HELD_SOURCE_CANDIDATE_DUPLICATE")
    names = ("p10_held_candidates", "p10_held_bars")
    existing = connection.execute("SHOW TABLES").fetchall()
    if any(row[0] in names for row in existing):
        raise ValueError("HELD_SOURCE_TMP_EXISTS")
    created: list[str] = []
    try:
        connection.execute(
            "CREATE TEMP TABLE p10_held_candidates (security_id VARCHAR,signal_date DATE)"
        )
        created.append(names[0])
        if candidates:
            connection.executemany("INSERT INTO p10_held_candidates VALUES (?,?)", candidates)
        connection.execute(
            "CREATE TEMP TABLE p10_held_bars AS SELECT *,"
            "lag(close) OVER(PARTITION BY security_id ORDER BY date) AS previous_close,"
            "lag(date) OVER(PARTITION BY security_id ORDER BY date) AS previous_bar_date "
            "FROM replay_bars"
        )
        created.append(names[1])
        result = connection.execute(
            """
            SELECT t.security_id,t.market_date,b.previous_bar_date,
              CASE l.observed_tradestatus WHEN '1' THEN true WHEN '0' THEN false
                ELSE NULL END AS observed_trading,
              a.security_id IS NOT NULL AS action_present,
              EXISTS(SELECT 1 FROM replay_action_exceptions_mapped e
                WHERE e.security_id=t.security_id AND e.blocked_from<=t.market_date
                AND(e.blocked_through IS NULL OR e.blocked_through>=t.market_date))
                AS active_exception,
              EXISTS(SELECT 1 FROM p10_held_candidates p
                WHERE p.security_id=t.security_id AND p.signal_date<t.market_date)
                AS possible_post_candidate,
              abs(b.pct_change-(b.close/b.previous_close-1)*100)<=0.02
                AS reported_change_matches_previous_bar,
              b.pct_change BETWEEN -l.lower_limit_pct-0.1 AND l.upper_limit_pct+0.1
                AS reported_change_within_regime,
              b.close<l.lower_limit_price-0.011 AS below_declared_lower_limit,
              b.close>l.upper_limit_price+0.011 AS above_declared_upper_limit,
              sha256(to_json(b)) AS diagnostic_bar_sha256,
              sha256(to_json(l)) AS limit_row_sha256,
              l.reason_code AS limit_reason_code,l.price_reference_kind,
              b.close IS NULL OR NOT isfinite(b.close) OR b.close<=0
                OR b.pct_change IS NULL OR NOT isfinite(b.pct_change)
                OR b.previous_close IS NULL OR NOT isfinite(b.previous_close)
                OR b.previous_close<=0 AS missing_reference,
              l.in_ipo_window AS ipo_window,
              b.previous_close=l.price_reference AS limit_reference_equals_previous_bar
            FROM replay_unexplained_price_transitions t
            JOIN p10_held_bars b ON b.security_id=t.security_id AND b.date=t.market_date
            JOIN replay_limits l
              ON l.security_id=t.security_id AND l.execution_date=t.market_date
            LEFT JOIN (SELECT DISTINCT security_id,effective_date
              FROM replay_actions_mapped) a
              ON a.security_id=t.security_id AND a.effective_date=t.market_date
            WHERE t.market_date BETWEEN DATE '2012-01-04' AND DATE '2021-12-31'
            ORDER BY t.market_date,t.security_id
            """
        )
        columns = [column[0] for column in result.description]
        rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        expected = connection.execute(
            "SELECT count(*) FROM replay_unexplained_price_transitions "
            "WHERE market_date BETWEEN DATE '2012-01-04' AND DATE '2021-12-31'"
        ).fetchone()[0]
        keys = {(row["security_id"], row["market_date"]) for row in rows}
        if len(rows) != expected or len(keys) != len(rows):
            raise ValueError("HELD_SOURCE_CENSUS_KEY_CLOSURE")
        for row in rows:
            row["classification"] = classify_facts(row)
        return rows
    finally:
        for name in reversed(created):
            connection.execute(f"DROP TABLE {name}")
