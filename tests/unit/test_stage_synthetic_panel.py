from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.synthetic_panel import (
    SyntheticPanelError,
    assign_synthetic_reviewers,
    assignment_manifest,
    audit_review_packet,
    evaluate_panel,
    load_synthetic_panel,
    review_packet,
)

ROOT = find_project_root()
PANEL = ROOT / "config/stage-learning/synthetic-expert-panel.v1.json"


def packet(
    *,
    context: str = "a",
    observation: str = "held",
    phase: str = "active",
    hold: int = 2,
    close: str = "102",
    anchor: str = "100",
    activity: str = "1.2",
    daily_return: str = "0.003",
) -> dict:
    market_date = date(2020, 2, 20)
    value = 90.0
    history = []
    for offset in range(61):
        day = market_date - timedelta(days=60 - offset)
        value *= 1 + float(daily_return)
        history.append(
            {
                "market_date": day.isoformat(),
                "admitted": True,
                "reason": "admitted",
                "high": f"{value * 1.01:.4f}",
                "low": f"{value * 0.99:.4f}",
                "close": f"{value:.4f}",
                "volume": "100000",
                "coordinate_epoch": "original-price-quantity-epoch",
                "reset_reason": None,
                "source_member_digest": "sha256:" + "b" * 64,
            }
        )
    return {
        "schema_version": "quanthus.stage-reference-review-packet.v1",
        "context_id": "sha256:" + context * 64,
        "sampling_stratum": "random",
        "boundary_tags": [],
        "security_code": "600519",
        "market_date": market_date.isoformat(),
        "anchor_scale": 20,
        "frozen_reference": {
            "anchor_key": "anchor",
            "anchor_revision": "sha256:" + "c" * 64,
            "frozen_high": anchor,
            "frozen_low": "80",
            "episode_started_at": "2020-02-10",
        },
        "t_only_process_facts": {
            "observation": observation,
            "phase": phase,
            "exchange_session_age": hold,
            "observed_bar_count": max(hold, 1),
            "hold_count": hold,
            "close": close,
            "interaction_ref": None,
            "previous_loss_ref": None,
            "censor_reason": None,
        },
        "activity": {
            "market_date": market_date.isoformat(),
            "available": True,
            "volume_ratio": activity,
            "reason": "available",
        },
        "history": history,
        "review_contract": {
            "reference_kind": ["definite", "coexistence", "ambiguous_set", "unjudgeable"],
            "supported_stage_codes": [3, 4],
            "adjacent_context_tokens": [
                "consolidation_like",
                "trend_background",
                "breakout_failure",
                "other",
            ],
            "primary_reviewers_required": 2,
            "adjudicator_required_on_disagreement": True,
        },
        "forbidden_fields": [
            "engineering_candidate_state",
            "engineering_primary_stage",
            "future_prices",
            "future_settlement",
            "strategy_result",
        ],
    }


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


def test_started_is_definite_stage3_and_lost_is_definite_negative() -> None:
    evidence = "sha256:" + "d" * 64
    started = packet(observation="started", phase="active", hold=0, activity="1.8")
    positive = review_packet(
        started,
        evidence_digest=evidence,
        persona_id="youzi-emotion-cycle-v1",
        reviewer_role="primary",
    )
    assert positive.reference_kind.value == "definite"
    assert positive.supported_stage_codes == (3,)

    lost = packet(observation="lost", phase="below", hold=3, close="99")
    negative = review_packet(
        lost,
        evidence_digest=evidence,
        persona_id="youzi-capacity-trend-v1",
        reviewer_role="primary",
    )
    assert negative.reference_kind.value == "definite"
    assert negative.supported_stage_codes == ()
    assert negative.adjacent_contexts == ("breakout_failure",)


def test_one_session_acceptance_can_disagree_across_persona_policies() -> None:
    item = packet(hold=1, close="101.5", activity="1.4", daily_return="0.004")
    evidence = "sha256:" + "e" * 64
    tactical = review_packet(
        item,
        evidence_digest=evidence,
        persona_id="youzi-emotion-cycle-v1",
        reviewer_role="primary",
    )
    capacity = review_packet(
        item,
        evidence_digest=evidence,
        persona_id="youzi-capacity-trend-v1",
        reviewer_role="primary",
    )
    assert tactical.reference_kind.value == "definite"
    assert tactical.supported_stage_codes == (4,)
    assert capacity.reference_kind.value == "ambiguous_set"
    assert capacity.supported_stage_codes == (3, 4)


def test_full_panel_adjudicates_primary_disagreement_without_future_data() -> None:
    panel = load_synthetic_panel(PANEL)
    rows = [
        packet(context="1", hold=1, close="101.5", activity="1.4", daily_return="0.004"),
        packet(context="2", observation="started", phase="active", hold=0, activity="1.8"),
        packet(context="3", observation="below", phase="below", hold=3, close="98"),
    ]
    evidence = {
        row["context_id"]: "sha256:" + str(index) * 64
        for index, row in enumerate(rows, 1)
    }
    result = evaluate_panel(panel, rows, evidence)
    assert result["future_settlement_read"] is False
    assert len(result["primary_a"]) == 3
    assert len(result["primary_b"]) == 3
    assert len(result["disagreement_context_ids"]) == 1
    assert len(result["adjudicator"]) == 1
    assert result["adjudicator"][0].reviewer_id == "youzi-breakout-tempo-v1"


def test_quant_auditor_vetoes_actual_future_field() -> None:
    item = packet()
    item["future_settlement"] = {"outcome": "positive"}
    audited = audit_review_packet(item, "sha256:" + "f" * 64)
    assert audited["veto"] is True
    assert "forbidden_field_present" in audited["reason_codes"]
