"""Paired publication primitives: immutable producer receipts and guarded markers.

The controller must obtain a fresh isolated audit for every incomplete recovery.
An AUDIT file from a prior attempt is evidence, never publication authority.
Only top-level manifests/READY are added after the owning stage images move.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base23_native_image import native_manifest
from .base_build_inputs import (
    GENERIC_KINDS,
    _tree,
    canonical,
    file_sha,
    identity,
    member,
)
from .base_build_period import load
from .base_build_profile import profile, validate_contract
from .base_build_quality import POLICY, inherited_cases
from .base_build_stages import KIND as STAGE_KIND
from .base_build_stages import _save_new

CONTRACT = "p10-standard-base-paired-publication-v1"
DUAL_REVISION_CONTRACT = "p10-standard-base-paired-publication-v2"


def portable_audit_identity(receipt: dict[str, Any]) -> str:
    # Operational fingerprints and scratch-byte metrics are not portable data.
    return identity(
        {
            k: v
            for k, v in receipt.items()
            if k not in {"identity", "physical_guard", "source_replay_bytes"}
        }
    )


def product_inventory(root: Path, role: str) -> list[dict[str, Any]]:
    record = load(member(root, "MATERIALIZATION.json"))
    if role == "ods":
        files = [{k: r[k] for k in ("path", "sha256", "size_bytes")} for r in record["files"]]
    elif role == "dwd":
        files = [
            {k: r[k] for k in ("path", "sha256", "size_bytes")}
            for entry in record["layout"].values()
            for r in entry["files"]
        ]
        files.extend(
            {"path": p, "sha256": sha, "size_bytes": member(root, p).stat().st_size}
            for p, sha in record["evidence"].items()
        )
        files.sort(key=lambda r: r["path"])
    else:
        raise BoundedError("BUILD_PUBLICATION_ROLE_INVALID")
    path = member(root, "MATERIALIZATION.json")
    files.append(
        {
            "path": "MATERIALIZATION.json",
            "sha256": file_sha(path),
            "size_bytes": path.stat().st_size,
        }
    )
    if len({r["path"] for r in files}) != len(files):
        raise BoundedError("BUILD_PUBLICATION_MEMBER_COLLISION")
    return files


def verify_publication_bytes(
    root: Path, role: str, expected_materialization: str
) -> dict[str, int]:
    """Fresh complete readback at the mutation boundary; no fingerprint shortcut."""
    if file_sha(member(root, "MATERIALIZATION.json")) != expected_materialization:
        raise BoundedError("BUILD_PUBLICATION_MATERIALIZATION_CHANGED")
    files = product_inventory(root, role)
    physical = _tree(root) - {"manifest.json", "READY"}
    if physical != {r["path"] for r in files}:
        raise BoundedError("BUILD_PUBLICATION_PHYSICAL_NAMESPACE_CHANGED")
    for row in files:
        path = member(root, row["path"])
        if path.stat().st_size != row["size_bytes"] or file_sha(path) != row["sha256"]:
            raise BoundedError("BUILD_PUBLICATION_FRESH_BYTE_READBACK_FAILED")
    return {"files": len(files), "bytes": sum(r["size_bytes"] for r in files)}


def _require_audit(
    receipt: dict[str, Any],
    contract: dict[str, Any],
    revision: str,
    *,
    materialization_revision: str | None = None,
) -> None:
    validate_contract(contract)
    binding = receipt.get("binding", {})
    historical = receipt.get("historical_gate", {})
    if (
        receipt.get("contract") != "p10-standard-base-pair-audit-v1"
        or receipt.get("identity")
        != identity({k: v for k, v in receipt.items() if k != "identity"})
        or binding.get("code_revision") != (materialization_revision or revision)
        or binding.get("policy_identity") != identity(contract)
        or binding.get("target_version") != contract["base_version"]
        or binding.get("through_date") != contract["business_through"]
        or binding.get("frozen_input_identity") != receipt.get("input_identity")
        or receipt.get("source_replay") != "PASS_ALL_PERIOD_SOURCES_AND_TYPED_DELTA"
        or receipt.get("replay_identity") != receipt.get("ODS", {}).get("period_admission_identity")
        or receipt.get("ODS", {}).get("quality_held_added") != 0
        or receipt.get("DWD", {}).get("assessment", {}).get("build_eligible") is not True
        or receipt.get("DWD", {}).get("global_keys_versions_references_lineage") != "PASS"
        or len(receipt.get("closure", {}).get("table_counts", {})) != 33
        or receipt.get("publication_eligible") is not True
        or historical.get("publication_eligible") is not True
        or historical.get("identity")
        != identity({k: v for k, v in historical.items() if k != "identity"})
        or historical.get("facts_or_grades_changed") is not False
        or historical.get("held_added") != 0
    ):
        raise BoundedError("BUILD_PUBLICATION_FULL_SOURCE_AND_QUALITY_AUDIT_REQUIRED")
    for role, hash_function in (("ODS", identity), ("DWD", digest)):
        layer = receipt.get(role, {})
        if layer.get("identity") != hash_function(
            {k: v for k, v in layer.items() if k != "identity"}
        ) or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(layer.get("materialization_sha256"))):
            raise BoundedError("BUILD_PUBLICATION_LAYER_AUDIT_CHANGED")
    if receipt["ODS"].get("input_identity") != receipt["input_identity"]:
        raise BoundedError("BUILD_PUBLICATION_INPUT_BINDING_CHANGED")
    cases = inherited_cases({"pairing": {"historical_gate": historical}})
    if any(
        c["scope_registered"] and c["status"] == "PENDING_GOVERNED_DISPOSITION"
        for c in cases.values()
    ) or historical.get("policy_sha256") != file_sha(POLICY):
        raise BoundedError("BUILD_PUBLICATION_HISTORICAL_REGISTRATION_UNRESOLVED")


def pair_gate(
    receipt: dict[str, Any],
    *,
    revision: str,
    materialization_revision: str | None = None,
) -> dict[str, Any]:
    """Publication gate shared by both revisions of the pairing contract."""
    materialization_revision = materialization_revision or revision
    if receipt["binding"]["code_revision"] != materialization_revision:
        raise BoundedError("BUILD_PUBLICATION_MATERIALIZATION_REVISION_CHANGED")
    gate = {
        "contract": DUAL_REVISION_CONTRACT if revision != materialization_revision else CONTRACT,
        "input_identity": receipt["input_identity"],
        "binding": receipt["binding"],
        **(
            {
                "audit_revision": revision,
                "materialization_revision": materialization_revision,
            }
            if revision != materialization_revision
            else {}
        ),
        "audit_content_identity": portable_audit_identity(receipt),
        "materializations": {
            role: receipt[role.upper()]["materialization_sha256"] for role in ("ods", "dwd")
        },
        "historical_gate": receipt["historical_gate"],
        "source_replay_identity": receipt["replay_identity"],
        "quality_held_added": 0,
        "consumer_admission": "NOT_GRANTED",
    }
    gate["identity"] = identity(gate)
    return gate


def require_gate_revisions(gate: dict[str, Any], manifest_revision: Any) -> str:
    """Single and dual gates each pin the audit and materialization revisions."""
    materialization_revision = gate["binding"]["code_revision"]
    if gate.get("contract") == DUAL_REVISION_CONTRACT:
        if (
            gate.get("audit_revision") != manifest_revision
            or gate.get("materialization_revision") != materialization_revision
            or manifest_revision == materialization_revision
        ):
            raise BoundedError("BUILD_PUBLICATION_DUAL_REVISION_BINDING_CHANGED")
    elif (
        manifest_revision != materialization_revision
        or "audit_revision" in gate
        or "materialization_revision" in gate
    ):
        raise BoundedError("BUILD_PUBLICATION_SINGLE_REVISION_BINDING_CHANGED")
    return materialization_revision


def pair_documents(
    *,
    ods: Path,
    dwd: Path,
    receipt: dict[str, Any],
    contract: dict[str, Any],
    revision: str,
    materialization_revision: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Internal controller operation after a fresh worker, not a public seal bypass."""
    _require_audit(receipt, contract, revision, materialization_revision=materialization_revision)
    materialization_revision = materialization_revision or revision
    if receipt["binding"]["code_revision"] != materialization_revision:
        raise BoundedError("BUILD_PUBLICATION_MATERIALIZATION_REVISION_CHANGED")
    for role, root in (("ods", ods), ("dwd", dwd)):
        if (root / "manifest.json").exists() or (root / "READY").exists():
            raise BoundedError("BUILD_PUBLICATION_EXISTING_MARKERS_REQUIRE_RECOVERY")
        verify_publication_bytes(root, role, receipt[role.upper()]["materialization_sha256"])
    gate = pair_gate(receipt, revision=revision, materialization_revision=materialization_revision)
    ods_content = {
        "role": "ods",
        "base_version": contract["base_version"],
        "through_date": contract["business_through"],
        "files": product_inventory(ods, "ods"),
        "audit_identity": receipt["ODS"]["identity"],
    }
    ods_manifest = {
        "kind": GENERIC_KINDS["ods"],
        "content": ods_content,
        "content_digest": identity(ods_content),
        "code_revision": revision,
        "pairing": gate,
        "paired_audit": receipt,
        "consumer_admission": "NOT_GRANTED",
    }
    p = profile(
        contract,
        content={
            "role": "dwd",
            "base_version": contract["base_version"],
            "through_date": contract["business_through"],
            "files": product_inventory(dwd, "dwd"),
        },
    )
    p = replace(
        p,
        manifest_fields=(
            *p.manifest_fields,
            ("pairing", gate),
            ("paired_audit", receipt),
        ),
    )
    dwd_manifest = native_manifest(dwd, receipt["DWD"], code_revision=revision, profile=p)
    return {"ods": ods_manifest, "dwd": dwd_manifest}


def verify_pair_registration(ods: Path, dwd: Path) -> dict[str, Any]:
    """Check two complete generic layers and their mutual materialization binding.

    This is registration/byte authority only, not another source or quality scan.
    """
    manifests = {
        role: load(member(root, "manifest.json")) for role, root in (("ods", ods), ("dwd", dwd))
    }
    gate = manifests["ods"].get("pairing", {})
    if (
        gate.get("contract") not in {CONTRACT, DUAL_REVISION_CONTRACT}
        or gate.get("identity") != identity({k: v for k, v in gate.items() if k != "identity"})
        or gate != manifests["dwd"].get("pairing")
        or gate.get("quality_held_added") != 0
        or gate.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise BoundedError("BUILD_PUBLICATION_PAIR_REGISTRATION_CHANGED")
    contract = load(member(dwd, "contract/base-build-delivery.v1.json"))
    shared_proof = manifests["ods"].get("paired_audit", {})
    audit_revision = manifests["ods"].get("code_revision")
    materialization_revision = require_gate_revisions(gate, audit_revision)
    _require_audit(
        shared_proof,
        contract,
        audit_revision,
        materialization_revision=materialization_revision,
    )
    if (
        gate.get("binding") != shared_proof["binding"]
        or gate.get("input_identity") != shared_proof["input_identity"]
        or gate.get("historical_gate") != shared_proof["historical_gate"]
        or gate.get("source_replay_identity") != shared_proof["replay_identity"]
    ):
        raise BoundedError("BUILD_PUBLICATION_PAIR_PROOF_BINDING_CHANGED")
    for role, root in (("ods", ods), ("dwd", dwd)):
        manifest = manifests[role]
        content = manifest.get("content", {})
        proof = manifest.get("paired_audit", {})
        if (
            manifest.get("kind") != GENERIC_KINDS[role]
            or content.get("role") != role
            or content.get("base_version") != gate["binding"]["target_version"]
            or content.get("through_date") != gate["binding"]["through_date"]
            or file_sha(member(root, "MATERIALIZATION.json")) != gate["materializations"][role]
            or member(root, "READY").read_text().strip() != file_sha(root / "manifest.json")
            or proof.get("identity")
            != identity({k: v for k, v in proof.items() if k != "identity"})
            or proof != shared_proof
            or manifest.get("code_revision") != audit_revision
            or content.get("files") != product_inventory(root, role)
            or gate["materializations"][role] != proof[role.upper()]["materialization_sha256"]
            or portable_audit_identity(proof) != gate["audit_content_identity"]
        ):
            raise BoundedError("BUILD_PUBLICATION_LAYER_REGISTRATION_CHANGED")
        if role == "ods":
            valid = (
                manifest.get("content_digest") == identity(content)
                and content.get("audit_identity") == proof["ODS"]["identity"]
            )
        else:
            native = proof["DWD"]
            valid = (
                manifest.get("content_digest")
                == digest({k: v for k, v in manifest.items() if k != "content_digest"})
                and manifest.get("materialization_sha256") == native["materialization_sha256"]
                and manifest.get("scan_identity") == native["identity"]
                and manifest.get("summaries") == native["summaries"]
                and manifest.get("assessment") == native["assessment"]
            )
        if not valid:
            raise BoundedError("BUILD_PUBLICATION_MANIFEST_PROOF_CHANGED")
    return gate


def verify_pair_seal(ods: Path, dwd: Path, fresh_audit: dict[str, Any]) -> None:
    gate = verify_pair_registration(ods, dwd)
    _require_audit(
        fresh_audit,
        load(member(dwd, "contract/base-build-delivery.v1.json")),
        gate.get("audit_revision", gate["binding"]["code_revision"]),
        materialization_revision=gate["binding"]["code_revision"],
    )
    if (
        portable_audit_identity(fresh_audit) != gate["audit_content_identity"]
        or fresh_audit["input_identity"] != gate["input_identity"]
    ):
        raise BoundedError("BUILD_PUBLICATION_RECOMPUTED_PAIRED_AUDIT_CHANGED")


def producer_record(output: Path, role: str) -> dict[str, Any]:
    """Pin a completed immutable producer attempt before moving its layer root."""
    root = member(output, role.upper())
    receipt_path = member(output.parent, "SUCCESS.json")
    receipt = load(receipt_path)
    if (
        receipt.get("kind") != STAGE_KIND
        or receipt.get("stage") != role
        or receipt.get("identity")
        != identity({k: v for k, v in receipt.items() if k != "identity"})
        or receipt.get("output_identity") != identity(receipt["content"])
        or (output.parent / "FAILURE.json").exists()
    ):
        raise BoundedError("BUILD_PUBLICATION_PRODUCER_RECEIPT_CHANGED")
    prefix = role.upper() + "/"
    members = [
        {**r, "path": r["path"][len(prefix) :]}
        for r in receipt["content"]["files"]
        if r["path"].startswith(prefix)
    ]
    if (
        not members
        or len(members) != len(receipt["content"]["files"])
        or _tree(root) != {r["path"] for r in members}
    ):
        raise BoundedError("BUILD_PUBLICATION_PRODUCER_INVENTORY_CHANGED")
    for row in members:
        path = member(root, row["path"])
        if path.stat().st_size != row["size_bytes"]:
            raise BoundedError("BUILD_PUBLICATION_PRODUCER_SIZE_CHANGED")
    return {
        "stage_receipt_sha256": file_sha(receipt_path),
        "stage_receipt_identity": receipt["identity"],
        "output_identity": receipt["output_identity"],
        "stage_identity": receipt["stage_identity"],
        "source": str(root),
        "materialization_sha256": file_sha(root / "MATERIALIZATION.json"),
        "files": members,
    }


def begin_transfer(work: Path, outputs: dict[str, Path], *, owner_identity: str) -> dict[str, Any]:
    path = work / "PUBLICATION.json"
    if path.exists():
        intent = load(path)
        if (
            intent.get("contract") != CONTRACT
            or intent.get("owner_identity") != owner_identity
            or intent.get("identity")
            != identity({k: v for k, v in intent.items() if k != "identity"})
        ):
            raise BoundedError("BUILD_PUBLICATION_OWNERSHIP_CONFLICT_PRESERVED")
        return intent
    published = work / "published"
    if published.exists():
        raise BoundedError("BUILD_PUBLICATION_UNKNOWN_TARGET_PRESERVED")
    producers = {role: producer_record(outputs[role], role) for role in ("ods", "dwd")}
    if any(work not in Path(p["source"]).parents for p in producers.values()):
        raise BoundedError("BUILD_PUBLICATION_SOURCE_OUTSIDE_OWNED_JOB")
    intent = {
        "contract": CONTRACT,
        "owner_identity": owner_identity,
        "producers": producers,
        "target": str(published),
        "lifecycle": "TMP_OWNERSHIP_TRANSFER",
    }
    intent["identity"] = identity(intent)
    _save_new(path, intent)
    return intent


def resume_transfer(work: Path, intent: dict[str, Any]) -> dict[str, Path]:
    if load(member(work, "PUBLICATION.json")) != intent or intent.get("identity") != identity(
        {k: v for k, v in intent.items() if k != "identity"}
    ):
        raise BoundedError("BUILD_PUBLICATION_TRANSFER_INTENT_CHANGED")
    published = work / "published"
    if intent["target"] != str(published):
        raise BoundedError("BUILD_PUBLICATION_TARGET_OUTSIDE_OWNED_JOB")
    published.mkdir(exist_ok=True)
    result = {}
    for role in ("ods", "dwd"):
        producer = intent["producers"][role]
        source, target = Path(producer["source"]), published / role.upper()
        if work not in source.parents or source == target:
            raise BoundedError("BUILD_PUBLICATION_SOURCE_OUTSIDE_OWNED_JOB")
        if source.exists() == target.exists():
            raise BoundedError("BUILD_PUBLICATION_TRANSFER_LOCATION_CONFLICT")
        if (
            file_sha(member(source.parent.parent, "SUCCESS.json"))
            != producer["stage_receipt_sha256"]
        ):
            raise BoundedError("BUILD_PUBLICATION_PRODUCER_RECEIPT_CHANGED")
        current = source if source.exists() else target
        # Even an interrupted transfer retains the exact original producer bytes.
        if _tree(current) - {"manifest.json", "READY"} != {r["path"] for r in producer["files"]}:
            raise BoundedError("BUILD_PUBLICATION_TRANSFER_NAMESPACE_CHANGED")
        for row in producer["files"]:
            path = member(current, row["path"])
            if path.stat().st_size != row["size_bytes"]:
                raise BoundedError("BUILD_PUBLICATION_TRANSFER_SIZE_CHANGED")
        if file_sha(current / "MATERIALIZATION.json") != producer["materialization_sha256"]:
            raise BoundedError("BUILD_PUBLICATION_TRANSFER_MATERIALIZATION_CHANGED")
        if current == source:
            if source.stat().st_dev != published.stat().st_dev:
                raise BoundedError("BUILD_PUBLICATION_SAME_FILESYSTEM_RENAME_REQUIRED")
            os.rename(source, target)
        result[role] = target
    return result


def control_bytes(value: Any) -> bytes:
    return canonical(value) + b"\n"


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _prepared(value: dict[str, Any]) -> None:
    if (
        set(value) != {"contract", "documents", "manifest_hashes", "identity"}
        or value.get("contract") != CONTRACT
        or value.get("identity") != identity({k: v for k, v in value.items() if k != "identity"})
        or set(value["documents"]) != {"ods", "dwd"}
        or value["manifest_hashes"]
        != {
            role: "sha256:" + hashlib.sha256(control_bytes(doc)).hexdigest()
            for role, doc in value["documents"].items()
        }
    ):
        raise BoundedError("BUILD_PUBLICATION_PREPARED_MARKERS_CHANGED")


def prepare_markers(work: Path, documents: dict[str, Any], *, attempt: str) -> dict[str, Any]:
    if not re.fullmatch(r"attempt-[0-9]{6}", attempt) or set(documents) != {
        "ods",
        "dwd",
    }:
        raise BoundedError("BUILD_PUBLICATION_MARKER_ATTEMPT_INVALID")
    directory = member(work, "publication-writes/" + attempt)
    directory.mkdir(parents=True, exist_ok=False)
    prepared = {
        "contract": CONTRACT,
        "documents": documents,
        "manifest_hashes": {
            role: "sha256:" + hashlib.sha256(control_bytes(doc)).hexdigest()
            for role, doc in documents.items()
        },
    }
    prepared["identity"] = identity(prepared)
    _save_new(directory / "PREPARED.json", prepared)
    return prepared


def write_markers(
    work: Path, products: dict[str, Path], prepared: dict[str, Any], *, attempt: str
) -> None:
    _prepared(prepared)
    directory = member(work, "publication-writes/" + attempt)
    if load(member(directory, "PREPARED.json")) != prepared:
        raise BoundedError("BUILD_PUBLICATION_PREPARED_MARKERS_CHANGED")
    # Fully write on the same owned filesystem, then publish without overwrite.
    for name in ("manifest.json", "READY"):
        for role in ("ods", "dwd"):
            target = products[role] / name
            temporary = directory / (role + "-" + name + ".writing")
            data = (
                control_bytes(prepared["documents"][role])
                if name == "manifest.json"
                else (prepared["manifest_hashes"][role] + "\n").encode("ascii")
            )
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target)
            temporary.unlink()
            _sync_directory(target.parent)
            _sync_directory(directory)


def recover_owned_markers(work: Path, products: dict[str, Path]) -> bool:
    """Archive incomplete owned control bytes, then require another fresh audit.

    Return True only when both complete READY/manifests can be freshly verified.
    Unknown or altered final markers are preserved as a conflict.
    """
    writes = work / "publication-writes"
    if not writes.exists():
        if any(
            (root / n).exists() for root in products.values() for n in ("manifest.json", "READY")
        ):
            raise BoundedError("BUILD_PUBLICATION_UNKNOWN_MARKERS_PRESERVED")
        return False
    attempts = sorted(writes.iterdir())
    expected = None
    owning_directory = None
    for directory in attempts:
        if not re.fullmatch(r"attempt-[0-9]{6}", directory.name):
            raise BoundedError("BUILD_PUBLICATION_UNKNOWN_ATTEMPT_PRESERVED")
        prepared_path = member(directory, "PREPARED.json")
        partial = directory / ".PREPARED.json.partial"
        if not prepared_path.exists():
            # A killed prepare has never been allowed to touch product markers.
            # Retain it; another uniquely numbered attempt may prepare anew.
            if any(p.name != partial.name for p in directory.iterdir()):
                raise BoundedError("BUILD_PUBLICATION_UNPREPARED_WRITES_PRESERVED")
            continue
        if partial.exists():
            if not os.path.samefile(partial, prepared_path):
                raise BoundedError("BUILD_PUBLICATION_PREPARED_MARKERS_CHANGED")
            partial.unlink()
            _sync_directory(directory)
        prepared = load(prepared_path)
        _prepared(prepared)
        expected = prepared
        owning_directory = directory
        for role in ("ods", "dwd"):
            for name in ("manifest.json", "READY"):
                writing = directory / (role + "-" + name + ".writing")
                final = products[role] / name
                if writing.exists():
                    archive = directory / (role + "-" + name + ".interrupted")
                    if final.exists() and os.path.samefile(writing, final):
                        # An interrupted no-overwrite link retains its bytes in
                        # final. Unlink the redundant name before normal hashing,
                        # which rightly rejects multiply-linked inputs.
                        wanted = (
                            control_bytes(prepared["documents"][role])
                            if name == "manifest.json"
                            else (prepared["manifest_hashes"][role] + "\n").encode("ascii")
                        )
                        if writing.read_bytes() != wanted:
                            raise BoundedError("BUILD_PUBLICATION_ALTERED_MARKER_PRESERVED")
                        writing.unlink()
                    else:
                        if archive.exists():
                            raise BoundedError("BUILD_PUBLICATION_ARCHIVE_CONFLICT_PRESERVED")
                        # Atomic rename retains even a partial write without a
                        # second interrupted-copy state or linked archive.
                        os.rename(writing, archive)
                    _sync_directory(directory)
    present = any(
        (root / n).exists() for root in products.values() for n in ("manifest.json", "READY")
    )
    if expected is None:
        if present:
            raise BoundedError("BUILD_PUBLICATION_PREPARED_MARKERS_REQUIRED")
        return False
    for role, root in products.items():
        for name in ("manifest.json", "READY"):
            final = member(root, name)
            wanted = (
                control_bytes(expected["documents"][role])
                if name == "manifest.json"
                else (expected["manifest_hashes"][role] + "\n").encode("ascii")
            )
            if final.exists() and final.read_bytes() != wanted:
                raise BoundedError("BUILD_PUBLICATION_ALTERED_MARKER_PRESERVED")
    complete = all(
        (root / n).exists() for root in products.values() for n in ("manifest.json", "READY")
    )
    if complete:
        return True
    archive = owning_directory / "archived-final-markers"
    archive.mkdir(exist_ok=True)
    for role, root in products.items():
        for name in ("READY", "manifest.json"):
            path = root / name
            if path.exists():
                target = archive / (role + "-" + name)
                if target.exists():
                    raise BoundedError("BUILD_PUBLICATION_ARCHIVE_CONFLICT_PRESERVED")
                os.rename(path, target)
                _sync_directory(root)
                _sync_directory(archive)
    return False
