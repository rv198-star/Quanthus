"""Apply only exact versions from an independently qualified cash scope.

This partition operation consumes an immutable history and an immutable patch;
it neither normalizes raw data again nor chooses between conflicting providers.
The caller pins their complete identities and owns physical materialization.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_cash_restatement import _scan_cash_global
from .base22_sources import _tree

TABLES = ("dwd_corporate_action", "dwd_corporate_action_term")
MAX_ROWS = 65536


def merge_cash_partition(
    table: str,
    part: str,
    rule: dict[str, Any],
    history: list[dict[str, Any]],
    before: list[dict[str, Any]],
    qualified: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Preserve every atom except the exact qualified old interval closures."""
    if table not in TABLES or any(len(rs) > MAX_ROWS for rs in (history, before, qualified)):
        raise BoundedError("CASH_HISTORY_TABLE_OR_PARTITION_BOUND_INVALID")
    if any(partition(r, rule) != part for rs in (history, before, qualified) for r in rs):
        raise BoundedError("CASH_HISTORY_PHYSICAL_PARTITION_MISMATCH")
    fields = rule["business_key"] + rule["version_key"]

    def key(row: dict[str, Any]) -> str:
        if any(row.get(f) is None for f in fields):
            raise BoundedError("CASH_HISTORY_NULL_KEY")
        return digest([row[f] for f in fields])

    def index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result = {key(r): r for r in rows}
        if len(result) != len(rows):
            raise BoundedError("CASH_HISTORY_DUPLICATE_VERSION")
        return result

    old, selected, patch = index(history), index(before), index(qualified)
    if not selected:
        if patch:
            raise BoundedError("CASH_HISTORY_UNBOUND_ADDITION")
        return list(history), {"old_rows": len(history), "closed_versions": 0, "new_versions": 0}
    entity = "corporate_action_id" if table == TABLES[0] else "corporate_action_term_id"
    by_entity = {}
    for row in selected.values():
        ident = row[entity]
        if ident in by_entity or row.get("knowledge_to") is not None:
            raise BoundedError("CASH_HISTORY_SELECTED_ENTITY_NOT_SINGLE_OPEN_VERSION")
        by_entity[ident] = row
    if any(k not in old or digest(old[k]) != digest(r) for k, r in selected.items()):
        raise BoundedError("CASH_HISTORY_EXACT_BEFORE_CHANGED_OR_MISSING")
    if {k for k, r in old.items() if r[entity] in by_entity} != set(selected):
        raise BoundedError("CASH_HISTORY_ADDITIONAL_EXISTING_SELECTED_VERSION")
    if len(patch) != 2 * len(selected) or not set(selected) <= set(patch):
        raise BoundedError("CASH_HISTORY_EXACT_PATCH_VERSION_SET_INVALID")
    added = {k: r for k, r in patch.items() if k not in selected}
    if (
        set(added) & set(old)
        or Counter(r[entity] for r in added.values()) != Counter(by_entity.keys())
    ):
        raise BoundedError("CASH_HISTORY_NEW_ENTITY_OR_VERSION_CONFLICT")
    incoming = {r[entity]: r for r in added.values()}
    for k, row in selected.items():
        new = incoming[row[entity]]
        clock = new.get("knowledge_from")
        if (
            not isinstance(clock, datetime)
            or clock.utcoffset() is None
            or not isinstance(row.get("knowledge_from"), datetime)
            or clock <= row["knowledge_from"]
            or new.get("knowledge_to") is not None
            or patch[k] != {**row, "knowledge_to": clock}
            or any(
                new.get(f) != row.get(f) for f in (entity, "corporate_action_id", "effective_at")
            )
        ):
            raise BoundedError("CASH_HISTORY_EXACT_CLOSURE_OR_NEW_CLOCK_INVALID")
    result = [dict(patch[key(r)]) if key(r) in selected else dict(r) for r in history]
    result.extend(dict(added[k]) for k in sorted(added))
    if len(result) > MAX_ROWS:
        raise BoundedError("CASH_HISTORY_OUTPUT_PARTITION_BOUND_INVALID")
    proof = {
        "old_rows": len(history),
        "closed_versions": len(selected),
        "new_versions": len(added),
        "before_census_sha256": digest(sorted(digest(r) for r in history)),
        "selected_before_census_sha256": digest(sorted(digest(r) for r in before)),
        "qualified_patch_census_sha256": digest(sorted(digest(r) for r in qualified)),
        "output_census_sha256": digest(sorted(digest(r) for r in result)),
        "provider_priority_granted": False,
        "new_observation_clock_inferred": False,
        "complete_Base22": False,
    }
    return result, proof


def audit_cash_history_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Reconstruct every allowed change using image-local typed evidence.

    Original raw admission was independently proved by the pinned predecessor
    receipts. This audit proves the merge, not another original raw replay.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("purpose") != "EXACT_QUALIFIED_CASH_HISTORY_MERGE_NOT_COMPLETE_BASE22"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("READY") is not False
        or body.get("complete_Base22") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("output_rows", {})) != set(TABLES)
    ):
        raise BoundedError("CASH_HISTORY_SCOPE_INVALID")
    if set(_tree(root)) != set(body["physical_members"]) | {"QUALIFICATION.json"}:
        raise BoundedError("CASH_HISTORY_PHYSICAL_SET_CHANGED")
    for relative, item in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if file_sha(path) != item["sha256"] or path.stat().st_size != item["size_bytes"]:
            raise BoundedError("CASH_HISTORY_MEMBER_CHANGED")
        budget.check()
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)

    def rows(item: dict[str, Any]) -> list[dict[str, Any]]:
        table = item["table"]
        path = _physical(root, item["path"], independent=True)
        physical = pq.ParquetFile(path)
        if (
            physical.metadata.num_rows > MAX_ROWS
            or physical.metadata.num_rows != item["rows"]
            or not physical.schema_arrow.equals(ss[table], check_metadata=False)
        ):
            raise BoundedError("CASH_HISTORY_SCHEMA_OR_ROW_BOUND_CHANGED")
        budget.check()
        return cast(list[dict[str, Any]], physical.read().to_pylist())

    counts: Counter[str] = Counter()
    closures: Counter[str] = Counter()
    compared = set()
    for output in body["outputs"]:
        table, part = output["table"], output["partition"]
        if table not in TABLES or (table, part) in compared:
            raise BoundedError("CASH_HISTORY_TABLE_PARTITION_DUPLICATE")
        compared.add((table, part))
        binding = body["partition_bindings"][table][part]
        expected, proof = merge_cash_partition(
            table, part, contract["tables"][table], rows(binding["history"]),
            rows(binding["before"]) if binding["before"] else [],
            rows(binding["patch"]) if binding["patch"] else [],
        )
        actual = rows(output)
        if Counter(digest(r) for r in actual) != Counter(digest(r) for r in expected):
            raise BoundedError("CASH_HISTORY_UNAPPROVED_OUTPUT_CHANGE")
        if proof != binding["proof"] or semantic_scan(
            [root / output["path"]], ss[table], check=budget.check
        ) != output["semantic_scan"]:
            raise BoundedError("CASH_HISTORY_RECOMPUTED_PROOF_CHANGED")
        if not binding["before"] and file_sha(root / output["path"]) != file_sha(
            root / binding["history"]["path"]
        ):
            raise BoundedError("CASH_HISTORY_HEALTHY_PHYSICAL_BYTES_CHANGED")
        counts[table] += len(actual)
        closures[table] += proof["closed_versions"]
    if (
        dict(counts) != body["output_rows"]
        or dict(closures) != body["selected_versions_each"]
        or compared != {
            (t, p) for t, parts in body["partition_bindings"].items() for p in parts
        }
    ):
        raise BoundedError("CASH_HISTORY_EXACT_PARTITION_OR_VERSION_CENSUS_CHANGED")
    _scan_cash_global(root, body, budget, contract, ss, rows)
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for table in TABLES:
            scan.add(table, [root / i["path"] for i in body["outputs"] if i["table"] == table])
            scan.lineage(table, [tuple(i) for i in body["admitted_lineage"][table]])
    result = {
        "status": "ALL_TYPED_CASH_MERGE_PARTITIONS_PASS_NOT_COMPLETE_BASE22",
        "scope_identity": body["identity"],
        "output_rows": dict(counts),
        "closed_and_added_versions_each": dict(closures),
        "global_keys_time_lineage_parents": "PASS",
        "all_unapproved_atoms_unchanged": True,
        "raw_source_admission": "PINNED_PREDECESSOR_PROOFS_NOT_REPLAYED_HERE",
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result
