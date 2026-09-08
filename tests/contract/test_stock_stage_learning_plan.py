"""Planning contracts; these tests do not train or read market-result partitions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from quanthus.policy import find_project_root

ROOT = find_project_root()
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import sync_stock_stage_learning_issues as sync  # noqa: E402


def test_exact_plan_keeps_training_and_sealed_data_unopened() -> None:
    plan = sync.load_plan()
    assert len(plan["items"]) == 9
    ready = {item["id"] for item in plan["items"] if item["status"] == "ready"}
    assert ready == {"P3-STAGE-001-C"}
    stage_a = next(item for item in plan["items"] if item["id"] == "P3-STAGE-001-A")
    assert stage_a["status"] == "closed"
    assert plan["data_policy"]["development_years"] == [2012, 2021]
    assert plan["data_policy"]["arbitration_years"] == [2022, 2024]
    assert plan["data_policy"]["permanent_blind_years"] == [2025, 2025]
    assert plan["authorization"][sync.CAPACITY_FLAG] is True
    assert plan["authorization"][sync.HARNESS_FLAG] is True
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


@pytest.mark.parametrize("flag", [sync.CAPACITY_FLAG, sync.HARNESS_FLAG])
def test_positive_authorizations_must_be_explicit(copied_plan, flag: str) -> None:
    plan, path = copied_plan
    plan["authorization"][flag] = False
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="authorizations must be explicit"):
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
