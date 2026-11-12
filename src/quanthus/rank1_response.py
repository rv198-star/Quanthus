"""Frozen original rank1 price diagnostic; a research allocator, without capital replay."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from quanthus.hf_dwd_research import digest, require
from quanthus.hf_dwd_research_sql import circular_block_interval

ARMS = ("candidate", "control")


def compare(want: Any, got: Any, tolerance: float = 1e-10, path: str = "root") -> int:
    """Compare identities exactly and finite numeric construction within the frozen tolerance."""
    if isinstance(want, dict):
        require(isinstance(got, dict) and set(want) == set(got), "keys: " + path)
        return sum(compare(v, got[k], tolerance, path + "." + k) for k, v in want.items())
    if isinstance(want, list):
        require(isinstance(got, list) and len(want) == len(got), "length: " + path)
        return sum(
            compare(v, w, tolerance, path + f"[{i}]")
            for i, (v, w) in enumerate(zip(want, got, strict=False))
        )
    if type(want) is float:
        require(type(got) in (int, float), "numeric type: " + path)
        require(math.isfinite(got) and abs(want - got) <= tolerance, "numeric value: " + path)
    else:
        require(type(want) is type(got) and want == got, "identity: " + path)
    return 1


def validate_nominals(old: dict[str, Any], actual: list[dict[str, Any]]) -> None:
    expected = old["nominal_choices"]

    def project(rows: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
        return [(x["date"], x["arm"], x["listing_id"], x["ranking_vol12"]) for x in rows]

    require(project(expected) == project(actual), "original T-close nominal selection differs")
    require(
        len({(x["date"], x["arm"]) for x in expected}) == len(expected), "duplicate nominal key"
    )


def summarize(
    ledger: list[dict[str, Any]], population: dict[str, Any], analysis: dict[str, Any]
) -> dict[str, Any]:
    """Only the predeclared paired rank1 response drives the resource branch."""
    all_dates = sorted({x["date"] for x in ledger})
    by_date = {day: {x["arm"]: x for x in ledger if x["date"] == day} for day in all_dates}
    require(all(set(rows) == set(ARMS) for rows in by_date.values()), "incomplete nominal arms")
    require(len(ledger) == 2 * len(all_dates), "duplicate nominal observation")
    paired = []
    for day, choices in by_date.items():
        if all(choices[a]["label_reason"] == "pass" for a in ARMS):
            c, k = (choices[a]["response"] for a in ARMS)
            require(all(v is not None and math.isfinite(v) for v in (c, k)), "invalid response")
            paired.append({"date": day, "candidate_mean": c, "control_mean": k, "increment": c - k})
    paired_dates = {x["date"] for x in paired}
    years = analysis["required_years"]
    year_days = {str(y): [x for x in paired if x["date"].startswith(str(y))] for y in years}

    def average(values: list[float]) -> float | None:
        return math.fsum(values) / len(values) if values else None

    mean = average([x["increment"] for x in paired])
    by_year = {y: average([x["increment"] for x in rows]) for y, rows in year_days.items()}
    gaps = [
        "paired_days." + y
        for y, rows in year_days.items()
        if len(rows) < analysis["min_paired_days_per_year"]
    ]
    interval: list[float | None] = [None, None]
    branch, verdict = "insufficient", "inconclusive"
    if not gaps:
        lower, upper = circular_block_interval(paired, analysis)
        interval = [lower, upper]
        assert mean is not None
        if (
            mean >= analysis["minimum_mean_increment"]
            and lower > 0
            and all(v is not None and v > 0 for v in by_year.values())
        ):
            branch, verdict = "advance", "supported"
        elif upper < analysis["minimum_mean_increment"]:
            branch, verdict = "below_minimum", "not_met"
        else:
            branch = "uncertain"
    counts = Counter((x["arm"], int(x["date"][:4]), x["label_reason"]) for x in ledger)
    label_ledger = [
        {"arm": arm, "year": year, "reason": reason, "count": n}
        for (arm, year, reason), n in sorted(counts.items())
    ]
    groups = {}
    for arm in ARMS:
        rows = [x for x in ledger if x["arm"] == arm]
        matched = [x for x in rows if x["date"] in paired_dates]
        concentration = Counter(x["listing_id"] for x in matched).most_common()
        longest = run = 0
        previous = None
        for x in rows:
            run = run + 1 if x["listing_id"] == previous else 1
            previous = x["listing_id"]
            longest = max(longest, run)
        groups[arm] = {
            "nominal": len(rows),
            "label_pass": sum(x["label_reason"] == "pass" for x in rows),
            "paired": len(matched),
            "unique_listings": len(concentration),
            "top1_share": concentration[0][1] / len(matched) if matched else None,
            "top10_share": sum(n for _, n in concentration[:10]) / len(matched)
            if matched
            else None,
            "longest_same_listing_nominal_run": longest,
            "decision_hold": sum(x["decision_hold"] for x in rows),
            "r4_closed_entries": sum(x["actual_r4_closed_entry"] for x in rows),
        }
    cells = {(x["trade_date"], x["venue_id"]): x for x in population["paired_cells"]}
    bridge_daily, missing_bridge_dates = [], []
    required_venues = population["analysis"]["required_venues"]
    for row in paired:
        day, increment = row["date"], row["increment"]
        choices = by_date[day]
        if not all((day, v) in cells for v in required_venues):
            missing_bridge_dates.append(day)
            continue
        p = math.fsum(cells[(day, v)]["increment"] for v in required_venues) / 2
        v = (
            cells[(day, choices["candidate"]["venue_id"])]["candidate_mean"]
            - cells[(day, choices["control"]["venue_id"])]["control_mean"]
        )
        bridge_daily.append(
            {
                "date": day,
                "population": p,
                "chosen_venue_population": v,
                "rank1_bridge": increment,
                "venue_component": v - p,
                "within_venue_selection_residual": increment - v,
            }
        )
    bridge_means = {
        key: average([x[key] for x in bridge_daily])
        for key in (
            "population",
            "chosen_venue_population",
            "rank1_bridge",
            "venue_component",
            "within_venue_selection_residual",
        )
    }
    p0 = population["mean_increment"]
    date_component = bridge_means["population"] - p0 if bridge_daily else None
    residual = (
        (
            bridge_means["rank1_bridge"]
            - p0
            - date_component
            - bridge_means["venue_component"]
            - bridge_means["within_venue_selection_residual"]
        )
        if bridge_daily
        else None
    )
    indices = {day: i for i, day in enumerate(all_dates)}
    paired_gaps = [
        {
            "previous": x["date"],
            "next": y["date"],
            "excluded_nominal_dates": indices[y["date"]] - indices[x["date"]] - 1,
        }
        for x, y in zip(paired, paired[1:], strict=False)
        if indices[y["date"]] - indices[x["date"]] > 1
    ]
    return {
        "schema": "quanthus.rank1-price-response.v1",
        "nominal_rows": len(ledger),
        "nominal_ledger": ledger,
        "label_ledger": label_ledger,
        "paired_days": len(paired),
        "paired_daily": paired,
        "paired_days_by_year": {y: len(v) for y, v in year_days.items()},
        "valid_unpaired_rows": sum(
            x["label_reason"] == "pass" and x["date"] not in paired_dates for x in ledger
        ),
        "paired_date_gaps": paired_gaps,
        "groups": groups,
        "mean_increment": mean,
        "mean_increment_by_year": by_year,
        "confidence_interval": interval,
        "sample_gaps": gaps,
        "branch": branch,
        "verdict": verdict,
        "decision": analysis["decision_branches"][branch],
        "r4_capital_use": "park",
        "bridge": {
            "daily": bridge_daily,
            "missing_dates": missing_bridge_dates,
            "population_original_mean": p0,
            "means": bridge_means,
            "date_component": date_component,
            "additive_residual": residual,
        },
    }


def formal(
    connection: Any, old: dict[str, Any], population: dict[str, Any], analysis: dict[str, Any]
) -> dict[str, Any]:
    actual = [
        dict(zip(("date", "arm", "listing_id", "ranking_vol12"), row, strict=False))
        for row in connection.execute("""
      SELECT CAST(trade_date AS VARCHAR),CASE WHEN selected THEN 'candidate' ELSE 'control' END,
        listing_id,CAST(CAST(round(return_vol20,12) AS DECIMAL(20,12)) AS VARCHAR)
      FROM (SELECT *,row_number() OVER(PARTITION BY trade_date,selected
        ORDER BY round(return_vol20,12),listing_id) AS choice FROM r2_base)
      WHERE choice=1 ORDER BY trade_date,selected DESC
    """).fetchall()
    ]
    validate_nominals(old, actual)
    columns = (
        "date",
        "listing_id",
        "venue_id",
        "label_reason",
        "first_date",
        "second_date",
        "response",
    )
    all_rows = {
        (str(row[0]), row[1]): dict(zip(columns, row, strict=False))
        for row in connection.execute("""
          SELECT CAST(b.trade_date AS VARCHAR),b.listing_id,b.venue_id,b.label_reason,
            CAST(c1.trade_date AS VARCHAR),CAST(c2.trade_date AS VARCHAR),
            CASE WHEN b.label_reason='pass' THEN b.second_open/b.next_open-1 ELSE NULL END
          FROM r2_base b LEFT JOIN calendar c1 ON c1.calendar_session_id=b.next_session
            AND c1.venue_id=b.venue_id
          LEFT JOIN calendar c2 ON c2.calendar_session_id=c1.next_session
            AND c2.venue_id=b.venue_id
        """).fetchall()
    }
    ledger = []
    for item in old["nominal_choices"]:
        row = all_rows.get((item["date"], item["listing_id"]))
        require(row is not None, "original choice missing from fixed baseline")
        assert row is not None
        trades = old["arms"][item["arm"]]["result"]["trades"]
        ledger.append(
            {
                **row,
                "arm": item["arm"],
                "ranking_vol12": item["ranking_vol12"],
                "decision_hold": item["decision_hold"],
                "actual_r4_closed_entry": any(
                    t["security_code"] == item["listing_id"]
                    and t["entry_date"] == row["first_date"]
                    for t in trades
                ),
            }
        )
    result = summarize(ledger, population, analysis)
    result["original_nominal_sha256"] = digest(old["nominal_choices"])
    return result
