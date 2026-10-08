"""No-priority union of native history and certified source change observations.

A possible native observation for the same company/security/literal holder/day
blocks the whole incoming event series. Neither source aliases nor transaction
identities are inferred; the complete qualified source remains local evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, identifier, semantic_scan
from .base22_holder_change_source import TABLE, audit_change_source_scope
from .base22_sources import _tree

CONTRACT_ID = "p10-base22-native-history-plus-nonoverlapping-qualified-holder-changes-v1"
PURPOSE = "NATIVE_AND_DISTINCT_SOURCE_OBSERVATIONS_NO_ALIAS_PRIORITY_OR_COMPLETE_BASE22"
BROAD_KEYS = ("company_id", "security_id", "holder_match_key", "effective_at")
NATIVE_MANIFEST = "sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b"


def register_change_selection(
    scan: GlobalScan, native_paths: list[Path], source_paths: list[Path]
) -> str:
    """All prior native versions participate, not just the current partition."""
    scan.add("native_change", native_paths)
    scan.add("source_change", source_paths)
    missing = " OR ".join(
        [identifier(k) + " IS NULL" for k in BROAD_KEYS]
        + ["length(trim(" + identifier(k) + "))=0" for k in BROAD_KEYS[:-1]]
    )
    scan.reject(
        "SELECT 1 FROM source_change WHERE " + missing + " LIMIT 1",
        "HOLDER_CHANGE_UNION_SOURCE_IDENTITY_UNKNOWN",
    )
    match = " AND ".join(
        f"n.{identifier(k)} IS NOT DISTINCT FROM s.{identifier(k)}" for k in BROAD_KEYS
    )
    query = (
        "SELECT s.* FROM source_change s WHERE NOT EXISTS(SELECT 1 FROM native_change n WHERE "
        + match
        + ")"
    )
    scan.connection.execute("CREATE VIEW eligible_changes AS " + query)
    return "SELECT * FROM eligible_changes ORDER BY shareholding_change_id,knowledge_from"


def change_union_batches(scan: GlobalScan, query: str, schema: Any) -> Any:
    """Typed streaming projection; numerical/text/time atoms remain unchanged."""
    for batch in scan.connection.execute(query).to_arrow_reader(1024):
        scan.budget.check(reserve=batch.nbytes * 2)
        yield batch.cast(schema, safe=True)


def audit_change_union(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Full original raw replay followed by full native and source union checks."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("purpose") != PURPOSE
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("evaluator_sha256") != file_sha(Path(__file__))
        or body.get("native_manifest_sha256") != NATIVE_MANIFEST
        or body.get("consumer_admission") != "NOT_GRANTED"
        or any(
            body.get(k) is not False
            for k in (
                "READY",
                "complete_Base22",
                "complete_weekly_union",
                "historical_PIT_admission",
                "cross_provider_alias_or_priority_granted",
            )
        )
        or _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}
    ):
        raise BoundedError("HOLDER_CHANGE_UNION_OFFLINE_DESCRIPTOR_OR_FILE_SET_CHANGED")
    for rel, member in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if file_sha(p) != member["sha256"] or p.stat().st_size != member["size_bytes"]:
            raise BoundedError("HOLDER_CHANGE_UNION_OFFLINE_MEMBER_CHANGED")
        budget.check()
    manifest_path = root / "evidence/native-manifest.json"
    if file_sha(manifest_path) != NATIVE_MANIFEST:
        raise BoundedError("HOLDER_CHANGE_UNION_NATIVE_MANIFEST_CHANGED")
    native = json.loads(manifest_path.read_bytes())
    selected = {i["path"]: i for i in native["files"] if "/" + TABLE + "/" in i["path"]}
    if set(selected) != set(body["history_bindings"]):
        raise BoundedError("HOLDER_CHANGE_UNION_EXACT_NATIVE_PHYSICAL_SET_CHANGED")
    source_root = root / "qualified-change-source"
    source = json.loads((source_root / "QUALIFICATION.json").read_bytes())
    if source["identity"] != body["source_identity"] or body["parents"] != source["parents"]:
        raise BoundedError("HOLDER_CHANGE_UNION_SOURCE_QUALIFICATION_CHANGED")
    replay = audit_change_source_scope(source_root, source, budget)
    budget.begin_stage("full_native_nonoverlap_union")
    contract, model, extension = load_contract(root / "contract")
    schema, rule = schemas(model, extension)[TABLE], contract["tables"][TABLE]
    native_paths, new_paths = [], []
    native_rows = 0
    for rel, item in body["history_bindings"].items():
        p = _physical(root, item["path"], independent=True)
        if file_sha(p) != selected[rel]["sha256"]:
            raise BoundedError("HOLDER_CHANGE_UNION_NATIVE_ATOMS_OR_BYTES_CHANGED")
        pf = pq.ParquetFile(p)
        if (
            not pf.schema_arrow.equals(schema, check_metadata=False)
            or pf.metadata.num_rows != item["rows"]
        ):
            raise BoundedError("HOLDER_CHANGE_UNION_NATIVE_SCHEMA_OR_ROWS_CHANGED")
        if semantic_scan([p], schema, check=budget.check) != item["semantic_scan"]:
            raise BoundedError("HOLDER_CHANGE_UNION_NATIVE_SEMANTIC_SUMMARY_CHANGED")
        native_rows += item["rows"]
        native_paths.append(p)
    if native_rows != native["row_counts"][TABLE]:
        raise BoundedError("HOLDER_CHANGE_UNION_COMPLETE_NATIVE_CENSUS_CHANGED")
    streams = {}
    expected_counts = body["new_partition_rows"]
    for item in body["new_outputs"]:
        part = item["partition"]
        if part in streams or part not in expected_counts or item["rows"] <= 0:
            raise BoundedError("HOLDER_CHANGE_UNION_NEW_PHYSICAL_PARTITION_SET_CHANGED")
        p = _physical(root, item["path"], independent=True)
        pf = pq.ParquetFile(p)
        if (
            not pf.schema_arrow.equals(schema, check_metadata=False)
            or pf.metadata.num_rows != item["rows"]
        ):
            raise BoundedError("HOLDER_CHANGE_UNION_NEW_SCHEMA_OR_CENSUS_CHANGED")
        if semantic_scan([p], schema, check=budget.check) != item["semantic_scan"]:
            raise BoundedError("HOLDER_CHANGE_UNION_NEW_SEMANTIC_SUMMARY_CHANGED")
        new_paths.append(p)
        streams[part] = (r for batch in pf.iter_batches(1024) for r in batch.to_pylist())
    if set(streams) != set(expected_counts):
        raise BoundedError("HOLDER_CHANGE_UNION_NEW_PARTITION_MISSING")
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        query = register_change_selection(
            scan, native_paths, [source_root / i["path"] for i in source["outputs"].values()]
        )
        actual_counts = dict.fromkeys(expected_counts, 0)
        for batch in change_union_batches(scan, query, schema):
            for row in batch.to_pylist():
                part = partition(row, rule)
                if part not in streams or next(streams[part], None) != row:
                    raise BoundedError(
                        "HOLDER_CHANGE_UNION_EXACT_SOURCE_ATOM_TIME_OR_PARTITION_CHANGED"
                    )
                actual_counts[part] += 1
        if actual_counts != expected_counts or any(
            next(s, None) is not None for s in streams.values()
        ):
            raise BoundedError("HOLDER_CHANGE_UNION_UNAPPROVED_OR_MISSING_SOURCE_ROWS")
        source_rows = sum(i["rows"] for i in source["outputs"].values())
        held = source_rows - sum(actual_counts.values())
        if held != body["held_native_potential_same_holder_day_versions"]:
            raise BoundedError("HOLDER_CHANGE_UNION_POTENTIAL_CROSS_PROVIDER_COINCIDENCE_CHANGED")
        scan.add(TABLE, native_paths + new_paths)
        scan.keys(TABLE, rule["business_key"], rule["version_key"])
        scan.row_rules(TABLE, schema, rule)
        scan.reject(
            f"SELECT 1 FROM {TABLE} WHERE disclosure_id IS NOT NULL "
            "OR shareholder_id IS NOT NULL LIMIT 1",
            "HOLDER_CHANGE_UNION_OPTIONAL_REFERENCE_SCOPE_UNQUALIFIED",
        )
        for parent, items in body["parents"].items():
            for item in items:
                if (
                    file_sha(root / item["path"])
                    != source["physical_members"][item["path"]]["sha256"]
                ):
                    raise BoundedError("HOLDER_CHANGE_UNION_PARENT_BYTES_CHANGED")
            scan.add(parent, [root / i["path"] for i in items])
        for parent, field in (
            ("dwd_listed_company", "company_id"),
            ("dwd_security", "security_id"),
        ):
            scan.reject(
                f"SELECT 1 FROM {TABLE} h WHERE NOT EXISTS(SELECT 1 FROM {parent} p "
                f"WHERE p.{field}=h.{field}) LIMIT 1",
                "HOLDER_CHANGE_UNION_PARENT_CLOSURE_FAILED",
            )
        total = native_rows + sum(actual_counts.values())
        if total != body["output_rows"]:
            raise BoundedError("HOLDER_CHANGE_UNION_COMPLETE_CENSUS_CHANGED")
    result = {
        "status": "NATIVE_AND_NONOVERLAPPING_QUALIFIED_OBSERVATIONS_PASS_NOT_COMPLETE_BASE22",
        "scope_identity": body["identity"],
        "output_rows": total,
        "unchanged_native_rows": native_rows,
        "new_source_versions": sum(actual_counts.values()),
        "held_native_potential_same_holder_day_versions": held,
        "closed_current_versions": 0,
        "new_governance_versions": 0,
        "source_observation_knowledge_instants_unchanged": True,
        "source_raw_replay_sha256": digest(replay),
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result
