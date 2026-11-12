#!/usr/bin/env python3
"""Run only a frozen native qualification fetch/census, without a strategy."""

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

from quanthus.backtest.native_capital_qualification import census, member_table
from quanthus.hf_dwd_research import (
    digest,
    fetch,
    file_digest,
    read,
    safe_path,
    verify_members,
    write,
)

ROOT = Path(__file__).resolve().parents[1]
CODE = (
    "scripts/research_capital_qualify.py",
    "src/quanthus/backtest/native_capital_qualification.py",
    "src/quanthus/hf_dwd_research.py",
)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=["fetch", "census"])
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--expected-git", required=True)
    p.add_argument("--source-cache", type=Path)
    p.add_argument("--guarded", action="store_true")
    a = p.parse_args()
    q = read(a.request)
    assert q["schema"] == "quanthus.native-capital-qualification-request.v1"
    assert q["authorization"]["no_capital_experiment"] is True
    assert (
        q["scope"]["market_start"] == "2020-01-02" and q["scope"]["execution_end"] == "2021-12-31"
    )
    assert (
        q["scope"]["calendar_start"] == "2019-01-01"
        and q["scope"]["forbidden_after"] == "2022-01-01"
    )
    assert q["scope"]["knowledge_view"] == "latest_sealed_retrospective"
    assert q["budget"]["max_action_examples"] <= 8
    closure = safe_path(ROOT, q["closure"]["path"])
    assert file_digest(closure) == q["closure"]["sha256"]
    members = read(closure)["members"]
    assert (
        len(members) == q["closure"]["exact_members"]
        and sum(x["size_bytes"] for x in members) == q["closure"]["exact_bytes"]
    )
    assert len({x["path"] for x in members}) == len(members)
    for item in members:
        safe_path(Path("/declared-root"), item["path"])
        member_table(item["path"])
    lock = safe_path(ROOT, q["environment"]["lock_path"])
    assert file_digest(lock) == q["environment"]["lock_sha256"]
    assert (
        sys.platform == q["environment"]["platform"]
        and ".".join(map(str, sys.version_info[:3])) == q["environment"]["python"]
    )
    versions = {}
    for line in lock.read_text().splitlines():
        name, version = line.split("==", 1)
        actual = importlib.metadata.version(name)
        assert actual == version
        versions[name] = actual
    git = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert git == a.expected_git
    protected = [
        *CODE,
        str(a.request.resolve().relative_to(ROOT)),
        q["closure"]["path"],
        q["environment"]["lock_path"],
    ]
    assert not subprocess.check_output(
        ["git", "status", "--porcelain", "--", *protected], cwd=ROOT, text=True
    ).strip()
    identity = {
        "git": git,
        "code": {n: file_digest(ROOT / n) for n in CODE},
        "request_digest": digest(q),
        "environment": versions,
        "qualification_only": True,
    }
    a.work.mkdir(parents=True, exist_ok=True)
    resource.setrlimit(resource.RLIMIT_CPU, (q["budget"]["max_cpu_seconds"],) * 2)
    if a.guarded:
        from quanthus.hf_dwd_research import clean_environment, directory_bytes

        marker = a.work / (a.phase + "-guard-start.json")
        assert not marker.exists(), "guarded phase already attempted"
        write(marker, identity)
        command = [
            sys.executable,
            str(ROOT / CODE[0]),
            a.phase,
            "--request",
            str(a.request.resolve()),
            "--work",
            str(a.work.resolve()),
            "--expected-git",
            a.expected_git,
        ]
        if a.source_cache:
            command.extend(["--source-cache", str(a.source_cache.resolve())])
        began = time.monotonic()
        peak = scratch = 0
        failure = None
        with (a.work / (a.phase + ".log")).open("wb") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=log,
                start_new_session=True,
                env=clean_environment("screen" if a.phase == "census" else "fetch"),
            )
            while process.poll() is None:
                try:
                    status = Path(f"/proc/{process.pid}/status").read_text()
                    match = re.search(r"VmHWM:\s+(\d+) kB", status)
                    if match:
                        peak = max(peak, int(match.group(1)) * 1024)
                except FileNotFoundError:
                    pass
                scratch = max(scratch, directory_bytes(a.work))
                if time.monotonic() - began > q["budget"]["max_qualification_wall_seconds"]:
                    failure = "wall_exceeded"
                elif peak > q["budget"]["max_rss_bytes"]:
                    failure = "rss_exceeded"
                elif scratch > q["budget"]["max_scratch_bytes"]:
                    failure = "scratch_exceeded"
                if failure:
                    os.killpg(process.pid, signal.SIGKILL)
                    break
                time.sleep(0.2)
            code = process.wait()
        receipt = {
            "identity": identity,
            "phase": a.phase,
            "exit_code": code,
            "failure": failure,
            "wall_seconds": time.monotonic() - began,
            "peak_rss_bytes": peak,
            "peak_job_bytes": scratch,
            "qualification_only": True,
        }
        write(a.work / (a.phase + "-guard.json"), receipt)
        print(json.dumps({k: v for k, v in receipt.items() if k != "identity"}))
        return 0 if code == 0 and failure is None else 2
    if a.phase == "fetch":
        fetch(q, members, identity, a.work, a.source_cache)
    else:
        assert not any(
            any(word in name.upper() for word in ("TOKEN", "PASSWORD", "SECRET", "API_KEY"))
            for name in os.environ
        )
        assert not (a.work / "census.json").exists(), "one census attempt only"
        assert not (a.work / "census-started.json").exists(), "census already attempted"
        fetched = read(a.work / "fetch.json")
        assert fetched["identity"] == identity
        assert fetched["digest"] == digest({k: v for k, v in fetched.items() if k != "digest"})
        verify_members(a.work / "source", q, members)
        write(a.work / "census-started.json", identity)
        value = census(a.work / "source", members, q, a.work / "scratch")
        value["identity"] = identity
        value["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        value["digest"] = digest(value)
        write(a.work / "census.json", value)
        print(
            json.dumps(
                {
                    "census_digest": value["digest"],
                    "wall_seconds": value["wall_seconds"],
                    "peak_rss_kib": value["peak_rss_kib"],
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
