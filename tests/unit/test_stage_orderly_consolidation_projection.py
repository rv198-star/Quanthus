from __future__ import annotations

from decimal import Decimal

import pytest

from quanthus.stage_learning.orderly_consolidation_projection import (
    CONTRACT_DIGEST,
    OrderlyConsolidationMeasurements,
    OrderlyConsolidationProjectionError,
    OrderlyConsolidationThresholds,
    contraction_magnitude20,
    load_contract,
    project_orderly_consolidation,
)
from quanthus.structure_v2_inputs import digest


def thresholds() -> OrderlyConsolidationThresholds:
    return OrderlyConsolidationThresholds(
        efficiency20_abs_max=Decimal("0.30"),
        range_width20_max=Decimal("0.12"),
        contraction_magnitude20_min=Decimal("0.20"),
    )


def measurements(
    *,
    efficiency: str | None = "0.10",
    width: str | None = "0.08",
    change: str | None = "-0.50",
    relocation: str = "FALSE",
) -> OrderlyConsolidationMeasurements:
    return OrderlyConsolidationMeasurements(
        efficiency20_abs=None if efficiency is None else Decimal(efficiency),
        range_width20=None if width is None else Decimal(width),
        width_change20=None if change is None else Decimal(change),
        coherent_directional_relocation20=relocation,  # type: ignore[arg-type]
    )


def test_contract_digest_and_authority_are_exact() -> None:
    contract = load_contract()
    assert digest(contract) == CONTRACT_DIGEST
    assert set(contract["inputs"]) == {
        "efficiency20_abs",
        "range_width20",
        "width_change20",
        "coherent_directional_relocation20",
    }
    assert contract["authority"]["center_shift_truth_authority"] is False
    assert contract["authority"]["new_fact_registration"] is False
    assert contract["authority"]["market_data_read"] is False
    assert contract["authority"]["preserved_2021_evaluation_read"] is False


def test_positive_orderly_consolidation_requires_all_four_positive_conditions() -> None:
    result = project_orderly_consolidation(measurements(), thresholds())
    assert result.status == "TRUE"
    assert result.primitives == {
        "low_directional_efficiency": "TRUE",
        "materially_narrow_current_range": "TRUE",
        "material_range_contraction": "TRUE",
        "negative_coherent_relocation": "TRUE",
    }
    assert result.derived["contraction_magnitude20"] == "0.50"


@pytest.mark.parametrize(
    ("case", "primitive"),
    [
        (measurements(efficiency="0.31"), "low_directional_efficiency"),
        (measurements(width="0.13"), "materially_narrow_current_range"),
        (measurements(change="-0.19"), "material_range_contraction"),
        (measurements(relocation="TRUE"), "negative_coherent_relocation"),
    ],
)
def test_each_required_clause_can_fail_stage2(case, primitive: str) -> None:
    result = project_orderly_consolidation(case, thresholds())
    assert result.status == "FALSE"
    assert result.primitives[primitive] == "FALSE"


def test_center_shift_and_path_churn_are_not_projection_inputs() -> None:
    fields = set(OrderlyConsolidationMeasurements.__dataclass_fields__)
    assert "center_shift20_abs" not in fields
    assert "range_traversal20" not in fields
    assert "counter_direction_excursion_share20" not in fields
    assert "path_turnover20" not in fields
    result = project_orderly_consolidation(measurements(), thresholds()).to_dict()
    assert result["center_shift_truth_authority"] is False
    assert result["path_turnover_truth_authority"] is False
    assert result["relocation_guard_retuned"] is False


def test_tight_contracting_high_churn_is_not_vetoed_by_an_unowned_smoothness_rule() -> None:
    # Churn/counter-excursion are deliberately absent from the truth surface. If the governed
    # primary geometry is tight and contracting and the separate relocation proposition is false,
    # the projection may assert Stage2 regardless of in-band path activity.
    assert project_orderly_consolidation(measurements(), thresholds()).status == "TRUE"


def test_center_drift_without_relocation_cannot_veto_stage2() -> None:
    # Center shift is not an input to the projection; this test protects that design boundary.
    assert (
        project_orderly_consolidation(measurements(relocation="FALSE"), thresholds()).status
        == "TRUE"
    )


def test_directional_repricing_vetoes_even_a_tight_contracting_band() -> None:
    result = project_orderly_consolidation(measurements(relocation="TRUE"), thresholds())
    assert result.status == "FALSE"
    assert result.primitives["negative_coherent_relocation"] == "FALSE"


def test_narrow_but_non_contracting_structure_is_not_stage2() -> None:
    result = project_orderly_consolidation(measurements(change="0.00"), thresholds())
    assert result.status == "FALSE"
    assert result.derived["contraction_magnitude20"] == "0"


def test_missing_or_unknown_input_fails_closed_to_unknown_when_no_false_exists() -> None:
    assert project_orderly_consolidation(measurements(width=None), thresholds()).status == "UNKNOWN"
    assert (
        project_orderly_consolidation(measurements(relocation="UNKNOWN"), thresholds()).status
        == "UNKNOWN"
    )


def test_definitive_false_dominates_other_unknown_inputs() -> None:
    result = project_orderly_consolidation(
        measurements(efficiency=None, width="0.50", relocation="UNKNOWN"), thresholds()
    )
    assert result.status == "FALSE"
    assert result.primitives["low_directional_efficiency"] == "UNKNOWN"
    assert result.primitives["materially_narrow_current_range"] == "FALSE"


def test_contraction_magnitude_is_projection_local_and_deterministic() -> None:
    assert contraction_magnitude20(Decimal("-0.25")) == Decimal("0.25")
    assert contraction_magnitude20(Decimal("0.25")) == Decimal("0")
    assert contraction_magnitude20(None) is None


def test_invalid_measurements_and_thresholds_fail_closed() -> None:
    with pytest.raises(OrderlyConsolidationProjectionError, match="efficiency_out_of_bounds"):
        measurements(efficiency="1.01")
    with pytest.raises(OrderlyConsolidationProjectionError, match="negative_range_width"):
        measurements(width="-0.01")
    with pytest.raises(OrderlyConsolidationProjectionError, match="relocation_truth_value"):
        measurements(relocation="MAYBE")
    with pytest.raises(
        OrderlyConsolidationProjectionError, match="contraction_threshold_must_be_positive"
    ):
        OrderlyConsolidationThresholds(
            efficiency20_abs_max=Decimal("0.30"),
            range_width20_max=Decimal("0.12"),
            contraction_magnitude20_min=Decimal("0"),
        )
