from __future__ import annotations

import json

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
CONTRACT = ROOT / "config/stage-learning/positive-instability-conformance.v1.json"
RESULT = ROOT / "config/stage-learning/positive-instability-conformance-result.v1.json"
CONTRACT_DIGEST = "sha256:a979781a61ddb4807aff01033f55b9bdc6513b8303e8e7d8680d0966d33e3ce7"
RESULT_DIGEST = "sha256:4dd0011e7ee63e62fd3dd80483461744823cabed33a0033c945756f11d160f49"


def test_conformance_contract_is_diagnostic_only() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert digest(contract) == CONTRACT_DIGEST
    ceiling = contract["claim_ceiling"]
    assert ceiling == {
        "qualification": False,
        "threshold_learning": False,
        "candidate_selection": False,
        "new_market_data_read": False,
        "runtime_job": False,
        "2022_2024_access": False,
        "2025_access": False,
        "stage_2_change": False,
        "relocation_guard_retuning": False,
        "official_labels": False,
        "daily_or_strategy": False,
        "trading": False,
    }
    assert contract["metrics"]["numeric_cutoff_selection"] is False
    assert contract["metrics"]["parameter_trials"] == 0


def test_consumed_evidence_result_supports_two_channel_shape_without_thresholds() -> None:
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    assert digest(result) == RESULT_DIGEST
    assert result["status"] == "PASS_DIAGNOSTIC_CONFORMANCE_ONLY"
    assert result["source"]["case_count"] == 95
    assert result["source"]["read_only_stage_status_calls"] == 13
    true_group = result["fixed_groups"]["center_shift_only_true_stage0"]
    false_group = result["fixed_groups"]["center_shift_only_false_positive"]
    assert true_group["indices"] == [26, 48]
    assert false_group["indices"] == [30, 44, 46, 50, 53, 67, 76, 93]
    comparisons = result["comparisons"]
    assert comparisons and all(comparisons.values())
    stage0 = result["definite_stage0"]
    assert stage0["count"] == 15
    assert stage0["width_change_signs"] == {"positive": 13, "zero": 0, "negative": 2}
    assert stage0["negative_width_change_cases_are_center_shift_only_true_stage0_group"] is True
    ceiling = result["claim_ceiling"]
    assert ceiling["numeric_thresholds_selected"] is False
    assert ceiling["parameter_trials"] == 0
    assert ceiling["new_market_data_read"] is False
    assert ceiling["qualification_claim"] is False
    assert ceiling["stage_2_changed"] is False
    assert ceiling["relocation_guard_retuned"] is False
