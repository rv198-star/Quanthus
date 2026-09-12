from __future__ import annotations

from decimal import Decimal

import pytest

from quanthus.stage_learning.positive_instability_quantitative_projection import (
    PositiveInstabilityProjectionError,
    ProjectionMeasurements,
    ProjectionThresholds,
    absolute_range_width_gain,
    at_least_two_of_three,
    load_contract,
    project_positive_instability,
)


def thresholds() -> ProjectionThresholds:
    return ProjectionThresholds(
        expansion_width_change_min=Decimal("0.30"),
        expansion_range_width_min=Decimal("0.12"),
        expansion_absolute_width_gain_min=Decimal("0.04"),
        expansion_efficiency_max=Decimal("0.35"),
        expansion_traversal_max=Decimal("0.55"),
        expansion_counter_excursion_min=Decimal("0.45"),
        broad_range_width_min=Decimal("0.20"),
        broad_efficiency_max=Decimal("0.30"),
        broad_traversal_max=Decimal("0.45"),
        broad_counter_excursion_min=Decimal("0.60"),
    )


def m(
    *,
    width: str,
    change: str,
    efficiency: str,
    traversal: str,
    counter: str | None,
) -> ProjectionMeasurements:
    return ProjectionMeasurements(
        range_width20=Decimal(width),
        width_change20=Decimal(change),
        efficiency20_abs=Decimal(efficiency),
        range_traversal20=Decimal(traversal),
        counter_direction_excursion_share20=None if counter is None else Decimal(counter),
    )


def test_contract_has_no_numeric_threshold_values_or_runtime_authority() -> None:
    contract = load_contract()
    assert contract["authority"]["numeric_threshold_values"] is False
    assert contract["authority"]["market_data_read"] is False
    assert contract["authority"]["runtime_job_submission"] is False
    assert contract["authority"]["parameter_search"] is False
    assert "structure.geometry.range-center-shift@20_vs_prior20" in contract["forbidden_inputs"]


def test_absolute_width_gain_is_exact_and_projection_local() -> None:
    assert absolute_range_width_gain(Decimal("0.18"), Decimal("0.50")) == Decimal("0.06")
    assert absolute_range_width_gain(Decimal("0.00"), Decimal("-1")) is None


def test_tiny_relative_expansion_without_absolute_growth_stays_false() -> None:
    result = project_positive_instability(
        m(width="0.03", change="0.50", efficiency="0.20", traversal="0.40", counter="0.60"),
        thresholds(),
    )
    assert result.expanding_disorder_20 == "FALSE"
    assert result.status == "FALSE"


def test_expanding_disorder_can_pass_without_broad_channel_width_floor() -> None:
    result = project_positive_instability(
        m(width="0.15", change="0.50", efficiency="0.25", traversal="0.80", counter="0.20"),
        thresholds(),
    )
    assert result.primitives["broad_range_width_material"] == "FALSE"
    assert result.expanding_disorder_20 == "TRUE"
    assert result.status == "TRUE"


def test_monotonic_expanding_path_fails_when_all_path_disorder_signals_are_false() -> None:
    result = project_positive_instability(
        m(width="0.30", change="0.80", efficiency="0.90", traversal="1.00", counter="0.05"),
        thresholds(),
    )
    assert result.primitives["width_expansion_material"] == "TRUE"
    assert result.derived["any_path_disorder"] == "FALSE"
    assert result.expanding_disorder_20 == "FALSE"


def test_broad_roundtrip_can_pass_with_moderate_efficiency_when_other_two_signals_agree() -> None:
    result = project_positive_instability(
        m(width="0.30", change="-0.10", efficiency="0.45", traversal="0.20", counter="0.90"),
        thresholds(),
    )
    assert result.primitives["broad_low_directional_efficiency"] == "FALSE"
    assert result.derived["broad_path_disorder_two_of_three"] == "TRUE"
    assert result.broad_reversal_disorder_20 == "TRUE"
    assert result.status == "TRUE"


def test_broad_low_efficiency_without_reversal_is_not_enough() -> None:
    result = project_positive_instability(
        m(width="0.30", change="-0.10", efficiency="0.10", traversal="0.90", counter="0.10"),
        thresholds(),
    )
    assert result.derived["broad_path_disorder_two_of_three"] == "FALSE"
    assert result.broad_reversal_disorder_20 == "FALSE"


def test_zero_net_counter_unknown_can_pass_with_low_efficiency_and_low_traversal() -> None:
    result = project_positive_instability(
        m(width="0.30", change="-0.10", efficiency="0.10", traversal="0.20", counter=None),
        thresholds(),
    )
    assert result.primitives["broad_high_counter_excursion"] == "UNKNOWN"
    assert result.derived["broad_path_disorder_two_of_three"] == "TRUE"
    assert result.broad_reversal_disorder_20 == "TRUE"


def test_two_of_three_truth_handles_unknown_fail_closed() -> None:
    assert at_least_two_of_three("TRUE", "TRUE", "UNKNOWN") == "TRUE"
    assert at_least_two_of_three("TRUE", "FALSE", "UNKNOWN") == "UNKNOWN"
    assert at_least_two_of_three("FALSE", "FALSE", "UNKNOWN") == "FALSE"


def test_center_shift_cannot_be_passed_into_truth_function() -> None:
    result = project_positive_instability(
        m(width="0.10", change="-0.50", efficiency="0.50", traversal="0.90", counter="0.10"),
        thresholds(),
    )
    payload = result.to_dict()
    assert payload["center_shift_truth_authority"] is False
    assert all("center_shift" not in key for key in payload["primitives"])
    assert all("center_shift" not in key for key in payload["derived"])


def test_invalid_thresholds_fail_closed() -> None:
    with pytest.raises(PositiveInstabilityProjectionError):
        ProjectionThresholds(
            expansion_width_change_min=Decimal("0"),
            expansion_range_width_min=Decimal("0.1"),
            expansion_absolute_width_gain_min=Decimal("0.01"),
            expansion_efficiency_max=Decimal("0.2"),
            expansion_traversal_max=Decimal("0.2"),
            expansion_counter_excursion_min=Decimal("0.2"),
            broad_range_width_min=Decimal("0.2"),
            broad_efficiency_max=Decimal("0.2"),
            broad_traversal_max=Decimal("0.2"),
            broad_counter_excursion_min=Decimal("0.2"),
        )
