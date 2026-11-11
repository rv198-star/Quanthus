"""Cold reconstruction of event/identity TMP; no external source-root lookup.

This checks exact partition files, all typed atoms, keys, times and per-table
lineage. Integrated cross-table references remain a separate full-assembly gate;
earlier source-domain qualification is preserved, not enlarged by this receipt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .base22 import digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, semantic_scan
from .base22_financial_union import _ColumnSummary
from .base22_native_coordinates import check_decoded
from .base22_sources import _tree, physical_path
from .base23_native_keys import NativeGlobalScan
from .base231_financial_integration import native_batches
from .base231_observation_integration import TABLES, observation_views

QUALIFICATIONS = {
    "body": "p10-base231-exact-official-disclosure-body-observation-v1",
    "large_body": "p10-base231-exact-official-disclosure-body-observation-v1",
    "names": "p10-base231-official-dated-stock-short-name-v1",
    "terms": "p10-base231-dated-official-implemented-distribution-terms-v1",
    "cash": "p10-base231-official-sse-complete-cash-plan-reference-input-v1",
    "origin": "p10-base231-official-profile-current-financial-origin-v1",
}
CODE = {
    "base231_observation_integration.py",
    "base231_financial_integration.py",
    "base231_disclosure_body.py",
    "base231_image.py",
    "base22_bounded.py",
    "base23_native_keys.py",
    "base22_native_keys.py",
}


def audit_observation_tmp(root: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    root = physical_path(root)
    plan = json.loads((root / "integration-TMP.json").read_bytes())
    if (
        plan.get("kind") != "P10_BASE231_EVENT_IDENTITY_INTEGRATION_TMP_V1"
        or plan.get("lifecycle") != "TMP"
        or plan.get("complete_Base") is not False
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("business_through") != "2026-09-30"
        or plan.get("business_timezone") != "Asia/Shanghai"
        or plan.get("READY_written") is not False
        or plan.get("historical_PIT_qualified") is not False
        or plan.get("whole_image_acceptance") is not False
        or plan.get("integrated_cross_table_references")
        != "PENDING_FULL_ASSEMBLY_GATES_SOURCE_DOMAINS_ALREADY_QUALIFIED"
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
        or {k: q.get("contract_id") for k, q in plan.get("source_qualifications", {}).items()}
        != QUALIFICATIONS
    ):
        raise BoundedError("OBSERVATION_TMP_OFFLINE_SCOPE_CHANGED")
    if set(plan["layout"]) != TABLES or set(plan["inputs"]) != TABLES:
        raise BoundedError("OBSERVATION_TMP_TABLE_SET_CHANGED")
    if _tree(root) != set(plan["members"]) | {"integration-TMP.json"}:
        raise BoundedError("OBSERVATION_TMP_PHYSICAL_SET_CHANGED")

    def local(name: str) -> Path:
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or name not in plan["members"]:
            raise BoundedError("OBSERVATION_TMP_LOCAL_PATH_INVALID")
        path = physical_path(root / p)
        if root not in path.parents:
            raise BoundedError("OBSERVATION_TMP_LOCAL_PATH_INVALID")
        return path

    for name, item in plan["members"].items():
        p = local(name)
        if (
            p.stat().st_nlink != 1
            or p.stat().st_size != item["bytes"]
            or file_sha(p) != item["sha256"]
        ):
            raise BoundedError("OBSERVATION_TMP_MEMBER_BYTES_CHANGED")
    if set(plan["code_inputs"]) != CODE or any(
        file_sha(Path(__file__).parent / n) != h for n, h in plan["code_inputs"].items()
    ):
        raise BoundedError("OBSERVATION_TMP_ORDINARY_CODE_CHANGED")
    outputs = {x["path"] for ms in plan["layout"].values() for x in ms}
    if {"outputs/" + n for n in _tree(root / "outputs")} != outputs:
        raise BoundedError("OBSERVATION_TMP_OUTPUT_SET_CHANGED")
    dirs = {Path("outputs")}
    for name in outputs:
        dirs.update(p for p in Path(name).parents if p != Path("."))

    def fail(error: OSError) -> None:
        raise BoundedError("OBSERVATION_TMP_DIRECTORY_SCAN_FAILED") from error

    if any(
        Path(p).relative_to(root) not in dirs
        for p, _, _ in os.walk(root / "outputs", onerror=fail, followlinks=False)
    ):
        raise BoundedError("OBSERVATION_TMP_OUTPUT_DIRECTORY_CHANGED")
    rules, model, ext = load_contract(root / "config")
    ss = schemas(model, ext)
    readonly = [] if root == budget.root or budget.root in root.parents else [root]
    summaries = {}
    for table in sorted(TABLES):
        with NativeGlobalScan(budget, read_only_roots=readonly) as scan:
            for name, key in [("before", "native"), ("incoming_raw", "incoming")]:
                scan.add(name, [local(p) for p in plan["inputs"][table][key]])
            proof = observation_views(scan, table, ss[table], rules["tables"][table])
            if proof != plan["proofs"][table]:
                raise BoundedError("OBSERVATION_TMP_REVISION_RECONSTRUCTION_CHANGED")
            expected = _ColumnSummary(ss[table])
            for batch in native_batches(
                scan, "integrated", ss[table], rules["tables"][table]["version_key"]
            ):
                expected.add(batch)
            actual = _ColumnSummary(ss[table])
            files, parts = [], set()
            for item in plan["layout"][table]:
                path = local(item["path"])
                part = item["partition"]
                pf = pq.ParquetFile(path)
                if (
                    part in parts
                    or item["table"] != table
                    or str(Path(item["path"]).parent) != "outputs/" + table + "/" + part
                    or not pf.schema_arrow.equals(ss[table], check_metadata=False)
                    or pf.metadata.num_rows != item["rows"]
                    or item["rows"] <= 0
                    or file_sha(path) != item["sha256"]
                    or path.stat().st_size != item["size_bytes"]
                ):
                    raise BoundedError("OBSERVATION_TMP_PARTITION_SCHEMA_CENSUS_CHANGED")
                parts.add(part)
                files.append(path)
                for batch in pf.iter_batches(batch_size=budget.limits.batch_rows):
                    budget.check()
                    check_decoded(batch, part, rules["tables"][table])
            scan.add("serialized", files)
            for batch in native_batches(
                scan, "serialized", ss[table], rules["tables"][table]["version_key"]
            ):
                actual.add(batch)
            if expected.finish() != actual.finish():
                raise BoundedError("OBSERVATION_TMP_TYPED_ATOMS_CHANGED")
            scan.keys(
                "serialized",
                rules["tables"][table]["business_key"],
                rules["tables"][table]["version_key"],
            )
            scan.row_rules("serialized", ss[table], rules["tables"][table])
            scan.lineage("serialized", [tuple(x) for x in plan["lineage"][table]])
            summaries[table] = semantic_scan(files, ss[table], check=budget.check)
            if summaries[table] != plan["summaries"][table]:
                raise BoundedError("OBSERVATION_TMP_SEMANTIC_SUMMARY_CHANGED")
    return dict(
        identity=plan["identity"],
        summaries=summaries,
        complete_Base=False,
        archived_roots_dereferenced=False,
        consumer_admission="NOT_GRANTED",
        integrated_cross_table_references="NOT_RUN_FULL_ASSEMBLY_GATE",
    )
