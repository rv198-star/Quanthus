#!/usr/bin/env python3
"""Offline audit counterexamples against the unmodified R1 v2 Worker."""

import argparse
import copy
import json
import shutil
import sys
import types
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from quanthus import hf_dwd_research as worker
from quanthus.hf_dwd_research_sql import SSE, SZSE, calculate

ROOT = Path(__file__).resolve().parents[1]


def sample_days_probe(out, request):
    import duckdb

    q = copy.deepcopy(request)
    q["scope"].update(decision_end="2021-01-07", execution_end="2021-01-08")
    q["screen"]["max_abs_unadjusted_close_return"] = 1e-12
    q["decision"].update(min_nominal=2, min_nominal_per_year=1,
                         min_decision_days_per_year=3, min_nominal_listings=1,
                         max_top_listing_share=1, max_top10_listing_share=1)
    c = duckdb.connect()
    definitions = {
        "dwd_trading_calendar": "calendar_session_id VARCHAR,venue_id VARCHAR,trade_date DATE,is_open BOOLEAN,knowledge_to TIMESTAMP",
        "dwd_listing": "listing_id VARCHAR,security_id VARCHAR,venue_id VARCHAR,effective_from DATE,effective_to DATE,knowledge_to TIMESTAMP",
        "dwd_security": "security_id VARCHAR,instrument_type VARCHAR,effective_from DATE,effective_to DATE,knowledge_to TIMESTAMP",
        "dwd_security_bar": "listing_id VARCHAR,calendar_session_id VARCHAR,observation_start DATE,open DOUBLE,close DOUBLE,turnover DOUBLE,interval_size VARCHAR,price_basis VARCHAR,knowledge_to TIMESTAMP",
        "dwd_tradability_state": "listing_id VARCHAR,calendar_session_id VARCHAR,observation_start DATE,upper_price DOUBLE,is_tradable BOOLEAN,can_buy BOOLEAN,knowledge_to TIMESTAMP",
    }
    for table, definition in definitions.items():
        c.execute(f"CREATE TABLE {table} ({definition})")
    days = []
    day = date(2019, 1, 1)
    while day <= date(2021, 1, 8):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    c.executemany("INSERT INTO dwd_trading_calendar VALUES (?,?,?,?,NULL)",
                  [(v + str(d), v, d, True) for v in (SSE, SZSE) for d in days])
    c.execute("INSERT INTO dwd_listing VALUES ('L','S',?,'2010-01-01',NULL,NULL)", [SSE])
    c.execute("INSERT INTO dwd_security VALUES ('S','common_equity','2010-01-01',NULL,NULL)")
    price = 10.0
    bar_rows, state_rows = [], []
    for d in days:
        if d < date(2020, 1, 2):
            continue
        if d not in (date(2020, 3, 10), date(2021, 1, 5)):
            price *= 1.003
        bar_rows.append(("L", SSE + str(d), d, price, price, 2e8, "1d", "UNADJUSTED"))
        state_rows.append(("L", SSE + str(d), d, price * 1.1, True, True))
    c.executemany("INSERT INTO dwd_security_bar VALUES (?,?,?,?,?,?,?,?,NULL)", bar_rows)
    c.executemany("INSERT INTO dwd_tradability_state VALUES (?,?,?,?,?,?,NULL)", state_rows)
    paths = {}
    for table in definitions:
        path = out / (table + ".parquet")
        c.execute(f"COPY {table} TO '{path}' (FORMAT PARQUET)")
        paths[table] = [str(path)]
    c.close()
    c = duckdb.connect()
    result = calculate(c, q, paths)
    c.close()
    candidates = {k: v["candidate"]["decision_days"] for k, v in result["by_year"].items()}
    controls = {k: v["control"]["decision_days"] for k, v in result["by_year"].items()}
    assert candidates == {"2020": 1, "2021": 1}
    assert all(x >= 3 for x in controls.values())
    return {"probe": "candidate_days_gate", "required_days_per_year": 3,
            "candidate_days": candidates, "control_days": controls,
            "expected_branch": "insufficient", "actual_branch": result["decision_branch"],
            "actual_sufficient": result["sample_sufficient"],
            "defect_reproduced": result["sample_sufficient"] is True}


def seed(original, destination):
    destination.mkdir()
    (destination / "source").symlink_to(original / "source", target_is_directory=True)
    for name in ("fetch.json", "result.json", "run.json", "resources.json", "attempts.json"):
        shutil.copyfile(original / name, destination / name)


def failed_stage_probe(original, out, request, members):
    job = out / "failed-screen"
    seed(original, job)
    identity = worker.read(job / "result.json")["identity"]
    resources = worker.read(job / "resources.json")
    resources["screen"].update(exit_code=-9, failure="rss_exceeded")
    worker.write(job / "resources.json", resources)
    attempts = worker.read(job / "attempts.json")
    for item in attempts:
        if item["phase"] == "screen":
            item.update(exit_code=-9, failure="rss_exceeded")
    worker.write(job / "attempts.json", attempts)
    commits = []
    class API:
        def __init__(self, **kwargs):
            pass
        def dataset_info(self, *args):
            return types.SimpleNamespace(sha="b" * 40, private=True)
        def file_exists(self, **kwargs):
            return False
        def list_repo_files(self, **kwargs):
            return ([request["publish"]["path_prefix"] + "/" + p.name
                     for p in (job / "package").iterdir()]
                    if kwargs["revision"] == "c" * 40 else [])
        def create_commit(self, **kwargs):
            commits.append(kwargs["parent_commit"])
            return types.SimpleNamespace(oid="c" * 40)
        def hf_hub_download(self, **kwargs):
            return str(job / "package" / Path(kwargs["filename"]).name)
    caught = None
    with patch.dict(sys.modules, {"huggingface_hub": types.SimpleNamespace(
            HfApi=API, CommitOperationAdd=lambda **kwargs: kwargs)}):
        try:
            worker.publish(request, members, identity, job)
        except Exception as exc:
            caught = type(exc).__name__ + ": " + str(exc)
    return {"probe": "failed_screen_publication", "screen_exit": -9, "screen_failure": "rss_exceeded",
            "input_and_result_bytes": "authentic verified A2 copies; only synthetic operational failure receipts changed",
            "network": "offline fake HF only", "commit_calls": len(commits), "exception": caught,
            "expected": "reject before any commit", "defect_reproduced": len(commits) == 1}


def partial_package_probe(original, out, request, members):
    job = out / "partial-package"
    seed(original, job)
    identity = worker.read(job / "result.json")["identity"]
    package = job / "package"
    package.mkdir()
    worker.write(package / worker.artifact_name(request, "request"), request)
    errors = []
    for _ in range(2):
        try:
            worker.build_package(request, members, identity, job)
        except Exception as exc:
            errors.append(type(exc).__name__ + ": " + str(exc))
    return {"probe": "partial_package_retry", "attempt_errors": errors,
            "disposition": "recoverable manually by quarantining partial package; automatic retry remains stuck",
            "defect_reproduced": len(errors) == 2}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--original", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True)
    request = worker.read(ROOT / "config/research/research-r1/request.v2.a2.json")
    members = worker.read(ROOT / request["closure"]["path"])["members"]
    result = {"reviewed_code_git": "2dc01f18a2e6655b67cb2aa771b226231026e5b9",
              "probes": [sample_days_probe(a.out, request),
                         failed_stage_probe(a.original, a.out, request, members),
                         partial_package_probe(a.original, a.out, request, members)]}
    worker.write(a.out / "fault-probes.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
