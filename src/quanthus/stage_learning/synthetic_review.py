"""Governed synthetic expert-panel execution over one sealed D T-only pilot artifact."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.stage_learning.reference_pilot import verify_reference_pilot_artifact
from quanthus.stage_learning.review_ledger import (
    REVIEW_SCHEMA,
    publish_resolution,
    verify_resolution_artifact,
)
from quanthus.stage_learning.synthetic_panel import (
    SyntheticPanelError,
    assignment_manifest,
    evaluate_panel,
    load_synthetic_panel,
)
from quanthus.structure_v2_inputs import canonical


class SyntheticReviewRunError(ValueError):
    """Raised when the synthetic development review cannot be executed or verified."""


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyntheticReviewRunError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise SyntheticReviewRunError(f"{label} must be a JSON object")
    return value


def _jsonl(path: Path, label: str) -> tuple[dict[str, Any], ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SyntheticReviewRunError(f"cannot read {label}: {path}") from exc
    output: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SyntheticReviewRunError(f"{label} line {number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise SyntheticReviewRunError(f"{label} line {number} must be an object")
        output.append(value)
    return tuple(output)


def _review_payload(rows: Sequence[Any]) -> bytes:
    return b"".join(
        canonical({"schema_version": REVIEW_SCHEMA, **item.to_dict()}) + b"\n" for item in rows
    )


def _dict_payload(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical(dict(item)) + b"\n" for item in rows)


def run_synthetic_review(
    *,
    pilot_artifact: Path,
    panel_config: Path,
    output_root: Path,
) -> dict[str, Any]:
    pilot_root = pilot_artifact.expanduser().resolve()
    verified_pilot = verify_reference_pilot_artifact(pilot_root)
    if verified_pilot.get("independent_reference_complete") is not False:
        raise SyntheticReviewRunError("synthetic panel requires an unresolved D engineering pilot")
    panel = load_synthetic_panel(panel_config)
    sample_manifest = _object(pilot_root / "sample-manifest.json", "D sample manifest")
    contexts = sample_manifest.get("contexts")
    if not isinstance(contexts, list) or len(contexts) != 120:
        raise SyntheticReviewRunError("synthetic panel requires the frozen 120-context D sample")
    evidence_by_context: dict[str, str] = {}
    context_ids: list[str] = []
    for item in contexts:
        if not isinstance(item, Mapping):
            raise SyntheticReviewRunError("D sample context is malformed")
        context_id = str(item.get("context_id") or "")
        evidence_digest = str(item.get("evidence_digest") or "")
        if not context_id.startswith("sha256:") or not evidence_digest.startswith("sha256:"):
            raise SyntheticReviewRunError("D sample context identity/evidence is not digest-bound")
        if context_id in evidence_by_context:
            raise SyntheticReviewRunError("D sample context identity is duplicated")
        evidence_by_context[context_id] = evidence_digest
        context_ids.append(context_id)
    packets = _jsonl(pilot_root / "review-packets.jsonl", "D review packets")
    if len(packets) != len(contexts):
        raise SyntheticReviewRunError("D review packet count differs from the frozen sample")
    try:
        evaluated = evaluate_panel(panel, packets, evidence_by_context)
    except SyntheticPanelError as exc:
        raise SyntheticReviewRunError(str(exc)) from exc

    run_id = "stage-synthetic-review-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = output_root.expanduser().resolve() / run_id
    root.mkdir(parents=True, exist_ok=False)
    primary_a_path = root / "primary-a.jsonl"
    primary_b_path = root / "primary-b.jsonl"
    adjudicator_path = root / "adjudicator.jsonl"

    payloads: dict[str, bytes] = {
        "primary-a.jsonl": _review_payload(evaluated["primary_a"]),
        "primary-b.jsonl": _review_payload(evaluated["primary_b"]),
        "audit.jsonl": _dict_payload(evaluated["audits"]),
        "institutional-challenges.jsonl": _dict_payload(evaluated["challenges"]),
        "assignment.json": canonical(assignment_manifest(panel, context_ids)),
    }
    if evaluated["adjudicator"]:
        payloads["adjudicator.jsonl"] = _review_payload(evaluated["adjudicator"])
    for name, payload in payloads.items():
        (root / name).write_bytes(payload)

    resolution = publish_resolution(
        sample_manifest=pilot_root / "sample-manifest.json",
        primary_paths=(primary_a_path, primary_b_path),
        adjudicator_path=adjudicator_path if evaluated["adjudicator"] else None,
        output_root=root / "resolution",
        evidence_tier="SYNTHETIC_EXPERT_CONSENSUS",
        panel_config=panel_config,
    )
    resolution_root = Path(str(resolution["artifact_root"]))
    verified_resolution = verify_resolution_artifact(resolution_root)
    if (
        verified_resolution.get("resolved_count") != 120
        or verified_resolution.get("evidence_tier") != "SYNTHETIC_EXPERT_CONSENSUS"
        or verified_resolution.get("human_expert_validated") is not False
    ):
        raise SyntheticReviewRunError("synthetic resolution authority differs")

    summary = {
        "schema_version": "quanthus.stage-synthetic-review-summary.v1",
        "run_id": run_id,
        "pilot_manifest_sha256": verified_pilot["manifest_sha256"],
        "panel_id": panel["panel_id"],
        "evidence_tier": "SYNTHETIC_EXPERT_CONSENSUS",
        "review_engine": evaluated["review_engine"],
        "single_model_role_isolated_policy_authoring": evaluated[
            "single_model_role_isolated_policy_authoring"
        ],
        "row_execution_deterministic": evaluated["row_execution_deterministic"],
        "context_count": 120,
        "primary_a_count": len(evaluated["primary_a"]),
        "primary_b_count": len(evaluated["primary_b"]),
        "disagreement_count": len(evaluated["disagreement_context_ids"]),
        "adjudicated_count": len(evaluated["adjudicator"]),
        "institutional_challenge_context_count": len(evaluated["challenge_context_ids"]),
        "audit_veto_count": sum(bool(item["veto"]) for item in evaluated["audits"]),
        "resolution_manifest_sha256": verified_resolution["manifest_sha256"],
        "resolution_artifact": str(resolution_root),
        "human_expert_validated": False,
        "future_settlement_read": False,
        "formal_qualification_allowed": False,
        "claim_limits": [
            "public names are style anchors only and no output is attributed to a real person",
            "persona policies were authored by one model and row execution is deterministic",
            "synthetic consensus is development reference only, not human expert validation",
            "institutional challenge review cannot override the frozen primary/adjudication policy",
        ],
    }
    summary_payload = canonical(summary)
    (root / "summary.json").write_bytes(summary_payload)
    local_files = {**payloads, "summary.json": summary_payload}
    manifest = {
        "schema_version": "quanthus.stage-synthetic-review-artifact.v1",
        "run_id": run_id,
        "pilot_manifest_sha256": verified_pilot["manifest_sha256"],
        "panel_id": panel["panel_id"],
        "files": {name: _sha256(payload) for name, payload in sorted(local_files.items())},
        "resolution_manifest_sha256": verified_resolution["manifest_sha256"],
        "evidence_tier": "SYNTHETIC_EXPERT_CONSENSUS",
        "human_expert_validated": False,
        "future_settlement_read": False,
        "formal_qualification_allowed": False,
    }
    manifest_bytes = canonical(manifest)
    (root / "manifest.json").write_bytes(manifest_bytes)
    ready = _sha256(manifest_bytes)
    (root / "READY").write_text(ready + "\n", encoding="ascii")
    return {
        **summary,
        "artifact_root": str(root),
        "manifest_sha256": ready,
    }


def verify_synthetic_review_artifact(path: Path) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "synthetic review manifest")
    if manifest.get("schema_version") != "quanthus.stage-synthetic-review-artifact.v1":
        raise SyntheticReviewRunError("synthetic review artifact schema differs")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise SyntheticReviewRunError("synthetic review READY is unreadable") from exc
    if ready != _sha256(manifest_bytes):
        raise SyntheticReviewRunError("synthetic review READY does not bind manifest")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise SyntheticReviewRunError("synthetic review file inventory is missing")
    for name, expected in files.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise SyntheticReviewRunError("synthetic review file inventory is invalid")
        try:
            observed = _sha256((root / name).read_bytes())
        except OSError as exc:
            raise SyntheticReviewRunError(f"synthetic review file is unreadable: {name}") from exc
        if observed != expected:
            raise SyntheticReviewRunError(f"synthetic review file digest differs: {name}")
    summary = _object(root / "summary.json", "synthetic review summary")
    if (
        manifest.get("evidence_tier") != "SYNTHETIC_EXPERT_CONSENSUS"
        or manifest.get("human_expert_validated") is not False
        or manifest.get("future_settlement_read") is not False
        or manifest.get("formal_qualification_allowed") is not False
        or summary.get("context_count") != 120
        or summary.get("resolution_manifest_sha256") != manifest.get(
            "resolution_manifest_sha256"
        )
    ):
        raise SyntheticReviewRunError("synthetic review authority/summary differs")
    resolution_root = Path(str(summary["resolution_artifact"]))
    resolution = verify_resolution_artifact(resolution_root)
    if resolution["manifest_sha256"] != manifest["resolution_manifest_sha256"]:
        raise SyntheticReviewRunError("synthetic review resolution digest differs")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "resolution_artifact": str(resolution_root),
        "resolution_manifest_sha256": resolution["manifest_sha256"],
        "verified": True,
        "human_expert_validated": False,
    }
