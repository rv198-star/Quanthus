from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.third_group_profile import (
    StageThirdGroupProfileError,
    build_observation_metrics,
    build_third_group_profile,
    load_third_group_profile_config,
    summarize_metrics,
)
from quanthus.structure_v2_inputs import admit_daily

from .test_structure_v2_inputs import policy, row

ROOT = find_project_root()


def series(*, start: Decimal = Decimal("100"), step: Decimal = Decimal("1"), count: int = 70):
    values = []
    for index in range(count):
        close = start + step * Decimal(index)
        values.append(
            row(
                index,
                str(close),
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                volume=Decimal("1000") + Decimal(index),
            )
        )
    return values


def test_config_freezes_three_semantic_subgroups_and_no_label_authority():
    config = load_third_group_profile_config(
        ROOT / "config/stage-learning/third-group-profile.v1.json"
    )
    assert config["semantic_subgroups"]["order_consolidation"]["candidate_stage_codes"] == [0, 2]
    assert config["semantic_subgroups"]["downside_lifecycle"]["candidate_stage_codes"] == [1, 12]
    assert config["semantic_subgroups"]["high_side_modifiers"]["candidate_stage_codes"] == [8, 9]
    assert (
        config["semantic_subgroups"]["high_side_modifiers"]["modifier_hypothesis_explicit"]
        is True
    )
    assert config["authority"]["stage_label_output_authorized"] is False
    assert config["authority"]["parameter_search_authorized"] is False
    assert config["authority"]["formal_structure_fact_registration_authorized"] is False
    assert config["authority"]["arbitration_access_authorized"] is False
    assert config["authority"]["permanent_blind_access_authorized"] is False


def test_uptrend_metrics_have_positive_direction_and_high_efficiency():
    metrics = build_observation_metrics(series())
    assert metrics["return_5_sessions"] > 0
    assert metrics["return_20_sessions"] > 0
    assert metrics["return_60_sessions"] > 0
    assert metrics["signed_directional_efficiency_5"] == Decimal(1)
    assert metrics["signed_directional_efficiency_20"] == Decimal(1)
    assert metrics["return_velocity_5_per_session"] > 0
    assert metrics["position_in_range_60"] is not None
    assert metrics["position_in_range_60"] > Decimal("0.9")


def test_downtrend_metrics_have_negative_direction_and_low_position():
    metrics = build_observation_metrics(series(start=Decimal("200"), step=Decimal("-1")))
    assert metrics["return_20_sessions"] < 0
    assert metrics["signed_directional_efficiency_20"] == Decimal(-1)
    assert metrics["drawdown_from_high_60"] < 0
    assert metrics["position_in_range_60"] is not None
    assert metrics["position_in_range_60"] < Decimal("0.1")
    assert metrics["rebound_from_low_20"] >= 0


def test_recent_deceleration_is_visible_without_future_low():
    values = []
    close = Decimal("200")
    for index in range(70):
        step = Decimal("-2") if index < 65 else Decimal("-0.2")
        close += step
        values.append(
            row(
                index,
                str(close),
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                volume=Decimal("1000"),
            )
        )
    metrics = build_observation_metrics(values)
    assert metrics["return_20_sessions"] < 0
    assert metrics["velocity_delta_5_vs_20"] > 0


def test_width_contraction_and_center_shift_are_separate_dimensions():
    values = series(step=Decimal("0"))
    for index in range(50, 60):
        values[index] = replace(
            values[index],
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("100"),
        )
    for index in range(60, 70):
        values[index] = replace(
            values[index],
            high=Decimal("102"),
            low=Decimal("98"),
            close=Decimal("100"),
        )
    metrics = build_observation_metrics(values)
    assert metrics["range_width_change_10_vs_prior10"] is not None
    assert metrics["range_width_change_10_vs_prior10"] < 0
    assert metrics["center_shift_10_vs_prior10"] == 0
    assert metrics["signed_directional_efficiency_20"] == 0


def test_missing_volume_only_suppresses_activity_metrics():
    values = series()
    values[-1] = replace(values[-1], volume=None)
    metrics = build_observation_metrics(values)
    assert metrics["return_20_sessions"] is not None
    assert metrics["volume_ratio_20"] is None
    assert metrics["recent_volume_ratio_5_to_prior20"] is None


def test_profile_rewarms_after_nonadmitted_row_and_does_not_bridge_gap():
    values = series(count=140)
    values[70] = replace(values[70], quality_reason="missing_bar")
    admissions = tuple(admit_daily(item, policy()) for item in values)
    observations, counts = build_third_group_profile(
        admissions,
        report_start=values[60].market_date,
        report_end=values[-1].market_date,
    )
    assert counts["nonadmitted"] == 1
    # 10 rows before the reset (60..69) + 9 after the second 61-session warm-up (131..139).
    assert len(observations) == 19
    assert counts["profiled_rows"] == len(observations)


def test_summary_buckets_do_not_select_states_or_depend_on_future_outcomes():
    observations = [build_observation_metrics(series())]
    profiles, joint, slices = summarize_metrics(
        observations,
        metric_names=("return_20_sessions", "position_in_range_60"),
        quantiles=(0.25, 0.5, 0.75),
        bands={
            "position_lower_third_max": "0.3333333333333333333333333333",
            "position_upper_third_min": "0.6666666666666666666666666667",
            "efficiency_low_abs_max": "0.3333333333333333333333333333",
            "efficiency_high_abs_min": "0.6666666666666666666666666667",
        },
    )
    assert profiles["return_20_sessions"]["count"] == 1
    assert sum(joint.values()) == 1
    assert slices["up20"] == 1
    assert slices["upper_third"] == 1
    assert slices["high_abs_efficiency"] == 1


def test_config_rejects_any_attempt_to_open_parameter_search(tmp_path: Path):
    source = (ROOT / "config/stage-learning/third-group-profile.v1.json").read_text(
        encoding="utf-8"
    )
    path = tmp_path / "config.json"
    changed = source.replace(
        '"parameter_search_authorized": false',
        '"parameter_search_authorized": true',
    )
    path.write_text(changed, encoding="utf-8")
    with pytest.raises(StageThirdGroupProfileError, match="authority"):
        load_third_group_profile_config(path)
