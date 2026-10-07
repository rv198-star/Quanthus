"""Bounded knowledge versions for open, individually qualified ownership atoms.

The caller supplies independent, job-owned typed files and archives every source
observation. This successor covers open native observations only: closed native
intervals need their own reviewed transition contract and cannot be reopened.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from itertools import groupby
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, identifier, semantic_scan
from .base22_f10_ownership import TABLES
from .base22_outputs import knowledge_versions
from .base22_ownership_audit import OwnershipReplaySink, audit_ownership_observations
from .base22_sources import _tree

CONTRACT_ID = "p10-base22-open-ownership-observation-versions-v1"
MAX_VERSIONS_PER_KEY = 64


def ownership_versions(rows: list[dict[str, Any]], rule: dict[str, Any]) -> list[dict[str, Any]]:
    """No priority at equal clocks; only observed successor clocks close intervals."""
    if not rows or len(rows) > MAX_VERSIONS_PER_KEY:
        raise BoundedError("OWNERSHIP_VERSION_GROUP_BOUND_INVALID")
    keys = {digest([r[k] for k in rule["business_key"]]) for r in rows}
    if len(keys) != 1 or any(r["knowledge_to"] is not None for r in rows):
        raise BoundedError("OWNERSHIP_OPEN_OBSERVATION_VERSION_SCOPE_INVALID")
    result = knowledge_versions(rows, rule)
    # The helper copies atoms; incoming source rows and raw source evidence remain
    # immutable. Duplicate unchanged observations are retained in that evidence.
    for row in result:
        atoms = digest({k: v for k, v in row.items() if k != "knowledge_to"})
        if not any(
            atoms == digest({k: v for k, v in original.items() if k != "knowledge_to"})
            for original in rows
        ):
            raise BoundedError("OWNERSHIP_VERSION_ATOM_OR_ORIGINAL_CLOCK_CHANGED")
    return result


def version_batches(
    table: str,
    paths: list[Path],
    rule: dict[str, Any],
    schema: Any,
    budget: Budget,
    *,
    read_only_roots: list[Path] | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Global typed sort spills within the frozen budget; each key is bounded."""
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        table not in TABLES
        or not paths
        or rule["version_key"] != (rule["business_key"] + ["knowledge_from"])
    ):
        raise BoundedError("OWNERSHIP_VERSION_TABLE_OR_KEY_CONTRACT_INVALID")
    with GlobalScan(budget, read_only_roots=read_only_roots) as scan:
        scan.add(table, paths)
        scan.connection.execute("SET TimeZone='UTC'")
        if any(
            not pq.ParquetFile(p).schema_arrow.equals(schema, check_metadata=False) for p in paths
        ):
            raise BoundedError("OWNERSHIP_VERSION_INPUT_SCHEMA_CHANGED")
        order = ",".join(identifier(k) for k in rule["business_key"] + ["knowledge_from"])
        reader = scan.connection.execute(
            f"SELECT * FROM {identifier(table)} ORDER BY {order}"
        ).fetch_record_batch(4096)

        def source() -> Iterator[dict[str, Any]]:
            for batch in reader:
                if batch.schema.names != schema.names or any(
                    actual.type != expected.type
                    and not (
                        pa.types.is_timestamp(actual.type)
                        and pa.types.is_timestamp(expected.type)
                        and actual.type.unit == expected.type.unit
                        and actual.type.tz in {"UTC", "Etc/UTC"}
                        and expected.type.tz == "UTC"
                    )
                    for actual, expected in zip(batch.schema, schema, strict=True)
                ):
                    raise BoundedError("OWNERSHIP_VERSION_INPUT_SCHEMA_CHANGED")
                budget.check()
                # DuckDB marks fields nullable and names UTC as Etc/UTC. Pinned
                # original Parquet types above permit only this lossless recast.
                yield from batch.cast(schema, safe=True).to_pylist()

        key_fields = rule["business_key"]
        for _, group in groupby(source(), key=lambda r: tuple(r[k] for k in key_fields)):
            rows: list[dict[str, Any]] = []
            for row in group:
                if len(rows) >= MAX_VERSIONS_PER_KEY:
                    raise BoundedError("OWNERSHIP_VERSION_GROUP_BOUND_INVALID")
                rows.append(row)
            budget.check()
            yield ownership_versions(rows, rule)


def audit_ownership_union(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Raw replay, exact native closure and every allowed version atom offline."""
    import pyarrow.parquet as pq

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != "QUALIFIED_OPEN_OWNERSHIP_OBSERVATION_NATIVE_UNION"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or not body.get("selected_tables")
        or not set(body["selected_tables"]) <= set(TABLES)
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(body.get(k) is not False for k in ("READY", "complete_Base22"))
    ):
        raise BoundedError("OWNERSHIP_UNION_OFFLINE_SCOPE_INVALID")
    if _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}:
        raise BoundedError("OWNERSHIP_UNION_PHYSICAL_FILE_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if p.stat().st_size != item["size_bytes"] or file_sha(p) != item["sha256"]:
            raise BoundedError("OWNERSHIP_UNION_PHYSICAL_MEMBER_CHANGED")
        budget.check()
    observations = root / body["observations_root"]
    obs = json.loads(_physical(observations, "QUALIFICATION.json", independent=True).read_bytes())
    raw_replay = audit_ownership_observations(observations, obs, budget)
    if obs["identity"] != body["observation_scope_identity"]:
        raise BoundedError("OWNERSHIP_UNION_RAW_SCOPE_IDENTITY_CHANGED")
    contract, model, extension = load_contract(observations / "contract")
    ss = schemas(model, extension)
    original = _physical(root, body["original_native_manifest"], independent=True)
    if file_sha(original) != body["original_native_manifest_sha256"]:
        raise BoundedError("OWNERSHIP_UNION_ORIGINAL_MANIFEST_CHANGED")
    manifest = json.loads(original.read_bytes())
    selected = set(body["selected_tables"])
    expected_native = {
        i["path"]: i for i in manifest["files"] if any("/" + t + "/" in i["path"] for t in selected)
    }
    declared_native = {i["original_path"]: i for i in body["native_members"]}
    if not expected_native or set(expected_native) != set(declared_native):
        raise BoundedError("OWNERSHIP_UNION_EXACT_NATIVE_MEMBER_SET_CHANGED")
    input_paths: dict[str, list[Path]] = {t: [] for t in selected}
    source_lineage: dict[str, set[tuple[str, str, str]]] = {t: set() for t in selected}
    native_rows: Counter[str] = Counter()
    for relative, item in declared_native.items():
        p = _physical(root, item["path"], independent=True)
        pf = pq.ParquetFile(p)
        table = item["table"]
        if (
            table not in selected
            or "/" + table + "/" not in relative
            or expected_native[relative]["sha256"] != file_sha(p)
            or pf.metadata.num_rows != item["rows"]
            or not pf.schema_arrow.equals(ss[table], check_metadata=False)
        ):
            raise BoundedError("OWNERSHIP_UNION_NATIVE_ATOMS_OR_SCHEMA_CHANGED")
        native_rows[table] += item["rows"]
        input_paths[table].append(p)
    for i in obs["outputs"]:
        if i["table"] in selected:
            input_paths[i["table"]].append(observations / i["path"])
    for table, paths in input_paths.items():
        for path in paths:
            for batch in pq.ParquetFile(path).iter_batches(
                batch_size=4096,
                columns=["record_source", "source_revision", "source_artifact_sha256"],
            ):
                budget.check()
                source_lineage[table].update(tuple(r.values()) for r in batch.to_pylist())
    outputs = {(i["table"], i["partition"], i["number"]): i for i in body["outputs"]}
    if len(outputs) != len(body["outputs"]) or {k[0] for k in outputs} != selected:
        raise BoundedError("OWNERSHIP_UNION_EXACT_OUTPUT_TABLE_SET_CHANGED")
    checked = set()

    def compare(table: str, part: str, number: int, expected: list[dict[str, Any]]) -> None:
        key = table, part, number
        item = outputs.get(key)
        if item is None or key in checked or item["rows"] != len(expected):
            raise BoundedError("OWNERSHIP_UNION_EXACT_OUTPUT_SHARD_SET_CHANGED")
        p = _physical(root, item["path"], independent=True)
        pf = pq.ParquetFile(p)
        if (
            not pf.schema_arrow.equals(ss[table], check_metadata=False)
            or pf.metadata.num_rows != len(expected)
            or len(expected) > 2048
            or pf.read().to_pylist() != expected
            or semantic_scan([p], ss[table], check=budget.check) != item["semantic_scan"]
        ):
            raise BoundedError("OWNERSHIP_UNION_REPRODUCED_ALLOWED_VERSION_ATOMS_CHANGED")
        checked.add(key)

    sink = OwnershipReplaySink(contract["tables"], compare)
    for table in sorted(selected):
        for rows in version_batches(
            table,
            input_paths[table],
            contract["tables"][table],
            ss[table],
            budget,
            read_only_roots=[root],
        ):
            sink.add({table: rows})
    sink.finish()
    if checked != set(outputs) or dict(sink.counts) != body["output_rows"]:
        raise BoundedError("OWNERSHIP_UNION_REPRODUCED_GLOBAL_CENSUS_CHANGED")
    closure = selected | set(body["parents"])
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for table in sorted(selected):
            scan.add(table, [root / i["path"] for i in outputs.values() if i["table"] == table])
            scan.keys(
                table,
                contract["tables"][table]["business_key"],
                contract["tables"][table]["version_key"],
            )
            scan.row_rules(table, ss[table], contract["tables"][table])
            scan.lineage(table, sorted(source_lineage[table]))
        for table, items in body["parents"].items():
            scan.add(table, [observations / i["path"] for i in items])
        scan.references(
            {**model, "tables": [t for t in model["tables"] if t["name"] in closure]},
            {**contract, "tables": {t: contract["tables"][t] for t in closure}},
        )
    return {
        "status": "EXACT_QUALIFIED_OWNERSHIP_NATIVE_VERSION_UNION_PASS_COVERAGE_GATES_OPEN",
        "scope_identity": body["identity"],
        "output_rows": dict(sink.counts),
        "native_rows": dict(native_rows),
        "raw_replay": raw_replay,
        "whole_domain_August_coverage": "NOT_QUALIFIED",
        "complete_Base22": False,
    }
