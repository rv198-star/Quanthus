"""Read-only evidence gate for representative DWD Base replay determinism."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DwdReplayDeterminismAuditError(RuntimeError):
    """Raised when replay evidence is malformed or not bound to the Base."""


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
        raise DwdReplayDeterminismAuditError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise DwdReplayDeterminismAuditError(f"{label} must be a JSON object: {path}")
    return value


def _normalized_replay(value: dict[str, Any]) -> dict[str, Any]:
    ignored = {
        "canonical_digest",
        "completed_at",
        "generated_at",
        "output_root",
        "report_path",
        "semantic_digest",
        "started_at",
    }
    return {key: item for key, item in value.items() if key not in ignored}


def audit_dwd_replay_determinism(
    *, base_root: Path, plan_root: Path, replay_a: Path | None = None, replay_b: Path | None = None
) -> dict[str, Any]:
    """Classify the existence and identity of two independently named Base reruns."""

    base_root = base_root.resolve()
    plan_root = plan_root.resolve()
    manifest = _object(base_root / "manifest.json", "DWD manifest")
    plan = _object(plan_root / "input-plan.json", "input plan")
    base_release = {
        "package_id": manifest.get("package_id"),
        "manifest_sha256": _sha256(base_root / "manifest.json"),
        "content_digest": manifest.get("content_digest"),
        "through_date": manifest.get("through_date"),
    }
    input_plan = {"run_id": plan.get("run_id"), "plan_digest": plan.get("plan_digest")}
    evidence: list[dict[str, Any]] = []
    reasons: list[str] = []
    if (replay_a is None) != (replay_b is None):
        raise DwdReplayDeterminismAuditError("replay-a and replay-b must be supplied together")
    if replay_a is None:
        reasons.append("base_replay_evidence_missing")
    else:
        assert replay_b is not None
        values: list[dict[str, Any]] = []
        resolved_paths = (replay_a.resolve(), replay_b.resolve())
        if resolved_paths[0] == resolved_paths[1]:
            reasons.append("replay_evidence_paths_not_independent")
        for path in resolved_paths:
            value = _object(path, "replay evidence")
            if value.get("kind") != "dwd_base_v2_replay_execution":
                reasons.append("replay_evidence_kind_invalid")
            if value.get("base_release") != base_release:
                reasons.append("replay_evidence_base_identity_mismatch")
            if value.get("input_plan") != input_plan:
                reasons.append("replay_evidence_input_plan_mismatch")
            if value.get("mutation_performed") is not False:
                reasons.append("replay_evidence_not_read_only")
            unsigned = dict(value)
            supplied_digest = unsigned.pop("canonical_digest", None)
            if supplied_digest != _canonical_digest(unsigned):
                reasons.append("replay_evidence_canonical_digest_invalid")
            values.append(value)
            evidence.append(
                {
                    "path": str(path),
                    "sha256": _sha256(path),
                    "canonical_digest": value.get("canonical_digest"),
                }
            )
        if not reasons and _normalized_replay(values[0]) != _normalized_replay(values[1]):
            reasons.append("replay_semantic_digest_mismatch")
    status = "verified" if not reasons else "finding"
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dwd_base_v2_replay_determinism_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_release": base_release,
        "input_plan": input_plan,
        "summary": {
            "status": status,
            "mutation_performed": False,
            "reason_codes": sorted(set(reasons)),
            "independent_execution_count": len(evidence),
        },
        "evidence": evidence,
        "contract": {
            "required_execution_count": 2,
            "required_kind": "dwd_base_v2_replay_execution",
            "byte_or_declared_semantic_digest_equality": True,
            "network_and_mutable_state_forbidden": True,
        },
    }
    report["semantic_digest"] = _canonical_digest(
        {
            "kind": report["kind"],
            "base_release": report["base_release"],
            "input_plan": report["input_plan"],
            "summary": report["summary"],
            "evidence": report["evidence"],
            "contract": report["contract"],
        }
    )
    report["canonical_digest"] = _canonical_digest(report)
    return report


def write_replay_determinism_report(
    path: Path, report: dict[str, Any], *, sealed_roots: tuple[Path, ...] = ()
) -> None:
    resolved = path.resolve()
    for root in sealed_roots:
        sealed = root.resolve()
        if resolved == sealed or resolved.is_relative_to(sealed):
            raise DwdReplayDeterminismAuditError(
                f"replay report must remain outside sealed package: {resolved}"
            )
    if resolved.exists():
        raise DwdReplayDeterminismAuditError(f"refusing to overwrite replay report: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.partial")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    temporary.chmod(0o640)
    temporary.replace(resolved)
