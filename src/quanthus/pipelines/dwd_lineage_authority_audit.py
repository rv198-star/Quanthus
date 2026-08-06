"""Read-only manifest and input-authority closure for a sealed DWD Base."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class DwdLineageAuthorityAuditError(RuntimeError):
    """Raised when exact DWD input authority cannot be proven."""


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DwdLineageAuthorityAuditError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdLineageAuthorityAuditError(f"{label} must be a JSON object: {path}")
    return value


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DwdLineageAuthorityAuditError(f"cannot hash artifact: {path}") from exc
    return "sha256:" + digest.hexdigest()


def _safe_member(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise DwdLineageAuthorityAuditError("plan manifest member path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 1 or path.name in {".", ".."}:
        raise DwdLineageAuthorityAuditError(f"plan manifest member path is unsafe: {value}")
    return value


def _verify_plan_package(plan_root: Path) -> dict[str, Any]:
    manifest_path = plan_root / "manifest.json"
    manifest = _object(manifest_path, "input-plan manifest")
    try:
        ready = (plan_root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise DwdLineageAuthorityAuditError("cannot read input-plan READY") from exc
    if ready != _sha256(manifest_path):
        raise DwdLineageAuthorityAuditError("input-plan READY does not bind manifest")
    expected = {
        "capacity-estimate.json",
        "coverage-matrix.json",
        "input-plan.json",
        "run-receipt.json",
        "source-disposition-ledger.json",
    }
    declared: set[str] = set()
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise DwdLineageAuthorityAuditError("input-plan manifest files are invalid")
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            raise DwdLineageAuthorityAuditError("input-plan manifest member is invalid")
        relative = _safe_member(raw.get("path"))
        if relative in declared:
            raise DwdLineageAuthorityAuditError("input-plan manifest has duplicate members")
        declared.add(relative)
        path = plan_root / relative
        if (
            not path.is_file()
            or path.stat().st_size != raw.get("size_bytes")
            or _sha256(path) != raw.get("sha256")
        ):
            raise DwdLineageAuthorityAuditError(
                f"input-plan member failed size or digest verification: {relative}"
            )
    if declared != expected:
        raise DwdLineageAuthorityAuditError("input-plan package inventory is incomplete")
    plan = _object(plan_root / "input-plan.json", "input plan")
    semantic_plan = plan.get("plan")
    if (
        not isinstance(semantic_plan, Mapping)
        or plan.get("plan_digest") != _canonical_digest(semantic_plan)
        or manifest.get("plan_digest") != plan.get("plan_digest")
    ):
        raise DwdLineageAuthorityAuditError("input-plan semantic digest differs")
    return plan


def _git_commit_exists(repository: Path, revision: object) -> bool:
    if not isinstance(revision, str) or len(revision) != 40:
        return False
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=repository,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _ods_authority(
    current_manifest: Mapping[str, Any], expected: Mapping[str, Any]
) -> dict[str, Any]:
    if current_manifest.get("bundle_id") == expected.get("package_id") and current_manifest.get(
        "content_digest"
    ) == expected.get("content_digest"):
        return {"status": "exact_identity", "source": dict(expected)}
    reuse = current_manifest.get("storage_reuse")
    sources = reuse.get("sources") if isinstance(reuse, Mapping) else None
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            if (
                source.get("bundle_id") == expected.get("package_id")
                and source.get("manifest_sha256") == expected.get("manifest_sha256")
                and source.get("content_digest") == expected.get("content_digest")
            ):
                return {
                    "status": "content_preserving_identity_republish",
                    "source": dict(source),
                    "current_bundle_id": current_manifest.get("bundle_id"),
                    "current_content_digest": current_manifest.get("content_digest"),
                }
    raise DwdLineageAuthorityAuditError(
        "installed ODS package does not close the DWD input identity chain"
    )


def audit_dwd_lineage_authority(
    *, dwd_root: Path, ods_root: Path, plan_root: Path, repository: Path, model_path: Path
) -> dict[str, Any]:
    """Prove manifest, plan, ODS identity, model, and code-revision closure."""

    dwd_manifest = _object(dwd_root / "manifest.json", "DWD manifest")
    ods_manifest = _object(ods_root / "manifest.json", "ODS manifest")
    plan = _verify_plan_package(plan_root)
    semantic_plan = plan["plan"]
    if not isinstance(semantic_plan, Mapping):
        raise DwdLineageAuthorityAuditError("input plan is invalid")
    expected_ods = dwd_manifest.get("ods_base")
    plan_input = semantic_plan.get("input")
    plan_model = semantic_plan.get("model")
    if not isinstance(expected_ods, Mapping) or not isinstance(plan_input, Mapping):
        raise DwdLineageAuthorityAuditError("DWD or plan ODS identity is invalid")
    if (
        dwd_manifest.get("run_id") != plan.get("run_id")
        or dwd_manifest.get("input_plan_digest") != plan.get("plan_digest")
        or expected_ods.get("package_id") != plan_input.get("bundle_id")
        or expected_ods.get("manifest_sha256") != plan_input.get("manifest_sha256")
        or expected_ods.get("content_digest") != plan_input.get("content_digest")
    ):
        raise DwdLineageAuthorityAuditError("DWD manifest and exact input plan differ")
    if (
        not isinstance(plan_model, Mapping)
        or dwd_manifest.get("model_sha256") != plan_model.get("sha256")
        or _sha256(model_path) != dwd_manifest.get("model_sha256")
    ):
        raise DwdLineageAuthorityAuditError("DWD model identity does not close")

    ledger = _object(plan_root / "source-disposition-ledger.json", "source ledger")
    coverage = _object(plan_root / "coverage-matrix.json", "coverage matrix")
    capacity = _object(plan_root / "capacity-estimate.json", "capacity estimate")
    if (
        semantic_plan.get("source_ledger_digest") != _canonical_digest(ledger)
        or semantic_plan.get("coverage_matrix_digest") != _canonical_digest(coverage)
        or semantic_plan.get("capacity_requirement_digest") != capacity.get("requirement_digest")
    ):
        raise DwdLineageAuthorityAuditError("input-plan component digest does not close")

    revisions = {
        "plan_code_revision": semantic_plan.get("code_revision"),
        "package_assembly_revision": dwd_manifest.get("code_revision"),
    }
    missing_revisions = [
        label
        for label, revision in revisions.items()
        if not _git_commit_exists(repository, revision)
    ]
    records = ledger.get("records")
    summary = ledger.get("summary")
    if not isinstance(records, list) or not isinstance(summary, Mapping):
        raise DwdLineageAuthorityAuditError("source ledger structure is invalid")
    modeled_records = [
        item
        for item in records
        if isinstance(item, Mapping) and item.get("disposition") == "modeled"
    ]
    modeled_without_targets = [
        item.get("dataset_id")
        for item in modeled_records
        if not isinstance(item.get("target_tables"), list) or not item.get("target_tables")
    ]
    findings: list[dict[str, Any]] = []
    if missing_revisions:
        findings.append({"reason": "code_revision_not_resolvable", "items": missing_revisions})
    if modeled_without_targets:
        findings.append(
            {
                "reason": "modeled_source_without_target_table",
                "count": len(modeled_without_targets),
                "sample": modeled_without_targets[:20],
            }
        )
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dwd_base_v2_lineage_authority_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "verified" if not findings else "finding",
        "dwd": {
            "package_id": dwd_manifest.get("package_id"),
            "input_plan_digest": dwd_manifest.get("input_plan_digest"),
            "model_sha256": dwd_manifest.get("model_sha256"),
        },
        "plan": {
            "run_id": plan.get("run_id"),
            "plan_digest": plan.get("plan_digest"),
            "package_verified": True,
            "source_record_count": len(records),
            "source_summary": dict(summary),
        },
        "ods_authority": _ods_authority(ods_manifest, expected_ods),
        "code_revisions": {
            label: {"revision": revision, "resolves": label not in missing_revisions}
            for label, revision in revisions.items()
        },
        "findings": findings,
        "mutation_performed": False,
    }
    report["canonical_digest"] = _canonical_digest(report)
    return report


def write_lineage_authority_report(
    path: Path, report: Mapping[str, Any], *, sealed_roots: Sequence[Path] = ()
) -> None:
    """Write one atomic report outside sealed packages."""

    resolved = path.resolve()
    for root in sealed_roots:
        sealed = root.resolve()
        if resolved == sealed or resolved.is_relative_to(sealed):
            raise DwdLineageAuthorityAuditError(
                f"audit output must remain outside sealed package: {resolved}"
            )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.partial")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    temporary.chmod(0o640)
    temporary.replace(resolved)
