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

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, Member, _batches, semantic_scan
from .base22_holder_exact import TABLE
from .base22_precision import decimal_profile
from .base22_sources import _fingerprint, _tree

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


def audit_native_holder_scope(
    root: Path, descriptor: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Full native table precision proof, exact local files and scoped references.

    No prior source path, provider acquisition or publication time is inferred.
    v3 preparation admission remains separate from final image/consumer acceptance.
    """
    if (
        descriptor.get("kind") != "isolated_native_holder_exact_preparation_not_complete_base22"
        or descriptor.get("identity")
        != digest({k: v for k, v in descriptor.items() if k != "identity"})
        or descriptor.get("schema_contract_id") != CONTRACT_ID
        or descriptor.get("consumer_admission") != "NOT_GRANTED"
        or descriptor.get("whole_image_schema_admission") != "pending_final_candidate_review"
        or descriptor.get("whole33_acceptance") is not False
        or descriptor.get("READY") is not False
        or set(descriptor.get("output_rows", {})) != {TABLE, "dwd_security", "dwd_listed_company"}
    ):
        raise BoundedError("NATIVE_HOLDER_SCOPE_CONTRACT_INVALID")
    stage = root / "observations" / descriptor["label"]
    config = stage / "contract"
    expected_configs = {
        "base22-holder-native-exact.v3.json",
        "base22-independent-image.v1.json",
        "dwd-domain-model.v1.json",
        "dwd-domain-model.v1.1-extension.json",
    }
    if (
        set(descriptor["contract_members"]) != expected_configs
        or set(_tree(config)) != expected_configs
    ):
        raise BoundedError("NATIVE_HOLDER_LOCAL_CONTRACT_SET_INVALID")
    for name, sha in descriptor["contract_members"].items():
        if (
            file_sha(_physical(root, str((config / name).relative_to(root)), independent=True))
            != sha
        ):
            raise BoundedError("NATIVE_HOLDER_LOCAL_CONTRACT_HASH_INVALID")
    if (
        descriptor["schema_contract_sha256"]
        != descriptor["contract_members"]["base22-holder-native-exact.v3.json"]
    ):
        raise BoundedError("NATIVE_HOLDER_LOCAL_SCHEMA_BINDING_INVALID")
    _, legacy, exact = load_native_holder_contract(config)
    contract, model, extension = load_contract(config)
    ss = schemas(model, extension)
    ss[TABLE] = exact
    archives = {a["path"]: a for a in descriptor["original_native_archives"]}
    if len(archives) != len(descriptor["original_native_archives"]):
        raise BoundedError("NATIVE_HOLDER_DUPLICATE_LOCAL_ARCHIVE")
    for relative, item in archives.items():
        if item["role"] != "original_native_holder":
            raise BoundedError("NATIVE_HOLDER_ORIGINAL_ARCHIVE_ROLE_INVALID")
        member = Member(
            _physical(root, relative, independent=True),
            item["sha256"],
            item["size_bytes"],
            item["rows"],
        )
        member.verify()
    actual_archives = {
        str((stage / "archives" / "native" / p).relative_to(root))
        for p in _tree(stage / "archives" / "native")
    }
    if actual_archives != set(archives):
        raise BoundedError("NATIVE_HOLDER_EXACT_PHYSICAL_ARCHIVE_SET_INVALID")
    declared = set()
    paths: dict[str, list[Path]] = {}
    counts: dict[str, int] = {}
    native_outputs = {}
    for item in descriptor["outputs"]:
        t, relative = item["table"], item["path"]
        if t not in descriptor["output_rows"] or relative in declared:
            raise BoundedError("NATIVE_HOLDER_OUTPUT_SET_INVALID")
        path = _physical(root, relative, independent=True)
        if file_sha(path) != item["sha256"] or path.stat().st_size != item["size_bytes"]:
            raise BoundedError("NATIVE_HOLDER_OUTPUT_HASH_INVALID")
        semantics = semantic_scan([path], ss[t], check=budget.check)
        if semantics != item["semantic_scan"] or semantics["rows"] != item["rows"]:
            raise BoundedError("NATIVE_HOLDER_RECOMPUTED_SEMANTICS_INVALID")
        declared.add(relative)
        paths.setdefault(t, []).append(path)
        counts[t] = counts.get(t, 0) + semantics["rows"]
        if t == TABLE:
            native_outputs[relative] = item
    if counts != descriptor["output_rows"] or declared != {
        str((stage / "payload" / p).relative_to(root)) for p in _tree(stage / "payload")
    }:
        raise BoundedError("NATIVE_HOLDER_EXACT_PHYSICAL_OUTPUT_OR_ROWS_INVALID")
    proofs = descriptor["exact_native_precision_proofs"]
    if len(proofs) != len(native_outputs) or {p["output_local_member"] for p in proofs} != set(
        native_outputs
    ):
        raise BoundedError("NATIVE_HOLDER_EXACT_PROOF_SET_INVALID")
    used_archives = []
    for proof in proofs:
        members = []
        for relative in proof["native_local_members"]:
            item = archives[relative]
            if item["partition"] != proof["partition"]:
                raise BoundedError("NATIVE_HOLDER_PHYSICAL_PARTITION_PROOF_INVALID")
            members.append(
                Member(
                    _physical(root, relative, independent=True),
                    item["sha256"],
                    item["size_bytes"],
                    item["rows"],
                )
            )
            used_archives.append(relative)
        n = validate_native_holder_partition(
            members,
            _physical(root, proof["output_local_member"], independent=True),
            legacy_schema=legacy,
            exact_schema=exact,
            budget=budget,
        )
        if (
            n != proof["rows"]
            or proof["new_exact_rule"] != CONTRACT["rule"]
            or proof["old_columns_unchanged"] is not True
        ):
            raise BoundedError("NATIVE_HOLDER_PRECISION_PROOF_INVALID")
        import pyarrow.compute as pc  # type: ignore[import-untyped]
        import pyarrow.parquet as pq

        if (
            not proof["partition"].startswith("period_end_year=")
            or proof["partition"] != native_outputs[proof["output_local_member"]]["partition"]
        ):
            raise BoundedError("NATIVE_HOLDER_PARTITION_COORDINATE_INVALID")
        year = int(proof["partition"].split("=", 1)[1])
        for batch in pq.ParquetFile(
            _physical(root, proof["output_local_member"], independent=True)
        ).iter_batches(columns=["period_end"], batch_size=1024):
            if not pc.all(pc.equal(pc.year(batch.column(0)), year)).as_py():
                raise BoundedError("NATIVE_HOLDER_ROW_PHYSICAL_PARTITION_MISMATCH")
    if len(used_archives) != len(set(used_archives)) or set(used_archives) != set(archives):
        raise BoundedError("NATIVE_HOLDER_EXACT_ARCHIVE_PROOF_MEMBERS_INVALID")
    with GlobalScan(budget) as scan:
        for t in sorted(paths):
            scan.add(t, paths[t])
            rule = contract["tables"][t]
            scan.keys(t, rule["business_key"], rule["version_key"])
            scan.row_rules(t, ss[t], rule)
            scan.lineage(t, [tuple(v) for v in descriptor["source_registries"][t]])
        for col, parent, key in [
            ("company_id", "dwd_listed_company", "company_id"),
            ("security_id", "dwd_security", "security_id"),
        ]:
            scan.reject(
                f"SELECT 1 FROM {TABLE} r WHERE NOT EXISTS(SELECT 1 FROM {parent} p "
                f"WHERE p.{key}=r.{col} AND p.knowledge_from<=r.knowledge_from "
                "AND (p.knowledge_to IS NULL OR r.knowledge_from<p.knowledge_to) "
                "AND CAST(timezone('Asia/Shanghai',p.effective_from) AS DATE)<=r.period_end "
                "AND (p.effective_to IS NULL OR r.period_end<"
                "CAST(timezone('Asia/Shanghai',p.effective_to) AS DATE))) LIMIT 1",
                "NATIVE_HOLDER_DATED_PARENT_CLOSURE",
            )
        summary = scan.connection.execute(
            f"SELECT COUNT(*),COUNT(average_holding_exact) FROM {TABLE}"
        ).fetchone()
        if summary is None:
            raise BoundedError("NATIVE_HOLDER_PRECISION_COUNTS_UNAVAILABLE")
        total, finite = summary
        if descriptor["precision_counts"] != {
            "rows": total,
            "exact_finite": finite,
            "raw_source_null": total - finite,
        }:
            raise BoundedError("NATIVE_HOLDER_PRECISION_COUNT_INVALID")
    return {
        "status": "OFFLINE_NATIVE_HOLDER_PRECISION_PREPARATION_PASS_NOT_IMAGE_OR_CONSUMER",
        "scope_identity": descriptor["identity"],
        "rows": counts,
        "all_original_native_columns_and_exact13_values": "PASS",
        "scoped_global_keys_dates_parents_lineage": "PASS",
        "vendor_decimal_publication_provider_acquisition_or_PIT_qualification": False,
        "whole_image_schema_admission": "pending_final_candidate_review",
        "complete_Base22": False,
    }
