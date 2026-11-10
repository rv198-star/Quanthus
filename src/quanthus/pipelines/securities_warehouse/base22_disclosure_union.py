"""Append-only disclosure union; existing entities never gain inferred revisions.

Only absent entity observations enter this scope. Equal clocks, changed publication
coordinates and alternate URIs cannot authorize an overwrite. Callers freeze the
whole native entity census, archive all held observations and check global closure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base22 import digest, file_sha, partition
from .base22_bounded import BoundedError, Budget, Member, rewrite_partition, semantic_scan
from .base22_sources import _fingerprint
from .base22_tail_union import member_identity

CONTRACT_ID = "p10-base22-absent-disclosure-union-v1"
ENTITIES = {
    "dwd_disclosure": "disclosure_id",
    "dwd_disclosure_subject": "disclosure_subject_id",
}
MAX_ADDITIONS = 65536


def observation_census(member: Member, *, schema: Any, rule: dict[str, Any]) -> str:
    """Exact full-row census, bounded to one declared incoming partition."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if not 0 < member.rows <= MAX_ADDITIONS:
        raise BoundedError("DISCLOSURE_ADDITION_SCOPE_TOO_LARGE_OR_EMPTY")
    member.verify()
    file = pq.ParquetFile(member.path)
    if file.metadata.num_rows != member.rows or not file.schema_arrow.equals(schema):
        raise BoundedError("DISCLOSURE_ADDITION_SCHEMA_OR_ROWS_MISMATCH")
    rows = []
    keys = set()
    for batch in file.iter_batches(batch_size=1024):
        for row in batch.to_pylist():
            key = digest([row[k] for k in rule["version_key"]])
            if key in keys:
                raise BoundedError("DISCLOSURE_ADDITION_DUPLICATE_VERSION")
            keys.add(key)
            rows.append(digest(row))
    return digest(sorted(rows))


def append_absent_partition(
    native: list[Member],
    additions: Member,
    *,
    proof: dict[str, Any],
    schema: Any,
    rule: dict[str, Any],
    output: Path,
    budget: Budget,
) -> dict[str, Any]:
    """Preserve every native byte semantic; append exact absent entity versions.

    This partition proof does not establish absence in other partitions, admission
    of sources or full warehouse acceptance. The caller separately binds and scans
    the complete native entity inventory before creating the input proof.
    """
    import pyarrow.parquet as pq

    body = {k: v for k, v in proof.items() if k != "identity"}
    required = {
        "contract_id",
        "table",
        "partition",
        "native",
        "additions",
        "added_row_census_sha256",
        "whole_native_absence_proof_sha256",
        "source_qualification_sha256",
        "configuration_sha256",
        "evaluator_sha256",
        "consumer_admission",
    }
    table = body.get("table")
    if (
        set(body) != required
        or table not in ENTITIES
        or proof.get("identity") != digest(body)
        or body.get("contract_id") != CONTRACT_ID
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("native") != [member_identity(m) for m in native]
        or body.get("additions") != member_identity(additions)
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or body.get("configuration_sha256") != digest({"schema": str(schema), "rule": rule})
        or any(
            not isinstance(body.get(k), str)
            or len(body[k]) != 71
            or not body[k].startswith("sha256:")
            or any(c not in "0123456789abcdef" for c in body[k][7:])
            for k in ["whole_native_absence_proof_sha256", "source_qualification_sha256"]
        )
    ):
        raise BoundedError("DISCLOSURE_FROZEN_PROOF_INVALID")
    members = [*native, additions]
    if len({m.path for m in members}) != len(members):
        raise BoundedError("DISCLOSURE_REPEATED_INPUT")
    before = [m.verify() for m in members]
    census = observation_census(additions, schema=schema, rule=rule)
    if census != body["added_row_census_sha256"]:
        raise BoundedError("DISCLOSURE_EXACT_ADDITION_CENSUS_MISMATCH")
    entity = ENTITIES[table]
    added_entities = set()
    for batch in pq.ParquetFile(additions.path).iter_batches(batch_size=1024):
        for row in batch.to_pylist():
            if partition(row, rule) != body["partition"] or not row[entity]:
                raise BoundedError("DISCLOSURE_ADDITION_PARTITION_OR_ENTITY_INVALID")
            added_entities.add(row[entity])
    for member in native:
        file = pq.ParquetFile(member.path)
        if file.metadata.num_rows != member.rows or not file.schema_arrow.equals(schema):
            raise BoundedError("DISCLOSURE_NATIVE_SCHEMA_OR_ROWS_MISMATCH")
        for batch in file.iter_batches(batch_size=1024):
            budget.check()
            for row in batch.to_pylist():
                if partition(row, rule) != body["partition"]:
                    raise BoundedError("DISCLOSURE_NATIVE_PARTITION_INVALID")
                if row[entity] in added_entities:
                    raise BoundedError("DISCLOSURE_ENTITY_ALREADY_PRESENT")
    expected = semantic_scan([m.path for m in members], schema, check=budget.check)
    receipt = rewrite_partition(members, output, budget)
    actual = semantic_scan([output], schema, check=budget.check)
    if actual != expected or any(
        _fingerprint(m.path) != fp or file_sha(m.path) != m.sha256
        for m, fp in zip(members, before, strict=True)
    ):
        raise BoundedError("DISCLOSURE_UNION_SEMANTICS_OR_SOURCE_CHANGED")
    receipt.update(
        {
            "semantic_scan": actual,
            "native_rows_preserved": sum(m.rows for m in native),
            "added_rows": additions.rows,
            "added_entities": len(added_entities),
            "added_row_census_sha256": census,
            "proof_identity": proof["identity"],
            "old_row_replacements": 0,
        }
    )
    return receipt
