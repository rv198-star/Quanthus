"""Bounded image-local replay of September valuation and index observations."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_outputs import instant
from .base22_sources import _tree
from .base23_measures import CONTRACT_ID, QUALIFICATION, TABLES, project_revisions
from .base23_sources import START, THROUGH

FAMILIES = dict(zip(TABLES, ("valuation_daily_delta", "index_daily_delta"), strict=True))
PARENTS = {
    "dwd_listing",
    "dwd_security",
    "dwd_listed_company",
    "dwd_trading_venue",
    "dwd_market_index",
    "dwd_trading_calendar",
}


def audit_measures_scope(root: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    closure = body.get("input_closure", {})
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("input_digest") != digest(closure)
        or closure.get("purpose") != CONTRACT_ID
        or closure.get("qualification") != QUALIFICATION
        or body.get("business_window") != [str(START), str(THROUGH)]
        or body.get("projection_module_sha256")
        != file_sha(Path(__file__).with_name("base23_measures.py"))
        or body.get("READY") is not False
        or body.get("complete_Base23") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("SEPTEMBER_MEASURES_LOCAL_CONTRACT_UNQUALIFIED")
    physical = body["physical_members"]
    if _tree(root) != {"SCOPE.json", *physical}:
        raise BoundedError("SEPTEMBER_MEASURES_EXACT_PHYSICAL_FILES_CHANGED")
    for rel, item in physical.items():
        p = _physical(root, rel, independent=True)
        if p.stat().st_size != item["size_bytes"] or file_sha(p) != item["sha256"]:
            raise BoundedError("SEPTEMBER_MEASURES_PHYSICAL_HASH_CHANGED")
        budget.check()
    c, model, ext = load_contract(root / "contract")
    ss = schemas(model, ext)

    def read(rel: str, bound: int = 32768) -> list[dict[str, Any]]:
        p = _physical(root, rel, independent=True)
        pf = pq.ParquetFile(p)
        if pf.metadata.num_rows > bound:
            raise BoundedError("SEPTEMBER_MEASURES_REGISTERED_ROW_BOUND_CHANGED")
        budget.check()
        return pf.read().to_pylist()  # type: ignore[no-any-return]

    history_path = _physical(root, body["history_materialization"], independent=True)
    if file_sha(history_path) != closure["history_materialization_sha256"]:
        raise BoundedError("SEPTEMBER_MEASURES_HISTORY_IDENTITY_CHANGED")
    history = json.loads(history_path.read_bytes())
    cal_path = _physical(root, body["calendar_scope"], independent=True)
    if file_sha(cal_path) != closure["calendar_scope_sha256"]:
        raise BoundedError("SEPTEMBER_MEASURES_CALENDAR_SCOPE_CHANGED")
    cal = json.loads(cal_path.read_bytes())
    if not cal["calendar_projection_proof"]["full_month_coverage"]:
        raise BoundedError("SEPTEMBER_MEASURES_COMPLETE_CALENDAR_REQUIRED")
    if set(body["parents"]) != PARENTS:
        raise BoundedError("SEPTEMBER_MEASURES_EXACT_PARENT_SET_CHANGED")
    parents = {}
    for table, item in body["parents"].items():
        original = (
            cal["physical_members"][item["archive_path"]]
            if table == "dwd_trading_calendar"
            else next(
                (f for f in history["layout"][table]["files"] if f["path"] == item["archive_path"]),
                {},
            )
        )
        p = _physical(root, item["path"], independent=True)
        if original.get("sha256") != file_sha(p) or not pq.ParquetFile(p).schema_arrow.equals(
            ss[table], check_metadata=False
        ):
            raise BoundedError("SEPTEMBER_MEASURES_PARENT_MEMBERSHIP_OR_SCHEMA_CHANGED")
        parents[table] = read(item["path"])
    venues = {r["venue_id"] for r in parents["dwd_trading_venue"]}
    calendar = parents["dwd_trading_calendar"]
    expected_calendar = {(v, START + timedelta(days=n), "day") for v in venues for n in range(30)}
    if (
        len(venues) != 3
        or len(calendar) != 90
        or {(r["venue_id"], r["trade_date"], r["session_type"]) for r in calendar}
        != expected_calendar
        or digest(calendar) != cal["calendar_projection_proof"]["output_rows_sha256"]
    ):
        raise BoundedError("SEPTEMBER_MEASURES_COMPLETE_CALENDAR_REQUIRED")
    grouped: dict[str, dict[str, list[Any]]] = {}
    ids: dict[str, set[str]] = {t: set() for t in TABLES}
    registry: dict[str, list[tuple[str, str, str]]] = {t: [] for t in TABLES}
    selected = closure["sources"]
    if len(selected) != len(body["sources"]) or not selected:
        raise BoundedError("SEPTEMBER_MEASURES_EXACT_SOURCES_CHANGED")
    for source, admitted in zip(body["sources"], selected, strict=True):
        mp = _physical(root, source["manifest"], independent=True)
        ready = _physical(root, source["ready"], independent=True)
        manifest = json.loads(mp.read_bytes())
        if (
            file_sha(mp) != admitted["manifest_sha256"]
            or ready.read_text().strip() != file_sha(mp)
            or manifest["release_id"] != admitted["revision"]
            or manifest["market_date"] != admitted["market_date"]
            or {m["table"] for m in source["members"]} != set(TABLES)
        ):
            raise BoundedError("SEPTEMBER_MEASURES_ORIGINAL_MANIFEST_BINDING_CHANGED")
        for member in source["members"]:
            table = member["table"]
            entry = [f for f in manifest["artifacts"] if f["name"] == FAMILIES[table]]
            approved = [f for f in admitted["members"] if f["table"] == table]
            p = _physical(root, member["path"], independent=True)
            if (
                len(entry) != 1
                or len(approved) != 1
                or entry[0]["sha256"] != file_sha(p)
                or approved[0]["sha256"] != file_sha(p)
            ):
                raise BoundedError("SEPTEMBER_MEASURES_TABLE_SOURCE_MEMBERSHIP_CHANGED")
            ids[table].add(file_sha(p))
            registry[table].append(
                ("base23_september/" + FAMILIES[table], admitted["revision"], file_sha(p))
            )
            grouped.setdefault(admitted["market_date"], {t: [] for t in TABLES})[table].append(
                (file_sha(p), admitted["revision"], read(member["path"], 8192))
            )
    if (
        set(closure.get("source_ids_by_table", {})) != set(TABLES)
        or any(sorted(ids[t]) != closure["source_ids_by_table"][t] for t in TABLES)
        or len(body["days"]) != len(grouped)
        or {d["date"] for d in body["days"]} != set(grouped)
    ):
        raise BoundedError("SEPTEMBER_MEASURES_OFFLINE_SOURCE_PLAN_CHANGED")
    payload = {d["outputs"][t]["path"] for d in body["days"] for t in TABLES}
    if {p for p in physical if p.startswith("payload/")} != payload:
        raise BoundedError("SEPTEMBER_MEASURES_PHYSICAL_PARTITIONS_CHANGED")
    outputs: dict[str, list[Path]] = {t: [] for t in TABLES}
    counts = {t: 0 for t in TABLES}
    for day in body["days"]:
        rows, proof = project_revisions(
            grouped[day["date"]],
            day=date.fromisoformat(day["date"]),
            listings=parents["dwd_listing"],
            calendars=parents["dwd_trading_calendar"],
            indexes=parents["dwd_market_index"],
            observed_at=instant(body["observed_at"]),
            ss=ss,
        )
        if digest(proof) != digest(json.loads((root / day["proof_path"]).read_bytes())):
            raise BoundedError("SEPTEMBER_MEASURES_LOCAL_REPLAY_PROOF_CHANGED")
        for table in TABLES:
            item = day["outputs"][table]
            p = _physical(root, item["path"], independent=True)
            if (
                not pq.ParquetFile(p).schema_arrow.equals(ss[table])
                or digest(read(item["path"])) != digest(rows[table])
                or any(partition(r, c["tables"][table]) != item["partition"] for r in rows[table])
                or Path(item["path"]).parent != Path("payload") / table / item["partition"]
                or semantic_scan([p], ss[table], check=budget.check) != item["semantic_scan"]
            ):
                raise BoundedError("SEPTEMBER_MEASURES_TYPED_REPLAY_OR_PARTITION_CHANGED")
            outputs[table].append(p)
            counts[table] += len(rows[table])
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for table, item in body["parents"].items():
            scan.add(table, [root / item["path"]])
            scan.keys(table, c["tables"][table]["business_key"], c["tables"][table]["version_key"])
        for table, paths in outputs.items():
            scan.add(table, paths)
            scan.keys(table, c["tables"][table]["business_key"], c["tables"][table]["version_key"])
            scan.lineage(table, registry[table])
            required = " OR ".join(f.name + " IS NULL" for f in ss[table] if not f.nullable)
            scan.reject(
                f"SELECT 1 FROM {table} WHERE {required} LIMIT 1", "SEPTEMBER_MEASURES_NULL"
            )
            scan.reject(
                f"SELECT 1 FROM {table} WHERE "
                "CAST(timezone('Asia/Shanghai',observation_start) AS DATE)"
                f" NOT BETWEEN DATE '{START}' AND DATE '{THROUGH}' "
                "OR observation_end<=observation_start"
                " OR knowledge_to<=knowledge_from LIMIT 1",
                "SEPTEMBER_MEASURES_TIME_INVALID",
            )
        scan.references(
            {"tables": [t for t in model["tables"] if t["name"] in scan.tables]},
            {"tables": {t: c["tables"][t] for t in scan.tables}},
        )
    result = {
        "status": "FULL_SEPTEMBER_MEASURES_LOCAL_REPLAY_PASS_NOT_COMPLETE_BASE23",
        "scope_identity": body["identity"],
        "rows": counts,
        "days": len(body["days"]),
        "complete_Base23": False,
        "READY": False,
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result
