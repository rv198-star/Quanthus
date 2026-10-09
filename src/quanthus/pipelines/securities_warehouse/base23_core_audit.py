"""Offline replay of a bounded three-table September intermediate, not a mirror."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, cast

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_sources import _tree
from .base23_core import (
    BAR,
    CONTRACT_ID,
    STATUS,
    TABLES,
    calendar_union,
    project_calendar,
    project_revisions,
)
from .base23_sources import START, THROUGH


def audit_core_scope(root: Path, budget: Budget) -> dict[str, Any]:
    """Every file/hash, local raw replay, all global versions and dated parents.

    Historical paths are archival strings only. All schema, producer rules, raw,
    original manifests/READY and parent membership evidence live inside the scope.
    Even a passing scope never proves the four unavailable month-end core dates,
    original provider PIT, full September domains or complete Base 2.3.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("business_window") != [str(START), str(THROUGH)]
        or body.get("complete_Base23") is not False
        or body.get("READY") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("source_qualification") != "RETAINED_BYTE_VERIFIED_GOVERNANCE_NOT_PROVIDER_PIT"
        or body.get("input_digest") != digest(body.get("input_closure"))
        or body.get("projection_module_sha256")
        != file_sha(Path(__file__).with_name("base23_core.py"))
        or set(body.get("outputs", {})) != {"dwd_trading_calendar", *TABLES.values()}
    ):
        raise BoundedError("SEPTEMBER_LOCAL_SCOPE_OR_EVALUATOR_CHANGED")
    if set(_tree(root)) != {"SCOPE.json", *body["physical_members"]}:
        raise BoundedError("SEPTEMBER_EXACT_PHYSICAL_FILE_SET_CHANGED")
    declared_payload = {item["path"] for items in body["outputs"].values() for item in items}
    if {p for p in _tree(root) if p.startswith("payload/")} != declared_payload:
        raise BoundedError("SEPTEMBER_UNDECLARED_PHYSICAL_PARTITION_FILE")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if file_sha(p) != item["sha256"] or p.stat().st_size != item["size_bytes"]:
            raise BoundedError("SEPTEMBER_PHYSICAL_MEMBER_CHANGED")
        budget.check()
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)

    def read(rel: str, *, bound: int = 8192) -> list[dict[str, Any]]:
        p = _physical(root, rel, independent=True)
        if pq.ParquetFile(p).metadata.num_rows > bound:
            raise BoundedError("SEPTEMBER_REGISTERED_ROW_BOUND_EXCEEDED")
        budget.check()
        return cast(list[dict[str, Any]], pq.ParquetFile(p).read().to_pylist())

    original = json.loads(
        _physical(root, body["history_materialization"], independent=True).read_bytes()
    )
    if file_sha(root / body["history_materialization"]) != body["history_materialization_sha256"]:
        raise BoundedError("SEPTEMBER_ORIGINAL_HISTORY_METADATA_CHANGED")
    parent_rows: dict[str, list[dict[str, Any]]] = {}
    for name, item in body["parents"].items():
        matches = [
            f
            for f in original["layout"][name]["files"]
            if f["path"] == item["archive_path"] and f["sha256"] == item["sha256"]
        ]
        if len(matches) != 1 or file_sha(root / item["path"]) != item["sha256"]:
            raise BoundedError("SEPTEMBER_HISTORY_PARENT_MEMBERSHIP_CHANGED")
        if not pq.ParquetFile(root / item["path"]).schema_arrow.equals(
            ss[name], check_metadata=False
        ):
            raise BoundedError("SEPTEMBER_PARENT_SCHEMA_CHANGED")
        parent_rows[name] = read(item["path"])
    if set(parent_rows) != {"dwd_listing", "dwd_trading_venue"}:
        raise BoundedError("SEPTEMBER_EXACT_SHARED_PARENTS_REQUIRED")
    local_sources: dict[str, list[dict[str, Any]]] = {}
    admitted_sources = {
        (s["revision"], s["manifest_sha256"]): {m["family"]: m["sha256"] for m in s["members"]}
        for s in body["input_closure"]["sources"]
    }
    if len(admitted_sources) != len(body["sources"]):
        raise BoundedError("SEPTEMBER_EXACT_QUALIFIED_SOURCE_SET_CHANGED")
    for source in body["sources"]:
        manifest = _physical(root, source["manifest"], independent=True)
        ready = _physical(root, source["ready"], independent=True)
        if file_sha(manifest) != source["manifest_sha256"] or ready.read_text().strip() != file_sha(
            manifest
        ):
            raise BoundedError("SEPTEMBER_ORIGINAL_MANIFEST_READY_CHANGED")
        value = json.loads(manifest.read_bytes())
        admitted = admitted_sources.get((source["revision"], file_sha(manifest)))
        if value["release_id"] != source["revision"] or admitted is None:
            raise BoundedError("SEPTEMBER_SOURCE_REVISION_CHANGED")
        for member in source["members"]:
            matches = [
                f
                for f in value["artifacts"]
                if f["name"] == member["family"] and f["sha256"] == member["sha256"]
            ]
            if (
                len(matches) != 1
                or file_sha(root / member["path"]) != member["sha256"]
                or admitted.get(member["family"]) != member["sha256"]
            ):
                raise BoundedError("SEPTEMBER_RAW_ORIGINAL_ARTIFACT_MEMBERSHIP_CHANGED")
            local_sources.setdefault(member["family"], []).append(
                {**member, "revision": value["release_id"], "market_date": value["market_date"]}
            )
    if set(local_sources) != {BAR, STATUS, "trading_calendar"}:
        raise BoundedError("SEPTEMBER_EXACT_LOCAL_SOURCE_FAMILIES_REQUIRED")
    fragments = []
    for member in local_sources["trading_calendar"]:
        selected: list[dict[str, Any]] = []
        p = _physical(root, member["path"], independent=True)
        for batch in pq.ParquetFile(p).iter_batches(batch_size=budget.limits.batch_rows):
            selected.extend(r for r in batch.to_pylist() if START <= r["trade_date"] <= THROUGH)
            budget.check()
        if len(selected) > 90:
            raise BoundedError("SEPTEMBER_CALENDAR_FRAGMENT_BOUND_CHANGED")
        fragments.append((member["sha256"], selected))
    union, union_proof = calendar_union(fragments)
    # Canonical comparison retains actual date types across JSON transport.
    if digest(union) != digest(read(body["calendar_union"])) or digest(union_proof) != digest(
        body["calendar_union_proof"]
    ):
        raise BoundedError("SEPTEMBER_LOCAL_CALENDAR_RAW_REPLAY_CHANGED")
    calendar, projection = project_calendar(
        union,
        parent_rows["dwd_trading_venue"],
        observed_at=body["observed_at"],
        source_sha256=file_sha(root / body["calendar_union"]),
        revision=body["calendar_revision"],
    )
    if digest(projection) != digest(body["calendar_projection_proof"]):
        raise BoundedError("SEPTEMBER_CALENDAR_PROJECTION_CHANGED")
    actual_calendar = read(body["outputs"]["dwd_trading_calendar"][0]["path"])
    if digest(actual_calendar) != digest(calendar):
        raise BoundedError("SEPTEMBER_CALENDAR_TYPED_REPLAY_CHANGED")
    grouped: dict[str, dict[str, list[Any]]] = {}
    for family in [BAR, STATUS]:
        for member in local_sources[family]:
            grouped.setdefault(member["market_date"], {BAR: [], STATUS: []})[family].append(
                (member["sha256"], member["revision"], read(member["path"]))
            )
    if set(grouped) != {d["date"] for d in body["days"]} or len(body["days"]) != len(grouped):
        raise BoundedError("SEPTEMBER_EXACT_PHYSICAL_DAY_SET_CHANGED")
    expected_paths = {
        "dwd_trading_calendar": [body["outputs"]["dwd_trading_calendar"][0]["path"]],
        **{t: [] for t in TABLES.values()},
    }
    for day in body["days"]:
        if set(day["outputs"]) != set(TABLES.values()):
            raise BoundedError("SEPTEMBER_DAY_TABLE_OUTPUT_SET_CHANGED")
        out, details = project_revisions(
            grouped[day["date"]],
            day=date.fromisoformat(day["date"]),
            listings=parent_rows["dwd_listing"],
            calendars=calendar,
            observed_at=body["observed_at"],
            input_digest=body["input_digest"],
            schemas=ss,
        )
        evidence = json.loads(_physical(root, day["proof_path"], independent=True).read_bytes())
        if digest(details) != digest(evidence):
            raise BoundedError("SEPTEMBER_EXACT_SAFE_DAY_PROOF_CHANGED")
        for table, item in day["outputs"].items():
            if table not in TABLES.values() or digest(read(item["path"])) != digest(out[table]):
                raise BoundedError("SEPTEMBER_TYPED_DAY_RAW_REPLAY_CHANGED")
            expected_paths[table].append(item["path"])
    summaries = {}
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for table, items in body["outputs"].items():
            if [i["path"] for i in items] != expected_paths[table] or len(
                set(expected_paths[table])
            ) != len(items):
                raise BoundedError("SEPTEMBER_EXACT_TABLE_PARTITION_FILE_SET_CHANGED")
            paths = []
            for item in items:
                path = _physical(root, item["path"], independent=True)
                expected_partition = partition(
                    {"interval_size": "1d", "observation_start": START, "trade_date": START},
                    contract["tables"][table],
                )
                if item["partition"] != expected_partition or str(Path(item["path"]).parent) != str(
                    Path("payload") / table / expected_partition
                ):
                    raise BoundedError("SEPTEMBER_EXACT_PHYSICAL_PARTITION_CHANGED")
                rows = read(item["path"])
                if any(partition(r, contract["tables"][table]) != item["partition"] for r in rows):
                    raise BoundedError("SEPTEMBER_PHYSICAL_PARTITION_CHANGED")
                summary = semantic_scan([path], ss[table], check=budget.check)
                if summary != item["semantic_scan"]:
                    raise BoundedError("SEPTEMBER_RECOMPUTED_TYPED_SUMMARY_CHANGED")
                paths.append(path)
            scan.add(table, paths)
            scan.keys(
                table,
                contract["tables"][table]["business_key"],
                contract["tables"][table]["version_key"],
            )
            required = " OR ".join(f.name + " IS NULL" for f in ss[table] if not f.nullable)
            scan.reject(
                f"SELECT 1 FROM {table} WHERE {required} LIMIT 1", "SEPTEMBER_REQUIRED_FIELD_NULL"
            )
            coordinate = (
                "trade_date"
                if table == "dwd_trading_calendar"
                else "CAST(timezone('Asia/Shanghai',observation_start) AS DATE)"
            )
            scan.reject(
                f"SELECT 1 FROM {table} WHERE {coordinate}<DATE '{START}' "
                f"OR {coordinate}>DATE '{THROUGH}' OR knowledge_to<=knowledge_from "
                "OR observation_end<=observation_start LIMIT 1",
                "SEPTEMBER_BUSINESS_OR_KNOWLEDGE_INTERVAL_INVALID",
            )
            summaries[table] = {
                "rows": sum(i["semantic_scan"]["rows"] for i in items),
                "partition_summaries_sha256": digest(items),
            }
        for name, item in body["parents"].items():
            scan.add(name, [root / item["path"]])
        for table in TABLES.values():
            scan.reject(
                f"""SELECT 1 FROM {table} r LEFT JOIN dwd_listing l ON r.listing_id=l.listing_id
                LEFT JOIN dwd_trading_calendar c ON r.calendar_session_id=c.calendar_session_id
                WHERE l.listing_id IS NULL OR c.calendar_session_id IS NULL
                OR l.venue_id<>c.venue_id
                OR c.trade_date<>CAST(timezone('Asia/Shanghai',r.observation_start) AS DATE)
                OR l.effective_from>r.observation_start
                OR (l.effective_to IS NOT NULL AND r.observation_start>=l.effective_to)
                OR l.knowledge_from>r.knowledge_from
                OR (l.knowledge_to IS NOT NULL AND r.knowledge_from>=l.knowledge_to)
                OR c.knowledge_from>r.knowledge_from
                OR (c.knowledge_to IS NOT NULL AND r.knowledge_from>=c.knowledge_to) LIMIT 1""",
                "SEPTEMBER_CALENDAR_DATE_VENUE_KNOWLEDGE_REFERENCE_FAILED",
            )
    result = {
        "status": "FULL_LOCAL_THREE_TABLE_REPLAY_PASS_NOT_COMPLETE_BASE23",
        "scope_identity": body["identity"],
        "tables": summaries,
        "calendar_missing_month_keys": union_proof["missing_month_keys"],
        "original_provider_PIT_qualified": False,
        "complete_Base23": False,
        "READY": False,
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result
