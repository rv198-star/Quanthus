#!/usr/bin/env python3
"""Independent R1 v2 audit: pandas/numpy semantics, immutable inputs, no publication."""

import argparse
import hashlib
import importlib.metadata
import json
import math
import resource
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENUES = {
    "SSE": "venue_bec59e31eb6bb28111a10d7e6d2c689f",
    "SZSE": "venue_8c0bd9088a0dc88f28e5e7fa8efdce87",
}
SPEC = ROOT / "config/research/research-r1-audit/request.v2.json"


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def canonical(value):
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
    )


def checked_source(original, out, spec):
    request = read(ROOT / spec["request"])
    assert canonical(request) == spec["request_digest"]
    closure_path = ROOT / request["closure"]["path"]
    assert sha(closure_path) == request["closure"]["sha256"]
    members = read(closure_path)["members"]
    assert len(members) == 56
    assert sum(x["size_bytes"] for x in members) == 187921175
    source = original / "source/DWD"
    assert sha(source / "manifest.json") == request["authority"]["manifest_sha256"]
    assert (source / "READY").read_text().strip() == sha(source / "manifest.json")
    manifest = read(source / "manifest.json")
    assert manifest["content_digest"] == request["authority"]["content_digest"]
    declared = {x["path"]: x for x in manifest["content"]["files"]}
    isolated = out / "source/DWD"
    isolated.mkdir(parents=True)
    for item in members:
        name = item["path"]
        assert declared[name] == item
        assert not Path(name).is_absolute() and ".." not in Path(name).parts
        src, dst = source / name, isolated / name
        assert sha(src) == item["sha256"] and src.stat().st_size == item["size_bytes"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        assert sha(dst) == item["sha256"] and dst.stat().st_ino != src.stat().st_ino
    for name in ("manifest.json", "READY"):
        shutil.copyfile(source / name, isolated / name)
    for name in (
        "result.json",
        "run.json",
        "fetch.json",
        "resources.json",
        "attempts.json",
        "job.json",
        "publish.json",
    ):
        if (
            name == "publish.json"
            and spec.get("allow_unpublished") is True
            and not (original / name).is_file()
        ):
            continue
        shutil.copyfile(original / name, out / name)
    result = read(out / "result.json")
    assert result["identity"]["git"] == spec["execution_git"]
    assert result["identity"]["request_digest"] == spec["request_digest"]
    assert result["digest"] == spec["result_digest"]
    assert canonical({k: v for k, v in result.items() if k != "digest"}) == result["digest"]
    save(
        out / "input-verification.json",
        {
            "members": len(members),
            "member_bytes": 187921175,
            "source_manifest": sha(source / "manifest.json"),
            "physical_isolated_copies": True,
            "row_scope": request["scope"],
            "request_digest": canonical(request),
            "audit_git": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
        },
    )
    return request, members, isolated, result


def reproduce(
    original,
    out,
    spec,
    evaluator=None,
    source_resolver=None,
    panel_evaluator=None,
    audit_decision_end=None,
    calendar_observer=None,
):
    import duckdb
    import numpy as np
    import pandas as pd

    began = time.monotonic()
    q, members, source, author = (source_resolver or checked_source)(original, out, spec)
    scope, filt = q["scope"], q["screen"]
    db = duckdb.connect()
    db.execute("SET threads=2")
    db.execute("SET memory_limit='2GB'")

    def table(name, columns):
        paths = [
            str(source / x["path"])
            for x in members
            if x["path"].startswith("payload/" + name + "/")
        ]
        frame = (
            db.read_parquet(paths, hive_partitioning=True)
            .project(",".join([*columns, "knowledge_to"]))
            .df()
        )
        return frame.loc[frame.knowledge_to.isna(), columns].copy()

    def local_days(series):
        converted = pd.to_datetime(series)
        if converted.dt.tz is not None:
            converted = converted.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
        return converted.dt.normalize()

    cal = table(
        "dwd_trading_calendar", ["calendar_session_id", "venue_id", "trade_date", "is_open"]
    )
    cal["day"] = local_days(cal.trade_date)
    cal = cal.loc[
        cal.is_open
        & cal.venue_id.isin(VENUES.values())
        & cal.day.between(scope["calendar_start"], scope["execution_end"])
    ].copy()
    assert not cal.calendar_session_id.duplicated().any()
    assert not cal.duplicated(["venue_id", "day"]).any()
    if calendar_observer is not None:
        calendar_observer(cal.copy())
    calendars = {v: g.sort_values("day").reset_index(drop=True) for v, g in cal.groupby("venue_id")}
    for calendar in calendars.values():
        assert int((calendar.day < scope["decision_start"]).sum()) >= filt["min_listing_sessions"]

    listings = table(
        "dwd_listing", ["listing_id", "security_id", "venue_id", "effective_from", "effective_to"]
    )
    securities = table(
        "dwd_security", ["security_id", "instrument_type", "effective_from", "effective_to"]
    )
    for frame in (listings, securities):
        frame["start"] = local_days(frame.effective_from)
        frame["end"] = local_days(frame.effective_to).fillna(pd.Timestamp("2100-01-01"))
    security_groups = {key: group for key, group in securities.groupby("security_id")}
    pieces = []
    # Build valid calendar slots by interval masks, independently of the SQL joins/window ages.
    for listing, versions in listings.groupby("listing_id", sort=True):
        if not versions.venue_id.isin(VENUES.values()).any():
            continue
        assert versions.venue_id.nunique() == 1 and versions.security_id.nunique() == 1
        venue, security = versions.iloc[0][["venue_id", "security_id"]]
        calendar = calendars[venue]
        occupied = np.zeros(len(calendar), dtype=np.int16)
        for version in versions.itertuples():
            occupied += ((calendar.day >= version.start) & (calendar.day < version.end)).to_numpy()
        assert occupied.max() <= 1
        ages = occupied.cumsum()
        active = (occupied == 1) & (calendar.day >= scope["read_start"]).to_numpy()
        if not active.any():
            continue
        part = calendar.loc[active, ["calendar_session_id", "venue_id", "day"]].copy()
        part["next_session"] = calendar.calendar_session_id.shift(-1).loc[active]
        part["listing_id"] = listing
        part["age"] = ages[active]
        valid = np.zeros(len(part), dtype=np.int16)
        qualified = np.zeros(len(part), dtype=bool)
        for version in security_groups.get(security, securities.iloc[:0]).itertuples():
            mask = ((part.day >= version.start) & (part.day < version.end)).to_numpy()
            valid += mask
            if version.instrument_type in ("common_equity", "equity"):
                qualified |= mask
        assert valid.max() <= 1
        part["security_ok"] = qualified
        pieces.append(part)
    panel = pd.concat(pieces, ignore_index=True)
    assert not panel.duplicated(["listing_id", "day"]).any()
    assert not panel.duplicated(["listing_id", "calendar_session_id"]).any()
    del pieces
    keys = ["listing_id", "calendar_session_id"]
    bars = table(
        "dwd_security_bar",
        [*keys, "observation_start", "open", "close", "turnover", "interval_size", "price_basis"],
    )
    bars["day"] = local_days(bars.observation_start)
    bars = bars.loc[
        bars.day.between(scope["read_start"], scope["execution_end"])
        & (bars.interval_size == "1d")
        & (bars.price_basis == "UNADJUSTED")
    ]
    bars = bars[[*keys, "open", "close", "turnover"]].rename(
        columns={"open": "op", "close": "px", "turnover": "amount"}
    )
    states = table(
        "dwd_tradability_state",
        [*keys, "observation_start", "upper_price", "is_tradable", "can_buy"],
    )
    states["day"] = local_days(states.observation_start)
    states = states.loc[
        states.day.between(scope["read_start"], scope["execution_end"]),
        [*keys, "upper_price", "is_tradable", "can_buy"],
    ]
    panel = panel.merge(bars, how="left", on=keys, validate="one_to_one")
    panel = panel.merge(states, how="left", on=keys, validate="one_to_one")
    del bars, states
    db.close()
    panel = panel.sort_values(["listing_id", "day"]).reset_index(drop=True)
    for name in ("op", "px", "amount", "upper_price"):
        panel[name] = pd.to_numeric(panel[name], errors="raise").astype(float)
    group = panel.listing_id
    good_close = np.isfinite(panel.px) & (panel.px > 0)
    px = panel.px.where(good_close)
    previous = px.groupby(group).shift(1)
    raw_returns = px / previous - 1
    vol = raw_returns.groupby(group).rolling(20, min_periods=20).std(ddof=1).droplevel(
        0
    ).sort_index() * math.sqrt(252)
    momentum = px / px.groupby(group).shift(20) - 1
    amount = panel.amount.where(np.isfinite(panel.amount) & (panel.amount >= 0))
    prior = (
        amount.groupby(group)
        .shift(1)
        .groupby(group)
        .rolling(20, min_periods=20)
        .mean()
        .droplevel(0)
        .sort_index()
    )
    closes21 = (
        good_close.astype(int)
        .groupby(group)
        .rolling(21, min_periods=21)
        .sum()
        .droplevel(0)
        .sort_index()
    )
    panel["universe"] = good_close & panel.security_ok & panel.is_tradable.eq(True).fillna(False)
    panel["base_selected"] = (
        (closes21 == 21)
        & (panel.age >= filt["min_listing_sessions"])
        & (prior >= filt["min_avg_turnover_prior20"])
        & (vol <= filt["max_unadjusted_close_return_vol20"])
        & (raw_returns.abs() <= filt["max_abs_unadjusted_close_return"])
        & np.isfinite(momentum)
    )
    panel["return_vol20"] = vol
    panel["momentum20"] = momentum
    panel["selected"] = panel.base_selected & momentum.between(*filt["unadjusted_momentum20_band"])
    columns = [
        "listing_id",
        "calendar_session_id",
        "security_ok",
        "op",
        "upper_price",
        "is_tradable",
        "can_buy",
    ]
    if evaluator is not None or panel_evaluator is not None:
        columns.extend(["px", "day"])
    future = panel[columns].rename(columns={x: "next_" + x for x in columns if x != "listing_id"})
    label_end = audit_decision_end or scope["decision_end"]
    assert scope["decision_end"] <= label_end <= scope["execution_end"]
    labelled = panel.loc[panel.day.between(scope["decision_start"], label_end)].merge(
        future,
        how="left",
        left_on=["listing_id", "next_session"],
        right_on=["listing_id", "next_calendar_session_id"],
        validate="one_to_one",
    )

    def true(name):
        return labelled[name].eq(True).fillna(False).to_numpy(dtype=bool)

    conditions = [
        ~true("next_security_ok"),
        ~(np.isfinite(labelled.next_op) & (labelled.next_op > 0)).to_numpy(),
        (labelled.next_is_tradable.isna() | labelled.next_can_buy.isna()).to_numpy(),
        ~true("next_is_tradable"),
        ~true("next_can_buy"),
        ~(np.isfinite(labelled.next_upper_price) & (labelled.next_upper_price > 0)).to_numpy(),
        (labelled.next_op >= labelled.next_upper_price - 0.005).to_numpy(),
    ]
    labelled["reason"] = np.select(
        conditions,
        [
            "missing_next_identity",
            "missing_or_invalid_open",
            "missing_state",
            "suspended",
            "cannot_buy",
            "missing_or_invalid_upper",
            "upper_price_block",
        ],
        "pass",
    )

    if panel_evaluator is not None:
        assert evaluator is None
        return panel_evaluator(q, labelled, panel, author, out, began)
    if evaluator is not None:
        return evaluator(q, labelled, author, out, began)

    def stats(rows):
        n = len(rows)
        reasons = {k: int(v) for k, v in rows.reason.value_counts().sort_index().items()}
        passed = reasons.get("pass", 0)
        assert sum(reasons.values()) == n
        return {
            "opportunities": n,
            "price_guard_pass": passed,
            "blocked": n - passed,
            "blocked_rate": (n - passed) / n if n else None,
            "decision_days": int(rows.day.nunique()),
            "listings": int(rows.listing_id.nunique()),
            "reasons": reasons,
        }

    universe = labelled.loc[labelled.universe]
    selected = universe.loc[universe.selected]
    metrics = {"candidate": stats(selected), "control": stats(universe)}
    metrics["by_year"] = {
        str(year): {
            "candidate": stats(selected.loc[selected.day.dt.year == year]),
            "control": stats(universe.loc[universe.day.dt.year == year]),
        }
        for year in (2020, 2021)
    }
    metrics["by_exchange"] = {
        name: {
            "candidate": stats(selected.loc[selected.venue_id == venue]),
            "control": stats(universe.loc[universe.venue_id == venue]),
        }
        for name, venue in VENUES.items()
    }
    counts = selected.listing_id.value_counts()
    metrics["top_listing_share"] = int(counts.iloc[0]) / len(selected)
    metrics["top10_listing_share"] = int(counts.iloc[:10].sum()) / len(selected)
    decision = q["decision"]
    sufficient = (
        len(selected) >= decision["min_nominal"]
        and len(counts) >= decision["min_nominal_listings"]
        and metrics["top_listing_share"] <= decision["max_top_listing_share"]
        and metrics["top10_listing_share"] <= decision["max_top10_listing_share"]
        and all(
            x["candidate"]["opportunities"] >= decision["min_nominal_per_year"]
            and x["candidate"]["decision_days"] >= decision["min_decision_days_per_year"]
            for x in metrics["by_year"].values()
        )
    )
    metrics["sample_sufficient"] = bool(sufficient)
    material = metrics["candidate"]["blocked_rate"] >= decision["material_block_rate"] or any(
        x["candidate"]["blocked_rate"] >= decision["material_year_block_rate"]
        for x in metrics["by_year"].values()
    )
    metrics["decision_branch"] = (
        "insufficient" if not sufficient else "material" if material else "small"
    )
    metrics["duplicate_listing_date_keys"] = 0
    metrics["panel_rows"] = len(panel)
    mismatches = []

    def compare(want, got, path="metrics"):
        if isinstance(want, dict):
            for key, value in want.items():
                compare(value, got.get(key), path + "." + key)
        elif isinstance(want, float):
            if got is None or not math.isclose(want, got, rel_tol=1e-11, abs_tol=1e-12):
                mismatches.append({"path": path, "independent": want, "author": got})
        elif want != got:
            mismatches.append({"path": path, "independent": want, "author": got})

    compare(metrics, author["metrics"])
    label_bytes = (
        selected[["listing_id", "day", "reason"]]
        .sort_values(["listing_id", "day"])
        .to_csv(index=False)
        .encode()
    )
    evidence = {
        "schema": "quanthus.r1-v2-independent.v1",
        "metrics": metrics,
        "mismatches": mismatches,
        "candidate_key_reason_digest": "sha256:" + hashlib.sha256(label_bytes).hexdigest(),
        "implementation": (
            "Pandas interval masks, merges, independent rolling returns, "
            "calendar next-session lookup; no candidate module import"
        ),
        "wall_seconds": time.monotonic() - began,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "packages": {
            x: importlib.metadata.version(x) for x in ("numpy", "pandas", "duckdb", "pyarrow")
        },
    }
    save(out / "independent-reproduction.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False))
    return 0 if not mismatches else 2


def readback(original, out, spec):
    from huggingface_hub import HfApi

    started = time.monotonic()
    api = HfApi()
    info = api.dataset_info(spec["result_dataset"], revision=spec["result_revision"])
    assert info.private and info.sha == spec["result_revision"]
    prefix = spec["result_prefix"] + "/"
    names = sorted(
        x
        for x in api.list_repo_files(
            spec["result_dataset"], repo_type="dataset", revision=spec["result_revision"]
        )
        if x.startswith(prefix)
    )
    assert len(names) == 10
    target = out / "hf-readback"
    target.mkdir(parents=True)
    entries = []
    for name in names:
        path = Path(
            api.hf_hub_download(
                spec["result_dataset"],
                name,
                repo_type="dataset",
                revision=spec["result_revision"],
                force_download=True,
                cache_dir=str(out / "hf-cache"),
            )
        )
        local = original / "package" / Path(name).name
        assert path.read_bytes() == local.read_bytes()
        shutil.copyfile(path, target / local.name)
        entries.append({"path": name, "sha256": sha(path), "size_bytes": path.stat().st_size})
    manifest_path = next(target.glob("*-manifest.json"))
    manifest = read(manifest_path)
    assert (
        canonical({k: v for k, v in manifest.items() if k != "digest"})
        == spec["package_digest"]
        == manifest["digest"]
    )
    assert next(target.glob("*-READY")).read_text().strip() == sha(manifest_path)
    for name, item in manifest["artifacts"].items():
        assert (
            sha(target / name) == item["sha256"]
            and (target / name).stat().st_size == item["size_bytes"]
        )
    receipt = {
        "revision": info.sha,
        "files": entries,
        "package_digest": manifest["digest"],
        "readback_all_files": True,
        "wall_seconds": time.monotonic() - started,
    }
    save(out / "hf-readback.json", receipt)
    print(json.dumps(receipt))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["reproduce", "readback"])
    p.add_argument("--original", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    spec = read(SPEC)
    return (
        reproduce(a.original, a.out, spec)
        if a.mode == "reproduce"
        else readback(a.original, a.out, spec)
    )


if __name__ == "__main__":
    raise SystemExit(main())
