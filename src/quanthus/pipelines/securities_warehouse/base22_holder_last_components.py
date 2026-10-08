"""Compose retained single-component qualifications without source rollback."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_holder_column_revision import TABLE, _key
from .base22_holder_component_migration import (
    MIGRATION_ID,
    audit_component_migrations,
)
from .base22_holder_components_v2 import load_component_contract
from .base22_holder_current_columns import qualify_current_columns
from .base22_holder_current_native_merge import CONTRACT_ID as HISTORY_ID
from .base22_holder_native_repair_merge import reconcile_current_pair
from .base22_sources import _tree
from .financial_reporting import (
    FinancialCompanyResolver,
    financial_company_resolver_from_listed_company_rows,
)

CONTRACT_ID = "p10-base22-retained-single-component-current-native-merge-v1"
PURPOSE = "EXACT_RETAINED_SIX_SINGLE_GROUP_NATIVE_HISTORY_NOT_COMPLETE_WEEKLY_OR_BASE22"


def qualify_last_components(
    current: list[dict[str, Any]],
    prequalified_pairs: list[dict[str, Any]],
    *,
    raw_closure: list[tuple[str, dict[str, Any]]],
    resolver: FinancialCompanyResolver,
    component_contract: dict[str, Any],
    rule: dict[str, Any],
    observed_at: datetime,
    source_identity: str,
) -> dict[str, Any]:
    """Each selected old key must have one current and one unsplit successor."""
    if not 0 < len(current) <= 80 or len(prequalified_pairs) > 160 or len(raw_closure) > 32:
        raise BoundedError("LAST_COMPONENT_CURRENT_OR_SOURCE_BOUND_EXCEEDED")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in prequalified_pairs:
        grouped[_key(r)].append(r)
    before = {_key(r): r for r in current}
    if len(before) != len(current) or set(before) != set(grouped):
        raise BoundedError("LAST_COMPONENT_EXACT_CURRENT_KEY_SET_REQUIRED")
    output, proof_rows = [], []
    needs_raw: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for key, group in sorted(grouped.items()):
        old = [r for r in group if r["knowledge_to"] is not None]
        new = [r for r in group if r["knowledge_to"] is None]
        if (
            len(old) != 1
            or len(new) != 1
            or not new[0]["source_revision"].startswith(MIGRATION_ID + ":")
        ):
            raise BoundedError("LAST_COMPONENT_UNIQUE_PREQUALIFIED_SINGLE_GROUP_REQUIRED")
        original = {**old[0], "knowledge_to": None}
        if before[key] == original:
            closed, incoming, proof = reconcile_current_pair(old[0], new[0], before[key])
            output.extend([closed, incoming])
            proof_rows.append(proof)
            counts["exact_prequalified_original"] += 1
        else:
            # A qualified earlier snapshot cannot roll a later literal body
            # back. The exact current raw must independently restore columns.
            row = before[key]
            needs_raw[(row["source_artifact_sha256"], row["source_record_id"])].append(row)
    for (sha, record_id), selected in sorted(needs_raw.items()):
        matches = [
            raw
            for member_sha, raw in raw_closure
            if member_sha == sha
            and record_id
            == (
                f"stock_code={raw['stock_code']}&category=股东研究"
                f"&content_sha256={raw['content_sha256']}"
            )
        ]
        if len(matches) != 1:
            raise BoundedError("LAST_COMPONENT_EXACT_CURRENT_RAW_UNAVAILABLE_OR_AMBIGUOUS")
        qualification = qualify_current_columns(
            selected,
            original_raw=matches[0],
            original_member_sha256=sha,
            resolver=resolver,
            component_contract=component_contract,
            rule=rule,
            observed_at=observed_at,
        )
        if qualification["held"] or len(qualification["revisions"]) != len(selected):
            raise BoundedError("LAST_COMPONENT_CURRENT_RAW_COLUMNS_UNQUALIFIED")
        for item in qualification["revisions"].values():
            output.extend(item["output"])
            proof_rows.append({"current_column_revision_identity": item["identity"]})
        counts["exact_current_raw_columns"] += len(selected)
    result = {
        "contract_id": CONTRACT_ID,
        "source_identity": source_identity,
        "observed_at": observed_at,
        "input_current_census_sha256": digest(Counter(digest(r) for r in current)),
        "qualified_source_pairs_sha256": digest(prequalified_pairs),
        "raw_closure_sha256": digest([(sha, digest(raw)) for sha, raw in raw_closure]),
        "counts": dict(counts),
        "proofs": proof_rows,
        "output": output,
        "prior_closed_intervals_reopened": False,
        "provider_priority_inferred": False,
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result


def merge_last_component_partition(
    original: list[dict[str, Any]], pairs: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(original) > 65536 or len(pairs) > 160:
        raise BoundedError("LAST_COMPONENT_NATIVE_PARTITION_BOUND_EXCEEDED")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in pairs:
        grouped[_key(r)].append(r)
    closures = {}
    for group in grouped.values():
        old = [r for r in group if r["knowledge_to"] is not None]
        new = [r for r in group if r["knowledge_to"] is None]
        if len(old) != 1 or len(new) != 1:
            raise BoundedError("LAST_COMPONENT_NATIVE_UNIQUE_PAIR_REQUIRED")
        before = {**old[0], "knowledge_to": None}
        closed, _, _ = reconcile_current_pair(old[0], new[0], before)
        closures[digest(before)] = closed
    found: Counter[str] = Counter()
    result = []
    for r in original:
        sha = digest(r)
        if sha in closures:
            found[sha] += 1
            result.append(closures[sha])
        else:
            result.append(r)
    if found != Counter(dict.fromkeys(closures, 1)):
        raise BoundedError("LAST_COMPONENT_NATIVE_CURRENT_EXACT_ONCE_REQUIRED")
    proof = {
        "closed_current_versions": len(closures),
        "new_governance_versions": len(grouped),
        "input_row_census_sha256": digest(Counter(digest(r) for r in original)),
        "output_row_census_sha256": digest(Counter(digest(r) for r in result)),
        "unapproved_atoms_and_closed_intervals_unchanged": True,
    }
    proof["identity"] = digest(proof)
    return result, proof


def audit_last_components(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Replay the local retained source once, then scan every native partition."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != PURPOSE
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(
            body.get(k) is not False for k in ("READY", "complete_Base22", "complete_weekly_union")
        )
        or _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}
    ):
        raise BoundedError("LAST_COMPONENT_NATIVE_DESCRIPTOR_OR_FILE_SET_CHANGED")
    for rel, member in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if file_sha(p) != member["sha256"] or p.stat().st_size != member["size_bytes"]:
            raise BoundedError("LAST_COMPONENT_NATIVE_MEMBER_CHANGED")
        budget.check()
    source_root = root / "qualified-component-source"
    source = json.loads((source_root / "QUALIFICATION.json").read_bytes())
    if source["identity"] != body["component_source_identity"]:
        raise BoundedError("LAST_COMPONENT_NATIVE_RAW_SOURCE_IDENTITY_CHANGED")
    replay = audit_component_migrations(source_root, source, budget)
    history = json.loads((root / "evidence/native-source-descriptor.json").read_bytes())
    cold = json.loads((root / "evidence/native-source-cold.json").read_bytes())
    if (
        history["contract_id"] != HISTORY_ID
        or history["identity"] != body["native_source_identity"]
        or history["identity"] != digest({k: v for k, v in history.items() if k != "identity"})
        or cold["scope_identity"] != history["identity"]
        or not cold["source_unchanged"]
        or len(cold["results"]) != 4
        or any(i["old_source_reads"] or i["network_calls"] for i in cold["results"])
        or any(i["status"] != "EXPECTED_FAIL_CLOSED" for i in cold["results"][2:])
        or body["parents"] != history["parents"]
    ):
        raise BoundedError("LAST_COMPONENT_NATIVE_PREDECESSOR_ADMISSION_CHANGED")
    frozen = [b["output"] for b in history["history_bindings"].values()] + history["new_outputs"]
    if set(body["history_bindings"]) != {i["path"] for i in frozen}:
        raise BoundedError("LAST_COMPONENT_NATIVE_COMPLETE_PARTITION_SET_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    schema, rule = schemas(model, extension)[TABLE], contract["tables"][TABLE]

    def read(item: dict[str, Any], base: Path = root) -> list[dict[str, Any]]:
        pf = pq.ParquetFile(_physical(base, item["path"], independent=True))
        if (
            not pf.schema_arrow.equals(schema, check_metadata=False)
            or not 0 <= item["rows"] <= 65536
            or pf.metadata.num_rows != item["rows"]
        ):
            raise BoundedError("LAST_COMPONENT_NATIVE_SCHEMA_OR_CENSUS_CHANGED")
        return pf.read().to_pylist()  # type: ignore[no-any-return]

    raw_closure = [
        (item["original_member_sha256"], r)
        for item in source["raw_inputs"]
        for r in pq.ParquetFile(source_root / item["path"]).read().to_pylist()
    ]
    source_pairs = [
        r for i in source["stock_outputs"].values() if i["path"] for r in read(i, source_root)
    ]
    parents = {
        name: [
            r
            for i in source["parents"][name]
            for r in pq.ParquetFile(source_root / i["path"]).read().to_pylist()
        ]
        for name in ("dwd_security", "dwd_security_identifier")
    }
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    current = read(body["shared_current_before"])
    qualification = qualify_last_components(
        current,
        source_pairs,
        raw_closure=raw_closure,
        resolver=resolver,
        component_contract=load_component_contract(root / "contract"),
        rule=rule,
        observed_at=datetime.fromisoformat(body["observed_at"]),
        source_identity=source["identity"],
    )
    if qualification["identity"] != body["qualification_identity"]:
        raise BoundedError("LAST_COMPONENT_NATIVE_REPLAYED_QUALIFICATION_CHANGED")
    pairs = qualification["output"]
    old_keys = {_key(r) for r in pairs if r["knowledge_to"] is not None}
    expected_new = [r for r in pairs if r["knowledge_to"] is None]
    total, changed, healthy = 0, 0, 0
    found: Counter[str] = Counter()
    lineage: set[tuple[str, str, str]] = set()
    for rel, binding in body["history_bindings"].items():
        if file_sha(root / binding["before"]["path"]) != history["physical_members"][rel]["sha256"]:
            raise BoundedError("LAST_COMPONENT_NATIVE_SOURCE_BYTES_CHANGED")
        original = read(binding["before"])
        keys = {_key(r) for r in original if r["knowledge_to"] is None and _key(r) in old_keys}
        expected, proof = merge_last_component_partition(
            original, [r for r in pairs if _key(r) in keys]
        )
        actual = read(binding["output"])
        if (
            proof != binding["proof"]
            or {partition(r, rule) for r in original + actual} != {binding["partition"]}
            or Counter(digest(r) for r in actual) != Counter(digest(r) for r in expected)
            or semantic_scan([root / binding["output"]["path"]], schema, check=budget.check)
            != binding["output"]["semantic_scan"]
        ):
            raise BoundedError("LAST_COMPONENT_NATIVE_UNAPPROVED_ATOM_OR_PARTITION_CHANGE")
        found.update(keys)
        total += len(actual)
        changed += bool(keys)
        healthy += not keys
        if not keys and file_sha(root / binding["before"]["path"]) != file_sha(
            root / binding["output"]["path"]
        ):
            raise BoundedError("LAST_COMPONENT_NATIVE_HEALTHY_BYTES_CHANGED")
        lineage.update(
            (r["record_source"], r["source_revision"], r["source_artifact_sha256"]) for r in actual
        )
    new = [r for i in body["new_outputs"] for r in read(i)]
    if (
        found != Counter(dict.fromkeys(old_keys, 1))
        or Counter(digest(r) for r in new) != Counter(digest(r) for r in expected_new)
        or total != history["output_rows"]
        or total + len(new) != body["output_rows"]
    ):
        raise BoundedError("LAST_COMPONENT_NATIVE_EXACT_GLOBAL_CENSUS_CHANGED")
    for i in body["new_outputs"]:
        if {partition(r, rule) for r in read(i)} != {i["partition"]}:
            raise BoundedError("LAST_COMPONENT_NATIVE_NEW_PARTITION_CHANGED")
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
                    raise BoundedError("LAST_COMPONENT_NATIVE_PARENT_BYTES_CHANGED")
            scan.add(parent, [root / i["path"] for i in items])
        for parent, field in (
            ("dwd_listed_company", "company_id"),
            ("dwd_security", "security_id"),
        ):
            scan.reject(
                f"SELECT 1 FROM {TABLE} h WHERE NOT EXISTS(SELECT 1 FROM {parent} p "
                f"WHERE p.{field}=h.{field}) LIMIT 1",
                "LAST_COMPONENT_NATIVE_PARENT_CLOSURE_FAILED",
            )
    receipt = {
        "status": "ALL_NATIVE_RETAINED_SIX_SINGLE_GROUP_HISTORY_PASS_NOT_COMPLETE_WEEKLY_OR_BASE22",
        "scope_identity": body["identity"],
        "output_rows": total + len(new),
        "closed_current_versions": len(old_keys),
        "new_governance_versions": len(new),
        "changed_native_files": changed,
        "healthy_byte_exact_native_files": healthy,
        "dispositions": qualification["counts"],
        "source_raw_replay_sha256": digest(replay),
        "complete_Base22": False,
    }
    receipt["identity"] = digest(receipt)
    return receipt
