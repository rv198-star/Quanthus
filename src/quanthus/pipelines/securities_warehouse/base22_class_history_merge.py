"""Bounded native history merge of an already independently replayed class revision."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, semantic_scan
from .base22_canonical_action_revision import SOURCE_VERSION
from .base22_cash_history_merge import MAX_ROWS, merge_cash_partition
from .base22_cash_restatement import _scan_cash_global
from .base22_notice_class_revision import CONTRACT_ID, PREFIX, SOURCE, TABLES, load_class_contract
from .base22_precision import decimal_profile
from .base22_sources import _tree
from .disclosure_event import _stable_id

PURPOSE = "EXACT_QUALIFIED_LITERAL_CLASS_HISTORY_MERGE_NOT_COMPLETE_BASE22"


def merge_class_partition(
    table: str,
    part: str,
    rule: dict[str, Any],
    history: list[dict[str, Any]],
    before: list[dict[str, Any]],
    qualified: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Only the complete old term group closes; new literal classes may change keys."""
    if table == TABLES[0] or not before:
        return merge_cash_partition(table, part, rule, history, before, qualified)
    if table != TABLES[1] or any(len(rs) > MAX_ROWS for rs in (history, before, qualified)):
        raise BoundedError("CLASS_HISTORY_TABLE_OR_PARTITION_BOUND_INVALID")
    if any(
        not isinstance(r.get("effective_at"), datetime) or r["effective_at"].utcoffset() is None
        for rs in (history, before, qualified)
        for r in rs
    ):
        raise BoundedError("CLASS_HISTORY_PHYSICAL_PARTITION_MISMATCH")
    if any(partition(r, rule) != part for rs in (history, before, qualified) for r in rs):
        raise BoundedError("CLASS_HISTORY_PHYSICAL_PARTITION_MISMATCH")
    fields = rule["business_key"] + rule["version_key"]

    def key(r: dict[str, Any]) -> str:
        if any(r.get(f) is None for f in fields):
            raise BoundedError("CLASS_HISTORY_NULL_KEY")
        return digest([r[f] for f in fields])

    def index(rs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result = {key(r): r for r in rs}
        if len(result) != len(rs):
            raise BoundedError("CLASS_HISTORY_DUPLICATE_VERSION")
        return result

    old, selected, patch = index(history), index(before), index(qualified)
    if any(k not in old or digest(old[k]) != digest(r) for k, r in selected.items()):
        raise BoundedError("CLASS_HISTORY_EXACT_BEFORE_CHANGED_OR_MISSING")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected.values():
        if row.get("knowledge_to") is not None:
            raise BoundedError("CLASS_HISTORY_BEFORE_GROUP_NOT_OPEN")
        groups[row["corporate_action_id"]].append(row)
    if {
        k
        for k, r in old.items()
        if r["corporate_action_id"] in groups and r["knowledge_to"] is None
    } != set(selected):
        raise BoundedError("CLASS_HISTORY_INCOMPLETE_SELECTED_OLD_GROUP")
    if not set(selected) <= set(patch):
        raise BoundedError("CLASS_HISTORY_OLD_CLOSURES_MISSING")
    added = {k: r for k, r in patch.items() if k not in selected}
    if set(added) & set(old) or len(added) != 2 * len(groups):
        raise BoundedError("CLASS_HISTORY_NEW_GROUP_CENSUS_OR_VERSION_CONFLICT")
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in added.values():
        incoming[row["corporate_action_id"]].append(row)
    if set(incoming) != set(groups):
        raise BoundedError("CLASS_HISTORY_UNBOUND_NEW_GROUP")
    pairs = {
        frozenset({"SOURCE_RESTRICTED_SHAREHOLDERS", "SOURCE_FLOATING_SHAREHOLDERS"}),
        frozenset({"SOURCE_RESTRICTED_SHAREHOLDERS", "SOURCE_UNRESTRICTED_SHAREHOLDERS"}),
        frozenset({"SOURCE_LARGE_SHAREHOLDERS", "SOURCE_SMALL_MEDIUM_SHAREHOLDERS"}),
    }
    for action, original in groups.items():
        new = incoming[action]
        old_types = {r["term_type"] for r in original}
        if (
            len(old_types) != len(original)
            or "CASH_AMOUNT_PER_SHARE" not in old_types
            or not old_types
            <= {"CASH_AMOUNT_PER_SHARE", "BONUS_SHARES_PER_SHARE", "TRANSFER_SHARES_PER_SHARE"}
            or len(new) != 2
            or frozenset(r["term_type"].removeprefix(PREFIX) for r in new) not in pairs
        ):
            raise BoundedError("CLASS_HISTORY_EXACT_CLASS_PAIR_AND_OLD_TERM_GROUP_REQUIRED")
        clock = new[0].get("knowledge_from")
        if not isinstance(clock, datetime) or clock.utcoffset() is None:
            raise BoundedError("CLASS_HISTORY_REAL_NEW_KNOWLEDGE_REQUIRED")
        for row in original:
            if (
                not isinstance(row["knowledge_from"], datetime)
                or clock <= row["knowledge_from"]
                or patch[key(row)] != {**row, "knowledge_to": clock}
            ):
                raise BoundedError("CLASS_HISTORY_EXACT_OLD_CLOSURE_INVALID")
        for row in new:
            value, typ = row.get("amount_per_share"), row.get("term_type")
            if (
                not isinstance(typ, str)
                or not typ.startswith(PREFIX)
                or row.get("corporate_action_term_id")
                != _stable_id("corporate_action_term", action, typ)
                or row.get("record_source") != SOURCE
                or not row["source_revision"].startswith(CONTRACT_ID + ":sha256:")
                or any(row.get(k) != new[0].get(k) for k in SOURCE_VERSION)
                or row.get("knowledge_to") is not None
                or row.get("announced_at") is not None
                or any(
                    row.get(f) is not None
                    for f in (
                        "option_id",
                        "subscription_price",
                        "ratio_numerator",
                        "ratio_denominator",
                    )
                )
                or row.get("currency") != "CNY"
                or not isinstance(value, Decimal)
                or not value.is_finite()
                or value < 0
                or not decimal_profile(value, precision=38, scale=10)["fits"]
                or any(row.get("effective_at") != r.get("effective_at") for r in original)
            ):
                raise BoundedError("CLASS_HISTORY_LITERAL_TERM_SOURCE_COORDINATES_INVALID")
    result = [dict(patch[key(r)]) if key(r) in selected else dict(r) for r in history]
    result.extend(dict(added[k]) for k in sorted(added))
    if len(result) > MAX_ROWS:
        raise BoundedError("CLASS_HISTORY_OUTPUT_PARTITION_BOUND_INVALID")
    proof = {
        "old_rows": len(history),
        "closed_versions": len(selected),
        "new_versions": len(added),
        "selected_before_census_sha256": digest(sorted(digest(r) for r in before)),
        "qualified_patch_census_sha256": digest(sorted(digest(r) for r in qualified)),
        "output_census_sha256": digest(sorted(digest(r) for r in result)),
        "uniform_cash_inferred": False,
        "actual_holder_membership_inferred": False,
        "source_priority_decided_in_merge": False,
        "complete_Base22": False,
    }
    return result, proof


def audit_class_history_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """All physical history partitions and exact before/after closure without original roots."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("purpose") != PURPOSE
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("READY") is not False
        or body.get("complete_Base22") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("output_rows", {})) != set(TABLES)
    ):
        raise BoundedError("CLASS_HISTORY_SCOPE_INVALID")
    if set(_tree(root)) != set(body["physical_members"]) | {"QUALIFICATION.json"}:
        raise BoundedError("CLASS_HISTORY_PHYSICAL_SET_CHANGED")
    for relative, member in body["physical_members"].items():
        p = _physical(root, relative, independent=True)
        if file_sha(p) != member["sha256"] or p.stat().st_size != member["size_bytes"]:
            raise BoundedError("CLASS_HISTORY_MEMBER_CHANGED")
        budget.check()
    load_class_contract(root / "contract")
    source_body = json.loads((root / "evidence/qualified-source-descriptor.json").read_bytes())
    source_cold_registration = json.loads(
        (root / "evidence/qualified-source-cold-registration.json").read_bytes()
    )
    if (
        source_body["identity"] != body["cash_scope_identity"]
        or source_body["identity"]
        != digest({k: v for k, v in source_body.items() if k != "identity"})
        or file_sha(root / "evidence/qualified-source-descriptor.json")
        != source_cold_registration["descriptor_sha256"]
        or source_cold_registration["scope_identity"] != source_body["identity"]
        or source_body["contract_id"] != CONTRACT_ID
    ):
        raise BoundedError("CLASS_HISTORY_EXACT_SOURCE_ADMISSION_CHANGED")
    for name, identity in (
        ("history_cold", body["history_scope_identity"]),
        ("cash_cold", body["cash_scope_identity"]),
    ):
        proof = json.loads((root / "evidence" / (name + ".json")).read_bytes())
        if (
            proof.get("scope_identity") != identity
            or proof.get("source_unchanged") is not True
            or len(proof["results"]) != 4
            or any(r["old_source_reads"] or r["network_calls"] for r in proof["results"])
            or any(r["status"] != "EXPECTED_FAIL_CLOSED" for r in proof["results"][2:])
        ):
            raise BoundedError("CLASS_HISTORY_PREDECESSOR_COLD_ADMISSION_INVALID")
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)

    def rows(item: dict[str, Any]) -> list[dict[str, Any]]:
        physical = pq.ParquetFile(_physical(root, item["path"], independent=True))
        if (
            not 0 <= item["rows"] <= MAX_ROWS
            or physical.metadata.num_rows != item["rows"]
            or not physical.schema_arrow.equals(ss[item["table"]], check_metadata=False)
        ):
            raise BoundedError("CLASS_HISTORY_SCHEMA_OR_CENSUS_CHANGED")
        return physical.read().to_pylist()  # type: ignore[no-any-return]

    counts: Counter[str] = Counter()
    closed: Counter[str] = Counter()
    added: Counter[str] = Counter()
    compared = set()
    admitted_before: dict[str, Counter[str]] = {t: Counter() for t in TABLES}
    admitted_new: dict[str, Counter[str]] = {t: Counter() for t in TABLES}
    for item in body["outputs"]:
        table, part = item["table"], item["partition"]
        if table not in TABLES or (table, part) in compared:
            raise BoundedError("CLASS_HISTORY_DUPLICATE_PARTITION")
        compared.add((table, part))
        binding = body["partition_bindings"][table][part]
        expected, proof = merge_class_partition(
            table,
            part,
            contract["tables"][table],
            rows(binding["history"]),
            rows(binding["before"]) if binding["before"] else [],
            rows(binding["patch"]) if binding["patch"] else [],
        )
        if binding["before"]:
            selected = rows(binding["before"])
            closed_keys = {
                digest(
                    [
                        r[k]
                        for k in contract["tables"][table]["business_key"]
                        + contract["tables"][table]["version_key"]
                    ]
                )
                for r in selected
            }
            admitted_before[table].update(digest(r) for r in selected)
            admitted_new[table].update(
                digest(r)
                for r in rows(binding["patch"])
                if digest(
                    [
                        r[k]
                        for k in contract["tables"][table]["business_key"]
                        + contract["tables"][table]["version_key"]
                    ]
                )
                not in closed_keys
            )
        actual = rows(item)
        if (
            Counter(digest(r) for r in actual) != Counter(digest(r) for r in expected)
            or proof != binding["proof"]
            or semantic_scan([root / item["path"]], ss[table], check=budget.check)
            != item["semantic_scan"]
        ):
            raise BoundedError("CLASS_HISTORY_UNAPPROVED_CHANGE_OR_SUMMARY")
        if not binding["before"] and file_sha(root / item["path"]) != file_sha(
            root / binding["history"]["path"]
        ):
            raise BoundedError("CLASS_HISTORY_HEALTHY_BYTES_CHANGED")
        counts[table] += len(actual)
        closed[table] += proof["closed_versions"]
        added[table] += proof["new_versions"]
    if (
        dict(counts) != body["output_rows"]
        or dict(closed) != body["selected_versions_each"]
        or dict(added) != body["added_versions_each"]
        or compared != {(t, p) for t, parts in body["partition_bindings"].items() for p in parts}
    ):
        raise BoundedError("CLASS_HISTORY_EXACT_PARTITION_VERSION_CENSUS_CHANGED")
    expected_before: dict[str, Counter[str]] = {TABLES[0]: Counter(), TABLES[1]: Counter()}
    expected_new: dict[str, Counter[str]] = {TABLES[0]: Counter(), TABLES[1]: Counter()}
    for proof in source_body["proofs"].values():
        expected_before[TABLES[0]].update([proof["before_action_sha256"]])
        expected_before[TABLES[1]].update(proof["before_terms_sha256"])
        expected_new[TABLES[0]].update([proof["new_action_sha256"]])
        expected_new[TABLES[1]].update(proof["new_class_terms_sha256"])
    if admitted_before != expected_before or admitted_new != expected_new:
        raise BoundedError("CLASS_HISTORY_TYPED_PATCH_NOT_ADMITTED_BY_SOURCE")
    _scan_cash_global(root, body, budget, contract, ss, rows)
    receipt = {
        "status": "ALL_LITERAL_CLASS_HISTORY_PARTITIONS_PASS_NOT_COMPLETE_BASE22",
        "scope_identity": body["identity"],
        "output_rows": dict(counts),
        "closed_and_added_versions_each": dict(closed),
        "added_versions_each": dict(added),
        "global_keys_time_lineage_parents": "PASS",
        "all_unapproved_atoms_unchanged": True,
        "raw_source_admission": "PINNED_PREDECESSOR_DOUBLE_COLD_PROOFS",
        "complete_Base22": False,
    }
    receipt["identity"] = digest(receipt)
    return receipt
