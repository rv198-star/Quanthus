from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pytest

from quanthus.stage_learning.p5_assessment import (
    DecisionState,
    FeatureCell,
    FeatureWindow,
    FixedBaselineSet,
    P5AssessmentError,
    TemporalEncoderConfig,
    aggregate_seed_scores,
    build_t0_model,
    masked_binary_cross_entropy,
    resolve_output,
    summarize_window,
    validate_checkpoint_payload,
)

ROOT = Path(__file__).resolve().parents[2]
P5 = ROOT / "config/research/p5-stage-001"


def test_successor_activation_preserves_e_and_protected_boundaries() -> None:
    value = json.loads((P5 / "successor-activation.v1.json").read_text(encoding="utf-8"))
    assert value["source_commit"] == "8bd989dfae98acd4f28799a376ee0ca5df9162aa"
    assert value["activation"] == {
        "B_issue_402": "ACTIVE_OUTCOME_BLIND_READINESS_FIRST",
        "C_issue_403": "ACTIVE_SYNTHETIC_ENVIRONMENT_ONLY",
        "D_issue_404": "ACTIVE_SYNTHETIC_ENGINEERING_ONLY",
        "E_issue_405": "INACTIVE_PENDING_B_C_D_ACCEPTANCE",
    }
    assert value["B_data_boundary"]["2011_warmup_authorized"] is False
    assert value["B_data_boundary"]["protected_2022_2026_access_authorized"] is False
    assert value["D_boundary"]["result_bearing_model_training_authorized"] is False


def test_readiness_keeps_unknown_null_and_known_ineligible_fail_closed() -> None:
    value = json.loads((P5 / "readiness-census.v1.json").read_text(encoding="utf-8"))
    matrix = value["readiness_matrix"]
    assert len(matrix) == 12
    assert value["market_data_rows_read"] == 0
    by_code = {row["historical_stage_code"]: row for row in matrix}
    for code in (7, 8, 9):
        assert by_code[code]["trainable"] is False
        assert by_code[code]["candidate_positive_count"] is None
        assert by_code[code]["candidate_negative_count"] is None
    for code in set(range(1, 13)) - {7, 8, 9}:
        assert by_code[code]["trainable"] is None


def test_cpu_preflight_is_real_but_does_not_claim_rx6800_or_c_acceptance() -> None:
    preflight = json.loads((P5 / "compute-preflight.v1.json").read_text(encoding="utf-8"))
    result = json.loads((P5 / "cpu-probe-result.v1.json").read_text(encoding="utf-8"))
    assert preflight["host"]["RX6800_present"] is False
    assert preflight["benchmark"]["CPU_full_path"] == "PASS_T0_SYNTHETIC_ONLY"
    assert preflight["benchmark"]["checkpoint_restart_resume"] is True
    assert preflight["decision"] == "CPU_PATH_PROVISIONALLY_VALID_RX6800_AND_T1_UNVERIFIED"
    assert result["measured_steps"] == 3
    assert result["checkpoint_resume"]["completed_batch_cursor"] == 4
    assert result["market_data_rows_read"] == 0


def test_temporal_encoder_is_fixed_multi_head_and_below_parameter_cap() -> None:
    config = TemporalEncoderConfig()
    config.validate()
    assert config.output_heads == 12
    assert config.parameter_count() == 844_428
    assert config.parameter_count() < 2_000_000


def test_feature_window_rejects_future_input_and_mask_conflation() -> None:
    config = TemporalEncoderConfig(window_sessions=5)
    row = tuple(FeatureCell(1.0) for _ in range(24))
    with pytest.raises(P5AssessmentError, match="future session"):
        FeatureWindow(
            (date(2021, 1, 4), date(2021, 1, 5)),
            (row, row),
            date(2021, 1, 4),
        ).validate(config)
    with pytest.raises(P5AssessmentError, match="must stay distinct"):
        FeatureCell(None, actual_missing=True, artificial_mask=True).validate()


def test_l_and_g_share_one_deterministic_causal_summary() -> None:
    config = TemporalEncoderConfig(window_sessions=5)
    rows = tuple(
        tuple(FeatureCell(float(session + channel)) for channel in range(24))
        for session in range(5)
    )
    window = FeatureWindow(
        tuple(date(2021, 1, day) for day in range(4, 9)),
        rows,
        date(2021, 1, 8),
    )
    summary = summarize_window(window, config)
    assert len(summary) == 24 * 5
    assert summary[:5] == (4.0, 2.0, 0.0, 4.0, 0.0)


def test_masked_bce_ignores_unknown_and_non_applicable_without_making_negatives() -> None:
    score = [[0.8] * 12]
    labels = [[1, 0, None, *([None] * 9)]]
    applicability = [[True, True, True, *([False] * 9)]]
    loss = masked_binary_cross_entropy(score, labels, applicability)
    expected = (-math.log(0.8) - math.log(0.2)) / 2
    assert loss == pytest.approx(expected)
    with pytest.raises(P5AssessmentError, match="no definite applicable label"):
        masked_binary_cross_entropy(score, [[None] * 12], [[True] * 12])


def test_seed_primary_requires_all_three_without_best_seed_selection() -> None:
    scores = {
        17: [0.1] * 12,
        29: [0.4] * 12,
        43: [0.7] * 12,
    }
    assert aggregate_seed_scores(scores) == pytest.approx((0.4,) * 12)
    with pytest.raises(P5AssessmentError, match="exactly seeds"):
        aggregate_seed_scores({17: [0.1] * 12, 29: [0.4] * 12})


def test_stage11_parent_false_and_unknown_are_not_negative() -> None:
    not_applicable = resolve_output(
        historical_stage_code=11,
        applicability=False,
        raw_score=0.99,
        calibrated_probability=0.99,
    )
    assert not_applicable.decision_state is DecisionState.NOT_APPLICABLE
    assert not_applicable.raw_score is None
    assert not_applicable.calibrated_probability is None
    unknown = resolve_output(
        historical_stage_code=11,
        applicability=None,
        raw_score=0.99,
        calibrated_probability=0.99,
    )
    assert unknown.decision_state is DecisionState.ABSTAIN
    assert unknown.calibrated_probability is None


def test_ineligible_heads_and_missing_seed_are_unavailable() -> None:
    for code in (7, 8, 9):
        output = resolve_output(
            historical_stage_code=code,
            applicability=True,
            raw_score=0.9,
            calibrated_probability=0.9,
        )
        assert output.decision_state is DecisionState.UNAVAILABLE
        assert output.raw_score is None
        assert output.calibrated_probability is None
    missing = resolve_output(
        historical_stage_code=2,
        applicability=True,
        raw_score=0.9,
        calibrated_probability=0.9,
        required_seeds_complete=False,
    )
    assert missing.decision_state is DecisionState.UNAVAILABLE


def test_uncalibrated_score_cannot_create_assertion_and_x_has_precedence() -> None:
    uncalibrated = resolve_output(
        historical_stage_code=2,
        applicability=True,
        raw_score=0.99,
        calibrated_probability=None,
    )
    assert uncalibrated.decision_state is DecisionState.ABSTAIN
    assert uncalibrated.raw_score == 0.99
    assert uncalibrated.calibrated_probability is None
    controlled = resolve_output(
        historical_stage_code=2,
        applicability=True,
        raw_score=0.99,
        calibrated_probability=0.99,
        control_x=True,
    )
    assert controlled.decision_state is DecisionState.X
    assert controlled.raw_score is None


def test_checkpoint_requires_full_resume_state_and_fixed_seed() -> None:
    digest = "sha256:" + "a" * 64
    payload = {
        "model_state": {},
        "optimizer_state": {},
        "scheduler_state": {},
        "rng_state": {},
        "sampler_state": {},
        "epoch_cursor": 2,
        "batch_cursor": 3,
        "seed": 17,
        "input_digest": digest,
        "model_digest": digest,
        "version_digest": digest,
    }
    validate_checkpoint_payload(payload)
    payload["seed"] = 101
    with pytest.raises(P5AssessmentError, match="fixed seed"):
        validate_checkpoint_payload(payload)


def test_fixed_l_and_g_fit_independent_heads_and_suppress_7_8_9() -> None:
    pytest.importorskip("sklearn")
    features = [[float(index), float(index % 2)] for index in range(20)]
    labels = [[index % 2] * 12 for index in range(20)]
    applicability = [[True] * 12 for _ in features]
    for kind in ("L", "G"):
        model = FixedBaselineSet(kind).fit(features, labels, applicability)
        rows = model.predict_raw(features[:2])
        assert len(rows) == 2
        assert all(rows[0][index] is None for index in (6, 7, 8))
        assert all(
            rows[0][index] is not None for index in set(range(12)) - {6, 7, 8}
        )


def test_t0_has_fixed_shape_parameter_count_and_distinct_masks() -> None:
    torch = pytest.importorskip("torch")
    model = build_t0_model()
    assert sum(parameter.numel() for parameter in model.parameters()) == 844_428
    values = torch.randn(2, 120, 24)
    actual = torch.zeros_like(values, dtype=torch.bool)
    artificial = torch.zeros_like(values, dtype=torch.bool)
    assert tuple(model(values, actual, artificial).shape) == (2, 12)
    actual[0, 0, 0] = True
    artificial[0, 0, 0] = True
    with pytest.raises(P5AssessmentError, match="overlap"):
        model(values, actual, artificial)
