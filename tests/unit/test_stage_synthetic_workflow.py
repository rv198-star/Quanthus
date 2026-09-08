from __future__ import annotations

import json
from pathlib import Path

from quanthus.stage_learning import synthetic_workflow as workflow

REVISION = "a" * 40


def test_synthetic_workflow_binds_d_review_resolution_and_e(
    tmp_path: Path, monkeypatch
) -> None:
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    panel = tmp_path / "panel.json"
    panel.write_text("{}", encoding="utf-8")
    review_root = tmp_path / "review-artifact"
    review_root.mkdir()
    (review_root / "summary.json").write_text(
        json.dumps(
            {
                "context_count": 120,
                "disagreement_count": 10,
                "institutional_challenge_context_count": 32,
            }
        ),
        encoding="utf-8",
    )
    learning_root = tmp_path / "learning-artifact"
    learning_root.mkdir()
    (learning_root / "summary.json").write_text(
        json.dumps(
            {
                "r1_trial_count": 18,
                "development_rank_1_trial_id": "trial-7",
                "development_rank_1_score": {"macro_f1": 0.81},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        workflow,
        "verify_reference_pilot_artifact",
        lambda *_args, **_kwargs: {
            "manifest_sha256": "sha256:" + "1" * 64,
            "independent_reference_complete": False,
        },
    )
    monkeypatch.setattr(
        workflow,
        "run_synthetic_review",
        lambda **_kwargs: {"artifact_root": str(review_root)},
    )
    monkeypatch.setattr(
        workflow,
        "verify_synthetic_review_artifact",
        lambda *_args, **_kwargs: {
            "manifest_sha256": "sha256:" + "2" * 64,
            "resolution_manifest_sha256": "sha256:" + "3" * 64,
            "resolution_artifact": str(tmp_path / "resolution"),
            "human_expert_validated": False,
        },
    )
    monkeypatch.setattr(
        workflow,
        "run_development_learning",
        lambda **_kwargs: {"run_root": str(learning_root)},
    )
    monkeypatch.setattr(
        workflow,
        "verify_learning_artifact",
        lambda *_args, **_kwargs: {
            "manifest_sha256": "sha256:" + "4" * 64,
            "verified": True,
        },
    )

    result = workflow.run_synthetic_workflow(
        pilot_artifact=pilot,
        panel_config=panel,
        training_root=tmp_path / "training",
        output_root=tmp_path / "out",
        code_revision=REVISION,
    )
    artifact = Path(result["artifact_root"])
    verified = workflow.verify_synthetic_workflow_artifact(artifact)
    assert verified["verified"] is True
    assert verified["development_rank_1_trial_id"] == "trial-7"
    assert result["reference_evidence_tier"] == "SYNTHETIC_EXPERT_CONSENSUS"
    assert result["human_expert_validated"] is False
    assert result["formal_qualification_authorized"] is False
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pilot_manifest_sha256"] == "sha256:" + "1" * 64
    assert manifest["synthetic_review_manifest_sha256"] == "sha256:" + "2" * 64
    assert manifest["resolution_manifest_sha256"] == "sha256:" + "3" * 64
    assert manifest["learning_manifest_sha256"] == "sha256:" + "4" * 64
