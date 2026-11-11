"""Bounded, source-contained financial/holder patch projections and offline replay.

This is a domain output, not a whole Base release or consumer admission. Source
files and referential parents are immutable local evidence. Original payloads are
never opened by the replay through archived source-root strings.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .base22 import canonical, digest, file_sha
from .base22_bounded import BoundedError, Budget, GlobalScan
from .base22_holder_components_v2 import COMPONENT_KEYS, load_component_contract
from .base22_outputs import financial_concept_versions
from .base22_sources import _tree, physical_path
from .base23_ownership import ownership_observation
from .base231_f10_selection import select_metadata, selected_bodies
from .base231_financial_summary import load_contract, summary_observation
from .base231_holder_scope import certify_frames, project_change_tables
from .financial_reporting import (
    financial_company_resolver_from_listed_company_rows,
    financial_reporting_schemas,
)
from .ownership_capital import ownership_capital_schemas

TABLES = tuple(financial_reporting_schemas()) + (
    "dwd_shareholding_observation", "dwd_shareholding_change",
)
KIND = "p10_base231_source_contained_domain_projection_v1"


def schemas() -> dict[str, Any]:
    return {**financial_reporting_schemas(),
            **{t: ownership_capital_schemas()[t] for t in TABLES[3:]}}


def _local(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or any(p in {".", ".."} for p in path.parts):
        raise BoundedError("BASE231_EVIDENCE_PATH_INVALID")
    return physical_path(root / path)


def validate_inputs(root: Path, inventory: dict[str, Any]) -> None:
    if (inventory.get("business_through") != "2026-09-30"
            or inventory.get("kind") != KIND or not inventory.get("raw_members")
            or inventory.get("consumer_admission") != "NOT_GRANTED"
            or inventory.get("provider_PIT_qualified") is not False):
        raise BoundedError("BASE231_DOMAIN_INVENTORY_INVALID")
    code = inventory.get("code_inputs", {})
    required = {"base231_domain_pack.py", "base231_f10_selection.py",
                "base231_financial_summary.py", "base231_holder_scope.py",
                "base23_ownership.py", "financial_reporting.py", "ownership_capital.py"}
    if not required <= set(code) or any(
        Path(name).name != name or file_sha(Path(__file__).parent / name) != sha
        for name, sha in code.items()
    ):
        raise BoundedError("BASE231_DOMAIN_EVALUATOR_IDENTITY_CHANGED")
    entries = inventory["files"]
    if len({r["path"] for r in entries}) != len(entries):
        raise BoundedError("BASE231_DOMAIN_DUPLICATE_FILE")
    known = {r["path"] for r in entries}
    if (not all(r["path"] in known for r in inventory["raw_members"])
            or set(inventory["parents"]) != {"dwd_security", "dwd_security_identifier"}
            or any(p not in known for paths in inventory["parents"].values() for p in paths)):
        raise BoundedError("BASE231_DOMAIN_EVIDENCE_CLOSURE_INCOMPLETE")
    by_path = {r["path"]: r for r in entries}
    if (inventory["domain_contract"] not in known
            or any(not isinstance(r.get("source_revision"), str) or not r["source_revision"]
                   or r["sha256"] != by_path[r["path"]]["sha256"]
                   for r in inventory["raw_members"])):
        raise BoundedError("BASE231_DOMAIN_SOURCE_QUALIFICATION_CHANGED")
    for item in entries:
        path = _local(root, item["path"])
        if (not path.is_file() or path.stat().st_nlink != 1
                or path.stat().st_size != item["bytes"] or file_sha(path) != item["sha256"]):
            raise BoundedError("BASE231_DOMAIN_EVIDENCE_BYTES_CHANGED")


def _project(
    root: Path, inventory: dict[str, Any], output: Path, budget: Budget,
) -> dict[str, Any]:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    validate_inputs(root, inventory)
    contract = load_contract(_local(root, inventory["domain_contract"]))
    component = load_component_contract(_local(root, inventory["component_config_root"]))
    parents = {t: [row for path in paths
                   for row in pq.read_table(_local(root, path)).to_pylist()]
               for t, paths in inventory["parents"].items()}
    resolver = financial_company_resolver_from_listed_company_rows(
        parents["dwd_security"], parents["dwd_security_identifier"],
    )
    files = [(r["path"], _local(root, r["path"])) for r in inventory["raw_members"]]
    bindings = {r["path"]: r for r in inventory["raw_members"]}
    selection = select_metadata(files)
    observed = datetime.fromisoformat(inventory["governance_observed_at"])
    output.mkdir(parents=True, exist_ok=False)
    (output / "selection.json").write_bytes(canonical(selection))
    writers: dict[str, Any] = {}
    concepts: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    row_schemas = schemas()
    hashes = {t: hashlib.sha256() for t in TABLES}
    row_counts: Counter[str] = Counter()
    source_records: Counter[str] = Counter()

    def write(table: str, rows: list[dict[str, Any]]) -> None:
        if table == "dwd_financial_concept":
            for row in rows:
                key = row["financial_concept_id"]
                if key in concepts:
                    values = financial_concept_versions([concepts[key], row])
                    if len(values) != 1:
                        raise BoundedError("BASE231_CONCEPT_VERSION_CONFLICT")
                    concepts[key] = values[0]
                else:
                    concepts[key] = row
            return
        typed = pa.Table.from_pylist(rows, schema=row_schemas[table])
        if typed.to_pylist() != rows or any(
            not field.nullable and typed.column(field.name).null_count
            for field in row_schemas[table]
        ):
            raise BoundedError("BASE231_DOMAIN_TYPED_ATOM_CHANGED")
        writers[table].write_table(typed)
        row_counts[table] += len(rows)
        for row in typed.to_pylist():
            hashes[table].update(canonical(row) + b"\n")

    evidence = output / "frame-proofs.jsonl"
    try:
        for table in TABLES:
            writers[table] = pq.ParquetWriter(output / (table + ".parquet"), row_schemas[table],
                                            compression="zstd")
        with evidence.open("wb") as proofs:
            for item, raw in selected_bodies(files, selection):
                budget.check()
                source = bindings[item["member"]]
                common = dict(source_sha256=source["sha256"], revision=source["source_revision"],
                              resolver=resolver, observed_at=observed)
                identity = dict(member=item["member"], ordinal=item["ordinal"],
                                raw_row_sha256=digest(raw), category=raw["category"])
                try:
                    if raw["category"] == "财务分析":
                        rows, proof = summary_observation(raw, contract=contract, **common)
                        for key, value in proof["classification_counts"].items():
                            counts["financial:" + key] += value
                        counts["financial:held_frames"] += len(proof["held_frames"])
                        counts["financial:conflicting_coordinates"] += proof[
                            "conflicting_coordinates"]
                    else:
                        rows, proof = ownership_observation(raw, component_contract=component,
                                                            **common)
                        certificate = certify_frames(proof, rows["dwd_shareholding_observation"])
                        counts["holder:certified_frames"] += certificate["certified_frames"]
                        counts["holder:held_frames"] += certificate["held_frames"]
                        changes, change_proof = project_change_tables(
                            raw, source_sha256=source["sha256"], resolver=resolver,
                            observed_at=observed,
                        )
                        rows.update(changes)
                        proof["complete_frame_certificate"] = certificate
                        proof["source_reported_changes"] = change_proof
                        counts["change:held_blocks"] += sum(
                            b["status"] == "HELD_NOT_EMPTY_COVERAGE"
                            for b in change_proof["blocks"])
                        counts["change:held_natural_keys"] += change_proof["counts"].get(
                            "held_natural_keys", 0)
                except BoundedError as exc:
                    counts["held_body:" + str(exc)] += 1
                    proofs.write(canonical({**identity, "status": "HELD", "reason": str(exc)})
                                 + b"\n")
                    continue
                for table, values in rows.items():
                    write(table, values)
                source_records[raw["category"]] += 1
                proofs.write(canonical({**identity, "status": "PROJECTION_WITH_SCOPED_HOLDS",
                                        "proof": proof}) + b"\n")
        concept_rows = [concepts[k] for k in sorted(concepts)]
        table = "dwd_financial_concept"
        typed = pa.Table.from_pylist(concept_rows, schema=row_schemas[table])
        writers[table].write_table(typed)
        row_counts[table] = len(concept_rows)
        for row in typed.to_pylist():
            hashes[table].update(canonical(row) + b"\n")
    finally:
        for writer in writers.values():
            writer.close()
    metrics = {"rows": {t: row_counts[t] for t in TABLES},
               "typed_summaries": {t: "sha256:" + hashes[t].hexdigest() for t in TABLES},
               "source_records": dict(source_records), "counts": dict(counts),
               "selection_sha256": file_sha(output / "selection.json"),
               "frame_proofs_sha256": file_sha(evidence), "selected_bodies": len(
                   selection["selected"]), "selection_held": len(selection["held"]),
               "transaction_equivalence": "NOT_PROVEN_KEEP_SEPARATE_DO_NOT_NET",
               "provider_PIT_qualified": False, "consumer_admission": "NOT_GRANTED"}
    (output / "metrics.json").write_bytes(canonical(metrics))
    budget.check()
    return metrics


def _global_checks(output: Path, inventory: dict[str, Any], budget: Budget) -> None:
    with GlobalScan(budget) as scan:
        for table in TABLES:
            scan.add(table, [output / (table + ".parquet")])
        primary = {"dwd_financial_report": ["financial_report_id"],
                   "dwd_financial_concept": ["financial_concept_id"],
                   "dwd_financial_fact": ["financial_fact_id"],
                   "dwd_shareholding_observation": COMPONENT_KEYS,
                   "dwd_shareholding_change": ["shareholding_change_id"]}
        for table, keys in primary.items():
            # Component-null is a governed business coordinate, not a missing
            # key. SQL grouping compares full typed keys across all row groups.
            columns = ",".join([*keys, "knowledge_from"])
            scan.reject(f"SELECT 1 FROM {table} GROUP BY {columns} HAVING count(*)>1 LIMIT 1",
                        "BASE231_DOMAIN_GLOBAL_VERSION_KEY_DUPLICATE")
            scan.reject(f"SELECT 1 FROM {table} WHERE knowledge_from IS NULL "
                        "OR knowledge_to IS NOT NULL LIMIT 1",
                        "BASE231_DOMAIN_NEW_OBSERVATION_INTERVAL_INVALID")
        scan.reject("SELECT 1 FROM dwd_financial_fact f LEFT JOIN dwd_financial_report r "
                    "USING(financial_report_id) WHERE r.financial_report_id IS NULL",
                    "BASE231_FINANCIAL_REPORT_REFERENCE_MISSING")
        scan.reject("SELECT 1 FROM dwd_financial_fact f LEFT JOIN dwd_financial_concept c "
                    "USING(financial_concept_id) WHERE c.financial_concept_id IS NULL",
                    "BASE231_FINANCIAL_CONCEPT_REFERENCE_MISSING")
        allowed = {r["sha256"] for r in inventory["raw_members"]}
        for table in TABLES:
            values = scan.connection.execute(
                f"SELECT DISTINCT source_artifact_sha256 FROM {table}").fetchall()
            if any(v[0] not in allowed for v in values):
                raise BoundedError("BASE231_DOMAIN_UNKNOWN_ROW_SOURCE")


def build_domain_pack(root: Path, inventory: dict[str, Any], budget: Budget) -> dict[str, Any]:
    if root != budget.root or (root / "READY").exists():
        raise BoundedError("BASE231_DOMAIN_UNSEALED_NEW_ROOT_REQUIRED")
    inventory_path = root / "INPUTS.json"
    if inventory_path.exists():
        raise BoundedError("BASE231_DOMAIN_INPUT_FREEZE_ALREADY_EXISTS")
    inventory_path.write_bytes(canonical(inventory))
    metrics = _project(root, inventory, root / "projection", budget)
    _global_checks(root / "projection", inventory, budget)
    members = [{"path": name, "sha256": file_sha(root / name),
                "bytes": (root / name).stat().st_size} for name in sorted(_tree(root))]
    manifest = {"kind": KIND, "version": "2.3.1", "business_through": "2026-09-30",
                "purpose": "GOVERNANCE_LITERAL_DOMAIN_OUTPUT_NOT_FULL_BASE",
                "consumer_admission": "NOT_GRANTED", "provider_PIT_qualified": False,
                "members": members, "metrics": metrics, "input_digest": digest(inventory)}
    (root / "manifest.json").write_bytes(canonical(manifest))
    budget.check()
    (root / "READY").write_bytes(canonical({"manifest_sha256": file_sha(root / "manifest.json")}))
    return manifest


def audit_domain_pack(root: Path, budget: Budget) -> dict[str, Any]:
    root = physical_path(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    ready = json.loads((root / "READY").read_bytes())
    if (manifest.get("kind") != KIND or manifest.get("version") != "2.3.1"
            or manifest.get("business_through") != "2026-09-30"
            or manifest.get("consumer_admission") != "NOT_GRANTED"
            or manifest.get("provider_PIT_qualified") is not False
            or ready != {"manifest_sha256": file_sha(root / "manifest.json")}):
        raise BoundedError("BASE231_DOMAIN_SEAL_INVALID")
    members = manifest["members"]
    expected = {r["path"] for r in members} | {"manifest.json", "READY"}
    actual = _tree(root)
    if actual != expected or len(members) != len({r["path"] for r in members}):
        raise BoundedError("BASE231_DOMAIN_PHYSICAL_FILE_SET_CHANGED")
    for item in members:
        path = _local(root, item["path"])
        if (path.stat().st_nlink != 1 or path.stat().st_size != item["bytes"]
                or file_sha(path) != item["sha256"]):
            raise BoundedError("BASE231_DOMAIN_MEMBER_CHANGED")
    inventory = json.loads((root / "INPUTS.json").read_bytes())
    if digest(inventory) != manifest["input_digest"]:
        raise BoundedError("BASE231_DOMAIN_INPUT_IDENTITY_CHANGED")
    replay = budget.root / "replay"
    metrics = _project(root, inventory, replay, budget)
    _global_checks(replay, inventory, budget)
    if metrics != manifest["metrics"]:
        raise BoundedError("BASE231_DOMAIN_OFFLINE_REPLAY_CHANGED")
    import pyarrow.parquet as pq

    for table in TABLES:
        path = root / "projection" / (table + ".parquet")
        if (not pq.ParquetFile(path).schema_arrow.equals(schemas()[table], check_metadata=True)
                or file_sha(path) != file_sha(replay / path.name)):
            raise BoundedError("BASE231_DOMAIN_OUTPUT_NOT_SOURCE_REPRODUCIBLE")
    return {"status": "PASS_SOURCE_CONTAINED_DOMAIN_REPLAY", "manifest_sha256": file_sha(
        root / "manifest.json"), "metrics": metrics, "full_base_acceptance": False,
        "old_source_roots_dereferenced": False, "network_required": False}
