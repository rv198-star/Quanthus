"""Append-only, partition-bounded union of retained daily tail observations.

This component preserves every native row. It cannot replace a conflicting value,
grant source/PIT/consumer qualification, change state/gap, or mark an image READY.
Both inputs and the exact added key/row digests are frozen before any output write.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha, partition
from .base22_bounded import (
    BoundedError,
    Budget,
    GlobalScan,
    Member,
    identifier,
    rewrite_partition,
    semantic_scan,
)
from .base22_sources import _fingerprint

TABLE = "dwd_security_bar"
CONTRACT_ID = "p10-base22-retained-tail-partition-union-v1"
MAX_ADDITIONS_PER_PARTITION = 4096


def member_identity(member: Member) -> dict[str, Any]:
    return {
        "path": str(member.path),
        "sha256": member.sha256,
        "size_bytes": member.size_bytes,
        "rows": member.rows,
    }


def addition_identity(row: Mapping[str, Any], rule: Mapping[str, Any]) -> dict[str, str]:
    """Only hashes escape the sealed process; no numerical scalars in receipts."""
    return {
        "business_key_sha256": digest([row[k] for k in rule["business_key"]]),
        "version_key_sha256": digest([row[k] for k in rule["version_key"]]),
        "row_sha256": digest(row),
    }


def append_missing_bar_partition(
    native: list[Member],
    additions: Member,
    *,
    proof: Mapping[str, Any],
    schema: Any,
    rule: Mapping[str, Any],
    output: Path,
    budget: Budget,
) -> dict[str, Any]:
    """Exact new missing business keys, immutable native rows, independent output.

    Absence is checked against all frozen native files in the affected partition,
    including every knowledge version. A different knowledge time cannot bypass an
    existing business key. The caller additionally performs whole-scope global
    key/reference/lineage checks; this receipt never qualifies the full warehouse.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    body = {k: v for k, v in proof.items() if k != "identity"}
    required = {
        "contract_id",
        "operation",
        "partition",
        "native",
        "additions",
        "allowed_additions",
        "repair_evidence_sha256",
        "history_descriptor_sha256",
        "configuration_sha256",
        "evaluator_sha256",
        "consumer_admission",
    }
    if (
        set(body) != required
        or proof.get("identity") != digest(body)
        or body["contract_id"] != CONTRACT_ID
        or body["operation"] != "APPEND_ONLY_ABSENT_BUSINESS_KEYS"
        or body["consumer_admission"] != "NOT_GRANTED"
        or body["evaluator_sha256"] != file_sha(Path(__file__))
        or body["configuration_sha256"] != digest({"schema": str(schema), "rule": rule})
        or not native
        or body["native"] != [member_identity(m) for m in native]
        or body["additions"] != member_identity(additions)
        or not 0 < additions.rows <= MAX_ADDITIONS_PER_PARTITION
    ):
        raise BoundedError("TAIL_UNION_FROZEN_PROOF_INVALID")
    members = [*native, additions]
    if len({m.path for m in members}) != len(members):
        raise BoundedError("TAIL_UNION_REPEATED_INPUT")
    fingerprints = [m.verify() for m in members]
    allowed = body["allowed_additions"]
    if (
        not isinstance(allowed, list)
        or len(allowed) != additions.rows
        or len({p["version_key_sha256"] for p in allowed}) != len(allowed)
        or len({p["business_key_sha256"] for p in allowed}) != len(allowed)
    ):
        raise BoundedError("TAIL_UNION_EXACT_ADDITION_CENSUS_INVALID")

    # Entire native partition, not just the three securities, is checked before
    # the writer starts. Only partition coordinates and identities become Python
    # objects; prices/clocks remain typed Arrow buffers for semantic hashing.
    actual_additions = []
    for member in members:
        footer = pq.ParquetFile(member.path)
        if (
            not footer.schema_arrow.equals(schema, check_metadata=False)
            or footer.metadata.num_rows != member.rows
        ):
            raise BoundedError("TAIL_UNION_INPUT_SCHEMA_CENSUS_MISMATCH")
        columns = (
            schema.names
            if member == additions
            else ["interval_size", "price_basis", "observation_start"]
        )
        for batch in footer.iter_batches(batch_size=budget.limits.batch_rows, columns=columns):
            budget.check(extra_rss=batch.nbytes * 2)
            for row in batch.to_pylist():
                if (
                    row["interval_size"] != "1d"
                    or row["price_basis"] != "UNADJUSTED"
                    or partition(row, rule) != body["partition"]
                ):
                    raise BoundedError("TAIL_UNION_INPUT_PARTITION_OR_BASIS_MISMATCH")
                if member == additions:
                    actual_additions.append(addition_identity(row, rule))
    if actual_additions != allowed:
        raise BoundedError("TAIL_UNION_ALLOWED_ADDITION_DIGEST_MISMATCH")

    with GlobalScan(budget) as scan:
        # Explicit verified source paths are inputs to this construction step,
        # never a cold-reader fallback. GlobalScan.add stays local-only.
        scan.connection.read_parquet(
            [str(m.path) for m in native], hive_partitioning=False
        ).create_view("native_tail_partition")
        scan.connection.read_parquet(str(additions.path), hive_partitioning=False).create_view(
            "new_tail_partition"
        )
        keys = " AND ".join(
            "n." + identifier(k) + "=a." + identifier(k) for k in rule["business_key"]
        )
        scan.reject(
            "SELECT 1 FROM new_tail_partition a WHERE EXISTS "
            "(SELECT 1 FROM native_tail_partition n WHERE " + keys + ") LIMIT 1",
            "TAIL_UNION_EXISTING_BUSINESS_KEY_REPLACEMENT_FORBIDDEN",
        )

    # Preserve native order then frozen additions; no price, timestamp or old
    # knowledge interval transformation is authorized by this append operation.
    before = semantic_scan([m.path for m in members], schema, check=budget.check)
    receipt = rewrite_partition(members, output, budget)
    after = semantic_scan([output], schema, check=budget.check)
    if before != after:
        raise BoundedError("TAIL_UNION_TYPED_COLUMN_PRESERVATION_FAILED")
    if any(_fingerprint(m.path) != f for m, f in zip(members, fingerprints, strict=True)):
        raise BoundedError("TAIL_UNION_INPUT_CHANGED")
    if any(file_sha(m.path) != m.sha256 for m in members):
        raise BoundedError("TAIL_UNION_INPUT_CHANGED")
    return {
        **receipt,
        "contract_id": CONTRACT_ID,
        "proof_identity": proof["identity"],
        "native_rows_preserved": sum(m.rows for m in native),
        "new_missing_observation_rows": additions.rows,
        "semantic_scan": after,
        "all_columns_values_and_instants_preserved": True,
        "native_row_replacements": 0,
        "state_gap_changes": 0,
        "source_and_consumer_admission": "NOT_GRANTED",
    }
