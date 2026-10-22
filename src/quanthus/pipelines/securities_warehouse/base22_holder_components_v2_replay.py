"""Bounded offline raw replay for the opt-in holder component source scope."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base22 import _physical, canonical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget
from .base22_holder_components_v2 import (
    CONTRACT_ID,
    TABLE,
    component_key,
    load_component_contract,
    project_holder_components,
)
from .base22_sources import _tree
from .financial_reporting import financial_company_resolver_from_listed_company_rows

PURPOSE = "COMPLETE_DATED_COMPONENT_MATRICES_WITH_EXPLICIT_RAW_HOLDS_NOT_FINAL_UNION"
REPLAY_MODULES = (
    "base22.py",
    "base22_bounded.py",
    "base22_sources.py",
    "base22_precision.py",
    "base22_financial.py",
    "base22_f10_ownership.py",
    "base22_holder_literals.py",
    "base22_holder_column_revision.py",
    "base22_holder_column_continuation.py",
    "base22_holder_components.py",
    "base22_holder_components_replay.py",
    "base22_holder_components_v2.py",
    "base22_holder_components_v2_replay.py",
    "financial_reporting.py",
    "ownership_capital.py",
)


def replay_component_sources(
    root: Path,
    body: dict[str, Any],
    budget: Budget,
    consume: Callable[..., None],
) -> dict[str, Any]:
    """One shared parse per original raw row, bounded by one raw body's frames."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    contract = load_component_contract(root / "contract")
    _, model, extension = load_contract(root / "contract")
    physical = schemas(model, extension)
    parents = {}
    for name in ("dwd_security", "dwd_security_identifier"):
        items = body["parents"][name]
        if sum(i["rows"] for i in items) > 32768:
            raise BoundedError("HOLDER_COMPONENT_PARENT_BOUND_INVALID")
        parent_rows: list[dict[str, Any]] = []
        for item in items:
            pf = pq.ParquetFile(_physical(root, item["path"], independent=True))
            if pf.metadata.num_rows != item["rows"] or not pf.schema_arrow.equals(
                physical[name], check_metadata=False
            ):
                raise BoundedError("HOLDER_COMPONENT_PARENT_SCHEMA_OR_CENSUS_CHANGED")
            parent_rows.extend(pf.read().to_pylist())
        parents[name] = parent_rows
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"],
        parents["dwd_security_identifier"],
    )
    members = body["raw_inputs"]
    if len(members) != 4 or len({i["source_day"] for i in members}) != 4:
        raise BoundedError("HOLDER_COMPONENT_EXACT_FOUR_WEEKLY_SCOPE_CHANGED")
    counts: Counter[str] = Counter()
    held: Counter[str] = Counter()
    basis: Counter[str] = Counter()
    for item in members:
        path = _physical(root, item["path"], independent=True)
        pf = pq.ParquetFile(path)
        if (
            pf.metadata.num_rows != item["rows"]
            or not 0 < item["rows"] <= 10000
            or file_sha(path) != item["sha256"]
            or path.stat().st_size > 128 * 1024**2
        ):
            raise BoundedError("HOLDER_COMPONENT_RAW_SUBSET_HASH_OR_CENSUS_CHANGED")
        seen = set()
        for batch in pf.iter_batches(batch_size=16):
            budget.check()
            for raw in batch.to_pylist():
                if raw["category"] == "股本结构":
                    counts["capital_raw_reused_under_existing_scope"] += 1
                    continue
                if raw["category"] != "股东研究" or raw["stock_code"] in seen:
                    raise BoundedError("HOLDER_COMPONENT_RAW_CATEGORY_OR_DUPLICATE_CODE")
                seen.add(raw["stock_code"])
                try:
                    rows, proof = project_holder_components(
                        raw,
                        source_sha256=item["original_member_sha256"],
                        resolver=resolver,
                        contract=contract,
                    )
                except (BoundedError, ValueError) as error:
                    rows = {TABLE: []}
                    proof = {
                        "contract_id": CONTRACT_ID,
                        "raw_row_sha256": digest(raw),
                        "status": "HELD_RAW_CATEGORY",
                        "reason": str(error),
                    }
                    proof["identity"] = digest(proof)
                    held[str(error)] += 1
                    counts["held_whole_raw_categories"] += 1
                else:
                    counts["raw_with_all_dated_frames_qualified"] += (
                        proof["whole_category"] != "HELD"
                    )
                    counts["raw_with_held_frames_or_separate_domain"] += (
                        proof["whole_category"] == "HELD"
                    )
                    counts["raw_with_at_least_one_qualified_frame"] += bool(rows[TABLE])
                    basis.update(proof["percentage_basis_counts"])
                    for frame in proof["frames"]:
                        if frame["status"] != "QUALIFIED_COMPLETE_MATRIX":
                            held[frame["reason"]] += 1
                            counts["held_frames"] += 1
                        else:
                            counts["qualified_frames"] += 1
                            counts["blank_name_components"] += frame["census"][
                                "blank_name_components"
                            ]
                            counts["increment_comment_cells_not_events"] += frame["census"][
                                "increment_comments"
                            ]["cells"]
                    counts["separate_change_rows_not_qualified"] += sum(
                        i["nonempty_data_rows"] for i in proof["separate_nonempty_domains"]
                    )
                counts["holder_raw_rows"] += 1
                counts["typed_rows"] += len(rows[TABLE])
                consume(item, raw["stock_code"], rows[TABLE], proof)
        if len(seen) != 4992:
            raise BoundedError("HOLDER_COMPONENT_WEEKLY_EXACT_CODE_CENSUS_CHANGED")
    return {
        "counts": dict(counts),
        "held_reason_counts": dict(held),
        "literal_percentage_basis_counts": dict(basis),
        "full_union": "NOT_QUALIFIED",
    }


def audit_component_scope(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """All files, all raw rows, all physical typed rows and global version keys."""
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != PURPOSE
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("consumer_admission") != "NOT_GRANTED"
        or body.get("READY") is not False
        or body.get("complete_Base22") is not False
        or _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}
        or set(body["code_closure"]) != set(REPLAY_MODULES)
    ):
        raise BoundedError("HOLDER_COMPONENT_OFFLINE_DESCRIPTOR_OR_FILE_SET_CHANGED")
    for name, sha in body["code_closure"].items():
        if file_sha(Path(__file__).with_name(name)) != sha:
            raise BoundedError("HOLDER_COMPONENT_OFFLINE_CODE_CHANGED")
    for relative, member in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if file_sha(path) != member["sha256"] or path.stat().st_size != member["size_bytes"]:
            raise BoundedError("HOLDER_COMPONENT_OFFLINE_PHYSICAL_MEMBER_CHANGED")
        budget.check()
    _, model, extension = load_contract(root / "contract")
    schema = schemas(model, extension)[TABLE]
    streams, proofs = {}, {}
    for day, item in body["outputs"].items():
        pf = pq.ParquetFile(_physical(root, item["path"], independent=True))
        if (
            not pf.schema_arrow.equals(schema, check_metadata=False)
            or pf.metadata.num_rows != item["rows"]
        ):
            raise BoundedError("HOLDER_COMPONENT_OFFLINE_TYPED_SCHEMA_OR_CENSUS_CHANGED")
        streams[day] = (r for batch in pf.iter_batches(batch_size=1024) for r in batch.to_pylist())
        proofs[day] = _physical(root, item["proof_path"], independent=True).open("rb")
    temp = budget.root / "temp"
    temp.mkdir()
    db = duckdb.connect(
        str(temp / "keys.duckdb"),
        config={
            "memory_limit": str(budget.limits.memory_bytes) + "B",
            "max_temp_directory_size": str(budget.limits.temp_bytes) + "B",
            "temp_directory": str(temp / "spill"),
            "threads": "1",
            "autoload_known_extensions": "false",
            "autoinstall_known_extensions": "false",
        },
    )
    budget._connections.append(db)
    db.execute("CREATE TABLE keys(k VARCHAR)")
    pending: list[str] = []

    def flush_keys() -> None:
        if not pending:
            return
        db.register("key_batch", pa.table({"k": pending}))
        try:
            db.execute("INSERT INTO keys SELECT k FROM key_batch")
        finally:
            db.unregister("key_batch")
        pending.clear()

    def compare(
        item: dict[str, Any], code: str, expected: list[dict[str, Any]], proof: dict[str, Any]
    ) -> None:
        day = item["source_day"]
        try:
            recorded = json.loads(next(proofs[day]))
            actual = [next(streams[day]) for _ in expected]
        except (StopIteration, ValueError):
            raise BoundedError("HOLDER_COMPONENT_OFFLINE_STREAM_TRUNCATED") from None
        if (
            recorded != {"stock_code": code, "proof": json.loads(canonical(proof))}
            or actual != expected
        ):
            raise BoundedError("HOLDER_COMPONENT_OFFLINE_RAW_OR_TYPED_REPLAY_CHANGED")
        if expected:
            pending.extend(digest([component_key(r), r["knowledge_from"]]) for r in expected)
            if len(pending) >= 8192:
                flush_keys()
        budget.check()

    try:
        result = replay_component_sources(root, body, budget, compare)
        flush_keys()
        sentinel = object()
        if any(next(stream, sentinel) is not sentinel for stream in streams.values()) or any(
            next(p, sentinel) is not sentinel for p in proofs.values()
        ):
            raise BoundedError("HOLDER_COMPONENT_OFFLINE_EXTRA_PHYSICAL_ROWS_OR_PROOFS")
        if db.execute("SELECT k FROM keys GROUP BY k HAVING count(*) > 1 LIMIT 1").fetchone():
            raise BoundedError("HOLDER_COMPONENT_OFFLINE_GLOBAL_VERSION_DUPLICATE")
        if result != body["replay_summary"]:
            raise BoundedError("HOLDER_COMPONENT_OFFLINE_COMPLETE_CENSUS_CHANGED")
    finally:
        for handle in proofs.values():
            handle.close()
        db.close()
    return {
        "status": "ALL_LOCAL_RAW_FRAME_SCHEMA_ATOM_GLOBAL_VERSION_KEYS_PASS_NOT_FINAL_UNION",
        **result,
    }
