#!/usr/bin/env python3
"""Independent pandas two-calendar reconstruction and bounded R5 reference run."""

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import re
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

from research_r1_v2_independent_audit import canonical, read, reproduce, save
from research_r2_independent_audit import evaluate as evaluate_primary

ROOT = Path(__file__).resolve().parents[1]
REQUEST = "config/research/research-r5/request.v1.json"


def evaluate(q, labelled, panel, author, out, began, *, prepared_evaluator=None):
    import numpy as np

    prepared = None

    def labeler(base):
        nonlocal prepared
        first = panel[["listing_id", "calendar_session_id", "next_session"]].rename(
            columns={"calendar_session_id": "clock1_session", "next_session": "second_session"}
        )
        second = panel[["listing_id", "calendar_session_id", "security_ok", "op"]].rename(
            columns={
                "calendar_session_id": "clock2_session",
                "security_ok": "second_security_ok",
                "op": "second_op",
            }
        )
        base = base.merge(
            first,
            how="left",
            left_on=["listing_id", "next_session"],
            right_on=["listing_id", "clock1_session"],
            validate="one_to_one",
        )
        base = base.merge(
            second,
            how="left",
            left_on=["listing_id", "second_session"],
            right_on=["listing_id", "clock2_session"],
            validate="one_to_one",
        )
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            base["response"] = base.second_op / base.next_op - 1
            base["intraday"] = base.next_px / base.next_op - 1
            base["overnight"] = base.second_op / base.next_px - 1
            base["interaction"] = base.intraday * base.overnight
        conditions = [
            base.day.dt.strftime("%Y-%m-%d").eq(q["analysis"]["audit_right_censored_decision"]),
            ~base.reason.eq("pass"),
            ~(np.isfinite(base.next_px) & (base.next_px > 0)),
            ~base.second_security_ok.eq(True).fillna(False),
            ~(np.isfinite(base.second_op) & (base.second_op > 0)),
            ~np.isfinite(base[["response", "intraday", "overnight", "interaction"]]).all(axis=1),
        ]
        base["label_reason"] = np.select(
            conditions,
            [
                "right_censored_second_session",
                base.reason,
                "missing_or_invalid_next_close",
                "missing_second_identity",
                "missing_or_invalid_second_open",
                "nonfinite_response",
            ],
            "pass",
        )
        prepared = base
        return base

    primary_code = evaluate_primary(q, labelled, author, out, began, labeler=labeler)
    assert prepared is not None
    receipt = read(out / "independent-reproduction.json")
    valid = prepared.loc[prepared.label_reason.eq("pass")]
    dates = {x["date"] for x in receipt["metrics"]["paired_daily"]}
    matched = valid.loc[valid.day.dt.strftime("%Y-%m-%d").isin(dates)]
    components = {}
    for name in ("intraday", "overnight", "interaction"):
        cells = []
        for (day, venue), rows in matched.groupby(["day", "venue_id"], sort=True):
            candidate = float(np.mean(rows.loc[rows.selected, name]))
            control = float(np.mean(rows.loc[~rows.selected, name]))
            cells.append(
                {
                    "date": day.date().isoformat(),
                    "venue_id": venue,
                    "candidate_mean": candidate,
                    "control_mean": control,
                    "increment": candidate - control,
                }
            )
        daily = [
            {
                "date": day,
                "increment": float(np.mean([x["increment"] for x in cells if x["date"] == day])),
            }
            for day in sorted(dates)
        ]
        components[name] = {
            "paired_cells": cells,
            "paired_daily": daily,
            "mean_increment": float(np.mean([x["increment"] for x in daily])) if daily else None,
            "mean_increment_by_year": {
                str(year): float(np.mean(v)) if v else None
                for year in q["analysis"]["required_years"]
                for v in [[x["increment"] for x in daily if x["date"].startswith(str(year))]]
            },
        }
    ledger = [
        {
            "group": "candidate" if selected else "control",
            "year": int(year),
            "reason": reason,
            "count": len(rows),
        }
        for (selected, year, reason), rows in prepared.groupby(
            ["selected", prepared.day.dt.year, "label_reason"], sort=True
        )
    ]
    residual = valid.response - valid.intraday - valid.overnight - valid.interaction
    extra = {
        "components": components,
        "label_ledger_by_year": ledger,
        "decomposition": {
            "identity": "primary = intraday + overnight + interaction",
            "max_opportunity_residual": float(np.max(np.abs(residual))) if len(residual) else None,
            "mean_residual": (
                receipt["metrics"]["mean_increment"]
                - sum(c["mean_increment"] for c in components.values())
            )
            if receipt["metrics"]["mean_increment"] is not None
            else None,
        },
    }

    def compare(want, got, path="metrics"):
        if isinstance(want, dict) and isinstance(got, dict):
            for key, value in want.items():
                compare(value, got.get(key), path + "." + key)
        elif isinstance(want, list) and isinstance(got, list):
            if len(want) != len(got):
                receipt["mismatches"].append({"path": path, "length_mismatch": True})
            for i, (left, right) in enumerate(zip(want, got, strict=False)):
                compare(left, right, path + f"[{i}]")
        elif isinstance(want, float) and isinstance(got, (int, float)):
            error = abs(want - got)
            receipt["max_absolute_error"] = max(receipt["max_absolute_error"], error)
            if not math.isfinite(error) or error > 1e-10:
                receipt["mismatches"].append({"path": path, "independent": want, "producer": got})
        elif want != got:
            receipt["mismatches"].append({"path": path, "independent": want, "producer": got})

    compare(extra, author["metrics"])
    receipt["schema"] = "quanthus.r5-clock-independent.v1"
    receipt["metrics"].update(extra)
    receipt["wall_seconds"] = time.monotonic() - began
    receipt["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    receipt["request_digest"] = canonical(q)
    receipt["execution_git"] = author["identity"]["git"]
    receipt["r4_capital_use"] = "park"
    save(out / "independent-reproduction.json", receipt)
    if not primary_code and not receipt["mismatches"] and prepared_evaluator is not None:
        return prepared_evaluator(prepared, receipt["metrics"])
    return 2 if primary_code or receipt["mismatches"] else 0


def guarded(args, q, identity=None):
    args.out.mkdir(parents=True, exist_ok=True)
    marker = args.out / "reference-attempt-start.json"
    assert not marker.exists(), "one independent attempt; located recovery needs separate authority"
    if identity is not None:
        save(args.out / "reference-source-identity.json", identity)
    save(
        marker,
        {"git": args.execution_git, "request_digest": canonical(q), "started_epoch": time.time()},
    )
    command = [
        sys.executable,
        __file__,
        "--original",
        str(args.original.resolve()),
        "--out",
        str(args.out.resolve()),
        "--execution-git",
        args.execution_git,
    ]
    env = dict(os.environ, OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2", MKL_NUM_THREADS="2")
    began = time.monotonic()
    peak = size = 0
    failure = None
    with (args.out / "reference.log").open("wb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, env=env, start_new_session=True)
        while process.poll() is None:
            try:
                status = Path(f"/proc/{process.pid}/status").read_text()
                match = re.search(r"VmHWM:\s+(\d+) kB", status)
                if match:
                    peak = max(peak, int(match.group(1)) * 1024)
            except FileNotFoundError:
                pass
            size = max(size, sum(p.stat().st_size for p in args.out.rglob("*") if p.is_file()))
            if time.monotonic() - began > q["budget"]["max_stage_wall_seconds"]["screen"]:
                failure = "wall_exceeded"
            elif peak > q["budget"]["max_rss_bytes"]:
                failure = "rss_exceeded"
            elif size > q["budget"]["max_scratch_bytes"]:
                failure = "scratch_exceeded"
            if failure:
                os.killpg(process.pid, signal.SIGKILL)
                break
            time.sleep(0.2)
        code = process.wait()
    # A child may exit between polls after its final write or resource peak.
    size = max(size, sum(p.stat().st_size for p in args.out.rglob("*") if p.is_file()))
    reference_receipt = args.out / "independent-reproduction.json"
    if reference_receipt.is_file():
        peak = max(peak, int(read(reference_receipt).get("peak_rss_kib", 0)) * 1024)
    elapsed = time.monotonic() - began
    if failure is None:
        if elapsed > q["budget"]["max_stage_wall_seconds"]["screen"]:
            failure = "wall_exceeded"
        elif peak > q["budget"]["max_rss_bytes"]:
            failure = "rss_exceeded"
        elif size > q["budget"]["max_scratch_bytes"]:
            failure = "scratch_exceeded"
    receipt = {
        "git": args.execution_git,
        "request_digest": canonical(q),
        "exit_code": code,
        "failure": failure,
        "wall_seconds": elapsed,
        "peak_rss_bytes": peak,
        "peak_scratch_bytes": size,
    }
    save(args.out / "reference-guardian.json", receipt)
    print(json.dumps(receipt))
    return 0 if code == 0 and failure is None else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execution-git", required=True)
    parser.add_argument("--guarded", action="store_true")
    args = parser.parse_args()
    q = read(ROOT / REQUEST)
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == (
        args.execution_git
    )
    assert not subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
    ).strip(), "reference tracked source tree must be clean"
    protected = [
        REQUEST,
        q["environment"]["lock_path"],
        "scripts/research_r5_independent_audit.py",
        "scripts/research_r2_independent_audit.py",
        "scripts/research_r1_v2_independent_audit.py",
    ]
    identity = {
        "git": args.execution_git,
        "tracked_tree_clean": True,
        "files_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in protected
        },
    }
    assert (
        "sha256:" + identity["files_sha256"][q["environment"]["lock_path"]]
        == (q["environment"]["lock_sha256"])
    )
    for line in (ROOT / q["environment"]["lock_path"]).read_text().splitlines():
        name, version = line.split("==", 1)
        assert importlib.metadata.version(name) == version, name
    if args.guarded:
        return guarded(args, q, identity)
    resource.setrlimit(resource.RLIMIT_CPU, (q["budget"]["max_cpu_seconds"],) * 2)
    save(args.out / "reference-source-identity.json", identity)
    spec = {
        "request": REQUEST,
        "request_digest": canonical(q),
        "execution_git": args.execution_git,
        "result_digest": read(args.original / "result.json")["digest"],
        "allow_unpublished": True,
    }
    return reproduce(
        args.original,
        args.out,
        spec,
        panel_evaluator=evaluate,
        audit_decision_end=q["analysis"]["audit_right_censored_decision"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
