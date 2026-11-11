"""Bounded replay of qualified September statement changes, entirely local."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_financial import TABLES, _clock
from .base22_financial_audit import FinancialReplaySink
from .base22_sources import _tree
from .base23_financial import (
    CONTRACT_ID,
    QUALIFICATION,
    select_latest_changes,
    statement_observation,
)
from .financial_reporting import (
    FinancialStatementSource,
    financial_company_resolver_from_listed_company_rows,
)


def audit_financial_scope(root: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    closure = body.get("input_closure", {})
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("input_digest") != digest(closure)
        or closure.get("purpose") != CONTRACT_ID or closure.get("qualification") != QUALIFICATION
        or body.get("projection_module_sha256")
        != file_sha(Path(__file__).with_name("base23_financial.py"))
        or body.get("READY") is not False or body.get("complete_Base23") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("outputs", {})) != set(TABLES)
        or closure.get("same_provider_revision_rule") != "LATEST_REAL_CAPTURE_COMPLETE_ENVELOPE"
    ):
        raise BoundedError("SEPTEMBER_STATEMENT_SCOPE_UNQUALIFIED")
    if _tree(root) != {"SCOPE.json", *body["physical_members"]}:
        raise BoundedError("SEPTEMBER_STATEMENT_PHYSICAL_FILE_SET_CHANGED")
    expected_payload = {i["path"].removeprefix("payload/")
                        for t in TABLES for i in body["outputs"][t]}
    if (
        _tree(root / "payload") != expected_payload
        or any(i["path"] != f"payload/{t}/{i['partition']}/part-{i['number']:06d}.parquet"
               for t in TABLES for i in body["outputs"][t])
    ):
        raise BoundedError("SEPTEMBER_STATEMENT_PHYSICAL_TABLE_PARTITION_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if p.stat().st_size != item["size_bytes"] or file_sha(p) != item["sha256"]:
            raise BoundedError("SEPTEMBER_STATEMENT_PHYSICAL_HASH_CHANGED")
        budget.check()
    c, model, ext = load_contract(root / "contract")
    ss = schemas(model, ext)
    parent_rows = {}
    history_path = _physical(root, body["history_materialization"], independent=True)
    if file_sha(history_path) != closure["history_materialization_sha256"]:
        raise BoundedError("SEPTEMBER_STATEMENT_HISTORY_IDENTITY_CHANGED")
    history = json.loads(history_path.read_bytes())
    for table, item in body["parents"].items():
        p = _physical(root, item["path"], independent=True)
        if (
            not any(f["path"] == item["archive_path"] and f["sha256"] == file_sha(p)
                    for f in history["layout"][table]["files"])
            or pq.ParquetFile(p).metadata.num_rows > 32768
        ):
            raise BoundedError("SEPTEMBER_STATEMENT_NATIVE_PARENT_CHANGED")
        parent_rows[table] = pq.ParquetFile(p).read().to_pylist()
    if set(parent_rows) != {"dwd_security", "dwd_security_identifier", "dwd_listed_company"}:
        raise BoundedError("SEPTEMBER_STATEMENT_EXACT_PARENT_SET_CHANGED")
    resolver = financial_company_resolver_from_listed_company_rows(
        parent_rows["dwd_security"], parent_rows["dwd_security_identifier"]
    )
    selected = closure["selected_envelopes"]
    change_file = _physical(root, body["change_selection"], independent=True)
    if file_sha(change_file) != closure["change_selection_sha256"]:
        raise BoundedError("SEPTEMBER_STATEMENT_ORIGINAL_CHANGE_SELECTION_CHANGED")
    changes = [json.loads(line) for line in change_file.read_text().splitlines()]
    if select_latest_changes(changes, closure["sources"]) != selected:
        raise BoundedError("SEPTEMBER_STATEMENT_LATEST_REAL_CAPTURE_SELECTION_CHANGED")
    if len(selected) > 16384 or len({tuple(i["identity_key"]) for i in selected}) != len(selected):
        raise BoundedError("SEPTEMBER_STATEMENT_UNIQUE_SELECTED_WRAPPERS_REQUIRED")
    actual = {}
    for s, item in zip(closure["sources"], body["sources"], strict=True):
        release_path = _physical(root, item["manifest"], independent=True)
        release = json.loads(release_path.read_bytes())
        raw_path = _physical(root, item["raw"], independent=True)
        if (
            file_sha(release_path) != s["manifest_sha256"]
            or _physical(root, item["READY"], independent=True).read_text().strip()
            != s["manifest_sha256"] or release.get("status") != "ready"
            or release["release_id"] != s["release"]
            or file_sha(raw_path) != s["sha256"]
            or pq.ParquetFile(raw_path).metadata.num_rows != s["rows"]
            or not any(a["name"] == "financial_statements" and a["sha256"] == s["sha256"]
                       for a in release["artifacts"])
        ):
            raise BoundedError("SEPTEMBER_STATEMENT_ORIGINAL_RAW_SOURCE_CHANGED")
        needed = {i["ordinal"] for i in selected if i["member_sha256"] == s["sha256"]}
        ordinal = 0
        for batch in pq.ParquetFile(raw_path).iter_batches(batch_size=2):
            for raw in batch.to_pylist():
                if ordinal in needed:
                    actual[s["sha256"], ordinal] = raw
                ordinal += 1
            budget.check()
    proofs, output_seen = [], set()

    def compare(table: str, part: str, number: int, expected: list[dict[str, Any]]) -> None:
        key = table, part, number
        items = [i for i in body["outputs"][table] if (table, i["partition"], i["number"]) == key]
        if len(items) != 1:
            raise BoundedError("SEPTEMBER_STATEMENT_EXACT_OUTPUT_PARTITION_CHANGED")
        item = items[0]
        p = _physical(root, item["path"], independent=True)
        if pq.ParquetFile(p).read().to_pylist() != expected:
            raise BoundedError("SEPTEMBER_STATEMENT_EXACT_RAW_TO_TYPED_CHANGED")
        output_seen.add(key)

    sink = FinancialReplaySink(c["tables"], compare, flush_rows=16384)
    for i in selected:
        raw = actual.get((i["member_sha256"], i["ordinal"]))
        if raw is None or _clock(raw["acquired_at"]) != _clock(i["original_raw_acquired_at"]):
            raise BoundedError("SEPTEMBER_STATEMENT_SELECTED_CAPTURE_CHANGED")
        source = FinancialStatementSource(
            "September-statements", Path("/archival-source-never-opened"),
            "artifacts/financial_statements.parquet", i["member_sha256"],
            i["release"], "parent",
        )
        rows, proof = statement_observation(raw, source=source, resolver=resolver,
                                            observed_at=body["observed_at"])
        if (
            proof["complete_envelope_fingerprint"] != i["complete_envelope_fingerprint"]
            or tuple(proof["original_identity_key"]) != tuple(i["identity_key"])
        ):
            raise BoundedError("SEPTEMBER_STATEMENT_SELECTED_BODY_CHANGED")
        sink.add(rows)
        proofs.append(proof)
    sink.finish()
    all_items = {(t, i["partition"], i["number"])
                 for t in TABLES for i in body["outputs"][t]}
    if output_seen != all_items or digest(proofs) != body["projection_proof_sha256"]:
        raise BoundedError("SEPTEMBER_STATEMENT_OUTPUT_OR_PROOF_CENSUS_CHANGED")
    with GlobalScan(budget) as scan:
        for table in TABLES:
            paths = []
            for item in body["outputs"][table]:
                p = _physical(root, item["path"], independent=True)
                if semantic_scan([p], ss[table], check=budget.check) != item["semantic_scan"]:
                    raise BoundedError("SEPTEMBER_STATEMENT_RECOMPUTED_SUMMARY_CHANGED")
                paths.append(p)
            scan.add(table, paths)
            scan.keys(table, c["tables"][table]["business_key"], c["tables"][table]["version_key"])
        scan.add("dwd_listed_company", [root / body["parents"]["dwd_listed_company"]["path"]])
        scope_tables = {*TABLES, "dwd_listed_company"}
        scan.references({"tables": [t for t in model["tables"] if t["name"] in scope_tables]},
                        {"tables": {t: c["tables"][t] for t in scope_tables}})
    result = {"status": "SEPTEMBER_CHANGED_STATEMENT_LOCAL_REPLAY_PASS_NOT_NATIVE_UNION",
              "scope_identity": body["identity"], "selected_wrappers": len(selected),
              "rows": dict(sink.counts), "quarterly_and_daily_abstract_closure": "NOT_RUN",
              "two_whole_cold_runs": "FINAL_IMAGE_NOT_RUN", "READY": False}
    result["identity"] = digest(result)
    return result
