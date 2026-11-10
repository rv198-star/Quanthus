"""Bind the qualified action union to a full native image's offline acceptance.

The frozen native reader and domain evaluator stay byte-identical. This explicit
successor acceptance mode replays image-local repair evidence before the native
33-table scan. It grants no consumer admission and never resolves archived roots.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .base22 import _physical, digest, file_sha
from .base22_bounded import BoundedError, Budget
from .base22_sources import _tree
from .base23_action_merge import CONTRACT_ID
from .base23_action_scope import audit_action_scope
from .base23_native_image import audit_native_image

KIND = "p10-base23-repaired-native-acceptance-v1"
PREFIX = "evidence/qualified-action-union"
FIELDS = ("path", "partition", "rows", "size_bytes", "sha256")


def validate_repair_binding(plan: dict[str, Any], scope: dict[str, Any]) -> str:
    """Reject incomplete replacement, requalification or parent substitution."""
    binding = plan.get("repair_acceptance", {})
    sid = binding.get("source_id")
    source = plan.get("sources", {}).get(sid, {})
    if (
        binding.get("kind") != KIND
        or binding.get("local_prefix") != PREFIX
        or binding.get("scope_identity") != scope.get("identity")
        or not isinstance(binding.get("replay_identity"), str)
        or not binding["replay_identity"].startswith("sha256:")
        or source.get("descriptor") != "SCOPE.json"
        or source.get("role") != "qualified_domain_output"
        or source.get("quality_gate") != "QUALIFIED_FOR_GOVERNANCE_RETENTION"
        or scope.get("contract_id") != CONTRACT_ID
        or scope.get("consumer_admission") != "NOT_GRANTED"
        or scope.get("complete_Base23") is not False
        or set(scope.get("outputs", {}))
        != {"dwd_corporate_action", "dwd_corporate_action_term"}
    ):
        raise BoundedError("REPAIRED_IMAGE_QUALIFICATION_BINDING_CHANGED")
    for table, outputs in scope["outputs"].items():
        entry = plan["tables"].get(table, {})
        expected = [
            {**{k: item[k] for k in FIELDS}, "source_id": sid, "table": table}
            for item in outputs
        ]
        if entry.get("source_ids") != [sid] or entry.get("files") != expected:
            raise BoundedError("REPAIRED_IMAGE_DOMAIN_SELECTION_CHANGED")
    for table, inputs in scope.get("parents", {}).items():
        actual = plan["tables"].get(table, {}).get("files", [])
        # Old path names are archival only; final native paths may be renumbered.
        coordinates = ("partition", "rows", "size_bytes", "sha256")
        if ([tuple(i[k] for k in coordinates) for i in actual]
                != [tuple(i[k] for k in coordinates) for i in inputs]):
            raise BoundedError("REPAIRED_IMAGE_PARENT_ENTITIES_CHANGED")
    if set(scope.get("parents", {})) != {"dwd_security", "dwd_listed_company"}:
        raise BoundedError("REPAIRED_IMAGE_PARENT_CLOSURE_MISSING")
    return str(sid)


def repair_local_evidence(scope_root: Path, plan: dict[str, Any]) -> dict[str, Path]:
    """Freeze every local repair file before full assembly; never write anything."""
    p = _physical(scope_root, "SCOPE.json", independent=True)
    scope = json.loads(p.read_bytes())
    sid = validate_repair_binding(plan, scope)
    sha = file_sha(p)
    if (sha != plan["sources"][sid]["descriptor_sha256"]
            or _physical(scope_root, "READY", independent=True).read_text().strip() != sha):
        raise BoundedError("REPAIRED_IMAGE_SCOPE_OR_READY_CHANGED")
    names = {"SCOPE.json", "READY", *scope["physical_members"]}
    if _tree(scope_root) != names:
        raise BoundedError("REPAIRED_IMAGE_LOCAL_REPAIR_SET_CHANGED")
    result = {}
    for name in sorted(names):
        member = _physical(scope_root, name, independent=True)
        if name in scope["physical_members"]:
            frozen = scope["physical_members"][name]
            if (member.stat().st_size != frozen["size_bytes"]
                    or file_sha(member) != frozen["sha256"]):
                raise BoundedError("REPAIRED_IMAGE_LOCAL_REPAIR_BYTES_CHANGED")
        result[PREFIX + "/" + name] = member
    return result


def audit_repair_evidence(root: Path, budget: Budget) -> dict[str, Any]:
    """Replay the image-local domain and its exact plan binding, without old roots.

    This evidence-only entry point is not whole-image acceptance. A full run must
    call audit_repaired_image, which also performs the native global/row scan.
    """
    plan = json.loads(_physical(root, "evidence/input-plan.json", independent=True).read_bytes())
    local = root / PREFIX
    scope_path = _physical(local, "SCOPE.json", independent=True)
    scope = json.loads(scope_path.read_bytes())
    sid = validate_repair_binding(plan, scope)
    descriptor = _physical(root, "evidence/sources/" + sid + ".json", independent=True)
    sha = plan["sources"][sid]["descriptor_sha256"]
    if file_sha(scope_path) != sha or file_sha(descriptor) != sha:
        raise BoundedError("REPAIRED_IMAGE_EMBEDDED_SCOPE_CHANGED")
    budget.begin_stage("image-local-action-qualification-replay")
    receipt = audit_action_scope(local, budget)
    if receipt["identity"] != plan["repair_acceptance"]["replay_identity"]:
        raise BoundedError("REPAIRED_IMAGE_RECOMPUTED_REPLAY_CHANGED")
    return receipt


def audit_repaired_image(root: Path, budget: Budget) -> dict[str, Any]:
    """Explicit full successor acceptance: local raw replay then all 33 tables."""
    repair = audit_repair_evidence(root, budget)
    native = audit_native_image(root, budget)
    result = {
        "kind": KIND,
        "native_receipt": native,
        "repair_receipt": repair,
        "consumer_admission": "NOT_GRANTED",
    }
    result["identity"] = digest(result)
    return result


def copy_owned_member_then_retire(
    source: Path, destination: Path, *, sha256: str, size_bytes: int,
    journal: Any, check: Any,
) -> dict[str, Any]:
    """Independent physical copy before retiring an exact successful owned TMP.

    The caller registers the complete unsealed job-owned relocation and retains
    its successful acceptance receipt. This primitive is not source-data GC.
    A durable new file and journal entry precede unlink; a copy failure leaves the
    source intact. It creates neither a hardlink nor a rename-based pseudo-copy.
    """
    from .base22_sources import _fingerprint, physical_path

    source = physical_path(source)
    destination = physical_path(destination)
    if destination.exists() or source == destination:
        raise BoundedError("REPAIRED_RELOCATION_DESTINATION_EXISTS")
    before = _fingerprint(source)
    if (source.stat().st_nlink != 1 or before[2] != size_bytes
            or file_sha(source) != sha256):
        raise BoundedError("REPAIRED_RELOCATION_SOURCE_CHANGED")
    check(size_bytes)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        copied = 0
        while data := src.read(1024 * 1024):
            copied += len(data)
            if copied > size_bytes:
                raise BoundedError("REPAIRED_RELOCATION_SOURCE_GREW")
            dst.write(data)
            check(0)
        dst.flush()
        os.fsync(dst.fileno())
    new = destination.stat()
    if (copied != size_bytes or new.st_nlink != 1
            or (new.st_dev, new.st_ino) == before[:2]
            or file_sha(destination) != sha256
            or _fingerprint(source) != before):
        raise BoundedError("REPAIRED_RELOCATION_COPY_CHANGED")
    fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    entry = {"source": str(source), "destination": str(destination), "sha256": sha256,
             "size_bytes": size_bytes, "old_device_inode": list(before[:2]),
             "new_device_inode": [new.st_dev, new.st_ino]}
    journal.write(json.dumps(entry, sort_keys=True) + "\n")
    journal.flush()
    os.fsync(journal.fileno())
    source.unlink()
    return entry
