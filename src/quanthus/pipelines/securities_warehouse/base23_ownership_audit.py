"""Local literal replay of changed ownership scopes, never old-root fallback."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_financial import _clock
from .base22_holder_component_union import check_component_global_keys
from .base22_holder_components_v2 import load_component_contract
from .base22_sources import _tree
from .base22_weekly_literal_audit import OwnershipReplaySink
from .base23_ownership import CONTRACT_ID, HOLDING, QUALIFICATION, TABLES, ownership_observation
from .base23_ownership_reconfirmation import literal_identity
from .financial_reporting import financial_company_resolver_from_listed_company_rows


def changed_binding_census(comparison: dict[str, Any], proofs: Path) -> set[tuple[str, int]]:
    """Recompute latest capture selection from all frozen incoming hash bindings."""
    if file_sha(proofs) != comparison["complete_incoming_proof"]["sha256"]:
        raise BoundedError("SEPTEMBER_OWNERSHIP_COMPLETE_BINDINGS_CHANGED")
    latest: dict[tuple[str, ...], dict[str, Any]] = {}
    seen = set()
    counts: Counter[str] = Counter()
    with proofs.open() as stream:
        for n, line in enumerate(stream, 1):
            if n > 32768:
                raise BoundedError("SEPTEMBER_OWNERSHIP_CAPTURE_CENSUS_BOUND")
            item = json.loads(line)
            location = item["member_sha256"], item["ordinal"]
            if location in seen or type(item["prior_literal_scope_match"]) is not bool:
                raise BoundedError("SEPTEMBER_OWNERSHIP_DUPLICATE_CAPTURE_OR_UNKNOWN_DISPOSITION")
            seen.add(location)
            key = tuple(item["identity_key"])
            counts["BASELINE_IDENTICAL_LITERAL_SCOPE" if item["prior_literal_scope_match"]
                   else "CHANGED_LITERAL_SCOPE"] += 1
            if key in latest:
                old = latest[key]
                clock, previous = (_clock(i["actual_acquisition_instant"]) for i in (item, old))
                if clock == previous and item["literal_scope_fingerprint"] != old[
                    "literal_scope_fingerprint"
                ]:
                    raise BoundedError("SEPTEMBER_OWNERSHIP_EQUAL_CAPTURE_LITERAL_CONFLICT")
                if clock < previous or (clock == previous and digest(item) >= digest(old)):
                    continue
            latest[key] = item
    changes = {(i["member_sha256"], i["ordinal"]) for i in latest.values()
               if not i["prior_literal_scope_match"]}
    if (
        counts != comparison["incoming_complete_capture_counts"]
        or len(latest) != comparison["latest_identity_keys"]
        or len(changes) != comparison["latest_changed_scopes"]
    ):
        raise BoundedError("SEPTEMBER_OWNERSHIP_COMPLETE_SELECTION_CHANGED")
    return changes


def audit_ownership_scope(root: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    body = json.loads(_physical(root, "SCOPE.json", independent=True).read_bytes())
    closure = body.get("input_closure", {})
    if (
        body.get("contract_id") != CONTRACT_ID
        or body.get("identity") != digest({k: v for k, v in body.items() if k != "identity"})
        or body.get("input_digest") != digest(closure)
        or closure.get("purpose") != CONTRACT_ID or closure.get("qualification") != QUALIFICATION
        or body.get("projection_module_sha256")
        != file_sha(Path(__file__).with_name("base23_ownership.py"))
        or body.get("READY") is not False or body.get("complete_Base23") is not False
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("outputs", {})) != set(TABLES)
    ):
        raise BoundedError("SEPTEMBER_OWNERSHIP_SCOPE_UNQUALIFIED")
    if _tree(root) != {"SCOPE.json", *body["physical_members"]}:
        raise BoundedError("SEPTEMBER_OWNERSHIP_PHYSICAL_FILE_SET_CHANGED")
    wanted = {i["path"].removeprefix("payload/") for t in TABLES for i in body["outputs"][t]}
    if (_tree(root / "payload") != wanted or any(
        i["path"] != f"payload/{t}/{i['partition']}/part-{i['number']:06d}.parquet"
        for t in TABLES for i in body["outputs"][t]
    )):
        raise BoundedError("SEPTEMBER_OWNERSHIP_PHYSICAL_TABLE_PARTITION_SET_CHANGED")
    for relative, item in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if path.stat().st_size != item["size_bytes"] or file_sha(path) != item["sha256"]:
            raise BoundedError("SEPTEMBER_OWNERSHIP_PHYSICAL_HASH_CHANGED")
        budget.check()
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    grain = load_component_contract(root / "contract")
    hpath = _physical(root, body["history_materialization"], independent=True)
    if file_sha(hpath) != closure["history_materialization_sha256"]:
        raise BoundedError("SEPTEMBER_OWNERSHIP_NATIVE_HISTORY_CHANGED")
    history = json.loads(hpath.read_bytes())
    parents = {}
    for table, item in body["parents"].items():
        p = _physical(root, item["path"], independent=True)
        if (not any(f["path"] == item["archive_path"] and f["sha256"] == file_sha(p)
                    for f in history["layout"][table]["files"])
                or pq.ParquetFile(p).metadata.num_rows > 32768):
            raise BoundedError("SEPTEMBER_OWNERSHIP_NATIVE_IDENTITY_PARENT_CHANGED")
        parents[table] = pq.ParquetFile(p).read().to_pylist()
    if set(parents) != {"dwd_security", "dwd_security_identifier", "dwd_listed_company"}:
        raise BoundedError("SEPTEMBER_OWNERSHIP_EXACT_PARENT_SET_CHANGED")
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"],
    )
    cp = _physical(root, body["comparison_result"], independent=True)
    if file_sha(cp) != closure["comparison_result_sha256"]:
        raise BoundedError("SEPTEMBER_OWNERSHIP_LITERAL_COMPARISON_CHANGED")
    comparison = json.loads(cp.read_bytes())
    if comparison["identity"] != digest({k: v for k, v in comparison.items() if k != "identity"}):
        raise BoundedError("SEPTEMBER_OWNERSHIP_COMPARISON_IDENTITY_CHANGED")
    selected = changed_binding_census(comparison, root / body["complete_bindings"])
    proofs, checked, bindings = [], set(), set()

    def compare(table: str, part: str, number: int, expected: list[dict[str, Any]]) -> None:
        items = [i for i in body["outputs"][table]
                 if i["partition"] == part and i["number"] == number]
        if len(items) != 1:
            raise BoundedError("SEPTEMBER_OWNERSHIP_EXACT_OUTPUT_PARTITION_CHANGED")
        p = _physical(root, items[0]["path"], independent=True)
        if (pq.ParquetFile(p).read().to_pylist() != expected
                or semantic_scan([p], ss[table], check=budget.check) != items[0]["semantic_scan"]):
            raise BoundedError("SEPTEMBER_OWNERSHIP_EXACT_TYPED_ATOM_OR_SUMMARY_CHANGED")
        checked.add((table, part, number))

    sink = OwnershipReplaySink(contract["tables"], compare)
    if len(body["sources"]) != len(comparison["changed_literal_sources"]):
        raise BoundedError("SEPTEMBER_OWNERSHIP_EXACT_LITERAL_SOURCE_SET_CHANGED")
    for source, item in zip(comparison["changed_literal_sources"], body["sources"], strict=True):
        path = _physical(root, item["raw"], independent=True)
        if (file_sha(path) != source["sha256"]
                or pq.ParquetFile(path).metadata.num_rows != source["rows"]):
            raise BoundedError("SEPTEMBER_OWNERSHIP_COMPLETE_DERIVED_RAW_CHANGED")
        if len(source["bindings"]) != source["rows"]:
            raise BoundedError("SEPTEMBER_OWNERSHIP_DERIVED_ROW_BINDING_CENSUS_CHANGED")
        ordinal = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=64):
            for raw in batch.to_pylist():
                binding = source["bindings"][ordinal]
                location = binding["member_sha256"], binding["ordinal"]
                literal = literal_identity(raw)
                if (location not in selected or location in bindings
                        or digest(raw) != binding["complete_raw_row_sha256"]
                        or literal["literal_scope_fingerprint"]
                        != binding["literal_scope_fingerprint"]
                        or binding["member_sha256"] != source["original_member_sha256"]):
                    raise BoundedError("SEPTEMBER_OWNERSHIP_ORIGINAL_LITERAL_BINDING_CHANGED")
                bindings.add(location)
                rows, proof = ownership_observation(
                    raw, source_sha256=binding["member_sha256"], revision=binding["release"],
                    resolver=resolver, observed_at=body["observed_at"], component_contract=grain,
                )
                sink.add(rows)
                proofs.append(proof)
                ordinal += 1
            budget.check()
    sink.finish()
    if (bindings != selected or digest(proofs) != body["projection_proof_sha256"]
            or checked != {(t, i["partition"], i["number"])
                          for t in TABLES for i in body["outputs"][t]}):
        raise BoundedError("SEPTEMBER_OWNERSHIP_OUTPUT_PROOF_OR_SELECTION_CENSUS_CHANGED")
    with GlobalScan(budget) as scan:
        for table in TABLES:
            paths = [root / i["path"] for i in body["outputs"][table]]
            scan.add(table, paths)
            if table == HOLDING:
                check_component_global_keys(scan, table)
            else:
                rule = contract["tables"][table]
                scan.keys(table, rule["business_key"], rule["version_key"])
        for table in ("dwd_security", "dwd_listed_company"):
            scan.add(table, [root / body["parents"][table]["path"]])
        tables = {*TABLES, "dwd_security", "dwd_listed_company"}
        scan.references({"tables": [t for t in model["tables"] if t["name"] in tables]},
                        {"tables": {t: contract["tables"][t] for t in tables}})
    result = {"status": "SEPTEMBER_CHANGED_OWNERSHIP_LITERAL_LOCAL_REPLAY_PASS_NOT_NATIVE_UNION",
              "scope_identity": body["identity"], "changed_literal_sources": len(bindings),
              "prior_literal_scopes_reused_with_existing_holds": comparison[
                  "latest_baseline_identical_scopes"
              ], "rows": dict(sink.counts), "native_ownership_union": "NOT_RUN",
              "two_whole_cold_runs": "FINAL_IMAGE_NOT_RUN", "READY": False}
    result["identity"] = digest(result)
    return result
