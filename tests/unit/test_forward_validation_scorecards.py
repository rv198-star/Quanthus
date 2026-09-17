from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.forward_validation.contracts import ForwardValidationContractError
from quanthus.forward_validation.loss_attribution import classify_loss, review_ready
from quanthus.forward_validation.scorecards import build_role_scorecard, load_scorecard_policy

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/forward-validation/scorecards/p6-role-scorecards.v1.json"


def _policy():
    return load_scorecard_policy(POLICY)


def test_scorecard_policy_is_role_specific_and_pre_result() -> None:
    policy = _policy()
    assert set(policy.roles) == {
        "trend_assessment",
        "position_risk",
        "aggregator",
        "complete_strategy",
        "paper_operation",
    }
    assert policy.material_loss_threshold_cny == Decimal("1000")
    assert "net_total_return" not in policy.roles["trend_assessment"].primary_metrics
    assert "positive_entry_score" in policy.roles["position_risk"].forbidden_substitutions


def test_role_card_requires_frozen_primary_metrics_and_registered_slices() -> None:
    policy = _policy()
    card = build_role_scorecard(
        policy=policy,
        role="trend_assessment",
        source_artifact_digest="sha256:" + "1" * 64,
        metric_values={
            "trend_maoe": Decimal("0.6"),
            "trend_maoe_vs_always_neutral": Decimal("-0.2"),
            "coverage": Decimal("0.9"),
        },
        sample_count=22,
        slices={"collection_block_22_sessions": "block-1"},
        limitations=("first prospective block only",),
    )
    assert card.role == "trend_assessment"
    assert card.metric_values["coverage"] == "0.9"

    with pytest.raises(ForwardValidationContractError, match="primary_metric_missing"):
        build_role_scorecard(
            policy=policy,
            role="trend_assessment",
            source_artifact_digest="sha256:" + "1" * 64,
            metric_values={"coverage": Decimal("1")},
            sample_count=22,
            slices={},
            limitations=("incomplete",),
        )

    with pytest.raises(ForwardValidationContractError, match="slice_not_registered"):
        build_role_scorecard(
            policy=policy,
            role="paper_operation",
            source_artifact_digest="sha256:" + "2" * 64,
            metric_values={
                "cash_position_reconciliation_break_count": 0,
                "pending_intent_count": 1,
            },
            sample_count=1,
            slices={"security_code": "000001"},
            limitations=("synthetic",),
        )


def test_material_loss_threshold_and_integrity_override_are_frozen() -> None:
    policy = _policy()
    small = classify_loss(
        policy=policy,
        event_id="event-small",
        market_date=date(2026, 9, 18),
        loss_cny=Decimal("999.99"),
        mechanism_integrity_breach=False,
        classification=None,
        rationale="below the frozen one-percent-of-unit review threshold",
    )
    assert small.material is False

    material = classify_loss(
        policy=policy,
        event_id="event-material",
        market_date=date(2026, 9, 18),
        loss_cny=Decimal("1000"),
        mechanism_integrity_breach=False,
        classification="trend_assessment_error",
        rationale="settled target contradicted the frozen trend assessment",
    )
    assert material.material is True

    with pytest.raises(ForwardValidationContractError, match="unclassified_material_loss"):
        classify_loss(
            policy=policy,
            event_id="event-integrity",
            market_date=date(2026, 9, 18),
            loss_cny=Decimal("0"),
            mechanism_integrity_breach=True,
            classification=None,
            rationale="semantic drift is material regardless of PnL",
        )


def test_review_readiness_requires_all_roles_closed_ledger_and_zero_integrity_breaks() -> None:
    policy = _policy()
    roles = tuple(policy.roles)
    assert review_ready(
        policy=policy,
        role_scorecard_roles=roles,
        losses=(),
        mechanism_integrity_breach_count=0,
        mature_ledger_closed=True,
    )
    assert not review_ready(
        policy=policy,
        role_scorecard_roles=roles[:-1],
        losses=(),
        mechanism_integrity_breach_count=0,
        mature_ledger_closed=True,
    )
    assert not review_ready(
        policy=policy,
        role_scorecard_roles=roles,
        losses=(),
        mechanism_integrity_breach_count=1,
        mature_ledger_closed=True,
    )
