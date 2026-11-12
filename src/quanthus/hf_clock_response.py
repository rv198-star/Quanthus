"""One fixed calendar-aligned raw-price diagnostic; no execution or return oracle."""

from __future__ import annotations

import math
from typing import Any

from quanthus.hf_dwd_research_sql import positive, summarize_markout


def clock_markout(connection: Any, analysis: dict[str, Any]) -> dict[str, Any]:
    """Build the common cohort on exact scheduled sessions, never on observed-bar shifts."""
    connection.execute(f"""
    CREATE TEMP TABLE r2_base AS
    SELECT f.*,second.op AS second_open,second.trade_date AS second_date,
      CASE
       WHEN f.trade_date=DATE '{analysis["audit_right_censored_decision"]}'
         THEN 'right_censored_second_session'
       WHEN f.next_open_reason<>'pass' THEN f.next_open_reason
       WHEN NOT {positive("f.next_close")} THEN 'missing_or_invalid_next_close'
       WHEN second.listing_id IS NULL OR second.security_ok IS NOT TRUE
         THEN 'missing_second_identity'
       WHEN NOT {positive("second.op")} THEN 'missing_or_invalid_second_open'
       WHEN NOT isfinite(second.op/f.next_open-1)
         OR NOT isfinite(f.next_close/f.next_open-1)
         OR NOT isfinite(second.op/f.next_close-1)
         OR NOT isfinite((f.next_close/f.next_open-1)*(second.op/f.next_close-1))
         THEN 'nonfinite_response'
       ELSE 'pass' END AS label_reason
    FROM labelled f
    LEFT JOIN calendar first_clock ON first_clock.calendar_session_id=f.next_session
      AND first_clock.venue_id=f.venue_id
    LEFT JOIN panel second ON second.listing_id=f.listing_id
      AND second.calendar_session_id=first_clock.next_session
      AND second.venue_id=f.venue_id
    WHERE f.universe AND f.base_selected;
    CREATE TEMP TABLE r2_valid AS
    SELECT *,second_open/next_open-1 AS response,
      next_close/next_open-1 AS intraday,
      second_open/next_close-1 AS overnight,
      (next_close/next_open-1)*(second_open/next_close-1) AS interaction
    FROM r2_base WHERE label_reason='pass';
    CREATE TEMP TABLE r2_cells AS
    SELECT trade_date,venue_id,
      count(*) FILTER(WHERE selected) AS candidate_count,
      count(*) FILTER(WHERE NOT selected) AS control_count,
      avg(response) FILTER(WHERE selected) AS candidate_mean,
      avg(response) FILTER(WHERE NOT selected) AS control_mean,
      avg(CAST(response>0 AS DOUBLE)) FILTER(WHERE selected) AS candidate_positive_rate,
      avg(CAST(response>0 AS DOUBLE)) FILTER(WHERE NOT selected) AS control_positive_rate,
      avg(CAST(response< -0.01 AS DOUBLE)) FILTER(WHERE selected) AS candidate_loss1pct_rate,
      avg(CAST(response< -0.01 AS DOUBLE)) FILTER(WHERE NOT selected) AS control_loss1pct_rate
    FROM r2_valid GROUP BY trade_date,venue_id;
    CREATE TEMP TABLE r2_paired_dates AS
    SELECT trade_date FROM r2_cells WHERE candidate_count>0 AND control_count>0
    GROUP BY trade_date HAVING count(DISTINCT venue_id)=2;
    """)
    result = summarize_markout(connection, analysis)
    result["schema"] = "quanthus.momentum-band-clock-markout.v1"
    components: dict[str, Any] = {}
    for name in ("intraday", "overnight", "interaction"):
        cells = [
            {
                "date": day.isoformat(),
                "venue_id": venue,
                "candidate_mean": candidate,
                "control_mean": control,
                "increment": candidate - control,
            }
            for day, venue, candidate, control in connection.execute(
                f"SELECT trade_date,venue_id,avg({name}) FILTER(WHERE selected),"
                f"avg({name}) FILTER(WHERE NOT selected) FROM r2_valid "
                "WHERE trade_date IN(SELECT trade_date FROM r2_paired_dates) "
                "GROUP BY trade_date,venue_id ORDER BY trade_date,venue_id"
            ).fetchall()
        ]
        dates = sorted({x["date"] for x in cells})
        daily = [
            {
                "date": day,
                "increment": math.fsum(x["increment"] for x in cells if x["date"] == day) / 2,
            }
            for day in dates
        ]
        values = [x["increment"] for x in daily]
        components[name] = {
            "paired_cells": cells,
            "paired_daily": daily,
            "mean_increment": math.fsum(values) / len(values) if values else None,
            "mean_increment_by_year": {
                str(year): (math.fsum(v) / len(v) if v else None)
                for year in analysis["required_years"]
                for v in [[x["increment"] for x in daily if x["date"].startswith(str(year))]]
            },
        }
    result["components"] = components
    result["label_ledger_by_year"] = [
        {
            "group": "candidate" if selected else "control",
            "year": year,
            "reason": reason,
            "count": count,
        }
        for selected, year, reason, count in connection.execute(
            "SELECT selected,year(trade_date),label_reason,count(*) FROM r2_base "
            "GROUP BY 1,2,3 ORDER BY 1,2,3"
        ).fetchall()
    ]
    residual = connection.execute(
        "SELECT max(abs(response-intraday-overnight-interaction)) FROM r2_valid"
    ).fetchone()[0]
    result["decomposition"] = {
        "identity": "primary = intraday + overnight + interaction",
        "max_opportunity_residual": residual,
        "mean_residual": (
            result["mean_increment"] - math.fsum(c["mean_increment"] for c in components.values())
            if result["mean_increment"] is not None
            else None
        ),
    }
    if residual is not None and residual > 1e-10:
        raise ValueError("clock decomposition mismatch")
    result["scope_disposition"] = {
        "primary": "calendar-aligned unadjusted price response",
        "decision_end": "2021-12-29",
        "last_market_read": "2021-12-31",
        "right_censored_original_decision": "2021-12-30",
        "r4_capital_use": "park",
        "second_open_sell_execution": "not_evaluated",
    }
    return result
