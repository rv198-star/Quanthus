"""Planning contracts; these tests do not train or read market-result partitions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from quanthus.policy import find_project_root
from quanthus.structure_v2_inputs import digest

ROOT = find_project_root()
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import sync_stock_stage_learning_issues as sync  # noqa: E402


def test_exact_plan_keeps_training_and_sealed_data_unopened() -> None:
    plan = sync.load_plan()
    assert len(plan["items"]) == 11
    ready = {item["id"] for item in plan["items"] if item["status"] == "ready"}
    assert ready == set()
    by_id = {item["id"]: item for item in plan["items"]}
    assert by_id["P3-STAGE-001-A"]["status"] == "closed"
    assert by_id["P3-STAGE-001-B"]["status"] == "closed"
    assert by_id["P3-STAGE-001-C"]["status"] == "closed"
    assert by_id["P3-STAGE-001-D"]["status"] == "closed"
    assert by_id["P3-STAGE-001-E"]["status"] == "closed"
    assert by_id["P3-STAGE-001-F"]["status"] == "closed"
    assert by_id["P3-STAGE-001-I"]["status"] == "closed"
    assert by_id["P3-STAGE-001-J"]["status"] == "blocked"
    assert plan["data_policy"]["development_years"] == [2012, 2021]
    assert plan["data_policy"]["arbitration_years"] == [2022, 2024]
    assert plan["data_policy"]["permanent_blind_years"] == [2025, 2025]
    assert plan["authorization"][sync.CAPACITY_FLAG] is True
    assert plan["authorization"][sync.HARNESS_FLAG] is True
    assert plan["authorization"][sync.REFERENCE_FLAG] is True
    assert plan["authorization"][sync.DEVELOPMENT_SETTLEMENT_FLAG] is True
    assert plan["authorization"][sync.LEARNING_IMPL_FLAG] is True
    assert plan["authorization"][sync.SYNTHETIC_REFERENCE_FLAG] is True
    assert plan["authorization"][sync.SYNTHETIC_LEARNING_FLAG] is True
    assert plan["authorization"][sync.BOUNDED_ARBITRATION_FLAG] is False
    assert plan["authorization"][sync.BOUNDED_ARBITRATION_CONFIG] == (
        "config/stage-learning/qualification.v1.json"
    )
    assert plan["authorization"][sync.PERMANENT_BLIND_QUALIFICATION_FLAG] is False
    assert all(plan["authorization"][key] is False for key in sync.DENIED_AUTH_FLAGS)


@pytest.fixture
def copied_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict, Path]:
    plan = sync.load_plan()
    evidence = [path for item in plan["items"] for path in item["evidence"]]
    paths = (
        sync.PLAN,
        "config/p0-policy.v1.json",
        "config/p3-gates.v1.json",
        plan["design"],
        plan["compute_design"],
        plan["review"],
        plan["work_items"],
        *evidence,
    )
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    monkeypatch.setattr(sync, "ROOT", tmp_path)
    return plan, tmp_path / sync.PLAN


@pytest.mark.parametrize("flag", sorted(sync.DENIED_AUTH_FLAGS))
def test_issue_sync_cannot_enable_execution(copied_plan, flag: str) -> None:
    plan, path = copied_plan
    plan["authorization"][flag] = True
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="cannot grant"):
        sync.load_plan()


@pytest.mark.parametrize(
    "flag",
    [
        sync.CAPACITY_FLAG,
        sync.HARNESS_FLAG,
        sync.REFERENCE_FLAG,
        sync.DEVELOPMENT_SETTLEMENT_FLAG,
        sync.LEARNING_IMPL_FLAG,
        sync.SYNTHETIC_REFERENCE_FLAG,
        sync.SYNTHETIC_LEARNING_FLAG,
    ],
)
def test_positive_authorizations_must_be_explicit(copied_plan, flag: str) -> None:
    plan, path = copied_plan
    plan["authorization"][flag] = False
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="authorizations must be explicit"):
        sync.load_plan()


def test_consumed_arbitration_authority_cannot_be_reopened(copied_plan) -> None:
    plan, path = copied_plan
    plan["authorization"][sync.BOUNDED_ARBITRATION_FLAG] = True
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="cannot remain authorized"):
        sync.load_plan()


@pytest.mark.parametrize(
    "mutation",
    ["partition", "cycle", "scope", "empty", "premature", "ready", "missing_doc"],
)
def test_invalid_learning_plans_fail_closed(copied_plan, mutation: str) -> None:
    plan, path = copied_plan
    if mutation == "partition":
        plan["data_policy"]["development_years"] = [2019, 2024]
    elif mutation == "cycle":
        plan["items"][1]["depends_on"] = ["P3-STAGE-001-H"]
    elif mutation == "scope":
        plan["items"][1]["id"] = "P3-STAGE-999-A"
    elif mutation == "empty":
        plan["items"][3]["acceptance"] = []
    elif mutation == "premature":
        plan["items"][0]["status"] = "closed"
    elif mutation == "ready":
        plan["items"][5]["status"] = "ready"
    else:
        plan["review"] = "docs/nonexistent-review.md"
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        sync.load_plan()


def test_issue_body_contains_execution_contract_and_honest_review() -> None:
    plan = sync.load_plan()
    text = sync.body(
        plan["items"][2], plan, {"P3-STAGE-001": {"number": 1000}}
    )
    for term in (
        "## 输入",
        "## 交付物",
        "## 验收",
        "## 执行前提",
        "## 权限边界",
        "#1000",
    ):
        assert term in text
    review = (ROOT / plan["review"]).read_text()
    assert "单一助手" in review
    assert "并非三位真人/独立Agent签字" in review
    assert "未跑基准" in review


def test_stage_training_bootstrap_fails_closed_without_ensurepip() -> None:
    bootstrap = (ROOT / "ops/bin/bootstrap-stage-training-node").read_text()
    assert "python3-venv" in bootstrap
    assert "-m ensurepip --version" in bootstrap
    assert "-m ensurepip --upgrade" in bootstrap
    assert "training virtualenv has no usable pip" in bootstrap


def test_stage_training_dependency_group_includes_timezone_support_for_duckdb() -> None:
    project = (ROOT / "pyproject.toml").read_text()
    group = project.split("stage-training = [", 1)[1].split("]", 1)[0]
    assert '"pytz>=2024.1"' in group


def test_planning_routes_exist_without_runtime_feature_registration() -> None:
    plan = sync.load_plan()
    manifest = json.loads((ROOT / "config/data-indicator-catalog-sources.v1.json").read_text())
    assert not any("stock-stage-learning" in source["path"] for source in manifest["sources"])
    design = (ROOT / plan["design"]).read_text()
    for value in (
        "R0",
        "R1",
        "R2",
        "SHADOW / QUALIFIED",
        "ambiguous_set",
        "2012-2021",
        "2025",
    ):
        assert value in design
    experiment = json.loads((ROOT / "config/stage-learning/experiment.v1.json").read_text())
    assert experiment["experiment_id"] == "stock-stage-first-group-3-4-v1"
    assert experiment["delivery_authority"]["capacity_benchmark_authorized"] is True
    assert experiment["delivery_authority"]["classifier_training_authorized"] is False
    assert experiment["partition_policy"]["development"]["end"] == "2021-12-31"
    harness = json.loads((ROOT / "config/stage-learning/harness.v1.json").read_text())
    assert harness["authority"]["implementation_authorized"] is True
    assert harness["authority"]["classifier_training_authorized"] is False
    assert harness["development_read_guard"]["maximum_date"] == "2021-12-31"
    reference = json.loads((ROOT / "config/stage-learning/reference-pilot.v1.json").read_text())
    assert reference["population"]["cohort_size"] == 32
    assert reference["population"]["report_end"] == "2020-11-30"
    assert reference["population"]["read_end"] == "2020-12-31"
    assert reference["sampling"]["total_episode_contexts"] == 120
    assert reference["sampling"]["selection_mode"] == "stratified_development_supplement_v1"
    assert reference["sampling"]["prior_pilot_contexts_excluded"] is True
    assert reference["synthetic_workflow"]["panel_config"].endswith(
        "synthetic-expert-panel.v2.json"
    )
    selection = json.loads(
        (ROOT / "config/stage-learning/development-selection.v1.json").read_text()
    )
    assert selection["hard_gates"]["maximum_ambiguous_unique_assertion_rate"] == 0.10
    assert selection["hard_gates"]["minimum_positive_reference_coverage"] == 0.60
    assert reference["authority"]["arbitration_access_authorized"] is False
    assert reference["authority"]["permanent_blind_access_authorized"] is False
    candidate = json.loads(
        (ROOT / "config/stage-learning/development-candidate.v1.json").read_text()
    )
    assert candidate["status"] == "frozen_for_external_evaluation"
    assert candidate["trial"]["parameters"]["probe_volume_ratio20_min"] == "1.0"
    assert candidate["trial"]["parameters"]["acceptance_hold_sessions"] == 2
    qualification = json.loads(
        (ROOT / "config/stage-learning/qualification.v1.json").read_text()
    )
    assert qualification["authority"]["arbitration_access_budget_runs"] == 1
    assert qualification["authority"]["parameter_search_authorized"] is False
    assert qualification["authority"]["permanent_blind_access_authorized"] is False
    assert qualification["partition"]["name"] == "2022-2024-arbitration"
    assert qualification["partition"]["permanent_blind_2025_untouched"] is True
    assert qualification["decision"]["qualified_label_version_allowed"] is False
    p3_gate = json.loads((ROOT / "config/p3-gates.v1.json").read_text())
    bounded = p3_gate["bounded_partition_exceptions"]
    assert len(bounded) == 1
    assert bounded[0]["id"] == "P3-STAGE-001-F"
    assert bounded[0]["qualification_digest"] == digest(qualification)
    assert bounded[0]["candidate_digest"] == digest(candidate)
    assert bounded[0]["access_budget_runs"] == 1
    assert bounded[0]["parameter_search_authorized"] is False
    assert bounded[0]["permanent_blind_access_authorized"] is False
    reference = json.loads((ROOT / "config/stage-learning/reference-pilot.v1.json").read_text())
    assert reference["sampling"]["total_episode_contexts"] == 120
    assert reference["authority"]["development_future_settlement_authorized"] is True
    assert reference["authority"]["classifier_training_authorized"] is False
    assert reference["authority"]["arbitration_access_authorized"] is False
    assert reference["authority"]["permanent_blind_access_authorized"] is False
