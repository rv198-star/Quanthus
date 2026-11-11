"""Offline audit of the financial integration TMP, using only its local files.

This rebuilds report revisions and concept selection from the contained immutable
typed dependencies. It scans every serialized financial output, globally checks
its versions and temporal references, and compares every typed input/output
column. A prior-image collision receipt is retained evidence, not independent
acceptance of the absent eighty-million-row parent fact table or a complete Base.
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
from .base231_financial_integration import (
    financial_references,
    integrate_concepts,
    native_batches,
    report_revision_views,
)

TABLES = {
    "dwd_financial_report": "reports",
    "dwd_financial_concept": "concepts",
    "dwd_financial_fact": "facts",
}
QUALIFICATIONS = {
    "p10-base231-new-current-complete-statement-observations-v1",
    "p10-base231-current-origin-bounded-prelisting-statements-v1",
    "p10-base231-official-profile-current-financial-origin-v1",
}


def audit_financial_tmp(root: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    root = physical_path(root)
    plan = json.loads((root / "integration-TMP.json").read_bytes())
    if (
        plan.get("kind") != "P10_BASE231_FINANCIAL_INTEGRATION_TMP_V1"
        or plan.get("lifecycle") != "TMP"
        or plan.get("complete_Base") is not False
        or plan.get("consumer_admission") != "NOT_GRANTED"
        or plan.get("historical_PIT_qualified") is not False
        or plan.get("business_through") != "2026-09-30"
        or plan.get("business_timezone") != "Asia/Shanghai"
        or plan.get("READY_written") is not False
        or plan.get("identity") != digest({k: v for k, v in plan.items() if k != "identity"})
        or {q["contract_id"] for q in plan["source_qualifications"]} != QUALIFICATIONS
    ):
        raise BoundedError("FINANCIAL_TMP_OFFLINE_PURPOSE_OR_QUALIFICATION_CHANGED")
    if set(plan["layout"]) != set(TABLES) or set(plan["inputs"]) != set(TABLES):
        raise BoundedError("FINANCIAL_TMP_EXACT_TABLE_SET_CHANGED")
    if _tree(root) != set(plan["members"]) | {"integration-TMP.json"}:
        raise BoundedError("FINANCIAL_TMP_EXACT_PHYSICAL_SET_CHANGED")
    outputs = {item["path"] for files in plan["layout"].values() for item in files}
    if {"outputs/" + n for n in _tree(root / "outputs")} != outputs:
        raise BoundedError("FINANCIAL_TMP_OUTPUT_PHYSICAL_SET_CHANGED")
    expected_dirs = {Path("outputs")}
    for name in outputs:
        expected_dirs.update(p for p in Path(name).parents if p != Path("."))

    def scan_error(error: OSError) -> None:
        raise BoundedError("FINANCIAL_TMP_OUTPUT_DIRECTORY_SCAN_FAILED") from error

    for directory, _, _ in os.walk(root / "outputs", onerror=scan_error, followlinks=False):
        if Path(directory).relative_to(root) not in expected_dirs:
            raise BoundedError("FINANCIAL_TMP_OUTPUT_DIRECTORY_SET_CHANGED")

    def local(name: str) -> Path:
        p = Path(name)
        if p.is_absolute() or not p.parts or ".." in p.parts:
            raise BoundedError("FINANCIAL_TMP_LOCAL_PATH_INVALID")
        path = physical_path(root / p)
        if root not in path.parents or name not in plan["members"]:
            raise BoundedError("FINANCIAL_TMP_LOCAL_PATH_INVALID")
        return path

    for name, item in plan["members"].items():
        p = local(name)
        if (
            p.stat().st_nlink != 1
            or p.stat().st_size != item["bytes"]
            or file_sha(p) != item["sha256"]
        ):
            raise BoundedError("FINANCIAL_TMP_MEMBER_BYTES_CHANGED")
    required_code = {
        "base231_financial_integration.py",
        "base22_outputs.py",
        "base231_image.py",
        "base22_bounded.py",
        "base23_native_keys.py",
        "base22_native_keys.py",
    }
    if set(plan["code_inputs"]) != required_code or any(
        file_sha(Path(__file__).parent / n) != h for n, h in plan["code_inputs"].items()
    ):
        raise BoundedError("FINANCIAL_TMP_ORDINARY_CODE_CHANGED")
    rules, model, ext = load_contract(root / "config")
    ss = schemas(model, ext)
    before = pq.ParquetFile(local(plan["native_concepts"])).read().to_pylist()
    incoming = [
        r
        for n in plan["inputs"]["dwd_financial_concept"]
        for r in pq.ParquetFile(local(n)).read().to_pylist()
    ]
    concepts, proof = integrate_concepts(before, incoming)
    if proof != plan["concept_proof"]:
        raise BoundedError("FINANCIAL_TMP_CONCEPT_RECONSTRUCTION_CHANGED")
    summaries = {}
    # Build may use its own writable TMP inputs; cold mode supplies a real readonly mount.
    readonly = (
        [root]
        if root not in budget.root.parents
        and budget.root not in root.parents
        and root != budget.root
        else []
    )
    with NativeGlobalScan(budget, read_only_roots=readonly) as scan:
        for table, name in TABLES.items():
            files, parts = [], set()
            for item in plan["layout"][table]:
                p = local(item["path"])
                part = item["partition"]
                if (
                    part in parts
                    or item["table"] != table
                    or str(Path(item["path"]).parent) != "outputs/" + table + "/" + part
                ):
                    raise BoundedError("FINANCIAL_TMP_PHYSICAL_PARTITION_CHANGED")
                parts.add(part)
                pf = pq.ParquetFile(p)
                if (
                    not pf.schema_arrow.equals(ss[table], check_metadata=False)
                    or pf.metadata.num_rows != item["rows"]
                    or item["rows"] <= 0
                    or file_sha(p) != item["sha256"]
                    or p.stat().st_size != item["size_bytes"]
                ):
                    raise BoundedError("FINANCIAL_TMP_SERIALIZED_SCHEMA_CENSUS_CHANGED")
                for batch in pf.iter_batches(batch_size=budget.limits.batch_rows):
                    budget.check()
                    check_decoded(batch, part, rules["tables"][table])
                files.append(p)
            scan.add(name, files)
            rule = rules["tables"][table]
            scan.keys(name, rule["business_key"], rule["version_key"])
            scan.row_rules(name, ss[table], rule)
            scan.lineage(name, [tuple(x) for x in plan["lineage"][table]])
            summaries[table] = semantic_scan(files, ss[table], check=budget.check)
        scan.add("companies", [local(plan["companies"])])
        financial_references(scan)
        actual_concepts = [
            r
            for item in plan["layout"]["dwd_financial_concept"]
            for r in pq.ParquetFile(local(item["path"])).read().to_pylist()
        ]
        if actual_concepts != concepts:
            raise BoundedError("FINANCIAL_TMP_OLD_OR_NEW_CONCEPT_ATOM_CHANGED")
        scan.add("old_reports", [local(plan["native_reports"])])
        scan.add("incoming_reports", [local(n) for n in plan["inputs"]["dwd_financial_report"]])
        if report_revision_views(scan) != plan["report_proof"]:
            raise BoundedError("FINANCIAL_TMP_REPORT_RECONSTRUCTION_CHANGED")
        scan.add("incoming_facts", [local(n) for n in plan["inputs"]["dwd_financial_fact"]])
        for table, actual, expected in [
            ("dwd_financial_report", "reports", "integrated_reports"),
            ("dwd_financial_fact", "facts", "incoming_facts"),
        ]:
            signature = []
            for view in (actual, expected):
                accumulator = _ColumnSummary(ss[table])
                for batch in native_batches(
                    scan, view, ss[table], rules["tables"][table]["version_key"]
                ):
                    accumulator.add(batch)
                signature.append(accumulator.finish())
            if signature[0] != signature[1]:
                raise BoundedError("FINANCIAL_TMP_ORIGINAL_TYPED_ATOM_CHANGED:" + table)
    return dict(
        identity=plan["identity"],
        summaries=summaries,
        complete_Base=False,
        all_local_financial_output_keys_times_references="PASS",
        original_report_fact_columns="PASS",
        old_concept_atoms="PASS",
        archived_roots_dereferenced=False,
        independent_whole_prior_fact_scan=False,
        consumer_admission="NOT_GRANTED",
    )
