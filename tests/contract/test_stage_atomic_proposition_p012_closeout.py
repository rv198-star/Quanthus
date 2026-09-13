"""P012 fail-closeout evidence and synthetic pre-data capacity-stop conformance."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning import atomic_proposition_development_prepare as prepare
from quanthus.stage_learning.positive_instability_development_prepare import (
    PositiveInstabilityPrepareError,
    _fresh_pool,
)
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
CLOSEOUT = ROOT / "config/stage-learning/stage-atomic-proposition-p012-closeout.v1.json"
ORIGINAL_REVISION = "0e689614c6a458c89934c02c23177eecf7142084"


def _result() -> dict:
    return json.loads(CLOSEOUT.read_text(encoding="utf-8"))


def _members(count: int) -> list[dict[str, str]]:
    return [
        {"security_code": f"{index:06d}", "security_id": f"synthetic-identity-{index}"}
        for index in range(count)
    ]


def test_closeout_preserves_frozen_contract_digests() -> None:
    value = _result()
    for name in ("preregistration", "execution", "freshness_inventory", "evidence_scope"):
        binding = value["immutable_bindings"][name]
        current = json.loads((ROOT / binding["path"]).read_text(encoding="utf-8"))
        assert digest(current) == binding["digest"], name
    authority = value["authority"]
    assert digest(json.loads((ROOT / authority["path"]).read_text())) == authority["digest"]


def test_closeout_retains_failed_receipt_without_promotion_or_retry() -> None:
    value = _result()
    assert value["state"] == "CLOSED_INCONCLUSIVE_FRESHNESS_CAPACITY"
    receipt = value["original_job_receipt"]
    assert receipt["job_id"] == "stage_6cccde81c6f24fae949f947a3209cd25"
    assert receipt["code_revision"] == ORIGINAL_REVISION
    assert receipt["state"] == "failed"
    assert receipt["exit_code"] == 1
    assert receipt["result_ref"] is None
    assert value["attribution"]["required_fresh_pool_size"] == 512
    assert value["attribution"]["observed_eligible_count_exact"] is None
    assert value["attribution"]["technical_recovery_admissible_on_current_evidence"] is False
    assert value["budgets_and_boundaries"]["original_g1_submissions"] == 1
    for name in (
        "technical_retries", "semantic_retries", "new_candidates_or_profiles", "gate_changes",
    ):
        assert value["budgets_and_boundaries"][name] == 0
    for name in ("original_budget_reset", "preserved_2021_read_authorized",
                 "permanent_blind_2025_access", "daily_strategy_trading_production_activation"):
        assert value["budgets_and_boundaries"][name] is False
    for name in ("market_qualification", "stage_qualification", "new_runtime_binding",
                 "exact_projection_frozen", "relocation_retuned"):
        assert value["qualification"][name] is False
    assert value["qualification"]["selected_instability_candidates"] == 0
    assert value["qualification"]["selected_order_profiles"] == 0
    for phase in ("G2", "G3", "H", "I"):
        assert value["phase_disposition"][phase] == "NOT_EXECUTED_UPSTREAM_STOP"
    assert value["review"]["method"] == "same_origin_four_role_simulation"
    assert value["review"]["independent_model_review_claimed"] is False
    assert value["review"]["human_expert_review_claimed"] is False
    assert value["review"]["primary_case_review_performed"] is False
    assert value["successor_design_disposition"]["automatic_new_market_job_authorized"] is False


def test_active_navigation_has_no_ready_g1_claim() -> None:
    for relative in (
        "docs/README.md",
        "docs/work-items/index.md",
        "docs/work-items/stage-atomic-proposition-successor.md",
        "docs/design/stage-atomic-proposition-successor-concept.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "CLOSED_INCONCLUSIVE_FRESHNESS_CAPACITY" in text, relative
        assert "EXACT ONE G1 PREPARE READY" not in text, relative
        assert "exact one-shot G1 prepare ready" not in text, relative


@pytest.mark.parametrize("seed", ("synthetic-capacity-a", "synthetic-capacity-b"))
def test_fresh_pool_rejects_511_without_reseed_relief(seed: str) -> None:
    with pytest.raises(PositiveInstabilityPrepareError, match="^fresh_pool_capacity_insufficient$"):
        _fresh_pool(_members(511), excluded_ids=set(), seed=seed, size=512)


def test_fresh_pool_exact_boundary_and_exclusions_are_enforced() -> None:
    members = _members(512)
    assert len(_fresh_pool(members, excluded_ids=set(), seed="synthetic-boundary", size=512)) == 512
    with pytest.raises(PositiveInstabilityPrepareError, match="fresh_pool_capacity_insufficient"):
        _fresh_pool(
            members, excluded_ids={"synthetic-identity-0"}, seed="synthetic-boundary", size=512,
        )
    ambiguous = members + [{"security_code": "000000", "security_id": "synthetic-alternate"}]
    with pytest.raises(PositiveInstabilityPrepareError, match="fresh_pool_capacity_insufficient"):
        _fresh_pool(ambiguous, excluded_ids=set(), seed="synthetic-boundary", size=512)


def test_g1_capacity_stop_precedes_all_result_bearing_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use only synthetic identity inputs; this is not a rerun of the real G1 Job."""
    members = _members(511)
    monkeypatch.setattr(prepare, "find_project_root", lambda: ROOT)
    monkeypatch.setattr(
        prepare, "_verify_isolation",
        lambda **kwargs: ({}, set(), {"identities": [{"security_id": "synthetic-preserved"}]}),
    )
    monkeypatch.setattr(
        prepare, "discover_development_security_population",
        lambda **kwargs: tuple(item["security_code"] for item in members),
    )
    monkeypatch.setattr(prepare, "read_development_identity_members", lambda **kwargs: members)
    forbidden_calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        forbidden_calls.append("result-bearing-stage")
        raise AssertionError("result-bearing stage reached after failed capacity gate")

    for name in (
        "read_development_admissions", "build_order_observations", "_path_map",
        "threshold_basis", "instability_candidate_grid", "stage2_candidate_grid",
        "select_development_cases", "build_evidence", "_envelope_measurements",
    ):
        monkeypatch.setattr(prepare, name, forbidden)
    output = tmp_path / "never-published"
    with pytest.raises(PositiveInstabilityPrepareError, match="fresh_pool_capacity_insufficient"):
        prepare.run_prepare(
            config_path=ROOT / prepare.DEFAULT_CONFIG,
            training_root=tmp_path,
            output_root=output,
            code_revision=ORIGINAL_REVISION,
        )
    assert forbidden_calls == []
    assert not output.exists()
