"""Exact predecessor/period composition for all 33 standard Base tables.

This is a structural stage, not source admission or an audit receipt. The
shared native engine still checks global keys, references, lineage and times.
Empty physical placeholders never become extra partitions beside real rows.
"""
from __future__ import annotations

import importlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError, Budget, identifier
from .base23_native_image import (
    audit_native_image,
    seal_native_image,
    validate_selection,
)
from .base24_delivery import PURPOSE
from .base_build_delta import LINEAGE, _connection, relation
from .base_build_inputs import (
    GENERIC_KINDS,
    _tree,
    file_sha,
    identity,
    inventory,
    member,
)
from .base_build_profile import REFERENCE_TABLES, UPDATED_TABLES, profile, validate_contract


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise BoundedError("BUILD_COMPOSITION_OBJECT_REQUIRED")
    return value


def _lineage(paths: list[Path], budget: Budget) -> list[list[str]]:
    with _connection(budget) as connection:
        rows = connection.execute(
            "SELECT DISTINCT record_source,source_revision,source_artifact_sha256 FROM "
            + relation(paths) + " ORDER BY ALL",
        ).fetchall()
    if any(len(r) != 3 or any(not isinstance(v, str) or not v for v in r) for r in rows):
        raise BoundedError("BUILD_COMPOSITION_LINEAGE_INVALID")
    return [list(row) for row in rows]


def build_composition_plan(*, predecessor: Path, delta: Path, rules: dict[str, Any],
                           contract: dict[str, Any], binding: dict[str, Any],
                           budget: Budget) -> dict[str, Any]:
    """Freeze table rules while retaining original coverage and immutable bytes.

    `binding` must be the exact identity previously passed to period composition;
    it binds the predecessor, input closure, code, policy and actual interval.
    Source roots are execution locations only, never future reading dependencies.
    """
    validate_contract(contract)
    if (set(binding) != {"predecessor_manifest_sha256", "frozen_input_identity",
                         "code_revision", "policy_identity", "predecessor_through",
                         "through_date", "target_version"}
            or binding["through_date"] != contract["business_through"]
            or binding["target_version"] != contract["base_version"]
            or binding["policy_identity"] != identity(contract)
            or date.fromisoformat(binding["predecessor_through"])
            >= date.fromisoformat(binding["through_date"])):
        raise BoundedError("BUILD_COMPOSITION_BINDING_INVALID")
    import re

    for key in ("predecessor_manifest_sha256", "frozen_input_identity", "policy_identity"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(binding[key])):
            raise BoundedError("BUILD_COMPOSITION_BINDING_INVALID")
    if not re.fullmatch(r"[0-9a-f]{40}", str(binding["code_revision"])):
        raise BoundedError("BUILD_COMPOSITION_BINDING_INVALID")
    manifest_path = member(predecessor, "manifest.json")
    if (file_sha(manifest_path) != binding["predecessor_manifest_sha256"]
            or member(predecessor, "READY").read_text().strip() != file_sha(manifest_path)):
        raise BoundedError("BUILD_COMPOSITION_PREDECESSOR_IDENTITY_CHANGED")
    manifest = _object(manifest_path)
    generic = manifest.get("kind") == GENERIC_KINDS["dwd"]
    metadata = manifest.get("content", {}) if generic else manifest
    version = metadata.get("base_version") if generic else "2.4"
    through = metadata.get("through_date") if generic else metadata.get("business_through")
    if (manifest.get("kind") not in {GENERIC_KINDS["dwd"],
                                    "dwd_base24_native_1d5m_purpose_qualified_candidate_v1"}
            or version != contract["predecessor_version"]
            or through != binding["predecessor_through"]):
        raise BoundedError("BUILD_COMPOSITION_PREDECESSOR_SCOPE_CHANGED")
    record_path = member(predecessor, "MATERIALIZATION.json")
    original_plan_path = member(predecessor, "evidence/input-plan.json")
    record, old_plan = _object(record_path), _object(original_plan_path)
    if (file_sha(record_path) != manifest.get("materialization_sha256")
            or record.get("plan_digest") != digest(old_plan)
            or len(rules["tables"]) != 33
            or set(record.get("layout", {})) != set(rules["tables"])
            or set(old_plan.get("tables", {})) != set(rules["tables"])):
        raise BoundedError("BUILD_COMPOSITION_PREDECESSOR_LAYOUT_CHANGED")
    descriptor_path = member(delta, "DELTA.json")
    period = _object(descriptor_path)
    if (period.get("kind") != "p10-standard-base-period-dwd-v1"
            or period.get("identity") != identity({k: v for k, v in period.items()
                                                   if k != "identity"})
            or period.get("binding") != binding or period.get("READY") is not False
            or period.get("lifecycle") != "TMP"
            or set(period.get("tables", {})) not in (UPDATED_TABLES,
                                                       UPDATED_TABLES | REFERENCE_TABLES)):
        raise BoundedError("BUILD_COMPOSITION_PERIOD_IDENTITY_CHANGED")
    period_files: dict[str, list[dict[str, Any]]] = {table: [] for table in period["tables"]}
    seen = set()
    pq = importlib.import_module("pyarrow.parquet")
    for item in period["files"]:
        if item.get("table") not in period_files or item["path"] in seen:
            raise BoundedError("BUILD_COMPOSITION_PERIOD_FILE_SET_CHANGED")
        seen.add(item["path"])
        path = member(delta, item["path"])
        if (file_sha(path) != item["sha256"] or path.stat().st_size != item["size_bytes"]
                or pq.ParquetFile(path).metadata.num_rows != item["rows"]):
            raise BoundedError("BUILD_COMPOSITION_PERIOD_BYTES_CHANGED")
        period_files[item["table"]].append(item)
    if _tree(delta) != seen | {"DELTA.json"}:
        raise BoundedError("BUILD_COMPOSITION_PERIOD_FILE_SET_CHANGED")
    plan: dict[str, Any] = {
        "purpose": PURPOSE, "through_date": binding["through_date"],
        "timezone": "Asia/Shanghai", "build_binding": binding,
        "sources": {
            "predecessor": {"root": str(predecessor), "descriptor": "MATERIALIZATION.json",
                            "descriptor_sha256": file_sha(record_path),
                            "role": "retained_native",
                            "quality_gate": "QUALIFIED_FOR_GOVERNANCE_RETENTION"},
            "period": {"root": str(delta), "descriptor": "DELTA.json",
                       "descriptor_sha256": file_sha(descriptor_path),
                       "role": "qualified_domain_output",
                       "quality_gate": "QUALIFIED_FOR_GOVERNANCE_RETENTION"},
        },
        "tables": {}, "table_dispositions": {},
    }
    for table in sorted(rules["tables"]):
        budget.begin_stage("composition-" + table)
        previous = record["layout"][table]
        inherited = [{k: item[k] for k in ("path", "table", "partition", "rows",
                                           "size_bytes", "sha256")}
                     for item in previous["files"]]
        # The native record includes table; the original layout cannot invent rows.
        counts: Counter[str] = Counter()
        for item in inherited:
            counts[item["partition"]] += item["rows"]
        if dict(counts) != previous["expected_partition_rows"]:
            raise BoundedError("BUILD_COMPOSITION_PREDECESSOR_CENSUS_CHANGED")
        added = period_files.get(table, [])
        if table in REFERENCE_TABLES and any(i["rows"] for i in added):
            with _connection(budget) as connection:
                old = relation([member(predecessor, i["path"]) for i in inherited])
                new = relation([member(delta, i["path"]) for i in added])
                business = list(rules["tables"][table]["business_key"])
                names = pq.ParquetFile(member(delta, added[0]["path"])).schema_arrow.names
                values = [name for name in names if name not in LINEAGE]
                fact = " AND ".join("a." + identifier(name)
                                   + " IS NOT DISTINCT FROM b." + identifier(name)
                                   for name in values)
                keys = [k for k in business if k != "effective_from"]
                overlap = " AND ".join("a." + identifier(k) + " IS NOT DISTINCT FROM b."
                                       + identifier(k) for k in keys)
                # Reusing an inherited identity with a new effective interval or
                # new business values stays rejected exactly as before.
                if connection.execute(
                    "SELECT 1 FROM " + new + " a JOIN " + old + " b ON " + overlap
                    + " WHERE NOT (" + fact + ") LIMIT 1",
                ).fetchone() is not None:
                    raise BoundedError("BUILD_COMPOSITION_IDENTITY_EXTENSION_OVERLAPS_PREDECESSOR")
                # The identical fact may only gain a strictly earlier knowledge
                # version; a restatement at or after the predecessor clock adds
                # no admissible earlier observation and stays rejected.
                if connection.execute(
                    "SELECT 1 FROM " + new + " a JOIN " + old + " b ON " + overlap
                    + " WHERE " + fact + " AND a.knowledge_from>=b.knowledge_from LIMIT 1",
                ).fetchone() is not None:
                    raise BoundedError("BUILD_COMPOSITION_IDENTITY_EXTENSION_REWRITES_PREDECESSOR")
                # A genuinely new identity must still become effective inside the
                # admitted interval, exactly as before.
                lower, upper = binding["predecessor_through"], binding["through_date"]
                if connection.execute(
                    "SELECT 1 FROM " + new + " a WHERE NOT EXISTS (SELECT 1 FROM " + old
                    + " b WHERE " + overlap + ") AND (CAST(a.effective_from AS DATE)<=DATE '"
                    + lower + "' OR CAST(a.effective_from AS DATE)>DATE '" + upper + "') LIMIT 1",
                ).fetchone() is not None:
                    raise BoundedError("BUILD_COMPOSITION_NEW_IDENTITY_EFFECTIVE_OUTSIDE_INTERVAL")
        if table in period_files and (not added or sum(i["rows"] for i in added)
                                        != period["tables"][table]["output_rows"]):
            raise BoundedError("BUILD_COMPOSITION_PERIOD_CENSUS_CHANGED")
        candidates = [dict(item, source_id="predecessor") for item in inherited]
        candidates += [dict(item, source_id="period") for item in added]
        nonempty = [item for item in candidates if item["rows"] > 0]
        files = nonempty if nonempty else [candidates[0]]
        # Each physically empty table has exactly one retained placeholder.
        if not nonempty and (files[0]["partition"] != "empty"
                             or any(i["rows"] != 0 for i in candidates)):
            raise BoundedError("BUILD_COMPOSITION_EMPTY_PARTITION_CHANGED")
        ids = sorted({item["source_id"] for item in files})
        by_source = {}
        for sid in ids:
            if sid == "predecessor":
                by_source[sid] = old_plan["tables"][table]["admitted_lineage"]
            else:
                by_source[sid] = _lineage([member(delta, i["path"]) for i in files
                                            if i["source_id"] == sid], budget)
        admitted = [list(r) for r in sorted({tuple(r) for rs in by_source.values() for r in rs})]
        counts = Counter()
        for item in files:
            counts[item["partition"]] += item["rows"]
        plan["tables"][table] = {
            "source_ids": ids, "files": files, "expected_partition_rows": dict(counts),
            "rows": sum(counts.values()), "admitted_lineage": admitted,
            "lineage_by_source": by_source,
        }
        plan["table_dispositions"][table] = {
            "action": ("APPEND_ADMITTED_PERIOD" if table in UPDATED_TABLES else
                       "APPEND_SOURCE_REPLAYED_IDENTITY_EXTENSION"
                       if table in period_files else "INHERIT"),
            "predecessor_rows": sum(i["rows"] for i in inherited),
            "period_rows": sum(i["rows"] for i in added),
            "predecessor_through": binding["predecessor_through"],
            "coverage_statement": "ORIGINAL_COVERAGE_RETAINED_NO_REFRESH_INFERRED",
        }
    validate_selection(plan, rules, {"predecessor": record, "period": period},
                       profile=profile(contract))
    return plan


def delivery_content(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    """Enumerate all physical output members before the final manifest/READY."""
    _require_scope(root, contract)
    if (root / "manifest.json").exists() or (root / "READY").exists():
        raise BoundedError("BUILD_COMPOSITION_SEALED_OUTPUT_EXISTS")
    files = []
    for relative in sorted(_tree(root)):
        path = member(root, relative)
        files.append({"path": relative, "size_bytes": path.stat().st_size,
                      "sha256": file_sha(path)})
    return {"role": "dwd", "base_version": contract["base_version"],
            "through_date": contract["business_through"], "files": files}


def _require_scope(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    validate_contract(contract)
    stored = _object(member(root, "contract/base-build-delivery.v1.json"))
    plan = _object(member(root, "evidence/input-plan.json"))
    binding = plan.get("build_binding", {})
    if (stored != contract or binding.get("policy_identity") != identity(contract)
            or binding.get("target_version") != contract["base_version"]
            or plan.get("through_date") != contract["business_through"]):
        raise BoundedError("BUILD_COMPOSITION_OUTPUT_SCOPE_CHANGED")
    return plan


def audit_standard_image(root: Path, budget: Budget,
                         contract: dict[str, Any]) -> dict[str, Any]:
    _require_scope(root, contract)
    receipt = audit_native_image(root, budget, profile=profile(contract))
    if (root / "READY").exists():
        # The next build reads the same public inventory contract as this one.
        inventory(root, file_sha(root / "manifest.json"))
    return receipt


def seal_standard_image(root: Path, receipt: dict[str, Any], *, contract: dict[str, Any],
                        code_revision: str) -> dict[str, Any]:
    plan = _require_scope(root, contract)
    if (plan["build_binding"].get("code_revision") != code_revision
            or receipt.get("input_plan_digest") != digest(plan)
            or receipt.get("assessment", {}).get("base_version") != contract["base_version"]
            or receipt.get("assessment", {}).get("business_through")
            != contract["business_through"]):
        raise BoundedError("BUILD_COMPOSITION_SEAL_BINDING_CHANGED")
    return seal_native_image(root, receipt, code_revision=code_revision,
                             profile=profile(contract, content=delivery_content(root, contract)))
