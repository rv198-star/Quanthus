from __future__ import annotations

from decimal import Decimal

import pytest

from quanthus.stage_learning import orderly_consolidation_joint_development as api
from quanthus.stage_learning.orderly_consolidation_joint_development import (
    joint_candidate_grid,
    load_execution,
    load_preregistration,
    pair_passes,
    select_development_pair,
    stage2_candidate_grid,
    threshold_basis,
)
from quanthus.stage_learning.positive_instability_quantitative_development import (
    candidate_grid as stage0_candidate_grid,
)


def rows() -> list[dict[str, str]]:
    output = []
    for index in range(1, 81):
        width = Decimal(index + 10) / Decimal("500")
        change = Decimal(index - 40) / Decimal("25")
        output.append(
            {
                "range_width20": str(width),
                "width_change20": str(change),
                "efficiency20_abs": str(Decimal(index) / Decimal("100")),
                "range_traversal20": str(Decimal(index) / Decimal("100")),
                "counter_direction_excursion_share20": str(
                    Decimal(81 - index) / Decimal("100")
                ),
            }
        )
    return output


def test_preregistration_and_execution_freeze_exact_joint_surface() -> None:
    prereg = load_preregistration()
    execution = load_execution()
    surface = prereg["joint_candidate_surface"]
    assert surface["stage0_profile_count"] == 9
    assert surface["stage2_profile_count"] == 3
    assert surface["pair_count"] == 27
    assert surface["grid_expansion_after_result"] is False
    assert execution["pair_identity"]["pair_count"] == 27
    assert execution["authority"]["market_data_read"] is False
    assert execution["authority"]["runtime_job_submission"] is False


def test_joint_threshold_basis_extends_p010_without_labels() -> None:
    basis = threshold_basis(rows())
    assert basis["labels_used"] is False
    assert basis["reference_opinions_used"] is False
    assert basis["stage0_basis_topology"] == "P3-STAGE-010_EXACT"
    assert basis["positive_contraction_magnitude_observation_count"] == 39
    assert set(basis["positive_contraction_magnitude20"]) == {"0.30", "0.50", "0.70"}
    assert set(basis["range_width20"]) >= {"0.20", "0.30", "0.40", "0.50", "0.60"}


def test_stage2_grid_is_exactly_three_bundled_profiles() -> None:
    basis = threshold_basis(rows())
    candidates = stage2_candidate_grid(basis)
    assert len(candidates) == 3
    assert {item.order_profile_id for item in candidates} == {
        "order-lenient",
        "order-balanced",
        "order-strict",
    }
    assert len({item.candidate_id for item in candidates}) == 3


def test_joint_grid_reuses_exact_p010_stage0_candidates_and_has_27_unique_pairs() -> None:
    basis = threshold_basis(rows())
    expected_stage0_ids = {item.candidate_id for item in stage0_candidate_grid(basis)}
    pairs = joint_candidate_grid(basis)
    assert len(pairs) == 27
    assert len({pair.pair_id for pair in pairs}) == 27
    assert {pair.stage0.candidate_id for pair in pairs} == expected_stage0_ids
    assert len({pair.stage2.candidate_id for pair in pairs}) == 3


def _metrics(
    *, precision: str, coverage: str, fpr: str, fp: int = 1
) -> dict[str, object]:
    return {
        "case_count": 192,
        "complete_reference_count": 190,
        "definite_positive_references": 30,
        "definite_negative_references": 120,
        "ambiguous_references": 40,
        "input_unavailable_references": 2,
        "true_positive": 24,
        "false_positive": fp,
        "false_negative": 6,
        "predictions": 25,
        "precision": Decimal(precision),
        "positive_reference_coverage": Decimal(coverage),
        "definite_negative_false_positive_rate": Decimal(fpr),
    }


def test_pair_gates_require_both_classes_zero_overlap_and_stage0_no_regression() -> None:
    gates = load_preregistration()["development_gates"]
    baseline = _metrics(precision="0.85", coverage="0.80", fpr="0.08", fp=4)
    observed = {
        "stage_0": _metrics(precision="0.90", coverage="0.75", fpr="0.05", fp=2),
        "stage_2": _metrics(precision="0.85", coverage="0.70", fpr="0.07", fp=3),
        "proposition_overlap_count": 0,
    }
    assert pair_passes(observed, baseline, gates) is True
    assert pair_passes({**observed, "proposition_overlap_count": 1}, baseline, gates) is False
    weak_stage2 = {
        **observed,
        "stage_2": _metrics(precision="0.85", coverage="0.64", fpr="0.07", fp=3),
    }
    assert pair_passes(weak_stage2, baseline, gates) is False
    regressed_stage0 = {
        **observed,
        "stage_0": _metrics(precision="0.84", coverage="0.75", fpr="0.05", fp=2),
    }
    assert pair_passes(regressed_stage0, baseline, gates) is False


def test_pair_gates_fail_closed_when_stage0_baseline_metric_is_unavailable() -> None:
    gates = load_preregistration()["development_gates"]
    baseline = _metrics(precision="0.85", coverage="0.80", fpr="0.08", fp=4)
    baseline["precision"] = None
    observed = {
        "stage_0": _metrics(precision="0.90", coverage="0.75", fpr="0.05", fp=2),
        "stage_2": _metrics(precision="0.85", coverage="0.70", fpr="0.07", fp=3),
        "proposition_overlap_count": 0,
    }
    with pytest.raises(
        api.OrderlyConsolidationJointDevelopmentError,
        match="stage0_baseline_metric_unavailable",
    ):
        pair_passes(observed, baseline, gates)


def test_selection_order_is_frozen_before_fresh_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    basis = threshold_basis(rows())
    pairs = joint_candidate_grid(basis)[:3]
    baseline = _metrics(precision="0.85", coverage="0.80", fpr="0.08", fp=4)
    trial_metrics = {
        pairs[0].pair_id: {
            "stage_0": _metrics(precision="0.91", coverage="0.76", fpr="0.04"),
            "stage_2": _metrics(precision="0.86", coverage="0.72", fpr="0.05"),
            "proposition_overlap_count": 0,
        },
        pairs[1].pair_id: {
            "stage_0": _metrics(precision="0.90", coverage="0.78", fpr="0.03"),
            "stage_2": _metrics(precision="0.88", coverage="0.70", fpr="0.04"),
            "proposition_overlap_count": 0,
        },
        pairs[2].pair_id: {
            "stage_0": _metrics(precision="0.95", coverage="0.80", fpr="0.02"),
            "stage_2": _metrics(precision="0.87", coverage="0.74", fpr="0.03"),
            "proposition_overlap_count": 0,
        },
    }
    monkeypatch.setattr(api, "joint_candidate_grid", lambda _basis: pairs)
    monkeypatch.setattr(api, "_stage0_baseline_metrics", lambda _cases, _refs: baseline)
    monkeypatch.setattr(
        api,
        "pair_metrics",
        lambda _cases, _refs, pair: trial_metrics[pair.pair_id],
    )
    monkeypatch.setattr(api, "pair_passes", lambda _metrics, _baseline, _gates: True)
    result = select_development_pair([], {}, basis)
    # Pair 1 wins because its weaker precision is 0.88, above 0.87 and 0.86.
    assert result["pair"]["pair_id"] == pairs[1].pair_id
    assert result["trial_count"] == 3
    assert result["eligible_count"] == 3
