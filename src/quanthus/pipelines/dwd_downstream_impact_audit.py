"""Read-only CN01 downstream and increment compatibility audit."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DwdDownstreamImpactAuditError(RuntimeError):
    """Raised when downstream compatibility evidence cannot be established."""


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode("ascii")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdDownstreamImpactAuditError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdDownstreamImpactAuditError(f"{label} must be a JSON object: {path}")
    return value


def _runner_result(runner: Callable[..., Any], args: Sequence[str]) -> tuple[int, str, str]:
    try:
        result = runner(
            list(args),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return 127, "", str(exc)
    return (
        int(getattr(result, "returncode", 127)),
        str(getattr(result, "stdout", "") or "").strip(),
        str(getattr(result, "stderr", "") or "").strip(),
    )


def _unit_state(unit: str, *, runner: Callable[..., Any]) -> dict[str, Any]:
    enabled_code, enabled_out, enabled_err = _runner_result(
        runner, ("systemctl", "is-enabled", unit)
    )
    active_code, active_out, active_err = _runner_result(runner, ("systemctl", "is-active", unit))
    return {
        "unit": unit,
        "enabled": enabled_out or enabled_err or ("unknown" if enabled_code else ""),
        "active": active_out or active_err or ("unknown" if active_code else ""),
        "enabled_exit_code": enabled_code,
        "active_exit_code": active_code,
    }


def _unit_inventory(*, runner: Callable[..., Any]) -> tuple[list[str], dict[str, Any] | None]:
    code, stdout, stderr = _runner_result(runner, ("systemctl", "list-unit-files", "--no-legend"))
    if code != 0:
        return [], {"reason": "systemd_inventory_unavailable", "stderr": stderr}
    units: list[str] = []
    for line in stdout.splitlines():
        name = line.split(None, 1)[0] if line.split(None, 1) else ""
        if name and ("p4" in name.lower() or "pit" in name.lower()):
            units.append(name)
    return sorted(set(units)), None


def _finding(
    *,
    finding_id: str,
    scope: str,
    severity: str,
    evidence: Mapping[str, Any],
    root_cause: str,
    affected: Mapping[str, Any],
    change: str,
    tests: Sequence[str],
    acceptance: str,
    disposition: str = "add_immutable_evidence_then_fix_in_v2_1",
) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "dimension_id": "downstream_and_increment_compatibility",
        "table_or_cross_table_scope": scope,
        "severity": severity,
        "evidence": dict(evidence),
        "root_cause": root_cause,
        "affected_rows_or_partitions": dict(affected),
        "disposition": disposition,
        "builder_or_contract_change": change,
        "tests": list(tests),
        "v2_1_acceptance_check": acceptance,
    }


def audit_dwd_downstream_impact(
    *,
    base_root: Path,
    daily_root: Path,
    dwd_tree: Path,
    current_pointer: Path,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Audit live CN01 consumers and increments without touching their state."""

    base_root = base_root.resolve()
    daily_root = daily_root.resolve()
    dwd_tree = dwd_tree.resolve()
    current_pointer = current_pointer.resolve()
    manifest_path = base_root / "manifest.json"
    manifest = _object(manifest_path, "DWD Base manifest")
    base_release = {
        "package_id": manifest.get("package_id"),
        "manifest_sha256": _sha256(manifest_path),
        "content_digest": manifest.get("content_digest"),
        "through_date": manifest.get("through_date"),
    }
    expected_base = {
        "package_id": base_release["package_id"],
        "manifest_sha256": base_release["manifest_sha256"],
        "content_digest": base_release["content_digest"],
    }
    findings: list[dict[str, Any]] = []

    daily_manifests = sorted(daily_root.glob("packages/*/*/manifest.json"))
    daily_packages: list[dict[str, Any]] = []
    daily_mismatches: list[str] = []
    for path in daily_manifests:
        value = _object(path, "daily increment manifest")
        base = value.get("base")
        compatible = isinstance(base, Mapping) and all(
            base.get(key) == expected for key, expected in expected_base.items()
        )
        if not compatible:
            daily_mismatches.append(str(path))
        daily_packages.append(
            {
                "path": str(path),
                "manifest_sha256": _sha256(path),
                "package_id": value.get("package_id"),
                "market_date": value.get("market_date"),
                "base": dict(base) if isinstance(base, Mapping) else None,
                "status": "compatible" if compatible else "mismatch",
            }
        )
    daily_check = {
        "status": "verified" if daily_manifests and not daily_mismatches else "finding",
        "expected_base": expected_base,
        "checked_package_count": len(daily_packages),
        "mismatch_count": len(daily_mismatches),
        "mismatches": daily_mismatches,
        "packages": daily_packages,
        "disposition": "retain_until_successor_rebase",
    }
    if not daily_manifests or daily_mismatches:
        findings.append(
            _finding(
                finding_id="DWDV2-COMPAT-DAILY-BASE",
                scope=str(daily_root),
                severity="high",
                evidence=daily_check,
                root_cause=(
                    "A retained Daily increment is absent or does not bind the exact "
                    "canonical DWD Base identity."
                ),
                affected={"packages": len(daily_mismatches) or (0 if daily_manifests else 1)},
                change=(
                    "Rebase or retire every incompatible Daily increment before successor "
                    "cutover; never carry V2-bound rows into V2.1."
                ),
                tests=["test_downstream_audit_rejects_daily_base_identity_mismatch"],
                acceptance=(
                    "Every retained Daily manifest verifies the approved successor Base "
                    "identity or is explicitly retired."
                ),
            )
        )

    weekly_manifests = sorted((dwd_tree / "weekly").glob("**/manifest.json"))
    weekly_check: dict[str, Any] = {
        "status": "verified" if not weekly_manifests else "finding",
        "dwd_weekly_manifest_count": len(weekly_manifests),
        "paths": [str(path) for path in weekly_manifests],
        "dwd_weekly_input_unit": _unit_state("cn01-dwd-weekly-input.timer", runner=runner),
        "upstream_ods_weekly_unit": _unit_state("cn01-ods-weekly-corrections.timer", runner=runner),
        "disposition": "disabled_until_successor_contract",
    }
    weekly_unit: dict[str, Any] = weekly_check["dwd_weekly_input_unit"]
    if weekly_manifests or weekly_unit["enabled"] == "enabled" or weekly_unit["active"] == "active":
        findings.append(
            _finding(
                finding_id="DWDV2-COMPAT-WEEKLY-INPUT",
                scope=str(dwd_tree / "weekly"),
                severity="high",
                evidence=weekly_check,
                root_cause=(
                    "A DWD weekly correction path is present or enabled before successor "
                    "semantics and Base identity are accepted."
                ),
                affected={"weekly_manifests": len(weekly_manifests)},
                change=(
                    "Keep the DWD weekly input disabled and require successor-bound weekly "
                    "manifests before reactivation."
                ),
                tests=["test_downstream_audit_blocks_enabled_weekly_dwd_input"],
                acceptance=(
                    "No DWD weekly manifest or enabled DWD weekly input remains until the "
                    "V2.1 contract is approved."
                ),
            )
        )

    pointer_check: dict[str, Any]
    pointer = _object(current_pointer, "DWD Base current pointer")
    pointer_ok = all(
        pointer.get(key) == expected
        for key, expected in {
            "package_id": base_release["package_id"],
            "manifest_sha256": base_release["manifest_sha256"],
            "content_digest": base_release["content_digest"],
        }.items()
    )
    pointer_check = {
        "status": "verified" if pointer_ok else "finding",
        "path": str(current_pointer),
        "pointer": pointer,
        "expected_base": expected_base,
        "disposition": "retain_v2_pointer_until_successor_acceptance",
    }
    if not pointer_ok:
        findings.append(
            _finding(
                finding_id="DWDV2-COMPAT-CURRENT-POINTER",
                scope=str(current_pointer),
                severity="high",
                evidence=pointer_check,
                root_cause=(
                    "The live DWD pointer no longer identifies the retained V2 predecessor "
                    "exactly."
                ),
                affected={"pointers": 1},
                change=(
                    "Restore the exact V2 pointer under compare-and-swap preconditions "
                    "before any successor review."
                ),
                tests=["test_downstream_audit_rejects_current_pointer_drift"],
                acceptance=(
                    "The current pointer remains byte-bound to the retained V2 manifest "
                    "until approved cutover."
                ),
            )
        )

    base_parent = base_root.parent
    ingress = base_parent / ".incoming-dwd-base-202607-v2.1"
    successor = base_parent / "dwd-base-202607-v2.1"
    candidate_root = dwd_tree / "candidates"
    candidate_names = (
        sorted(path.name for path in candidate_root.iterdir()) if candidate_root.is_dir() else []
    )
    candidate_check = {
        "status": "verified" if not ingress.exists() and not successor.exists() else "finding",
        "ingress_path": str(ingress),
        "ingress_present": ingress.exists(),
        "successor_path": str(successor),
        "successor_present": successor.exists(),
        "unrelated_candidate_count": len(candidate_names),
        "unrelated_candidate_names": candidate_names,
        "disposition": "retain_v2_and_ignore_unrelated_candidates",
    }
    if ingress.exists() or successor.exists():
        findings.append(
            _finding(
                finding_id="DWDV2-COMPAT-PARTIAL-SUCCESSOR",
                scope=str(base_parent),
                severity="high",
                evidence=candidate_check,
                root_cause=(
                    "A V2.1 ingress or installed successor is present before audit approval."
                ),
                affected={"paths": int(ingress.exists()) + int(successor.exists())},
                change=(
                    "Quarantine and classify successor bytes without changing V2 or any "
                    "pointer; keep the builder gate blocked."
                ),
                tests=["test_downstream_audit_rejects_partial_successor"],
                acceptance=(
                    "No V2.1 ingress or installed successor exists before explicit approval."
                ),
            )
        )

    pit_units, pit_inventory_error = _unit_inventory(runner=runner)
    pit_states = [_unit_state(unit, runner=runner) for unit in pit_units]
    active_pit = [
        item["unit"]
        for item in pit_states
        if item["enabled"] == "enabled" or item["active"] == "active"
    ]
    consumer_check = {
        "status": "verified" if not active_pit and pit_inventory_error is None else "finding",
        "historical_pit_units": pit_states,
        "active_historical_pit_units": active_pit,
        "inventory_error": pit_inventory_error,
        "disposition": "blocked_until_successor_pit_contract",
    }
    if active_pit or pit_inventory_error is not None:
        findings.append(
            _finding(
                finding_id="DWDV2-COMPAT-HISTORICAL-CONSUMER",
                scope="CN01 historical PIT consumers",
                severity="high",
                evidence=consumer_check,
                root_cause=(
                    "A historical PIT consumer is active or its runtime inventory cannot be "
                    "verified before successor acceptance."
                ),
                affected={"active_units": len(active_pit)},
                change=(
                    "Keep historical PIT consumers blocked and require explicit successor "
                    "field/availability approval before activation."
                ),
                tests=["test_downstream_audit_blocks_active_historical_pit_consumer"],
                acceptance=(
                    "No unapproved historical PIT consumer is active and the runtime "
                    "inventory is sealed."
                ),
            )
        )

    mirror_check = {
        "status": "verified",
        "oci_and_hermes_pre_rebuild_semantics": "excluded_by_cn01_authority_contract",
        "oci_sync_unit": _unit_state("oci-dwd-sync.service", runner=runner),
        "hermes_dwd_unit": _unit_state("hermes-dwd.service", runner=runner),
        "disposition": "copy_and_checksum_after_cn01_successor_acceptance",
    }
    retained_check = {
        "status": "verified",
        "legacy_dwd_tree": str(dwd_tree),
        "legacy_candidates_retained": len(candidate_names),
        "legacy_artifacts_are_authority": False,
        "disposition": "retain_without_using_as_successor_input",
    }

    checks = {
        "daily_increment_compatibility": daily_check,
        "weekly_correction_compatibility": weekly_check,
        "historical_pit_consumers": consumer_check,
        "mirror_restore_deployment": mirror_check,
        "retained_operational_artifacts": retained_check,
        "current_pointer_and_successor_retention": pointer_check | {"candidate": candidate_check},
    }
    summary = {
        "status": "verified" if not findings else "finding",
        "check_count": len(checks),
        "finding_count": len(findings),
        "reason_codes": sorted(
            {
                str(reason)
                for finding in findings
                for reason in finding.get("evidence", {}).get("reason_codes", [])
                if isinstance(reason, str)
            }
        ),
        "mutation_performed": False,
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dwd_base_v2_downstream_increment_impact_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_release": base_release,
        "execution": {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "read_only": True,
        },
        "checks": checks,
        "findings": findings,
        "summary": summary,
    }
    report["semantic_digest"] = _canonical_digest(
        {
            "kind": report["kind"],
            "base_release": base_release,
            "checks": checks,
            "findings": findings,
            "summary": summary,
        }
    )
    report["canonical_digest"] = _canonical_digest(report)
    return report


def write_downstream_impact_report(
    path: Path, report: Mapping[str, Any], *, sealed_roots: tuple[Path, ...] = ()
) -> None:
    """Write one immutable downstream report outside all sealed inputs."""

    resolved = path.resolve()
    for root in sealed_roots:
        sealed = root.resolve()
        if resolved == sealed or resolved.is_relative_to(sealed):
            raise DwdDownstreamImpactAuditError(
                f"report must remain outside sealed input: {resolved}"
            )
    if resolved.exists():
        raise DwdDownstreamImpactAuditError(f"refusing to overwrite downstream report: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.{os.getpid()}.partial")
    temporary.write_text(
        json.dumps(dict(report), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    temporary.chmod(0o640)
    temporary.replace(resolved)
