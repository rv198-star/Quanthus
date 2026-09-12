from __future__ import annotations

from decimal import Decimal

from quanthus.stage_learning.positive_instability_quantitative_development import (
    candidate_grid,
    load_execution,
    load_preregistration,
    nearest_rank,
    threshold_basis,
)


def rows() -> list[dict[str, str]]:
    output = []
    for index in range(1, 41):
        width = Decimal(index) / Decimal("100")
        change = Decimal(index - 10) / Decimal("20")
        output.append(
            {
                "range_width20": str(width),
                "width_change20": str(change),
                "efficiency20_abs": str(Decimal(index) / Decimal("50")),
                "range_traversal20": str(Decimal(index) / Decimal("50")),
                "counter_direction_excursion_share20": str(
                    Decimal(41 - index) / Decimal("50")
                ),
            }
        )
    return output


def test_preregistration_and_execution_freeze_nine_profile_candidates() -> None:
    prereg = load_preregistration()
    execution = load_execution()
    assert prereg["candidate_grid"]["candidate_count"] == 9
    assert len(prereg["candidate_grid"]["expansion_profiles"]) == 3
    assert len(prereg["candidate_grid"]["broad_profiles"]) == 3
    assert execution["candidate_identity"]["candidate_count"] == 9
    assert prereg["authority"]["preserved_2021_evaluation_read_before_candidate_freeze"] is False


def test_nearest_rank_is_deterministic_without_interpolation() -> None:
    values = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]
    assert nearest_rank(values, Decimal("0.50")) == Decimal("2")
    assert nearest_rank(values, Decimal("0.70")) == Decimal("3")


def test_threshold_basis_adds_positive_absolute_width_gain() -> None:
    basis = threshold_basis(rows())
    assert basis["labels_used"] is False
    assert basis["observation_count"] == 40
    assert basis["positive_width_change_observation_count"] > 0
    assert basis["positive_absolute_width_gain_observation_count"] > 0
    assert set(basis["positive_absolute_width_gain20"]) == {"0.35", "0.50", "0.65"}


def test_candidate_grid_is_exactly_three_by_three_and_has_no_shared_core_id() -> None:
    basis = threshold_basis(rows())
    candidates = candidate_grid(basis)
    assert len(candidates) == 9
    assert len({candidate.candidate_id for candidate in candidates}) == 9
    assert {candidate.expansion_profile_id for candidate in candidates} == {
        "expansion-lenient",
        "expansion-balanced",
        "expansion-strict",
    }
    assert {candidate.broad_profile_id for candidate in candidates} == {
        "broad-lenient",
        "broad-balanced",
        "broad-strict",
    }
    assert all("core_profile_id" not in candidate.to_dict() for candidate in candidates)


def test_expansion_and_broad_thresholds_are_channel_specific() -> None:
    candidate = candidate_grid(threshold_basis(rows()))[0]
    values = candidate.to_dict()
    assert "expansion_range_width_min" in values
    assert "broad_range_width_min" in values
    assert "expansion_efficiency_max" in values
    assert "broad_efficiency_max" in values
    assert values["candidate_id"].startswith("sha256:")
