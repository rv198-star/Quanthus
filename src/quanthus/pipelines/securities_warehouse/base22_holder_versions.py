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
from .base22_f10_ownership import ownership_observation as legacy_observation
from .base22_holder_literals import TABLE, holder_observation
from .base22_holder_rolling_revision import apply_floor_revision, replay_floor_evidence
from .base22_outputs import knowledge_versions
from .base22_ownership_audit import OwnershipReplaySink, audit_ownership_observations
from .base22_sources import _tree
from .financial_reporting import financial_company_resolver_from_listed_company_rows

TABLES = (TABLE,)
CONTRACT_ID = "p10-base22-literal-holder-native-versions-v2"
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
    qualified_floor_revisions: dict[str, Any] | None = None,
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
        applied_floor_revisions = set()
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
            # The background monitor and each Arrow/write batch perform the
            # complete physical census. Do not rescan thousands of archived
            # files for every business key; propagate any monitored breach now.
            if budget.error:
                raise BoundedError(budget.error)
            correction = (qualified_floor_revisions or {}).get(
                digest([rows[0][k] for k in key_fields])
            )
            if correction is not None:
                if table != TABLES[0]:
                    raise BoundedError("OWNERSHIP_FLOOR_TABLE_SCOPE_INVALID")
                applied_floor_revisions.add(correction["business_key_sha256"])
            yield (
                apply_floor_revision(rows, rule, correction)
                if correction is not None
                else ownership_versions(rows, rule)
            )
        if applied_floor_revisions != set(qualified_floor_revisions or {}):
            raise BoundedError("OWNERSHIP_FLOOR_QUALIFIED_REVISION_NOT_CONSUMED")


def audit_holder_union(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Raw replay, exact native closure and every allowed version atom offline."""
    import pyarrow.parquet as pq

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != "QUALIFIED_LITERAL_HOLDER_OBSERVATION_NATIVE_UNION"
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or body.get("selected_tables") != [TABLE]
        or "floor_revision_evidence" not in body
        or "extra_holder_sources" not in body
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
    floor_proof = None
    if "floor_revision_evidence" in body:
        # This is a distinct, raw-reproduced new-knowledge successor, never an
        # exemption from the predecessor's equal-clock conflict check.
        parent_rows = {}
        for t in ("dwd_security", "dwd_security_identifier"):
            items = obs["parents"][t]
            if sum(i["rows"] for i in items) > 32768:
                raise BoundedError("OWNERSHIP_FLOOR_LOCAL_PARENT_BOUND_INVALID")
            parent_rows[t] = [
                r
                for i in items
                for r in pq.ParquetFile(_physical(observations, i["path"], independent=True))
                .read()
                .to_pylist()
            ]
        resolver = financial_company_resolver_from_listed_company_rows(
            parent_rows["dwd_security"], parent_rows["dwd_security_identifier"]
        )
        floor_proof = replay_floor_evidence(
            root,
            body["floor_revision_evidence"],
            resolver=resolver,
            rule=contract["tables"][TABLES[0]],
            schema=ss[TABLES[0]],
        )
        if floor_proof["identity"] != body["floor_revision_identity"]:
            raise BoundedError("OWNERSHIP_FLOOR_QUALIFIED_IDENTITY_CHANGED")
    extra_members, extra_evidence = replay_extra_holder_sources(
        root, body, observations, obs, resolver, ss[TABLE], budget
    )
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
    input_paths[TABLE].extend(extra_members)
    for table, paths in input_paths.items():
        for path in paths:
            for batch in pq.ParquetFile(path).iter_batches(
                batch_size=4096,
                columns=["record_source", "source_revision", "source_artifact_sha256"],
            ):
                budget.check()
                source_lineage[table].update(tuple(r.values()) for r in batch.to_pylist())
    if floor_proof:
        source_lineage[TABLES[0]].update(
            (r["record_source"], r["source_revision"], r["source_artifact_sha256"])
            for i in floor_proof["revisions"].values()
            for r in i["output"]
        )
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
            qualified_floor_revisions=(
                floor_proof["revisions"] if floor_proof and table == TABLES[0] else None
            ),
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
        "holder_rolling_replay": extra_evidence,
        "qualified_repaired_native_keys": len(floor_proof["revisions"]) if floor_proof else 0,
        "whole_domain_August_coverage": "NOT_QUALIFIED",
        "complete_Base22": False,
    }


def replay_extra_holder_sources(
    root: Path,
    body: dict[str, Any],
    observations: Path,
    obs: dict[str, Any],
    resolver: Any,
    schema: Any,
    budget: Budget,
) -> tuple[list[Path], dict[str, Any]]:
    """Match every successor raw atom to the original local weekly scope once.

    Only the two originally held last-week captures receive extra typed files.
    Earlier successful captures stay in the immutable predecessor scope. This
    avoids duplicate groups and never turns the separate change domain into PASS.
    """
    import pyarrow.parquet as pq

    evidence = body["floor_revision_evidence"]
    if len(evidence["raw_inputs"]) != 5 or len(body["extra_holder_sources"]) != 2:
        raise BoundedError("HOLDER_UNION_EXACT_FIVE_CAPTURE_SCOPE_REQUIRED")
    raws = []
    for member in evidence["raw_inputs"]:
        pf = pq.ParquetFile(_physical(root, member["path"], independent=True))
        if pf.metadata.num_rows != 1:
            raise BoundedError("HOLDER_UNION_LOCAL_RAW_COUNT_CHANGED")
        raw = pf.read().to_pylist()[0]
        if digest(raw) != member["raw_row_sha256"]:
            raise BoundedError("HOLDER_UNION_LOCAL_RAW_ATOM_CHANGED")
        raws.append(raw)
    linked = []
    for n, member in enumerate(obs["raw_local_members"], start=1):
        if n >= len(raws):
            raise BoundedError("HOLDER_UNION_PREDECESSOR_WEEKLY_SET_CHANGED")
        source = next(s for s in obs["source_members"] if s["day"] == member["source_day"])
        if source["member"]["sha256"] != evidence["raw_inputs"][n]["source_sha256"]:
            raise BoundedError("HOLDER_UNION_ORIGINAL_MEMBER_IDENTITY_CHANGED")
        matches: list[dict[str, Any]] = []
        for batch in pq.ParquetFile(
            _physical(observations, member["path"], independent=True)
        ).iter_batches(batch_size=64):
            budget.check()
            matches.extend(
                r
                for r in batch.to_pylist()
                if r["stock_code"] == raws[n]["stock_code"] and r["category"] == "股东研究"
            )
        if len(matches) != 1 or digest(matches[0]) != digest(raws[n]):
            raise BoundedError("HOLDER_UNION_EXACT_LOCAL_PREDECESSOR_RAW_BINDING_FAILED")
        try:
            previous, _ = legacy_observation(
                raws[n], source_sha256=source["member"]["sha256"], resolver=resolver
            )
        except (BoundedError, RuntimeError):
            if n not in {3, 4}:
                raise BoundedError("HOLDER_UNION_EARLIER_SOURCE_WAS_NOT_QUALIFIED") from None
            linked.append({"source_index": n, "predecessor": "HELD_NONEMPTY_RAW_CATEGORY"})
        else:
            if n in {3, 4} or not previous[TABLE]:
                raise BoundedError("HOLDER_UNION_SUCCESSOR_DUPLICATES_QUALIFIED_CAPTURE")
            current, _ = holder_observation(
                raws[n], source_sha256=source["member"]["sha256"], resolver=resolver
            )
            if previous != current:
                raise BoundedError("HOLDER_UNION_EARLIER_QUALIFIED_TYPED_ATOMS_CHANGED")
            linked.append({"source_index": n, "predecessor": "QUALIFIED_TYPED_ATOMS_UNCHANGED"})
    if len(linked) != 4:
        raise BoundedError("HOLDER_UNION_PREDECESSOR_WEEKLY_SET_CHANGED")
    paths = []
    replays = []
    if [i["source_index"] for i in body["extra_holder_sources"]] != [3, 4]:
        raise BoundedError("HOLDER_UNION_EXTRA_SOURCE_SCOPE_CHANGED")
    for item in body["extra_holder_sources"]:
        n = item["source_index"]
        rows, proof = holder_observation(
            raws[n], source_sha256=evidence["raw_inputs"][n]["source_sha256"], resolver=resolver
        )
        p = _physical(root, item["path"], independent=True)
        pf = pq.ParquetFile(p)
        if (
            pf.metadata.num_rows != 80
            or not pf.schema_arrow.equals(schema, check_metadata=False)
            or pf.read().to_pylist() != rows[TABLE]
            or proof["identity"] != item["projection_identity"]
        ):
            raise BoundedError("HOLDER_UNION_EXTRA_TYPED_LITERAL_ATOM_OR_CLOCK_CHANGED")
        paths.append(p)
        replays.append(
            {
                "source_index": n,
                "projection_identity": proof["identity"],
                "separate_nonempty_domains": proof["separate_nonempty_domains"],
                "rows": 80,
            }
        )
    return paths, {
        "linked_original_captures": linked,
        "extra_sources": replays,
        "separate_change_domain": "NOT_GRANTED",
        "whole_domain_August_coverage": "NOT_QUALIFIED",
    }
