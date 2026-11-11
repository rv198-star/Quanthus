"""Merge qualified literal issuer claims without inventing a uniform entitlement.

The complete three-document source closure is local and replayed once by the
cold audit. Partition merges then consume that immutable typed prerequisite.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, semantic_scan
from .base22_cash_history_merge import MAX_ROWS, merge_cash_partition
from .base22_cash_restatement import _scan_cash_global
from .base22_nonuniform_revision import (
    _ROLES,
    PREFIX,
    SOURCE,
    TABLES,
    audit_nonuniform_revision_scope,
)
from .base22_sources import _tree
from .disclosure_event import _stable_id

PURPOSE = "EXACT_LITERAL_NONUNIFORM_HISTORY_MERGE_NOT_GENERAL_CASH_NOT_COMPLETE_BASE22"
MERGE_MODULES = ("base22_nonuniform_history_merge.py", "base22_cash_history_merge.py")


def merge_nonuniform_partition(
    table: str,
    part: str,
    rule: dict[str, Any],
    history: list[dict[str, Any]],
    before: list[dict[str, Any]],
    qualified: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Close a complete old group; retain every other historical atom exactly."""
    if table == TABLES[0] or not before:
        return merge_cash_partition(table, part, rule, history, before, qualified)
    if table != TABLES[1] or any(len(rs) > MAX_ROWS for rs in (history, before, qualified)):
        raise BoundedError("NONUNIFORM_HISTORY_PARTITION_BOUND_INVALID")
    if any(
        not isinstance(r.get("effective_at"), datetime) or r["effective_at"].utcoffset() is None
        for rs in (history, before, qualified)
        for r in rs
    ):
        raise BoundedError("NONUNIFORM_HISTORY_EFFECTIVE_COORDINATE_INVALID")
    if any(partition(r, rule) != part for rs in (history, before, qualified) for r in rs):
        raise BoundedError("NONUNIFORM_HISTORY_PARTITION_MISMATCH")
    fields = rule["business_key"] + rule["version_key"]

    def key(row: dict[str, Any]) -> str:
        if any(row.get(f) is None for f in fields):
            raise BoundedError("NONUNIFORM_HISTORY_NULL_KEY")
        return digest([row[f] for f in fields])

    def index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result = {key(r): r for r in rows}
        if len(result) != len(rows):
            raise BoundedError("NONUNIFORM_HISTORY_DUPLICATE_VERSION")
        return result

    old, selected, patch = index(history), index(before), index(qualified)
    if any(k not in old or old[k] != r for k, r in selected.items()):
        raise BoundedError("NONUNIFORM_HISTORY_EXACT_BEFORE_CHANGED")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in before:
        if row.get("knowledge_to") is not None:
            raise BoundedError("NONUNIFORM_HISTORY_BEFORE_GROUP_NOT_OPEN")
        groups[row["corporate_action_id"]].append(row)
    if {
        k
        for k, r in old.items()
        if r["corporate_action_id"] in groups and r["knowledge_to"] is None
    } != set(selected) or not set(selected) <= set(patch):
        raise BoundedError("NONUNIFORM_HISTORY_INCOMPLETE_OLD_GROUP_OR_CLOSURES")
    added = {k: r for k, r in patch.items() if k not in selected}
    if set(added) & set(old):
        raise BoundedError("NONUNIFORM_HISTORY_NEW_VERSION_CONFLICT")
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in added.values():
        incoming[row["corporate_action_id"]].append(row)
    if set(incoming) != set(groups):
        raise BoundedError("NONUNIFORM_HISTORY_UNBOUND_NEW_GROUP")
    named = {PREFIX + _ROLES[r][0] for r in list(_ROLES)[:2]}
    mixed = {PREFIX + _ROLES[r][0] for r in list(_ROLES)[2:5]}
    all_types = {PREFIX + spec[0] for spec in _ROLES.values()}
    for action, original in groups.items():
        new = incoming[action]
        types = {r["term_type"] for r in new}
        if (
            len(types) != len(new)
            or not (types == named or mixed <= types <= all_types - named)
            or not 2 <= len(new) <= 4
        ):
            raise BoundedError("NONUNIFORM_HISTORY_COMPLETE_LITERAL_POPULATION_GROUP_REQUIRED")
        clock = new[0].get("knowledge_from")
        if not isinstance(clock, datetime) or clock.utcoffset() is None:
            raise BoundedError("NONUNIFORM_HISTORY_REAL_NEW_CLOCK_REQUIRED")
        for row in original:
            if clock <= row["knowledge_from"] or patch[key(row)] != {**row, "knowledge_to": clock}:
                raise BoundedError("NONUNIFORM_HISTORY_EXACT_OLD_INTERVAL_CLOSURE_REQUIRED")
        for row in new:
            if (
                row.get("knowledge_from") != clock
                or row.get("knowledge_to") is not None
                or row.get("record_source") != SOURCE
                or row.get("announced_at") is not None
                or row.get("corporate_action_term_id")
                != _stable_id("corporate_action_term", action, row["term_type"])
                or any(row.get("effective_at") != r["effective_at"] for r in original)
            ):
                raise BoundedError("NONUNIFORM_HISTORY_LITERAL_SOURCE_COORDINATES_CHANGED")
    result = [dict(patch[key(r)]) if key(r) in selected else dict(r) for r in history]
    result.extend(dict(added[k]) for k in sorted(added))
    if len(result) > MAX_ROWS:
        raise BoundedError("NONUNIFORM_HISTORY_OUTPUT_PARTITION_BOUND_EXCEEDED")
    return result, {
        "old_rows": len(history),
        "closed_versions": len(before),
        "new_versions": len(added),
        "selected_before_census_sha256": digest(sorted(digest(r) for r in before)),
        "qualified_patch_census_sha256": digest(sorted(digest(r) for r in qualified)),
        "output_census_sha256": digest(sorted(digest(r) for r in result)),
        "general_gross_cash_qualified": False,
        "holder_membership_or_aggregate_inferred": False,
    }


def audit_nonuniform_history_scope(
    root: Path, body: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """One original issuer replay, full partition census and exact allowed changes."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("purpose") != PURPOSE
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or any(
            body.get(f) is not False
            for f in ("READY", "complete_Base22", "general_gross_cash_qualified")
        )
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("output_rows", {})) != set(TABLES)
        or set(body.get("merge_code_closure", {})) != set(MERGE_MODULES)
        or _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}
    ):
        raise BoundedError("NONUNIFORM_HISTORY_SCOPE_OR_PHYSICAL_SET_CHANGED")
    for name, sha in body["merge_code_closure"].items():
        if file_sha(Path(__file__).with_name(name)) != sha:
            raise BoundedError("NONUNIFORM_HISTORY_MERGE_CODE_CHANGED")
    for relative, member in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if path.stat().st_size != member["size_bytes"] or file_sha(path) != member["sha256"]:
            raise BoundedError("NONUNIFORM_HISTORY_PHYSICAL_MEMBER_CHANGED")
        budget.check()
    source = root / "qualified-nonuniform"
    child = json.loads((source / "QUALIFICATION.json").read_bytes())
    if child["identity"] != body["nonuniform_scope_identity"]:
        raise BoundedError("NONUNIFORM_HISTORY_QUALIFIED_SOURCE_CHANGED")
    replay = audit_nonuniform_revision_scope(source, child, budget)
    if replay["general_gross_cash_qualified"] is not False:
        raise BoundedError("NONUNIFORM_HISTORY_UNKNOWN_TAX_CANNOT_BECOME_GROSS_CASH")
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    history = json.loads((root / "evidence/history-source-descriptor.json").read_bytes())
    cold = json.loads((root / "evidence/history_cold.json").read_bytes())
    if (
        history["identity"] != digest({k: v for k, v in history.items() if k != "identity"})
        or history["identity"] != body["history_scope_identity"]
        or cold["scope_identity"] != history["identity"]
        or cold.get("source_unchanged") is not True
        or len(cold["results"]) != 4
        or any(i["old_source_reads"] or i["network_calls"] for i in cold["results"])
        or any(i["status"] != "EXPECTED_FAIL_CLOSED" for i in cold["results"][2:])
    ):
        raise BoundedError("NONUNIFORM_HISTORY_IMMUTABLE_PREDECESSOR_ADMISSION_CHANGED")
    old_members = {(i["table"], i["partition"]): i for i in history["outputs"]}

    def rows(item: dict[str, Any], base: Path = root) -> list[dict[str, Any]]:
        physical = pq.ParquetFile(_physical(base, item["path"], independent=True))
        if (
            not 0 <= item["rows"] <= MAX_ROWS
            or physical.metadata.num_rows != item["rows"]
            or not physical.schema_arrow.equals(ss[item["table"]], check_metadata=False)
        ):
            raise BoundedError("NONUNIFORM_HISTORY_SCHEMA_OR_ROW_CENSUS_CHANGED")
        return physical.read().to_pylist()  # type: ignore[no-any-return]

    admitted_before: dict[str, Counter[str]] = {t: Counter() for t in TABLES}
    admitted_patch: dict[str, Counter[str]] = {t: Counter() for t in TABLES}
    counts: Counter[str] = Counter()
    closed: Counter[str] = Counter()
    added: Counter[str] = Counter()
    compared = set()
    for item in body["outputs"]:
        table, part = item["table"], item["partition"]
        if table not in TABLES or (table, part) in compared:
            raise BoundedError("NONUNIFORM_HISTORY_DUPLICATE_TABLE_PARTITION")
        compared.add((table, part))
        binding = body["partition_bindings"][table][part]
        old_member = old_members.get((table, part))
        if (
            old_member is None
            or file_sha(root / binding["history"]["path"]) != old_member["sha256"]
            or binding["history"]["rows"] != old_member["rows"]
        ):
            raise BoundedError("NONUNIFORM_HISTORY_BEFORE_PARTITION_NOT_EXACT_PREDECESSOR")
        selected = rows(binding["before"]) if binding["before"] else []
        patch = rows(binding["patch"]) if binding["patch"] else []
        expected, proof = merge_nonuniform_partition(
            table, part, contract["tables"][table], rows(binding["history"]), selected, patch
        )
        actual = rows(item)
        if (
            Counter(digest(r) for r in actual) != Counter(digest(r) for r in expected)
            or proof != binding["proof"]
            or semantic_scan([root / item["path"]], ss[table], check=budget.check)
            != item["semantic_scan"]
            or not selected
            and file_sha(root / item["path"]) != file_sha(root / binding["history"]["path"])
        ):
            raise BoundedError("NONUNIFORM_HISTORY_UNAPPROVED_CHANGE_OR_SUMMARY")
        admitted_before[table].update(digest(r) for r in selected)
        admitted_patch[table].update(digest(r) for r in patch)
        counts[table] += len(actual)
        closed[table] += proof["closed_versions"]
        added[table] += proof["new_versions"]
    for table in TABLES:
        if admitted_before[table] != Counter(
            digest(r) for r in rows(child["before"][table], source)
        ) or admitted_patch[table] != Counter(
            digest(r) for i in child["outputs"] if i["table"] == table for r in rows(i, source)
        ):
            raise BoundedError("NONUNIFORM_HISTORY_TYPED_PATCH_NOT_ADMITTED_BY_ORIGINAL_SOURCE")
    if (
        dict(counts) != body["output_rows"]
        or dict(closed) != body["selected_versions_each"]
        or dict(added) != body["added_versions_each"]
        or compared != {(t, p) for t, parts in body["partition_bindings"].items() for p in parts}
        or compared != set(old_members)
    ):
        raise BoundedError("NONUNIFORM_HISTORY_EXACT_VERSION_PARTITION_CENSUS_CHANGED")
    _scan_cash_global(root, body, budget, contract, ss, rows)
    receipt = {
        "status": "ALL_NONUNIFORM_HISTORY_PARTITIONS_PASS_NOT_GENERAL_CASH_NOT_COMPLETE_BASE22",
        "scope_identity": body["identity"],
        "output_rows": dict(counts),
        "closed_and_added_versions_each": dict(closed),
        "added_versions_each": dict(added),
        "all_unapproved_atoms_unchanged": True,
        "original_source_replay": replay["status"],
        "original_source_replay_count": 1,
        "general_gross_cash_qualified": False,
        "complete_Base22": False,
    }
    receipt["identity"] = digest(receipt)
    return receipt
