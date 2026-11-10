"""Source-independent full scans and raw replay of a retained-tail union scope."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan
from .base22_output_audit import audit_observation_stage
from .base22_outputs import instant, tail_observation, validate_tail_links
from .base22_sources import tail_raw_projection
from .base22_tail_union import CONTRACT_ID, MAX_ADDITIONS_PER_PARTITION, TABLE, addition_identity

TABLES = {TABLE, "dwd_listing", "dwd_trading_calendar"}


def _local_json(root: Path, relative: str, expected_sha: str) -> dict[str, Any]:
    path = _physical(root, relative, independent=True)
    if file_sha(path) != expected_sha:
        raise BoundedError("TAIL_UNION_LOCAL_EVIDENCE_HASH_MISMATCH")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise BoundedError("TAIL_UNION_LOCAL_EVIDENCE_SHAPE_INVALID")
    return value


def validate_union_proof(
    proof: dict[str, Any], descriptor: dict[str, Any], rule: Any, schema: Any
) -> None:
    """Archived source paths are strings; never open or resolve any of them."""
    from . import base22_tail_union

    body = {k: v for k, v in proof.items() if k != "identity"}
    if (
        proof.get("identity") != digest(body)
        or proof.get("contract_id") != CONTRACT_ID
        or proof.get("operation") != "APPEND_ONLY_ABSENT_BUSINESS_KEYS"
        or proof.get("consumer_admission") != "NOT_GRANTED"
        or proof.get("repair_evidence_sha256") != descriptor["governance_evidence_sha256"]
        or proof.get("history_descriptor_sha256") != descriptor["history_descriptor_sha256"]
        or proof.get("configuration_sha256") != digest({"schema": str(schema), "rule": rule})
        or proof.get("evaluator_sha256") != file_sha(Path(base22_tail_union.__file__))
        or not proof.get("native")
        or not 0 < proof["additions"]["rows"] <= MAX_ADDITIONS_PER_PARTITION
        or len(proof.get("allowed_additions", [])) != proof["additions"]["rows"]
    ):
        raise BoundedError("TAIL_UNION_OFFLINE_PROOF_INVALID")
    allowed = proof["allowed_additions"]
    if any(set(p) != {"business_key_sha256", "version_key_sha256", "row_sha256"} for p in allowed):
        raise BoundedError("TAIL_UNION_OFFLINE_ADDITION_EVIDENCE_INVALID")
    for key in ("business_key_sha256", "version_key_sha256"):
        if len({p[key] for p in allowed}) != len(allowed):
            raise BoundedError("TAIL_UNION_OFFLINE_DUPLICATE_ADDITION")


def audit_tail_union_scope(
    root: Path, descriptor: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """All typed columns, global keys/versions/lineage, calendar and original raw.

    Only the independent scope and ordinary code are read. A caller provides a
    unique job-local TMP budget whose root contains this image. Source/network
    isolation is imposed by the cold controller at the OS level, including Arrow
    and DuckDB, rather than relying on Python audit hooks alone.
    """
    import pyarrow as pa  # type: ignore[import-untyped]

    if (
        descriptor.get("projection_contract_id") != CONTRACT_ID
        or set(descriptor.get("output_rows", {})) != TABLES
        or set(descriptor.get("source_registries", {})) != TABLES
        or descriptor.get("mandatory_472_repair_acceptance") is not False
        or descriptor.get("full33_candidate_acceptance") is not False
    ):
        raise BoundedError("TAIL_UNION_OFFLINE_SCOPE_INVALID")
    config = root / "observations" / descriptor["label"] / "contract"
    full_scan = audit_observation_stage(root, descriptor, config)
    contract, model, extension = load_contract(config)
    ss = schemas(model, extension)
    rule = contract["tables"][TABLE]
    governance = _local_json(
        root, descriptor["governance_evidence_path"], descriptor["governance_evidence_sha256"]
    )
    proofs = {}
    for item in descriptor["exact_union_proofs"]:
        proof = _local_json(root, item["path"], item["sha256"])
        validate_union_proof(proof, descriptor, rule, ss[TABLE])
        if proof["identity"] != item["proof_identity"] or item["path"] in proofs:
            raise BoundedError("TAIL_UNION_OFFLINE_PROOF_SET_INVALID")
        proofs[item["path"]] = proof
    bar_files = [item for item in descriptor["outputs"] if item["table"] == TABLE]
    if len(bar_files) != len(proofs) or {p["proof_path"] for p in bar_files} != set(proofs):
        raise BoundedError("TAIL_UNION_OFFLINE_EXACT_PARTITION_PROOF_SET_INVALID")

    # Three bounded retained raw shards, not full-history row documents.
    raw = {}
    for item in descriptor["archived_true_source_observations"]:
        if item.get("role") != "original_retained_tail_raw":
            raise BoundedError("TAIL_UNION_OFFLINE_RAW_SOURCE_ROLE_INVALID")
        stock = item["stock_code"]
        path = _physical(root, item["path"], independent=True)
        rows = tail_raw_projection(path, item["sha256"], stock).to_pylist()
        if len(rows) > 4096 or stock in raw:
            raise BoundedError("TAIL_UNION_OFFLINE_RAW_SCOPE_BOUND_INVALID")
        raw[stock] = {r["trade_date"]: r for r in rows}
    if len(raw) > 3 or not raw:
        raise BoundedError("TAIL_UNION_OFFLINE_RAW_UNIVERSE_INVALID")
    knowledge = instant(descriptor["new_knowledge_at"])
    replay_rows = regular = quarantined = 0
    with GlobalScan(budget) as scan:
        for table in sorted(TABLES):
            paths = [
                _physical(root, o["path"], independent=True)
                for o in descriptor["outputs"]
                if o["table"] == table
            ]
            scan.add(table, paths)
            scan.keys(
                table,
                contract["tables"][table]["business_key"],
                contract["tables"][table]["version_key"],
            )
            scan.row_rules(table, ss[table], contract["tables"][table])
            scan.lineage(table, [tuple(s) for s in descriptor["source_registries"][table]])
        validate_tail_links(scan)
        parents = {}
        for table, key in (
            ("dwd_listing", "listing_id"),
            ("dwd_trading_calendar", "calendar_session_id"),
        ):
            # One small, bounded immutable identity dependency for all raw rows.
            rows = (
                scan.connection.execute(
                    "SELECT * FROM " + table + " WHERE knowledge_from<=? AND "
                    "(knowledge_to IS NULL OR ?<knowledge_to) LIMIT 65537",
                    [knowledge, knowledge],
                )
                .to_arrow_table()
                .to_pylist()
            )
            if len(rows) > 65536 or len({r[key] for r in rows}) != len(rows):
                raise BoundedError("TAIL_UNION_OFFLINE_RAW_PARENT_IDENTITY_FAILED")
            parents[table] = {r[key]: r for r in rows}
        for item in bar_files:
            proof = proofs[item["proof_path"]]
            if (
                item["proof_identity"] != proof["identity"]
                or item["rows"]
                != sum(m["rows"] for m in proof["native"]) + proof["additions"]["rows"]
            ):
                raise BoundedError("TAIL_UNION_OFFLINE_PARTITION_ROW_CENSUS_INVALID")
            # Paths originate only in the checked physical image inventory.
            path = _physical(root, item["path"], independent=True)
            rows = (
                scan.connection.read_parquet(str(path), hive_partitioning=False)
                .filter("record_source='p10_retained_baostock_tail'")
                .to_arrow_table()
                .to_pylist()
            )
            if [addition_identity(row, rule) for row in rows] != proof["allowed_additions"]:
                raise BoundedError("TAIL_UNION_OFFLINE_ADDED_ROW_DIGEST_MISMATCH")
            for row in rows:
                stock = row["source_record_id"].split("&")[0].removeprefix("stock_code=")
                day = row["observation_start"].astimezone(ZoneInfo("Asia/Shanghai")).date()
                original = raw.get(stock, {}).get(day)
                if original is None or row["knowledge_from"] != knowledge:
                    raise BoundedError("TAIL_UNION_OFFLINE_RAW_OR_REAL_CLOCK_CLOSURE_FAILED")
                listing = parents["dwd_listing"].get(row["listing_id"])
                calendar = parents["dwd_trading_calendar"].get(row["calendar_session_id"])
                if listing is None or calendar is None:
                    raise BoundedError("TAIL_UNION_OFFLINE_RAW_PARENT_IDENTITY_FAILED")
                rebuilt, evidence = tail_observation(
                    original,
                    listing=listing,
                    calendar=calendar,
                    observed_at=knowledge,
                    source_sha256=row["source_artifact_sha256"],
                    revision=row["source_revision"],
                )
                if row["source_artifact_sha256"] not in {
                    a["sha256"]
                    for a in descriptor["archived_true_source_observations"]
                    if a["stock_code"] == stock
                }:
                    raise BoundedError("TAIL_UNION_OFFLINE_RAW_ARTIFACT_IDENTITY_FAILED")
                physical = pa.Table.from_pylist([rebuilt], schema=ss[TABLE]).to_pylist()[0]
                if physical != row:
                    raise BoundedError("TAIL_UNION_OFFLINE_ORIGINAL_RAW_REPLAY_DRIFT")
                replay_rows += 1
                if evidence["classification"] == "RAW_OBSERVATION":
                    regular += 1
                else:
                    quarantined += 1
        if (replay_rows, regular, quarantined) != (
            descriptor["new_missing_observations"],
            governance["regular_observations"],
            governance["quarantined_rows"],
        ):
            raise BoundedError("TAIL_UNION_OFFLINE_QUARANTINE_CENSUS_CHANGED")
        for held in descriptor["held_conflicts_preserved"]:
            rows = (
                scan.connection.execute(
                    "SELECT * FROM dwd_security_bar WHERE listing_id=? AND "
                    "CAST(timezone('Asia/Shanghai',observation_start) AS DATE)=? "
                    "AND interval_size='1d'",
                    [
                        next(
                            c["listing_id"]
                            for c in governance["three_conflicts"]
                            if c["stock_code"] == held["stock_code"]
                            and c["trade_date"] == held["trade_date"]
                        ),
                        held["trade_date"],
                    ],
                )
                .to_arrow_table()
                .to_pylist()
            )
            if held["before_row_sha256"] not in {digest(row) for row in rows}:
                raise BoundedError("TAIL_UNION_OFFLINE_HELD_VERSION_CHANGED")
    return {
        "status": "PASS_FULL_RETAINED_TAIL_SCOPE_NOT_BASE22_ACCEPTANCE",
        "identity": descriptor["identity"],
        "full_typed_scan": full_scan,
        "global_keys_versions_calendar_venue_lineage": "RECOMPUTED_PASS",
        "raw_replay": {"rows": replay_rows, "regular": regular, "quarantined": quarantined},
        "held_original_conflicts_preserved": len(descriptor["held_conflicts_preserved"]),
        "source_or_consumer_admission": "NOT_GRANTED",
        "full33_candidate_acceptance": False,
    }
