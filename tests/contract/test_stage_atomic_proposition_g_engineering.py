from __future__ import annotations

import json

from quanthus.policy import find_project_root
from quanthus.stage_learning import atomic_proposition_development as scoring
from quanthus.stage_learning import atomic_proposition_development_prepare as prepare
from quanthus.stage_learning.worker import (
    _JOB_SPECS,
    STAGE_ATOMIC_PROPOSITION_G1_JOB,
)
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
PREREG_DIGEST = "sha256:24aacc70ef7ffa3bbfd0e28e8aa33fd91ba51bbd65da6c13640926215987c54d"
EXECUTION_DIGEST = "sha256:5203b5c4a325b775722c90c7404fc149567e448cf63d39e731e22fcd6cebe098"
EVIDENCE_DIGEST = "sha256:6bbe9c56b850d6bb6ebe49766f2e5c9c2860def98fb06b54c5320ea672b6e32b"


def load(path: str) -> dict[str, object]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_g_contracts_bind_exact_prereg_execution_and_2016_scope() -> None:
    prereg = load(scoring.PREREG_PATH)
    execution = load(scoring.EXECUTION_PATH)
    evidence = load(prepare.EVIDENCE_SCOPE_PATH)
    assert digest(prereg) == PREREG_DIGEST == scoring.PREREG_DIGEST == prepare.PREREG_DIGEST
    assert (
        digest(execution)
        == EXECUTION_DIGEST
        == scoring.EXECUTION_DIGEST
        == prepare.EXECUTION_DIGEST
    )
    assert digest(evidence) == EVIDENCE_DIGEST == prepare.EVIDENCE_SCOPE_DIGEST
    assert execution["preregistration"]["digest"] == PREREG_DIGEST
    assert execution["evidence_protocol"]["digest"] == EVIDENCE_DIGEST
    assert evidence["history_start"] == "2015-09-01"
    assert evidence["case_start"] == "2016-01-01"
    assert evidence["development_end"] == "2016-12-31"


def test_g_is_atomic_first_and_has_no_joint_pair_search_or_retry() -> None:
    prereg = load(scoring.PREREG_PATH)
    assert prereg["instability_candidate_surface"]["selectable_candidate_count"] == 9
    assert prereg["relocation_surface"] == {
        "candidate_count": 1,
        "source": "exact_frozen_P008_relocation_guard",
        "threshold_retune": False,
        "parameter_search": False,
        "instability_truth_can_coexist": True,
    }
    assert prereg["order_candidate_surface"]["selectable_profile_count"] == 3
    assert prereg["order_candidate_surface"]["relocation_veto_inside_atomic_order_truth"] is False
    assert prereg["independent_selection"]["joint_pair_ranking_forbidden"] is True
    assert prereg["budgets"]["joint_pair_search_count"] == 0
    assert prereg["budgets"]["automatic_technical_retries"] == 0
    assert prereg["authority"]["preserved_2021_evaluation_read_before_exact_freeze"] is False


def test_g_blind_review_hides_envelope_and_selection_transport_is_later_only() -> None:
    prereg = load(scoring.PREREG_PATH)
    execution = load(scoring.EXECUTION_PATH)
    blind = prereg["review_blinding"]
    assert blind["registered_envelope_measurement_visible"] is False
    assert blind["raw_21_close_primary_path_visible"] is True
    assert blind["candidate_output_visible"] is False
    assert blind["threshold_basis_visible"] is False
    assert execution["review_transport"]["envelope_measurement"] is False
    assert execution["review_transport"]["candidate_outputs"] is False
    assert execution["review_transport"]["threshold_basis"] is False


def test_worker_and_restricted_helper_expose_only_exact_g1_submission() -> None:
    spec = _JOB_SPECS[STAGE_ATOMIC_PROPOSITION_G1_JOB]
    assert spec == {
        "config": "config/stage-learning/stage-atomic-proposition-development-screen.v1.json",
        "module": "quanthus.stage_learning.atomic_proposition_development_prepare",
        "artifact_glob": "stage-atomic-proposition-g1-*",
        "process_error": "stage_atomic_proposition_g1_process_failed",
        "wall_seconds": 2700,
    }
    helper = (ROOT / "ops/bin/quanthus-stage-agentdock-control").read_text(encoding="utf-8")
    assert 'p012_g1_kind="quanthus.stage-atomic-proposition-g1-prepare.v1"' in helper
    assert "submit-p012-g1)" in helper
    assert 'bash "${worker}" submit --kind "${p012_g1_kind}" --code-revision "${target}"' in helper
    assert "cancel)" not in helper
    assert "serve)" not in helper


def test_g_acceptance_exists_and_does_not_claim_market_read() -> None:
    path = ROOT / "docs/acceptance/stage-atomic-proposition-g-engineering-20260914.md"
    text = path.read_text(encoding="utf-8")
    assert "G ENGINEERING: ACCEPTED" in text
    assert "MARKET DATA READ IN THIS CHECKPOINT: 0" in text
    assert "PRESERVED 2021 READ: 0" in text
    assert "JOINT PAIR SEARCH: 0" in text
