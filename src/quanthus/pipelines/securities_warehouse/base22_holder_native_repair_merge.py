"""Compose exact qualified holder repairs with a current native timeline.

An already closed historical interval stays closed at its original endpoint.
Only the unique current open version may receive a qualified successor. This
does not infer a provider priority over a different, unproved current value.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_holder_column_revision import _KEYS, _META, TABLE
from .base22_sources import _tree

CONTRACT_ID = "p10-base22-current-native-holder-qualified-repair-composition-v1"
PURPOSE = "EXACT_QUALIFIED_HOLDER_CURRENT_TIMELINE_MERGE_NOT_COMPLETE_WEEKLY_OR_BASE22"
MAX_KEYS = 8192
MAX_FILE_ROWS = 65536
META = _META | {"parser_version"}
SOURCE_CONTRACTS = {
    "column": "p10-base22-holder-original-column-text-restoration-v1",
    "chronology": "p10-base22-same-provider-holder-retained-chronology-v1",
}


def reconcile_current_pair(
    qualified_closed: dict[str, Any], qualified_new: dict[str, Any], current: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Only original qualified deltas; third current values remain held."""
    original = {**qualified_closed, "knowledge_to": None}
    clock = qualified_new.get("knowledge_from")
    if (
        any(
            tuple(r[k] for k in _KEYS) != tuple(original[k] for k in _KEYS)
            for r in (current, qualified_new)
        )
        or current.get("knowledge_to") is not None
        or qualified_new.get("knowledge_to") is not None
        or not isinstance(clock, datetime)
        or clock.utcoffset() is None
        or any(
            not isinstance(r.get("knowledge_from"), datetime)
            or r["knowledge_from"].utcoffset() is None
            for r in (original, current)
        )
        or qualified_closed.get("knowledge_to") != clock
        or clock <= current["knowledge_from"]
        or clock <= original["knowledge_from"]
        or set(current) != set(original)
        or set(qualified_new) != set(original)
    ):
        raise BoundedError("HOLDER_NATIVE_REPAIR_EXACT_KEY_PAIR_OR_CURRENT_CLOCK_INVALID")
    fields = set(original) - META
    qualified_delta = {k for k in fields if original[k] != qualified_new[k]}
    changed = {k for k in fields if current[k] != qualified_new[k]}
    if not changed <= qualified_delta or any(current[k] != original[k] for k in changed):
        raise BoundedError("HOLDER_NATIVE_REPAIR_UNPROVED_CURRENT_VALUE_PRIORITY")
    # Changed data must still be the exact legacy provider value that the
    # original raw-replayed qualification corrected, not another provider's
    # competing value. An unchanged body needs no source-value arbitration.
    if changed and current["record_source"] != original["record_source"]:
        raise BoundedError("HOLDER_NATIVE_REPAIR_DIFFERENT_PROVIDER_PRIORITY_UNQUALIFIED")
    closed = {**current, "knowledge_to": clock}
    proof = {
        "contract_id": CONTRACT_ID,
        "business_key_sha256": digest([current[k] for k in _KEYS]),
        "qualified_original_closed_sha256": digest(qualified_closed),
        "qualified_new_sha256": digest(qualified_new),
        "current_before_sha256": digest(current),
        "current_after_closure_sha256": digest(closed),
        "original_qualified_delta_fields": sorted(qualified_delta),
        "actual_current_changed_fields": sorted(changed),
        "current_is_exact_original": current == original,
        "previously_closed_intervals_rewritten": False,
        "third_value_or_different_provider_priority_granted": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }
    proof["identity"] = digest(proof)
    return closed, dict(qualified_new), proof


def audit_native_repair_merge(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """All native atoms, exact local qualified pairs, keys/time/lineage/parents.

    Original source qualification stays pinned to its predecessor full raw and
    cold receipts. This is a typed composition audit, not a new raw-source audit.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != PURPOSE
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or any(
            body.get(k) is not False for k in ("READY", "complete_Base22", "complete_weekly_union")
        )
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("selected_source_identities", {})) != {"column", "chronology"}
        or _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}
    ):
        raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_DESCRIPTOR_OR_FILE_SET_CHANGED")
    for relative, member in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if file_sha(path) != member["sha256"] or path.stat().st_size != member["size_bytes"]:
            raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_MEMBER_CHANGED")
        budget.check()
    _, model, extension = load_contract(root / "contract")
    schema = schemas(model, extension)[TABLE]

    def rows(item: dict[str, Any]) -> list[dict[str, Any]]:
        physical = pq.ParquetFile(_physical(root, item["path"], independent=True))
        if (
            not physical.schema_arrow.equals(schema, check_metadata=False)
            or not 0 <= item["rows"] <= MAX_FILE_ROWS
            or physical.metadata.num_rows != item["rows"]
        ):
            raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_SCHEMA_OR_COUNT_CHANGED")
        return physical.read().to_pylist()  # type: ignore[no-any-return]

    pairs: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for source, identity in body["selected_source_identities"].items():
        descriptor = json.loads((root / "evidence" / (source + "-descriptor.json")).read_bytes())
        cold = json.loads((root / "evidence" / (source + "-cold.json")).read_bytes())
        if (
            descriptor["identity"] != identity
            or descriptor["contract_id"] != SOURCE_CONTRACTS[source]
            or descriptor["identity"]
            != digest({k: v for k, v in descriptor.items() if k != "identity"})
            or cold["scope_identity"] != identity
            or not cold["source_unchanged"]
            or len(cold["results"]) != 4
            or any(i["old_source_reads"] or i["network_calls"] for i in cold["results"])
            or any(i["status"] != "EXPECTED_FAIL_CLOSED" for i in cold["results"][2:])
        ):
            raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_QUALIFIED_SOURCE_ADMISSION_CHANGED")
        admitted_paths = {i["path"] for i in descriptor["stock_outputs"].values() if i["path"]}
        if set(body["qualified_patch_members"][source]) != admitted_paths:
            raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_QUALIFIED_PATCH_SET_CHANGED")
        for original_path, item in body["qualified_patch_members"][source].items():
            if (
                file_sha(root / item["path"])
                != descriptor["physical_members"][original_path]["sha256"]
            ):
                raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_PATCH_NOT_QUALIFIED_BYTES")
            groups: dict[str, list[dict[str, Any]]] = {}
            for row in rows(item):
                groups.setdefault(digest([row[k] for k in _KEYS]), []).append(row)
            for key, group in groups.items():
                old = [r for r in group if r["knowledge_to"] is not None]
                incoming = [r for r in group if r["knowledge_to"] is None]
                if key in pairs or len(old) != 1 or len(incoming) != 1 or len(pairs) >= MAX_KEYS:
                    raise BoundedError(
                        "HOLDER_NATIVE_REPAIR_MERGE_QUALIFIED_PAIR_BOUND_OR_DUPLICATE"
                    )
                pairs[key] = (old[0], incoming[0])
    history = json.loads((root / "evidence/history-descriptor.json").read_bytes())
    history_cold = json.loads((root / "evidence/history-cold.json").read_bytes())
    history_outputs = {i["path"]: i for i in history["outputs"]}
    if (
        history["identity"] != body["history_scope_identity"]
        or history["identity"] != digest({k: v for k, v in history.items() if k != "identity"})
        or history_cold["scope_identity"] != history["identity"]
        or not history_cold["source_unchanged"]
        or len(history_cold["results"]) != 4
        or any(i["old_source_reads"] or i["network_calls"] for i in history_cold["results"])
        or any(i["status"] != "EXPECTED_FAIL_CLOSED" for i in history_cold["results"][2:])
        or set(body["history_bindings"]) != set(history_outputs)
        or body["parents"] != history["parents"]
    ):
        raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_NATIVE_INPUT_SCOPE_CHANGED")
    changes = body["allowed_changes"]
    if not set(changes) <= set(pairs) or set(body["held_current_qualification_keys"]) != set(
        pairs
    ) - set(changes):
        raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_UNQUALIFIED_CHANGE_KEY")
    current_rows = rows(body["selected_current_before"])
    if len(current_rows) > MAX_KEYS:
        raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_SHARED_CURRENT_BOUND_EXCEEDED")
    current_index = {digest(r): r for r in current_rows}
    if len(current_index) != len(current_rows):
        raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_SHARED_CURRENT_DUPLICATE")
    expected_new = []
    consumed = set()
    for key, binding in changes.items():
        current = current_index.get(binding["proof"]["current_before_sha256"])
        if current is None:
            raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_CURRENT_SELECTION_NOT_SINGLE")
        consumed.add(digest(current))
        closed, new, proof = reconcile_current_pair(*pairs[key], current)
        if proof != binding["proof"]:
            raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_RECOMPUTED_CURRENT_PROOF_CHANGED")
        expected_new.append(new)
    if consumed != set(current_index):
        raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_UNUSED_SHARED_CURRENT_INPUT")
    target_closures = {c["proof"]["current_before_sha256"]: c["proof"] for c in changes.values()}
    found: Counter[str] = Counter()
    total = 0
    for original_path, binding in body["history_bindings"].items():
        if file_sha(root / binding["before"]["path"]) != history_outputs[original_path]["sha256"]:
            raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_EXACT_NATIVE_BEFORE_CHANGED")
        original = rows(binding["before"])
        expected = []
        for row in original:
            sha = digest(row)
            if sha in target_closures:
                proof = target_closures[sha]
                closed, _, _ = reconcile_current_pair(*pairs[proof["business_key_sha256"]], row)
                found[sha] += 1
                expected.append(closed)
            else:
                expected.append(row)
        actual = rows(binding["output"])
        if (
            Counter(digest(r) for r in actual) != Counter(digest(r) for r in expected)
            or semantic_scan([root / binding["output"]["path"]], schema, check=budget.check)
            != binding["output"]["semantic_scan"]
        ):
            raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_UNAPPROVED_NATIVE_ATOM_CHANGE")
        if not any(digest(r) in target_closures for r in original) and file_sha(
            root / binding["before"]["path"]
        ) != file_sha(root / binding["output"]["path"]):
            raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_HEALTHY_BYTES_CHANGED")
        total += len(actual)
    new_rows = [r for i in body["new_outputs"] for r in rows(i)]
    if (
        found != Counter(dict.fromkeys(target_closures, 1))
        or Counter(digest(r) for r in new_rows) != Counter(digest(r) for r in expected_new)
        or total + len(new_rows) != body["output_rows"]
    ):
        raise BoundedError("HOLDER_NATIVE_REPAIR_MERGE_EXACT_GLOBAL_ALLOWED_CENSUS_CHANGED")
    contract, _, _ = load_contract(root / "contract")
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        scan.add(
            TABLE,
            [root / b["output"]["path"] for b in body["history_bindings"].values()]
            + [root / i["path"] for i in body["new_outputs"]],
        )
        rule = contract["tables"][TABLE]
        scan.keys(TABLE, rule["business_key"], rule["version_key"])
        scan.row_rules(TABLE, schema, rule)
        scan.lineage(TABLE, [tuple(i) for i in body["admitted_lineage"]])
        for parent, items in body["parents"].items():
            scan.add(parent, [root / i["path"] for i in items])
        for parent, field in (
            ("dwd_listed_company", "company_id"),
            ("dwd_security", "security_id"),
        ):
            scan.reject(
                f"SELECT 1 FROM {TABLE} h WHERE NOT EXISTS(SELECT 1 FROM {parent} p "
                f"WHERE p.{field}=h.{field}) LIMIT 1",
                "HOLDER_NATIVE_REPAIR_MERGE_PARENT_CLOSURE_FAILED",
            )
    result = {
        "status": "ALL_NATIVE_HOLDER_TYPED_REPAIR_COMPOSITION_PASS_NOT_COMPLETE_WEEKLY_OR_BASE22",
        "scope_identity": body["identity"],
        "output_rows": total + len(new_rows),
        "closed_current_versions": len(changes),
        "new_governance_versions": len(new_rows),
        "old_closed_intervals_and_unapproved_atoms_unchanged": True,
        "original_source_admission": "PINNED_PREDECESSOR_RAW_AND_DOUBLE_COLD_NOT_RERUN_HERE",
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result
