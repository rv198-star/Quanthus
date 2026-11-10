"""Checkpointed lexical BaoStock history collection for the frozen Hist-1D scope."""

from __future__ import annotations

import argparse
import gzip
import importlib
import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.hist_1d import file_sha256
from quanthus.pipelines.hist_1d_audit import BASE251_MANIFEST

FIELDS = "date,code,open,high,low,close,volume,amount,tradestatus,isST"


def make_plan(workspace: Path, base251: Path) -> dict[str, Any]:
    target = workspace / "COLLECTION-PLAN.json"
    if target.exists():
        return dict(json.loads(target.read_bytes()))
    if file_sha256(base251 / "manifest.json") != BASE251_MANIFEST:
        raise ValueError("BASE251_REFERENCE_IDENTITY_MISMATCH")
    duckdb = importlib.import_module("duckdb")
    c = duckdb.connect()
    c.execute("SET TimeZone='Asia/Shanghai'")
    for name, directory in [("listings", "dwd_listing"), ("status", "dwd_listing_status_interval")]:
        c.read_parquet(
            [str(p) for p in (base251 / "payload" / directory).rglob("*.parquet")],
            hive_partitioning=False,
        ).create_view(name)
    missing = workspace / "audit-a2/missing-listing-sessions.parquet"
    c.read_parquet(str(missing)).create_view("missing")
    rows = c.execute("""WITH lifetimes AS (
          SELECT listing_id,min(CAST(effective_from AS DATE)) AS start,
                 coalesce(max(CAST(effective_to AS DATE)),DATE '2011-01-01') AS stop
          FROM status WHERE knowledge_to IS NULL AND status_type='LISTING_LIFECYCLE'
          AND status_value='LISTED' GROUP BY listing_id),
        gaps AS (SELECT listing_id,min(trade_date) AS gap_start,max(trade_date) AS gap_end,
                        count(*) AS missing_sessions FROM missing GROUP BY listing_id)
        SELECT l.listing_id,l.security_id,l.listing_code,l.board_code,
          greatest(DATE '1990-12-19',least(t.start,coalesce(g.gap_start,t.start))) AS start,
          least(DATE '2019-12-31',greatest(least(t.stop-1,DATE '2010-12-31'),
                                        coalesce(g.gap_end,DATE '1990-12-19'))) AS stop,
          coalesce(g.missing_sessions,0) AS missing_sessions
        FROM listings l JOIN lifetimes t USING(listing_id) LEFT JOIN gaps g USING(listing_id)
        WHERE l.knowledge_to IS NULL AND l.listing_currency='CNY'
        AND l.board_code IN ('SSE_main','SZ_main','ChiNext','CHINEXT','STAR')
        AND (t.start<DATE '2011-01-01' OR g.listing_id IS NOT NULL)
        ORDER BY missing_sessions DESC,l.listing_code,l.listing_id""").fetchall()
    tasks = [
        {
            "listing_id": r[0],
            "security_id": r[1],
            "listing_code": r[2],
            "board_code": r[3],
            "start_date": str(r[4]),
            "end_date": str(r[5]),
            "missing_sessions": r[6],
            "code": ("sh." if r[3] == "SSE_main" else "sz.") + r[2],
        }
        for r in rows
    ]
    plan = {
        "schema_version": "quanthus.hist-1d.collection.v1",
        "source": "BaoStock 0.9.3",
        "base251_manifest_sha256": BASE251_MANIFEST,
        "missing_ledger_sha256": file_sha256(missing),
        "tasks": tasks,
        "max_logical_queries": 6000,
        "queries_per_second": 5,
        "max_wall_seconds": 3600,
        "daily_fields": FIELDS,
        "factor_end_date": "2011-01-31",
        "max_rows_per_query": 10000,
        "failure_stop_count": 10,
    }
    target.write_text(json.dumps(plan, indent=2) + "\n")
    c.close()
    return plan


def capture(
    bs: Any, task: dict[str, Any], method: str, path: Path, plan_digest: str
) -> dict[str, Any]:
    sidecar = path.with_suffix(path.suffix + ".receipt.json")
    if path.exists() or sidecar.exists():
        if not path.is_file() or not sidecar.is_file():
            raise ValueError("INCOMPLETE_CAPTURE_CHECKPOINT")
        receipt: dict[str, Any] = json.loads(sidecar.read_bytes())
        if receipt["plan_sha256"] != plan_digest or file_sha256(path) != receipt["sha256"]:
            raise ValueError("CAPTURE_CHECKPOINT_IDENTITY_MISMATCH")
        return {**receipt, "reused": True}
    started = datetime.now(timezone.utc).isoformat()
    if method == "daily":
        args = {
            "code": task["code"],
            "fields": FIELDS,
            "start_date": task["start_date"],
            "end_date": task["end_date"],
            "frequency": "d",
            "adjustflag": "3",
        }
        response = bs.query_history_k_data_plus(**args)
    else:
        args = {"code": task["code"], "start_date": "1990-12-19", "end_date": "2011-01-31"}
        response = bs.query_adjust_factor(**args)
    rows = []
    while response.error_code == "0" and response.next():
        rows.append(response.get_row_data())
        if len(rows) > 10000:
            raise ValueError("COLLECTION_ROW_CAP_EXCEEDED")
    completed = datetime.now(timezone.utc).isoformat()
    payload = {
        "source": "BaoStock 0.9.3",
        "method": method,
        "request": args,
        "listing_id": task["listing_id"],
        "security_id": task["security_id"],
        "acquisition_started_at": started,
        "acquisition_completed_at": completed,
        "representation": "decoded vendor protocol strings; no float conversion",
        "error_code": response.error_code,
        "error_message": response.error_msg,
        "fields": response.fields,
        "rows": rows,
    }
    raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as stream, gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as zipped:
        zipped.write(raw)
    receipt = {
        "plan_sha256": plan_digest,
        "path": str(path),
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
        "rows": len(rows),
        "error_code": response.error_code,
        "method": method,
        "code": task["code"],
        "reused": False,
    }
    sidecar.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def collect(workspace: Path, base251: Path, limit: int) -> dict[str, Any]:
    plan = make_plan(workspace, base251)
    if limit < 1 or limit > len(plan["tasks"]):
        raise ValueError("COLLECTION_LIMIT_OUT_OF_SCOPE")
    digest = file_sha256(workspace / "COLLECTION-PLAN.json")
    bs = importlib.import_module("baostock")
    socket.setdefaulttimeout(20)
    login = bs.login()
    if login.error_code != "0":
        raise ValueError("BAOSTOCK_LOGIN_FAILED")
    started = time.monotonic()
    receipts = []
    failures = 0
    queries = 0
    try:
        for index, task in enumerate(plan["tasks"][:limit]):
            if time.monotonic() - started > plan["max_wall_seconds"]:
                raise ValueError("COLLECTION_WALL_BUDGET_EXCEEDED")
            for method in ["daily", "factor"]:
                if queries >= plan["max_logical_queries"]:
                    raise ValueError("COLLECTION_QUERY_BUDGET_EXCEEDED")
                path = workspace / "raw/baostock" / task["listing_id"] / (method + ".json.gz")
                receipt = capture(bs, task, method, path, digest)
                receipts.append(receipt)
                if not receipt["reused"]:
                    queries += 1
                    failures += receipt["error_code"] != "0"
                    time.sleep(1 / plan["queries_per_second"])
                if failures >= plan["failure_stop_count"]:
                    raise ValueError("COLLECTION_FAILURE_BUDGET_EXCEEDED")
            if (index + 1) % 10 == 0 or index + 1 == limit:
                print(
                    json.dumps(
                        {
                            "completed": index + 1,
                            "target": limit,
                            "new_queries": queries,
                            "elapsed_s": round(time.monotonic() - started, 3),
                        }
                    ),
                    flush=True,
                )
    finally:
        bs.logout()
    summary = {
        "plan_sha256": digest,
        "target_securities": len(plan["tasks"]),
        "completed_securities": limit,
        "new_queries": queries,
        "failures": failures,
        "rows": sum(r["rows"] for r in receipts),
        "raw_bytes": sum(r["bytes"] for r in receipts),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "receipts": receipts,
    }
    target = workspace / f"COLLECTION-{limit}.json"
    target.write_text(json.dumps(summary, indent=2) + "\n")
    return {k: v for k, v in summary.items() if k != "receipts"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--base251", required=True, type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    plan = make_plan(args.workspace, args.base251)
    if args.limit is None:
        print(json.dumps({"planned_securities": len(plan["tasks"])}))
    else:
        print(json.dumps(collect(args.workspace, args.base251, args.limit)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
