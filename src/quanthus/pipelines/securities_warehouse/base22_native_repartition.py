"""Bounded representation-only Shanghai repartition of pinned qualified rows."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha, partition
from .base22_bounded import BoundedError, Budget
from .base22_native_coordinates import check_decoded
from .base22_native_image import _write, typed_summary
from .base22_sources import _fingerprint, _member, physical_path


def repartition_table(
    *,
    plan: dict[str, Any],
    table: str,
    rule: dict[str, Any],
    schema: Any,
    budget: Budget,
    code_revision: str,
) -> dict[str, Any]:
    """Keep every original atom; only file/partition representation changes.

    One source batch feeds both its partition projection and exact typed witness.
    New files are independently reopened before their hashes enter the descriptor.
    No accepted-child, original normalization, acquisition or new knowledge claim.
    """
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    entry = plan["tables"][table]
    columns = list(
        dict.fromkeys(
            f.rsplit("_", 1)[0] if f.endswith(("_year", "_month")) else f
            for f in rule["partition_by"]
        )
    )
    descriptors = {}
    for sid in entry["source_ids"]:
        binding = plan["sources"][sid]
        path = _member(physical_path(Path(binding["root"])), binding["descriptor"])
        if file_sha(path) != binding["descriptor_sha256"]:
            raise BoundedError("REPARTITION_SOURCE_DESCRIPTOR_CHANGED")
        descriptors[sid] = path.read_bytes()
    files: list[dict[str, Any]] = []
    proofs: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    input_hashes = {}
    for member in entry["files"]:
        source = _member(
            physical_path(Path(plan["sources"][member["source_id"]]["root"])), member["path"]
        )
        before = _fingerprint(source)
        if before[2] != member["size_bytes"] or file_sha(source) != member["sha256"]:
            raise BoundedError("REPARTITION_PINNED_INPUT_CHANGED")
        pf = pq.ParquetFile(source)
        if (
            not pf.schema_arrow.equals(schema, check_metadata=False)
            or pf.metadata.num_rows != member["rows"]
        ):
            raise BoundedError("REPARTITION_INPUT_SCHEMA_OR_ROWS_CHANGED")
        input_hashes[member["source_id"] + ":" + member["path"]] = member["sha256"]
        offset = 0
        for batch in pf.iter_batches(batch_size=budget.limits.batch_rows):
            budget.check()
            coords = pa.Table.from_arrays(
                [batch.column(batch.schema.get_field_index(f)) for f in columns], names=columns
            ).to_pylist()
            groups: dict[str, list[int]] = {}
            for n, row in enumerate(coords):
                groups.setdefault(partition(row, rule), []).append(n)
            for part, indices in sorted(groups.items()):
                expected = pa.Table.from_batches([batch]).take(pa.array(indices, type=pa.int64()))
                check_decoded(expected.to_batches()[0], part, rule)
                relative = (
                    "payload/"
                    + table
                    + "/"
                    + part
                    + "/part-"
                    + str(len(files)).zfill(6)
                    + ".parquet"
                )
                target = budget.destination(budget.root / relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(
                    expected, target, compression="zstd", row_group_size=budget.limits.batch_rows
                )
                actual = pq.ParquetFile(target).read()
                if not expected.equals(actual, check_metadata=False):
                    raise BoundedError("REPARTITION_ORIGINAL_ATOM_CHANGED")
                summary = typed_summary([target], schema, check=budget.check, part=part, rule=rule)
                item = {
                    "path": relative,
                    "table": table,
                    "partition": part,
                    "rows": len(indices),
                    "size_bytes": target.stat().st_size,
                    "sha256": file_sha(target),
                }
                files.append(item)
                counts[part] += len(indices)
                proofs.append(
                    {
                        "output": relative,
                        "source_id": member["source_id"],
                        "source_member": member["path"],
                        "source_sha256": member["sha256"],
                        "source_batch_start": offset,
                        "source_row_indices": indices,
                        "decoded_original_atoms_equal_after_serialization": True,
                        "typed_summary": summary,
                    }
                )
            offset += batch.num_rows
        if (
            offset != member["rows"]
            or _fingerprint(source) != before
            or file_sha(source) != member["sha256"]
        ):
            raise BoundedError("REPARTITION_SOURCE_CHANGED_DURING_PROCESSING")
    if sum(counts.values()) != entry["rows"]:
        raise BoundedError("REPARTITION_ROW_CONSERVATION_FAILED")
    for sid, data in descriptors.items():
        path = budget.root / "evidence" / (sid + "-original-descriptor.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.open("xb").write(data)
    _write(budget.root / "evidence/row-conservation.json", proofs)
    result = {
        "kind": "p10_native_shanghai_representation_only_v1",
        "code_revision": code_revision,
        "table": table,
        "rows": sum(counts.values()),
        "partition_rows": dict(counts),
        "files": files,
        "input_member_digest": digest(input_hashes),
        "input_plan_digest": digest(plan),
        "original_descriptor_sha256": {
            sid: plan["sources"][sid]["descriptor_sha256"] for sid in descriptors
        },
        "witness_sha256": file_sha(budget.root / "evidence/row-conservation.json"),
        "all_original_values_and_times_preserved": True,
        "new_provider_observation_or_knowledge_or_original_normalization_claim": False,
    }
    result["identity"] = digest(result)
    _write(budget.root / "manifest.json", result)
    budget.check()
    return result
