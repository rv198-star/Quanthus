"""Small source-contained successor for the previously held financial frames.

Only an exact subset of the accepted domain's original raw members is used. The
accepted selection, full original source catalog and identity parents are local
evidence. Offline replay never dereferences archived source roots.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .base22 import canonical, digest, file_sha
from .base22_bounded import BoundedError, Budget, GlobalScan, semantic_scan
from .base22_outputs import financial_concept_versions
from .base22_sources import _tree, physical_path
from .base231_domain_pack import _local
from .base231_f10_selection import selected_bodies
from .base231_financial_sparse import repair_sparse_summary
from .base231_financial_summary import load_contract
from .base231_source_qualification import CATALOG, CONTRACT, DWD, ODS
from .financial_reporting import (
    financial_company_resolver_from_listed_company_rows,
    financial_reporting_schemas,
)

KIND = "p10_base231_explicit_undated_financial_column_repair_v1"
QUALIFIED = "sha256:78d090892f436577f3b7b50a4cf6f951e3814ac55a351e699ab5b27cef6b49c6"


def validate_inputs(root: Path) -> dict[str, Any]:
    inventory: dict[str, Any] = json.loads(_local(root, "INPUTS.json").read_bytes())
    if (inventory.get("kind") != KIND or inventory.get("business_through") != "2026-09-30"
            or inventory.get("consumer_admission") != "NOT_GRANTED"
            or not inventory.get("raw_members")
            or set(inventory.get("parents", {})) != {"dwd_security", "dwd_security_identifier"}):
        raise BoundedError("SPARSE_PACK_INPUT_CONTRACT_CHANGED")
    entries = inventory["files"]
    members = {m["path"]: m for m in entries}
    if len(members) != len(entries):
        raise BoundedError("SPARSE_PACK_DUPLICATE_INPUT_MEMBER")
    for name, item in members.items():
        path = _local(root, name)
        if (not path.is_file() or path.stat().st_nlink != 1
                or path.stat().st_size != item["bytes"] or file_sha(path) != item["sha256"]):
            raise BoundedError("SPARSE_PACK_INPUT_BYTES_CHANGED")
    pins = {"evidence/qualified-bundle-manifest.json": QUALIFIED,
            "evidence/SOURCE-CATALOG.json": CATALOG,
            "evidence/original-dwd-manifest.json": DWD,
            "evidence/original-ods-manifest.json": ODS,
            "config/base231-domains.v1.json": CONTRACT}
    if any(name not in members or members[name]["sha256"] != sha for name, sha in pins.items()):
        raise BoundedError("SPARSE_PACK_ORIGINAL_SOURCE_AUTHORITY_CHANGED")
    codes = inventory.get("code_inputs", {})
    required = {"base231_sparse_pack.py", "base231_financial_sparse.py",
                "base231_f10_selection.py", "base231_financial_summary.py",
                "financial_reporting.py"}
    if not required <= set(codes) or any(
        Path(name).name != name or file_sha(Path(__file__).parent / name) != sha
        for name, sha in codes.items()
    ):
        raise BoundedError("SPARSE_PACK_EVALUATOR_CHANGED")
    qualified = json.loads(_local(root, "evidence/qualified-bundle-manifest.json").read_bytes())
    qmembers = {m["path"]: m for m in qualified["members"]}
    catalog = json.loads(_local(root, "evidence/SOURCE-CATALOG.json").read_bytes())
    original = {m["path"]: m for m in catalog["records"] if m["record_type"] == "file"}
    selection_path = "evidence/original-selection.json"
    if members[selection_path]["sha256"] != qmembers["domain/projection/selection.json"]["sha256"]:
        raise BoundedError("SPARSE_PACK_ORIGINAL_SELECTION_CHANGED")
    raw = inventory["raw_members"]
    if len({m["path"] for m in raw}) != len(raw):
        raise BoundedError("SPARSE_PACK_RAW_SET_CHANGED")
    for member in raw:
        original_record = original.get(member["original_path"], {})
        if (not member["original_path"].endswith("/raw_f10.parquet")
                or original_record.get("sha256") != member["sha256"]
                or member["sha256"] != members[member["path"]]["sha256"]
                or original_record.get("source_manifest_sha256")
                != member["original_manifest_sha256"]
                or original_record.get("source_revision", original_record.get("rollup_source"))
                != member["source_revision"]
                or qmembers.get("domain/" + member["path"], {}).get("sha256")
                != member["sha256"]):
            raise BoundedError("SPARSE_PACK_RAW_SOURCE_UNQUALIFIED")
    descriptor = _local(root, "evidence/original-dwd-materialization.json")
    dwd = json.loads(_local(root, "evidence/original-dwd-manifest.json").read_bytes())
    if file_sha(descriptor) != dwd["materialization_sha256"]:
        raise BoundedError("SPARSE_PACK_IDENTITY_PARENT_DESCRIPTOR_CHANGED")
    layout = json.loads(descriptor.read_bytes())["layout"]
    for table, paths in inventory["parents"].items():
        expected = layout[table]["files"]
        if len(paths) != len(expected) or any(
            members[p]["sha256"] != m["sha256"] or members[p]["bytes"] != m["size_bytes"]
            for p, m in zip(paths, expected, strict=True)
        ):
            raise BoundedError("SPARSE_PACK_IDENTITY_PARENT_CHANGED")
    return inventory


def project(root: Path, output: Path, budget: Budget) -> dict[str, Any]:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    inventory = validate_inputs(root)
    selection = json.loads(_local(root, "evidence/original-selection.json").read_bytes())
    sources = {m["path"]: m for m in inventory["raw_members"]}
    selection = {**selection, "selected": [r for r in selection["selected"]
                 if r["member"] in sources and r["category"] == "财务分析"]}
    parents = {t: [r for p in paths for r in pq.read_table(_local(root, p)).to_pylist()]
               for t, paths in inventory["parents"].items()}
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"],
    )
    contract = load_contract(_local(root, "config/base231-domains.v1.json"))
    observed = datetime.fromisoformat(inventory["governance_observed_at"])
    output = budget.destination(output)
    output.mkdir(parents=True, exist_ok=False)
    schemas = financial_reporting_schemas()
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    hashes = {t: hashlib.sha256() for t in schemas}
    concepts: dict[str, dict[str, Any]] = {}
    writers = {t: pq.ParquetWriter(output / (t + ".parquet"), schema, compression="zstd")
               for t, schema in schemas.items()}
    try:
        with (output / "repair-proofs.jsonl").open("xb") as proofs:
            files = [(name, _local(root, name)) for name in sources]
            for item, raw in selected_bodies(files, selection):
                budget.check()
                source = sources[item["member"]]
                rows, proof = repair_sparse_summary(
                    raw, source_sha256=source["sha256"], revision=source["source_revision"],
                    resolver=resolver, observed_at=observed, contract=contract,
                )
                counts["previously_held_frames_with_dated_view"] += len(proof["frames"])
                counts["still_held_frames"] += len(proof["held_frames"])
                counts["reused_already_valid_frames"] += proof["already_valid_frames_reused"]
                counts["excluded_undated_cells"] += proof["excluded_undated_cells"]
                counts["undated_nonempty_cells_held"] += proof["undated_nonempty_cells"]
                classifications = proof["frozen_evaluator"].get("classification_counts", {})
                for key, value in classifications.items():
                    counts[key] += value
                if proof["frames"] or proof["held_frames"]:
                    proofs.write(canonical({"member": item["member"], "ordinal": item["ordinal"],
                                             "proof": proof}) + b"\n")
                for row in rows["dwd_financial_concept"]:
                    key = row["financial_concept_id"]
                    versions = financial_concept_versions([concepts[key], row]) if key in concepts \
                        else [row]
                    if len(versions) != 1:
                        raise BoundedError("SPARSE_PACK_CONCEPT_CONFLICT")
                    concepts[key] = versions[0]
                for table in ("dwd_financial_report", "dwd_financial_fact"):
                    typed = pa.Table.from_pylist(rows[table], schema=schemas[table])
                    if typed.to_pylist() != rows[table]:
                        raise BoundedError("SPARSE_PACK_SERIALIZED_ATOM_CHANGED")
                    writers[table].write_table(typed)
                    sizes[table] += len(rows[table])
                    for row in typed.to_pylist():
                        hashes[table].update(canonical(row) + b"\n")
            table = "dwd_financial_concept"
            concept_rows = [concepts[k] for k in sorted(concepts)]
            typed = pa.Table.from_pylist(concept_rows, schema=schemas[table])
            writers[table].write_table(typed)
            sizes[table] = len(concept_rows)
            for row in typed.to_pylist():
                hashes[table].update(canonical(row) + b"\n")
    finally:
        for writer in writers.values():
            writer.close()
    readers = [] if budget.root == root or budget.root in root.parents else [root]
    with GlobalScan(budget, read_only_roots=readers) as scan:
        for table in schemas:
            scan.add(table, [output / (table + ".parquet")])
            key = table.removeprefix("dwd_") + "_id"
            scan.reject(f"SELECT 1 FROM {table} GROUP BY {key} HAVING count(*)>1 LIMIT 1",
                        "SPARSE_PACK_GLOBAL_BUSINESS_KEY_DUPLICATE")
            scan.reject(f"SELECT 1 FROM {table} WHERE knowledge_from IS NULL "
                        "OR knowledge_to IS NOT NULL LIMIT 1",
                        "SPARSE_PACK_NEW_KNOWLEDGE_INTERVAL_INVALID")
            allowed = {r["sha256"] for r in inventory["raw_members"]}
            if any(r[0] not in allowed for r in scan.connection.execute(
                f"SELECT DISTINCT source_artifact_sha256 FROM {table}").fetchall()
            ):
                raise BoundedError("SPARSE_PACK_ROW_SOURCE_UNQUALIFIED")
        for column, target in [("financial_report_id", "dwd_financial_report"),
                               ("financial_concept_id", "dwd_financial_concept")]:
            scan.reject(f"SELECT 1 FROM dwd_financial_fact f LEFT JOIN {target} t "
                        f"USING({column}) WHERE t.{column} IS NULL LIMIT 1",
                        "SPARSE_PACK_FINANCIAL_FOREIGN_KEY_MISSING")
        for table in ("dwd_financial_report", "dwd_financial_fact"):
            scan.reject(f"SELECT 1 FROM {table} WHERE period_end > DATE '2026-09-30' "
                        "OR published_at IS NOT NULL LIMIT 1",
                        "SPARSE_PACK_PERIOD_OR_PUBLICATION_INVALID")
    physical = {t: semantic_scan([output / (t + ".parquet")], schema,
                                check=budget.check) for t, schema in schemas.items()}
    return {"rows": {t: sizes[t] for t in schemas}, "counts": dict(counts),
            "semantic_sha256": {t: "sha256:" + hashes[t].hexdigest() for t in schemas},
            "physical_semantics": physical,
            "proofs_sha256": file_sha(output / "repair-proofs.jsonl"),
            "consumer_admission": "NOT_GRANTED", "full_mirror_integration_complete": False}


def seal(root: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    root = physical_path(root)
    if (root / "READY").exists() or (root / "manifest.json").exists():
        raise BoundedError("SPARSE_PACK_ALREADY_SEALED")
    validate_inputs(root)
    members = [{"path": p, "bytes": (root / p).stat().st_size, "sha256": file_sha(root / p)}
               for p in sorted(_tree(root))]
    manifest = {"kind": KIND, "business_through": "2026-09-30", "receipt": receipt,
                "members": members, "consumer_admission": "NOT_GRANTED",
                "full_mirror_integration_complete": False}
    manifest["identity"] = digest(manifest)
    (root / "manifest.json").open("xb").write(canonical(manifest))
    (root / "READY").open("xb").write(canonical({"manifest_sha256": file_sha(
        root / "manifest.json")}))
    return manifest


def audit(root: Path, budget: Budget) -> dict[str, Any]:
    root = physical_path(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    if (manifest.get("kind") != KIND
            or manifest.get("identity") != digest({k: v for k, v in manifest.items()
                                                   if k != "identity"})
            or manifest.get("business_through") != "2026-09-30"
            or manifest.get("consumer_admission") != "NOT_GRANTED"
            or manifest.get("full_mirror_integration_complete") is not False
            or json.loads((root / "READY").read_bytes()) != {
                "manifest_sha256": file_sha(root / "manifest.json")}):
        raise BoundedError("SPARSE_PACK_SEAL_CHANGED")
    members = manifest["members"]
    if (len(members) != len({m["path"] for m in members}) or _tree(root)
            != {m["path"] for m in members} | {"manifest.json", "READY"}):
        raise BoundedError("SPARSE_PACK_PHYSICAL_SET_CHANGED")
    for member in members:
        path = _local(root, member["path"])
        if (not path.is_file() or path.stat().st_nlink != 1
                or path.stat().st_size != member["bytes"] or file_sha(path) != member["sha256"]):
            raise BoundedError("SPARSE_PACK_PHYSICAL_BYTES_CHANGED")
    actual = project(root, budget.root / "replay", budget)
    if actual != manifest["receipt"]:
        raise BoundedError("SPARSE_PACK_OFFLINE_SEMANTIC_REPLAY_CHANGED")
    return {"status": "PASS_SOURCE_BOUND_SPARSE_FINANCIAL_OFFLINE_REPLAY",
            "identity": manifest["identity"], "receipt": actual,
            "full_mirror_integration_complete": False}
