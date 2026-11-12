#!/usr/bin/env python3
"""Frozen native capital execution and resource envelope; no source repair."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

from quanthus.backtest.capital_purpose import copy_package, load_purpose, verify_package
from quanthus.backtest.native_capital_replay import replay_arms
from quanthus.backtest.policy import load_execution_policy
from quanthus.hf_dwd_research import (
    clean_environment,
    digest,
    directory_bytes,
    fetch,
    file_digest,
    read,
    verify_members,
    write,
)
from quanthus.hf_dwd_research_sql import calculate

ROOT = Path(__file__).resolve().parents[1]


def identity(q, request, expected):
    git = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert git == expected
    assert not subprocess.check_output(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            "src",
            "scripts",
            "tests",
            str(request.resolve().relative_to(ROOT)),
        ],
        cwd=ROOT,
        text=True,
    ).strip()
    assert q["schema"] in {
        "quanthus.native-capital-request.v1",
        "quanthus.native-capital-request.v2",
    }
    if q["schema"].endswith(".v2"):
        effective = q["effective_input"]
        value = read(ROOT / effective["path"])
        assert file_digest(ROOT / effective["path"]) == effective["manifest_sha256"]
        assert digest(value) == effective["content_digest"]
        for key in (
            "authority",
            "purpose",
            "scope",
            "screen",
            "capital",
            "decision",
            "policy",
            "r2_request",
            "native_knowledge_cutoff",
            "source_knowledge_cutoff",
        ):
            assert value[key] == q[key]
        for name, sha in value["consumer_code"].items():
            assert file_digest(ROOT / name) == sha
        assert effective["clock_identity"] == {
            "native_content_digest": q["authority"]["content_digest"],
            "purpose_sha256": q["purpose"]["purpose_sha256"],
            "consumer_closure_sha256": digest(value["consumer_code"]),
            "native_knowledge_cutoff": q["native_knowledge_cutoff"],
            "purpose_qualified_at": q["purpose"]["qualified_at"],
        }
        assert effective["source_revision"] == "capital-effective@" + effective["content_digest"]
    assert q["authorization"]["issue"] == 628 and q["authorization"]["remaining_BCD"] is True
    assert q["scope"]["forbidden_after"] == "2022-01-01"
    assert sys.version.split()[0] == q["environment"]["python"] and sys.platform == "linux"
    assert file_digest(ROOT / q["environment"]["lock_path"]) == q["environment"]["lock_sha256"]
    assert file_digest(ROOT / q["closure"]["path"]) == q["closure"]["sha256"]
    assert file_digest(ROOT / q["policy"]["path"]) == q["policy"]["sha256"]
    assert digest(read(ROOT / q["r2_request"]["path"])) == q["r2_request"]["digest"]
    versions = {}
    for line in (ROOT / q["environment"]["lock_path"]).read_text().splitlines():
        name, version = line.split("==")
        assert importlib.metadata.version(name) == version
        versions[name] = version
    return {
        "git": git,
        "request_digest": digest(q),
        "environment": versions,
        "capital_use": "guarded-retrospective",
        "native_source_clock_preserved": True,
        "request_id": q["request_id"],
        "task_id": q["task_id"],
        "attempt_id": q["attempt_id"],
        "native_authority": q["authority"],
        "effective_input": q.get("effective_input"),
    }


def load_native(db, q, members, source):
    for native, short, clock in [
        ("dwd_corporate_action", "actions", "effective_at"),
        ("dwd_corporate_action_term", "terms", "effective_at"),
        ("dwd_quality_exception", "quality", "affected_effective_at"),
    ]:
        paths = [
            str(source / "DWD" / x["path"])
            for x in members
            if x["path"].startswith("payload/" + native + "/")
        ]
        db.read_parquet(paths, hive_partitioning=True).create_view("raw_" + short)
        db.execute(
            f"CREATE TEMP TABLE capital_{short} AS SELECT * FROM raw_{short} "
            f"WHERE knowledge_to IS NULL AND CAST({clock} AS DATE) BETWEEN DATE '2020-01-02' "
            "AND DATE '2021-12-31'"
        )
    for table in [
        "dwd_security_bar",
        "dwd_trading_calendar",
        "dwd_tradability_state",
        "dwd_listing",
        "dwd_security",
        "capital_actions",
        "capital_terms",
        "capital_quality",
    ]:
        latest = db.execute(f"SELECT max(knowledge_from) FROM {table}").fetchone()[0]
        assert latest is not None and latest <= __import__("datetime").datetime.fromisoformat(
            q.get("native_knowledge_cutoff", q["source_knowledge_cutoff"])
        )
    db.execute("""CREATE TEMP TABLE capital_execution AS
        SELECT p.listing_id,p.trade_date,l.security_id,l.venue_id,
        l.listing_code,l.listing_currency,l.lot_size,
        b.open,b.close,b.knowledge_from AS bar_known,t.knowledge_from AS state_known,
        t.is_tradable,t.can_buy,t.can_sell,t.reason_code,t.upper_price,t.lower_price,
        s.knowledge_from AS security_known,l.knowledge_from AS listing_known
        FROM panel p JOIN dwd_listing l ON l.listing_id=p.listing_id AND l.knowledge_to IS NULL
        AND p.trade_date>=CAST(l.effective_from AS DATE)
        AND (l.effective_to IS NULL OR p.trade_date<CAST(l.effective_to AS DATE))
        JOIN dwd_security s ON s.security_id=l.security_id AND s.knowledge_to IS NULL
        AND p.trade_date>=CAST(s.effective_from AS DATE)
        AND (s.effective_to IS NULL OR p.trade_date<CAST(s.effective_to AS DATE))
        LEFT JOIN bars b ON b.listing_id=p.listing_id
          AND b.calendar_session_id=p.calendar_session_id
        LEFT JOIN states t ON t.listing_id=p.listing_id
          AND t.calendar_session_id=p.calendar_session_id;
        CREATE INDEX capital_execution_key ON capital_execution(listing_id,trade_date)
    """)
    assert (
        db.execute(
            "SELECT count(*) FROM (SELECT listing_id,trade_date FROM capital_execution "
            "GROUP BY 1,2 HAVING count(*)<>1)"
        ).fetchone()[0]
        == 0
    )


def formal(q, members, ident, job):
    import duckdb

    began = time.monotonic()
    purpose = load_purpose(q, job / "purpose") if "purpose" in q else None
    db = duckdb.connect()
    db.execute("SET TimeZone='Asia/Shanghai'")
    db.execute("SET threads=2")
    db.execute("SET memory_limit='3GB'")
    scratch = job / "scratch"
    scratch.mkdir()
    db.execute("SET temp_directory=?", [str(scratch)])
    r2 = read(ROOT / q["r2_request"]["path"])
    paths = {
        name: [
            str(job / "source/DWD" / x["path"])
            for x in members
            if x["path"].startswith("payload/" + name + "/")
        ]
        for name in [
            "dwd_security_bar",
            "dwd_trading_calendar",
            "dwd_tradability_state",
            "dwd_listing",
            "dwd_security",
        ]
    }
    feature_start = time.monotonic()
    calculate(db, r2, paths, features_only=True)
    feature_seconds = time.monotonic() - feature_start
    exec_start = time.monotonic()
    load_native(db, q, members, job / "source")
    stages = {
        "T_features_TMP": feature_seconds,
        "native_execution_TMP": time.monotonic() - exec_start,
        "native_execution_rows": db.execute("SELECT count(*) FROM capital_execution").fetchone()[0],
        "prerequisite_disposition": "build once per Job, shared by candidate/control",
    }
    value = replay_arms(
        db, q, load_execution_policy(ROOT / q["policy"]["path"]), ident["git"], purpose
    )
    db.close()
    result = {
        "schema": "quanthus.native-capital-result.v1",
        "identity": ident,
        "stages": stages,
        **value,
        "wall_seconds": time.monotonic() - began,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    result["digest"] = digest(result)
    write(job / "result.json", result)
    print(
        json.dumps(
            {
                "decision": result["decision"],
                "wall_seconds": result["wall_seconds"],
                "holds": {k: v.get("hold") for k, v in result["arms"].items()},
            }
        )
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["formal", "independent"])
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--source-cache", type=Path, required=True)
    p.add_argument("--expected-git", required=True)
    p.add_argument("--guarded", action="store_true")
    p.add_argument("--author-job", type=Path)
    p.add_argument("--purpose-cache", type=Path)
    a = p.parse_args()
    q = read(a.request)
    ident = identity(q, a.request, a.expected_git)
    members = read(ROOT / q["closure"]["path"])["members"]
    assert len(members) == 126 and sum(x["size_bytes"] for x in members) == 192098985
    a.work.mkdir(parents=True, exist_ok=True)
    if a.guarded:
        marker = a.work / "attempt-start.json"
        assert not marker.exists(), "one guarded attempt only"
        if "purpose" in q:
            assert a.purpose_cache is not None
            copied = copy_package(q, a.purpose_cache, a.work / "purpose")
            write(a.work / "purpose-copy.json", {"identity": ident, **copied})
        write(marker, {"identity": ident, "mode": a.mode, "started_epoch": time.time()})
        command = [
            sys.executable,
            __file__,
            a.mode,
            "--request",
            str(a.request.resolve()),
            "--work",
            str(a.work.resolve()),
            "--source-cache",
            str(a.source_cache.resolve()),
            "--expected-git",
            a.expected_git,
        ]
        if a.author_job:
            command += ["--author-job", str(a.author_job.resolve())]
        began = time.monotonic()
        peak = size = 0
        failure = None
        with (a.work / "execution.log").open("wb") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=log,
                start_new_session=True,
                env=clean_environment("screen"),
            )
            while process.poll() is None:
                try:
                    status = Path(f"/proc/{process.pid}/status").read_text()
                    match = re.search(r"VmHWM:\s+(\d+) kB", status)
                    if match:
                        peak = max(peak, int(match.group(1)) * 1024)
                except FileNotFoundError:
                    pass
                size = max(size, directory_bytes(a.work))
                if time.monotonic() - began > q["budget"]["max_stage_wall_seconds"][a.mode]:
                    failure = "wall_exceeded"
                elif peak > q["budget"]["max_rss_bytes"]:
                    failure = "rss_exceeded"
                elif size > q["budget"]["max_scratch_bytes"]:
                    failure = "scratch_exceeded"
                if failure:
                    os.killpg(process.pid, signal.SIGKILL)
                    break
                time.sleep(0.25)
            code = process.wait()
        receipt = {
            "identity": ident,
            "mode": a.mode,
            "exit_code": code,
            "failure": failure,
            "wall_seconds": time.monotonic() - began,
            "peak_rss_bytes": peak,
            "peak_job_bytes": size,
            "output_digest": read(
                a.work / ("result.json" if a.mode == "formal" else "independent.json")
            ).get("digest")
            if (a.work / ("result.json" if a.mode == "formal" else "independent.json")).is_file()
            else None,
        }
        write(a.work / "guardian.json", receipt)
        print(json.dumps({k: v for k, v in receipt.items() if k != "identity"}))
        return 0 if code == 0 and failure is None else 2
    assert not (a.work / "run-start.json").exists(), (
        "one calculation or independent construction only"
    )
    write(a.work / "run-start.json", {"identity": ident, "mode": a.mode})
    if "purpose" in q:
        verify_package(q, a.work / "purpose")
    resource.setrlimit(resource.RLIMIT_CPU, (q["budget"]["max_cpu_seconds"],) * 2)
    fetch(q, members, ident, a.work, a.source_cache)
    verify_members(a.work / "source", q, members)
    if a.mode == "formal":
        formal(q, members, ident, a.work)
    else:
        assert a.author_job is not None
        from research_capital_independent import independent

        independent(q, members, ident, a.work, a.author_job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
