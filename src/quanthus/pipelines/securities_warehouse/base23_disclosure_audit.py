"""Full bounded replay of image-local September CNINFO captures and holds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_outputs import instant
from .base22_sources import _tree
from .base23_disclosure import (
    CONTRACT_ID,
    MAX_CAPTURES,
    QUALIFICATION,
    TABLES,
    project_documents,
    select_documents,
)
from .base23_sources import THROUGH

PARENTS = {"dwd_listing", "dwd_security", "dwd_listed_company", "dwd_trading_venue"}


def audit_disclosure_scope(root: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    plan = body.get("input_closure", {})
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("input_digest") != digest(plan)
        or plan.get("purpose") != CONTRACT_ID
        or plan.get("qualification") != QUALIFICATION
        or body.get("projection_module_sha256")
        != file_sha(Path(__file__).with_name("base23_disclosure.py"))
        or body.get("business_through") != str(THROUGH)
        or body.get("complete_Base23") is not False
        or body.get("READY") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("parents", {})) != PARENTS
        or set(body.get("outputs", {})) != set(TABLES)
    ):
        raise BoundedError("SEPTEMBER_DISCLOSURE_LOCAL_CONTRACT_UNQUALIFIED")
    physical = body["physical_members"]
    if _tree(root) != {"SCOPE.json", *physical}:
        raise BoundedError("SEPTEMBER_DISCLOSURE_EXACT_PHYSICAL_FILES_CHANGED")
    for rel, info in physical.items():
        p = _physical(root, rel, independent=True)
        if p.stat().st_size != info["size_bytes"] or file_sha(p) != info["sha256"]:
            raise BoundedError("SEPTEMBER_DISCLOSURE_PHYSICAL_HASH_CHANGED")
        budget.check()
    c, model, ext = load_contract(root / "contract")
    ss = schemas(model, ext)
    history_path = _physical(root, body["history_materialization"], independent=True)
    if file_sha(history_path) != plan["history_materialization_sha256"]:
        raise BoundedError("SEPTEMBER_DISCLOSURE_HISTORY_IDENTITY_CHANGED")
    history = json.loads(history_path.read_bytes())
    parents = {}
    for table, item in body["parents"].items():
        p = _physical(root, item["path"], independent=True)
        admitted = [
            f
            for f in history["layout"][table]["files"]
            if f["path"] == item["archive_path"] and f["sha256"] == file_sha(p)
        ]
        pf = pq.ParquetFile(p)
        if (
            len(admitted) != 1
            or pf.metadata.num_rows > 32768
            or not pf.schema_arrow.equals(ss[table], check_metadata=False)
        ):
            raise BoundedError("SEPTEMBER_DISCLOSURE_PARENT_MEMBERSHIP_OR_SCHEMA_CHANGED")
        parents[table] = pf.read().to_pylist()
        budget.check()
    sources = []
    total = 0
    registry = []
    if not plan.get("sources") or len(plan["sources"]) != len(body["sources"]):
        raise BoundedError("SEPTEMBER_DISCLOSURE_EXACT_SOURCES_CHANGED")
    for local, original in zip(body["sources"], plan["sources"], strict=True):
        mp = _physical(root, local["manifest"], independent=True)
        rp = _physical(root, local["ready"], independent=True)
        manifest = json.loads(mp.read_bytes())
        if (
            file_sha(mp) != original["manifest_sha256"]
            or rp.read_text().strip() != file_sha(mp)
            or manifest["release_id"] != original["revision"]
            or manifest["market_date"] != original["market_date"]
        ):
            raise BoundedError("SEPTEMBER_DISCLOSURE_ORIGINAL_RELEASE_CHANGED")
        member = [f for f in manifest["artifacts"] if f["name"] == "announcements_delta"]
        p = _physical(root, local["raw"], independent=True)
        if (
            len(member) != 1
            or member[0]["sha256"] != file_sha(p)
            or file_sha(p) != original["sha256"]
        ):
            raise BoundedError("SEPTEMBER_DISCLOSURE_TABLE_SOURCE_CHANGED")
        source_rows = []
        for batch in pq.ParquetFile(p).iter_batches(batch_size=budget.limits.batch_rows):
            source_rows.extend(batch.to_pylist())
            total += batch.num_rows
            if total > MAX_CAPTURES:
                raise BoundedError("SEPTEMBER_DISCLOSURE_CAPTURE_OR_ENTITY_BOUND_EXCEEDED")
            budget.check()
        sources.append((original["sha256"], original["revision"], source_rows))
        registry.append(("announcement_archive", original["revision"], original["sha256"]))
    ids = sorted({s[0] for s in sources})
    if set(plan.get("source_ids_by_table", {})) != set(TABLES) or any(
        plan["source_ids_by_table"][t] != ids for t in TABLES
    ):
        raise BoundedError("SEPTEMBER_DISCLOSURE_OFFLINE_SOURCE_PLAN_CHANGED")
    selected, held, union = select_documents(sources, observed_at=instant(body["observed_at"]))
    rows, projection = project_documents(
        selected,
        listings=parents["dwd_listing"],
        securities=parents["dwd_security"],
        observed_at=instant(body["observed_at"]),
        ss=ss,
    )
    evidence = json.loads(_physical(root, body["proof_path"], independent=True).read_bytes())
    if digest(evidence) != digest(dict(union=union, raw_holds=held, projection=projection)):
        raise BoundedError("SEPTEMBER_DISCLOSURE_LOCAL_FULL_REPLAY_PROOF_CHANGED")
    declared = {i["path"] for items in body["outputs"].values() for i in items}
    if {p for p in physical if p.startswith("payload/")} != declared:
        raise BoundedError("SEPTEMBER_DISCLOSURE_PHYSICAL_PARTITIONS_CHANGED")
    outputs: dict[str, list[Path]] = {t: [] for t in TABLES}
    counts = {}
    for table in TABLES:
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in rows[table]:
            groups.setdefault(partition(r, c["tables"][table]), []).append(r)
        items = body["outputs"][table]
        if len(items) != len(groups) or {i["partition"] for i in items} != set(groups):
            raise BoundedError("SEPTEMBER_DISCLOSURE_PHYSICAL_PARTITIONS_CHANGED")
        for item in items:
            p = _physical(root, item["path"], independent=True)
            pf = pq.ParquetFile(p)
            if (
                pf.metadata.num_rows > MAX_CAPTURES
                or not pf.schema_arrow.equals(ss[table])
                or Path(item["path"]).parent != Path("payload") / table / item["partition"]
                or digest(pf.read().to_pylist()) != digest(groups[item["partition"]])
                or semantic_scan([p], ss[table], check=budget.check) != item["semantic_scan"]
            ):
                raise BoundedError("SEPTEMBER_DISCLOSURE_TYPED_REPLAY_CHANGED")
            outputs[table].append(p)
        counts[table] = len(rows[table])
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for table, item in body["parents"].items():
            scan.add(table, [root / item["path"]])
            scan.keys(table, c["tables"][table]["business_key"], c["tables"][table]["version_key"])
        for table, paths in outputs.items():
            scan.add(table, paths)
            scan.keys(table, c["tables"][table]["business_key"], c["tables"][table]["version_key"])
            scan.lineage(table, registry)
            required = " OR ".join(f.name + " IS NULL" for f in ss[table] if not f.nullable)
            scan.reject(
                f"SELECT 1 FROM {table} WHERE {required} LIMIT 1", "SEPTEMBER_DISCLOSURE_NULL"
            )
            scan.reject(
                f"SELECT 1 FROM {table} WHERE "
                f"CAST(timezone('Asia/Shanghai',effective_at) AS DATE)>DATE '{THROUGH}' "
                "OR knowledge_to<=knowledge_from LIMIT 1",
                "SEPTEMBER_DISCLOSURE_TIME_INVALID",
            )
        scoped_model = []
        for table in model["tables"]:
            if table["name"] not in scan.tables:
                continue
            for fk in table["foreign_keys"]:
                if fk["table"] not in scan.tables:
                    scan.reject(
                        f"SELECT 1 FROM {table['name']} WHERE {fk['column']} IS NOT NULL LIMIT 1",
                        "SEPTEMBER_DISCLOSURE_OUT_OF_SCOPE_REFERENCE_NON_NULL",
                    )
            scoped_model.append(
                dict(
                    table,
                    foreign_keys=[fk for fk in table["foreign_keys"] if fk["table"] in scan.tables],
                )
            )
        scan.references(
            {"tables": scoped_model},
            {"tables": {t: c["tables"][t] for t in scan.tables}},
        )
    result = dict(
        status="FULL_RETAINED_CNINFO_REPLAY_PASS_NOT_COMPLETE_BASE23",
        scope_identity=body["identity"],
        rows=counts,
        raw_capture_census=union["census"],
        held_raw_document_keys=len(held),
        held_identity_document_keys=len(projection["holds"]),
        complete_Base23=False,
        READY=False,
        consumer_admission="NOT_GRANTED",
    )
    result["identity"] = digest(result)
    return result
