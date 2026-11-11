"""Offline exact disclosure union scans; never dereference archived source roots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, Member, semantic_scan
from .base22_disclosure_union import CONTRACT_ID, ENTITIES, MAX_ADDITIONS, observation_census
from .base22_output_audit import audit_observation_stage


def audit_disclosure_union(
    root: Path, descriptor: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Replay all selected additions, native column streams and scoped references.

    Whole-native entity absence was verified at construction over its frozen full
    inventory. This limited July/August artifact cannot independently reproduce
    that census for unmaterialized historical partitions or full33 acceptance.
    """
    if (
        descriptor.get("projection_contract_id") != CONTRACT_ID
        or descriptor.get("full33_candidate_acceptance") is not False
        or descriptor.get("complete_Base22") is not False
        or set(descriptor.get("output_rows", {}))
        != {*ENTITIES, "dwd_security", "dwd_listed_company"}
        or set(descriptor.get("source_registries", {})) != set(descriptor.get("output_rows", {}))
    ):
        raise BoundedError("DISCLOSURE_OFFLINE_SCOPE_INVALID")
    stage = root / "observations" / descriptor["label"]
    full = audit_observation_stage(root, descriptor, stage / "contract")
    contract, model, extension = load_contract(stage / "contract")
    ss = schemas(model, extension)
    archives = {a["path"]: a for a in descriptor["archived_true_source_observations"]}
    if len(archives) != len(descriptor["archived_true_source_observations"]):
        raise BoundedError("DISCLOSURE_REPEATED_ARCHIVE")
    expected_outputs = {o["path"]: o for o in descriptor["outputs"] if o["table"] in ENTITIES}
    proofs = descriptor["exact_union_proofs"]
    if (
        len(proofs) != len(expected_outputs)
        or {p["output_local_member"] for p in proofs} != set(expected_outputs)
        or len({p["path"] for p in proofs}) != len(proofs)
    ):
        raise BoundedError("DISCLOSURE_OFFLINE_EXACT_PROOF_SET_INVALID")

    def local_member(relative: str, role: str) -> Member:
        item = archives[relative]
        path = _physical(root, relative, independent=True)
        if item["role"] != role or path.stat().st_size != item["size_bytes"]:
            raise BoundedError("DISCLOSURE_LOCAL_MEMBER_ROLE_OR_BYTES_INVALID")
        return Member(path, item["sha256"], item["size_bytes"], item["rows"])

    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    donor_items = [a for a in archives.values() if a["role"] == "source_scoped_descriptor"]
    if len(donor_items) != 1:
        raise BoundedError("DISCLOSURE_SOURCE_DESCRIPTOR_SET_INVALID")
    donor_path = _physical(root, donor_items[0]["path"], independent=True)
    donor = json.loads(donor_path.read_bytes())
    if (
        file_sha(donor_path) != descriptor["source_scoped_descriptor_sha256"]
        or donor.get("identity") != descriptor["source_scoped_identity"]
        or donor.get("identity") != digest({k: v for k, v in donor.items() if k != "identity"})
        or donor.get("kind") != "sealed_actual_domain_observations_not_complete_base22"
        or donor.get("consumer_admission") != "NOT_GRANTED"
        or donor.get("label") != "announcements-publication-revision-v1"
    ):
        raise BoundedError("DISCLOSURE_SOURCE_SCOPED_DESCRIPTOR_INVALID")
    donor_members = {(a["table"], a["partition"]): a for a in donor["outputs"]}
    native_entities: dict[str, set[str]] = {t: set() for t in ENTITIES}
    for p in proofs:
        for relative in p["native_local_members"]:
            member = local_member(relative, "native_selected_partition")
            member.verify()
            for batch in pq.ParquetFile(member.path).iter_batches(
                columns=[ENTITIES[p["table"]]], batch_size=1024
            ):
                native_entities[p["table"]].update(batch.column(0).to_pylist())
    totals = {t: 0 for t in ENTITIES}
    held = {t: 0 for t in ENTITIES}
    for entry in proofs:
        path = _physical(root, entry["path"], independent=True)
        if file_sha(path) != entry["sha256"]:
            raise BoundedError("DISCLOSURE_LOCAL_PROOF_HASH_MISMATCH")
        proof = json.loads(path.read_bytes())
        t = entry["table"]
        from . import base22_disclosure_union

        if (
            proof.get("identity") != digest({k: v for k, v in proof.items() if k != "identity"})
            or proof.get("identity") != entry["proof_identity"]
            or proof.get("contract_id") != CONTRACT_ID
            or proof.get("consumer_admission") != "NOT_GRANTED"
            or proof.get("table") != t
            or proof.get("partition") != entry["partition"]
            or proof.get("configuration_sha256")
            != digest({"schema": str(ss[t]), "rule": contract["tables"][t]})
            or proof.get("evaluator_sha256") != file_sha(Path(base22_disclosure_union.__file__))
            or proof.get("source_qualification_sha256")
            != descriptor["source_scoped_descriptor_sha256"]
        ):
            raise BoundedError("DISCLOSURE_LOCAL_PROOF_INVALID")
        native = [
            local_member(r, "native_selected_partition") for r in entry["native_local_members"]
        ]
        additions = local_member(entry["additions_local_member"], "exact_selected_additions")
        if (
            [(m.sha256, m.size_bytes, m.rows) for m in native]
            != [(m["sha256"], m["size_bytes"], m["rows"]) for m in proof["native"]]
            or (additions.sha256, additions.size_bytes, additions.rows)
            != tuple(proof["additions"][k] for k in ["sha256", "size_bytes", "rows"])
            or observation_census(additions, schema=ss[t], rule=contract["tables"][t])
            != proof["added_row_census_sha256"]
        ):
            raise BoundedError("DISCLOSURE_OFFLINE_INPUT_CENSUS_MISMATCH")
        incoming = local_member(entry["incoming_local_member"], "original_qualified_partition")
        donor_member = donor_members[t, entry["partition"]]
        if not 0 < incoming.rows <= MAX_ADDITIONS or (
            incoming.sha256,
            incoming.rows,
            incoming.size_bytes,
        ) != (donor_member["sha256"], donor_member["rows"], donor_member["size_bytes"]):
            raise BoundedError("DISCLOSURE_BOUND_INCOMING_SOURCE_CHANGED")
        keep_hashes: list[str] = []
        held_hashes: list[str] = []
        for batch in pq.ParquetFile(incoming.path).iter_batches(batch_size=1024):
            budget.check()
            for row in batch.to_pylist():
                if partition(row, contract["tables"][t]) != entry["partition"]:
                    raise BoundedError("DISCLOSURE_BOUND_PHYSICAL_PARTITION_MISMATCH")
                target = held_hashes if row[ENTITIES[t]] in native_entities[t] else keep_hashes
                target.append(digest(row))
        if (
            len(keep_hashes) != additions.rows
            or digest(sorted(keep_hashes)) != proof["added_row_census_sha256"]
            or len(held_hashes) != entry["held_existing_entity_rows"]
            or digest(sorted(held_hashes)) != entry["held_row_census_sha256"]
        ):
            raise BoundedError("DISCLOSURE_OFFLINE_ABSENT_SELECTION_MISMATCH")
        expected = semantic_scan([m.path for m in [*native, additions]], ss[t], check=budget.check)
        output = _physical(root, entry["output_local_member"], independent=True)
        if semantic_scan([output], ss[t], check=budget.check) != expected:
            raise BoundedError("DISCLOSURE_NATIVE_OR_ADDED_TYPED_STREAM_CHANGED")
        totals[t] += additions.rows
        held[t] += len(held_hashes)
    with GlobalScan(budget) as scan:
        for t in sorted(descriptor["output_rows"]):
            paths = [
                _physical(root, o["path"], independent=True)
                for o in descriptor["outputs"]
                if o["table"] == t
            ]
            scan.add(t, paths)
            rule = contract["tables"][t]
            scan.keys(t, rule["business_key"], rule["version_key"])
            scan.row_rules(t, ss[t], rule)
            scan.lineage(t, [tuple(v) for v in descriptor["source_registries"][t]])
        scan.reject(
            "SELECT 1 FROM dwd_disclosure_subject r WHERE NOT EXISTS "
            "(SELECT 1 FROM dwd_disclosure p WHERE p.disclosure_id=r.disclosure_id "
            "AND p.knowledge_from<=r.knowledge_from AND "
            "(p.knowledge_to IS NULL OR r.knowledge_from<p.knowledge_to)) LIMIT 1",
            "DISCLOSURE_PARENT_KNOWLEDGE_CLOSURE",
        )
        for t, col, parent, key in [
            ("dwd_disclosure", "company_id", "dwd_listed_company", "company_id"),
            ("dwd_disclosure_subject", "security_id", "dwd_security", "security_id"),
        ]:
            scan.reject(
                f"SELECT 1 FROM {t} r WHERE r.{col} IS NOT NULL AND NOT EXISTS "
                f"(SELECT 1 FROM {parent} p WHERE p.{key}=r.{col} "
                "AND p.knowledge_from<=r.knowledge_from AND "
                "(p.knowledge_to IS NULL OR r.knowledge_from<p.knowledge_to) "
                "AND p.effective_from<=r.effective_at AND "
                "(p.effective_to IS NULL OR r.effective_at<p.effective_to)) LIMIT 1",
                "DISCLOSURE_DATED_IDENTITY_CLOSURE",
            )
    return {
        "status": "OFFLINE_SCOPED_UNION_PASS_NOT_COMPLETE_DOMAIN_OR_BASE22",
        "scope_identity": descriptor["identity"],
        "rows": full["tables"],
        "added_versions": totals,
        "held_existing_entity_observations": held,
        "native_typed_columns_preserved": True,
        "all_scoped_global_keys_versions_parents_lineage": "PASS",
        "whole_unmaterialized_native_entity_census_independently_repeated": False,
        "all33_foreign_keys_or_image_acceptance": False,
        "original_source_root_dereferences": 0,
    }
