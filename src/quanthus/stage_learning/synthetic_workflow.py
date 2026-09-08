"""End-to-end development-only D synthetic-panel -> E R0/R1 workflow receipt."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quanthus.stage_learning.learning_runner import (
    run_development_learning,
    verify_learning_artifact,
)
from quanthus.stage_learning.reference_pilot import verify_reference_pilot_artifact
from quanthus.stage_learning.synthetic_review import (
    run_synthetic_review,
    verify_synthetic_review_artifact,
)
from quanthus.structure_v2_inputs import canonical


class SyntheticWorkflowError(ValueError):
    """Raised when the bounded synthetic development workflow cannot close."""


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyntheticWorkflowError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise SyntheticWorkflowError(f"{label} must be a JSON object")
    return value


def run_synthetic_workflow(
    *,
    pilot_artifact: Path,
    panel_config: Path,
    training_root: Path,
    output_root: Path,
    code_revision: str,
) -> dict[str, Any]:
    pilot = verify_reference_pilot_artifact(pilot_artifact, code_revision=code_revision)
    if pilot.get("independent_reference_complete") is not False:
        raise SyntheticWorkflowError("synthetic workflow expects an unresolved D pilot")

    review = run_synthetic_review(
        pilot_artifact=pilot_artifact,
        panel_config=panel_config,
        output_root=output_root / "synthetic-review",
    )
    verified_review = verify_synthetic_review_artifact(Path(review["artifact_root"]))
    if verified_review.get("human_expert_validated") is not False:
        raise SyntheticWorkflowError("synthetic review unexpectedly claims human validation")

    learning = run_development_learning(
        pilot_artifact=pilot_artifact,
        resolution_artifact=Path(verified_review["resolution_artifact"]),
        training_root=training_root,
        output_root=output_root / "development-learning",
        code_revision=code_revision,
    )
    verified_learning = verify_learning_artifact(
        Path(str(learning["run_root"])),
        code_revision=code_revision,
    )
    if verified_learning.get("verified") is not True:
        raise SyntheticWorkflowError("E development learning artifact verification failed")

    run_id = "stage-synthetic-workflow-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = output_root.expanduser().resolve() / run_id
    root.mkdir(parents=True, exist_ok=False)
    review_summary = _object(
        Path(review["artifact_root"]) / "summary.json",
        "synthetic review summary",
    )
    learning_summary = _object(Path(learning["run_root"]) / "summary.json", "learning summary")
    summary = {
        "schema_version": "quanthus.stage-synthetic-workflow-summary.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "pilot_manifest_sha256": pilot["manifest_sha256"],
        "synthetic_review_manifest_sha256": verified_review["manifest_sha256"],
        "resolution_manifest_sha256": verified_review["resolution_manifest_sha256"],
        "learning_manifest_sha256": verified_learning["manifest_sha256"],
        "reference_evidence_tier": "SYNTHETIC_EXPERT_CONSENSUS",
        "human_expert_validated": False,
        "context_count": review_summary["context_count"],
        "primary_disagreement_count": review_summary["disagreement_count"],
        "institutional_challenge_context_count": review_summary[
            "institutional_challenge_context_count"
        ],
        "r1_trial_count": learning_summary["r1_trial_count"],
        "development_selection_policy_id": learning_summary[
            "development_selection_policy_id"
        ],
        "development_selection_disposition": learning_summary[
            "development_selection_disposition"
        ],
        "development_eligible_trial_count": learning_summary[
            "development_eligible_trial_count"
        ],
        "development_rank_1_trial_id": learning_summary["development_rank_1_trial_id"],
        "development_rank_1_score": learning_summary["development_rank_1_score"],
        "future_settlement_read_by_semantic_review": False,
        "future_settlement_read_by_learning": False,
        "arbitration_read": False,
        "permanent_blind_read": False,
        "formal_qualification_authorized": False,
        "claim_limits": [
            "synthetic consensus is a development reference, not human expert validation",
            "a development rank is emitted only after all development hard gates pass",
            "F held-out qualification remains separately gated",
        ],
    }
    summary_bytes = canonical(summary)
    (root / "summary.json").write_bytes(summary_bytes)
    manifest = {
        "schema_version": "quanthus.stage-synthetic-workflow-artifact.v1",
        "run_id": run_id,
        "code_revision": code_revision,
        "pilot_manifest_sha256": pilot["manifest_sha256"],
        "synthetic_review_manifest_sha256": verified_review["manifest_sha256"],
        "resolution_manifest_sha256": verified_review["resolution_manifest_sha256"],
        "learning_manifest_sha256": verified_learning["manifest_sha256"],
        "files": {"summary.json": _sha256(summary_bytes)},
        "reference_evidence_tier": "SYNTHETIC_EXPERT_CONSENSUS",
        "human_expert_validated": False,
        "formal_qualification_authorized": False,
    }
    manifest_bytes = canonical(manifest)
    (root / "manifest.json").write_bytes(manifest_bytes)
    ready = _sha256(manifest_bytes)
    (root / "READY").write_text(ready + "\n", encoding="ascii")
    return {**summary, "artifact_root": str(root), "manifest_sha256": ready}


def verify_synthetic_workflow_artifact(path: Path) -> dict[str, Any]:
    root = path.expanduser().resolve()
    manifest = _object(root / "manifest.json", "synthetic workflow manifest")
    if manifest.get("schema_version") != "quanthus.stage-synthetic-workflow-artifact.v1":
        raise SyntheticWorkflowError("synthetic workflow artifact schema differs")
    manifest_bytes = canonical(manifest)
    try:
        ready = (root / "READY").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise SyntheticWorkflowError("synthetic workflow READY is unreadable") from exc
    if ready != _sha256(manifest_bytes):
        raise SyntheticWorkflowError("synthetic workflow READY does not bind manifest")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or files.get("summary.json") != _sha256(
        (root / "summary.json").read_bytes()
    ):
        raise SyntheticWorkflowError("synthetic workflow summary digest differs")
    summary = _object(root / "summary.json", "synthetic workflow summary")
    if (
        manifest.get("reference_evidence_tier") != "SYNTHETIC_EXPERT_CONSENSUS"
        or manifest.get("human_expert_validated") is not False
        or manifest.get("formal_qualification_authorized") is not False
        or summary.get("formal_qualification_authorized") is not False
        or summary.get("future_settlement_read_by_semantic_review") is not False
        or summary.get("future_settlement_read_by_learning") is not False
    ):
        raise SyntheticWorkflowError("synthetic workflow authority differs")
    return {
        "run_id": manifest["run_id"],
        "manifest_sha256": ready,
        "development_rank_1_trial_id": summary["development_rank_1_trial_id"],
        "verified": True,
        "human_expert_validated": False,
        "formal_qualification_authorized": False,
    }
