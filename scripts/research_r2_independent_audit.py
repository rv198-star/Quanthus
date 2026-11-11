#!/usr/bin/env python3
"""Independent R2 reconstruction; pandas cohorts, numpy aggregation; no producer imports."""

import argparse
import math
import random
import resource
import time
from pathlib import Path

from research_r1_v2_independent_audit import canonical, read, reproduce, save


def evaluate(q, labelled, author, out, began):
    import numpy as np

    a = q["analysis"]
    base = labelled.loc[labelled.universe & labelled.base_selected].copy()
    assert (
        base.selected == base.momentum20.between(*q["screen"]["unadjusted_momentum20_band"])
    ).all()
    base["label_reason"] = base.reason
    passed = base.reason.eq("pass")
    close_ok = np.isfinite(base.next_px) & (base.next_px > 0)
    base.loc[passed & ~close_ok, "label_reason"] = "missing_or_invalid_next_close"
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        base["response"] = base.next_px / base.next_op - 1
    base.loc[passed & close_ok & ~np.isfinite(base.response), "label_reason"] = "nonfinite_response"
    valid = base.loc[base.label_reason.eq("pass")]
    cells = []
    for (day, venue), rows in valid.groupby(["day", "venue_id"], sort=True):
        item = {"trade_date": day.date().isoformat(), "venue_id": venue}
        for name, selected in (("candidate", True), ("control", False)):
            values = rows.loc[rows.selected.eq(selected), "response"].to_numpy()
            item[name + "_count"] = len(values)
            item[name + "_mean"] = float(np.mean(values)) if len(values) else None
            item[name + "_positive_rate"] = float(np.mean(values > 0)) if len(values) else None
            item[name + "_loss1pct_rate"] = float(np.mean(values < -0.01)) if len(values) else None
        cells.append(item)
    eligible = {}
    for cell in cells:
        if cell["candidate_count"] and cell["control_count"]:
            eligible.setdefault(cell["trade_date"], []).append(cell)
    paired_dates = {
        day
        for day, rows in eligible.items()
        if {x["venue_id"] for x in rows} == set(a["required_venues"])
    }
    paired = [cell for cell in cells if cell["trade_date"] in paired_dates]
    for cell in paired:
        cell["increment"] = cell["candidate_mean"] - cell["control_mean"]
    daily = [
        {"date": day, "increment": float(np.mean([x["increment"] for x in eligible[day]]))}
        for day in sorted(paired_dates)
    ]
    matched = valid.loc[valid.day.dt.strftime("%Y-%m-%d").isin(paired_dates)]
    groups = {}
    for name, selected in (("candidate", True), ("control", False)):
        rows = matched.loc[matched.selected.eq(selected)]
        counts = rows.listing_id.value_counts().to_numpy()
        n = len(rows)
        groups[name] = {
            "opportunities": n,
            "listings": len(counts),
            "by_year": {str(y): int((rows.day.dt.year == y).sum()) for y in a["required_years"]},
            "top_listing_share": int(counts[0]) / n if n else None,
            "top10_listing_share": int(counts[:10].sum()) / n if n else None,
        }
    ledger = [
        {"group": "candidate" if selected else "control", "reason": reason, "count": len(rows)}
        for (selected, reason), rows in base.groupby(["selected", "label_reason"], sort=True)
    ]
    assert sum(x["count"] for x in ledger) == len(base)
    year_values = {
        str(y): np.array([x["increment"] for x in daily if x["date"].startswith(str(y))])
        for y in a["required_years"]
    }
    gaps = [
        f"paired_days.{y}"
        for y, values in year_values.items()
        if len(values) < a["min_paired_days_per_year"]
    ]
    for name, group in groups.items():
        for key, minimum in (
            ("opportunities", a["min_group_opportunities"]),
            ("listings", a["min_group_listings"]),
        ):
            if group[key] < minimum:
                gaps.append(name + "." + key)
        for year in a["required_years"]:
            if group["by_year"][str(year)] < a["min_group_opportunities_per_year"]:
                gaps.append(name + ".opportunities." + str(year))
        for key, maximum in (
            ("top_listing_share", a["max_top_listing_share"]),
            ("top10_listing_share", a["max_top10_listing_share"]),
        ):
            if group[key] is None or group[key] > maximum:
                gaps.append(name + "." + key)
    mean = float(np.mean([x["increment"] for x in daily])) if daily else None
    year_means = {
        year: float(np.mean(values)) if len(values) else None
        for year, values in year_values.items()
    }
    lower = upper = None
    branch, verdict = "insufficient", "inconclusive"
    if not gaps:
        rng = random.Random(a["bootstrap"]["seed"])
        draws = []
        block = a["bootstrap"]["block_sessions"]
        for _ in range(a["bootstrap"]["replicates"]):
            sampled = []
            for values in year_values.values():
                starts = [rng.randrange(len(values)) for _ in range(math.ceil(len(values) / block))]
                indices = (np.array(starts)[:, None] + np.arange(block)) % len(values)
                sampled.extend(values[indices.ravel()[: len(values)]])
            draws.append(float(np.mean(sampled)))
        draws.sort()
        lower, upper = [draws[math.ceil(p * len(draws)) - 1] for p in (0.025, 0.975)]
        if (
            mean >= a["minimum_mean_increment"]
            and lower > 0
            and all(x > 0 for x in year_means.values())
        ):
            branch, verdict = "advance", "supported"
        elif upper < a["minimum_mean_increment"]:
            branch, verdict = "below_minimum", "not_met"
        else:
            branch = "uncertain"
    metrics = {
        "groups_on_paired_dates": groups,
        "paired_cells": paired,
        "paired_daily": daily,
        "label_ledger": ledger,
        "valid_unpaired_opportunities": len(valid) - len(matched),
        "unpaired_cells": [
            {
                "date": x["trade_date"],
                "venue_id": x["venue_id"],
                "candidate_count": x["candidate_count"],
                "control_count": x["control_count"],
            }
            for x in cells
            if x["trade_date"] not in paired_dates
        ],
        "sample_sufficient": not gaps,
        "sample_gaps": gaps,
        "paired_days": len(daily),
        "paired_days_by_year": {year: len(values) for year, values in year_values.items()},
        "mean_increment": mean,
        "mean_increment_by_year": year_means,
        "interval95": [lower, upper],
        "decision_branch": branch,
        "verdict": verdict,
        "decision": a["decision_branches"][branch],
    }
    mismatches = []
    max_error = 0.0

    def compare(want, got, path="metrics"):
        nonlocal max_error
        if isinstance(want, dict) and isinstance(got, dict):
            for key, value in want.items():
                compare(value, got.get(key), path + "." + key)
        elif isinstance(want, list) and isinstance(got, list):
            if len(want) != len(got):
                mismatches.append(
                    {"path": path, "independent_length": len(want), "producer_length": len(got)}
                )
            for i, (left, right) in enumerate(zip(want, got, strict=False)):
                compare(left, right, path + f"[{i}]")
        elif isinstance(want, float) and isinstance(got, (int, float)):
            error = abs(want - got)
            max_error = max(error, max_error)
            if not math.isfinite(error) or error > 1e-10:
                mismatches.append({"path": path, "independent": want, "producer": got})
        elif want != got:
            mismatches.append({"path": path, "independent": want, "producer": got})

    compare(metrics, author["metrics"])
    receipt = {
        "schema": "quanthus.r2-independent.v1",
        "metrics": metrics,
        "mismatches": mismatches,
        "max_absolute_error": max_error,
        "wall_seconds": time.monotonic() - began,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "implementation": (
            "Independent pandas calendar/interval/rolling construction; "
            "numpy cohort aggregation; no producer imports"
        ),
    }
    save(out / "independent-reproduction.json", receipt)
    print(
        {
            "mismatches": len(mismatches),
            "max_absolute_error": max_error,
            "decision_branch": branch,
            "wall_seconds": receipt["wall_seconds"],
            "peak_rss_kib": receipt["peak_rss_kib"],
        }
    )
    return 2 if mismatches else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--original", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--execution-git", required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    request = "config/research/research-r2/request.v1.json"
    spec = {
        "request": request,
        "request_digest": canonical(read(root / request)),
        "execution_git": args.execution_git,
        "result_digest": read(args.original / "result.json")["digest"],
        "allow_unpublished": True,
    }
    return reproduce(args.original, args.out, spec, evaluator=evaluate)


if __name__ == "__main__":
    raise SystemExit(main())
