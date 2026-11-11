"""Bounded same-current-raw literal-column repairs, without changing old scopes.

One immutable selected-current Arrow table and metadata-only row index serve all
raw consumers. Each selected original is replayed once; original intervals and
all scalar/text/unit/time atoms outside the two literal columns remain exact.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .base22 import _physical, canonical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_financial import _clock
from .base22_holder_column_revision import TABLE
from .base22_holder_component_union import check_component_global_keys
from .base22_holder_components_v2 import load_component_contract
from .base22_holder_current_columns import (
    REPLAY_MODULES,
    qualify_current_columns,
)
from .base22_sources import _tree
from .financial_reporting import financial_company_resolver_from_listed_company_rows

CONTRACT_ID = "p10-base22-bounded-same-current-raw-literal-columns-native-v1"
PURPOSE = "EXACT_CURRENT_RAW_COLUMN_REPAIRS_NO_NUMERIC_PRIORITY_OR_COMPLETE_BASE22"
MAX_CURRENT_ROWS = 100000


def current_row_index(table: Any, budget: Budget) -> dict[tuple[str, str], list[int]]:
    """Only metadata is indexed in Python; affected rows remain typed Arrow."""
    if not 0 < table.num_rows <= MAX_CURRENT_ROWS or table.nbytes > 256 * 1024**2:
        raise BoundedError("BULK_CURRENT_TYPED_SELECTION_BOUND_INVALID")
    budget.check(reserve=table.nbytes * 2)
    index: dict[tuple[str, str], list[int]] = defaultdict(list)
    offset = 0
    for batch in table.select(["source_artifact_sha256", "source_record_id"]).to_batches(1024):
        for i, meta in enumerate(batch.to_pylist()):
            key = (meta["source_artifact_sha256"], meta["source_record_id"])
            if not all(key) or len(index[key]) >= 80:
                raise BoundedError("BULK_CURRENT_EXACT_RAW_RECORD_BOUND_INVALID")
            index[key].append(offset + i)
        offset += len(batch)
    return dict(index)


def apply_column_closures(
    rows: list[dict[str, Any]], closures: set[str], observed_at: datetime
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Match complete before-row SHA; close only its unique currently open copy."""
    if len(rows) > 2048 or len(closures) > MAX_CURRENT_ROWS:
        raise BoundedError("BULK_CURRENT_REWRITE_BATCH_BOUND_INVALID")
    clock = _clock(observed_at)
    output = []
    found: Counter[str] = Counter()
    for row in rows:
        sha = digest(row)
        if sha in closures:
            if row["knowledge_to"] is not None or _clock(row["knowledge_from"]) >= clock:
                raise BoundedError("BULK_CURRENT_CLOSED_OR_NONFORWARD_ORIGINAL")
            output.append({**row, "knowledge_to": clock})
            found[sha] += 1
        else:
            output.append(row)
    return output, found


def audit_bulk_columns(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """All chosen originals, exact old native physical set and full typed union."""
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.compute as pc  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != PURPOSE
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(
            body.get(k) is not False
            for k in (
                "READY",
                "complete_Base22",
                "complete_weekly_union",
                "historical_PIT_admission",
                "numeric_or_percentage_token_changes_granted",
                "provider_priority_granted",
            )
        )
        or _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}
        or set(body.get("qualifier_code_closure", {})) != set(REPLAY_MODULES)
    ):
        raise BoundedError("BULK_CURRENT_DESCRIPTOR_OR_PHYSICAL_SET_CHANGED")
    for name, sha in body["qualifier_code_closure"].items():
        if file_sha(Path(__file__).with_name(name)) != sha:
            raise BoundedError("BULK_CURRENT_QUALIFIER_CODE_CHANGED")
    for rel, i in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if file_sha(p) != i["sha256"] or p.stat().st_size != i["size_bytes"]:
            raise BoundedError("BULK_CURRENT_PHYSICAL_MEMBER_CHANGED")
        budget.check()
    source_root = root / "qualified-components"
    source = json.loads((source_root / "QUALIFICATION.json").read_bytes())
    history = json.loads((root / "evidence/native-source-descriptor.json").read_bytes())
    cold = json.loads((root / "evidence/native-source-cold.json").read_bytes())
    for d, expected in [
        (source, body["component_source_identity"]),
        (history, body["native_source_identity"]),
    ]:
        if d["identity"] != expected or d["identity"] != digest(
            {k: v for k, v in d.items() if k != "identity"}
        ):
            raise BoundedError("BULK_CURRENT_PREDECESSOR_IDENTITY_CHANGED")
    if (
        cold["scope_identity"] != history["identity"]
        or not cold["source_unchanged"]
        or len(cold["results"]) != 4
        or any(i["old_source_reads"] or i["network_calls"] for i in cold["results"])
        or any(
            i["status"]
            != "ALL_NATIVE_PLUS_EXACT_CERTIFIED_ADDITIONS_PASS_NOT_COMPLETE_WEEKLY_OR_BASE22"
            for i in cold["results"][:2]
        )
        or any(i["status"] != "EXPECTED_FAIL_CLOSED" for i in cold["results"][2:])
    ):
        raise BoundedError("BULK_CURRENT_NATIVE_QUALIFICATION_CHANGED")
    frozen = [*history["history_bindings"].values(), *history["new_outputs"]]
    if (
        set(body["history_bindings"]) != {i["path"] for i in frozen}
        or body["parents"] != history["parents"]
    ):
        raise BoundedError("BULK_CURRENT_COMPLETE_NATIVE_INPUT_SET_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    schema = schemas(model, extension)[TABLE]
    rule = contract["tables"][TABLE]
    current = pq.ParquetFile(root / body["current_before"]["path"]).read()
    if (
        not current.schema.equals(schema, check_metadata=False)
        or current.num_rows != body["qualified_keys"]
        or file_sha(root / body["current_before"]["path"]) != body["current_before"]["sha256"]
    ):
        raise BoundedError("BULK_CURRENT_SELECTED_CURRENT_CHANGED")
    index = current_row_index(current, budget)
    parents = {
        n: [
            r
            for i in source["parents"][n]
            for r in pq.ParquetFile(source_root / i["path"]).read().to_pylist()
        ]
        for n in ["dwd_security", "dwd_security_identifier"]
    }
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    component = load_component_contract(source_root / "contract")
    clock = _clock(body["observed_at"])
    selected = set()
    closures = set()
    counts: Counter[str] = Counter()
    for item in body["new_outputs"]:
        pf = pq.ParquetFile(_physical(root, item["path"], independent=True))
        if not pf.schema_arrow.equals(schema, check_metadata=False) or (
            pf.metadata.num_rows != item["rows"] or item["rows"] <= 0
        ):
            raise BoundedError("BULK_CURRENT_NEW_SCHEMA_OR_CENSUS_CHANGED")
    newstreams = {
        i["partition"]: (
            r
            for batch in pq.ParquetFile(root / i["path"]).iter_batches(1024)
            for r in batch.to_pylist()
        )
        for i in body["new_outputs"]
    }
    new_counts: Counter[str] = Counter()
    if len(newstreams) != len(body["new_outputs"]):
        raise BoundedError("BULK_CURRENT_NEW_PHYSICAL_PARTITION_DUPLICATE")
    with (root / "evidence/qualifications-safe.jsonl").open("rb") as proofs:
        for member in source["raw_inputs"]:
            p = _physical(source_root, member["path"], independent=True)
            if file_sha(p) != member["sha256"]:
                raise BoundedError("BULK_CURRENT_ORIGINAL_RAW_MEMBER_CHANGED")
            for batch in pq.ParquetFile(p).iter_batches(16):
                for raw in batch.filter(pc.equal(batch.column("category"), "股东研究")).to_pylist():
                    record = (
                        "stock_code="
                        + raw["stock_code"]
                        + "&category=股东研究&content_sha256="
                        + raw["content_sha256"]
                    )
                    key = (member["original_member_sha256"], record)
                    if key not in index:
                        continue
                    if key in selected:
                        raise BoundedError("BULK_CURRENT_ORIGINAL_RAW_RECORD_DUPLICATE")
                    selected.add(key)
                    before = current.take(pa.array(index[key])).to_pylist()
                    q = qualify_current_columns(
                        before,
                        original_raw=raw,
                        original_member_sha256=key[0],
                        resolver=resolver,
                        component_contract=component,
                        rule=rule,
                        observed_at=clock,
                    )
                    recorded = json.loads(proofs.readline())
                    expected = {
                        "original_member_sha256": key[0],
                        "source_record_id": record,
                        "raw_row_sha256": digest(raw),
                        "qualification_identity": q["identity"],
                        "candidate_rows": len(before),
                        "qualified_rows": len(q["revisions"]),
                        "held_reason_counts": dict(Counter(q["held"].values())),
                        "current_before_sha256": digest(before),
                        "revisions_safe": {
                            k: {n: v for n, v in i.items() if n != "output"}
                            for k, i in q["revisions"].items()
                        },
                        "held_key_reasons": q["held"],
                    }
                    if recorded != json.loads(canonical(expected)) or q["held"]:
                        raise BoundedError("BULK_CURRENT_ORIGINAL_REPLAY_QUALIFICATION_CHANGED")
                    counts["raw_records"] += 1
                    counts["qualified_keys"] += len(q["revisions"])
                    for i in q["revisions"].values():
                        if i["before_row_sha256"] in closures:
                            raise BoundedError("BULK_CURRENT_DUPLICATE_ORIGINAL_CLOSURE")
                        closures.add(i["before_row_sha256"])
                        new = i["output"][1]
                        part = partition(new, rule)
                        if part not in newstreams or next(newstreams[part], None) != new:
                            raise BoundedError("BULK_CURRENT_NEW_ORIGINAL_ATOM_OR_TIME_CHANGED")
                        new_counts[part] += 1
                    budget.check()
        if (
            proofs.readline()
            or selected != set(index)
            or counts["qualified_keys"] != body["qualified_keys"]
            or any(next(s, None) is not None for s in newstreams.values())
        ):
            raise BoundedError("BULK_CURRENT_EXACT_RAW_CURRENT_OR_OUTPUT_CENSUS_CHANGED")
    found: Counter[str] = Counter()
    native_paths = []
    rows = 0
    for rel, binding in body["history_bindings"].items():
        before_path = _physical(root, binding["before"]["path"], independent=True)
        out = _physical(root, binding["output"]["path"], independent=True)
        expected_meta = next(i for i in frozen if i["path"] == rel)
        if (
            file_sha(before_path) != history["physical_members"][rel]["sha256"]
            or not pq.ParquetFile(out).schema_arrow.equals(schema, check_metadata=False)
            or pq.ParquetFile(before_path).metadata.num_rows != expected_meta["rows"]
        ):
            raise BoundedError("BULK_CURRENT_BEFORE_NATIVE_BYTES_CHANGED")
        streams = (r for b in pq.ParquetFile(out).iter_batches(2048) for r in b.to_pylist())
        local: Counter[str] = Counter()
        n = 0
        for batch in pq.ParquetFile(before_path).iter_batches(2048):
            if not batch.schema.equals(schema, check_metadata=False):
                raise BoundedError("BULK_CURRENT_NATIVE_SCHEMA_CHANGED")
            expected, items = apply_column_closures(batch.to_pylist(), closures, clock)
            local.update(items)
            if [next(streams, None) for _ in expected] != expected:
                raise BoundedError("BULK_CURRENT_UNAPPROVED_NATIVE_ATOM_OR_INTERVAL_CHANGE")
            n += len(expected)
            budget.check()
        if (
            next(streams, None) is not None
            or n != binding["output"]["rows"]
            or semantic_scan([out], schema, check=budget.check)
            != binding["output"]["semantic_scan"]
        ):
            raise BoundedError("BULK_CURRENT_NATIVE_PHYSICAL_CENSUS_CHANGED")
        if not local and file_sha(out) != file_sha(before_path):
            raise BoundedError("BULK_CURRENT_UNCHANGED_NATIVE_BYTES_CHANGED")
        found.update(local)
        rows += n
        native_paths.append(out)
    if found != Counter(dict.fromkeys(closures, 1)) or rows != history["output_rows"]:
        raise BoundedError("BULK_CURRENT_EXACT_ALL_NATIVE_CLOSURE_SET_CHANGED")
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        scan.add(TABLE, native_paths + [root / i["path"] for i in body["new_outputs"]])
        check_component_global_keys(scan, TABLE)
        scan.row_rules(TABLE, schema, rule)
        for parent, items in body["parents"].items():
            for i in items:
                if file_sha(root / i["path"]) != history["physical_members"][i["path"]]["sha256"]:
                    raise BoundedError("BULK_CURRENT_PARENT_BYTES_CHANGED")
            scan.add(parent, [root / i["path"] for i in items])
        for parent, field in [
            ("dwd_listed_company", "company_id"),
            ("dwd_security", "security_id"),
        ]:
            scan.reject(
                f"SELECT 1 FROM {TABLE} h WHERE NOT EXISTS(SELECT 1 FROM {parent} p "
                f"WHERE p.{field}=h.{field}) LIMIT 1",
                "BULK_CURRENT_PARENT_CLOSURE_FAILED",
            )
    total = rows + sum(new_counts.values())
    if total != body["output_rows"] or dict(new_counts) != body["new_partition_rows"]:
        raise BoundedError("BULK_CURRENT_GLOBAL_OUTPUT_CENSUS_CHANGED")
    result = {
        "status": "ALL_NATIVE_SAME_CURRENT_RAW_LITERAL_COLUMN_REPAIRS_PASS_NOT_COMPLETE_BASE22",
        "scope_identity": body["identity"],
        "output_rows": total,
        "closed_current_versions": len(closures),
        "new_governance_versions": sum(new_counts.values()),
        "raw_records_replayed_once": len(selected),
        "all_other_native_atoms_preserved": True,
        "numeric_or_percentage_token_changes": 0,
        "provider_priority_granted": False,
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result
