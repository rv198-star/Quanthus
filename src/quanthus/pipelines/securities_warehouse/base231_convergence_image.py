"""Complete native successor with exact financial/event/holder scope composition.

Legacy qualification and consumer gates remain unchanged. Reconstruct contained
financial/event dependencies after the full native scan; archived roots are only
strings. Only this explicit composition may receive the 2.3.1 successor seal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base22 import canonical, digest, file_sha
from .base22_bounded import BoundedError, Budget
from .base22_financial_union import _ColumnSummary
from .base22_holder_components_v2 import COMPONENT_KEYS
from .base22_sources import _tree
from .base23_delivery import PURPOSE
from .base23_native_image import KIND, audit_native_image, image_schemas
from .base23_native_keys import NativeGlobalScan
from .base231_financial_integration import native_batches
from .base231_financial_integration_audit import audit_financial_tmp
from .base231_observation_integration_audit import audit_observation_tmp

PREDECESSOR = "sha256:91845422851368028e4e8a6b83e0dc9ba2a9e30dfdd8e4d6de892f64adac1d7f"
SOURCES = {"predecessor", "financial", "event_identity", "holder", "priority"}
PRIORITY_TABLES = {"dwd_security_bar", "dwd_tradability_state", "dwd_shareholding_observation"}
PREFIXES = {
    "financial": "evidence/convergence/financial",
    "event_identity": "evidence/convergence/event-identity",
    "holder": "evidence/convergence/holder",
}


def composition_files(descriptors: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Recompute the only allowed replacement/append file set from local descriptors."""
    if set(descriptors) != SOURCES:
        raise BoundedError("CONVERGENCE_EXACT_SOURCE_SET_CHANGED")
    before = descriptors["predecessor"]["layout"]
    financial = descriptors["financial"]["layout"]
    events = descriptors["event_identity"]["layout"]
    holders = descriptors["holder"]["layout"]
    priority = descriptors["priority"]["tables"]
    result = {}
    for table, entry in before.items():
        replaced = {x["partition"] for x in events.get(table, [])}
        old = (
            []
            if table in {"dwd_financial_report", "dwd_financial_concept"}
            else [x for x in entry["files"] if x["partition"] not in replaced]
        )
        files: list[dict[str, Any]] = []
        for source, items in [
            ("predecessor", old),
            ("financial", financial.get(table, [])),
            ("event_identity", events.get(table, [])),
            ("holder", holders.get(table, [])),
            ("priority", priority.get(table, []) if table in PRIORITY_TABLES else []),
        ]:
            files.extend(
                dict(
                    table=table,
                    source_id=source,
                    **{k: x[k] for k in ["path", "partition", "rows", "size_bytes", "sha256"]},
                )
                for x in items
            )
        result[table] = sorted(files, key=lambda x: (x["partition"], x["source_id"], x["path"]))
    return result


def audit_convergence_image(root: Path, budget: Budget) -> dict[str, Any]:
    plan = json.loads((root / "evidence/input-plan.json").read_bytes())
    if plan.get("predecessor_manifest_sha256") != PREDECESSOR:
        raise BoundedError("CONVERGENCE_PREDECESSOR_IDENTITY_CHANGED")
    descriptors = {
        source: json.loads((root / "evidence/sources" / (source + ".json")).read_bytes())
        for source in plan["sources"]
    }
    expected = composition_files(descriptors)
    if set(plan["tables"]) != set(expected) or any(
        plan["tables"][table]["files"] != files for table, files in expected.items()
    ):
        raise BoundedError("CONVERGENCE_ALLOWED_FILE_COMPOSITION_CHANGED")
    for source, prefix in PREFIXES.items():
        name = "SCOPE.json" if source == "holder" else "integration-TMP.json"
        if file_sha(root / prefix / name) != plan["sources"][source]["descriptor_sha256"]:
            raise BoundedError("CONVERGENCE_CONTAINED_SCOPE_IDENTITY_CHANGED")
    native = audit_native_image(root, budget, unique_business_key_shortcut=True)
    budget.begin_stage("contained-financial-reconstruction")
    financial = audit_financial_tmp(root / PREFIXES["financial"], budget)
    budget.begin_stage("contained-event-identity-reconstruction")
    events = audit_observation_tmp(root / PREFIXES["event_identity"], budget)
    budget.begin_stage("contained-holder-physical-partition-reconstruction")
    holder = descriptors["holder"]
    hroot = root / PREFIXES["holder"]
    if (
        holder.get("kind") != "P10_BASE231_EXACT_HOLDER_COMPONENT_PHYSICAL_PARTITION_TMP_V1"
        or holder.get("identity") != digest({k: v for k, v in holder.items() if k != "identity"})
        or holder.get("complete_Base") is not False
        or holder.get("consumer_admission") != "NOT_GRANTED"
        or holder.get("flat_union_complete_ranking") is not False
        or holder.get("historical_conflicts_arbitrated") != 0
        or _tree(hroot) != set(holder["physical_members"]) | {"SCOPE.json"}
    ):
        raise BoundedError("CONVERGENCE_HOLDER_SCOPE_CHANGED")
    model = json.loads((root / "contract/dwd-domain-model.v1.json").read_bytes())
    ext = json.loads((root / "contract/dwd-domain-model.v1.1-extension.json").read_bytes())
    schema = image_schemas(model, ext)["dwd_shareholding_observation"]
    with NativeGlobalScan(budget, read_only_roots=[root]) as scan:
        scan.add("holder_original", [hroot / "inputs/holder_additions.parquet"])
        scan.add(
            "holder_partitioned",
            [hroot / x["path"] for x in holder["layout"]["dwd_shareholding_observation"]],
        )
        signatures = []
        for view in ["holder_original", "holder_partitioned"]:
            acc = _ColumnSummary(schema)
            for batch in native_batches(scan, view, schema, COMPONENT_KEYS + ["knowledge_from"]):
                acc.add(batch)
            signatures.append(acc.finish())
        if signatures != [holder["typed_summary"], holder["typed_summary"]]:
            raise BoundedError("CONVERGENCE_HOLDER_ORIGINAL_ATOMS_CHANGED")
    result = dict(
        kind="p10_base231_convergence_full_native_acceptance_v1",
        native=native,
        financial_scope_identity=financial["identity"],
        event_scope_identity=events["identity"],
        holder_scope_identity=descriptors["holder"]["identity"],
        predecessor_manifest_sha256=PREDECESSOR,
        complete_native_scan=True,
        source_roots_dereferenced=False,
        consumer_admission="NOT_GRANTED",
    )
    result["identity"] = digest(result)
    if (root / "manifest.json").exists():
        manifest = json.loads((root / "manifest.json").read_bytes())
        if (
            manifest.get("version") != "2.3.1"
            or manifest.get("convergence_scan_identity") != result["identity"]
        ):
            raise BoundedError("CONVERGENCE_SEAL_RECONSTRUCTION_CHANGED")
    return result


def seal_convergence_image(
    root: Path,
    receipt: dict[str, Any],
    *,
    code_revision: str,
) -> dict[str, Any]:
    if (root / "manifest.json").exists() or (root / "READY").exists():
        raise BoundedError("CONVERGENCE_SEALED_OUTPUT_EXISTS")
    native = receipt.get("native", {})
    if (
        receipt.get("kind") != "p10_base231_convergence_full_native_acceptance_v1"
        or receipt.get("identity") != digest({k: v for k, v in receipt.items() if k != "identity"})
        or receipt.get("predecessor_manifest_sha256") != PREDECESSOR
        or receipt.get("complete_native_scan") is not True
        or receipt.get("source_roots_dereferenced") is not False
        or not native.get("assessment", {}).get("build_eligible")
        or native.get("kind") != KIND
        or native.get("materialization_sha256") != file_sha(root / "MATERIALIZATION.json")
        or native.get("identity") != digest({k: v for k, v in native.items() if k != "identity"})
    ):
        raise BoundedError("CONVERGENCE_FULL_STRUCTURAL_RECEIPT_REQUIRED")
    manifest = dict(
        kind=KIND,
        purpose=PURPOSE,
        version="2.3.1",
        business_through="2026-09-30",
        business_timezone="Asia/Shanghai",
        materialization_sha256=native["materialization_sha256"],
        scan_identity=native["identity"],
        convergence_scan_identity=receipt["identity"],
        code_revision=code_revision,
        assessment=native["assessment"],
        summaries=native["summaries"],
        runtime={"pyarrow": native["pyarrow_version"]},
        independent_acceptance="NOT_RUN_SEPARATE_RECEIPTS_REQUIRED",
        consumer_admission="NOT_GRANTED",
    )
    manifest["content_digest"] = digest(manifest)
    (root / "manifest.json").open("xb").write(canonical(manifest))
    (root / "READY").open("x").write(file_sha(root / "manifest.json") + "\n")
    return manifest
