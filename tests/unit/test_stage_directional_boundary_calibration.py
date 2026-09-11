from __future__ import annotations

import json

from quanthus.policy import find_project_root
from quanthus.stage_learning.directional_boundary_calibration import load_ledger, resolve_references
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
CANDIDATE = ROOT / "config/stage-learning/directional-boundary-candidate.v1.json"


def test_frozen_calibration_ledger_is_complete_and_candidate_blind() -> None:
    ledger = load_ledger()
    assert len(ledger["rows"]) == 64
    assert len({row["case_id"] for row in ledger["rows"]}) == 64
    assert ledger["sha256"] == (
        "sha256:f147090c7d6f814e89b652570a89d9aa805b9a0ff34913ab258135ce9356c070"
    )
    metadata = ledger["metadata"]
    assert metadata["candidate_output_seen_during_review"] == "false"
    assert metadata["explicit_recent_derived_facts_seen"] == "false"
    assert metadata["cognitive_blinding_claimed"] == "false"


def test_role_resolution_retains_five_primary_disagreements() -> None:
    ledger = load_ledger()
    cases = [{"case_id": row["case_id"], "primary_available": True} for row in ledger["rows"]]
    resolved = resolve_references(cases, ledger)
    assert resolved["primary_reference_counts"] == {
        "AMBIGUOUS": 5,
        "NEITHER": 15,
        "STAGE_0": 28,
        "STAGE_2": 16,
    }
    assert resolved["primary_disagreement_count"] == 5


def test_candidate_freeze_binds_one_of_twenty_seven_and_opens_only_d3_evaluation_read() -> None:
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    assert digest(candidate) == (
        "sha256:c815077233ff837e7a8d0ebd884b12dcfb3d6d5c31abfe302cefe97e6997b3ff"
    )
    assert candidate["calibration_search"]["candidate_count"] == 27
    assert candidate["calibration_search"]["eligible_count"] == 1
    assert candidate["d1_prepare"]["evaluation_data_read_at_candidate_freeze"] is False
    assert candidate["frozen_candidate"] == {
        "candidate_id": "sha256:d11c368d4111ebf991315a6c61b800a6737d56e5f81792cf153dabcda7cb52fd",
        "absolute_window_return20_min": "0.10",
        "range_traversal20_min": "0.50",
        "counter_direction_excursion_share20_max": "0.60",
        "calibration_metrics": {
            "definite_positive_references": 28,
            "definite_negative_references": 31,
            "ambiguous_references": 5,
            "true_positive": 28,
            "false_positive": 3,
            "false_negative": 0,
            "stage_0_predictions": 31,
            "precision": "0.9032258064516129032258064516",
            "positive_reference_coverage": "1",
            "definite_negative_false_positive_rate": "0.09677419354838709677419354839",
        },
    }
    authority = candidate["evaluation_authority"]
    assert authority["d3_read_exact_frozen_2021_evaluation_identities"] is True
    assert authority["evaluation_parameter_search"] is False
    assert authority["candidate_replacement_after_evaluation_read"] is False
    assert authority["2022_2024_access"] is False
    assert authority["2025_access"] is False
