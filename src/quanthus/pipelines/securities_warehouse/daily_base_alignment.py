"""Replay authoritative existing Daily sources against a sealed Base; no acquisition.

The indexed date entry and its exact source manifests choose authority. Complete
standard deliveries include closed days and feed the next Base unchanged.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .base22_bounded import Budget, Limits
from .base22_sources import disjoint_output, physical_path
from .base_build_inputs import file_sha, identity
from .daily_base24 import build as build_projection
from .daily_base24 import verify as verify_projection
from .daily_core_domain import (
    advance_daily_current,
    build_daily_domain_increment,
    verify_daily_domain_increment,
)
from .daily_core_ods_catalog import verify_daily_ods_catalog
from .daily_reference_views import reference_relation
from .native_base_standard_reader import governance_config, read_native_parent_references
from .standard_daily import inspect as inspect_delivery
from .standard_daily import publish as publish_delivery
from .standard_daily import verify as verify_delivery


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("DAILY_ALIGNMENT_OBJECT_REQUIRED")
    return value


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + str(os.getpid()))
    with temporary.open("x") as f:
        json.dump(value, f, sort_keys=True, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def lanes(runtime: Path, entry: dict[str, Any]) -> dict[str, Any]:
    core = physical_path(Path(entry["core"]))
    if file_sha(core / "manifest.json") != entry["core_manifest_sha256"]:
        raise ValueError("DAILY_ALIGNMENT_INDEXED_CORE_CHANGED")
    m = load(core / "manifest.json")
    catalog = core.parent / "catalog"
    verified = verify_daily_ods_catalog(catalog)
    if verified.root != catalog or file_sha(catalog / "catalog.json") != m["catalog_sha256"]:
        raise ValueError("DAILY_ALIGNMENT_CATALOG_CHANGED")
    releases = runtime / "collector-home/shadow/releases"
    chosen = {}
    for row in load(catalog / "catalog.json")["lanes"]:
        lane = row["lane"]
        source = physical_path(releases / row["release_path"])
        if (
            not source.is_relative_to(releases)
            or file_sha(source / "release.json") != row["release_manifest_sha256"]
            or row["release_manifest_sha256"] != m["selected_releases"][lane]["manifest_sha256"]
        ):
            raise ValueError("DAILY_ALIGNMENT_SOURCE_CHANGED")
        chosen[lane] = str(source)
    if set(chosen) != {"market_daily_core", "calendar_daily_events"}:
        raise ValueError("DAILY_ALIGNMENT_EXACT_LANES_REQUIRED")
    return {
        "catalog": str(catalog),
        "release_root": str(releases),
        "core": str(core),
        "sources": chosen,
        "source_hashes": {k: file_sha(Path(v) / "release.json") for k, v in chosen.items()},
    }


def prepare(base: Path, runtime: Path, through: date | None, revision: str) -> dict[str, Any]:
    base, runtime = physical_path(base), physical_path(runtime)
    cfg = governance_config(
        base, build_id="daily-alignment-" + file_sha(base / "manifest.json")[7:19]
    )
    read_native_parent_references(base, cfg)
    index_path = runtime / "DATES.json"
    index = load(index_path)
    dates = index["dates"]
    through = through or date.fromisoformat(max(dates))
    cutoff = date.fromisoformat(cfg["through_date"])
    through = max(through, cutoff)
    entries = []
    day = cutoff + timedelta(days=1)
    while day <= through:
        ds = day.isoformat()
        if ds not in dates:
            raise ValueError("DAILY_ALIGNMENT_DATE_MISSING:" + ds)
        old = dates[ds]
        if old["status"] == "QUALIFIED_1D_NATIVE5M":
            row = {"market_date": ds, "day_kind": "market", **lanes(runtime, old)}
            native = physical_path(Path(old["native_source"]))
            product = physical_path(Path(old["product"]))
            if file_sha(product / "manifest.json") != old["product_manifest_sha256"]:
                raise ValueError("DAILY_ALIGNMENT_INDEXED_PRODUCT_CHANGED")
            row.update(
                native=str(native),
                original_product=str(product),
                native_sha256=file_sha(native / "manifest.json"),
            )
        elif old["status"] == "AUTHORITATIVE_CLOSED_EVENT_SOURCE_READY":
            events = physical_path(Path(old["event_source"]))
            if file_sha(events / "release.json") != old["event_manifest_sha256"]:
                raise ValueError("DAILY_ALIGNMENT_CLOSED_SOURCE_CHANGED")
            candidates = [
                (d, e)
                for d, e in dates.items()
                if d < ds and e.get("status") == "QUALIFIED_1D_NATIVE5M"
            ]
            if not candidates:
                raise ValueError("DAILY_ALIGNMENT_CLOSED_REFERENCE_MISSING:" + ds)
            reference = lanes(runtime, max(candidates, key=lambda x: x[0])[1])
            sources = {
                "calendar_reference": reference["sources"]["market_daily_core"],
                "calendar_daily_events": str(events),
            }
            row = {
                "market_date": ds,
                "day_kind": "closed",
                "sources": sources,
                "source_hashes": {
                    k: file_sha(Path(v) / "release.json") for k, v in sources.items()
                },
            }
            observations = sorted(
                {
                    p
                    for p in runtime.glob(ds + "-*/calendar/calendar.json")
                    if file_sha(p) == old["calendar_sha256"]
                }
            )
            if not observations:
                raise ValueError("DAILY_ALIGNMENT_INDEXED_CALENDAR_MISSING:" + ds)
            from .observed_daily_calendar import validate as validate_calendar

            validate_calendar(observations[0].parent, day=ds)
            row["calendar_observation"] = str(observations[0])
            row["calendar_observation_sha256"] = old["calendar_sha256"]
        else:
            raise ValueError("DAILY_ALIGNMENT_UNQUALIFIED_DATE:" + ds + ":" + old["status"])
        entries.append(row)
        day += timedelta(days=1)
    request = {
        "base_root": str(base),
        "source_runtime": str(runtime),
        "source_index_sha256": file_sha(index_path),
        "base": cfg["layers"]["dwd"],
        "through": str(through),
        "code_revision": revision,
        "governance": cfg,
        "entries": entries,
    }
    return {"kind": "p10-daily-base-alignment-plan-v1", **request, "identity": identity(request)}


def compare_bars(old: Path, new: Path) -> dict[str, Any]:
    import duckdb
    import pyarrow.parquet as pq

    results = {}
    lineage = {
        "record_source",
        "source_record_id",
        "source_revision",
        "source_artifact_sha256",
        "knowledge_from",
        "knowledge_to",
    }
    with duckdb.connect() as c:
        c.execute("SET threads=1")
        for interval in ("1d", "5m"):
            before, after = (
                old / ("payload/" + interval + ".parquet"),
                new / ("payload/" + interval + ".parquet"),
            )
            cols = [x for x in pq.ParquetFile(before).schema_arrow.names if x not in lineage]
            names = ",".join('"' + x + '"' for x in cols)
            c.execute(
                "CREATE OR REPLACE VIEW old_rows AS SELECT "
                + names
                + " FROM "
                + reference_relation([before])
            )
            c.execute(
                "CREATE OR REPLACE VIEW new_rows AS SELECT "
                + names
                + " FROM "
                + reference_relation([after])
            )
            delta = c.execute("""SELECT count(*) FROM
                ((SELECT * FROM old_rows EXCEPT ALL SELECT * FROM new_rows)
                 UNION ALL (SELECT * FROM new_rows
                    EXCEPT ALL SELECT * FROM old_rows))""").fetchone()[0]
            if delta:
                raise ValueError(
                    "DAILY_ALIGNMENT_BUSINESS_VALUES_CHANGED:" + interval + ":" + str(delta)
                )
            results[interval] = {
                "business_rows": pq.ParquetFile(after).metadata.num_rows,
                "differences": 0,
            }
    return results


def verify_result(result: dict[str, Any]) -> None:
    delivery = Path(result["delivery"])
    if file_sha(delivery / "manifest.json") != result["delivery_manifest_sha256"]:
        raise ValueError("DAILY_ALIGNMENT_DELIVERY_CHANGED")
    body = inspect_delivery(delivery)
    if body["base"]["manifest_sha256"] != result["base_manifest_sha256"]:
        raise ValueError("DAILY_ALIGNMENT_DELIVERY_BASE_CHANGED")
    if result["day_kind"] == "market":
        core = Path(result["core"])
        projection = Path(result["product"])
        if (
            file_sha(core / "manifest.json") != result["core_manifest_sha256"]
            or file_sha(projection / "manifest.json") != result["product_manifest_sha256"]
        ):
            raise ValueError("DAILY_ALIGNMENT_RESULT_CHANGED")
        verify_daily_domain_increment(core)
        verify_projection(projection)


def execute(plan: dict[str, Any], output: Path) -> dict[str, Any]:
    base, runtime = Path(plan["base_root"]), Path(plan["source_runtime"])
    output = disjoint_output(output, [base, runtime])
    output.mkdir(exist_ok=True, parents=True)
    with (output / "ALIGN.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        request = output / "PLAN.json"
        if request.exists() and load(request)["identity"] != plan["identity"]:
            raise ValueError("DAILY_ALIGNMENT_EXISTING_IDENTITY_CHANGED")
        if not request.exists():
            save(request, plan)
        cfg = output / "governance.json"
        if not cfg.exists():
            save(cfg, plan["governance"])
        elif load(cfg) != plan["governance"]:
            raise ValueError("DAILY_ALIGNMENT_GOVERNANCE_CHANGED")
        parent = read_native_parent_references(base, plan["governance"])
        pointer = output / "core/CURRENT_DAILY_CORE.json"
        records = []
        for row in plan["entries"]:
            day = row["market_date"]
            done = output / "receipts" / (day + ".json")
            if done.exists():
                result = load(done)
                if result["request_identity"] != plan["identity"]:
                    raise ValueError("DAILY_ALIGNMENT_DATE_IDENTITY_CHANGED")
                verify_result(result)
                records.append(result)
                continue
            sources = {k: Path(v) for k, v in row["sources"].items()}
            for role, path in sources.items():
                if file_sha(path / "release.json") != row["source_hashes"][role]:
                    raise ValueError("DAILY_ALIGNMENT_FROZEN_SOURCE_CHANGED")
            result = {
                "market_date": day,
                "day_kind": row["day_kind"],
                "request_identity": plan["identity"],
                "base": plan["base"],
                "source_manifests": row["source_hashes"],
            }
            projection = None
            if row["day_kind"] == "market":
                native = Path(row["native"])
                if file_sha(native / "manifest.json") != row["native_sha256"]:
                    raise ValueError("DAILY_ALIGNMENT_NATIVE_CHANGED")
                core = build_daily_domain_increment(
                    catalog_root=Path(row["catalog"]),
                    release_root=Path(row["release_root"]),
                    base_root=base,
                    release_config=cfg,
                    output=output / "core/packages" / day / "rebased",
                    code_revision=plan["code_revision"],
                    prior_pointer=pointer if pointer.exists() else None,
                )
                verify_daily_domain_increment(core.root)
                advance_daily_current(package=core.root, pointer=pointer)
                projection = output / "projections" / day
                build_projection(
                    core=core.root,
                    native5m=native,
                    config=cfg,
                    output=projection,
                    code_revision=plan["code_revision"],
                )
                result["bar_equivalence"] = compare_bars(Path(row["original_product"]), projection)
                result.update(
                    core=str(core.root),
                    core_manifest_sha256=core.manifest_sha256,
                    product=str(projection),
                    product_manifest_sha256=file_sha(projection / "manifest.json"),
                    native_source=str(native),
                    native_manifest_sha256=row["native_sha256"],
                )
            scratch = output / "scratch" / day
            limits = Limits(
                allocated_bytes=12 * 1024**3, free_floor_bytes=16 * 1024**3, wall_seconds=1800
            )
            with Budget(scratch, [base, runtime], limits) as budget:
                delivery = output / "deliveries" / day
                body = publish_delivery(
                    projection=projection,
                    sources=sources,
                    config=cfg,
                    base_root=base,
                    output=delivery,
                    code_revision=plan["code_revision"],
                    budget=budget,
                    calendar_observation=Path(row["calendar_observation"])
                    if row.get("calendar_observation")
                    else None,
                )
                verify_delivery(
                    delivery, parent=parent, budget=budget, output=budget.root / "acceptance-replay"
                )
            result.update(
                delivery=str(delivery),
                delivery_manifest_sha256=file_sha(delivery / "manifest.json"),
                delivery_identity=body["request_identity"],
                base_manifest_sha256=plan["base"]["manifest_sha256"],
                status="VERIFIED",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            save(done, result)
            records.append(result)
            print(
                json.dumps(
                    {
                        "day_verified": day,
                        "kind": row["day_kind"],
                        "completed": len(records),
                        "total": len(plan["entries"]),
                    }
                ),
                flush=True,
            )
        result = {
            "kind": "p10-daily-base-alignment-receipt-v1",
            "status": "VERIFIED",
            "base": plan["base"],
            "through": plan["through"],
            "code_revision": plan["code_revision"],
            "request_identity": plan["identity"],
            "days": records,
            "consumer_admission": "NOT_GRANTED",
            "execution_authorized": False,
        }
        save(output / "VERIFIED.json", result)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "build"))
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--source-runtime", type=Path, required=True)
    parser.add_argument("--through", type=date.fromisoformat)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[4]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo):
        raise ValueError("DAILY_ALIGNMENT_PINNED_CLEAN_CODE_REQUIRED")
    plan = prepare(args.base, args.source_runtime, args.through, revision)
    if args.action == "plan":
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        result = execute(plan, args.output)
        print(
            json.dumps(
                {"status": result["status"], "days": len(result["days"]), "base": result["base"]}
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
