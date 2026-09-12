from __future__ import annotations

import inspect

import pytest

from quanthus.stage_learning.order_reference_protocol_v2 import project_atomic_primary
from quanthus.stage_learning.positive_instability import (
    CONTRACT_DIGEST,
    PositiveInstabilityError,
    PrimitiveEvidence,
    assess_positive_instability,
    attach_modifiers,
    load_contract,
    truth_and,
    truth_or,
)


def evidence(
    *,
    width: str,
    expansion: str,
    low_efficiency: str,
    low_traversal: str,
    high_counter: str,
) -> PrimitiveEvidence:
    return PrimitiveEvidence.from_mapping(
        {
            "range_width_material_20": width,
            "width_expansion_material_20": expansion,
            "low_directional_efficiency_20": low_efficiency,
            "low_range_traversal_20": low_traversal,
            "high_counter_direction_excursion_20": high_counter,
        }
    )


def test_contract_contains_no_numeric_thresholds_or_runtime_authority() -> None:
    contract = load_contract()
    assert CONTRACT_DIGEST.startswith("sha256:")
    assert contract["thresholds"] == {
        "numeric_thresholds_in_contract": False,
        "consumed_008_evaluation_may_supply_thresholds": False,
        "parameter_search_authorized": False,
    }
    assert contract["authority"]["market_data_read"] is False
    assert contract["authority"]["runtime_job_submission"] is False
    assert contract["authority"]["parameter_search"] is False
    assert contract["authority"]["stage_2_change"] is False
    assert contract["authority"]["relocation_guard_retuning"] is False


def test_three_value_logic_is_fail_closed_and_explainable() -> None:
    assert truth_and("TRUE", "TRUE") == "TRUE"
    assert truth_and("TRUE", "UNKNOWN") == "UNKNOWN"
    assert truth_and("UNKNOWN", "FALSE") == "FALSE"
    assert truth_or("FALSE", "FALSE") == "FALSE"
    assert truth_or("FALSE", "UNKNOWN") == "UNKNOWN"
    assert truth_or("UNKNOWN", "TRUE") == "TRUE"
    with pytest.raises(PositiveInstabilityError):
        truth_and()
    with pytest.raises(PositiveInstabilityError):
        truth_or()


def test_expanding_low_efficiency_disorder_activates_expansion_channel() -> None:
    result = assess_positive_instability(
        evidence(
            width="TRUE",
            expansion="TRUE",
            low_efficiency="TRUE",
            low_traversal="FALSE",
            high_counter="FALSE",
        )
    )
    assert result.status == "TRUE"
    assert result.expanding_disorder_20.status == "TRUE"
    assert result.broad_reversal_disorder_20.status == "FALSE"
    assert result.activated_positive_channels == ("expanding_disorder_20",)


def test_monotonic_or_one_way_expansion_does_not_become_instability() -> None:
    result = assess_positive_instability(
        evidence(
            width="TRUE",
            expansion="TRUE",
            low_efficiency="FALSE",
            low_traversal="FALSE",
            high_counter="FALSE",
        )
    )
    assert result.status == "FALSE"
    assert result.activated_positive_channels == ()


def test_contracting_broad_reversal_can_be_instability_without_expansion() -> None:
    result = assess_positive_instability(
        evidence(
            width="TRUE",
            expansion="FALSE",
            low_efficiency="TRUE",
            low_traversal="FALSE",
            high_counter="TRUE",
        )
    )
    assert result.status == "TRUE"
    assert result.expanding_disorder_20.status == "FALSE"
    assert result.broad_reversal_disorder_20.status == "TRUE"


def test_zero_net_broad_oscillation_does_not_invent_counter_direction() -> None:
    result = assess_positive_instability(
        evidence(
            width="TRUE",
            expansion="FALSE",
            low_efficiency="TRUE",
            low_traversal="TRUE",
            high_counter="UNKNOWN",
        )
    )
    assert result.status == "TRUE"
    broad = result.broad_reversal_disorder_20
    assert broad.status == "TRUE"
    assert broad.inputs["reversal_or_roundtrip_20"] == "TRUE"


def test_tiny_low_efficiency_noise_and_broad_stable_box_do_not_assert_instability() -> None:
    tiny = assess_positive_instability(
        evidence(
            width="FALSE",
            expansion="FALSE",
            low_efficiency="TRUE",
            low_traversal="TRUE",
            high_counter="TRUE",
        )
    )
    assert tiny.status == "FALSE"
    broad_stable = assess_positive_instability(
        evidence(
            width="TRUE",
            expansion="FALSE",
            low_efficiency="FALSE",
            low_traversal="FALSE",
            high_counter="FALSE",
        )
    )
    assert broad_stable.status == "FALSE"


def test_unknown_inputs_remain_unknown_without_residual_stage0() -> None:
    result = assess_positive_instability(
        evidence(
            width="TRUE",
            expansion="UNKNOWN",
            low_efficiency="TRUE",
            low_traversal="FALSE",
            high_counter="UNKNOWN",
        )
    )
    assert result.status == "UNKNOWN"
    projected = project_atomic_primary(
        {
            "structural_instability_20": result.status,
            "coherent_directional_relocation_20": "FALSE",
            "orderly_consolidation_20": "FALSE",
        }
    )
    assert projected["status"] == "AMBIGUOUS"


def test_center_shift_is_not_in_truth_function_and_cannot_mutate_status() -> None:
    parameters = inspect.signature(assess_positive_instability).parameters
    assert "center_shift" not in parameters
    assert "range_center_shift_20_vs_prior20" not in parameters
    result = assess_positive_instability(
        evidence(
            width="FALSE",
            expansion="FALSE",
            low_efficiency="TRUE",
            low_traversal="TRUE",
            high_counter="TRUE",
        )
    )
    low = attach_modifiers(result, range_center_shift_20_vs_prior20="0.01")
    high = attach_modifiers(result, range_center_shift_20_vs_prior20="0.50")
    assert low["status_after_modifiers"] == "FALSE"
    assert high["status_after_modifiers"] == "FALSE"
    assert low["status"] == high["status"]


def test_mixed_instability_and_relocation_stays_outside_stage0() -> None:
    result = assess_positive_instability(
        evidence(
            width="TRUE",
            expansion="TRUE",
            low_efficiency="TRUE",
            low_traversal="FALSE",
            high_counter="TRUE",
        )
    )
    projected = project_atomic_primary(
        {
            "structural_instability_20": result.status,
            "coherent_directional_relocation_20": "TRUE",
            "orderly_consolidation_20": "FALSE",
        }
    )
    assert projected["status"] == "NEITHER"
    assert "mixed_directional_instability" in projected["reason_codes"]


def test_assessment_emits_channels_and_no_opaque_score() -> None:
    result = assess_positive_instability(
        evidence(
            width="TRUE",
            expansion="TRUE",
            low_efficiency="TRUE",
            low_traversal="TRUE",
            high_counter="TRUE",
        )
    ).to_dict()
    assert result["status"] == "TRUE"
    assert set(result["activated_positive_channels"]) == {
        "expanding_disorder_20",
        "broad_reversal_disorder_20",
    }
    assert result["opaque_score"] is None
    assert result["center_shift_truth_authority"] is False
