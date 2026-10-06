"""Move native event rows to Shanghai months without changing any value.

The legacy files are small. A frozen per-member row ceiling keeps reconstruction
bounded; larger files fail rather than falling back to a whole-table materialization.
This scope proves physical relocation only, never raw admission or historical PIT.
"""

from __future__ import annotations

import importlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, Member, identifier, independent_copy
from .base22_sources import _fingerprint, _tree

CONTRACT_ID = "p10-base22-native-event-month-relocation-v1"
TABLES = {"dwd_disclosure", "dwd_disclosure_subject", "dwd_shareholding_change"}
MAX_MEMBER_ROWS = 8192
# Exact immutable producer at 5ec4447. The construction/row selection contract
# is unchanged; the new verifier removes redundant per-month hash fan-out.
LEGACY_PRODUCER_SHA256 = "sha256:86259b087ccf2f1b333afb77fdae6b67612938d4e1043239ef00e33a2ff68a63"


def _month_keys(scan: GlobalScan, name: str, rule: dict[str, Any]) -> None:
    """Full keys within a proven key-derived month, with no duplicate key TMP.

    The caller verifies every physical row's Shanghai month first. Since the
    complete key contains effective_at, equal keys cannot live in other months.
    All files in this month participate in the exact comparisons below.
    """
    if (
        rule["partition_by"] != ["effective_at_year", "effective_at_month"]
        or "effective_at" not in rule["business_key"]
        or rule["version_key"] != rule["business_key"] + ["knowledge_from"]
    ):
        raise BoundedError("RELOCATION_GLOBAL_MONTH_KEY_CLOSURE_NOT_PROVEN")
    table = identifier(name)
    keys = ",".join(identifier(k) for k in rule["version_key"])
    business = ",".join(identifier(k) for k in rule["business_key"])
    nulls = " OR ".join(identifier(k) + " IS NULL" for k in rule["version_key"])
    scan.reject(f"SELECT 1 FROM {table} WHERE {nulls} LIMIT 1", "GLOBAL_KEY_NULL")
    scan.reject(
        f"SELECT 1 FROM {table} GROUP BY {keys} HAVING count(*)>1 LIMIT 1",
        "GLOBAL_DUPLICATE_VERSION_KEY",
    )
    scan.reject(
        "SELECT 1 FROM (SELECT knowledge_from,lag(knowledge_to) OVER w AS previous_end,"
        f"row_number() OVER w AS n FROM {table} WINDOW w AS "
        f"(PARTITION BY {business} ORDER BY knowledge_from)) "
        "WHERE n>1 AND (previous_end IS NULL OR previous_end>knowledge_from) LIMIT 1",
        "GLOBAL_OVERLAPPING_KNOWLEDGE_VERSIONS",
    )


def audit_relocation_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Verify only local native typed relocation; archived source paths are strings.

    Original raw admission and historical PIT are explicitly outside this proof.
    The caller must pin the qualification descriptor digest independently.
    """
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("purpose") != "EXACT_NATIVE_TYPED_RELOCATION_NOT_RAW_OR_PIT"
        or body.get("READY") is not False
        or body.get("complete_Base22") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("evaluator_sha256") not in {file_sha(Path(__file__)), LEGACY_PRODUCER_SHA256}
    ):
        raise BoundedError("RELOCATION_OFFLINE_SCOPE_INVALID")
    physical = body["physical_files"]
    if set(_tree(root)) != set(physical) | {"QUALIFICATION.json"}:
        raise BoundedError("RELOCATION_PHYSICAL_FILE_SET_CHANGED")
    for relative, sha in physical.items():
        budget.check()
        if file_sha(_physical(root, relative, independent=True)) != sha:
            raise BoundedError("RELOCATION_PHYSICAL_HASH_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    plans: set[str] = set()
    totals: dict[str, int] = {}
    moves: dict[str, int] = {}
    outputs: dict[str, list[dict[str, Any]]] = {}
    pq = importlib.import_module("pyarrow.parquet")
    for relative in ["evidence/plans.jsonl", "evidence/receipts.jsonl"]:
        _physical(root, relative, independent=True)
    expected: dict[str, dict[str, Any]] = {}
    with (root / "evidence/plans.jsonl").open() as stream:
        for line in stream:
            p = json.loads(line)
            t = p["table"]
            if (
                t not in TABLES
                or p["contract_id"] != CONTRACT_ID
                or p["identity"] != digest({k: v for k, v in p.items() if k != "identity"})
                or p["identity"] in plans
                or p["evaluator_sha256"] != body["evaluator_sha256"]
                or p["schema_and_rule_sha256"]
                != digest({"schema": str(ss[t]), "rule": contract["tables"][t]})
                or p["semantic_changes_allowed"] != []
                or p["consumer_admission"] != "NOT_GRANTED"
                or sum(r["rows"] for r in p["routes"]) != p["input"]["rows"]
            ):
                raise BoundedError("RELOCATION_LOCAL_FROZEN_PLAN_INVALID")
            plans.add(p["identity"])
            expected[p["identity"]] = p
    seen = set()
    with (root / "evidence/receipts.jsonl").open() as stream:
        for line in stream:
            r = json.loads(line)
            p = expected[r["plan_identity"]]
            if (
                r["identity"] != digest({k: v for k, v in r.items() if k != "identity"})
                or r["plan_identity"] in seen
                or r["all_typed_values_exact"] is not True
                or r["source_unchanged"] is not True
                or r["READY"] is not False
                or r["original_rows"] != p["input"]["rows"]
                or [(o["partition"], o["rows"], o["indices_sha256"]) for o in r["files"]]
                != [(o["partition"], o["rows"], o["indices_sha256"]) for o in p["routes"]]
            ):
                raise BoundedError("RELOCATION_LOCAL_TYPED_RECEIPT_INVALID")
            seen.add(r["plan_identity"])
            t = p["table"]
            totals[t] = totals.get(t, 0) + r["original_rows"]
            moves[t] = moves.get(t, 0) + r["wrong_partition_rows"]
            outputs.setdefault(t, []).extend(r["files"])
    if seen != plans or set(outputs) != TABLES or totals != body["relocated_table_rows"]:
        raise BoundedError("RELOCATION_COMPLETE_PLAN_RECEIPT_CENSUS_CHANGED")
    files = [o["path"] for os_ in outputs.values() for o in os_]
    if len(files) != len(set(files)) or set(files) != {
        p for p in physical if p.startswith("payload/")
    }:
        raise BoundedError("RELOCATION_EXACT_TYPED_FILE_SET_CHANGED")
    key_classes = 0
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for t, items in outputs.items():
            groups: dict[str, list[Path]] = {}
            for item in items:
                path = _physical(root, item["path"], independent=True)
                file = pq.ParquetFile(path)
                if (
                    file_sha(path) != item["sha256"]
                    or file.metadata.num_rows != item["rows"]
                    or not file.schema_arrow.equals(ss[t])
                    or item["partition"] not in item["path"]
                ):
                    raise BoundedError("RELOCATION_TYPED_SCHEMA_OR_CENSUS_CHANGED")
                for batch in file.iter_batches(batch_size=budget.limits.batch_rows):
                    if any(f.nullable is False and batch.column(f.name).null_count for f in ss[t]):
                        raise BoundedError("RELOCATION_REQUIRED_FIELD_NULL")
                    if set(_groups(batch)) != {item["partition"]}:
                        raise BoundedError("RELOCATION_SHANGHAI_PHYSICAL_MONTH_INVALID")
                    budget.check()
                groups.setdefault(item["partition"], []).append(path)
            for paths in groups.values():
                scan.connection.read_parquet(
                    [str(p) for p in paths], hive_partitioning=False
                ).create_view("relocated_keys", replace=True)
                _month_keys(scan, "relocated_keys", contract["tables"][t])
                scan.connection.execute("DROP VIEW relocated_keys")
                key_classes += 1
            scan.add(t, [_physical(root, o["path"], independent=True) for o in items])
            scan.lineage(t, [tuple(x) for x in body["inherited_native_lineage"][t]])
            scan.reject(
                f'SELECT 1 FROM "{t}" WHERE effective_at >= '
                "TIMESTAMPTZ '2026-09-01T00:00:00+08:00' OR "
                "(knowledge_to IS NOT NULL AND knowledge_to<=knowledge_from) LIMIT 1",
                "RELOCATION_BUSINESS_CUTOFF_OR_KNOWLEDGE_INTERVAL_INVALID",
            )
        for t, items in body["parents"].items():
            paths = []
            for item in items:
                path = _physical(root, item["path"], independent=True)
                f = pq.ParquetFile(path)
                if f.metadata.num_rows != item["rows"] or not f.schema_arrow.equals(ss[t]):
                    raise BoundedError("RELOCATION_LOCAL_PARENT_SCHEMA_OR_ROWS_INVALID")
                paths.append(path)
            scan.add(t, paths)
        by_name = {t["name"]: t for t in model["tables"]}
        for t in TABLES:
            for fk in by_name[t].get("foreign_keys", []):
                child, parent, pk = fk["column"], fk["table"], fk["reference_column"]
                scan.reject(
                    f'SELECT 1 FROM "{t}" r WHERE r."{child}" IS NOT NULL AND NOT EXISTS '
                    f'(SELECT 1 FROM "{parent}" p WHERE p."{pk}"=r."{child}") LIMIT 1',
                    "RELOCATION_MODEL_FOREIGN_KEY_MISSING",
                )
        scan.reject(
            "SELECT 1 FROM dwd_disclosure_subject WHERE "
            "(CASE WHEN security_id IS NULL THEN 0 ELSE 1 END)+"
            "(CASE WHEN corporate_action_id IS NULL THEN 0 ELSE 1 END)+"
            "(CASE WHEN financial_report_id IS NULL THEN 0 ELSE 1 END)<>1 LIMIT 1",
            "RELOCATION_SUBJECT_IDENTIFIER_CONSTRAINT",
        )
    receipt = {
        "status": "FULL_LOCAL_NATIVE_TYPED_RELOCATION_PASS_NOT_RAW_PIT_OR_BASE22",
        "scope_identity": body["identity"],
        "rows": totals,
        "relocated_rows": moves,
        "key_derived_partition_classes": key_classes,
        "source_runtime_dereferences": 0,
        "raw_source_admission": False,
        "historical_PIT_admission": False,
        "complete_Base22": False,
    }
    return {**receipt, "identity": digest(receipt)}


def _groups(data: Any) -> dict[str, list[int]]:
    pa = importlib.import_module("pyarrow")
    pc = importlib.import_module("pyarrow.compute")

    values = data.column("effective_at")
    if not pa.types.is_timestamp(values.type) or values.type.tz is None or values.null_count:
        raise BoundedError("RELOCATION_AWARE_NON_NULL_EFFECTIVE_TIME_REQUIRED")
    local = pc.cast(values, pa.timestamp(values.type.unit, tz="Asia/Shanghai"))
    years, months = pc.year(local).to_pylist(), pc.month(local).to_pylist()
    groups: dict[str, list[int]] = {}
    for index, (year, month) in enumerate(zip(years, months, strict=True)):
        if (year, month) > (2026, 8):
            raise BoundedError("RELOCATION_BUSINESS_CUTOFF_EXCEEDED")
        key = f"effective_at_year={year}/effective_at_month={month}"
        groups.setdefault(key, []).append(index)
    return groups


def _input(member: Member, schema: Any) -> Any:
    pq = importlib.import_module("pyarrow.parquet")

    if not 0 < member.rows <= MAX_MEMBER_ROWS:
        raise BoundedError("RELOCATION_REGISTERED_MEMBER_ROW_CEILING")
    member.verify()
    file = pq.ParquetFile(member.path)
    if file.metadata.num_rows != member.rows or not file.schema_arrow.equals(schema):
        raise BoundedError("RELOCATION_INPUT_SCHEMA_OR_CENSUS_MISMATCH")
    return file


def plan_relocation(
    member: Member, *, table: str, schema: Any, rule: dict[str, Any], original_partition: str
) -> dict[str, Any]:
    """Freeze exact original bytes and row selection before the output is created."""
    if table not in TABLES or rule["partition_by"] != ["effective_at_year", "effective_at_month"]:
        raise BoundedError("RELOCATION_SCOPE_NOT_APPROVED_EVENT_MONTHS")
    before = _fingerprint(member.path)
    data = _input(member, schema).read(columns=["effective_at"])
    groups = _groups(data)
    match = re.fullmatch(
        r"effective_at_year=([0-9]+)/effective_at_month=([0-9]+)", original_partition
    )
    if match is None or not 1 <= int(match[2]) <= 12:
        raise BoundedError("RELOCATION_ORIGINAL_MONTH_COORDINATE_INVALID")
    # Padding is a legacy physical spelling, not a different business month.
    # The original path remains verbatim in the pinned member identity.
    original_partition = f"effective_at_year={int(match[1])}/effective_at_month={int(match[2])}"
    if _fingerprint(member.path) != before or file_sha(member.path) != member.sha256:
        raise BoundedError("RELOCATION_SOURCE_CHANGED")
    body = {
        "contract_id": CONTRACT_ID,
        "table": table,
        "input": {
            "path": str(member.path),
            "sha256": member.sha256,
            "size_bytes": member.size_bytes,
            "rows": member.rows,
        },
        "schema_and_rule_sha256": digest({"schema": str(schema), "rule": rule}),
        "evaluator_sha256": file_sha(Path(__file__)),
        "original_partition": original_partition,
        "row_ceiling": MAX_MEMBER_ROWS,
        "routes": [
            {"partition": part, "rows": len(indices), "indices_sha256": digest(indices)}
            for part, indices in sorted(groups.items())
        ],
        "wrong_partition_rows": sum(
            len(indices) for part, indices in groups.items() if part != original_partition
        ),
        "semantic_changes_allowed": [],
        "consumer_admission": "NOT_GRANTED",
    }
    return {**body, "identity": digest(body)}


def relocate_member(
    member: Member,
    *,
    proof: dict[str, Any],
    schema: Any,
    rule: dict[str, Any],
    output: Path,
    budget: Budget,
) -> dict[str, Any]:
    """Write distinct files and reconstruct the original typed rows exactly.

    No sorting, clock repair, deduplication, alias repair or scalar conversion occurs.
    Only the physical month changes. Exact all-column equality includes nulls,
    Decimal precision and the original timestamp instants.
    """
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")

    expected = plan_relocation(
        member,
        table=proof.get("table", ""),
        schema=schema,
        rule=rule,
        original_partition=proof.get("original_partition", ""),
    )
    if expected != proof:
        raise BoundedError("RELOCATION_FROZEN_PLAN_MISMATCH")
    # The guard precedes all mkdir/write operations, including healthy copies.
    output = budget.destination(output)
    before = member.verify()
    data = _input(member, schema).read()
    budget.check(reserve=data.nbytes * 4)
    groups = _groups(data)
    stem = digest(proof["input"])[7:] + ".parquet"
    results, reconstructed, positions = [], [], []
    for part, indices in sorted(groups.items()):
        dest = output / proof["table"] / part / stem
        selected = data.take(pa.array(indices, type=pa.int64()))
        if proof["wrong_partition_rows"] == 0:
            copied = independent_copy(member, dest, budget)
            disposition = copied["mode"]
        else:
            dest = budget.destination(dest)
            if dest.exists():
                raise BoundedError("RELOCATION_OUTPUT_EXISTS")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("xb") as stream:
                pq.write_table(selected, stream, compression="zstd")
                stream.flush()
                os.fsync(stream.fileno())
            disposition = "exact_typed_row_month_relocation"
        actual = pq.ParquetFile(dest).read()
        if not actual.equals(selected, check_metadata=True) or dest.stat().st_nlink != 1:
            raise BoundedError("RELOCATION_SERIALIZED_TYPED_VALUES_CHANGED")
        reconstructed.append(actual)
        positions.extend(indices)
        results.append(
            {
                "partition": part,
                "path": str(dest.relative_to(budget.root)),
                "rows": actual.num_rows,
                "sha256": file_sha(dest),
                "allocated_bytes": dest.stat().st_blocks * 512,
                "indices_sha256": digest(indices),
                "disposition": disposition,
            }
        )
        budget.check()
    reverse = sorted(range(len(positions)), key=positions.__getitem__)
    restored = pa.concat_tables(reconstructed).take(pa.array(reverse, type=pa.int64()))
    if sorted(positions) != list(range(member.rows)) or not restored.equals(
        data, check_metadata=True
    ):
        raise BoundedError("RELOCATION_COMPLETE_ORIGINAL_ROWS_NOT_PRESERVED")
    if _fingerprint(member.path) != before or file_sha(member.path) != member.sha256:
        raise BoundedError("RELOCATION_SOURCE_CHANGED")
    body = {
        "plan_identity": proof["identity"],
        "files": results,
        "original_rows": member.rows,
        "wrong_partition_rows": proof["wrong_partition_rows"],
        "all_typed_values_exact": True,
        "source_unchanged": True,
        "complete_Base22": False,
        "READY": False,
    }
    return {**body, "identity": digest(body)}
