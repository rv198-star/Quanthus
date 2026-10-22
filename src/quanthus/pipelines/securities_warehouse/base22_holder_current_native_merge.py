"""Materialize exact same-current-raw column repairs into full native history."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_financial import _clock
from .base22_holder_column_revision import _META, TABLE, _key
from .base22_holder_current_columns import CONTRACT_ID as REPAIR_ID
from .base22_holder_current_columns import audit_current_columns
from .base22_holder_native_repair_merge import CONTRACT_ID as HISTORY_ID
from .base22_sources import _tree

CONTRACT_ID = "p10-base22-native-history-exact-current-column-merge-v1"
PURPOSE = "EXACT_CURRENT_RAW_COLUMN_FULL_NATIVE_HISTORY_NOT_COMPLETE_WEEKLY_OR_BASE22"


def merge_current_columns_partition(
    original: list[dict[str, Any]], pairs: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One bounded partition; exact old SHA once, all other atoms stay unchanged."""
    if len(original) > 65536 or len(pairs) > 160:
        raise BoundedError("CURRENT_COLUMNS_NATIVE_PARTITION_BOUND_EXCEEDED")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pairs:
        grouped[_key(row)].append(row)
    closures = {}
    for group in grouped.values():
        old = [r for r in group if r["knowledge_to"] is not None]
        new = [r for r in group if r["knowledge_to"] is None]
        if len(old) != 1 or len(new) != 1:
            raise BoundedError("CURRENT_COLUMNS_NATIVE_EXACT_PAIR_REQUIRED")
        before, after = old[0], new[0]
        data = set(before) - _META - {"parser_version"}
        changed = {k for k in data if before[k] != after[k]}
        if (
            set(before) != set(after)
            or before["knowledge_to"] != after["knowledge_from"]
            or _clock(after["knowledge_from"]) <= _clock(before["knowledge_from"])
            or not changed
            or not changed <= {"share_nature", "holder_category"}
            or before["record_source"] != after["record_source"]
            or before["source_record_id"] != after["source_record_id"]
            or not after["source_revision"].startswith(REPAIR_ID + ":")
        ):
            raise BoundedError("CURRENT_COLUMNS_NATIVE_UNQUALIFIED_PAIR_DELTA_OR_CLOCK")
        closures[digest({**before, "knowledge_to": None})] = before
    hits: Counter[str] = Counter()
    actual = []
    for row in original:
        sha = digest(row)
        if sha in closures:
            hits[sha] += 1
            actual.append(dict(closures[sha]))
        else:
            actual.append(row)
    if hits != Counter(dict.fromkeys(closures, 1)):
        raise BoundedError("CURRENT_COLUMNS_NATIVE_EXACT_CURRENT_ONCE_REQUIRED")
    proof = {
        "closed_current_versions": len(closures),
        "new_governance_versions": len(grouped),
        "already_closed_intervals_rewritten": False,
        "all_other_atoms_unchanged": True,
        "input_row_census_sha256": digest(dict(Counter(digest(r) for r in original))),
        "output_row_census_sha256": digest(dict(Counter(digest(r) for r in actual))),
    }
    proof["identity"] = digest(proof)
    return actual, proof


def native_members(history: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Only this exact predecessor native composition, including its new versions."""
    if history.get("contract_id") != HISTORY_ID:
        raise BoundedError("CURRENT_COLUMNS_NATIVE_PREDECESSOR_CONTRACT_CHANGED")
    members = [b["output"] for b in history["history_bindings"].values()] + history["new_outputs"]
    result = {i["path"]: i for i in members}
    if len(result) != len(members) or sum(i["rows"] for i in members) != history["output_rows"]:
        raise BoundedError("CURRENT_COLUMNS_NATIVE_PREDECESSOR_PHYSICAL_CENSUS_CHANGED")
    return result


def audit_current_native_history(
    root: Path, body: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Full same-current raw replay once, all native partitions and global closure."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != PURPOSE
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(
            body.get(k) is not False for k in ("READY", "complete_Base22", "complete_weekly_union")
        )
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}
    ):
        raise BoundedError("CURRENT_COLUMNS_NATIVE_DESCRIPTOR_OR_FILE_SET_CHANGED")
    for relative, member in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if path.stat().st_size != member["size_bytes"] or file_sha(path) != member["sha256"]:
            raise BoundedError("CURRENT_COLUMNS_NATIVE_MEMBER_CHANGED")
        budget.check()
    nested = root / "qualified-current-raw"
    qualified = json.loads((nested / "QUALIFICATION.json").read_bytes())
    if qualified["identity"] != body["current_raw_qualification_identity"]:
        raise BoundedError("CURRENT_COLUMNS_NATIVE_QUALIFIED_RAW_IDENTITY_CHANGED")
    raw_replay = audit_current_columns(nested, qualified, budget)
    history = json.loads((root / "evidence/native-source-descriptor.json").read_bytes())
    cold = json.loads((root / "evidence/native-source-cold.json").read_bytes())
    if (
        history["identity"] != body["native_source_identity"]
        or history["identity"] != digest({k: v for k, v in history.items() if k != "identity"})
        or cold["scope_identity"] != history["identity"]
        or not cold["source_unchanged"]
        or len(cold["results"]) != 4
        or any(i["old_source_reads"] or i["network_calls"] for i in cold["results"])
        or any(i["status"] != "EXPECTED_FAIL_CLOSED" for i in cold["results"][2:])
        or body["parents"] != history["parents"]
    ):
        raise BoundedError("CURRENT_COLUMNS_NATIVE_PREDECESSOR_ADMISSION_CHANGED")
    frozen = native_members(history)
    if set(body["history_bindings"]) != set(frozen):
        raise BoundedError("CURRENT_COLUMNS_NATIVE_COMPLETE_PARTITION_SET_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    schema, rule = schemas(model, extension)[TABLE], contract["tables"][TABLE]

    def read(item: dict[str, Any], base: Path = root) -> list[dict[str, Any]]:
        pf = pq.ParquetFile(_physical(base, item["path"], independent=True))
        if (
            not pf.schema_arrow.equals(schema, check_metadata=False)
            or not 0 <= item["rows"] <= 65536
            or pf.metadata.num_rows != item["rows"]
        ):
            raise BoundedError("CURRENT_COLUMNS_NATIVE_SCHEMA_OR_ROW_BOUND_CHANGED")
        return pf.read().to_pylist()  # type: ignore[no-any-return]

    pairs = [r for i in qualified["stock_outputs"].values() for r in read(i, nested)]
    expected_new = [r for r in pairs if r["knowledge_to"] is None]
    closed = {_key(r): r for r in pairs if r["knowledge_to"] is not None}
    if len(closed) != qualified["qualified_keys"] or len(expected_new) != len(closed):
        raise BoundedError("CURRENT_COLUMNS_NATIVE_EXACT_QUALIFIED_PAIR_CENSUS_CHANGED")
    total, changed, healthy = 0, 0, 0
    seen: Counter[str] = Counter()
    lineage: set[tuple[str, str, str]] = set()
    for relative, binding in body["history_bindings"].items():
        before = read(binding["before"])
        if (
            file_sha(root / binding["before"]["path"])
            != history["physical_members"][relative]["sha256"]
        ):
            raise BoundedError("CURRENT_COLUMNS_NATIVE_EXACT_SOURCE_BYTES_CHANGED")
        keys = {_key(r) for r in before if r["knowledge_to"] is None and _key(r) in closed}
        selected = [r for r in pairs if _key(r) in keys]
        expected, proof = merge_current_columns_partition(before, selected)
        actual = read(binding["output"])
        if (
            proof != binding["proof"]
            or {partition(r, rule) for r in before + actual} != {binding["partition"]}
            or Counter(digest(r) for r in actual) != Counter(digest(r) for r in expected)
            or semantic_scan([root / binding["output"]["path"]], schema, check=budget.check)
            != binding["output"]["semantic_scan"]
        ):
            raise BoundedError("CURRENT_COLUMNS_NATIVE_UNAPPROVED_ATOM_CHANGE")
        seen.update(keys)
        if keys:
            changed += 1
        else:
            healthy += 1
            if file_sha(root / binding["before"]["path"]) != file_sha(
                root / binding["output"]["path"]
            ):
                raise BoundedError("CURRENT_COLUMNS_NATIVE_HEALTHY_FILE_BYTES_CHANGED")
        total += len(actual)
        lineage.update(
            (r["record_source"], r["source_revision"], r["source_artifact_sha256"]) for r in actual
        )
    new = [r for i in body["new_outputs"] for r in read(i)]
    for item in body["new_outputs"]:
        if {partition(r, rule) for r in read(item)} != {item["partition"]}:
            raise BoundedError("CURRENT_COLUMNS_NATIVE_NEW_PHYSICAL_PARTITION_CHANGED")
    if (
        seen != Counter(dict.fromkeys(closed, 1))
        or Counter(digest(r) for r in new) != Counter(digest(r) for r in expected_new)
        or total != history["output_rows"]
        or total + len(new) != body["output_rows"]
    ):
        raise BoundedError("CURRENT_COLUMNS_NATIVE_GLOBAL_ALLOWED_CENSUS_CHANGED")
    lineage.update(
        (r["record_source"], r["source_revision"], r["source_artifact_sha256"]) for r in new
    )
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        scan.add(
            TABLE,
            [root / b["output"]["path"] for b in body["history_bindings"].values()]
            + [root / i["path"] for i in body["new_outputs"]],
        )
        scan.keys(TABLE, rule["business_key"], rule["version_key"])
        scan.row_rules(TABLE, schema, rule)
        scan.lineage(TABLE, list(lineage))
        for parent, items in body["parents"].items():
            for i in items:
                if file_sha(root / i["path"]) != history["physical_members"][i["path"]]["sha256"]:
                    raise BoundedError("CURRENT_COLUMNS_NATIVE_PARENT_BYTES_CHANGED")
            scan.add(parent, [root / i["path"] for i in items])
        for parent, field in (
            ("dwd_listed_company", "company_id"),
            ("dwd_security", "security_id"),
        ):
            scan.reject(
                f"SELECT 1 FROM {TABLE} h WHERE NOT EXISTS(SELECT 1 FROM {parent} p "
                f"WHERE p.{field}=h.{field}) LIMIT 1",
                "CURRENT_COLUMNS_NATIVE_PARENT_CLOSURE_FAILED",
            )
    receipt = {
        "status": "ALL_NATIVE_CURRENT_RAW_COLUMN_HISTORY_PASS_NOT_COMPLETE_WEEKLY_OR_BASE22",
        "scope_identity": body["identity"],
        "output_rows": total + len(new),
        "closed_current_versions": len(closed),
        "new_governance_versions": len(new),
        "native_changed_files": changed,
        "healthy_byte_exact_files": healthy,
        "raw_replay_identity": raw_replay["identity"],
        "complete_Base22": False,
    }
    receipt["identity"] = digest(receipt)
    return receipt
