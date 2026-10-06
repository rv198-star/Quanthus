"""Offline raw replay and global closure of the bounded history dependency.

Only image-local paths are opened. This validates new historical versions and
preserved before atoms; unresolved identities remain unresolved. It cannot admit
the complete 33-table image, historical PIT or legacy normalization evidence.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_action_union import select_uncontested_actions, selected_rows
from .base22_bounded import BoundedError, Budget, GlobalScan, Member, semantic_scan
from .base22_canonical_action_revision import (
    CONTRACT_ID,
    MAX_PARTITION_ROWS,
    choose_successor_sources,
    revise_canonical_action,
    validate_revision_partition,
)
from .base22_normalization_sources import (
    replay_f10_source,
    replay_factor_source,
    replay_implementation_source,
)
from .base22_outputs import instant
from .base22_sources import _tree
from .disclosure_event import _stable_id

TABLES = ("dwd_corporate_action", "dwd_corporate_action_term")
PARENTS = (
    "dwd_listed_company",
    "dwd_security",
    "dwd_security_identifier",
    "dwd_trading_venue",
)


def verify_local_closure(root: Path, body: dict[str, Any], check: Callable[[], None]) -> None:
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose")
        != "QUALIFIED_NORMALIZATION_SUCCESSOR_HISTORY_TMP_WITH_EXPLICIT_UNRESOLVED_KEYS"
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(
            body.get(k) is not False
            for k in (
                "READY",
                "historical_PIT_admission",
                "full_original_normalization_restored",
                "positive_source_priority_granted",
                "all_history_or_August33_or_Base22_qualified",
            )
        )
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
    ):
        raise BoundedError("SUCCESSOR_HISTORY_SCOPE_INVALID")
    if set(_tree(root)) != {*body["physical_members"], "QUALIFICATION.json"}:
        raise BoundedError("SUCCESSOR_HISTORY_EXACT_PHYSICAL_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if p.stat().st_size != item["size_bytes"] or file_sha(p) != item["sha256"]:
            raise BoundedError("SUCCESSOR_HISTORY_LOCAL_MEMBER_BYTES_CHANGED")
        check()


def _json(root: Path, relative: str) -> dict[str, Any]:
    value = json.loads(_physical(root, relative, independent=True).read_bytes())
    if not isinstance(value, dict):
        raise BoundedError("SUCCESSOR_HISTORY_OBJECT_REQUIRED")
    return value


def _rows(root: Path, item: dict[str, Any], budget: Budget) -> Iterator[dict[str, Any]]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    p = _physical(root, item["path"], independent=True)
    if file_sha(p) != item["sha256"]:
        raise BoundedError("SUCCESSOR_HISTORY_MEMBER_CHANGED")
    total = 0
    for batch in pq.ParquetFile(p).iter_batches(batch_size=1024):
        budget.check()
        for row in batch.to_pylist():
            total += 1
            yield row
    if total != item["rows"]:
        raise BoundedError("SUCCESSOR_HISTORY_PHYSICAL_ROW_COUNT_CHANGED")


def audit_history_action_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Full independent raw replay, before conservation and global native scan."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    verify_local_closure(root, body, budget.check)
    reg = _json(root, "evidence/source-history-registration.json")
    code = _json(root, "evidence/execution-code-closure.json")
    plan = _json(root, "evidence/actual-history-write-registration.json")
    safe = _json(root, "evidence/full-history-safe-conservation.json")
    for value in (reg, code, plan, safe):
        if value["identity"] != digest({k: v for k, v in value.items() if k != "identity"}):
            raise BoundedError("SUCCESSOR_HISTORY_LOCAL_EVIDENCE_IDENTITY_CHANGED")
    if (
        reg["identity"] != body["source_registration_identity"]
        or code["identity"] != body["code_closure_identity"]
        or code["HEAD"] != body["HEAD"]
        or code["source_registration_identity"] != reg["identity"]
        or file_sha(root / "evidence/source-history-registration.json")
        != code["source_registration_SHA256"]
        or plan["source_registration_identity"] != reg["identity"]
        or plan["code_closure_identity"] != code["identity"]
        or safe["identity"] != body["full_safe_conservation_identity"]
        or set(body["source_dependencies"]) != {"EM", "F10", "factor", "quantity"}
        or set(body["output_rows"]) != set(TABLES)
    ):
        raise BoundedError("SUCCESSOR_HISTORY_INPUT_CODE_SCOPE_CLOSURE_CHANGED")
    expected = {
        "sources/original-canonical-actions.parquet",
        "evidence/source-history-registration.json",
        "evidence/execution-code-closure.json",
        "evidence/actual-history-write-registration.json",
        "evidence/full-history-safe-conservation.json",
        "evidence/successor-proofs-safe.json",
    }
    donors = {}
    for label, item in reg["inputs"].items():
        local = "source-dependencies/" + label
        archived = body["source_dependencies"][label]
        if archived != dict(item, local_root=local):
            raise BoundedError("SUCCESSOR_HISTORY_DONOR_SCOPE_CHANGED")
        for rel, member in item["physical_members"].items():
            path = local + "/" + rel
            if body["physical_members"].get(path) != member:
                raise BoundedError("SUCCESSOR_HISTORY_DONOR_PHYSICAL_CLOSURE_CHANGED")
            expected.add(path)
        donor_path = local + "/" + item["descriptor"]
        if file_sha(root / donor_path) != item["descriptor_sha256"]:
            raise BoundedError("SUCCESSOR_HISTORY_DONOR_DESCRIPTOR_CHANGED")
        donor = _json(root, donor_path)
        if donor["identity"] != item["identity"] or donor["identity"] != digest(
            {k: v for k, v in donor.items() if k != "identity"}
        ):
            raise BoundedError("SUCCESSOR_HISTORY_DONOR_IDENTITY_CHANGED")
        donors[label] = donor
    for item in reg["metadata"].values():
        p = "evidence/frozen-inputs/" + Path(item["path"]).name
        if file_sha(root / p) != item["sha256"]:
            raise BoundedError("SUCCESSOR_HISTORY_FROZEN_SELECTION_CHANGED")
        expected.add(p)
    for name, sha in reg["configuration_SHA256"].items():
        p = "contract/" + name
        if file_sha(root / p) != sha:
            raise BoundedError("SUCCESSOR_HISTORY_CONFIGURATION_CHANGED")
        expected.add(p)
    output_map = {(i["table"], i.get("partition")): i for i in body["outputs"]}
    if len(output_map) != len(body["outputs"]) or set(i["table"] for i in body["outputs"]) != {
        *TABLES,
        *PARENTS,
    }:
        raise BoundedError("SUCCESSOR_HISTORY_EXACT_TABLE_PARTITION_SET_CHANGED")
    expected.update(i["path"] for i in body["outputs"])
    if expected != set(body["physical_members"]):
        raise BoundedError("SUCCESSOR_HISTORY_REGISTERED_PHYSICAL_CLOSURE_CHANGED")
    raw = dict(reg["original_raw"], path="sources/original-canonical-actions.parquet")
    if raw != body["raw_member"]:
        raise BoundedError("SUCCESSOR_HISTORY_ORIGINAL_RAW_IDENTITY_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    receipts, loaded = {}, {}
    for label, replay in (
        ("EM", replay_implementation_source),
        ("F10", replay_f10_source),
        ("factor", replay_factor_source),
    ):
        receipt, actions, terms = replay(
            root / ("source-dependencies/" + label), donors[label], budget
        )
        receipts[label] = receipt
        loaded[label] = actions, terms
    if receipts != body["source_replays"] or receipts != safe["source_replays"]:
        raise BoundedError("SUCCESSOR_HISTORY_COMPLETE_RAW_REPLAY_CHANGED")

    def frozen(name: str) -> Any:
        p = "evidence/frozen-inputs/" + Path(reg["metadata"][name]["path"]).name
        return json.loads((root / p).read_bytes())

    em = donors["EM"]
    lineage = next(
        i
        for i in em["archived_true_source_observations"]
        if i.get("role") == "original_lineage_not_raw_values"
    )
    required = list(_rows(root / "source-dependencies/EM", lineage, budget))
    conflicts = {r["old_action_id"] for r in frozen("27cash_conflicts")} | {
        r["key"]["action_id"] for r in frozen("48cash_conflicts")
    }
    summary = _json(root / "source-dependencies/EM", em["safe_summary_path"])
    prelisting = {r["old_action_id"] for r in summary["rejected"]}
    bindings = {r["key"]["action_id"]: r for r in frozen("F10_factor2927bindings")}
    chosen, held = choose_successor_sources(
        required, loaded, bindings, cash_conflicts=conflicts, prelisting=prelisting
    )
    if (
        sorted(chosen) != body["selected_original_keys"]
        or held != body["held_original_keys"]
        or held != safe["held"]
        or len(chosen) != reg["expected_required_scope"]["positive_new_canonical_versions"]
    ):
        raise BoundedError("SUCCESSOR_HISTORY_EXACT_SELECTED_AND_HELD_KEYS_CHANGED")
    clock = instant(body["observed_at"])
    semantic = digest(
        {
            "source_registration_identity": reg["identity"],
            "code_closure_identity": code["identity"],
            "configuration_SHA256": reg["configuration_SHA256"],
            "complete_source_replays": receipts,
            "selected_exact_raw_source_keys_sha256": digest(sorted(chosen)),
            "held_exact_original_keys_sha256": digest(held),
            "observed_at": clock,
        }
    )
    if (
        body["revision"] != CONTRACT_ID + ":" + semantic
        or body["revision"] != plan["revision"]
        or body["observed_at"] != plan["observed_at"]
        or safe["actual_new_knowledge"] != body["observed_at"]
    ):
        raise BoundedError("SUCCESSOR_HISTORY_REVISION_SEMANTIC_CLOSURE_CHANGED")
    raw_index: dict[str, dict[str, Any]] = {}
    for row in _rows(root, raw, budget):
        if row["action_id"] in raw_index or len(raw_index) >= MAX_PARTITION_ROWS:
            raise BoundedError("SUCCESSOR_HISTORY_RAW_DUPLICATE_OR_BOUND_EXCEEDED")
        raw_index[row["action_id"]] = row
    wanted = {
        _stable_id("corporate_action", "corporate_actions", raw_index[k]["source_row_key"]): k
        for k in chosen
    }
    quantity = donors["quantity"]
    quantity_root = root / "source-dependencies/quantity"
    native = {t: [i for i in quantity["outputs"] if i["table"] == t] for t in TABLES}
    before_entities = {}
    for item in native[TABLES[0]]:
        for row in _rows(quantity_root, item, budget):
            key = row["corporate_action_id"]
            if key in wanted:
                if key in before_entities:
                    raise BoundedError("SUCCESSOR_HISTORY_BEFORE_ENTITY_NOT_UNIQUE")
                before_entities[key] = row
    if set(before_entities) != set(wanted):
        raise BoundedError("SUCCESSOR_HISTORY_ORIGINAL_ENTITY_CLOSURE_MISSING")
    adds: dict[str, Any] = {t: defaultdict(list) for t in TABLES}
    proof_hashes = []
    for parent, key in wanted.items():
        label, donor, terms = chosen[key]
        binding = {
            "source_scope_identity": reg["inputs"][label]["identity"],
            "raw_replay_receipt_sha256": receipts[label]["identity"],
            "original_raw_row_sha256": digest(raw_index[key]),
            "original_raw_member_sha256": raw["sha256"],
            "qualified_action_sha256": digest(donor),
            "qualified_term_rows_sha256": sorted(digest(r) for r in terms),
            "disposition": "UNCONTESTED_RAW_SOURCE_BOUND_SUCCESSOR",
            "historical_PIT_admission": False,
            "old_manifest_restored": False,
        }
        a, ts, proof = revise_canonical_action(
            raw_index[key],
            before_entities[parent],
            donor,
            terms,
            binding=binding,
            observed_at=clock,
            revision=body["revision"],
        )
        proof_hashes.append(
            digest({"old_action_id": key, "old_entity_id": parent, "label": label, "proof": proof})
        )
        for t, rows in ((TABLES[0], [a]), (TABLES[1], ts)):
            typed = pa.Table.from_pylist(rows, schema=ss[t]).to_pylist()
            if typed != rows:
                raise BoundedError("SUCCESSOR_HISTORY_TYPED_NEW_ATOM_CHANGED")
            for row in typed:
                adds[t][partition(row, contract["tables"][t])].append(row)
        budget.check()
    stored = json.loads((root / "evidence/successor-proofs-safe.json").read_bytes())
    if proof_hashes != [digest(p) for p in stored]:
        raise BoundedError("SUCCESSOR_HISTORY_RECOMPUTED_RAW_BEFORE_NEW_PROOF_CHANGED")

    def members(base: Path, items: list[dict[str, Any]]) -> list[Member]:
        return [
            Member(base / i["path"], i["sha256"], (base / i["path"]).stat().st_size, i["rows"])
            for i in items
        ]

    froot = root / "source-dependencies/F10"
    selection = select_uncontested_actions(
        members(quantity_root, native[TABLES[0]]),
        members(froot, [i for i in donors["F10"]["outputs"] if i["table"] == TABLES[0]]),
        members(
            root / "source-dependencies/EM", [i for i in em["outputs"] if i["table"] == TABLES[0]]
        ),
        schema=ss[TABLES[0]],
        budget=budget,
    )
    if (
        selection["selected_entity_ids"] != body["uncontested_selected_entities"]
        or selection["selected_entity_ids"]
        != frozen("uncontested5024selection")["selected_entity_ids"]["F10"]
    ):
        raise BoundedError("SUCCESSOR_HISTORY_WHOLE_UNCONTESTED_SELECTION_CHANGED")
    extensions: dict[str, Any] = {t: defaultdict(list) for t in TABLES}
    for t in TABLES:
        for row in selected_rows(
            members(froot, [i for i in donors["F10"]["outputs"] if i["table"] == t]),
            selected_ids=set(selection["selected_entity_ids"]),
            schema=ss[t],
            budget=budget,
        ):
            extensions[t][partition(row, contract["tables"][t])].append(row)
    totals: Counter[str] = Counter()
    partition_proofs = []
    scans = {}
    for t in TABLES:
        grouped: dict[str, Any] = defaultdict(list)
        for i in native[t]:
            file = pq.ParquetFile(quantity_root / i["path"])
            first = next(file.iter_batches(batch_size=1)).to_pylist()[0]
            grouped[partition(first, contract["tables"][t])].append(i)
        parts = set(grouped) | set(adds[t]) | set(extensions[t])
        if {part for table, part in output_map if table == t} != parts:
            raise BoundedError("SUCCESSOR_HISTORY_PHYSICAL_PARTITION_SET_CHANGED")
        for part in sorted(parts):
            old: list[dict[str, Any]] = []
            for i in grouped.get(part, []):
                old.extend(_rows(quantity_root, i, budget))
                if len(old) > MAX_PARTITION_ROWS:
                    raise BoundedError("SUCCESSOR_HISTORY_PARTITION_BOUND_EXCEEDED")
            out = output_map[t, part]
            if out["path"] != f"payload/{t}/{part}/part-history.parquet":
                raise BoundedError("SUCCESSOR_HISTORY_PHYSICAL_PARTITION_PATH_CHANGED")
            after = list(_rows(root, out, budget))
            selected_here = {
                r["corporate_action_id"] for r in old if r["corporate_action_id"] in wanted
            }
            rewritten = [
                dict(r, knowledge_to=clock) if r["corporate_action_id"] in wanted else r
                for r in old
            ] + adds[t].get(part, [])
            conservation = validate_revision_partition(
                old,
                rewritten,
                selected_here,
                adds[t].get(part, []),
                observed_at=clock,
                action=t == TABLES[0],
            )
            extra = extensions[t].get(part, [])
            if Counter(digest(r) for r in after) != Counter(
                digest(r) for r in rewritten + extra
            ) or any(partition(r, contract["tables"][t]) != part for r in after):
                raise BoundedError("SUCCESSOR_HISTORY_ALLOWED_COLUMNS_KEYS_CLOCKS_CHANGED")
            partition_proofs.append(
                {
                    "table": t,
                    "partition": part,
                    "before_files": [
                        dict(i, path="source-dependencies/quantity/" + i["path"])
                        for i in grouped.get(part, [])
                    ],
                    "conservation": conservation,
                    "uncontested_added": len(extra),
                    "uncontested_original_row_census_sha256": digest(
                        sorted(digest(r) for r in extra)
                    ),
                    "output_sha256": out["sha256"],
                }
            )
            totals[t] += len(after)
    if dict(totals) != body["output_rows"] or partition_proofs != safe["partition_proofs"]:
        raise BoundedError("SUCCESSOR_HISTORY_COMPLETE_BEFORE_AFTER_CENSUS_CHANGED")
    for t in PARENTS:
        actual = [i for i in body["outputs"] if i["table"] == t]
        original = [
            dict(i, path="source-dependencies/quantity/" + i["path"])
            for i in quantity["outputs"]
            if i["table"] == t
        ]
        if actual != original:
            raise BoundedError("SUCCESSOR_HISTORY_PARENT_IDENTITY_CHANGED")
    scoped = set(TABLES + PARENTS)
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for t in sorted(scoped):
            paths = [root / i["path"] for i in body["outputs"] if i["table"] == t]
            for p in paths:
                scans[str(p.relative_to(root))] = semantic_scan(
                    [p], ss[t], batch_rows=1024, check=budget.check
                )
            scan.add(t, paths)
            scan.row_rules(t, ss[t], contract["tables"][t])
            scan.keys(
                t, contract["tables"][t]["business_key"], contract["tables"][t]["version_key"]
            )
        scan.references(
            {"tables": [t for t in model["tables"] if t["name"] in scoped]},
            {"tables": {t: contract["tables"][t] for t in scoped}},
        )
        scan.reject(
            """SELECT 1 FROM dwd_corporate_action_term t WHERE NOT EXISTS
            (SELECT 1 FROM dwd_corporate_action a WHERE a.corporate_action_id=t.corporate_action_id
             AND a.knowledge_from=t.knowledge_from
             AND a.effective_at=t.effective_at
             AND a.record_source=t.record_source AND a.source_revision=t.source_revision
             AND a.source_record_id=t.source_record_id
             AND a.source_artifact_sha256=t.source_artifact_sha256) LIMIT 1""",
            "SUCCESSOR_HISTORY_TERM_EXACT_SOURCE_KNOWLEDGE_PARENT_FAILED",
        )
    result = {
        "status": "FULL_LOCAL_RAW_BEFORE_ALLOWED_VERSION_AND_SCOPED_GLOBAL_PASS_NOT_BASE22",
        "scope_identity": body["identity"],
        "selected_new_actions": len(chosen),
        "held_keys": len(held),
        "uncontested_entities": len(selection["selected_entity_ids"]),
        "output_rows": dict(totals),
        "all_output_semantic_scans": scans,
        "source_replays": receipts,
        "partition_proof_census_sha256": digest(partition_proofs),
        "raw_before_new_proof_census_sha256": digest(proof_hashes),
        "historical_PIT_admission": False,
        "original_normalization_restored": False,
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result
