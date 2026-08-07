"""Read-only CN01 rebuild, publication, and rollback readiness checks."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class DwdRebuildReadinessAuditError(RuntimeError):
    """Raised when readiness evidence cannot be read or is malformed."""


_DISPOSITIONS = {
    "fix_in_v2_1",
    "add_immutable_evidence_then_fix_in_v2_1",
    "fail_closed_exclusion",
}


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("ascii")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DwdRebuildReadinessAuditError(f"cannot hash evidence: {path}") from exc
    return "sha256:" + digest.hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdRebuildReadinessAuditError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdRebuildReadinessAuditError(f"{label} must be a JSON object: {path}")
    return value


def _sealed_object(path: Path, label: str) -> tuple[dict[str, Any], str]:
    value = _object(path, label)
    supplied = value.get("canonical_digest")
    unsigned = dict(value)
    unsigned.pop("canonical_digest", None)
    if supplied != _canonical_digest(unsigned):
        raise DwdRebuildReadinessAuditError(f"{label} canonical digest mismatch: {path}")
    return value, str(supplied)


def _mount_entries(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DwdRebuildReadinessAuditError(f"cannot read mount table: {path}") from exc
    entries: list[dict[str, Any]] = []
    for line in lines:
        fields = line.split()
        if len(fields) < 4:
            continue
        entries.append(
            {
                "source": fields[0],
                "mount_point": fields[1],
                "filesystem_type": fields[2],
                "options": fields[3].split(","),
            }
        )
    return entries


def _mount_for(path: Path, entries: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    resolved = path.resolve()
    matches: list[Mapping[str, Any]] = []
    for entry in entries:
        raw_mount = entry.get("mount_point")
        if not isinstance(raw_mount, str):
            continue
        mount = Path(raw_mount)
        try:
            if resolved == mount or resolved.is_relative_to(mount):
                matches.append(entry)
        except (OSError, RuntimeError):
            continue
    if not matches:
        return None
    return dict(max(matches, key=lambda item: len(str(item.get("mount_point", "")))))


def _free_bytes(path: Path, statvfs_fn: Callable[[str], Any]) -> int:
    try:
        stats = statvfs_fn(str(path))
    except OSError as exc:
        raise DwdRebuildReadinessAuditError(f"cannot stat filesystem: {path}") from exc
    return int(stats.f_bavail) * int(stats.f_frsize)


def _finding(
    *,
    finding_id: str,
    scope: str,
    evidence: Mapping[str, Any],
    root_cause: str,
    affected: Mapping[str, Any],
    disposition: str,
    change: str,
    acceptance: str,
    test: str,
) -> dict[str, Any]:
    if disposition not in _DISPOSITIONS:
        raise DwdRebuildReadinessAuditError(f"invalid readiness disposition: {disposition}")
    return {
        "finding_id": finding_id,
        "dimension_id": "rebuild_publication_and_rollback_readiness",
        "table_or_cross_table_scope": scope,
        "severity": "high",
        "evidence": dict(evidence),
        "root_cause": root_cause,
        "affected_rows_or_partitions": dict(affected),
        "disposition": disposition,
        "builder_or_contract_change": change,
        "tests": [test],
        "v2_1_acceptance_check": acceptance,
    }


def _check(status: str, *, evidence: Mapping[str, Any], reason_codes: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "status": status,
        "reason_codes": sorted(set(reason_codes)),
        "evidence": dict(evidence),
    }


def audit_dwd_rebuild_readiness(
    *,
    repository: Path,
    base_root: Path,
    plan_root: Path,
    workspace_root: Path,
    coordination_root: Path,
    lock_path: Path,
    current_pointer: Path,
    ingress_path: Path,
    installed_successor: Path,
    closure_root: Path | None = None,
    pilot_report: Path | None = None,
    policy_path: Path | None = None,
    mounts_path: Path = Path("/proc/mounts"),
    statvfs_fn: Callable[[str], Any] = os.statvfs,
) -> dict[str, Any]:
    """Audit runtime topology against the frozen CN01 rebuild contract."""

    repository = repository.resolve()
    base_root = base_root.resolve()
    plan_root = plan_root.resolve()
    workspace_root = workspace_root.resolve()
    coordination_root = coordination_root.resolve()
    lock_path = lock_path.resolve()
    current_pointer = current_pointer.resolve()
    ingress_path = ingress_path.resolve()
    installed_successor = installed_successor.resolve()
    if policy_path is None:
        policy_path = repository / "config" / "audits" / "ods-pit-sufficiency.v1.json"
    policy_path = policy_path.resolve()
    policy = _object(policy_path, "CN01 topology policy")
    topology = policy.get("cn01_rebuild_execution_topology")
    if not isinstance(topology, Mapping):
        raise DwdRebuildReadinessAuditError("topology policy has no CN01 contract")
    local = topology.get("local_state")
    nfs = topology.get("nfs")
    publication = topology.get("publication")
    limits = topology.get("execution_limits")
    if not all(isinstance(item, Mapping) for item in (local, nfs, publication, limits)):
        raise DwdRebuildReadinessAuditError("topology policy has incomplete sections")
    plan_capacity = _object(plan_root / "capacity-estimate.json", "capacity estimate")
    manifest = _object(base_root / "manifest.json", "DWD Base manifest")
    manifest_sha = _sha256(base_root / "manifest.json")
    base_release = {
        "package_id": manifest.get("package_id"),
        "manifest_sha256": manifest_sha,
        "content_digest": manifest.get("content_digest"),
        "through_date": manifest.get("through_date"),
    }
    entries = _mount_entries(mounts_path)
    workspace_mount = _mount_for(workspace_root, entries)
    local_mount = _mount_for(base_root.parent, entries)
    checks: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []

    expected_nfs_options = set(str(item) for item in nfs.get("required_options", []))
    observed_options = set(str(item) for item in (workspace_mount or {}).get("options", []))
    nfs_free = _free_bytes(workspace_root, statvfs_fn)
    nfs_reasons: list[str] = []
    if not workspace_mount or workspace_mount.get("mount_point") != nfs.get("mount_point"):
        nfs_reasons.append("nfs_mount_point_mismatch")
    if (workspace_mount or {}).get("filesystem_type") != nfs.get("filesystem_type"):
        nfs_reasons.append("nfs_filesystem_type_mismatch")
    nfs_reasons.extend(f"nfs_option_missing:{item}" for item in sorted(expected_nfs_options - observed_options))
    if nfs_free < int(nfs.get("minimum_free_bytes", 0)):
        nfs_reasons.append("nfs_capacity_below_floor")
    checks["nfs_mount_and_capacity"] = _check(
        "verified" if not nfs_reasons else "finding",
        evidence={
            "mount": workspace_mount,
            "free_bytes": nfs_free,
            "minimum_free_bytes": nfs.get("minimum_free_bytes"),
            "workspace_root": str(workspace_root),
            "policy_probe_evidence": {
                key: nfs.get(key)
                for key in ("write_probe", "atomic_rename_probe", "same_mount_hardlink_probe", "cn01_local_flock_probe")
            },
        },
        reason_codes=nfs_reasons,
    )
    if nfs_reasons:
        findings.append(
            _finding(
                finding_id="DWDV2-READINESS-NFS",
                scope=str(workspace_root),
                evidence=checks["nfs_mount_and_capacity"],
                root_cause="The mandatory CN01 NFS workspace does not satisfy the frozen mount, option, or capacity contract.",
                affected={"free_bytes": nfs_free},
                disposition="add_immutable_evidence_then_fix_in_v2_1",
                change="Restore the exact /personal NFS mount and capacity floor, then capture fresh read-only preflight evidence before any builder starts.",
                acceptance="A fresh CN01 readiness report verifies the exact NFS mount/options, probes, and free-space floor.",
                test="test_rebuild_readiness_rejects_nfs_contract_drift",
            )
        )

    required_local_free = int(
        plan_capacity.get("requirement", {}).get("estimated_final_dwd_bytes", 0)
    ) + int(local.get("install_safety_margin_bytes", 0))
    local_free = _free_bytes(base_root.parent, statvfs_fn)
    local_capacity_reasons = []
    if local_free < required_local_free:
        local_capacity_reasons.append("local_install_capacity_below_candidate_plus_margin")
    checks["local_install_capacity"] = _check(
        "verified" if not local_capacity_reasons else "finding",
        evidence={
            "base_root": str(base_root),
            "filesystem": local_mount,
            "free_bytes": local_free,
            "estimated_final_dwd_bytes": plan_capacity.get("requirement", {}).get("estimated_final_dwd_bytes"),
            "safety_margin_bytes": local.get("install_safety_margin_bytes"),
            "required_free_bytes": required_local_free,
        },
        reason_codes=local_capacity_reasons,
    )
    if local_capacity_reasons:
        findings.append(
            _finding(
                finding_id="DWDV2-READINESS-LOCAL-CAPACITY",
                scope=str(base_root.parent),
                evidence=checks["local_install_capacity"],
                root_cause="CN01 local storage cannot satisfy the manifest-sized successor installation gate plus its safety margin.",
                affected={"free_bytes": local_free, "required_free_bytes": required_local_free},
                disposition="add_immutable_evidence_then_fix_in_v2_1",
                change="Reserve local installation capacity using the sealed successor manifest size plus the policy margin; do not use local storage for NFS candidates or spill.",
                acceptance="A fresh readiness receipt proves local free bytes are at least the sealed V2.1 candidate size plus the required margin.",
                test="test_rebuild_readiness_rejects_insufficient_local_install_capacity",
            )
        )

    lock_reasons: list[str] = []
    if not coordination_root.is_dir():
        lock_reasons.append("coordination_root_missing")
    if not lock_path.parent.is_dir():
        lock_reasons.append("lock_parent_missing")
    if (local_mount or {}).get("filesystem_type") == "nfs":
        lock_reasons.append("lock_authority_on_nfs")
    checks["single_writer_lock"] = _check(
        "verified" if not lock_reasons else "finding",
        evidence={
            "single_writer_policy": topology.get("single_writer"),
            "coordination_root": str(coordination_root),
            "lock_path": str(lock_path),
            "coordination_root_exists": coordination_root.is_dir(),
            "lock_parent_exists": lock_path.parent.is_dir(),
            "filesystem": local_mount,
            "lock_file_present": lock_path.exists(),
        },
        reason_codes=lock_reasons,
    )
    if lock_reasons:
        findings.append(
            _finding(
                finding_id="DWDV2-READINESS-SINGLE-WRITER",
                scope=str(coordination_root),
                evidence=checks["single_writer_lock"],
                root_cause="The CN01-local coordination root or authoritative single-writer lock is not ready, or is placed on the wrong filesystem.",
                affected={"paths": len(lock_reasons)},
                disposition="add_immutable_evidence_then_fix_in_v2_1",
                change="Create and preflight the CN01-local coordination root and lock under the frozen path; never move lock authority to NFS.",
                acceptance="A fresh readiness report proves the local lock parent exists, is not NFS, and the lock can be acquired without a second writer.",
                test="test_rebuild_readiness_requires_local_single_writer_lock",
            )
        )

    pilot_reasons: list[str] = []
    pilot_evidence: dict[str, Any] = {"path": str(pilot_report) if pilot_report else None}
    if pilot_report is None:
        pilot_reasons.append("partition_pilot_evidence_missing")
    else:
        pilot_value, pilot_digest = _sealed_object(pilot_report.resolve(), "partition pilot report")
        pilot_evidence.update({"digest": pilot_digest, "kind": pilot_value.get("kind"), "status": pilot_value.get("status")})
        if pilot_value.get("kind") != "dwd_base_v2_partition_pilot":
            pilot_reasons.append("partition_pilot_kind_invalid")
        if pilot_value.get("status") != "verified":
            pilot_reasons.append("partition_pilot_not_verified")
        if pilot_value.get("mutation_performed") is not False:
            pilot_reasons.append("partition_pilot_not_read_only")
    checks["partition_pilot_and_spill"] = _check(
        "verified" if not pilot_reasons else "finding",
        evidence=pilot_evidence,
        reason_codes=pilot_reasons,
    )
    if pilot_reasons:
        findings.append(
            _finding(
                finding_id="DWDV2-READINESS-PARTITION-PILOT",
                scope="CN01 sequential domain builder limits",
                evidence=checks["partition_pilot_and_spill"],
                root_cause="No verified partition pilot establishes the per-builder memory limit and NFS spill bound before a full rebuild.",
                affected={"pilot_reports": 0 if pilot_report is None else 1},
                disposition="add_immutable_evidence_then_fix_in_v2_1",
                change="Run one bounded, read-only representative partition pilot per heavy builder on NFS and freeze memory/spill limits before full execution.",
                acceptance="A sealed pilot report verifies sequential execution, explicit memory limit, NFS spill path, and no local-scratch fallback.",
                test="test_rebuild_readiness_requires_partition_pilot_evidence",
            )
        )

    expected_manifest = str(publication.get("expected_current_manifest_sha256"))
    pointer_value = _object(current_pointer, "BASE_CURRENT pointer")
    pointer_reasons: list[str] = []
    if pointer_value.get("manifest_sha256") != expected_manifest:
        pointer_reasons.append("current_pointer_predecessor_mismatch")
    if pointer_value.get("package_id") != base_release.get("package_id"):
        pointer_reasons.append("current_pointer_package_mismatch")
    if manifest_sha != expected_manifest:
        pointer_reasons.append("sealed_v2_manifest_mismatch")
    if publication.get("pointer_update") != "compare_and_swap":
        pointer_reasons.append("pointer_update_contract_not_cas")
    checks["cas_pointer_and_v2_identity"] = _check(
        "verified" if not pointer_reasons else "finding",
        evidence={
            "pointer": pointer_value,
            "pointer_path": str(current_pointer),
            "expected_manifest_sha256": expected_manifest,
            "sealed_v2_manifest_sha256": manifest_sha,
            "pointer_update": publication.get("pointer_update"),
        },
        reason_codes=pointer_reasons,
    )
    if pointer_reasons:
        findings.append(
            _finding(
                finding_id="DWDV2-READINESS-CAS-POINTER",
                scope=str(current_pointer),
                evidence=checks["cas_pointer_and_v2_identity"],
                root_cause="The current Base pointer or its cutover contract is not bound to the exact retained V2 manifest with compare-and-swap semantics.",
                affected={"pointer_errors": len(pointer_reasons)},
                disposition="fix_in_v2_1",
                change="Keep V2 as the exact CAS predecessor and require a SHA-preconditioned pointer update only after local deep verification and approval.",
                acceptance="The readiness report verifies the current pointer, V2 manifest, and compare-and-swap predecessor identity byte-for-byte.",
                test="test_rebuild_readiness_rejects_pointer_precondition_drift",
            )
        )

    candidate_reasons: list[str] = []
    if ingress_path.exists():
        candidate_reasons.append("hidden_ingress_already_present")
    if installed_successor.exists():
        candidate_reasons.append("successor_package_already_present")
    checks["candidate_and_rollback_retention"] = _check(
        "verified" if not candidate_reasons else "finding",
        evidence={
            "v2_root": str(base_root),
            "v2_manifest_sha256": manifest_sha,
            "v2_ready_present": (base_root / "READY").is_file(),
            "ingress_path": str(ingress_path),
            "ingress_present": ingress_path.exists(),
            "installed_successor": str(installed_successor),
            "installed_successor_present": installed_successor.exists(),
            "rollback_contract": publication.get("rollback"),
        },
        reason_codes=candidate_reasons,
    )
    if candidate_reasons:
        findings.append(
            _finding(
                finding_id="DWDV2-READINESS-PARTIAL-CANDIDATE",
                scope=str(ingress_path.parent),
                evidence=checks["candidate_and_rollback_retention"],
                root_cause="A successor ingress or installed package already exists before the audit and approval gates are closed.",
                affected={"paths": len(candidate_reasons)},
                disposition="fail_closed_exclusion",
                change="Quarantine and classify the pre-existing successor bytes without changing V2 or any pointer; require human disposition before any rebuild.",
                acceptance="Readiness finds no unapproved ingress or successor package, while the immutable V2 package remains retained and rollback-verifiable.",
                test="test_rebuild_readiness_rejects_partial_successor_presence",
            )
        )

    closure_reasons: list[str] = []
    closure_evidence: dict[str, Any] = {"root": str(closure_root) if closure_root else None}
    if closure_root is None:
        closure_reasons.append("audit_closure_missing")
    else:
        ledger, ledger_digest = _sealed_object(closure_root / "findings-ledger.json", "findings ledger")
        scope, scope_digest = _sealed_object(closure_root / "v2.1-remediation-scope.json", "remediation scope")
        contract, contract_digest = _sealed_object(closure_root / "v2.1-build-contract.json", "build contract")
        closure_evidence.update(
            {
                "ledger_digest": ledger_digest,
                "scope_digest": scope_digest,
                "contract_digest": contract_digest,
                "builder_gate": ledger.get("builder_gate"),
                "finding_count": ledger.get("summary", {}).get("finding_count"),
                "invalid_disposition_count": ledger.get("summary", {}).get("invalid_disposition_count"),
                "scope_source_ledger_digest": scope.get("source_ledger_digest"),
                "contract_finding_ledger_digest": contract.get("finding_ledger_digest"),
                "contract_remediation_scope_digest": contract.get("remediation_scope_digest"),
            }
        )
        if ledger.get("builder_gate") != "blocked" or contract.get("status") != "not_authorized":
            closure_reasons.append("audit_closure_gate_not_blocked")
        if ledger.get("summary", {}).get("invalid_disposition_count") != 0:
            closure_reasons.append("audit_closure_has_invalid_dispositions")
        if scope.get("source_ledger_digest") != ledger_digest:
            closure_reasons.append("remediation_scope_not_bound_to_ledger")
        if contract.get("finding_ledger_digest") != ledger_digest or contract.get("remediation_scope_digest") != scope_digest:
            closure_reasons.append("build_contract_not_bound_to_closure")
    checks["audit_closure_binding"] = _check(
        "verified" if not closure_reasons else "finding",
        evidence=closure_evidence,
        reason_codes=closure_reasons,
    )
    if closure_reasons:
        findings.append(
            _finding(
                finding_id="DWDV2-READINESS-CLOSURE-BINDING",
                scope="CN01 pre-rebuild audit closure",
                evidence=checks["audit_closure_binding"],
                root_cause="The single immutable findings/remediation/build-contract packet is missing or its digests are not mutually bound.",
                affected={"closure_errors": len(closure_reasons)},
                disposition="add_immutable_evidence_then_fix_in_v2_1",
                change="Regenerate one immutable closure packet after all audit dimensions are complete, then bind its exact digests into the V2.1 plan.",
                acceptance="The readiness report verifies all three closure artifacts, zero invalid dispositions, and exact cross-digest bindings with builder_gate blocked until approval.",
                test="test_rebuild_readiness_requires_bound_audit_closure",
            )
        )

    reason_codes = sorted({reason for check in checks.values() for reason in check["reason_codes"]})
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dwd_base_v2_rebuild_publication_rollback_readiness_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_release": base_release,
        "policy": {"path": str(policy_path), "sha256": _sha256(policy_path)},
        "summary": {
            "status": "verified" if not findings else "finding",
            "check_count": len(checks),
            "finding_count": len(findings),
            "reason_codes": reason_codes,
            "mutation_performed": False,
        },
        "checks": checks,
        "findings": findings,
        "contract": {
            "execution_node": "CN01",
            "candidate_build_started": False,
            "pointer_changed": False,
            "v2_retained": True,
            "nfs_only_candidate_and_spill": True,
            "single_writer_required": True,
        },
    }
    report["semantic_digest"] = _canonical_digest(
        {
            "kind": report["kind"],
            "base_release": report["base_release"],
            "policy": report["policy"],
            "summary": report["summary"],
            "checks": report["checks"],
            "findings": report["findings"],
            "contract": report["contract"],
        }
    )
    report["canonical_digest"] = _canonical_digest(report)
    return report


def write_readiness_report(
    path: Path, report: Mapping[str, Any], *, sealed_roots: Sequence[Path] = ()
) -> None:
    resolved = path.resolve()
    for root in sealed_roots:
        sealed = root.resolve()
        if resolved == sealed or resolved.is_relative_to(sealed):
            raise DwdRebuildReadinessAuditError(
                f"readiness report must remain outside sealed input: {resolved}"
            )
    if resolved.exists():
        raise DwdRebuildReadinessAuditError(f"refusing to overwrite readiness report: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.partial")
    temporary.write_text(
        json.dumps(dict(report), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    temporary.chmod(0o640)
    temporary.replace(resolved)
