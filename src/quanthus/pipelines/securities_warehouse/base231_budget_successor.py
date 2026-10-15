"""Exact #525 qualified append; no repair, source precedence or consumer admission."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base22 import canonical, digest, file_sha
from .base22_bounded import BoundedError, Budget
from .base23_delivery import PURPOSE
from .base23_native_image import KIND, audit_native_image
from .base231_dated_distribution import replay_terms_domain
from .base231_official_holder_replay import replay_official_holder

PREDECESSOR = "sha256:4fe9240fa48ba407b3721aa178dda8a1335bd28856638037ab351a832c648afe"
PREFIX = "evidence/budget"
INPUTS = {
    "actions": "sha256:ad47ee47457d00805007416c0be13ef14ecfb5d123dc838463c9af695c9840c7",
    "holders": "sha256:0e794e74d9adf4f07074ed872ae0fb8ca4da70c73ae71463fdfe2f207cd368c1",
}
ADDITIONS = {
    "dwd_corporate_action": ("actions", 4, "effective_at_year=2026/effective_at_month=9"),
    "dwd_corporate_action_term": ("actions", 4, "effective_at_year=2026"),
    "dwd_shareholding_observation": ("holders", 20, "observation_start_year=2026"),
}
RECEIPT_KIND = "p10_base231_budget_successor_full_native_acceptance_v1"


def composition_files(descriptors: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Append only the frozen native outputs, retaining every predecessor member."""
    if set(descriptors) != {"predecessor", *INPUTS}:
        raise BoundedError("BUDGET_SUCCESSOR_EXACT_SOURCE_SET_CHANGED")
    before = descriptors["predecessor"]["layout"]
    if not set(ADDITIONS) <= set(before):
        raise BoundedError("BUDGET_SUCCESSOR_REQUIRED_TABLES_MISSING")
    result = {}
    for table, entry in before.items():
        files = [
            dict(
                table=table,
                source_id="predecessor",
                **{k: x[k] for k in ["path", "partition", "rows", "size_bytes", "sha256"]},
            )
            for x in entry["files"]
        ]
        if table in ADDITIONS:
            sid, rows, partition = ADDITIONS[table]
            path = "outputs/" + table + ".parquet"
            member = descriptors[sid]["members"][path]
            files.append(
                dict(
                    table=table,
                    source_id=sid,
                    path=path,
                    rows=rows,
                    partition=partition,
                    size_bytes=member["bytes"],
                    sha256=member["sha256"],
                )
            )
        result[table] = sorted(files, key=lambda x: (x["partition"], x["source_id"], x["path"]))
    return result


def validate_composition(root: Path) -> dict[str, Any]:
    """Validate contained pins without opening any archived source-root string."""
    plan = json.loads((root / "evidence/input-plan.json").read_bytes())
    prior = root / PREFIX / "prior-manifest.json"
    if plan.get("predecessor_manifest_sha256") != PREDECESSOR or file_sha(prior) != PREDECESSOR:
        raise BoundedError("BUDGET_SUCCESSOR_PREDECESSOR_CHANGED")
    manifest: dict[str, Any] = json.loads(prior.read_bytes())
    sources = plan["sources"]
    if set(sources) != {"predecessor", *INPUTS}:
        raise BoundedError("BUDGET_SUCCESSOR_EXACT_SOURCE_SET_CHANGED")
    expected_pins = {"predecessor": manifest["materialization_sha256"], **INPUTS}
    descriptors = {}
    for sid, expected_sha in expected_pins.items():
        p = root / "evidence/sources" / (sid + ".json")
        if sources[sid]["descriptor_sha256"] != expected_sha or file_sha(p) != expected_sha:
            raise BoundedError("BUDGET_SUCCESSOR_SOURCE_DESCRIPTOR_CHANGED")
        descriptors[sid] = json.loads(p.read_bytes())
        if sid in INPUTS and file_sha(root / PREFIX / sid / "INPUTS.json") != expected_sha:
            raise BoundedError("BUDGET_SUCCESSOR_CONTAINED_INPUT_CHANGED")
    expected = composition_files(descriptors)
    if set(plan["tables"]) != set(expected) or any(
        plan["tables"][table]["files"] != files for table, files in expected.items()
    ):
        raise BoundedError("BUDGET_SUCCESSOR_ALLOWED_COMPOSITION_CHANGED")
    for relative, sha in descriptors["predecessor"]["evidence"].items():
        if file_sha(root / PREFIX / "prior" / relative) != sha:
            raise BoundedError("BUDGET_SUCCESSOR_PRIOR_EVIDENCE_CHANGED")
    return manifest


def audit_budget_successor(root: Path, budget: Budget) -> dict[str, Any]:
    prior = validate_composition(root)
    native = audit_native_image(root, budget, unique_business_key_shortcut=True)
    for table, summary in prior["summaries"].items():
        if table not in ADDITIONS and native["summaries"][table] != summary:
            raise BoundedError("BUDGET_SUCCESSOR_UNCHANGED_TABLE_SUMMARY_CHANGED")
    budget.begin_stage("contained-budget-action-replay")
    actions = replay_terms_domain(root / PREFIX / "actions")
    budget.begin_stage("contained-budget-holder-replay")
    holders = replay_official_holder(root / PREFIX / "holders")
    if actions["rows"] != {t: n for t, (sid, n, _) in ADDITIONS.items() if sid == "actions"}:
        raise BoundedError("BUDGET_SUCCESSOR_ACTION_CENSUS_CHANGED")
    if holders["rows"] != 20 or holders["frames"] != 2:
        raise BoundedError("BUDGET_SUCCESSOR_HOLDER_CENSUS_CHANGED")
    result = dict(
        kind=RECEIPT_KIND,
        native=native,
        actions=actions,
        holders=holders,
        predecessor_manifest_sha256=PREDECESSOR,
        complete_native_scan=True,
        source_roots_dereferenced=False,
        consumer_admission="NOT_GRANTED",
    )
    result["identity"] = digest(result)
    if (root / "manifest.json").exists():
        seal = json.loads((root / "manifest.json").read_bytes())
        if seal.get("version") != "2.3.1" or seal.get("budget_scan_identity") != result["identity"]:
            raise BoundedError("BUDGET_SUCCESSOR_SEAL_RECONSTRUCTION_CHANGED")
    return result


def seal_budget_successor(
    root: Path, receipt: dict[str, Any], *, code_revision: str
) -> dict[str, Any]:
    if (root / "manifest.json").exists() or (root / "READY").exists():
        raise BoundedError("BUDGET_SUCCESSOR_SEALED_OUTPUT_EXISTS")
    native = receipt.get("native", {})
    if (
        receipt.get("kind") != RECEIPT_KIND
        or receipt.get("identity") != digest({k: v for k, v in receipt.items() if k != "identity"})
        or receipt.get("predecessor_manifest_sha256") != PREDECESSOR
        or receipt.get("complete_native_scan") is not True
        or receipt.get("source_roots_dereferenced") is not False
        or not native.get("assessment", {}).get("build_eligible")
        or native.get("kind") != KIND
        or native.get("materialization_sha256") != file_sha(root / "MATERIALIZATION.json")
        or native.get("identity") != digest({k: v for k, v in native.items() if k != "identity"})
    ):
        raise BoundedError("BUDGET_SUCCESSOR_FULL_STRUCTURAL_RECEIPT_REQUIRED")
    manifest = dict(
        kind=KIND,
        purpose=PURPOSE,
        version="2.3.1",
        business_through="2026-09-30",
        business_timezone="Asia/Shanghai",
        materialization_sha256=native["materialization_sha256"],
        scan_identity=native["identity"],
        budget_scan_identity=receipt["identity"],
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
