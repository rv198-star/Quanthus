"""Source-independent validation of a complete scoped native factor copy."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .base22 import _physical, canonical, digest, file_sha, load_contract, schemas
from .base22_bounded import BoundedError, Budget, GlobalScan, Member, semantic_scan
from .base22_factor_lineage import CONTRACT_ID, TABLES, replay_factor_lineage
from .base22_sources import _tree
from .financial_reporting import financial_company_resolver_from_listed_company_rows

PARENTS = ("dwd_listed_company", "dwd_security", "dwd_security_identifier")
ROLES = (*TABLES, *PARENTS, "adjustment_factors_pit", "factor_pit_exceptions")
VOCABULARY_SHA = "sha256:9196a3c0dad1e35f7f873a652d7782734938f432d08c5cacb3d49ab2cba90577"
PURPOSE = "COMPLETE_NATIVE_FACTOR_COPY_WITH_RAW_BINDING_NOT_PIT_OR_BASE_ADMISSION"
CONFIGURATION = (
    "base22-independent-image.v1.json",
    "dwd-domain-model.v1.json",
    "dwd-domain-model.v1.1-extension.json",
    "disclosure-event-vocabulary.v1.json",
)


def audit_native_factor_scope(
    root: Path, descriptor: dict[str, Any], budget: Budget
) -> dict[str, Any]:
    """Recompute complete local raw/typed closure, global keys, endpoints and FKs.

    GlobalScan requires a kernel read-only root and a disjoint write-only TMP.
    All source_root strings in the archive are historical metadata, never paths
    to open. This cannot accept a full image or recover old normalization evidence.
    """
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    expected_disposition = {
        "schema_version": 1,
        "contract_id": CONTRACT_ID,
        "purpose": PURPOSE,
        "READY": False,
        "complete_Base22": False,
        "historical_PIT_admission": False,
        "original_normalization_manifest_restored": False,
        "original_vendor_decimal_precision_recovered": False,
        "provider_acquisition_inferred": False,
        "native_atoms_changed": 0,
        "consumer_admission": "NOT_GRANTED",
    }
    if descriptor.get("identity") != digest(
        {k: v for k, v in descriptor.items() if k != "identity"}
    ):
        raise BoundedError("NATIVE_FACTOR_SCOPE_IDENTITY_INVALID")
    if any(
        type(descriptor.get(k)) is not type(v) or descriptor.get(k) != v
        for k, v in expected_disposition.items()
    ):
        raise BoundedError("NATIVE_FACTOR_SCOPE_DISPOSITION_INVALID")
    if set(descriptor.get("members", {})) != set(ROLES):
        raise BoundedError("NATIVE_FACTOR_COMPLETE_ROLE_SET_INVALID")
    paths = {
        role: ("payload/" if role in {*TABLES, *PARENTS} else "sources/") + role + ".parquet"
        for role in ROLES
    }
    if any(descriptor["members"][r].get("path") != p for r, p in paths.items()):
        raise BoundedError("NATIVE_FACTOR_EXACT_ROLE_PATH_INVALID")
    if descriptor.get("bindings", {}).get("path") != "evidence/availability-bindings.parquet":
        raise BoundedError("NATIVE_FACTOR_EXACT_BINDING_PATH_INVALID")
    physical = descriptor.get("physical_members", {})
    required_files = {
        *paths.values(),
        "evidence/availability-bindings.parquet",
        *("contract/" + n for n in CONFIGURATION),
    }
    if (
        not isinstance(physical, dict)
        or set(physical) != required_files
        or set(_tree(root)) != {*required_files, "QUALIFICATION.json"}
    ):
        raise BoundedError("NATIVE_FACTOR_EXACT_PHYSICAL_SET_INVALID")
    for relative, item in physical.items():
        path = _physical(root, relative, independent=True)
        if path.stat().st_size != item["size_bytes"] or file_sha(path) != item["sha256"]:
            raise BoundedError("NATIVE_FACTOR_LOCAL_FILE_BYTES_CHANGED")
        budget.check()
    contract, model, extension = load_contract(root / "contract")
    ss = schemas(model, extension)
    vocab_path = _physical(root, "contract/disclosure-event-vocabulary.v1.json", independent=True)
    if file_sha(vocab_path) != VOCABULARY_SHA:
        raise BoundedError("NATIVE_FACTOR_VOCABULARY_NOT_FROZEN")
    vocabulary = json.loads(vocab_path.read_bytes())
    members = {}
    for role, meta in descriptor["members"].items():
        relative = meta["path"]
        if relative not in physical or any(
            meta[k] != physical[relative][k] for k in ("sha256", "size_bytes")
        ):
            raise BoundedError("NATIVE_FACTOR_ROLE_FILE_IDENTITY_INVALID")
        path = _physical(root, relative, independent=True)
        member = Member(path, meta["sha256"], meta["size_bytes"], meta["rows"])
        member.verify()
        parquet = pq.ParquetFile(path)
        if parquet.metadata.num_rows != meta["rows"] or meta["rows"] > 196608:
            raise BoundedError("NATIVE_FACTOR_PHYSICAL_ROW_SCOPE_INVALID")
        if role in ss and not parquet.schema_arrow.equals(ss[role]):
            raise BoundedError("NATIVE_FACTOR_PHYSICAL_SCHEMA_CHANGED")
        members[role] = member

    def rows(role: str) -> Iterator[dict[str, Any]]:
        for batch in pq.ParquetFile(members[role].path).iter_batches(batch_size=1024):
            budget.check()
            yield from batch.to_pylist()

    if any(members[t].rows > 65536 for t in PARENTS):
        raise BoundedError("NATIVE_FACTOR_PARENT_SCOPE_EXCEEDED")
    resolver = financial_company_resolver_from_listed_company_rows(
        list(rows("dwd_security")),
        list(rows("dwd_security_identifier")),
    )
    proof = replay_factor_lineage(
        rows("adjustment_factors_pit"),
        rows("factor_pit_exceptions"),
        {t: rows(t) for t in TABLES},
        resolver=resolver,
        vocabulary=vocabulary,
        raw_sha256=members["adjustment_factors_pit"].sha256,
        check=budget.check,
    )
    if proof != descriptor["full_raw_replay"]:
        raise BoundedError("NATIVE_FACTOR_RECOMPUTED_RAW_PROOF_DIFFERS")
    binding_meta = descriptor["bindings"]
    if binding_meta["path"] not in physical:
        raise BoundedError("NATIVE_FACTOR_BINDING_FILE_MISSING")
    binding_path = _physical(root, binding_meta["path"], independent=True)
    h = hashlib.sha256()
    count = 0
    for batch in pq.ParquetFile(binding_path).iter_batches(batch_size=1024):
        for row in batch.to_pylist():
            h.update(canonical(row) + b"\n")
            count += 1
        budget.check()
    if (
        "sha256:" + h.hexdigest() != proof["binding_stream_sha256"]
        or count != proof["factor_rows"]
        or count != binding_meta["rows"]
    ):
        raise BoundedError("NATIVE_FACTOR_UNAVAILABLE_BINDING_CENSUS_DIFFERS")
    scoped = {*TABLES, *PARENTS}
    summaries = {}
    with GlobalScan(budget, read_only_roots=[root]) as scan:
        for table in sorted(scoped):
            m = members[table]
            scan.add(table, [m.path])
            rule = contract["tables"][table]
            scan.row_rules(table, ss[table], rule)
            scan.keys(table, rule["business_key"], rule["version_key"])
            summaries[table] = semantic_scan([m.path], ss[table], batch_rows=1024)
        scan.references(
            {"tables": [t for t in model["tables"] if t["name"] in scoped]},
            {"tables": {t: contract["tables"][t] for t in scoped}},
        )
    if summaries != descriptor["native_semantic_scans"]:
        raise BoundedError("NATIVE_FACTOR_RECOMPUTED_TYPED_SUMMARY_DIFFERS")
    result = {
        "status": "ALL_NATIVE_FACTOR_RAW_TYPED_INTERVAL_UNAVAILABLE_GLOBAL_CLOSURE_PASS_NOT_PIT",
        "full_raw_replay": proof,
        "native_semantic_scans": summaries,
        "physical_files": len(physical) + 1,
        "complete_Base22": False,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result
