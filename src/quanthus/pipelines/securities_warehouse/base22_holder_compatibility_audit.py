"""Offline audit of scoped legacy-compatible holder governance revisions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, Member, semantic_scan
from .base22_holder_compatibility import qualify_legacy_capture
from .base22_holder_exact import CONTRACT_ID, TABLE, exact_holder_observation, load_holder_contract
from .base22_holder_revision import validate_holder_revision_partition
from .base22_outputs import instant
from .base22_sources import _tree, physical_path


def audit_compatible_holder_scope(
    root: Path, descriptor: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Read only complete local raw, before files, captures and physically owned outputs.

    Archival original paths are never dereferenced. A kernel read-only mount and
    a disjoint writable budget are required by GlobalScan, not replaced by Python
    path auditing. This is not full warehouse or consumer/PIT acceptance.
    """
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    body = {k: v for k, v in descriptor.items() if k != "identity"}
    if descriptor.get("identity") != digest(body):
        raise BoundedError("COMPATIBLE_HOLDER_DESCRIPTOR_IDENTITY_INVALID")
    required_disposition = {
        "kind": "sealed_scoped_compatible_holder_governance_revision_NOT_COMPLETE_BASE22",
        "label": "holder-binary-compatible-new-governance-version",
        "old_body_changes": 0,
        "old_exact13_changes": 0,
        "old_publication_atoms_preserved": True,
        "new_publication_unknown": True,
        "original_4173_visibility": "UNCHANGED_UNAVAILABLE",
        "full_image_schema_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "whole33_or_Base22_accepted": False,
        "READY": False,
    }
    if any(
        type(descriptor.get(k)) is not type(v) or descriptor.get(k) != v
        for k, v in required_disposition.items()
    ):
        raise BoundedError("COMPATIBLE_HOLDER_SCOPE_OR_DISPOSITION_INVALID")
    stage = root / "observations" / descriptor["label"]
    wanted = set(descriptor["physical_members"]) | {
        (stage / "OBSERVATIONS.json").relative_to(root).as_posix()
    }
    actual = {(stage / p).relative_to(root).as_posix() for p in _tree(stage)}
    if actual != wanted:
        raise BoundedError("COMPATIBLE_HOLDER_PHYSICAL_SET_MISMATCH")
    for relative, meta in descriptor["physical_members"].items():
        path = physical_path(root / relative)
        if path.stat().st_size != meta["size_bytes"] or file_sha(path) != meta["sha256"]:
            raise BoundedError("COMPATIBLE_HOLDER_MEMBER_HASH_MISMATCH")
        budget.check()
    config = stage / "contract"
    contract, model, extension = load_contract(config)
    ss = schemas(model, extension)
    _, capture_schema = load_holder_contract(config)
    schema = ss[TABLE].append(pa.field("average_holding_exact", pa.decimal256(41, 13)))
    archive = descriptor["archives"]

    def local(role: str) -> Path:
        selected = [f for f in archive if f["role"] == role]
        if len(selected) != 1:
            raise BoundedError("COMPATIBLE_HOLDER_ARCHIVE_ROLE_INVALID")
        return physical_path(root / selected[0]["path"])

    cap_desc = json.loads(local("capture_descriptor").read_bytes())
    cap_body = {k: v for k, v in cap_desc.items() if k != "identity"}
    if cap_desc.get("identity") != digest(cap_body):
        raise BoundedError("COMPATIBLE_HOLDER_RAW_DESCRIPTOR_IDENTITY_INVALID")
    indices = {b["raw_row_index"] for b in cap_desc["raw_origin_bindings"]}
    if not indices or len(indices) > 32768 or any(type(i) is not int or i < 0 for i in indices):
        raise BoundedError("COMPATIBLE_HOLDER_RAW_INDEX_BOUND_INVALID")
    if len(indices) != descriptor["qualified_legacy_binary_pairs"]:
        raise BoundedError("COMPATIBLE_HOLDER_EXACT_RAW_INDEX_CENSUS_INVALID")
    incoming: dict[str, Any] = {}
    offset = 0
    for batch in pq.ParquetFile(local("exact_capture_raw")).iter_batches(batch_size=1024):
        for i, row in enumerate(batch.to_pylist()):
            if offset + i in indices:
                key = f"stock_code={row['code']}&period_end={row['end_date'][:10]}"
                if key in incoming:
                    raise BoundedError("COMPATIBLE_HOLDER_DUPLICATE_RAW_KEY")
                incoming[key] = row
        offset += batch.num_rows
        budget.check()
    old_raw: dict[str, Any] = {}
    for batch in pq.ParquetFile(local("legacy_raw")).iter_batches(batch_size=1024):
        for row in batch.to_pylist():
            key = f"stock_code={row['code']}&period_end={row['end_date'][:10]}"
            if key in incoming:
                if key in old_raw:
                    raise BoundedError("COMPATIBLE_HOLDER_DUPLICATE_LEGACY_RAW")
                old_raw[key] = row
        budget.check()
    versions: dict[str, list[dict[str, Any]]] = {}
    for item in archive:
        if item["role"] != "current_output" or item["table"] != TABLE:
            continue
        for batch in pq.ParquetFile(root / item["path"]).iter_batches(batch_size=1024):
            for row in batch.to_pylist():
                if row["source_record_id"] in incoming:
                    versions.setdefault(row["source_record_id"], []).append(row)
            budget.check()
    legacy_sha = file_sha(local("legacy_raw"))
    qualified = []
    retained = {}
    for key, rows in versions.items():
        first = min(rows, key=lambda r: r["knowledge_from"])
        if first["source_artifact_sha256"] != legacy_sha:
            raise BoundedError("COMPATIBLE_HOLDER_OLD_RAW_MEMBER_BINDING_INVALID")
        qualified.append(qualify_legacy_capture(old_raw[key], first, incoming[key]))
        if len(rows) == 1 and first["knowledge_to"] is None:
            retained[key] = first
    qualification = json.loads((stage / "QUALIFICATION.json").read_bytes())
    if (
        set(versions) != set(incoming)
        or len(retained) != descriptor["new_versions"]
        or sorted(digest(q) for q in qualified)
        != sorted(digest(q) for q in qualification["compatibility_receipts"])
    ):
        raise BoundedError("COMPATIBLE_HOLDER_FULL_QUALIFICATION_CENSUS_MISMATCH")
    observed = instant(descriptor["observed_at"])
    revision = qualification["selection"]["new_governance_revision"]
    raw_sha = file_sha(local("exact_capture_raw"))
    expected = []
    for key, old in sorted(retained.items()):
        if observed <= old["knowledge_from"]:
            raise BoundedError("COMPATIBLE_HOLDER_GOVERNANCE_CLOCK_NOT_LATER")
        expected.extend(
            exact_holder_observation(
                incoming[key],
                company_id=old["company_id"],
                security_id=old["security_id"],
                parent_knowledge=observed,
                source_sha256=raw_sha,
                revision=revision,
                contract_id=CONTRACT_ID,
            )[TABLE]
        )
    actual_captures = []
    for item in archive:
        if item["role"] == "exact_governance_captures":
            actual_captures.extend(pq.ParquetFile(root / item["path"]).read().to_pylist())
    if sorted(digest(r) for r in expected) != sorted(digest(r) for r in actual_captures):
        raise BoundedError("COMPATIBLE_HOLDER_TRUE_RAW_NEW_VERSION_MISMATCH")
    scans = {}
    for item in descriptor["outputs"]:
        table = item["table"]
        output = root / item["path"]
        before = next(f for f in archive if f["path"] == item["before_local_path"])
        member = Member(
            root / before["path"], before["sha256"], before["size_bytes"], before["rows"]
        )
        if item["new_versions"]:
            capture = next(
                f
                for f in archive
                if f["role"] == "exact_governance_captures" and f["partition"] == item["partition"]
            )
            cm = Member(
                root / capture["path"], capture["sha256"], capture["size_bytes"], capture["rows"]
            )
            validate_holder_revision_partition(
                [member],
                cm,
                output,
                capture_schema=capture_schema,
                target_schema=schema,
                budget=budget,
            )
        elif file_sha(output) != member.sha256:
            raise BoundedError("COMPATIBLE_HOLDER_UNCHANGED_PARTITION_CHANGED")
        scan = semantic_scan([output], schema if table == TABLE else ss[table], check=budget.check)
        if scan != item["semantic_scan"] or scan["rows"] != item["rows"]:
            raise BoundedError("COMPATIBLE_HOLDER_OUTPUT_TYPED_CENSUS_MISMATCH")
        scans[item["path"]] = scan
    metrics = {}
    with GlobalScan(budget, read_only_roots=[root]) as global_scan:
        for table in (TABLE, "dwd_security", "dwd_listed_company"):
            global_scan.add(
                table, [root / f["path"] for f in descriptor["outputs"] if f["table"] == table]
            )
            rule = contract["tables"][table]
            global_scan.keys(table, rule["business_key"], rule["version_key"])
            global_scan.row_rules(table, schema if table == TABLE else ss[table], rule)
            metrics[table] = global_scan.key_metrics[table]
        for column, parent, key in (
            ("company_id", "dwd_listed_company", "company_id"),
            ("security_id", "dwd_security", "security_id"),
        ):
            global_scan.reject(
                f"SELECT 1 FROM {TABLE} r WHERE r.{column} IS NOT NULL AND NOT EXISTS("
                f"SELECT 1 FROM {parent} p WHERE p.{key}=r.{column} "
                "AND p.knowledge_from<=r.knowledge_from "
                "AND(p.knowledge_to IS NULL OR r.knowledge_from<p.knowledge_to) "
                "AND CAST(timezone('Asia/Shanghai',p.effective_from) AS DATE)<=r.period_end "
                "AND(p.effective_to IS NULL OR r.period_end<"
                "CAST(timezone('Asia/Shanghai',p.effective_to) AS DATE))) LIMIT 1",
                "COMPATIBLE_HOLDER_DATED_PARENT_CLOSURE_INVALID",
            )
    return {
        "identity": descriptor["identity"],
        "all_local_raw_pairs_reproduced": len(qualified),
        "exact_new_governance_versions_reproduced": len(expected),
        "old_bodies_and_unchanged_partitions_preserved": True,
        "semantic_scans": scans,
        "global_version_metrics": metrics,
        "historical_provider_precision_or_PIT_inferred": False,
        "whole33_or_Base22_acceptance": False,
    }
