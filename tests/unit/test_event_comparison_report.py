from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.event_library.comparison_report import (
    ComparisonReportError,
    _aggregate_feature_rows,
    _benjamini_hochberg,
    _effect,
    _feature_ranking,
    _Stats,
    calculate_comparison_report_policy_digest,
    load_comparison_report_policy,
)
from quanthus.policy import find_project_root

ROOT = find_project_root()
POLICY_PATH = ROOT / "config" / "p4-event-comparison-report.research.v1.json"
FEATURES = (
    "price.price_to_sma20",
    "price.sma20_to_sma60",
    "price.momentum20",
    "price.breakout20",
    "price.rsi14",
    "liquidity.volume_ratio20",
    "risk.realized_volatility20",
)


def test_repository_report_policy_is_approved_and_digest_bound() -> None:
    policy = load_comparison_report_policy(POLICY_PATH)

    assert policy.lifecycle == "approved"
    assert policy.governance.approval_state == "approved"
    assert policy.semantic_digest == calculate_comparison_report_policy_digest(POLICY_PATH)
    assert policy.semantic_digest == (
        "sha256:4b7e6f931b06615940a9a72a5bd05f800be40f825716093e04637ab5fd8134a0"
    )
    assert policy.primary_contrast == ("positive", "neutral")
    assert tuple(item.feature_id for item in policy.features) == FEATURES
    assert policy.lookback_sessions == 21
    assert policy.leading_feature_count == 3
    policy.require_reporting_authority()


def test_report_policy_digest_detects_semantic_tampering(tmp_path: Path) -> None:
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    raw["statistics"]["fdr_alpha"] = "0.10"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ComparisonReportError, match="semantic_digest"):
        load_comparison_report_policy(path)


def test_hedges_g_and_mean_difference_have_expected_direction() -> None:
    left = _Stats(count=1000, mean=0.10, stddev=0.20)
    right = _Stats(count=900, mean=0.04, stddev=0.20)

    hedges_g, mean_difference, ci_low, ci_high, p_value = _effect(left, right)

    assert hedges_g is not None and hedges_g > 0
    assert mean_difference == pytest.approx(0.06)
    assert ci_low is not None and ci_low > 0
    assert ci_high is not None and ci_high > ci_low
    assert p_value is not None and p_value < 0.001


def test_benjamini_hochberg_is_monotone_and_bounded() -> None:
    rows = [
        {"p_value": 0.001, "q_value": None},
        {"p_value": 0.01, "q_value": None},
        {"p_value": 0.04, "q_value": None},
        {"p_value": 0.20, "q_value": None},
    ]

    _benjamini_hochberg(rows)

    values = [float(row["q_value"]) for row in rows]
    assert values == sorted(values)
    assert all(0 <= value <= 1 for value in values)
    assert values[0] <= 0.01
    assert values[-1] == pytest.approx(0.20)


def test_feature_ranking_requires_predeclared_full_and_perturbation_checks() -> None:
    policy = load_comparison_report_policy(POLICY_PATH)
    pairwise: list[dict[str, object]] = []
    half_rows: list[dict[str, object]] = []
    for feature_index, feature_id in enumerate(FEATURES, 1):
        effect = 0.02 * feature_index
        for lag in range(21):
            pairwise.append(
                {
                    "feature_id": feature_id,
                    "lag": lag,
                    "contrast": "positive_vs_neutral",
                    "left_count": 1000,
                    "right_count": 1000,
                    "hedges_g": effect,
                    "q_value": 0.001,
                }
            )
            for half in (0, 1):
                half_rows.extend(
                    [
                        {
                            "feature_id": feature_id,
                            "lag": lag,
                            "hash_half": half,
                            "outcome": "positive",
                            "sample_count": 500,
                            "mean": effect,
                            "stddev": 1.0,
                        },
                        {
                            "feature_id": feature_id,
                            "lag": lag,
                            "hash_half": half,
                            "outcome": "neutral",
                            "sample_count": 500,
                            "mean": 0.0,
                            "stddev": 1.0,
                        },
                    ]
                )

    ranking = _feature_ranking(pairwise, half_rows, policy)

    assert all(row["survives_declared_checks"] for row in ranking)
    assert [row["feature_id"] for row in ranking[:3]] == [
        "risk.realized_volatility20",
        "liquidity.volume_ratio20",
        "price.rsi14",
    ]
    assert all(row["first_reliable_lag"] == 20 for row in ranking)


def test_feature_panel_alignment_expands_complete_three_group_matrix(tmp_path: Path) -> None:
    start = date(2020, 1, 1)
    rows: list[dict[str, object]] = []
    event_inputs: list[tuple[object, ...]] = []
    groups = (
        ("positive", 0),
        ("positive", 1),
        ("neutral", 0),
        ("neutral", 1),
        ("failure", 0),
        ("failure", 1),
    )
    for security_index, (outcome, half) in enumerate(groups, 1):
        security_id = f"security_{security_index:03d}"
        event_id = f"event:test:{security_index:03d}"
        for lag_index in range(21):
            market_date = start + timedelta(days=lag_index)
            base = 100.0 + security_index + lag_index / 10.0
            rows.append(
                {
                    "market_date": market_date,
                    "security_id": security_id,
                    "total_return_index": base,
                    "sma20": base * 0.99,
                    "sma60": base * 0.98,
                    "momentum20": 0.01 * security_index,
                    "prior_high20": base * 1.01,
                    "rsi14": 45.0 + security_index,
                    "volume_ratio20": 1.0 + security_index / 10.0,
                    "realized_volatility20": 0.2 + security_index / 100.0,
                }
            )
        event_inputs.append(
            (event_id, security_id, (start + timedelta(days=20)).isoformat(), outcome, half)
        )
    panel_path = tmp_path / "panel.parquet"
    pq.write_table(pa.Table.from_pylist(rows), panel_path)

    group_rows, half_rows = _aggregate_feature_rows(panel_path, event_inputs)

    assert len(group_rows) == len(FEATURES) * 21 * 3
    assert len(half_rows) == len(FEATURES) * 21 * 2 * 2
    at_t = [
        row
        for row in group_rows
        if row["feature_id"] == "price.momentum20" and int(row["lag"]) == 0
    ]
    assert {row["outcome"]: int(row["sample_count"]) for row in at_t} == {
        "failure": 2,
        "neutral": 2,
        "positive": 2,
    }


def test_feature_ranking_rejects_hash_half_direction_flip() -> None:
    policy = load_comparison_report_policy(POLICY_PATH)
    pairwise: list[dict[str, object]] = []
    half_rows: list[dict[str, object]] = []
    for feature_id in FEATURES:
        for lag in range(21):
            pairwise.append(
                {
                    "feature_id": feature_id,
                    "lag": lag,
                    "contrast": "positive_vs_neutral",
                    "left_count": 1000,
                    "right_count": 1000,
                    "hedges_g": 0.10,
                    "q_value": 0.001,
                }
            )
            for half in (0, 1):
                positive_mean = -0.10 if feature_id == FEATURES[0] and half == 1 else 0.10
                half_rows.extend(
                    [
                        {
                            "feature_id": feature_id,
                            "lag": lag,
                            "hash_half": half,
                            "outcome": "positive",
                            "sample_count": 500,
                            "mean": positive_mean,
                            "stddev": 1.0,
                        },
                        {
                            "feature_id": feature_id,
                            "lag": lag,
                            "hash_half": half,
                            "outcome": "neutral",
                            "sample_count": 500,
                            "mean": 0.0,
                            "stddev": 1.0,
                        },
                    ]
                )

    ranking = _feature_ranking(pairwise, half_rows, policy)
    first = next(row for row in ranking if row["feature_id"] == FEATURES[0])

    assert first["hash_half_stable"] is False
    assert first["survives_declared_checks"] is False
