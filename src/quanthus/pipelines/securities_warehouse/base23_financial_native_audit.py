"""Offline bounded replay of quarterly rows and complete abstract accounting."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_financial import TABLES
from .base22_financial_audit import FinancialReplaySink
from .base22_sources import _tree
from .base23_financial_native import (
    CONTRACT_ID,
    QUALIFICATION,
    abstract_disposition,
    latest_quarterly_captures,
    quarterly_observation,
)
from .financial_reporting import (
    FinancialReportingSource,
    financial_company_resolver_from_listed_company_rows,
)


def audit_financial_native_scope(root: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    closure = body.get("input_closure", {})
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("input_digest") != digest(closure)
        or closure.get("purpose") != CONTRACT_ID or closure.get("qualification") != QUALIFICATION
        or body.get("projection_module_sha256")
        != file_sha(Path(__file__).with_name("base23_financial_native.py"))
        or body.get("READY") is not False or body.get("complete_Base23") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("outputs", {})) != set(TABLES)
        or closure.get("same_provider_revision_rule")
        != "LATEST_REAL_CAPTURE_COMPLETE_QUARTERLY_ROW"
    ):
        raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_SCOPE_UNQUALIFIED")
    if _tree(root) != {"SCOPE.json", *body["physical_members"]}:
        raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_PHYSICAL_SET_CHANGED")
    payload = {i["path"].removeprefix("payload/") for t in TABLES for i in body["outputs"][t]}
    if (
        _tree(root / "payload") != payload
        or any(i["path"] != f"payload/{t}/{i['partition']}/part-{i['number']:06d}.parquet"
               for t in TABLES for i in body["outputs"][t])
    ):
        raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_PHYSICAL_PARTITION_SET_CHANGED")
    for rel, item in body["physical_members"].items():
        path = _physical(root, rel, independent=True)
        if path.stat().st_size != item["size_bytes"] or file_sha(path) != item["sha256"]:
            raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_PHYSICAL_HASH_CHANGED")
        budget.check()
    c, model, ext = load_contract(root / "contract")
    ss = schemas(model, ext)
    history_path = _physical(root, body["history_materialization"], independent=True)
    if file_sha(history_path) != closure["history_materialization_sha256"]:
        raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_HISTORY_IDENTITY_CHANGED")
    history = json.loads(history_path.read_bytes())
    parents = {}
    for table, item in body["parents"].items():
        path = _physical(root, item["path"], independent=True)
        if (
            not any(f["path"] == item["archive_path"] and f["sha256"] == file_sha(path)
                    for f in history["layout"][table]["files"])
            or pq.ParquetFile(path).metadata.num_rows > 32768
        ):
            raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_IDENTITY_PARENT_CHANGED")
        parents[table] = pq.ParquetFile(path).read().to_pylist()
    if set(parents) != {"dwd_security", "dwd_security_identifier", "dwd_listed_company"}:
        raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_EXACT_PARENT_SET_CHANGED")
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"],
    )
    quarterly_inputs, abstracts = [], []
    reasons: Counter[str] = Counter()
    for source, item in zip(closure["sources"], body["sources"], strict=True):
        release_path = _physical(root, item["manifest"], independent=True)
        release = json.loads(release_path.read_bytes())
        raw_path = _physical(root, item["raw"], independent=True)
        if (
            file_sha(release_path) != source["manifest_sha256"]
            or _physical(root, item["READY"], independent=True).read_text().strip()
            != source["manifest_sha256"] or release.get("status") != "ready"
            or release["release_id"] != source["release"]
            or file_sha(raw_path) != source["sha256"]
            or pq.ParquetFile(raw_path).metadata.num_rows != source["rows"]
            or not any(a["name"] == source["family"] and a["sha256"] == source["sha256"]
                       for a in release["artifacts"])
        ):
            raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_ORIGINAL_SOURCE_CHANGED")
        ordinal = 0
        for batch in pq.ParquetFile(raw_path).iter_batches(batch_size=1024):
            for raw in batch.to_pylist():
                if source["family"] == "financial_quarterly":
                    quarterly_inputs.append((raw, source, ordinal))
                elif source["family"] == "financial_disclosures_delta":
                    proof = abstract_disposition(raw, observed_at=body["observed_at"])
                    proof.update(member_sha256=source["sha256"], ordinal=ordinal,
                                 release=source["release"])
                    abstracts.append(proof)
                    reasons.update(proof["reasons"])
                else:
                    raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_UNKNOWN_NONEMPTY_DOMAIN")
                ordinal += 1
            budget.check()
        if ordinal != source["rows"]:
            raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_COMPLETE_SOURCE_CENSUS_CHANGED")
    selected = latest_quarterly_captures(quarterly_inputs)
    if (
        digest([item for _, item in selected]) != body["latest_capture_selection_sha256"]
        or digest(abstracts) != body["abstract_dispositions_sha256"]
    ):
        raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_SELECTION_OR_DISPOSITION_CHANGED")
    proofs, seen = [], set()

    def compare(table: str, part: str, number: int, expected: list[dict[str, Any]]) -> None:
        matches = [i for i in body["outputs"][table]
                   if i["partition"] == part and i["number"] == number]
        if len(matches) != 1:
            raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_EXACT_OUTPUT_PARTITION_CHANGED")
        path = _physical(root, matches[0]["path"], independent=True)
        if pq.ParquetFile(path).read().to_pylist() != expected:
            raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_EXACT_TYPED_ATOM_CHANGED")
        seen.add((table, part, number))

    sink = FinancialReplaySink(c["tables"], compare, flush_rows=16384)
    for raw, item in selected:
        source = FinancialReportingSource(
            "weekly_full_financial_quarterly", "quarterly_projection", item["member_sha256"],
            Path("/archival-source-never-opened"), "artifacts/financial_quarterly.parquet",
            item["member_sha256"], item["release"], "parent",
        )
        rows, proof = quarterly_observation(raw, source=source, resolver=resolver,
                                             observed_at=body["observed_at"])
        sink.add(rows)
        proofs.append(proof)
        budget.check()
    sink.finish()
    if (
        seen != {(t, i["partition"], i["number"]) for t in TABLES for i in body["outputs"][t]}
        or digest(proofs) != body["projection_proof_sha256"]
    ):
        raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_OUTPUT_OR_PROOF_CENSUS_CHANGED")
    with GlobalScan(budget) as scan:
        for table in TABLES:
            paths = []
            for item in body["outputs"][table]:
                path = _physical(root, item["path"], independent=True)
                if semantic_scan([path], ss[table], check=budget.check) != item["semantic_scan"]:
                    raise BoundedError("SEPTEMBER_NATIVE_FINANCIAL_RECOMPUTED_SUMMARY_CHANGED")
                paths.append(path)
            scan.add(table, paths)
            scan.keys(table, c["tables"][table]["business_key"], c["tables"][table]["version_key"])
        scan.add("dwd_listed_company", [root / body["parents"]["dwd_listed_company"]["path"]])
        tables = {*TABLES, "dwd_listed_company"}
        scan.references({"tables": [t for t in model["tables"] if t["name"] in tables]},
                        {"tables": {t: c["tables"][t] for t in tables}})
    result = {"status": "SEPTEMBER_QUARTERLY_LOCAL_REPLAY_PASS_ABSTRACTS_UNIT_UNQUALIFIED",
              "scope_identity": body["identity"], "selected_latest_rows": len(selected),
              "rows": dict(sink.counts), "abstract_captures_accounted": len(abstracts),
              "abstract_hold_reasons": dict(reasons), "native_financial_union": "NOT_RUN",
              "two_whole_cold_runs": "FINAL_IMAGE_NOT_RUN", "READY": False}
    result["identity"] = digest(result)
    return result
