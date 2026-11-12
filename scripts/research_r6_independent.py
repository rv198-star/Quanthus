"""Independent pandas/numpy rank1 labels, aggregation, bootstrap and bridge."""

import hashlib
import json
import math
import random
import shutil
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from research_r1_v2_independent_audit import canonical, reproduce, save
from research_r5_independent_audit import evaluate as evaluate_population


def construct(prepared, old, population, analysis):
    import numpy as np
    import pandas as pd

    base = prepared.copy()
    base["date"] = base.day.dt.strftime("%Y-%m-%d")
    base["arm"] = np.where(base.selected, "candidate", "control")
    base["ranking_value"] = base.return_vol20.map(
        lambda v: Decimal(str(v)).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)
    )
    base["ranking_vol12"] = base.ranking_value.map(lambda v: format(v, ".12f"))
    ranked = (
        base.sort_values(["date", "arm", "ranking_value", "listing_id"])
        .groupby(["date", "arm"], sort=True)
        .head(1)
        .sort_values(["date", "arm"])
    )
    keys = ["date", "arm", "listing_id", "ranking_vol12"]
    expected = pd.DataFrame(old["nominal_choices"])
    assert ranked[keys].to_dict("records") == expected[keys].to_dict("records")
    assert not expected.duplicated(["date", "arm"]).any()
    joined = expected.merge(base, on=keys, how="left", validate="one_to_one", indicator=True)
    assert joined._merge.eq("both").all()
    ledger = []
    for row in joined.itertuples():
        first = analysis["calendar_dates"].get(row.next_session)
        second = analysis["calendar_dates"].get(analysis["calendar_next"].get(row.next_session))
        trades = old["arms"][row.arm]["result"]["trades"]
        ledger.append(
            {
                "date": row.date,
                "arm": row.arm,
                "listing_id": row.listing_id,
                "venue_id": row.venue_id,
                "ranking_vol12": row.ranking_vol12,
                "decision_hold": row.decision_hold,
                "label_reason": row.label_reason,
                "first_date": first,
                "second_date": second,
                "response": float(row.response) if row.label_reason == "pass" else None,
                "actual_r4_closed_entry": any(
                    t["security_code"] == row.listing_id and t["entry_date"] == first
                    for t in trades
                ),
            }
        )
    frame = pd.DataFrame(ledger)
    valid = frame.loc[frame.label_reason.eq("pass")]
    pivot = (
        valid.pivot(index="date", columns="arm", values="response")
        .reindex(columns=["candidate", "control"])
        .dropna()
    )
    paired = [
        {
            "date": day,
            "candidate_mean": float(row.candidate),
            "control_mean": float(row.control),
            "increment": float(row.candidate - row.control),
        }
        for day, row in pivot.iterrows()
    ]
    dates = set(pivot.index)
    years = analysis["required_years"]
    ys = {str(y): [r["increment"] for r in paired if r["date"].startswith(str(y))] for y in years}

    def mean(values):
        return float(np.mean(values)) if len(values) else None

    means = {y: mean(v) for y, v in ys.items()}
    average = mean([x["increment"] for x in paired])
    gaps = [
        "paired_days." + y for y, v in ys.items() if len(v) < analysis["min_paired_days_per_year"]
    ]
    interval = [None, None]
    branch, verdict = "insufficient", "inconclusive"
    if not gaps:
        rng = random.Random(analysis["bootstrap"]["seed"])
        draws, block = [], analysis["bootstrap"]["block_sessions"]
        for _ in range(analysis["bootstrap"]["replicates"]):
            samples = []
            for values in ys.values():
                a = np.asarray(values)
                starts = np.array([rng.randrange(len(a)) for _ in range(math.ceil(len(a) / block))])
                idx = (starts[:, None] + np.arange(block)) % len(a)
                samples.extend(a[idx.ravel()[: len(a)]])
            draws.append(float(np.mean(samples)))
        draws.sort()
        interval = [draws[math.ceil(p * len(draws)) - 1] for p in (0.025, 0.975)]
        if (
            average >= analysis["minimum_mean_increment"]
            and interval[0] > 0
            and all(v > 0 for v in means.values())
        ):
            branch, verdict = "advance", "supported"
        elif interval[1] < analysis["minimum_mean_increment"]:
            branch, verdict = "below_minimum", "not_met"
        else:
            branch = "uncertain"
    label_ledger = [
        {"arm": arm, "year": int(year), "reason": reason, "count": len(rows)}
        for (arm, year, reason), rows in frame.groupby(
            ["arm", frame.date.str[:4], "label_reason"], sort=True
        )
    ]
    groups = {}
    for arm, rows in frame.groupby("arm", sort=True):
        matched = rows.loc[rows.date.isin(dates)]
        counts = matched.listing_id.value_counts().to_numpy()
        longest = run = 0
        prev = None
        for listing in rows.listing_id:
            run = run + 1 if listing == prev else 1
            prev = listing
            longest = max(longest, run)
        groups[arm] = {
            "nominal": len(rows),
            "label_pass": int(rows.label_reason.eq("pass").sum()),
            "paired": len(matched),
            "unique_listings": len(counts),
            "top1_share": int(counts[0]) / len(matched) if len(matched) else None,
            "top10_share": int(counts[:10].sum()) / len(matched) if len(matched) else None,
            "longest_same_listing_nominal_run": longest,
            "decision_hold": int(rows.decision_hold.sum()),
            "r4_closed_entries": int(rows.actual_r4_closed_entry.sum()),
        }
    cell = {(r["trade_date"], r["venue_id"]): r for r in population["paired_cells"]}
    lookup = {(r["date"], r["arm"]): r for r in ledger}
    venue_ids = analysis["required_venues"]
    bridge, missing = [], []
    for row in paired:
        d = row["date"]
        if not all((d, venue) in cell for venue in venue_ids):
            missing.append(d)
            continue
        p = float(np.mean([cell[(d, venue)]["increment"] for venue in venue_ids]))
        v = (
            cell[(d, lookup[(d, "candidate")]["venue_id"])]["candidate_mean"]
            - cell[(d, lookup[(d, "control")]["venue_id"])]["control_mean"]
        )
        bridge.append(
            {
                "date": d,
                "population": p,
                "chosen_venue_population": v,
                "rank1_bridge": row["increment"],
                "venue_component": v - p,
                "within_venue_selection_residual": row["increment"] - v,
            }
        )
    bm = {
        k: mean([r[k] for r in bridge])
        for k in (
            "population",
            "chosen_venue_population",
            "rank1_bridge",
            "venue_component",
            "within_venue_selection_residual",
        )
    }
    p0 = population["mean_increment"]
    dc = bm["population"] - p0 if bridge else None
    residual = (
        bm["rank1_bridge"] - p0 - dc - bm["venue_component"] - bm["within_venue_selection_residual"]
        if bridge
        else None
    )
    all_dates = sorted(frame.date.unique())
    gap_rows = [
        {
            "previous": x["date"],
            "next": y["date"],
            "excluded_nominal_dates": all_dates.index(y["date"]) - all_dates.index(x["date"]) - 1,
        }
        for x, y in zip(paired, paired[1:], strict=False)
        if all_dates.index(y["date"]) - all_dates.index(x["date"]) > 1
    ]
    return {
        "schema": "quanthus.rank1-price-response.v1",
        "nominal_rows": len(frame),
        "nominal_ledger": ledger,
        "label_ledger": label_ledger,
        "paired_days": len(paired),
        "paired_daily": paired,
        "paired_days_by_year": {y: len(v) for y, v in ys.items()},
        "valid_unpaired_rows": int((frame.label_reason.eq("pass") & ~frame.date.isin(dates)).sum()),
        "paired_date_gaps": gap_rows,
        "groups": groups,
        "mean_increment": average,
        "mean_increment_by_year": means,
        "confidence_interval": interval,
        "sample_gaps": gaps,
        "branch": branch,
        "verdict": verdict,
        "decision": analysis["decision_branches"][branch],
        "r4_capital_use": "park",
        "original_nominal_sha256": canonical(old["nominal_choices"]),
        "bridge": {
            "daily": bridge,
            "missing_dates": missing,
            "population_original_mean": p0,
            "means": bm,
            "date_component": dc,
            "additive_residual": residual,
        },
    }


def run(job, request, input_request, members, old_r4, old_r5):
    q = input_request
    output = job / "independent"
    calendar_maps = {}

    def observe_calendar(cal):
        ordered = cal.sort_values(["venue_id", "day"])
        calendar_maps["calendar_dates"] = dict(
            zip(ordered.calendar_session_id, ordered.day.dt.strftime("%Y-%m-%d"), strict=True)
        )
        calendar_maps["calendar_next"] = dict(
            zip(
                ordered.calendar_session_id,
                ordered.groupby("venue_id").calendar_session_id.shift(-1),
                strict=True,
            )
        )

    def source_resolver(original, out, spec):
        source = original / "source/DWD"
        isolated = out / "source/DWD"
        isolated.mkdir(parents=True)

        def sha(p):
            return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()

        assert sha(source / "manifest.json") == q["authority"]["manifest_sha256"]
        manifest = json.loads((source / "manifest.json").read_text())
        assert manifest["content_digest"] == q["authority"]["content_digest"]
        assert (source / "READY").read_text().strip() == sha(source / "manifest.json")
        declared = {x["path"]: x for x in manifest["content"]["files"]}
        for x in members:
            assert declared[x["path"]] == x
            src, dst = source / x["path"], isolated / x["path"]
            assert not Path(x["path"]).is_absolute() and ".." not in Path(x["path"]).parts
            assert src.stat().st_size == x["size_bytes"] and sha(src) == x["sha256"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            assert src.stat().st_ino != dst.stat().st_ino and sha(dst) == x["sha256"]
        save(
            out / "input-verification.json",
            {
                "members": len(members),
                "physical_copies": True,
                "bytes": sum(x["size_bytes"] for x in members),
            },
        )
        return q, members, isolated, old_r5

    def evaluator(q, labelled, panel, author, out, began):
        def finish(prepared, population):
            a = {
                **request["analysis"],
                "required_venues": q["analysis"]["required_venues"],
                **calendar_maps,
            }
            save(out / "metrics.json", construct(prepared, old_r4, population, a))
            return 0

        return evaluate_population(
            q, labelled, panel, author, out / "control", began, prepared_evaluator=finish
        )

    return reproduce(
        job,
        output,
        {},
        source_resolver=source_resolver,
        panel_evaluator=evaluator,
        audit_decision_end=q["analysis"]["audit_right_censored_decision"],
        calendar_observer=observe_calendar,
    )
