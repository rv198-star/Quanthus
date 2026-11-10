"""Bounded local replay of complete dividend literal subsets and typed output."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha, load_contract, partition, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_sources import _tree
from .base23_dividend import (
    CONTRACT_ID,
    QUALIFICATION,
    TABLES,
    dividend_observation,
    latest_literal_captures,
)
from .financial_reporting import financial_company_resolver_from_listed_company_rows


def audit_dividend_scope(root: Path, budget: Budget) -> dict[str, Any]:
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
        != file_sha(Path(__file__).with_name("base23_dividend.py"))
        or any(body.get(k) is not False for k in ("READY", "complete_Base23"))
        or body.get("consumer_admission") != "NOT_GRANTED"
        or set(body.get("outputs", {})) != set(TABLES)
    ):
        raise BoundedError("SEPTEMBER_DIVIDEND_SCOPE_UNQUALIFIED")
    if _tree(root) != {"SCOPE.json", *body["physical_members"]}:
        raise BoundedError("SEPTEMBER_DIVIDEND_PHYSICAL_SET_CHANGED")
    wanted = {i["path"].removeprefix("payload/") for t in TABLES for i in body["outputs"][t]}
    if _tree(root / "payload") != wanted or any(
        i["path"] != f"payload/{t}/{i['partition']}/part-000000.parquet"
        for t in TABLES for i in body["outputs"][t]
    ):
        raise BoundedError("SEPTEMBER_DIVIDEND_PHYSICAL_PARTITION_SET_CHANGED")
    for relative, item in body["physical_members"].items():
        path = _physical(root, relative, independent=True)
        if path.stat().st_size != item["size_bytes"] or file_sha(path) != item["sha256"]:
            raise BoundedError("SEPTEMBER_DIVIDEND_PHYSICAL_HASH_CHANGED")
        budget.check()
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    history = json.loads((root / body["history_materialization"]).read_bytes())
    if (file_sha(root / body["history_materialization"])
            != closure["history_materialization_sha256"]):
        raise BoundedError("SEPTEMBER_DIVIDEND_HISTORY_IDENTITY_CHANGED")
    parents = {}
    for table, item in body["parents"].items():
        path = _physical(root, item["path"], independent=True)
        if (pq.ParquetFile(path).metadata.num_rows > 32768 or not any(
            f["path"] == item["archive_path"] and f["sha256"] == file_sha(path)
            for f in history["layout"][table]["files"]
        )):
            raise BoundedError("SEPTEMBER_DIVIDEND_NATIVE_PARENT_CHANGED")
        parents[table] = pq.ParquetFile(path).read().to_pylist()
    if set(parents) != {"dwd_security", "dwd_security_identifier", "dwd_listed_company"}:
        raise BoundedError("SEPTEMBER_DIVIDEND_EXACT_PARENT_SET_CHANGED")
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"],
    )
    observations = []
    if len(body["sources"]) != len(closure["sources"]):
        raise BoundedError("SEPTEMBER_DIVIDEND_RAW_SOURCE_SET_CHANGED")
    for source, local in zip(closure["sources"], body["sources"], strict=True):
        manifest_path = _physical(root, local["manifest"], independent=True)
        manifest = json.loads(manifest_path.read_bytes())
        if (file_sha(manifest_path) != source["manifest_sha256"]
            or manifest.get("status") != "ready" or manifest["release_id"] != source["release"]
            or not any(a["relative_path"] == source["path"] and a["sha256"] == source["sha256"]
                       for a in manifest["artifacts"])):
            raise BoundedError("SEPTEMBER_DIVIDEND_ORIGINAL_MANIFEST_CHANGED")
        bindings = json.loads((root / local["bindings"]).read_bytes())
        raw_path = _physical(root, local["raw"], independent=True)
        count = 0
        for batch in pq.ParquetFile(raw_path).iter_batches(batch_size=256):
            for raw in batch.to_pylist():
                binding = bindings[count]
                if (binding["member_sha256"] != source["sha256"]
                    or binding["release"] != source["release"]
                    or binding["complete_raw_row_sha256"] != digest(raw)
                    or not 0 <= binding["ordinal"] < source["rows"]):
                    raise BoundedError("SEPTEMBER_DIVIDEND_DERIVED_LITERAL_BINDING_CHANGED")
                observations.append((raw, binding))
                count += 1
            budget.check()
        if count != len(bindings) or len({i["ordinal"] for i in bindings}) != count:
            raise BoundedError("SEPTEMBER_DIVIDEND_COMPLETE_SUBSET_CENSUS_CHANGED")
    selected = latest_literal_captures(observations)
    if digest([b for _, b in selected]) != body["selection_sha256"]:
        raise BoundedError("SEPTEMBER_DIVIDEND_LATEST_SELECTION_CHANGED")
    vocabulary = json.loads((root / "contract/disclosure-event-vocabulary.v1.json").read_bytes())
    expected: dict[str, dict[str, list[dict[str, Any]]]] = {t: defaultdict(list) for t in TABLES}
    proofs = []
    for raw, binding in selected:
        rows, proof = dividend_observation(
            raw, source_sha256=binding["member_sha256"], revision=binding["release"],
            resolver=resolver, observed_at=body["observed_at"], vocabulary=vocabulary, schemas=ss,
        )
        for table, values in rows.items():
            for row in values:
                expected[table][partition(row, contract["tables"][table])].append(row)
        proofs.append(proof)
    if digest(proofs) != body["projection_proof_sha256"]:
        raise BoundedError("SEPTEMBER_DIVIDEND_LITERAL_PROOF_CHANGED")
    with GlobalScan(budget) as scan:
        for table in TABLES:
            if set(expected[table]) != {i["partition"] for i in body["outputs"][table]}:
                raise BoundedError("SEPTEMBER_DIVIDEND_EXACT_PARTITION_CENSUS_CHANGED")
            paths = []
            for item in body["outputs"][table]:
                path = root / item["path"]
                if (pq.ParquetFile(path).read().to_pylist()
                    != sorted(expected[table][item["partition"]], key=digest)
                    or semantic_scan([path], ss[table], check=budget.check)
                    != item["semantic_scan"]):
                    raise BoundedError("SEPTEMBER_DIVIDEND_TYPED_ATOM_CHANGED")
                paths.append(path)
            scan.add(table, paths)
            rule = contract["tables"][table]
            scan.keys(table, rule["business_key"], rule["version_key"])
        for table in ("dwd_security", "dwd_listed_company"):
            scan.add(table, [root / body["parents"][table]["path"]])
        tables = {*TABLES, "dwd_security", "dwd_listed_company"}
        scan.references({"tables": [t for t in model["tables"] if t["name"] in tables]},
                        {"tables": {t: contract["tables"][t] for t in tables}})
    result = {"status": "SEPTEMBER_DIVIDEND_LOCAL_EXACT_REPLAY_PASS_NOT_NATIVE_UNION",
              "scope_identity": body["identity"], "archived_literal_rows": len(observations),
              "latest_literal_scopes": len(selected),
              "rows": {t: sum(map(len, expected[t].values())) for t in TABLES},
              "native_action_union": "NOT_RUN", "READY": False}
    result["identity"] = digest(result)
    return result
