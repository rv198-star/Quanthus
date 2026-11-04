"""Portable ODS source archive for the standard paired Base build.

Every input delivery/evidence byte is retained except predecessor DWD payloads,
which the paired DWD materialization owns. Its original control/evidence bytes
remain here. Embedded previous READY markers keep their original meaning; only
the outer audit/seal grants readiness to the new ODS product.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base22_bounded import BoundedError, Budget
from .base24_ods_image import audit_ods24
from .base_build_inputs import (
    GENERIC_KINDS,
    _fingerprint,
    _tree,
    canonical,
    file_sha,
    identity,
    member,
)
from .base_build_period import VerifiedInputs, load
from .base_build_profile import validate_contract
from .base_build_windows import audit_windows, verify_window_ancestry

CONTRACT = "p10-standard-base-ods-archive-v1"


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def copy_member(
    source: Path, destination: Path, expected: dict[str, Any], check: Callable[[], None]
) -> None:
    """Copy independent bytes with before/after source and streaming hash checks."""
    before = _fingerprint(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    if destination.exists():
        raise BoundedError("BUILD_ODS_EXISTING_MEMBER_PRESERVED")
    h, count = hashlib.sha256(), 0
    with source.open("rb") as src, temporary.open("xb") as dst:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            if count + len(block) > expected["size_bytes"]:
                raise BoundedError("BUILD_ODS_SOURCE_GREW_DURING_COPY")
            dst.write(block)
            h.update(block)
            count += len(block)
            check()
        dst.flush()
        os.fsync(dst.fileno())
    if (
        count != expected["size_bytes"]
        or "sha256:" + h.hexdigest() != expected["sha256"]
        or _fingerprint(source) != before
    ):
        raise BoundedError("BUILD_ODS_SOURCE_CHANGED_DURING_COPY")
    temporary.rename(destination)


def dwd_controls(frozen: dict[str, Any], roots: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Read only the frozen original DWD descriptor to retain empty placeholders.

    A later nonempty period removes an old empty partition from the new DWD.
    Its original bytes must still survive in ODS for full predecessor closure.
    """
    result = {}
    for package in frozen["content"]["selection"]["packages"]:
        if package["role"] != "dwd":
            continue
        sid = package["id"]
        path = member(Path(roots[sid]), "MATERIALIZATION.json")
        rows = frozen["content"]["packages"][sid]["content"]["files"]
        expected = next(r for r in rows if r["path"] == "MATERIALIZATION.json")
        if file_sha(path) != expected["sha256"]:
            raise BoundedError("BUILD_ODS_PREDECESSOR_DWD_DESCRIPTOR_CHANGED")
        result[sid] = load(path)
    return result


def selected_members(
    frozen: dict[str, Any], dwd_metadata: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Projection from the original frozen closure; no filesystem discovery."""
    content = frozen["content"]
    if frozen.get("identity") != identity(content):
        raise BoundedError("BUILD_ODS_FROZEN_IDENTITY_CHANGED")
    roles = {r["id"]: r["role"] for r in content["selection"]["packages"]}
    files, sources = [], {}
    for sid, frozen_package in sorted(content["packages"].items()):
        source_content = frozen_package["content"]
        if frozen_package.get("identity") != identity(source_content):
            raise BoundedError("BUILD_ODS_FROZEN_PACKAGE_IDENTITY_CHANGED")
        metadata_only = roles.get(sid) == "dwd"
        empty_members = set()
        if metadata_only:
            declared = {r["path"]: r for r in source_content["files"]}
            for entry in dwd_metadata[sid]["layout"].values():
                for item in entry["files"]:
                    if type(item.get("rows")) is not int or item["rows"] < 0:
                        raise BoundedError("BUILD_ODS_DWD_ROW_CENSUS_REQUIRED")
                    if item["rows"] == 0:
                        row = declared.get(item["path"], {})
                        if (
                            not item["path"].startswith("payload/")
                            or row.get("sha256") != item["sha256"]
                            or row.get("size_bytes") != item["size_bytes"]
                        ):
                            raise BoundedError("BUILD_ODS_DWD_EMPTY_MEMBER_NOT_FROZEN")
                        empty_members.add(item["path"])
        prefix = "members/" + sid
        member(Path("/offline-ods"), prefix)
        sources[sid] = {
            "role": roles.get(sid, "evidence"),
            "prefix": prefix,
            "input_identity": frozen_package["identity"],
            "manifest_sha256": source_content.get("manifest_sha256"),
            "metadata_only": metadata_only,
            "excluded_payload_owner": "PAIRED_DWD" if metadata_only else None,
            "retained_empty_DWD_payload": sorted(empty_members),
        }
        for row in source_content["files"]:
            member(Path("/offline-source"), row["path"])
            if (
                metadata_only
                and row["path"].startswith("payload/")
                and row["path"] not in empty_members
            ):
                continue
            files.append(
                {
                    "path": prefix + "/" + row["path"],
                    "source_id": sid,
                    "source_relative": row["path"],
                    "sha256": row["sha256"],
                    "size_bytes": row["size_bytes"],
                }
            )
    return files, sources


def _admission_scope(
    admission: dict[str, Any], frozen: dict[str, Any], contract: dict[str, Any]
) -> None:
    selection = frozen["content"]["selection"]
    binding = admission.get("binding", {})
    predecessor = next(r for r in selection["packages"] if r["role"] == "dwd")
    expected = {
        "predecessor_manifest_sha256": predecessor["manifest_sha256"],
        "frozen_input_identity": frozen["identity"],
        "policy_identity": identity(contract),
        "predecessor_through": selection["predecessor_through"],
        "through_date": selection["through_date"],
        "target_version": selection["target_version"],
        "code_revision": binding.get("code_revision"),
    }
    if (
        binding != expected
        or not re.fullmatch(r"[0-9a-f]{40}", str(binding.get("code_revision")))
        or admission.get("identity")
        != identity({k: v for k, v in admission.items() if k != "identity"})
        or selection["target_version"] != contract["base_version"]
        or selection["predecessor_version"] != contract["predecessor_version"]
        or selection["through_date"] != contract["business_through"]
    ):
        raise BoundedError("BUILD_ODS_PERIOD_OR_CONTRACT_SCOPE_CHANGED")


def materialize_ods(
    *,
    inputs: VerifiedInputs,
    period: Path,
    windows: Path,
    contract: dict[str, Any],
    output: Path,
    budget: Budget,
) -> dict[str, Any]:
    validate_contract(contract)
    frozen = inputs.frozen
    selection = frozen["content"]["selection"]
    if (
        inputs.verified_identity != frozen["identity"]
        or selection["target_version"] != contract["base_version"]
        or selection["through_date"] != contract["business_through"]
    ):
        raise BoundedError("BUILD_ODS_INPUT_OR_CONTRACT_SCOPE_CHANGED")
    admission = load(member(period, "ADMISSION.json"))
    _admission_scope(admission, frozen, contract)
    if admission.get("source_configuration_identity") != identity(
        load(member(period, "CONFIG.json"))
    ):
        raise BoundedError("BUILD_ODS_PERIOD_CONFIGURATION_CHANGED")
    window_receipt = audit_windows(windows, budget)
    if admission.get("binding", {}).get("frozen_input_identity") != frozen["identity"]:
        raise BoundedError("BUILD_ODS_PERIOD_BINDING_CHANGED")
    window_plan = load(windows / "PLAN.json")
    if (
        window_plan["period_admission_identity"] != admission["identity"]
        or window_plan["binding"] != admission["binding"]
    ):
        raise BoundedError("BUILD_ODS_WINDOW_PERIOD_BINDING_CHANGED")
    output = budget.destination(output)
    if output.exists():
        raise BoundedError("BUILD_ODS_OUTPUT_EXISTS_PRESERVED")
    output.mkdir()
    files, sources = selected_members(frozen, dwd_controls(frozen, inputs.roots))
    # Budget's monitor keeps checking disk/RSS while the copy is blocked. Its
    # full directory census must not run once per small archive member (O(n^2)).
    # Foreground checkpoints retain immediate background errors and perform an
    # additional full check at least once a second, plus stage/end boundaries.
    next_check = 0.0

    def check() -> None:
        nonlocal next_check
        if budget.error:
            raise BoundedError(budget.error)
        now = time.monotonic()
        if now >= next_check:
            budget.check()
            next_check = time.monotonic() + 1.0

    budget.check(reserve=sum(((r["size_bytes"] + 4095) // 4096) * 4096 for r in files))
    current_source = None
    for row in files:
        if row["source_id"] != current_source:
            current_source = row["source_id"]
            budget.begin_stage("ods-copy-" + current_source)
        check()
        source = member(Path(inputs.roots[row["source_id"]]), row["source_relative"])
        copy_member(source, member(output, row["path"]), row, check)
    extras = {
        "evidence/period/ADMISSION.json": member(period, "ADMISSION.json"),
        "evidence/period/CONFIG.json": member(period, "CONFIG.json"),
        "evidence/period/security-days.parquet": member(period, "security-days.parquet"),
    }
    extras.update(
        {
            "evidence/windows/" + name: member(windows, name)
            for name in ("PLAN.json", "CHECKPOINT.json", "WINDOWS.parquet")
        }
    )
    for relative, path in sorted(extras.items()):
        row = {
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": file_sha(path),
            "source_id": "job-evidence",
            "source_relative": relative,
        }
        copy_member(path, member(output, relative), row, check)
        files.append(row)
    save(output / "evidence/frozen-inputs.json", frozen)
    save(output / "contract/base-build-delivery.v1.json", contract)
    for relative in ("evidence/frozen-inputs.json", "contract/base-build-delivery.v1.json"):
        path = member(output, relative)
        files.append(
            {
                "path": relative,
                "sha256": file_sha(path),
                "size_bytes": path.stat().st_size,
                "source_id": "job-evidence",
                "source_relative": relative,
            }
        )
    record = {
        "contract": CONTRACT,
        "input_identity": frozen["identity"],
        "period_admission_identity": admission["identity"],
        "window_identity": window_receipt["identity"],
        "sources": sources,
        "delivery_contract_identity": identity(contract),
        "base_version": contract["base_version"],
        "through_date": contract["business_through"],
        "files": sorted(files, key=lambda r: r["path"]),
        "READY": False,
    }
    record["identity"] = identity(record)
    save(output / "MATERIALIZATION.json", record)
    budget.check()
    return record


def audit_ods(
    root: Path, budget: Budget, *, expected_input_identity: str | None = None
) -> dict[str, Any]:
    """Source-denied audit using only the local archive and original child controls."""
    if not os.statvfs(root).f_flag & os.ST_RDONLY:
        raise BoundedError("BUILD_ODS_KERNEL_READONLY_REQUIRED")
    record = load(member(root, "MATERIALIZATION.json"))
    frozen = load(member(root, "evidence/frozen-inputs.json"))
    contract = load(member(root, "contract/base-build-delivery.v1.json"))
    validate_contract(contract)
    if (
        record.get("contract") != CONTRACT
        or record.get("identity") != identity({k: v for k, v in record.items() if k != "identity"})
        or record.get("input_identity") != frozen.get("identity")
        or expected_input_identity is not None
        and frozen["identity"] != expected_input_identity
        or record["delivery_contract_identity"] != identity(contract)
        or record["base_version"] != contract["base_version"]
        or record["through_date"] != contract["business_through"]
    ):
        raise BoundedError("BUILD_ODS_MATERIALIZATION_IDENTITY_CHANGED")
    archived_roots = {
        sid: str(member(root, "members/" + sid)) for sid in frozen["content"]["packages"]
    }
    required, sources = selected_members(frozen, dwd_controls(frozen, archived_roots))
    declared = {r["path"]: r for r in record["files"]}
    if len(declared) != len(record["files"]) or sources != record["sources"]:
        raise BoundedError("BUILD_ODS_MEMBER_OR_SOURCE_SET_CHANGED")
    for row in required:
        if declared.get(row["path"]) != row:
            raise BoundedError("BUILD_ODS_FROZEN_MEMBER_CLOSURE_CHANGED")
    extra_paths = {
        "evidence/period/ADMISSION.json",
        "evidence/period/CONFIG.json",
        "evidence/period/security-days.parquet",
        "evidence/windows/PLAN.json",
        "evidence/windows/CHECKPOINT.json",
        "evidence/windows/WINDOWS.parquet",
        "evidence/frozen-inputs.json",
        "contract/base-build-delivery.v1.json",
    }
    if set(declared) != {r["path"] for r in required} | extra_paths:
        raise BoundedError("BUILD_ODS_FROZEN_MEMBER_CLOSURE_CHANGED")
    sealed = (root / "READY").exists() or (root / "manifest.json").exists()
    expected_files = set(declared) | {"MATERIALIZATION.json"}
    if sealed:
        expected_files |= {"manifest.json", "READY"}
    if _tree(root) != expected_files:
        raise BoundedError("BUILD_ODS_PHYSICAL_FILE_SET_CHANGED")
    # Validate historical predecessor once with its owning auditor. It verifies
    # its own bytes and legacy ledger contract, so do not rehash those members
    # a second time in this same audit.
    predecessor_id = next(k for k, v in sources.items() if v["role"] == "ods")
    previous = member(root, sources[predecessor_id]["prefix"])
    previous_kind = load(previous / "manifest.json")["kind"]
    if previous_kind == "quanthus_ods_base24_bundle":
        previous_receipt = audit_ods24(previous, budget)
    elif previous_kind == GENERIC_KINDS["ods"]:
        previous_receipt = audit_ods(previous, budget)
    else:
        raise BoundedError("BUILD_ODS_PREDECESSOR_KIND_UNSUPPORTED")
    previous_prefix = sources[predecessor_id]["prefix"] + "/"
    for row in record["files"]:
        budget.check()
        path = member(root, row["path"])
        if path.stat().st_size != row["size_bytes"]:
            raise BoundedError("BUILD_ODS_MEMBER_BYTES_CHANGED")
        if row["path"].startswith(previous_prefix):
            continue
        if file_sha(path) != row["sha256"]:
            raise BoundedError("BUILD_ODS_MEMBER_BYTES_CHANGED")
    # The predecessor auditor checks its manifest/READY; the frozen identity
    # additionally pins that exact previous product, rather than any valid one.
    if file_sha(previous / "manifest.json") != sources[predecessor_id]["manifest_sha256"]:
        raise BoundedError("BUILD_ODS_PREDECESSOR_IDENTITY_CHANGED")
    windows = verify_window_ancestry(
        root=member(root, "evidence/windows"),
        predecessor=previous,
        period=member(root, "evidence/period"),
        budget=budget,
    )
    admission = load(member(root, "evidence/period/ADMISSION.json"))
    _admission_scope(admission, frozen, contract)
    if admission.get("source_configuration_identity") != identity(
        load(member(root, "evidence/period/CONFIG.json"))
    ):
        raise BoundedError("BUILD_ODS_PERIOD_CONFIGURATION_CHANGED")
    ledger = member(root, "evidence/period/security-days.parquet")
    if (
        record["period_admission_identity"] != admission.get("identity")
        or admission.get("identity")
        != identity({k: v for k, v in admission.items() if k != "identity"})
        or admission["ledger_sha256"] != file_sha(ledger)
        or record["window_identity"] != windows["identity"]
        or load(root / "evidence/windows/PLAN.json")["binding"] != admission["binding"]
        or load(root / "evidence/windows/PLAN.json")["period_admission_identity"]
        != admission["identity"]
    ):
        raise BoundedError("BUILD_ODS_PERIOD_OR_WINDOW_CLOSURE_CHANGED")
    receipt = {
        "contract": CONTRACT,
        "materialization_sha256": file_sha(root / "MATERIALIZATION.json"),
        "input_identity": frozen["identity"],
        "window_identity": windows["identity"],
        "period_admission_identity": admission["identity"],
        "predecessor_audit_identity": previous_receipt["identity"],
        "source_member_count": len(required),
        "member_count": len(record["files"]),
        "bytes": sum(r["size_bytes"] for r in record["files"]),
        "quality_held_added": windows["new_quality_held"],
        "paired_DWD_and_historical_dispositions": "SEPARATE_GATE_REQUIRED",
    }
    receipt["identity"] = identity(receipt)
    if sealed:
        manifest = load(root / "manifest.json")
        content = manifest.get("content", {})
        files = [{k: r[k] for k in ("path", "size_bytes", "sha256")} for r in record["files"]]
        files.append(
            {
                "path": "MATERIALIZATION.json",
                "size_bytes": (root / "MATERIALIZATION.json").stat().st_size,
                "sha256": receipt["materialization_sha256"],
            }
        )
        if (
            manifest.get("kind") != GENERIC_KINDS["ods"]
            or content.get("files") != files
            or content.get("role") != "ods"
            or content.get("base_version") != contract["base_version"]
            or content.get("through_date") != contract["business_through"]
            or manifest.get("content_digest") != identity(content)
            or content.get("audit_identity") != receipt["identity"]
            or member(root, "READY").read_text().strip() != file_sha(root / "manifest.json")
        ):
            raise BoundedError("BUILD_ODS_SEAL_CHANGED")
    return receipt
