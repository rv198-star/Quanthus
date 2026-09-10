from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.order_proposition_development import _gate
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import sync_stage_order_proposition_issues as sync  # noqa: E402


def _copy_plan(tmp_path, monkeypatch):
    raw = sync.load_plan()
    required = [
        sync.PLAN, raw["design"], raw["review"], raw["runtime_config"], raw["panel_config"],
        raw["technical_retry"]["evidence"], raw["runtime_qualification"]["evidence"],
    ]
    required.extend(path for item in raw["items"] for path in item["evidence"])
    for relative in set(required):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    monkeypatch.setattr(sync, "ROOT", tmp_path)
    return raw


def test_stage_0_2_plan_closes_execution_without_accepting_candidates() -> None:
    raw = sync.load_plan()
    assert [item["id"] for item in raw["items"]] == [
        "P3-STAGE-004", "P3-STAGE-004-A", "P3-STAGE-004-B",
        "P3-STAGE-004-C", "P3-STAGE-004-D",
    ]
    assert raw["runtime_accepted"] is True  # experiment execution, not label acceptance
    assert all(item["status"] == "closed" for item in raw["items"])
    result = raw["runtime_qualification"]
    assert result["decision"] == "INCONCLUSIVE_REFERENCE_SUFFICIENCY"
    assert result["development_candidate_accepted"] is False
    assert result["reference_sufficient"] is False
    assert result["full_trial_detail_readback"] is False
    assert result["selected_parameters"] == {"stage_0": None, "stage_2": None}
    assert result["selected_pair_overlap_count"] is None
    assert result["observation_count"] == 224346
    assert result["reference_summary"]["label_counts"] == {
        "stage_0": 69, "stage_2": 9, "ambiguous": 65, "neither": 17,
    }
    assert raw["technical_retry"]["authorized"] is False
    assert len(raw["execution_attempts"]) == 2
    assert raw["technical_retry"]["job_id"] == result["job_id"]
    for key, value in raw["authorization"].items():
        if key.endswith("_authorized"):
            assert value is False


@pytest.mark.parametrize("key", [
    "permanent_blind_access_authorized", "primary_stage_arbitration_authorized",
    "limited_parameter_search_authorized", "development_data_read_authorized",
])
def test_stage_0_2_closed_plan_rejects_reopening(tmp_path, monkeypatch, key):
    raw = _copy_plan(tmp_path, monkeypatch)
    raw["authorization"][key] = True
    (tmp_path / sync.PLAN).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="authority"):
        sync.load_plan()


def test_stage_0_2_runtime_freezes_finite_grid_and_fact_contracts() -> None:
    raw = json.loads(
        (ROOT / "config/stage-learning/order-proposition-development.v1.json").read_text()
    )
    assert digest(raw) == "sha256:894a38df99419cadcc24d1ef3fd775ffd6189609f593dc8d4487c5f4f08bdb80"
    assert raw["trend_fact_contract_digest"].startswith("sha256:")
    assert raw["order_fact_contract_digest"].startswith("sha256:")
    trial_counts = {}
    for stage, parameters in raw["search_space"].items():
        count = 1
        for values in parameters.values():
            count *= len(values)
        trial_counts[stage] = count
    assert trial_counts == {"stage_2": 81, "stage_0": 27}
    assert raw["hard_gates"]["maximum_pair_overlap_count"] == 0
    assert raw["selection"]["no_in_run_hot_fix"] is True
    assert raw["selection"]["no_search_space_expansion_after_results"] is True


@pytest.mark.parametrize(
    ("key", "changed"),
    [("maximum_additional_attempts", 2), ("semantic_changes_authorized", True),
     ("wall_seconds", 5400), ("development_config_digest", "sha256:" + "0" * 64),
     ("authorized", True), ("job_id", "stage_" + "0" * 32)],
)
def test_runtime_retry_rejects_budget_or_semantic_drift(tmp_path, monkeypatch, key, changed):
    raw = _copy_plan(tmp_path, monkeypatch)
    assert raw["execution_attempts"][0]["job_id"] == "stage_64da9bea52984726ab32c10bfdd36d4c"
    raw["technical_retry"][key] = changed
    (tmp_path / sync.PLAN).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="retry"):
        sync.load_plan()


@pytest.mark.parametrize(("key", "changed"), [
    ("selected_parameters", {"stage_0": None, "stage_2": {"threshold": 1}}),
    ("selected_pair_overlap_count", 0),
    ("development_candidate_accepted", True),
    ("human_expert_validated", True),
    ("full_trial_detail_readback", True),
    ("observation_count", 225246),
])
def test_closeout_cannot_promote_or_change_observed_result(tmp_path, monkeypatch, key, changed):
    raw = _copy_plan(tmp_path, monkeypatch)
    raw["runtime_qualification"][key] = changed
    (tmp_path / sync.PLAN).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="closeout"):
        sync.load_plan()


@pytest.mark.parametrize("stage", [0, 2])
def test_actual_reference_counts_block_even_hypothetically_perfect_scores(stage):
    raw = sync.load_plan()
    counts = raw["runtime_qualification"]["reference_summary"]["label_counts"]
    runtime = json.loads((ROOT / raw["runtime_config"]).read_text())
    # Deliberately idealized scores: this proves a necessary count gate, not real trial accuracy.
    score = SimpleNamespace(
        definite_positive_count=counts[f"stage_{stage}"],
        definite_negative_count=counts[f"stage_{2 if stage == 0 else 0}"] + counts["neither"],
        recall=1.0, negative_false_positive_rate=0.0, ambiguous_assertion_rate=0.0,
        precision_lower=1.0, f1=1.0,
    )
    gate = _gate(score, reference_counts=counts, r0_f1=1.0, gates=runtime["hard_gates"])
    assert not gate["passed"]
    assert "stage_2_reference_count_below_gate" in gate["reason_codes"]
    assert ("definite_positive_count_below_gate" in gate["reason_codes"]) is (stage == 2)


def test_reconstructed_null_candidate_matches_worker_reported_digest():
    result = sync.load_plan()["runtime_qualification"]
    candidate = {
        "schema_version": "quanthus.stage-order-proposition-development-candidate.v1",
        "work_item": "P3-STAGE-004", "disposition": "INCONCLUSIVE",
        "stage_0": {"code": 0, "working_name": "structural_disorder",
                    "parameters": None, "development_only": True},
        "stage_2": {"code": 2, "working_name": "orderly_consolidation",
                    "parameters": None, "development_only": True},
        "both_false_allowed": True, "both_true_allowed": False,
        "selected_pair_overlap_count": None, "primary_stage_authorized": False,
        "official_label_card_authorized": False, "human_expert_validated": False,
    }
    assert digest(candidate) == result["candidate_artifact_digest"]
