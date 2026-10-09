"""Image-local September industry replay; final whole-image acceptance is separate."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_industry_snapshot_union import TABLES
from .base22_sources import _tree
from .base23_industry import CONTRACT_ID, QUALIFICATION, TABLE, snapshot_observation
from .financial_reporting import financial_company_resolver_from_listed_company_rows


def audit_industry_scope(root: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    closure = body.get("input_closure", {})
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("input_digest") != digest(closure)
        or closure.get("purpose") != CONTRACT_ID
        or closure.get("qualification") != QUALIFICATION
        or body.get("projection_module_sha256")
        != file_sha(Path(__file__).with_name("base23_industry.py"))
        or body.get("READY") is not False or body.get("complete_Base23") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("outputs", {})) != TABLES
    ):
        raise BoundedError("SEPTEMBER_INDUSTRY_SCOPE_INVALID")
    if _tree(root) != {"SCOPE.json", *body["physical_members"]}:
        raise BoundedError("SEPTEMBER_INDUSTRY_EXACT_PHYSICAL_FILES_CHANGED")
    for rel, item in body["physical_members"].items():
        p = _physical(root, rel, independent=True)
        if p.stat().st_size != item["size_bytes"] or file_sha(p) != item["sha256"]:
            raise BoundedError("SEPTEMBER_INDUSTRY_PHYSICAL_HASH_CHANGED")
        budget.check()
    c, model, ext = load_contract(root / "contract")
    ss = schemas(model, ext)

    def read(rel: str, bound: int) -> list[dict[str, Any]]:
        p = _physical(root, rel, independent=True)
        pf = pq.ParquetFile(p)
        if pf.metadata.num_rows > bound:
            raise BoundedError("SEPTEMBER_INDUSTRY_REGISTERED_ROW_BOUND_CHANGED")
        budget.check()
        return pf.read().to_pylist()  # type: ignore[no-any-return]

    history_path = _physical(root, body["history_materialization"], independent=True)
    if file_sha(history_path) != closure["history_materialization_sha256"]:
        raise BoundedError("SEPTEMBER_INDUSTRY_HISTORY_IDENTITY_CHANGED")
    history = json.loads(history_path.read_bytes())
    parents = {}
    for table, item in body["parents"].items():
        original: dict[str, Any] = next((f for f in history["layout"][table]["files"]
                                        if f["path"] == item["archive_path"]), {})
        p = _physical(root, item["path"], independent=True)
        if original.get("sha256") != file_sha(p):
            raise BoundedError("SEPTEMBER_INDUSTRY_NATIVE_PARENT_CHANGED")
        parents[table] = read(item["path"], 32768)
    if set(parents) != TABLES - {TABLE}:
        raise BoundedError("SEPTEMBER_INDUSTRY_EXACT_PARENT_SET_CHANGED")
    original_member = _physical(root, body["original_membership"], independent=True)
    if not any(f["sha256"] == file_sha(original_member)
               for f in history["layout"][TABLE]["files"]):
        raise BoundedError("SEPTEMBER_INDUSTRY_NATIVE_MEMBERSHIP_CHANGED")
    expected = read(body["original_membership"], 32768)
    native_count = len(expected)
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"]
    )
    nodes = {r["node_code"]: r for r in parents["dwd_industry_node"]}
    if len(nodes) != len(parents["dwd_industry_node"]):
        raise BoundedError("SEPTEMBER_INDUSTRY_NODE_IDENTITY_AMBIGUOUS")
    proofs, held = [], []
    if len(body["sources"]) != len(closure["sources"]) or not body["sources"]:
        raise BoundedError("SEPTEMBER_INDUSTRY_EXACT_SOURCE_SET_CHANGED")
    for selected, item in zip(closure["sources"], body["sources"], strict=True):
        release_path = _physical(root, item["manifest"], independent=True)
        release = json.loads(release_path.read_bytes())
        if (
            selected["manifest_sha256"] != file_sha(release_path)
            or _physical(root, item["READY"], independent=True).read_text().strip()
            != file_sha(release_path) or release.get("status") != "ready"
            or release["market_date"] != selected["market_date"]
            or release["release_id"] != selected["release"]
        ):
            raise BoundedError("SEPTEMBER_INDUSTRY_ORIGINAL_RELEASE_CHANGED")
        raw = read(item["raw"], 8192)
        artifact = [a for a in release["artifacts"] if a["name"] == "industry_membership"]
        if (
            len(artifact) != 1 or len(raw) != selected["rows"]
            or artifact[0]["sha256"] != selected["sha256"]
            or file_sha(root / item["raw"]) != selected["sha256"]
            or len({r["stock_code"] for r in raw}) != len(raw)
        ):
            raise BoundedError("SEPTEMBER_INDUSTRY_RAW_OR_KEY_CENSUS_CHANGED")
        day = date.fromisoformat(selected["market_date"])
        for ordinal, r in enumerate(raw):
            parent, _ = resolver.resolve_for_report(r["stock_code"], period_end=day)
            try:
                row, proof = snapshot_observation(
                    r, day=day, parent=parent, node=nodes.get(r["industry_code"], {}),
                    observed_at=body["observed_at"], source_sha256=selected["sha256"],
                    revision=selected["release"],
                )
                expected.append(row)
                proofs.append(proof)
            except BoundedError as exc:
                held.append({"source_sha256": selected["sha256"], "ordinal": ordinal,
                             "raw_row_sha256": digest(r), "reason": str(exc)})
    expected.sort(key=digest)
    summaries = {}
    for table, item in body["outputs"].items():
        if table != TABLE and item["path"] != body["parents"][table]["path"]:
            raise BoundedError("SEPTEMBER_INDUSTRY_NATIVE_OUTPUT_PARENT_CHANGED")
        p = _physical(root, item["path"], independent=True)
        summaries[table] = semantic_scan([p], ss[table], check=budget.check)
        if summaries[table] != item["semantic_scan"]:
            raise BoundedError("SEPTEMBER_INDUSTRY_TYPED_SUMMARY_CHANGED")
    if (
        read(body["outputs"][TABLE]["path"], 65536) != expected
        or digest(proofs) != body["projection_proof_sha256"]
        or held != body["held"] or native_count != body["native_membership_rows"]
    ):
        raise BoundedError("SEPTEMBER_INDUSTRY_NATIVE_UNION_REPLAY_CHANGED")
    with GlobalScan(budget) as scan:
        for table, item in body["outputs"].items():
            scan.add(table, [root / item["path"]])
            scan.keys(table, c["tables"][table]["business_key"], c["tables"][table]["version_key"])
        scan.references({"tables": [t for t in model["tables"] if t["name"] in TABLES]},
                        {"tables": {t: c["tables"][t] for t in TABLES}})
    result = {"status": "RETAINED_SEPTEMBER_INDUSTRY_LOCAL_REPLAY_PASS_NOT_COMPLETE_BASE23",
              "scope_identity": body["identity"], "native_rows_unchanged": native_count,
              "new_dated_snapshot_rows": len(expected) - native_count,
              "held_snapshot_rows": len(held), "two_whole_cold_runs": "FINAL_IMAGE_NOT_RUN",
              "historical_PIT_qualified": False, "READY": False}
    result["identity"] = digest(result)
    return result
