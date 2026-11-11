"""Complete only explicitly missing native factor histories in a new candidate."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from quanthus.pipelines.hist_1d import file_sha256
from quanthus.pipelines.hist_1d_audit import BASE251_MANIFEST
from quanthus.pipelines.hist_1d_collect import capture
from quanthus.pipelines.hist_1d_materialize import capture_payload, factor_records
from quanthus.pipelines.hist_1d_release import save


def canonicalize_factors(connection: Any) -> Any:
    conflicts = connection.execute("""SELECT security_id,effective_from
        FROM factor_candidates GROUP BY security_id,effective_from,
        adjustment_method,adjustment_direction HAVING count(DISTINCT factor_value)>1""").fetchall()
    if conflicts:
        raise ValueError("FACTOR_EVENT_VALUE_CONFLICT")
    return connection.execute("""WITH unique_events AS (
        SELECT * FROM factor_candidates QUALIFY row_number() OVER (
        PARTITION BY security_id,effective_from,adjustment_method,adjustment_direction
        ORDER BY knowledge_from,source_artifact_sha256,source_record_id)=1)
        SELECT * REPLACE (lead(effective_from) OVER (
        PARTITION BY security_id,adjustment_method,adjustment_direction ORDER BY effective_from)
        AS effective_to) FROM unique_events ORDER BY security_id,effective_from
        """).fetch_arrow_table()


def complete(workspace: Path, product: Path, base: Path, output: Path) -> dict[str, Any]:
    if file_sha256(base / "manifest.json") != BASE251_MANIFEST:
        raise ValueError("BASE251_REFERENCE_IDENTITY_MISMATCH")
    duckdb = importlib.import_module("duckdb")
    pq = importlib.import_module("pyarrow.parquet")
    pa = importlib.import_module("pyarrow")
    c = duckdb.connect(config={"threads": 2, "memory_limit": "1GB"})
    c.execute("SET TimeZone='Asia/Shanghai'")
    for table in [
        "dwd_listing",
        "dwd_security",
        "dwd_trading_calendar",
        "dwd_security_bar",
        "dwd_adjustment_factor",
    ]:
        c.read_parquet(
            [str(p) for p in (product / "payload" / table).rglob("*.parquet")],
            hive_partitioning=False,
        ).create_view(table)
    missing = c.execute("""SELECT DISTINCT l.listing_id,l.security_id,l.listing_code,l.board_code
        FROM dwd_listing l JOIN dwd_security_bar b USING(listing_id)
        WHERE NOT EXISTS (SELECT 1 FROM dwd_adjustment_factor f WHERE f.security_id=l.security_id)
        ORDER BY l.listing_code,l.listing_id""").fetchall()
    if not 0 < len(missing) <= 145:
        raise ValueError("FACTOR_COMPLETION_SCOPE_CHANGED")
    plan: dict[str, Any] = {
        "source": "already-qualified BaoStock query_adjust_factor",
        "parent_collection_plan_sha256": file_sha256(workspace / "COLLECTION-PLAN.json"),
        "candidate_materialization_sha256": file_sha256(product / "MATERIALIZATION.json"),
        "max_queries": 145,
        "max_seconds": 600,
        "tasks": [
            {
                "listing_id": r[0],
                "security_id": r[1],
                "listing_code": r[2],
                "code": ("sh." if r[3] in ["SSE_main", "STAR"] else "sz.") + r[2],
                "factor_start_date": "1990-12-19",
                "factor_end_date": "2019-12-31",
            }
            for r in missing
        ],
    }
    plan_path = workspace / "FACTOR-COMPLETION-PLAN.json"
    if plan_path.exists():
        if json.loads(plan_path.read_bytes()) != plan:
            raise ValueError("FACTOR_COMPLETION_PLAN_CHANGED")
    else:
        save(plan_path, plan)
    digest = file_sha256(plan_path)
    bs = importlib.import_module("baostock")
    socket.setdefaulttimeout(20)
    if bs.login().error_code != "0":
        raise ValueError("FACTOR_COMPLETION_LOGIN_FAILED")
    started = time.monotonic()
    receipts, factors, empty = [], [], []
    try:
        for task in plan["tasks"]:
            if time.monotonic() - started > plan["max_seconds"]:
                raise ValueError("FACTOR_COMPLETION_TIME_BUDGET")
            path = workspace / "raw/baostock" / task["listing_id"] / "factor-completion.json.gz"
            receipt = capture(bs, task, "factor", path, digest)
            receipts.append(receipt)
            if receipt["error_code"] != "0":
                raise ValueError("FACTOR_COMPLETION_SOURCE_FAILED")
            payload, source_sha = capture_payload(path, digest)
            records = factor_records(payload, source_sha, task)
            factors.extend(records)
            if not records:
                empty.append(task["listing_code"])
            if not receipt["reused"]:
                time.sleep(0.2)
            if len(receipts) % 10 == 0:
                print(
                    json.dumps({"factor_completion": len(receipts), "target": len(missing)}),
                    flush=True,
                )
    finally:
        bs.logout()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(
        product / "payload",
        output / "payload",
        copy_function=os.link,
        ignore=lambda directory, _names: (
            ["dwd_adjustment_factor"] if Path(directory) == product / "payload" else []
        ),
    )
    shutil.copytree(product / "evidence", output / "evidence")
    original = sorted((product / "payload/dwd_adjustment_factor").rglob("*.parquet"))
    schema = pq.ParquetFile(original[0]).schema_arrow
    c.register("new_factors", pa.Table.from_pylist(factors, schema=schema))
    c.execute("""CREATE TEMP VIEW factor_candidates AS
        SELECT * FROM dwd_adjustment_factor UNION ALL SELECT * FROM new_factors""")
    duplicates = c.execute("""SELECT * FROM factor_candidates QUALIFY count(*) OVER (
        PARTITION BY security_id,effective_from,adjustment_method,adjustment_direction)>1
        ORDER BY security_id,effective_from,knowledge_from""").fetch_arrow_table()
    pq.write_table(duplicates, output / "evidence/factor-equivalent-source-observations.parquet")
    final = canonicalize_factors(c)
    target = output / "payload/dwd_adjustment_factor/all/part-000000.parquet"
    target.parent.mkdir(parents=True)
    pq.write_table(final.cast(schema), target, compression="zstd")
    for table in ["dwd_listed_company", "dwd_trading_venue", "dwd_tradability_state"]:
        sources = sorted((base / "payload" / table).rglob("*.parquet"))
        source_schema = pq.ParquetFile(sources[0]).schema_arrow
        c.read_parquet([str(p) for p in sources], hive_partitioning=False).create_view(table)
        if table == "dwd_listed_company":
            sql = """SELECT * FROM dwd_listed_company WHERE knowledge_to IS NULL
                AND company_id IN (SELECT company_id FROM dwd_security)"""
        elif table == "dwd_trading_venue":
            sql = """SELECT * FROM dwd_trading_venue WHERE knowledge_to IS NULL
                AND venue_id IN (SELECT venue_id FROM dwd_listing)"""
        else:
            sql = """SELECT * FROM dwd_tradability_state WHERE knowledge_to IS NULL
                AND listing_id IN (SELECT listing_id FROM dwd_listing)
                AND observation_start>=TIMESTAMPTZ '1990-12-19 00:00:00+08'
                AND observation_start<TIMESTAMPTZ '2020-01-01 00:00:00+08'
                ORDER BY observation_start,listing_id"""
        reference = output / "payload" / table / "all/part-000000.parquet"
        reference.parent.mkdir(parents=True)
        with pq.ParquetWriter(reference, source_schema, compression="zstd") as writer:
            for batch in c.execute(sql).fetch_record_batch(131072):
                writer.write_table(pa.Table.from_batches([batch]).cast(source_schema))
    bar_paths = sorted((product / "payload/dwd_security_bar").rglob("*.parquet"))
    c.read_parquet([str(p) for p in bar_paths], hive_partitioning=False, filename=True).create_view(
        "bar_files"
    )
    clock_sql = """FROM bar_files b JOIN dwd_trading_calendar c USING(calendar_session_id)
        WHERE b.observation_start IS DISTINCT FROM c.open_at
           OR b.observation_end IS DISTINCT FROM c.close_at"""
    clock_count = c.execute("SELECT count(*) " + clock_sql).fetchone()[0]
    if clock_count > 600:
        raise ValueError("LEGACY_CLOCK_NORMALIZATION_SCOPE_CHANGED")
    c.execute(
        "COPY (SELECT b.listing_id,b.calendar_session_id,b.observation_start old_start,"
        "b.observation_end old_end,c.open_at new_start,c.close_at new_end,b.record_source,"
        "b.source_artifact_sha256 " + clock_sql + ") TO ? (FORMAT PARQUET)",
        [str(output / "evidence/legacy-clock-normalization.parquet")],
    )
    affected_files = c.execute("SELECT DISTINCT b.filename " + clock_sql).fetchall()
    for (filename,) in affected_files:
        old = Path(filename)
        c.read_parquet(str(old), hive_partitioning=False).create_view("clock_part", replace=True)
        normalized = c.execute("""SELECT b.* REPLACE (c.open_at AS observation_start,
            c.close_at AS observation_end) FROM clock_part b
            JOIN dwd_trading_calendar c USING(calendar_session_id)
            ORDER BY observation_start,listing_id""").fetch_arrow_table()
        destination = output / old.relative_to(product)
        temporary = destination.with_suffix(".clock-tmp")
        pq.write_table(
            normalized.cast(pq.ParquetFile(old).schema_arrow), temporary, compression="zstd"
        )
        os.replace(temporary, destination)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                      cwd=Path(__file__).resolve().parents[3]).strip()
    summary = {
        "status": "FACTOR_COMPLETION_CANDIDATE",
        "securities": len(missing),
        "accepted_captures": len(receipts),
        "factor_rows_added": len(factors),
        "equivalent_factor_observations": duplicates.num_rows,
        "canonical_factor_rows": final.num_rows,
        "empty_factor_histories": empty,
        "bar_clock_normalization": {
            "rule": "hist1d-session-clock-v1",
            "rows": clock_count,
            "values": "OHLCV,turnover and source bytes preserved",
        },
        "tradability_snapshot": "Unchanged sealed Base snapshot; old missing-observation "
        "reasons are retained as reference and reconciled separately.",
        "code_revision": revision,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "plan_sha256": digest,
        "receipts": receipts,
    }
    save(workspace / "FACTOR-COMPLETION.json", summary)
    save(output / "evidence/FACTOR-COMPLETION.json", summary)
    materialization = json.loads((product / "MATERIALIZATION.json").read_bytes())
    materialization["factor_completion"] = {k: v for k, v in summary.items() if k != "receipts"}
    save(output / "MATERIALIZATION.json", materialization)
    c.close()
    return {k: v for k, v in summary.items() if k != "receipts"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--product", type=Path, required=True)
    parser.add_argument("--base251", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(complete(args.workspace, args.product, args.base251, args.output)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
