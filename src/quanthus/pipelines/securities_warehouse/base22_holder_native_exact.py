"""Isolated native holder precision preparation; no consumer/schema activation.

The preserved warehouse raw text needs up to thirteen fractional digits. The
old rounded column, parser, provenance and clocks remain unchanged. Decimal256
keeps the old 28-integer-digit capacity with the thirteen-digit fractional part.
This proves exactness against retained native text, not vendor decimal tokens or
historical publication availability.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base22 import file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, Member, _batches, semantic_scan
from .base22_holder_exact import TABLE
from .base22_precision import decimal_profile
from .base22_sources import _fingerprint

CONTRACT_ID = "p10-base22-holder-native-exact-v3-preparation"
CONTRACT = {
    "schema_version": 3,
    "contract_id": CONTRACT_ID,
    "purpose": "isolated_native_exact_precision_preparation",
    "parent_schema_contract_sha256": "sha256:"
    "a57938ee94a91510133cbfbe78373c32c38673a5a3780dd759d250d8ddefe97c",
    "approved_11_scale_parent": "p10-base22-holder-exact-v2",
    "v2_unchanged": True,
    "table": TABLE,
    "field": {"name": "average_holding_exact", "precision": 41, "scale": 13, "nullable": True},
    "physical_decimal": "decimal256",
    "legacy_integer_digits_preserved": 28,
    "raw_field": "raw_average_holding",
    "rule": "exact_retained_native_decimal_text_else_null_only_for_source_null",
    "unsupported_or_nonfinite": "fail_closed",
    "old_columns_parser_provenance_time": "unchanged",
    "vendor_original_decimal_or_publication_availability": "not_inferred",
    "maximum_batch_rows": 16384,
    "business_through": "2026-08-31",
    "timezone": "Asia/Shanghai",
    "whole_image_schema_admission": "pending_final_candidate_review",
    "source_admission": "not_granted_by_representation",
    "consumer_admission": "NOT_GRANTED",
}


def load_native_holder_contract(config: Path) -> tuple[dict[str, Any], Any, Any]:
    import pyarrow as pa  # type: ignore[import-untyped]

    try:
        body = json.loads((config / "base22-holder-native-exact.v3.json").read_bytes())
        if (
            body != CONTRACT
            or file_sha(config / "base22-independent-image.v1.json")
            != CONTRACT["parent_schema_contract_sha256"]
        ):
            raise BoundedError("NATIVE_HOLDER_EXACT_CONTRACT_INVALID")
        _, model, extension = load_contract(config)
    except (OSError, ValueError) as exc:
        raise BoundedError("NATIVE_HOLDER_EXACT_CONTRACT_UNREADABLE") from exc
    legacy = schemas(model, extension)[TABLE]
    return body, legacy, legacy.append(pa.field("average_holding_exact", pa.decimal256(41, 13)))


def carry_native_holder_batch(batch: Any, *, legacy_schema: Any, contract_id: str) -> Any:
    """One bounded batch; never quantize, change old fields or invent missing raw."""
    import pyarrow as pa

    if (
        contract_id != CONTRACT_ID
        or not batch.schema.equals(legacy_schema)
        or batch.num_rows > CONTRACT["maximum_batch_rows"]
    ):
        raise BoundedError("NATIVE_HOLDER_EXACT_BATCH_CONTRACT_INVALID")
    exact: list[Decimal | None] = []
    for raw in batch.column("raw_average_holding").to_pylist():
        profile = decimal_profile(raw, precision=41, scale=13)
        if profile["classification"] == "NULL":
            exact.append(None)
        elif profile["fits"]:
            exact.append(Decimal(raw.strip()))
        else:
            raise BoundedError("NATIVE_HOLDER_EXACT_RAW_NOT_REPRESENTABLE")
    return pa.RecordBatch.from_arrays(
        [*batch.columns, pa.array(exact, type=pa.decimal256(41, 13))],
        schema=legacy_schema.append(pa.field("average_holding_exact", pa.decimal256(41, 13))),
    )


def validate_native_holder_partition(
    native: list[Member], output: Path, *, legacy_schema: Any, exact_schema: Any, budget: Budget
) -> int:
    """Compare every old column and derived exact value, across file boundaries."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    file = pq.ParquetFile(output)
    if not file.schema_arrow.equals(exact_schema):
        raise BoundedError("NATIVE_HOLDER_EXACT_OUTPUT_SCHEMA_INVALID")
    source = iter(_batches([m.path for m in native], legacy_schema, 1024))
    target = iter(file.iter_batches(batch_size=1024))
    left, right = next(source, None), next(target, None)
    left_pos = right_pos = total = 0
    while left is not None and right is not None:
        budget.check()
        n = min(left.num_rows - left_pos, right.num_rows - right_pos)
        expected = carry_native_holder_batch(
            left.slice(left_pos, n), legacy_schema=legacy_schema, contract_id=CONTRACT_ID
        )
        if not expected.equals(right.slice(right_pos, n)):
            raise BoundedError("NATIVE_HOLDER_OLD_COLUMNS_OR_EXACT_VALUE_CHANGED")
        total += n
        left_pos += n
        right_pos += n
        if left_pos == left.num_rows:
            left, left_pos = next(source, None), 0
        if right_pos == right.num_rows:
            right, right_pos = next(target, None), 0
    if left is not None or right is not None or total != sum(m.rows for m in native):
        raise BoundedError("NATIVE_HOLDER_EXACT_ROW_CENSUS_MISMATCH")
    return total


def materialize_native_holder_partition(
    native: list[Member],
    output: Path,
    *,
    legacy_schema: Any,
    exact_schema: Any,
    budget: Budget,
) -> dict[str, Any]:
    """One exclusive, bounded native partition; old data only read, never linked."""
    import os
    import time

    import pyarrow.parquet as pq

    if not native or len({m.path for m in native}) != len(native):
        raise BoundedError("NATIVE_HOLDER_PARTITION_INPUT_INVALID")
    import pyarrow as pa

    if not exact_schema.equals(
        legacy_schema.append(pa.field("average_holding_exact", pa.decimal256(41, 13)))
    ):
        raise BoundedError("NATIVE_HOLDER_PARTITION_TARGET_SCHEMA_INVALID")
    start = time.monotonic()
    before = [m.verify() for m in native]
    for m in native:
        file = pq.ParquetFile(m.path)
        if file.metadata.num_rows != m.rows or not file.schema_arrow.equals(legacy_schema):
            raise BoundedError("NATIVE_HOLDER_PARTITION_INPUT_SCHEMA_OR_ROWS_INVALID")
    output = budget.destination(output)
    partial = output.with_name(output.name + ".partial")
    if output.exists() or partial.exists():
        raise BoundedError("NATIVE_HOLDER_PARTITION_OUTPUT_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        partial.open("xb") as stream,
        pq.ParquetWriter(stream, exact_schema, compression="zstd") as writer,
    ):
        for batch in _batches([m.path for m in native], legacy_schema, 1024):
            budget.check(reserve=batch.nbytes * 3)
            writer.write_batch(
                carry_native_holder_batch(
                    batch, legacy_schema=legacy_schema, contract_id=CONTRACT_ID
                )
            )
    rows = validate_native_holder_partition(
        native, partial, legacy_schema=legacy_schema, exact_schema=exact_schema, budget=budget
    )
    if any(
        _fingerprint(m.path) != fp or file_sha(m.path) != m.sha256
        for m, fp in zip(native, before, strict=True)
    ):
        raise BoundedError("NATIVE_HOLDER_SOURCE_CHANGED")
    os.link(partial, output)  # Atomic local no-overwrite seal; no source hard links.
    partial.unlink()
    budget.check()
    return {
        "rows": rows,
        "sha256": file_sha(output),
        "size_bytes": output.stat().st_size,
        "allocated_bytes": output.stat().st_blocks * 512,
        "semantic_scan": semantic_scan([output], exact_schema, check=budget.check),
        "old_columns_and_native_raw_unchanged": True,
        "seconds": time.monotonic() - start,
        "source_or_consumer_admission": "NOT_GRANTED",
    }
