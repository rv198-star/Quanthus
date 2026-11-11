"""Explicit successor acceptance binding the two required local repair packages.

The frozen native 33-table/global evaluator stays unchanged. The new wrapper
replays local qualification and the full action domain before whole-image checks.
An evidence-only receipt never counts as complete image acceptance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha
from .base22_bounded import BoundedError, Budget
from .base22_sources import _tree
from .base23_native_image import audit_native_image
from .base23_required import CONTRACT_ID
from .base23_required_union import audit_required_union

KIND = "p10-base23-two-required-packages-native-acceptance-v1"
PREFIX = "evidence/required-action-union"
FIELDS = ("path", "partition", "rows", "size_bytes", "sha256")


def validate_required_binding(
    plan: dict[str, Any], scope: dict[str, Any], dependency: dict[str, Any]
) -> str:
    binding = plan.get("required_acceptance", {})
    sid = binding.get("source_id")
    source = plan.get("sources", {}).get(sid, {})
    if (
        binding.get("kind") != KIND
        or binding.get("local_prefix") != PREFIX
        or binding.get("scope_identity") != scope.get("identity")
        or binding.get("dependency_identity") != dependency.get("identity")
        or not isinstance(binding.get("replay_identity"), str)
        or not binding["replay_identity"].startswith("sha256:")
        or source.get("descriptor") != "SCOPE.json"
        or source.get("role") != "qualified_domain_output"
        or source.get("quality_gate") != "QUALIFIED_FOR_GOVERNANCE_RETENTION"
        or scope.get("contract_id") != CONTRACT_ID
        or scope.get("consumer_admission") != "NOT_GRANTED"
        or scope.get("complete_Base23") is not False
        or set(scope.get("outputs", {})) != {"dwd_corporate_action", "dwd_corporate_action_term"}
    ):
        raise BoundedError("REQUIRED_IMAGE_DOMAIN_QUALIFICATION_BINDING_CHANGED")
    for table, files in scope["outputs"].items():
        expected = [{**{k: i[k] for k in FIELDS}, "table": table, "source_id": sid} for i in files]
        entry = plan["tables"].get(table, {})
        if entry.get("source_ids") != [sid] or entry.get("files") != expected:
            raise BoundedError("REQUIRED_IMAGE_EXACT_ACTION_DOMAIN_SELECTION_CHANGED")
    for table, files in dependency["inputs"]["native"].items():
        if table in scope["outputs"]:
            continue
        actual = plan["tables"].get(table, {}).get("files", [])
        coordinates = ("partition", "rows", "size_bytes", "sha256")
        selected = actual
        if table == "dwd_tradability_state":
            selected = [
                x
                for x in actual
                if "year=2026" in x["partition"]
                and any("month=" + str(n) in x["partition"] for n in (8, 9))
            ]
        if sorted(tuple(x[k] for k in coordinates) for x in selected) != sorted(
            tuple(x[k] for k in coordinates) for x in files
        ):
            raise BoundedError("REQUIRED_IMAGE_CALENDAR_OR_DATED_LISTING_INPUT_CHANGED")
    return str(sid)


def required_local_evidence(scope_root: Path, plan: dict[str, Any]) -> dict[str, Path]:
    """Freeze physical local closure for the existing native assembler."""
    p = _physical(scope_root, "SCOPE.json", independent=True)
    scope = json.loads(p.read_bytes())
    dep = json.loads(
        _physical(scope_root, "evidence/required/SCOPE.json", independent=True).read_bytes()
    )
    sid = validate_required_binding(plan, scope, dep)
    if file_sha(p) != plan["sources"][sid]["descriptor_sha256"] or (
        scope_root / "READY"
    ).read_text().strip() != file_sha(p):
        raise BoundedError("REQUIRED_IMAGE_SOURCE_SCOPE_OR_READY_CHANGED")
    expected = {"SCOPE.json", "READY", *scope["physical_members"]}
    if _tree(scope_root) != expected:
        raise BoundedError("REQUIRED_IMAGE_LOCAL_EVIDENCE_SET_CHANGED")
    result = {}
    for rel in sorted(expected):
        member = _physical(scope_root, rel, independent=True)
        if rel in scope["physical_members"]:
            item = scope["physical_members"][rel]
            if file_sha(member) != item["sha256"] or member.stat().st_size != item["size_bytes"]:
                raise BoundedError("REQUIRED_IMAGE_LOCAL_EVIDENCE_BYTES_CHANGED")
        result[PREFIX + "/" + rel] = member
    return result


def audit_required_image(root: Path, budget: Budget) -> dict[str, Any]:
    """Local full domain replay and exact binding, then all native 33 tables."""
    plan = json.loads(_physical(root, "evidence/input-plan.json", independent=True).read_bytes())
    local = root / PREFIX
    scope = json.loads(_physical(local, "SCOPE.json", independent=True).read_bytes())
    dep = json.loads(
        _physical(local, "evidence/required/SCOPE.json", independent=True).read_bytes()
    )
    sid = validate_required_binding(plan, scope, dep)
    descriptor = _physical(root, "evidence/sources/" + sid + ".json", independent=True)
    sha = plan["sources"][sid]["descriptor_sha256"]
    if file_sha(local / "SCOPE.json") != sha or file_sha(descriptor) != sha:
        raise BoundedError("REQUIRED_IMAGE_EMBEDDED_SOURCE_DESCRIPTOR_CHANGED")
    budget.begin_stage("image-local-required-packages-replay")
    receipt = audit_required_union(local, budget)
    if receipt["identity"] != plan["required_acceptance"]["replay_identity"]:
        raise BoundedError("REQUIRED_IMAGE_RECOMPUTED_DOMAIN_RECEIPT_CHANGED")
    native = audit_native_image(root, budget)
    result = {
        "kind": KIND,
        "required_receipt": receipt,
        "native_receipt": native,
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result
