from __future__ import annotations

from decimal import Decimal

from quanthus.stage_learning.atomic_proposition_development import load_preregistration
from quanthus.stage_learning.combined_atomic_scoring import (
    _macro_atomic_metrics,
    _passes_atomic_macro_gate,
    _reference_counts,
    _resolved_reference,
    load_config,
)

PROPOSITION = "structural_instability_20"


def _eight_cell_fixture() -> tuple[list[str], dict, dict, dict]:
    case_ids: list[str] = []
    references = {}
    predictions = {}
    strata = {}
    for cell_index in range(8):
        cell = f"ERA{cell_index % 2}/BOARD{cell_index}"
        positive = f"p{cell_index}"
        negative = f"n{cell_index}"
        case_ids.extend((positive, negative))
        references[positive] = {PROPOSITION: "TRUE"}
        references[negative] = {PROPOSITION: "FALSE"}
        predictions[positive] = "TRUE" if cell_index < 4 else "FALSE"
        predictions[negative] = "FALSE"
        for case_id in (positive, negative):
            strata[case_id] = {
                "era": f"ERA{cell_index % 2}",
                "board_family": f"BOARD{cell_index}",
                "cell": cell,
            }
    return case_ids, references, predictions, strata


def test_p016_config_freezes_macro_rate_pooled_count_without_projection() -> None:
    value = load_config()
    assert value["work_item"] == "P3-STAGE-016-A"
    surface = value["candidate_surface"]
    assert surface["instability_candidate_count"] == 9
    assert surface["relocation_candidate_count"] == 1
    assert surface["order_profile_count"] == 3
    assert surface["joint_pair_search_count"] == 0
    assert surface["candidate_expansion"] is False
    assert surface["threshold_recompute"] is False
    aggregate = value["aggregate_contract"]
    assert aggregate["count_gates"] == "pooled_counts_exact_P012"
    assert aggregate["rate_gates"] == (
        "macro_cell_equal_weight_same_numeric_thresholds_as_P012"
    )
    assert aggregate["precision"]["zero_prediction_cell_value"] == "0"
    authority = value["authority"]
    assert authority["candidate_scoring"] is True
    assert authority["projection_compatibility"] is False
    assert authority["dwd_market_read"] is False
    assert authority["preserved_2021_read"] is False
    assert authority["2025_read"] is False


def test_macro_precision_penalizes_silent_cells_while_micro_remains_descriptive() -> None:
    case_ids, references, predictions, strata = _eight_cell_fixture()
    observed = _macro_atomic_metrics(
        case_ids=case_ids,
        references=references,
        predictions=predictions,
        proposition=PROPOSITION,
        strata=strata,
    )
    assert observed["pooled"]["precision"] == Decimal("1")
    assert observed["pooled"]["positive_reference_coverage"] == Decimal("0.5")
    assert observed["macro"]["precision"] == Decimal("0.5")
    assert observed["macro"]["positive_reference_coverage"] == Decimal("0.5")
    assert observed["macro"]["definite_negative_false_positive_rate"] == Decimal("0")
    assert observed["macro"]["precision_cell_count"] == 8
    assert observed["macro"]["coverage_cell_count"] == 8
    assert observed["macro"]["fpr_cell_count"] == 8


def test_macro_coverage_uses_only_cells_with_positive_reference_denominator() -> None:
    case_ids, references, predictions, strata = _eight_cell_fixture()
    for cell_index in range(4, 8):
        references[f"p{cell_index}"][PROPOSITION] = "FALSE"
    observed = _macro_atomic_metrics(
        case_ids=case_ids,
        references=references,
        predictions=predictions,
        proposition=PROPOSITION,
        strata=strata,
    )
    assert observed["macro"]["positive_reference_coverage"] == Decimal("1")
    assert observed["macro"]["coverage_cell_count"] == 4
    assert observed["macro"]["precision_cell_count"] == 8


def test_macro_gate_keeps_p012_pooled_count_minima() -> None:
    case_ids, references, predictions, strata = _eight_cell_fixture()
    observed = _macro_atomic_metrics(
        case_ids=case_ids,
        references=references,
        predictions=predictions,
        proposition=PROPOSITION,
        strata=strata,
    )
    assert _passes_atomic_macro_gate(
        metrics=observed,
        proposition=PROPOSITION,
        prereg=load_preregistration(),
    ) is False
    # The synthetic fixture fails the unchanged pooled P012 reference/prediction count minima even
    # though the macro-rate implementation itself is well-defined.
    assert observed["pooled"]["complete_reference_count"] == 16


def test_reference_resolution_matches_p012_two_role_semantics() -> None:
    row = {
        "short_term": {
            "structural_instability_20": "TRUE",
            "coherent_directional_relocation_20": "FALSE",
            "orderly_consolidation_20": "UNKNOWN",
        },
        "fund": {
            "structural_instability_20": "TRUE",
            "coherent_directional_relocation_20": "TRUE",
            "orderly_consolidation_20": "UNKNOWN",
        },
    }
    resolved = _resolved_reference(row)
    assert resolved["structural_instability_20"] == "TRUE"
    assert resolved["coherent_directional_relocation_20"] == "AMBIGUOUS"
    assert resolved["orderly_consolidation_20"] == "AMBIGUOUS"
    assert resolved["projected_primary_reference"] == "AMBIGUOUS"

    counts = _reference_counts({"case": resolved})
    assert counts["atomic"]["structural_instability_20"] == {"TRUE": 1}
    assert counts["atomic"]["coherent_directional_relocation_20"] == {"AMBIGUOUS": 1}
    assert counts["projected"] == {"AMBIGUOUS": 1}
