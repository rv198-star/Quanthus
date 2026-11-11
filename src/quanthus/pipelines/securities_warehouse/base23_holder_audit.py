"""Exact local replay of September retained holder-count observations."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_sources import _tree
from .base23_holder import (
    CONTRACT_ID,
    QUALIFICATION,
    TABLE,
    holder_observation,
    holder_schema,
    latest_holder_captures,
)
from .financial_reporting import financial_company_resolver_from_listed_company_rows


def audit_holder_scope(root: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    closure = body.get("input_closure", {})
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("input_digest") != digest(closure)
        or closure.get("purpose") != CONTRACT_ID or closure.get("qualification") != QUALIFICATION
        or body.get("projection_module_sha256")
        != file_sha(Path(__file__).with_name("base23_holder.py"))
        or body.get("READY") is not False or body.get("complete_Base23") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("outputs", {})) != {TABLE}
        or closure.get("same_provider_revision_rule") != "LATEST_REAL_CAPTURE_COMPLETE_HOLDER_ROW"
    ):
        raise BoundedError("SEPTEMBER_HOLDER_SCOPE_UNQUALIFIED")
    if _tree(root) != {"SCOPE.json", *body["physical_members"]}:
        raise BoundedError("SEPTEMBER_HOLDER_PHYSICAL_SET_CHANGED")
    wanted = {item["path"].removeprefix("payload/") for item in body["outputs"][TABLE]}
    if (
        _tree(root / "payload") != wanted
        or any(item["path"] != f"payload/{TABLE}/{item['partition']}/part-000000.parquet"
               for item in body["outputs"][TABLE])
    ):
        raise BoundedError("SEPTEMBER_HOLDER_PHYSICAL_PARTITION_SET_CHANGED")
    for relative, item in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if path.stat().st_size != item["size_bytes"] or file_sha(path) != item["sha256"]:
            raise BoundedError("SEPTEMBER_HOLDER_PHYSICAL_HASH_CHANGED")
        budget.check()
    contract, model, _ = load_contract(root / "contract")
    history_path = _physical(root, body["history_materialization"], independent=True)
    if file_sha(history_path) != closure["history_materialization_sha256"]:
        raise BoundedError("SEPTEMBER_HOLDER_NATIVE_HISTORY_IDENTITY_CHANGED")
    history = json.loads(history_path.read_bytes())
    parents = {}
    for table, item in body["parents"].items():
        path = _physical(root, item["path"], independent=True)
        if (
            not any(f["path"] == item["archive_path"] and f["sha256"] == file_sha(path)
                    for f in history["layout"][table]["files"])
            or pq.ParquetFile(path).metadata.num_rows > 32768
        ):
            raise BoundedError("SEPTEMBER_HOLDER_NATIVE_IDENTITY_PARENT_CHANGED")
        parents[table] = pq.ParquetFile(path).read().to_pylist()
    if set(parents) != {"dwd_security", "dwd_security_identifier", "dwd_listed_company"}:
        raise BoundedError("SEPTEMBER_HOLDER_EXACT_PARENT_SET_CHANGED")
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"],
    )
    observations = []
    for source, item in zip(closure["sources"], body["sources"], strict=True):
        manifest_path = _physical(root, item["manifest"], independent=True)
        manifest = json.loads(manifest_path.read_bytes())
        raw_path = _physical(root, item["raw"], independent=True)
        if (
            file_sha(manifest_path) != source["manifest_sha256"]
            or _physical(root, item["READY"], independent=True).read_text().strip()
            != source["manifest_sha256"] or manifest.get("status") != "ready"
            or manifest["release_id"] != source["release"]
            or file_sha(raw_path) != source["sha256"]
            or pq.ParquetFile(raw_path).metadata.num_rows != source["rows"]
            or not any(a["name"] == "shareholder_count" and a["sha256"] == source["sha256"]
                       and a["relative_path"] == source["path"]
                       for a in manifest["artifacts"])
        ):
            raise BoundedError("SEPTEMBER_HOLDER_ORIGINAL_SOURCE_CHANGED")
        ordinal = 0
        for batch in pq.ParquetFile(raw_path).iter_batches(batch_size=1024):
            for raw in batch.to_pylist():
                observations.append((raw, source, ordinal))
                ordinal += 1
            budget.check()
        if ordinal != source["rows"]:
            raise BoundedError("SEPTEMBER_HOLDER_COMPLETE_CAPTURE_CENSUS_CHANGED")
    selected = latest_holder_captures(observations)
    if digest([item for _, item in selected]) != body["latest_capture_selection_sha256"]:
        raise BoundedError("SEPTEMBER_HOLDER_LATEST_SELECTION_CHANGED")
    expected: dict[str, list[dict[str, Any]]] = defaultdict(list)
    proofs = []
    for raw, item in selected:
        rows, proof = holder_observation(
            raw, source_sha256=item["member_sha256"], revision=item["release"],
            resolver=resolver, observed_at=body["observed_at"],
        )
        for row in rows[TABLE]:
            expected[partition(row, contract["tables"][TABLE])].append(row)
        proofs.append(proof)
    if (
        set(expected) != {item["partition"] for item in body["outputs"][TABLE]}
        or len(expected) != len(body["outputs"][TABLE])
        or digest(proofs) != body["projection_proof_sha256"]
    ):
        raise BoundedError("SEPTEMBER_HOLDER_OUTPUT_OR_PROOF_CENSUS_CHANGED")
    schema = holder_schema()
    paths = []
    for item in body["outputs"][TABLE]:
        path = _physical(root, item["path"], independent=True)
        expected_rows = sorted(expected[item["partition"]], key=digest)
        if (
            pq.ParquetFile(path).read().to_pylist() != expected_rows
            or semantic_scan([path], schema, check=budget.check) != item["semantic_scan"]
        ):
            raise BoundedError("SEPTEMBER_HOLDER_TYPED_ATOM_OR_RECOMPUTED_SUMMARY_CHANGED")
        paths.append(path)
    with GlobalScan(budget) as scan:
        scan.add(TABLE, paths)
        rule = contract["tables"][TABLE]
        scan.keys(TABLE, rule["business_key"], rule["version_key"])
        for table in ("dwd_security", "dwd_listed_company"):
            scan.add(table, [root / body["parents"][table]["path"]])
        tables = {TABLE, "dwd_security", "dwd_listed_company"}
        scan.references({"tables": [t for t in model["tables"] if t["name"] in tables]},
                        {"tables": {t: contract["tables"][t] for t in tables}})
    result = {"status": "SEPTEMBER_HOLDER_LOCAL_EXACT_REPLAY_PASS_NOT_NATIVE_UNION",
              "scope_identity": body["identity"], "latest_selected_rows": len(selected),
              "typed_rows": sum(map(len, expected.values())), "source_captures": len(observations),
              "held_latest_rows": sum(p["held_reason"] is not None for p in proofs),
              "native_holder_union": "NOT_RUN", "two_whole_cold_runs": "FINAL_IMAGE_NOT_RUN",
              "READY": False}
    result["identity"] = digest(result)
    return result
