from __future__ import annotations

from decimal import Decimal

from quanthus.stage_learning import positive_instability_development as api


def _case(
    case_id: str,
    closes: list[str],
    *,
    efficiency: str = "0.10",
    width: str = "0.30",
    width_change: str = "0.30",
    center: str = "0.01",
) -> dict:
    assert len(closes) == 21
    return {
        "case_id": case_id,
        "primary_available": True,
        "primary": {
            "directional_efficiency": {"reason": "available", "value": efficiency},
            "range_width": {"reason": "available", "value": width},
            "width_change": {"reason": "available", "value": width_change},
            "center_shift": {"reason": "available", "value": center},
        },
        "history": [
            [
                f"sha256:{index:064x}",
                f"2020-01-{index + 1:02d}",
                value,
                value,
                value,
                "epoch",
                None,
            ]
            for index, value in enumerate(closes)
        ],
    }


def _candidate(
    *,
    width: str = "0.20",
    expansion: str = "0.20",
    efficiency: str = "0.20",
    traversal: str = "0.30",
    counter: str = "0.70",
) -> api.InstabilityCandidate:
    return api.InstabilityCandidate(
        "core-test",
        "expansion-test",
        "reversal-test",
        Decimal(width),
        Decimal(expansion),
        Decimal(efficiency),
        Decimal(traversal),
        Decimal(counter),
    )


def test_nearest_rank_is_decimal_ceiling_without_interpolation() -> None:
    values = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")]
    assert api.nearest_rank(values, Decimal("0.50")) == Decimal("3")
    assert api.nearest_rank(values, Decimal("0.60")) == Decimal("3")
    assert api.nearest_rank(values, Decimal("0.61")) == Decimal("4")
    assert api.nearest_rank(values, Decimal("1")) == Decimal("5")


def test_threshold_basis_is_unlabelled_positive_width_only_and_excludes_unavailable_counter(
) -> None:
    rows = [
        {
            "range_width20": "0.10",
            "width_change20": "-0.50",
            "efficiency20_abs": "0.10",
            "range_traversal20": "0.20",
            "counter_direction_excursion_share20": None,
        },
        {
            "range_width20": "0.20",
            "width_change20": "0.10",
            "efficiency20_abs": "0.20",
            "range_traversal20": "0.30",
            "counter_direction_excursion_share20": "0.40",
        },
        {
            "range_width20": "0.30",
            "width_change20": "0.20",
            "efficiency20_abs": "0.30",
            "range_traversal20": "0.40",
            "counter_direction_excursion_share20": "0.80",
        },
        {
            "range_width20": "0.40",
            "width_change20": "0.40",
            "efficiency20_abs": "0.40",
            "range_traversal20": "0.50",
            "counter_direction_excursion_share20": "0.90",
        },
    ]
    basis = api.threshold_basis(rows)
    assert basis["labels_used"] is False
    assert basis["observation_count"] == 4
    assert basis["positive_width_change_observation_count"] == 3
    assert basis["counter_excursion_available_observation_count"] == 3
    assert basis["range_width20"]["0.50"] == "0.20"
    assert basis["positive_width_change20"]["0.50"] == "0.20"
    assert basis["counter_direction_excursion_share20"]["0.70"] == "0.90"


def test_candidate_grid_is_exactly_27_unique_candidates() -> None:
    basis = {
        "range_width20": {"0.50": "0.10", "0.60": "0.20", "0.70": "0.30"},
        "positive_width_change20": {"0.50": "0.10", "0.65": "0.20", "0.80": "0.30"},
        "efficiency20_abs": {"0.50": "0.50", "0.40": "0.40", "0.30": "0.30"},
        "range_traversal20": {"0.50": "0.50", "0.40": "0.40", "0.30": "0.30"},
        "counter_direction_excursion_share20": {
            "0.50": "0.50",
            "0.60": "0.60",
            "0.70": "0.70",
        },
    }
    grid = api.candidate_grid(basis)
    assert len(grid) == 27
    assert len({candidate.candidate_id for candidate in grid}) == 27


def test_zero_net_path_maps_counter_excursion_to_unknown_but_broad_reversal_can_be_true() -> None:
    closes = ["100", "106", "94"] * 6 + ["100", "106", "100"]
    case = _case(
        "zero-net",
        closes,
        efficiency="0.05",
        width="0.30",
        width_change="-0.30",
    )
    candidate = _candidate(width="0.20", efficiency="0.10", traversal="0.30", counter="0.70")
    primitive = api.primitive_evidence_for_case(case, candidate)
    assert primitive.high_counter_direction_excursion_20 == "UNKNOWN"
    assert primitive.low_range_traversal_20 == "TRUE"
    assert api.candidate_instability(case, candidate) == "TRUE"
    assert api.candidate_stage0(case, candidate) is True


def test_center_shift_cannot_rescue_contraction_without_broad_reversal_evidence() -> None:
    closes = [str(100 + index) for index in range(21)]
    case = _case(
        "center-only",
        closes,
        efficiency="0.10",
        width="0.12",
        width_change="-0.40",
        center="0.50",
    )
    candidate = _candidate(width="0.20", expansion="0.20", efficiency="0.20")
    assert api.candidate_instability(case, candidate) == "FALSE"
    assert api.candidate_stage0(case, candidate) is False


def test_coherent_relocation_stays_outside_stage0_even_if_expansion_channel_is_true() -> None:
    closes = [str(100 + 2 * index) for index in range(21)]
    case = _case(
        "relocation",
        closes,
        efficiency="0.10",
        width="0.35",
        width_change="0.40",
    )
    candidate = _candidate(width="0.20", expansion="0.20", efficiency="0.20")
    assert api.candidate_instability(case, candidate) == "TRUE"
    assert api.relocation_guard(case, api.FROZEN_008_RELOCATION) is True
    assert api.candidate_stage0(case, candidate) is False


def test_candidate_instability_stage2_overlap_is_measured_and_fails_gate() -> None:
    closes = ["100", "104", "96"] * 6 + ["100", "104", "100"]
    case = _case(
        "stage2-overlap",
        closes,
        efficiency="0.10",
        width="0.10",
        width_change="-0.30",
        center="0.01",
    )
    candidate = _candidate(width="0.05", expansion="0.20", efficiency="0.20", traversal="0.30")
    references = {"stage2-overlap": "STAGE_2"}
    metrics = api.stage0_metrics([case], references, candidate=candidate)
    assert api.unchanged_stage2(case) is True
    assert api.candidate_instability(case, candidate) == "TRUE"
    assert metrics["candidate_instability_stage2_overlap_count"] == 1
    gates = {
        "minimum_complete_cases": 1,
        "minimum_definite_stage_0_positive_references": 0,
        "minimum_definite_stage_0_negative_references": 1,
        "minimum_stage_0_predictions": 0,
        "minimum_stage_0_precision": "0",
        "minimum_stage_0_positive_reference_coverage": "0",
        "maximum_stage_0_definite_negative_false_positive_rate": "1",
    }
    assert api._meets_absolute_gates(metrics, gates) is False


def test_execution_contract_freezes_selection_order_and_no_runtime_authority() -> None:
    execution = api.load_execution()
    assert execution["quantile_method"]["name"] == "nearest_rank_ceiling_1_based"
    assert execution["quantile_method"]["interpolation"] is False
    assert execution["selection_order"] == [
        "precision_desc",
        "definite_negative_false_positive_rate_asc",
        "positive_reference_coverage_desc",
        "candidate_id_asc",
    ]
    assert execution["stage_2_guard"]["candidate_instability_and_stage_2_positive_overlap_max"] == 0
    assert execution["authority"]["market_data_read"] is False
    assert execution["authority"]["runtime_job_submission"] is False
    assert execution["authority"]["parameter_search"] is False
