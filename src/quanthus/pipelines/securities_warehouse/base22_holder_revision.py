"""Bounded same-provider holder knowledge revisions, not historical PIT admission.

Callers qualify the local raw capture closure first. This kernel accepts one later
observed version per existing entity, preserves its old body and closes only the
old open knowledge interval. Equal clocks and alternate identities/providers fail.
The native exact13 preparation remains pending whole-image schema admission.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .base22 import file_sha
from .base22_bounded import BoundedError, Budget, Member, _batches, semantic_scan
from .base22_holder_exact import validate_holder_row
from .base22_sources import _fingerprint

CONTRACT_ID = "p10-base22-later-same-provider-holder-revision-v1"
NATURAL = ("company_id", "security_id", "period_end", "observation_scope")
ENTITY = "shareholder_count_observation_id"
MAX_CAPTURES = 32768


def _captures(member: Member, capture_schema: Any, target_schema: Any) -> dict[str, Any]:
    import pyarrow as pa  # type: ignore[import-untyped]

    if not 0 < member.rows <= MAX_CAPTURES:
        raise BoundedError("HOLDER_LATER_CAPTURE_SCOPE_INVALID")
    member.verify()
    result = {}
    for batch in _batches([member.path], capture_schema, 1024):
        for row in batch.to_pylist():
            validate_holder_row(row)
            if row[ENTITY] in result or row["knowledge_to"] is not None:
                raise BoundedError("HOLDER_LATER_SINGLE_OBSERVED_VERSION_REQUIRED")
            # Safe Arrow widening, never DuckDB decimal->double or quantization.
            row["average_holding_exact"] = pa.scalar(
                row["average_holding_exact"], type=target_schema.field("average_holding_exact").type
            ).as_py()
            result[row[ENTITY]] = row
    if len(result) != member.rows:
        raise BoundedError("HOLDER_LATER_CAPTURE_ROW_CENSUS_INVALID")
    return result


def _expected(
    native: list[Member], captures: dict[str, Any], schema: Any, budget: Budget
) -> Iterator[Any]:
    import pyarrow as pa

    seen = set()
    for batch in _batches([m.path for m in native], schema, 1024):
        budget.check(reserve=batch.nbytes * 3)
        ends = batch.column("knowledge_to").to_pylist()
        fields = [ENTITY, *NATURAL, "record_source", "knowledge_from", "knowledge_to"]
        for i, old in enumerate(batch.select(fields).to_pylist()):
            new = captures.get(old[ENTITY])
            if new is None:
                continue
            if (
                old[ENTITY] in seen
                or old["knowledge_to"] is not None
                or new["knowledge_from"] <= old["knowledge_from"]
                or new["record_source"] != old["record_source"]
                or any(new[k] != old[k] for k in NATURAL)
            ):
                raise BoundedError("HOLDER_LATER_IDENTITY_PROVIDER_OR_KNOWLEDGE_INVALID")
            seen.add(old[ENTITY])
            ends[i] = new["knowledge_from"]
        columns = list(batch.columns)
        columns[schema.get_field_index("knowledge_to")] = pa.array(
            ends, type=schema.field("knowledge_to").type
        )
        yield pa.RecordBatch.from_arrays(columns, schema=schema)
    if seen != set(captures):
        raise BoundedError("HOLDER_LATER_EXISTING_NATIVE_ENTITY_REQUIRED")
    rows = [captures[key] for key in sorted(captures)]
    for offset in range(0, len(rows), 1024):
        batch = pa.RecordBatch.from_pylist(rows[offset : offset + 1024], schema=schema)
        budget.check(reserve=batch.nbytes * 3)
        yield batch


def validate_holder_revision_partition(
    native: list[Member],
    capture: Member,
    output: Path,
    *,
    capture_schema: Any,
    target_schema: Any,
    budget: Budget,
) -> int:
    """Compare every typed old/new atom and permitted interval end across row groups."""
    captured = _captures(capture, capture_schema, target_schema)
    actual = iter(_batches([output], target_schema, 1024))
    current = next(actual, None)
    position = rows = 0
    for batch in _expected(native, captured, target_schema, budget):
        at = 0
        while at < batch.num_rows:
            if current is None:
                raise BoundedError("HOLDER_LATER_SERIALIZATION_ROW_LOSS")
            count = min(batch.num_rows - at, current.num_rows - position)
            if not batch.slice(at, count).equals(current.slice(position, count)):
                raise BoundedError("HOLDER_LATER_TYPED_BODY_OR_INTERVAL_CHANGED")
            at += count
            position += count
            rows += count
            if position == current.num_rows:
                current, position = next(actual, None), 0
    if current is not None:
        raise BoundedError("HOLDER_LATER_SERIALIZATION_EXTRA_ROWS")
    if rows != sum(m.rows for m in native) + capture.rows:
        raise BoundedError("HOLDER_LATER_NATIVE_ROW_CENSUS_INVALID")
    return rows


def materialize_holder_revision_partition(
    native: list[Member],
    capture: Member,
    output: Path,
    *,
    capture_schema: Any,
    target_schema: Any,
    budget: Budget,
) -> dict[str, Any]:
    """One independently owned Parquet, then full readback; never publish READY."""
    import pyarrow as pa
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        not target_schema.field("average_holding_exact").type.equals(pa.decimal256(41, 13))
        or not capture_schema.field("average_holding_exact").type.equals(pa.decimal128(38, 11))
        or not target_schema.equals(
            capture_schema.set(
                capture_schema.get_field_index("average_holding_exact"),
                pa.field("average_holding_exact", pa.decimal256(41, 13)),
            )
        )
        or not native
    ):
        raise BoundedError("HOLDER_LATER_PREPARATION_SCHEMA_INVALID")
    members = [*native, capture]
    if len({m.path for m in members}) != len(members):
        raise BoundedError("HOLDER_LATER_DUPLICATE_INPUT")
    before = [m.verify() for m in members]
    captured = _captures(capture, capture_schema, target_schema)
    output = budget.destination(output)
    partial = output.with_name(output.name + ".partial")
    if output.exists() or partial.exists():
        raise BoundedError("HOLDER_LATER_OUTPUT_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        partial.open("xb") as stream,
        pq.ParquetWriter(stream, target_schema, compression="zstd") as writer,
    ):
        for batch in _expected(native, captured, target_schema, budget):
            writer.write_batch(batch)
    rows = validate_holder_revision_partition(
        native,
        capture,
        partial,
        capture_schema=capture_schema,
        target_schema=target_schema,
        budget=budget,
    )
    if any(
        _fingerprint(m.path) != fp or file_sha(m.path) != m.sha256
        for m, fp in zip(members, before, strict=True)
    ):
        raise BoundedError("HOLDER_LATER_INPUT_CHANGED")
    with partial.open("rb") as reader:
        os.fsync(reader.fileno())
    os.link(partial, output)  # Owned partial only; never link a source member.
    partial.unlink()
    return {
        "rows": rows,
        "old_rows": sum(m.rows for m in native),
        "new_rows": capture.rows,
        "old_knowledge_ends_closed": capture.rows,
        "old_body_columns_unchanged": True,
        "sha256": file_sha(output),
        "size_bytes": output.stat().st_size,
        "semantic_scan": semantic_scan([output], target_schema, check=budget.check),
        "contract_id": CONTRACT_ID,
        "consumer_admission": "NOT_GRANTED",
        "whole_image_schema_admission": "pending_final_candidate_review",
    }


def audit_holder_revision_scope(
    root: Path, descriptor: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Offline raw/native replay, physical set and global closure of this domain scope."""
    import pyarrow.compute as pc  # type: ignore[import-untyped]
    import pyarrow.parquet as pq

    from .base22 import _physical, digest, load_contract, schemas
    from .base22_bounded import GlobalScan
    from .base22_holder_exact import TABLE, audit_holder_exact_scope, load_holder_contract
    from .base22_holder_native_exact import audit_native_holder_scope, load_native_holder_contract
    from .base22_sources import _member, _tree

    if (
        descriptor.get("kind") != "isolated_later_holder_native_union_not_complete_base22"
        or descriptor.get("projection_contract_id") != CONTRACT_ID
        or descriptor.get("identity")
        != digest({k: v for k, v in descriptor.items() if k != "identity"})
        or descriptor.get("consumer_admission") != "NOT_GRANTED"
        or descriptor.get("whole_image_schema_admission") != "pending_final_candidate_review"
        or descriptor.get("whole33_acceptance") is not False
        or descriptor.get("READY") is not False
        or descriptor.get("original_4173_visibility") != "UNCHANGED_UNAVAILABLE"
    ):
        raise BoundedError("HOLDER_REVISION_SCOPE_CONTRACT_INVALID")
    stage = _member(root, "observations/" + descriptor["label"])
    if not stage.is_dir():
        raise BoundedError("HOLDER_REVISION_LOCAL_STAGE_DIRECTORY_REQUIRED")
    files = descriptor["physical_members"]
    if set(_tree(stage)) - {"OBSERVATIONS.json"} != set(files):
        raise BoundedError("HOLDER_REVISION_EXACT_PHYSICAL_MEMBER_SET_INVALID")
    for relative, item in files.items():
        p = _physical(root, str((stage / relative).relative_to(root)), independent=True)
        if p.stat().st_size != item["size_bytes"] or file_sha(p) != item["sha256"]:
            raise BoundedError("HOLDER_REVISION_PHYSICAL_MEMBER_HASH_INVALID")
    config = stage / "contract"
    _, _, exact = load_native_holder_contract(config)
    _, capture_schema = load_holder_contract(config)
    contract, model, extension = load_contract(config)
    ss = schemas(model, extension)
    ss[TABLE] = exact
    nr = _member(root, descriptor["native_preparation_root"])
    cr = _member(root, descriptor["qualified_capture_root"])
    if not nr.is_dir() or not cr.is_dir():
        raise BoundedError("HOLDER_REVISION_LOCAL_ARCHIVE_DIRECTORIES_REQUIRED")
    ndp = _physical(nr, descriptor["native_descriptor_path"], independent=True)
    cdp = _physical(cr, descriptor["capture_descriptor_path"], independent=True)
    if (
        file_sha(ndp) != descriptor["native_descriptor_sha256"]
        or file_sha(cdp) != descriptor["capture_descriptor_sha256"]
    ):
        raise BoundedError("HOLDER_REVISION_ARCHIVED_DESCRIPTOR_HASH_INVALID")
    native = json.loads(ndp.read_bytes())
    capture = json.loads(cdp.read_bytes())
    _member(nr, "observations/" + native["label"])
    _member(cr, "observations/" + capture["label"])
    audit_native_holder_scope(nr, native, budget)
    if capture.get("input_kind") != "retained_raw_governance":
        raise BoundedError("HOLDER_REVISION_REAL_RAW_CLOSURE_REQUIRED")
    audit_holder_exact_scope(cr, capture, cr / "observations" / capture["label"] / "contract")
    manifests = {}
    for item in capture["original_manifests"]:
        p = _physical(cr, item["path"], independent=True)
        if file_sha(p) != item["sha256"] or item["sha256"] != item["original_manifest_sha256"]:
            raise BoundedError("HOLDER_REVISION_ORIGINAL_RELEASE_HASH_INVALID")
        manifests[item["sha256"]] = json.loads(p.read_bytes())
    raw_archives = {a["sha256"]: a for a in capture["archived_true_source_observations"]}
    for item in capture["inputs"]:
        mf = manifests.get(item["original_manifest_sha256"])
        if item["record_source"] != "eastmoney_rpt_holdernum_det" or mf is None:
            raise BoundedError("HOLDER_REVISION_CAPTURE_PROVIDER_CLOSURE_INVALID")
        matches = [
            a
            for a in mf["artifacts"]
            if a["name"] == "shareholder_count" and a["sha256"] == item["sha256"]
        ]
        if len(matches) != 1 or item["sha256"] not in raw_archives:
            raise BoundedError("HOLDER_REVISION_RAW_RELEASE_MEMBER_CLOSURE_INVALID")
        if (cr / raw_archives[item["sha256"]]["path"]).stat().st_size != matches[0]["size_bytes"]:
            raise BoundedError("HOLDER_REVISION_RAW_RELEASE_MEMBER_SIZE_INVALID")
    noutputs = {o["path"]: o for o in native["outputs"]}
    coutputs = {o["path"]: o for o in capture["outputs"]}
    seen_native = set()
    seen_capture = set()
    paths: dict[str, list[Path]] = {}
    counts: dict[str, int] = {}
    for item in descriptor["outputs"]:
        n = item["native_prior_member"]
        prior = noutputs[n]
        t = item["table"]
        if n in seen_native or t != prior["table"]:
            raise BoundedError("HOLDER_REVISION_NATIVE_PARTITION_SET_INVALID")
        seen_native.add(n)
        p = _physical(root, item["path"], independent=True)
        semantics = semantic_scan([p], ss[t], check=budget.check)
        if semantics != item["semantic_scan"] or semantics["rows"] != item["rows"]:
            raise BoundedError("HOLDER_REVISION_RECOMPUTED_SEMANTICS_INVALID")
        if item.get("capture_member") is None:
            if file_sha(p) != prior["sha256"]:
                raise BoundedError("HOLDER_REVISION_UNCHANGED_NATIVE_FILE_CHANGED")
        else:
            c = item["capture_member"]
            incoming = coutputs[c]
            if (
                t != TABLE
                or c in seen_capture
                or item["partition"] != prior["partition"]
                or item["partition"] != incoming["partition"]
            ):
                raise BoundedError("HOLDER_REVISION_AFFECTED_PARTITION_SET_INVALID")
            seen_capture.add(c)
            validate_holder_revision_partition(
                [Member(nr / n, prior["sha256"], prior["size_bytes"], prior["rows"])],
                Member(cr / c, incoming["sha256"], incoming["size_bytes"], incoming["rows"]),
                p,
                capture_schema=capture_schema,
                target_schema=exact,
                budget=budget,
            )
        if t == TABLE:
            year = int(item["partition"].split("=", 1)[1])
            for b in pq.ParquetFile(p).iter_batches(columns=["period_end"], batch_size=1024):
                if not pc.all(pc.equal(pc.year(b.column(0)), year)).as_py():
                    raise BoundedError("HOLDER_REVISION_ROW_PARTITION_MISMATCH")
        paths.setdefault(t, []).append(p)
        counts[t] = counts.get(t, 0) + item["rows"]
    if (
        seen_native != set(noutputs)
        or seen_capture != set(coutputs)
        or counts != descriptor["output_rows"]
    ):
        raise BoundedError("HOLDER_REVISION_EXACT_NATIVE_CAPTURE_ROW_SET_INVALID")
    if {str(p.relative_to(stage / "payload")) for ps in paths.values() for p in ps} != set(
        _tree(stage / "payload")
    ):
        raise BoundedError("HOLDER_REVISION_EXACT_PAYLOAD_SET_INVALID")
    registry = {t: set(tuple(v) for v in native["source_registries"][t]) for t in paths}
    registry[TABLE].update(
        (s["record_source"], s["revision"], s["sha256"]) for s in capture["inputs"]
    )
    with GlobalScan(budget) as scan:
        for t, ps in paths.items():
            scan.add(t, ps)
            rule = contract["tables"][t]
            scan.keys(t, rule["business_key"], rule["version_key"])
            scan.row_rules(t, ss[t], rule)
            scan.lineage(t, sorted(registry[t]))
        for col, parent, key in [
            ("company_id", "dwd_listed_company", "company_id"),
            ("security_id", "dwd_security", "security_id"),
        ]:
            scan.reject(
                f"SELECT 1 FROM {TABLE} r WHERE NOT EXISTS(SELECT 1 FROM {parent} p "
                f"WHERE p.{key}=r.{col} AND p.knowledge_from<=r.knowledge_from "
                "AND(p.knowledge_to IS NULL OR r.knowledge_from<p.knowledge_to) "
                "AND CAST(timezone('Asia/Shanghai',p.effective_from)AS DATE)<=r.period_end "
                "AND(p.effective_to IS NULL OR r.period_end<"
                "CAST(timezone('Asia/Shanghai',p.effective_to)AS DATE)))LIMIT 1",
                "HOLDER_REVISION_DATED_PARENT_CLOSURE_INVALID",
            )
    return {
        "status": "OFFLINE_HOLDER_REVISIONS_NATIVE_UNION_PASS_NOT_BASE22_OR_PIT",
        "scope_identity": descriptor["identity"],
        "rows": counts,
        "new_raw_reproduction": "PASS",
        "old_body_and_exact_permitted_interval_edits": "PASS",
        "whole_image_schema_admission": "pending_final_candidate_review",
        "complete_Base22": False,
    }
