from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.synthetic_panel import (
    SyntheticPanelError,
    assign_synthetic_reviewers,
    assignment_manifest,
    load_synthetic_panel,
)

ROOT = find_project_root()
PANEL = ROOT / "config/stage-learning/synthetic-expert-panel.v1.json"


def test_synthetic_panel_freezes_public_style_archetypes_without_impersonation() -> None:
    panel = load_synthetic_panel(PANEL)
    assert panel["evidence_tier"] == "SYNTHETIC_EXPERT_CONSENSUS"
    assert panel["development_use"]["formal_qualification_allowed"] is False
    anchors = [
        item["public_style_anchor"]
        for item in panel["personas"]
        if item["public_style_anchor"]
    ]
    assert "炒股养家风格锚点" in anchors
    assert "赵老哥风格锚点" in anchors
    assert "章盟主风格锚点" in anchors
    assert "张坤风格锚点" in anchors
    assert "朱少醒风格锚点" in anchors
    assert all(
        item.get("not_identity_or_personal_view") is True
        for item in panel["personas"]
        if item["public_style_anchor"] is not None
    )


def test_assignments_are_deterministic_cross_style_and_keep_auditor_nonvoting() -> None:
    panel = load_synthetic_panel(PANEL)
    context = "sha256:" + "a" * 64
    first = assign_synthetic_reviewers(panel, context)
    second = assign_synthetic_reviewers(panel, context)
    assert first == second
    by_id = {item["persona_id"]: item for item in panel["personas"]}
    assert first.primary_a != first.primary_b != first.adjudicator
    assert by_id[first.primary_a]["style_cluster"] != by_id[first.primary_b]["style_cluster"]
    assert by_id[first.auditor]["semantic_vote_authorized"] is False
    assert first.evidence_tier == "SYNTHETIC_EXPERT_CONSENSUS"


def test_assignment_manifest_cannot_claim_formal_qualification() -> None:
    panel = load_synthetic_panel(PANEL)
    contexts = ["sha256:" + f"{index:064x}" for index in range(1, 5)]
    manifest = assignment_manifest(panel, contexts)
    assert manifest["context_count"] == 4
    assert manifest["formal_qualification_allowed"] is False
    assert len(manifest["assignments"]) == 4


def test_panel_rejects_public_anchor_without_nonidentity_disclaimer(tmp_path: Path) -> None:
    raw = json.loads(PANEL.read_text(encoding="utf-8"))
    raw["personas"][0]["not_identity_or_personal_view"] = False
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SyntheticPanelError, match="non-identity"):
        load_synthetic_panel(path)


def test_panel_rejects_formal_qualification_authority(tmp_path: Path) -> None:
    raw = json.loads(PANEL.read_text(encoding="utf-8"))
    raw["development_use"]["formal_qualification_allowed"] = True
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SyntheticPanelError, match="formal qualification"):
        load_synthetic_panel(path)
