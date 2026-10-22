"""Bounded opt-in component additions to exact unchanged native observations.

Changed values, missing native holders and other providers need separate raw
qualification. This stage neither closes existing knowledge nor infers exits.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import (
    THROUGH,
    _physical,
    canonical,
    digest,
    file_sha,
    load_contract,
    partition,
    schemas,
)
from .base22_bounded import BoundedError, Budget, GlobalScan, identifier, semantic_scan
from .base22_financial import _clock
from .base22_holder_column_revision import _META
from .base22_holder_components_v2 import COMPONENT_KEYS, TABLE, component_key
from .base22_sources import _tree

CONTRACT_ID = "p10-base22-native-exact-unchanged-literal-component-additions-v1"
PURPOSE = "EXACT_UNCHANGED_NATIVE_AND_CERTIFIED_LITERAL_ADDITIONS_NOT_COMPLETE_WEEKLY_OR_BASE22"
FRAME_FIELDS = ("company_id", "security_id", "holding_type", "observation_start")
MAX_FRAME_ROWS = 4096


def frame_key(row: dict[str, Any]) -> str:
    return digest([row[k] for k in FRAME_FIELDS])


def body_key(row: dict[str, Any]) -> str:
    return digest({k: v for k, v in row.items() if k not in _META | {"parser_version"}})


def qualify_component_additions(
    current: list[dict[str, Any]],
    source: list[dict[str, Any]],
    *,
    source_identity: str,
    native_identity: str,
    observed_at: datetime,
) -> dict[str, Any]:
    """Consume one certified complete v2 frame, not a fallback/partial matrix.

    Source frame certification is the caller's pinned full raw/cold prerequisite.
    Existing native literal atoms must all match the complete source, including
    original scalar text/units. Only previously absent component keys are added.
    """
    clock = _clock(observed_at)
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", source_identity)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", native_identity)
        or not 0 < len(source) <= MAX_FRAME_ROWS
        or len(current) > MAX_FRAME_ROWS
        or len({frame_key(r) for r in current + source}) != 1
        or any(r["knowledge_to"] is not None for r in current + source)
        or any(clock <= _clock(r["knowledge_from"]) for r in current + source)
        or any(
            r["observation_start"].astimezone(ZoneInfo("Asia/Shanghai")).date() > THROUGH
            for r in current + source
        )
        or len({component_key(r) for r in source}) != len(source)
        or len({component_key(r) for r in current}) != len(current)
    ):
        raise BoundedError("COMPONENT_UNION_FRAME_BOUND_KEY_OR_CLOCK_INVALID")
    current_body = Counter(body_key(r) for r in current)
    source_body = Counter(body_key(r) for r in source)
    missing = current_body - source_body
    providers = {r["record_source"] for r in current + source}
    base = {
        "contract_id": CONTRACT_ID,
        "frame_key_sha256": frame_key(source[0]),
        "source_identity": source_identity,
        "native_identity": native_identity,
        "evaluator_sha256": file_sha(Path(__file__)),
        "observed_at": clock,
        "current_body_census_sha256": digest(current_body),
        "source_body_census_sha256": digest(source_body),
        "source_actual_knowledge_instants_sha256": digest([r["knowledge_from"] for r in source]),
        "current_row_census_sha256": digest(Counter(digest(r) for r in current)),
        "source_row_census_sha256": digest(Counter(digest(r) for r in source)),
        "closed_native_versions": 0,
        "provider_priority_granted": False,
        "absence_is_exit_or_zero": False,
        "consumer_admission": "NOT_GRANTED",
        "historical_PIT_admission": False,
        "complete_Base22": False,
    }
    if len(providers) != 1 or not next(iter(providers)):
        result = {**base, "status": "HELD_DIFFERENT_OR_UNKNOWN_PROVIDER", "new_rows": []}
    elif missing:
        result = {
            **base,
            "status": "HELD_CURRENT_BODY_OR_HOLDER_ABSENCE_NEEDS_EXACT_RAW_QUALIFICATION",
            "unmatched_current_rows": sum(missing.values()),
            "new_rows": [],
        }
    else:
        current_keys = {component_key(r) for r in current}
        added = [r for r in source if component_key(r) not in current_keys]
        identity = digest(
            {**base, "new_source_row_census_sha256": digest(Counter(digest(r) for r in added))}
        )
        rows = [
            {
                **r,
                "knowledge_from": clock,
                "source_revision": CONTRACT_ID + ":" + identity.removeprefix("sha256:"),
                "source_artifact_sha256": identity,
            }
            for r in added
        ]
        result = {
            **base,
            "status": "EXACT_UNCHANGED_NATIVE_PLUS_LITERAL_COMPONENTS",
            "input_identity": identity,
            "unchanged_native_rows": len(current),
            "new_rows": rows,
        }
    result["identity"] = digest(result)
    return result


def latest_source_records(
    source_root: Path, source: dict[str, Any], budget: Budget
) -> list[dict[str, Any]]:
    """One immutable metadata index, actual clocks rather than package/day order."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    latest: dict[str, dict[str, Any]] = {}
    for member in source["raw_inputs"]:
        path = _physical(source_root, member["path"], independent=True)
        if file_sha(path) != member["sha256"]:
            raise BoundedError("COMPONENT_UNION_RAW_INDEX_MEMBER_CHANGED")
        for batch in pq.ParquetFile(path).iter_batches(32):
            budget.check()
            for raw in batch.to_pylist():
                if raw["category"] != "股东研究":
                    continue
                code, acquired = raw["stock_code"], _clock(raw["acquired_at"])
                candidate = {
                    "stock_code": code,
                    "source_record_id": (
                        f"stock_code={code}&category=股东研究"
                        f"&content_sha256={raw['content_sha256']}"
                    ),
                    "source_artifact_sha256": member["original_member_sha256"],
                    "actual_acquired_at": acquired,
                    "raw_row_sha256": digest(raw),
                    "source_day": member["source_day"],
                }
                previous = latest.get(code)
                if (
                    previous
                    and acquired == previous["actual_acquired_at"]
                    and candidate != previous
                ):
                    raise BoundedError("COMPONENT_UNION_EQUAL_ACTUAL_CLOCK_CONFLICT")
                if previous is None or acquired > previous["actual_acquired_at"]:
                    latest[code] = candidate
    if len(latest) != 4992:
        raise BoundedError("COMPONENT_UNION_EXACT_PROVIDER_UNIVERSE_CHANGED")
    return [latest[k] for k in sorted(latest)]


def register_selection_views(
    scan: GlobalScan,
    native_paths: list[Path],
    source_paths: list[Path],
    records: list[dict[str, Any]],
    schema: Any,
) -> dict[str, str]:
    """Typed NULL-aware exact equality; only keys/proofs are materialized by caller."""
    import pyarrow as pa

    scan.add("native_all", native_paths)
    scan.add("component_source_all", source_paths)
    scan.connection.register("latest_records", pa.Table.from_pylist(records))
    scan.connection.execute(
        "CREATE VIEW native_open AS SELECT * FROM native_all WHERE knowledge_to IS NULL"
    )
    scan.connection.execute(
        "CREATE VIEW source_latest AS SELECT s.* FROM component_source_all s JOIN latest_records r "
        "ON s.source_record_id=r.source_record_id "
        "AND s.source_artifact_sha256=r.source_artifact_sha256"
    )
    data_fields = [f.name for f in schema if f.name not in _META | {"parser_version"}]
    same_body = " AND ".join(
        f"n.{identifier(k)} IS NOT DISTINCT FROM s.{identifier(k)}" for k in data_fields
    )
    same_frame = " AND ".join(
        f"n.{identifier(k)} IS NOT DISTINCT FROM s.{identifier(k)}" for k in FRAME_FIELDS
    )
    same_key = " AND ".join(
        f"n.{identifier(k)} IS NOT DISTINCT FROM s.{identifier(k)}" for k in COMPONENT_KEYS
    )
    columns = ",".join(identifier(k) for k in FRAME_FIELDS)
    ncolumns = ",".join("n." + identifier(k) for k in FRAME_FIELDS)
    scan.connection.execute(
        "CREATE VIEW held_frames AS SELECT DISTINCT " + ncolumns + " FROM native_open n "
        "WHERE EXISTS(SELECT 1 FROM source_latest s WHERE " + same_frame + ") "
        "AND NOT EXISTS(SELECT 1 FROM source_latest s WHERE "
        + same_body
        + " AND n.record_source=s.record_source)"
    )
    held_match = " AND ".join(
        f"h.{identifier(k)} IS NOT DISTINCT FROM s.{identifier(k)}" for k in FRAME_FIELDS
    )
    scan.connection.execute(
        "CREATE VIEW source_additions AS SELECT s.* FROM source_latest s "
        "WHERE NOT EXISTS(SELECT 1 FROM held_frames h WHERE " + held_match + ") "
        "AND NOT EXISTS(SELECT 1 FROM native_open n WHERE " + same_key + ")"
    )
    eligibility = (
        "SELECT "
        + columns
        + ",count(*) AS expected_new_rows FROM source_additions GROUP BY "
        + columns
    )
    combined = "SELECT 0 AS side,* FROM native_open UNION ALL SELECT 1 AS side,* FROM source_latest"
    ordered = (
        "SELECT * FROM ("
        + combined
        + ") ORDER BY "
        + columns
        + ",side,"
        + ",".join(identifier(k) for k in COMPONENT_KEYS)
    )
    return {"eligibility": eligibility, "ordered_frames": ordered}


def iter_selected_frames(scan: GlobalScan, query: str, schema: Any) -> Any:
    """One sorted typed cursor; memory bound is a complete frame, not all history."""
    from itertools import groupby

    reader = scan.connection.execute(query).to_arrow_reader(1024)

    def stream() -> Any:
        for batch in reader:
            scan.budget.check(reserve=batch.nbytes * 2)
            side = batch.column(0).to_pylist()
            rows = batch.select(schema.names).cast(schema, safe=True).to_pylist()
            yield from zip(side, rows, strict=True)

    for _, group in groupby(stream(), key=lambda pair: tuple(pair[1][k] for k in FRAME_FIELDS)):
        current: list[dict[str, Any]] = []
        source: list[dict[str, Any]] = []
        for side, row in group:
            target = source if side else current
            if len(target) >= MAX_FRAME_ROWS:
                raise BoundedError("COMPONENT_UNION_SORTED_FRAME_BOUND_EXCEEDED")
            target.append(row)
        yield current, source


def check_component_global_keys(scan: GlobalScan, table: str) -> None:
    """Compare all nullable component values, not just a hash or partition key."""
    keys = ",".join(identifier(k) for k in COMPONENT_KEYS)
    required = [k for k in COMPONENT_KEYS if k not in {"share_nature", "holding_percentage_unit"}]
    nulls = " OR ".join(identifier(k) + " IS NULL" for k in [*required, "knowledge_from"])
    scan.reject(f"SELECT 1 FROM {table} WHERE {nulls} LIMIT 1", "COMPONENT_UNION_REQUIRED_KEY_NULL")
    scan.reject(
        f"SELECT 1 FROM {table} GROUP BY {keys},knowledge_from HAVING count(*)>1 LIMIT 1",
        "COMPONENT_UNION_GLOBAL_VERSION_DUPLICATE",
    )
    scan.reject(
        f"SELECT 1 FROM (SELECT knowledge_from,row_number() OVER w AS rn,"
        f"lag(knowledge_to) OVER w AS previous_to FROM {table} WINDOW w AS "
        f"(PARTITION BY {keys} ORDER BY knowledge_from)) WHERE rn>1 AND "
        "(previous_to IS NULL OR knowledge_from<previous_to) LIMIT 1",
        "COMPONENT_UNION_GLOBAL_KNOWLEDGE_OVERLAP",
    )


def audit_component_union(root: Path, body: dict[str, Any], budget: Budget) -> dict[str, Any]:
    """Full local raw replay, bounded exact union and physical global constraints."""
    import pyarrow.parquet as pq

    from .base22_holder_components_v2_replay import audit_component_scope
    from .base22_holder_last_components import CONTRACT_ID as HISTORY_ID

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
            )
        )
        or _tree(root) != set(body["physical_members"]) | {"QUALIFICATION.json"}
    ):
        raise BoundedError("COMPONENT_UNION_OFFLINE_DESCRIPTOR_OR_PHYSICAL_SET_CHANGED")
    for relative, member in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if file_sha(path) != member["sha256"] or path.stat().st_size != member["size_bytes"]:
            raise BoundedError("COMPONENT_UNION_OFFLINE_PHYSICAL_MEMBER_CHANGED")
        budget.check()
    history = json.loads((root / "evidence/native-source-descriptor.json").read_bytes())
    cold = json.loads((root / "evidence/native-source-cold.json").read_bytes())
    if (
        history["contract_id"] != HISTORY_ID
        or history["identity"] != body["native_source_identity"]
        or history["identity"] != digest({k: v for k, v in history.items() if k != "identity"})
        or cold["scope_identity"] != history["identity"]
        or not cold["source_unchanged"]
        or len(cold["results"]) != 4
        or any(i["old_source_reads"] or i["network_calls"] for i in cold["results"])
        or cold["results"][0]["status"] != cold["results"][1]["status"]
        or cold["results"][0]["status"] == "EXPECTED_FAIL_CLOSED"
        or any(i["status"] != "EXPECTED_FAIL_CLOSED" for i in cold["results"][2:])
        or body["parents"] != history["parents"]
    ):
        raise BoundedError("COMPONENT_UNION_NATIVE_SOURCE_QUALIFICATION_CHANGED")
    frozen = [b["output"] for b in history["history_bindings"].values()] + history["new_outputs"]
    if set(body["history_bindings"]) != {i["path"] for i in frozen}:
        raise BoundedError("COMPONENT_UNION_EXACT_NATIVE_PARTITION_SET_CHANGED")
    source_root = root / "qualified-components"
    source = json.loads((source_root / "QUALIFICATION.json").read_bytes())
    if source["identity"] != body["component_source_identity"]:
        raise BoundedError("COMPONENT_UNION_SOURCE_IDENTITY_CHANGED")
    replay = audit_component_scope(source_root, source, budget)
    budget.begin_stage("full_bounded_component_union")
    records = latest_source_records(source_root, source, budget)
    if digest(records) != body["latest_source_records_sha256"]:
        raise BoundedError("COMPONENT_UNION_ACTUAL_SOURCE_CLOCK_SELECTION_CHANGED")
    contract, model, extension = load_contract(root / "contract")
    schema, rule = schemas(model, extension)[TABLE], contract["tables"][TABLE]
    native_paths, new_paths, source_paths = [], [], []
    for original, item in body["history_bindings"].items():
        p = _physical(root, item["path"], independent=True)
        if file_sha(p) != history["physical_members"][original]["sha256"]:
            raise BoundedError("COMPONENT_UNION_NATIVE_BYTES_CHANGED")
        native_paths.append(p)
    for item in body["new_outputs"]:
        new_paths.append(_physical(root, item["path"], independent=True))
    for item in source["outputs"].values():
        source_paths.append(_physical(source_root, item["path"], independent=True))
    for p, item in zip(
        native_paths + new_paths,
        [*body["history_bindings"].values(), *body["new_outputs"]],
        strict=True,
    ):
        pf = pq.ParquetFile(p)
        if (
            not pf.schema_arrow.equals(schema, check_metadata=False)
            or pf.metadata.num_rows != item["rows"]
            or semantic_scan([p], schema, check=budget.check) != item["semantic_scan"]
        ):
            raise BoundedError("COMPONENT_UNION_SCHEMA_OR_EXACT_ROW_CENSUS_CHANGED")
    streams = {
        i["partition"]: (
            r
            for batch in pq.ParquetFile(root / i["path"]).iter_batches(1024)
            for r in batch.to_pylist()
        )
        for i in body["new_outputs"]
    }
    if (
        len(streams) != len(body["new_outputs"])
        or set(streams) != set(body["new_partition_rows"])
        or any(i["rows"] <= 0 for i in body["new_outputs"])
    ):
        raise BoundedError("COMPONENT_UNION_PHYSICAL_NEW_PARTITION_DUPLICATE")
    counts: Counter[str] = Counter()
    new_counts: Counter[str] = Counter()
    proof = (root / "evidence/frame-proofs.jsonl").open("rb")
    try:
        with GlobalScan(budget, read_only_roots=[root]) as scan:
            queries = register_selection_views(scan, native_paths, source_paths, records, schema)
            for current, incoming in iter_selected_frames(scan, queries["ordered_frames"], schema):
                if not incoming:
                    counts["NATIVE_FRAME_WITHOUT_QUALIFIED_LATEST_SOURCE"] += 1
                    continue
                result = qualify_component_additions(
                    current,
                    incoming,
                    source_identity=source["identity"],
                    native_identity=history["identity"],
                    observed_at=datetime.fromisoformat(body["observed_at"]),
                )
                safe = {k: v for k, v in result.items() if k != "new_rows"}
                safe["new_rows_sha256"] = digest(result["new_rows"])
                safe["new_rows"] = len(result["new_rows"])
                if json.loads(proof.readline()) != json.loads(canonical(safe)):
                    raise BoundedError("COMPONENT_UNION_FRAME_PROOF_OR_DISPOSITION_CHANGED")
                counts[result["status"]] += 1
                for row in result["new_rows"]:
                    part = partition(row, rule)
                    if part not in streams or next(streams[part], None) != row:
                        raise BoundedError("COMPONENT_UNION_UNAPPROVED_NEW_ATOM_OR_TIME_CHANGE")
                    new_counts[part] += 1
            if proof.readline() or any(next(s, None) is not None for s in streams.values()):
                raise BoundedError("COMPONENT_UNION_EXTRA_OUTPUT_ROWS_OR_PROOFS")
            if (
                dict(counts) != body["frame_counts"]
                or dict(new_counts) != body["new_partition_rows"]
            ):
                raise BoundedError("COMPONENT_UNION_EXACT_FRAME_AND_PARTITION_CENSUS_CHANGED")
            scan.add(TABLE, native_paths + new_paths)
            check_component_global_keys(scan, TABLE)
            scan.row_rules(TABLE, schema, rule)
            for parent, items in body["parents"].items():
                for item in items:
                    if (
                        file_sha(root / item["path"])
                        != history["physical_members"][item["path"]]["sha256"]
                    ):
                        raise BoundedError("COMPONENT_UNION_NATIVE_PARENT_BYTES_CHANGED")
                scan.add(parent, [root / i["path"] for i in items])
            for parent, field in (
                ("dwd_listed_company", "company_id"),
                ("dwd_security", "security_id"),
            ):
                scan.reject(
                    f"SELECT 1 FROM {TABLE} h WHERE NOT EXISTS(SELECT 1 FROM {parent} p "
                    f"WHERE p.{field}=h.{field}) LIMIT 1",
                    "COMPONENT_UNION_PARENT_CLOSURE_FAILED",
                )
            census = scan.connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone()
            if census is None:
                raise BoundedError("COMPONENT_UNION_GLOBAL_CENSUS_UNAVAILABLE")
            total = census[0]
            if total != body["output_rows"] or total != history["output_rows"] + sum(
                new_counts.values()
            ):
                raise BoundedError("COMPONENT_UNION_COMPLETE_NATIVE_PLUS_ADDITIONS_CENSUS_CHANGED")
    finally:
        proof.close()
    result = {
        "status": "ALL_NATIVE_PLUS_EXACT_CERTIFIED_ADDITIONS_PASS_NOT_COMPLETE_WEEKLY_OR_BASE22",
        "scope_identity": body["identity"],
        "output_rows": total,
        "unchanged_native_rows": history["output_rows"],
        "new_governance_versions": sum(new_counts.values()),
        "closed_current_versions": 0,
        "frame_counts": dict(counts),
        "new_partition_rows": dict(new_counts),
        "source_raw_replay_sha256": digest(replay),
        "complete_Base22": False,
    }
    result["identity"] = digest(result)
    return result
