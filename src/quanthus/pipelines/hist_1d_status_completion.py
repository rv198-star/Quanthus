"""Bounded, source-observed binary-ST completion for the frozen Hist-1D scope."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import importlib
import json
import os
import shutil
import socket
import subprocess
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from quanthus.pipelines.hist_1d import file_sha256
from quanthus.pipelines.hist_1d_collect import capture, select_tasks
from quanthus.pipelines.hist_1d_materialize import (
    capture_payload,
    common,
    identifier,
    midnight,
)
from quanthus.pipelines.hist_1d_release import save

FIELDS = "date,code,isST"
INPUT_MANIFEST = "60062d17b9eeaf48ce455014ff898fb04d4a99e5723a87fb51d26c0736d04a92"


def binary_value(row: dict[str, Any]) -> int:
    if row["status_type"] == "PROVIDER_DAILY_STATE":
        return {"NORMAL": 1, "ST": 2}.get(row["status_value"], 0)
    if row["status_type"] == "SPECIAL_TREATMENT":
        return {"NORMAL": 1, "ST": 2, "DELISTING_RISK": 2}.get(row["status_value"], 0)
    return 0


def range_mask(opens: list[Any], intervals: list[tuple[Any, Any]]) -> bytearray:
    result = bytearray(len(opens))
    for start, stop in intervals:
        low = bisect.bisect_left(opens, start)
        high = len(opens) if stop is None else bisect.bisect_left(opens, stop)
        result[low:high] = b"\1" * (high - low)
    return result


def runs(indices: list[int]) -> list[list[int]]:
    result: list[list[int]] = []
    for index in indices:
        if result and result[-1][1] == index:
            result[-1][1] = index + 1
        else:
            result.append([index, index + 1])
    return result


def census(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[Any]]]:
    pq = importlib.import_module("pyarrow.parquet")

    def rows(table: str) -> list[dict[str, Any]]:
        return [
            row
            for path in sorted((root / "payload" / table).rglob("*.parquet"))
            for row in pq.ParquetFile(path).read().to_pylist()
            if row["knowledge_to"] is None
        ]

    calendars: dict[str, list[Any]] = defaultdict(list)
    for row in rows("dwd_trading_calendar"):
        if (
            row["is_open"]
            and row["session_type"] == "REGULAR_DAY"
            and date(2005, 1, 1) <= row["trade_date"] <= date(2019, 12, 31)
        ):
            calendars[row["venue_id"]].append((row["trade_date"], row["open_at"]))
    for values in calendars.values():
        values.sort()
    life: dict[str, list[Any]] = defaultdict(list)
    for row in rows("dwd_listing_status_interval"):
        if row["status_type"] == "LISTING_LIFECYCLE" and row["status_value"] == "LISTED":
            life[row["listing_id"]].append((row["effective_from"], row["effective_to"]))
    known: dict[str, list[Any]] = defaultdict(list)
    for row in rows("dwd_security_status_interval"):
        value = binary_value(row)
        if value:
            known[row["security_id"]].append((row["effective_from"], row["effective_to"], value))
    tasks = []
    yearly: dict[int, dict[str, int]] = defaultdict(
        lambda: {"expected": 0, "known": 0, "missing": 0, "conflicting": 0}
    )
    seen = set()
    for listing in sorted(rows("dwd_listing"), key=lambda r: (r["listing_code"], r["listing_id"])):
        if listing["listing_id"] in seen:
            raise ValueError("DUPLICATE_CURRENT_LISTING_IDENTITY")
        seen.add(listing["listing_id"])
        calendar = calendars[listing["venue_id"]]
        opens = [item[1] for item in calendar]
        active = range_mask(opens, life[listing["listing_id"]])
        flags = bytearray(len(opens))
        for start, stop, value in known[listing["security_id"]]:
            low = bisect.bisect_left(opens, start)
            high = len(opens) if stop is None else bisect.bisect_left(opens, stop)
            for index in range(low, high):
                flags[index] |= value
        missing = []
        for index, eligible in enumerate(active):
            if not eligible:
                continue
            counts = yearly[calendar[index][0].year]
            counts["expected"] += 1
            if flags[index] == 0:
                counts["missing"] += 1
                missing.append(index)
            elif flags[index] == 3:
                counts["conflicting"] += 1
            else:
                counts["known"] += 1
        if missing:
            code = listing["listing_code"]
            tasks.append(
                {
                    "listing_id": listing["listing_id"],
                    "security_id": listing["security_id"],
                    "listing_code": code,
                    "venue_id": listing["venue_id"],
                    "code": ("sh." if listing["board_code"] in ["SSE_main", "STAR"] else "sz.")
                    + code,
                    "start_date": str(calendar[missing[0]][0]),
                    "end_date": str(calendar[missing[-1]][0]),
                    "missing_sessions": len(missing),
                    "missing_ranges": runs(missing),
                    "missing_dates_sha256": hashlib.sha256(
                        "\n".join(str(calendar[i][0]) for i in missing).encode()
                    ).hexdigest(),
                }
            )
    summary: dict[str, Any] = {
        key: sum(item[key] for item in yearly.values())
        for key in ["expected", "known", "missing", "conflicting"]
    }
    summary["by_year"] = {str(k): v for k, v in sorted(yearly.items())}
    summary["missing_listings"] = len(tasks)
    if summary["conflicting"]:
        raise ValueError("EXISTING_BINARY_STATUS_CONFLICT")
    return summary, tasks, dict(calendars)


def make_plan(workspace: Path, candidate: Path) -> dict[str, Any]:
    if file_sha256(candidate / "manifest.json") != INPUT_MANIFEST:
        raise ValueError("STATUS_COMPLETION_INPUT_CHANGED")
    path = workspace / "BINARY-STATUS-PLAN.json"
    if path.exists():
        return dict(json.loads(path.read_bytes()))
    summary, tasks, _calendars = census(candidate)
    if len(tasks) > 4000 or not tasks:
        raise ValueError("STATUS_COMPLETION_SCOPE_CHANGED")
    plan = {
        "schema_version": "quanthus.hist1d.binary-status-plan.v1",
        "candidate_manifest_sha256": INPUT_MANIFEST,
        "fields": FIELDS,
        "source": "already-qualified BaoStock query_history_k_data_plus, adjustflag=3",
        "max_queries": 4500,
        "max_worker_seconds": 2400,
        "queries_per_second": 5,
        "failure_stop_count": 10,
        "census": summary,
        "tasks": tasks,
    }
    save(path, plan)
    return plan


def collect(
    workspace: Path, candidate: Path, limit: int, shard: int, shards: int
) -> dict[str, Any]:
    plan = make_plan(workspace, candidate)
    tasks = select_tasks(plan["tasks"], limit, shard, shards)
    digest = file_sha256(workspace / "BINARY-STATUS-PLAN.json")
    bs = importlib.import_module("baostock")
    socket.setdefaulttimeout(20)
    if bs.login().error_code != "0":
        raise ValueError("STATUS_COMPLETION_LOGIN_FAILED")
    started = time.monotonic()
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, cwd=Path(__file__).resolve().parents[3]
    ).strip()
    receipts = []
    failures = 0
    for index, task in enumerate(tasks):
        if time.monotonic() - started > plan["max_worker_seconds"]:
            raise ValueError("STATUS_COMPLETION_WALL_BUDGET")
        path = workspace / "raw/baostock-status" / task["listing_id"] / "daily.json.gz"
        receipt = capture(bs, task, "daily", path, digest, daily_fields=FIELDS)
        receipts.append(receipt)
        if receipt["error_code"] != "0" or not receipt["rows"]:
            failures += 1
        if failures >= max(1, plan["failure_stop_count"] // shards):
            raise ValueError("STATUS_COMPLETION_FAILURE_BUDGET")
        if not receipt["reused"]:
            time.sleep(shards / plan["queries_per_second"])
        if (index + 1) % 25 == 0 or index + 1 == len(tasks):
            print(
                json.dumps(
                    {
                        "status_captures": index + 1,
                        "target": len(tasks),
                        "shard": shard,
                        "seconds": round(time.monotonic() - started, 3),
                    }
                ),
                flush=True,
            )
    # Each shard exits its own process. No vendor logout request is sent while
    # other shard sessions may still be active on the shared anonymous account.
    summary = {
        "code_revision": revision,
        "plan_sha256": digest,
        "shard": shard,
        "shards": shards,
        "completed": len(tasks),
        "failures": failures,
        "rows": sum(r["rows"] for r in receipts),
        "receipts": receipts,
        "new_queries": sum(not r["reused"] for r in receipts),
        "seconds": round(time.monotonic() - started, 3),
    }
    save(workspace / f"BINARY-STATUS-COLLECTION-{limit}-{shard}-of-{shards}.json", summary)
    return {k: v for k, v in summary.items() if k != "receipts"}


def build(workspace: Path, candidate: Path, output: Path) -> dict[str, Any]:
    plan = make_plan(workspace, candidate)
    digest = file_sha256(workspace / "BINARY-STATUS-PLAN.json")
    _summary, tasks, calendars = census(candidate)
    if tasks != plan["tasks"]:
        raise ValueError("STATUS_COMPLETION_FROZEN_GAPS_CHANGED")
    records, receipts = [], []
    for task in tasks:
        path = workspace / "raw/baostock-status" / task["listing_id"] / "daily.json.gz"
        payload, source_sha = capture_payload(path, digest)
        if payload["error_code"] != "0" or payload["request"]["fields"] != FIELDS:
            raise ValueError("STATUS_CAPTURE_NOT_ACCEPTABLE")
        calendar = calendars[task["venue_id"]]
        missing = {
            calendar[i][0]: i for low, high in task["missing_ranges"] for i in range(low, high)
        }
        found = {}
        seen = set()
        for raw in payload["rows"]:
            row = dict(zip(payload["fields"], raw, strict=True))
            day = date.fromisoformat(row["date"])
            if (
                row["code"] != task["code"]
                or not task["start_date"] <= str(day) <= task["end_date"]
            ):
                raise ValueError("STATUS_CAPTURE_IDENTITY_OR_DATE_CHANGED")
            if day in seen:
                raise ValueError("DUPLICATE_SOURCE_STATUS_DATE")
            seen.add(day)
            if day in missing:
                if row["isST"] not in ["0", "1"]:
                    raise ValueError("SOURCE_BINARY_STATUS_UNAVAILABLE")
                found[day] = row["isST"]
        if set(found) != set(missing):
            raise ValueError("SOURCE_BINARY_STATUS_DATES_MISSING:" + task["listing_code"])
        groups: list[list[Any]] = []
        for day, flag in sorted(found.items()):
            if (
                groups
                and groups[-1][-1][1] == flag
                and missing[day] == missing[groups[-1][-1][0]] + 1
            ):
                groups[-1].append((day, flag))
            else:
                groups.append([(day, flag)])
        base = common(payload, source_sha, task, "daily")
        for group in groups:
            start, stop = group[0][0], group[-1][0] + timedelta(days=1)
            records.append(
                {
                    **base,
                    "security_status_id": identifier(
                        "security_status",
                        task["security_id"],
                        "PROVIDER_DAILY_STATE",
                        str(start),
                        source_sha,
                    ),
                    "security_id": task["security_id"],
                    "effective_from": midnight(start),
                    "effective_to": midnight(stop),
                    "status_type": "PROVIDER_DAILY_STATE",
                    "status_value": "ST" if group[0][1] == "1" else "NORMAL",
                    "reason_code": "BAOSTOCK_BINARY_ST_COMPLETION",
                    "source_record_id": base["source_record_id"]
                    + f"#from={start}&through={group[-1][0]}",
                }
            )
        receipts.append(
            {
                "path": str(path.relative_to(workspace)),
                "sha256": source_sha.removeprefix("sha256:"),
                "source_rows": len(payload["rows"]),
                "accepted_missing_sessions": len(found),
            }
        )
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(
        candidate / "payload",
        output / "payload",
        copy_function=os.link,
        ignore=lambda directory, _names: (
            ["dwd_security_status_interval"] if Path(directory) == candidate / "payload" else []
        ),
    )
    shutil.copytree(candidate / "evidence", output / "evidence")
    for source in candidate.iterdir():
        if source.is_file() and source.name != "manifest.json":
            shutil.copyfile(source, output / source.name)
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    old_path = next((candidate / "payload/dwd_security_status_interval").rglob("*.parquet"))
    old = pq.ParquetFile(old_path).read()
    new = pa.concat_tables([old, pa.Table.from_pylist(records, schema=old.schema)])
    target = output / "payload/dwd_security_status_interval/all/part-000000.parquet"
    target.parent.mkdir(parents=True)
    pq.write_table(new, target, compression="zstd")
    after, _tasks, _cal = census(output)
    if after["missing"] or after["conflicting"]:
        raise ValueError("BINARY_STATUS_COMPLETION_COVERAGE_FAILED")
    receipt = {
        "plan_sha256": digest,
        "captures": receipts,
        "source_captures": len(receipts),
        "added_intervals": len(records),
        "completed_sessions": plan["census"]["missing"],
        "binary_census": after,
        "candidate_manifest_sha256": INPUT_MANIFEST,
    }
    save(workspace / "BINARY-STATUS-COMPLETION.json", receipt)
    save(output / "evidence/BINARY-STATUS-COMPLETION.json", receipt)
    shutil.copyfile(
        workspace / "BINARY-STATUS-PLAN.json", output / "evidence/BINARY-STATUS-PLAN.json"
    )
    return {k: v for k, v in receipt.items() if k != "captures"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--build", type=Path)
    args = parser.parse_args()
    plan = make_plan(args.workspace, args.candidate)
    if args.build:
        result = build(args.workspace, args.candidate, args.build)
    elif args.limit:
        result = collect(args.workspace, args.candidate, args.limit, args.shard, args.shards)
    else:
        result = {"tasks": len(plan["tasks"]), "census": plan["census"]}
    print(json.dumps(result, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
