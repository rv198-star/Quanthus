"""Purpose-scoped warehouse reads of a sealed monthly ODS image.

The old July policy is not an admission policy for monthly images. This adapter
pins a successor contract, verifies native bytes once, and maps the ORIGINAL
source descriptions to image-local files. It never grants trading/research
admission, arbitrates revisions, rewrites the seal, or opens archived roots.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.pipelines.ods_monthly import PURPOSE, MonthlyOdsError, audit_monthly
from quanthus.pipelines.securities_warehouse.base22 import digest, file_sha
from quanthus.pipelines.securities_warehouse.base22_sources import (
    _fingerprint,
    _member,
    physical_path,
)

INPUT_PURPOSE = "WAREHOUSE_RAW_RECONSTRUCTION"
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")


class MonthlyOdsInputError(MonthlyOdsError):
    """Admission failure without raw values or credential-bearing diagnostics."""


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise MonthlyOdsInputError("MONTHLY_INPUT_JSON_UNREADABLE") from exc
    if not isinstance(value, dict):
        raise MonthlyOdsInputError("MONTHLY_INPUT_OBJECT_REQUIRED")
    return value


def _utc(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise MonthlyOdsInputError("MONTHLY_INPUT_CLOCK_INVALID") from exc
    if result.tzinfo is None:
        raise MonthlyOdsInputError("MONTHLY_INPUT_CLOCK_UNAVAILABLE")
    return result.astimezone(timezone.utc)


def _check_contract(contract: dict[str, Any], model_path: Path) -> None:
    if (
        contract.get("schema_version") != 1
        or contract.get("kind") != "ods_monthly_warehouse_input_contract"
        or contract.get("purpose") != INPUT_PURPOSE
        or contract.get("business_timezone") != "Asia/Shanghai"
        or contract.get("permitted_uses") != [INPUT_PURPOSE]
        or contract.get("execution_authorized") is not False
        or contract.get("research_admission") != "NOT_GRANTED"
        or contract.get("revision_rule") != "RETAIN_ORIGINAL_REVISIONS_NO_IMPLICIT_PRECEDENCE"
        or contract.get("model_sha256") != file_sha(physical_path(model_path))
    ):
        raise MonthlyOdsInputError("MONTHLY_INPUT_CONTRACT_CHANGED")
    image = contract.get("image", {})
    if set(image) != {
        "bundle_id",
        "manifest_sha256",
        "content_digest",
        "policy_sha256",
        "scan_identity",
        "through_date",
    } or any(not isinstance(image.get(k), str) or not image[k] for k in image):
        raise MonthlyOdsInputError("MONTHLY_INPUT_EXACT_IMAGE_REQUIRED")
    if any(
        _SHA.fullmatch(image[k]) is None
        for k in (
            "manifest_sha256",
            "content_digest",
            "policy_sha256",
            "scan_identity",
        )
    ):
        raise MonthlyOdsInputError("MONTHLY_INPUT_EXACT_IMAGE_REQUIRED")


def _envelope(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    path = _member(root, "manifest.json")
    if file_sha(path) != contract["image"]["manifest_sha256"]:
        raise MonthlyOdsInputError("MONTHLY_INPUT_IMAGE_IDENTITY_CHANGED")
    m = _object(path)
    c = m.get("content", {})
    if (
        m.get("kind") != "quanthus_ods_base_bundle"
        or m.get("schema_version") != 1
        or _member(root, "READY").read_text().strip() != file_sha(path)
        or digest(c) != m.get("content_digest")
        or m.get("bundle_id") != contract["image"]["bundle_id"]
        or m.get("content_digest") != contract["image"]["content_digest"]
        or c.get("through_date") != contract["image"]["through_date"]
        or c.get("policy_sha256") != contract["image"]["policy_sha256"]
        or c.get("scan_identity") != contract["image"]["scan_identity"]
        or c.get("governance_purpose") != PURPOSE
        or c.get("authority", {}).get("execution_authorized") is not False
        or c.get("consumer_admission") != "NOT_GRANTED"
    ):
        raise MonthlyOdsInputError("MONTHLY_INPUT_ENVELOPE_CHANGED")
    receipts = c.get("independent_acceptance", [])
    if (
        len(receipts) != 2
        or len({r.get("pid") for r in receipts}) != 2
        or any(
            r.get("identity") != c["scan_identity"]
            or r.get("source_network_denied") is not True
            or r.get("physical_copy_distinct_inodes") is not True
            or r.get("source_reads_after_probes") != 0
            or r.get("network_calls_after_probes") != 0
            or r.get("code_revision") != m.get("code_revision")
            or not isinstance(r.get("pid"), int)
            for r in receipts
        )
        or c.get("negative_probes")
        != {
            "missing": "MONTHLY_ODS_PHYSICAL_FILE_SET_CHANGED",
            "corrupt": "MONTHLY_ODS_PHYSICAL_BYTES_CHANGED",
            "restored_all_original_file_hashes": "PASS",
        }
    ):
        raise MonthlyOdsInputError("MONTHLY_INPUT_COLD_RECEIPTS_REQUIRED")
    return m


def _records(root: Path, manifest: dict[str, Any], model: dict[str, Any]) -> list[dict[str, Any]]:
    # Import lazily: the ordinary planner also offers an opt-in monthly entry.
    from quanthus.pipelines.securities_warehouse import planner

    lookups = planner._lookups(model)
    files = {f["path"]: f for f in manifest["content"]["files"]}
    records = []
    for source in manifest["content"]["source_inventory"]:
        sid = source["source_id"]
        original = _object(
            _member(root, f"payload/source-manifests/{sid}/" + source["manifest_name"])
        )
        provenance = {
            "source_id": sid,
            "source_manifest_sha256": source["manifest_sha256"],
            "source_role": source["role"],
            "source_business_date": source["business_date"],
            # Package creation is not inferred provider acquisition/PIT.
            "source_created_at": original.get("created_at", original.get("built_at")),
            "provider_acquisition_inferred": False,
        }
        if source["role"] == "historical_seed":
            items = planner._base_records(original, model)
            for item in items:
                records.append({**item, **provenance, "original_path": item["path"]})
            continue
        group = "weekly" if original["schedule"] == "weekly" else "release"
        mapping_group = "weekly_artifacts" if group == "weekly" else "release_artifacts"
        for artifact in original["artifacts"]:
            name = artifact["name"]
            mapping = lookups[group].get(name)
            selected_group = mapping_group
            if mapping is None:
                mapping = lookups["auxiliary"].get(name)
                selected_group = "release_auxiliary_artifacts"
            if mapping is None:
                raise MonthlyOdsInputError("MONTHLY_INPUT_UNCLASSIFIED_ARTIFACT:" + name)
            relative = f"payload/increments/{source['role']}/{sid}/{artifact['relative_path']}"
            physical = files.get(relative)
            if physical is None or any(
                physical[k] != artifact[k]
                for k in (
                    "sha256",
                    "size_bytes",
                )
            ):
                raise MonthlyOdsInputError("MONTHLY_INPUT_ORIGINAL_MEMBER_CHANGED")
            record = {
                "artifact": name,
                "dataset_id": name,
                "path": relative,
                "original_path": artifact["relative_path"],
                "record_type": "file",
                "format": physical["format"],
                "role": "ods_" + original["schedule"] + "_artifact",
                "sha256": artifact["sha256"],
                "size_bytes": artifact["size_bytes"],
                "knowledge_cutoff": artifact["knowledge_cutoff"],
                "source_revision": artifact["source_revision"],
                "release_id": original["release_id"],
                **planner._normalized_mapping(
                    mapping, mapping_group=selected_group, mapping_identity=name
                ),
                **provenance,
            }
            for key in ("row_count", "schema_sha256"):
                if key in physical:
                    record[key] = physical[key]
            records.append(record)
    records.sort(key=lambda x: (x["path"], x["record_type"]))
    if len({r["path"] for r in records}) != len(records):
        raise MonthlyOdsInputError("MONTHLY_INPUT_DUPLICATE_RECORD")
    return records


@dataclass(frozen=True)
class MonthlyOdsInput:
    root: Path
    identity: dict[str, Any]
    records: list[dict[str, Any]]

    def member(self, relative: str) -> Path:
        """Resolve one declared original file; never fallback or dereference old roots."""
        item = next(
            (r for r in self.records if r["record_type"] == "file" and r["path"] == relative), None
        )
        if item is None:
            raise MonthlyOdsInputError("MONTHLY_INPUT_UNDECLARED_MEMBER")
        path = _member(self.root, relative)
        if (
            path.stat().st_nlink != 1
            or path.stat().st_size != item["size_bytes"]
            or file_sha(path) != item["sha256"]
        ):
            raise MonthlyOdsInputError("MONTHLY_INPUT_MEMBER_CHANGED_AFTER_ADMISSION")
        return path


def open_monthly_input(
    *,
    root: Path,
    contract_path: Path,
    model_path: Path,
    budget: Any,
    knowledge_cutoff: str,
    purpose: str = INPUT_PURPOSE,
) -> MonthlyOdsInput:
    """Full source read admission for reconstruction, not consumer decision admission.

    Inventory/model checks precede the single expensive full native scan. Raw
    artifact cutoff is an observation constraint; unavailable row-level knowledge
    remains unavailable and never qualifies historical PIT.
    """
    if purpose != INPUT_PURPOSE:
        raise MonthlyOdsInputError("MONTHLY_INPUT_USE_NOT_ADMITTED")
    root = physical_path(root)
    contract_path, model_path = physical_path(contract_path), physical_path(model_path)
    contract_sha, model_sha = file_sha(contract_path), file_sha(model_path)
    contract = _object(contract_path)
    _check_contract(contract, model_path)
    manifest = _envelope(root, contract)
    records = _records(root, manifest, _object(model_path))
    cutoff = _utc(knowledge_cutoff)
    if any(_utc(r["knowledge_cutoff"]) > cutoff for r in records if r.get("knowledge_cutoff")):
        raise MonthlyOdsInputError("MONTHLY_INPUT_OBSERVATION_AFTER_CUTOFF")
    fingerprints = {
        f["path"]: _fingerprint(_member(root, f["path"])) for f in manifest["content"]["files"]
    }
    result = audit_monthly(
        root, budget, expected_contract_digest=contract["image"]["policy_sha256"]
    )
    if any(_fingerprint(_member(root, p)) != f for p, f in fingerprints.items()):
        raise MonthlyOdsInputError("MONTHLY_INPUT_CHANGED_DURING_ADMISSION")
    if (
        file_sha(contract_path) != contract_sha
        or file_sha(model_path) != model_sha
        or _envelope(root, contract) != manifest
    ):
        raise MonthlyOdsInputError("MONTHLY_INPUT_CHANGED_DURING_ADMISSION")
    if result["identity"] != contract["image"]["scan_identity"]:
        raise MonthlyOdsInputError("MONTHLY_INPUT_SCAN_IDENTITY_CHANGED")
    c = manifest["content"]
    identity = {
        **contract["image"],
        "kind": "ods_monthly_warehouse_input",
        "file_count": len(c["files"]),
        "verified_bytes": result["file_bytes"],
        "verification_mode": "full_native_offline",
        "source_count": len(c["source_inventory"]),
        "source_ledger_digest": digest(records),
        "consumer_contract_sha256": contract_sha,
        "model_sha256": model_sha,
        "purpose": INPUT_PURPOSE,
        "scoped_source_read_admission": "PASSED",
        "scope_gaps": c["scope_gaps"],
        "revision_rule": contract["revision_rule"],
        "knowledge_cutoff": cutoff.isoformat(),
        "row_level_PIT_qualification": "NOT_INFERRED",
        "execution_authorized": False,
        "research_admission": "NOT_GRANTED",
        "production_node_cutover": "NOT_PERFORMED",
    }
    return MonthlyOdsInput(root, identity, records)
