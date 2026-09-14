from __future__ import annotations

from datetime import date

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.complete_reserve_additional_review_prepare import (
    CompleteReserveAdditionalReviewPrepareError,
    _exact_selection,
    _runtime_rows,
    load_config,
)

ROOT = find_project_root()
CONFIG = ROOT / "config/stage-learning/stage-complete-reserve-additional-review-prepare.v1.json"


def test_config_freezes_exact_sixty_blind_review_prepare() -> None:
    value = load_config(CONFIG)
    assert value["work_item"] == "P3-STAGE-015-C1"
    assert value["state"] == "PREREGISTERED_EXACT_60_PRIMARY_BLIND_REVIEW_PREPARE"
    resource = value["resource_set"]
    assert resource["row_count"] == 60
    assert resource["all_matching_rows_included"] is True
    assert resource["case_t"] == "exact_window_end_only"
    assert resource["open_sessions_per_window"] == 61
    assert resource["fallback_or_replacement"] is False
    assert resource["semantic_selection"] is False
    assert resource["positive_count_stop"] is False
    prepare = value["review_prepare"]
    assert prepare["expected_case_count"] == 60
    assert prepare["publish_evidence"] is True
    assert prepare["publish_directional_path_facts"] is True
    assert prepare["publish_era_board_stratum"] is True
    assert prepare["publish_threshold_basis"] is False
    assert prepare["publish_candidate_grid"] is False
    assert prepare["publish_envelope_measurements"] is False
    assert prepare["read_existing_p014_opinions"] is False
    review = value["review"]
    assert review["roles"] == ["short_term", "fund"]
    assert review["same_origin_simulation"] is True
    assert review["candidate_output_visible"] is False
    assert review["threshold_basis_visible"] is False
    assert review["envelope_measurement_visible"] is False
    assert review["existing_p014_opinions_visible"] is False
    assert review["preserved_2021_visible"] is False
    assert value["runtime"] == {
        "maximum_jobs": 1,
        "automatic_retries": 0,
        "worker_wall_seconds": 3600,
    }


def _resource(index: int) -> dict:
    return {
        "security_id": f"security_{index:04d}",
        "security_code": f"{index:06d}",
        "era": "PRE_STAR",
        "board_family": "MAIN",
        "cell": "PRE_STAR/MAIN",
        "window_id": f"PRE_STAR|MAIN|{index:02d}",
        "window_start": "2014-01-01",
        "window_end": "2014-04-01",
        "plan_slot": index + 1,
        "plan_role": "fallback",
        "readiness_source_member_closure_digest": "sha256:" + f"{index:064x}"[-64:],
    }


def test_runtime_rows_and_selection_are_exact_not_semantic() -> None:
    rows = [_resource(index) for index in range(60)]
    runtime = _runtime_rows(rows)
    assert len(runtime) == 60
    assert {row["open_session_count"] for row in runtime} == {61}
    features = [
        {
            "security_code": row["security_code"],
            "market_date": date(2014, 4, 1),
            "range_width20": "0.1",
            "width_change20": "-0.2",
            "center_shift20_abs": "0.01",
            "efficiency20_abs": "0.05",
            "range_traversal20": "0.2",
            "counter_direction_excursion_share20": "0.8",
        }
        for row in rows
    ]
    selected = _exact_selection(runtime, features)
    assert len(selected) == 60
    assert [row["security_id"] for row in selected] == [row["security_id"] for row in rows]
    assert {row["market_date"] for row in selected} == {"2014-04-01"}


def test_exact_selection_fails_closed_if_readiness_drifts() -> None:
    rows = _runtime_rows([_resource(index) for index in range(60)])
    with pytest.raises(CompleteReserveAdditionalReviewPrepareError, match="readiness_drift"):
        _exact_selection(rows, [])
