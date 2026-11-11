"""Source-preserving full daily dataset from two immutable, accepted parents."""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.hist_1d import file_sha256
from quanthus.pipelines.hist_1d_audit import BASE251_MANIFEST
from quanthus.pipelines.hist_1d_release import save, verify_manifest, write_manifest

HIST_MANIFEST = "7cefe59c560a12dd4f5bb4ef1d6f327ea43ece7b4a56fe85ac1e90edc9459506"
HIST_REVISION = "0824ed0bcec6b979777a387a41ca3a8c0e491098"
PROFILE = "hist1d-full-source-preserving-v1"
NATIVE = "adjustment_method='PROVIDER_ADJUST_FACTOR' AND adjustment_direction='PROVIDER_NATIVE'"


def query(connection: Any, sql: str) -> list[dict[str, Any]]:
    result = connection.execute(sql)
    return [
        dict(zip([x[0] for x in result.description], row, strict=True)) for row in result.fetchall()
    ]


def bind(connection: Any, name: str, directory: Path) -> list[Path]:
    paths = sorted(directory.rglob("*.parquet"))
    if not paths:
        raise ValueError("FULL_DAILY_TABLE_MISSING:" + str(directory))
    connection.read_parquet([str(p) for p in paths], hive_partitioning=False).create_view(name)
    return paths


def merge_reference(connection: Any, table: str, key: str) -> str:
    """Base retains its full intervals; Hist contributes only additional observations."""
    columns = [row[0] for row in connection.execute("DESCRIBE h_" + table).fetchall()]
    compare = [name for name in columns if name not in {"effective_from", "effective_to"}]
    changed = " OR ".join(f'h."{name}" IS DISTINCT FROM b."{name}"' for name in compare)
    unknown_extension = (
        "h.status_value='UNKNOWN'" if table == "dwd_security_status_interval" else "false"
    )
    n = connection.execute(
        f"SELECT count(*) FROM h_{table} h JOIN b_{table} b "
        f"USING({key},knowledge_from) WHERE ({changed}) "
        "OR h.effective_from<b.effective_from "
        "OR (b.effective_to IS NOT NULL AND "
        "(h.effective_to IS NULL OR h.effective_to>b.effective_to)) "
        f"OR (h.effective_from<>b.effective_from AND NOT ({unknown_extension}))"
    ).fetchone()[0]
    if n:
        raise ValueError("REFERENCE_PARENT_CONFLICT:" + table)
    return (
        f"SELECT * FROM b_{table} UNION ALL SELECT h.* FROM h_{table} h "
        f"ANTI JOIN b_{table} b USING({key},knowledge_from)"
    )


def factor_union(connection: Any) -> str:
    """Preserve source events and values; derive interval ends from the complete series."""
    key = "security_id,effective_from,adjustment_method,adjustment_direction"
    conflict = connection.execute(
        f"SELECT count(*) FROM h_dwd_adjustment_factor h JOIN b_dwd_adjustment_factor b "
        f"USING({key}) WHERE h.factor_value IS DISTINCT FROM b.factor_value "
        "AND b.adjustment_method='PROVIDER_ADJUST_FACTOR' "
        "AND b.adjustment_direction='PROVIDER_NATIVE'"
    ).fetchone()[0]
    if conflict:
        raise ValueError("NATIVE_FACTOR_PARENT_VALUE_CONFLICT")
    return f"""WITH events AS (
      SELECT * FROM h_dwd_adjustment_factor WHERE {NATIVE}
      UNION ALL SELECT b.* FROM b_dwd_adjustment_factor b
      ANTI JOIN h_dwd_adjustment_factor h USING({key}) WHERE {NATIVE})
      SELECT * REPLACE(lead(effective_from) OVER (
        PARTITION BY security_id,adjustment_method,adjustment_direction
        ORDER BY effective_from) AS effective_to) FROM events"""


def export(connection: Any, sql: str, destination: Path, schema: Any) -> None:
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = connection.execute(sql).fetch_arrow_table()
    pq.write_table(
        pa.Table.from_arrays(list(result.columns), schema=schema), destination, compression="zstd"
    )


def build(hist: Path, base: Path, output: Path) -> dict[str, Any]:
    started = time.monotonic()
    hist, base, output = hist.resolve(), base.resolve(), output.resolve()
    if output.exists() or any(
        output.is_relative_to(p) or p.is_relative_to(output) for p in (hist, base)
    ):
        raise ValueError("FULL_DAILY_OUTPUT_NOT_DISJOINT_OR_EXISTS")
    if (
        file_sha256(hist / "manifest.json") != HIST_MANIFEST
        or file_sha256(base / "manifest.json") != BASE251_MANIFEST
    ):
        raise ValueError("FULL_DAILY_PARENT_IDENTITY_CHANGED")
    verify_manifest(hist)
    base_manifest = json.loads((base / "manifest.json").read_bytes())
    if base_manifest["business_through"] != "2026-10-31":
        raise ValueError("FULL_DAILY_BASE_CUTOFF_CHANGED")
    index = {row["path"]: row for row in base_manifest["content"]["files"]}
    tables = sorted(p.name for p in (hist / "payload").iterdir())
    sources = []
    for table in tables:
        directory = base / "payload" / table
        if table == "dwd_security_bar":
            directory /= "interval_size=1d"
        for path in sorted(directory.rglob("*.parquet")):
            relative = path.relative_to(base).as_posix()
            expected = index[relative]
            if (
                file_sha256(path) != expected["sha256"].removeprefix("sha256:")
                or path.stat().st_size != expected["size_bytes"]
            ):
                raise ValueError("FULL_DAILY_BASE_MEMBER_CHANGED:" + relative)
            sources.append(expected)
    required = sum(row["size_bytes"] for row in sources) + 2_000_000_000
    if shutil.disk_usage(output.parent).free < required:
        raise ValueError("FULL_DAILY_FREE_SPACE_INSUFFICIENT")
    output.mkdir(parents=True)
    shutil.copytree(hist / "evidence", output / "evidence/predecessor-hist")
    for name in ("ACCEPTANCE.json", "contract.json", "manifest.json", "QUALITY.json"):
        shutil.copyfile(hist / name, output / "evidence/predecessor-hist" / name)
    save(output / "evidence/BASE251-INPUT-MEMBERS.json", sources)
    save(output / "evidence/BASE251-LIMITATIONS.json", base_manifest["assessment"]["limitations"])
    save(
        output / "evidence/BASE251-HISTORICAL-GATES.json",
        base_manifest["paired_audit"]["historical_gate"],
    )
    repo = Path(__file__).resolve().parents[3]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    shutil.copyfile(repo / "config/pipelines/hist-1d-full-v1.json", output / "contract.json")
    shutil.copyfile(repo / "scripts/read_hist_1d.py", output / "read_hist_1d.py")
    c = importlib.import_module("duckdb").connect(config={"threads": 2, "memory_limit": "2GB"})
    c.execute("SET TimeZone='Asia/Shanghai'")
    pq = importlib.import_module("pyarrow.parquet")
    counts = {}
    for table in tables:
        hist_paths = bind(c, "h_" + table, hist / "payload" / table)
        modern_dir = base / "payload" / table
        if table == "dwd_security_bar":
            modern_dir /= "interval_size=1d"
        base_paths = bind(c, "b_" + table, modern_dir)
        schema = pq.ParquetFile(base_paths[0]).schema_arrow
        if any(
            not pq.ParquetFile(p).schema_arrow.equals(schema, check_metadata=False)
            for p in hist_paths
        ):
            raise ValueError("FULL_DAILY_SCHEMA_CHANGED:" + table)
        if table == "dwd_security_bar":
            # Independent files retain every source byte, timestamp and OHLCV value.
            for _label, root, paths in (("hist", hist, hist_paths), ("base251", base, base_paths)):
                for path in paths:
                    target = output / path.relative_to(root)
                    if target.exists():
                        raise ValueError("FULL_DAILY_PARTITION_OVERLAP")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
            counts[table] = sum(
                pq.ParquetFile(p).metadata.num_rows for p in hist_paths + base_paths
            )
        elif table == "dwd_adjustment_factor":
            export(
                c, factor_union(c), output / "payload" / table / "all/part-000000.parquet", schema
            )
        elif table in {"dwd_listing_status_interval", "dwd_security_status_interval"}:
            key = (
                "listing_status_id"
                if table == "dwd_listing_status_interval"
                else "security_status_id"
            )
            export(
                c,
                merge_reference(c, table, key),
                output / "payload" / table / "all/part-000000.parquet",
                schema,
            )
        else:
            if table != "dwd_tradability_state":
                missing = c.execute(
                    f"SELECT count(*) FROM (SELECT * FROM h_{table} EXCEPT SELECT * FROM b_{table})"
                ).fetchone()[0]
                if missing:
                    raise ValueError("FULL_DAILY_REFERENCE_SUBSET_CHANGED:" + table)
            for path in base_paths:
                target = output / path.relative_to(base)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        if table not in counts:
            counts[table] = sum(
                pq.ParquetFile(p).metadata.num_rows
                for p in (output / "payload" / table).rglob("*.parquet")
            )
    c.close()
    provenance = {
        "release_code_revision": revision,
        "hist_manifest_sha256": HIST_MANIFEST,
        "hist_revision": HIST_REVISION,
        "base251_manifest_sha256": BASE251_MANIFEST,
        "base251_revision": "1923c874b43d47e69190a6dc9546e6fd8f3d1ca3",
    }
    save(output / "PROVENANCE.json", provenance)
    save(
        output / "MATERIALIZATION.json",
        {
            "status": "MATERIALIZED_PENDING_AUDIT",
            "table_rows": counts,
            "source_bar_bytes_preserved": True,
            "modern_clock_policy": "PRESERVE_PARENT_COORDINATES",
            "native_factor_policy": "SOURCE_EVENTS_WITH_RECOMPUTED_NEXT_EVENT_END",
            "reference_policy": "BASE_FULL_INTERVALS_PLUS_ADDITIONAL_HIST_OBSERVATIONS",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        },
    )
    write_manifest(output, provenance)
    return dict(verify_manifest(output))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hist", required=True, type=Path)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.hist, args.base, args.output)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
