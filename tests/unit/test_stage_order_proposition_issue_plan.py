from __future__ import annotations

import json
import sys

import pytest

from quanthus.policy import find_project_root

ROOT = find_project_root()
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import sync_stage_order_proposition_issues as sync  # noqa: E402


def test_stage_0_2_plan_opens_only_one_development_run() -> None:
    raw = sync.load_plan()
    by_id = {item["id"]: item for item in raw["items"]}
    assert [item["id"] for item in raw["items"]] == [
        "P3-STAGE-004",
        "P3-STAGE-004-A",
        "P3-STAGE-004-B",
        "P3-STAGE-004-C",
        "P3-STAGE-004-D",
    ]
    assert raw["runtime_accepted"] is False
    assert by_id["P3-STAGE-004-A"]["status"] == "closed"
    assert by_id["P3-STAGE-004-B"]["status"] == "closed"
    assert by_id["P3-STAGE-004-C"]["status"] == "ready"
    assert by_id["P3-STAGE-004-D"]["status"] == "blocked"
    auth = raw["authorization"]
    for key in (
        "order_proposition_implementation_authorized",
        "development_data_read_authorized",
        "synthetic_reference_authorized",
        "limited_parameter_search_authorized",
        "development_candidate_artifact_authorized",
    ):
        assert auth[key] is True
    for key, value in auth.items():
        if key.endswith("_authorized") and key not in {
            "order_proposition_implementation_authorized",
            "development_data_read_authorized",
            "synthetic_reference_authorized",
            "limited_parameter_search_authorized",
            "development_candidate_artifact_authorized",
        }:
            assert value is False


def test_stage_0_2_plan_fails_closed_if_holdout_or_primary_stage_opens(
    tmp_path, monkeypatch
) -> None:
    raw = sync.load_plan()
    required = [
        sync.PLAN,
        raw["design"],
        raw["review"],
        raw["runtime_config"],
        raw["panel_config"],
    ]
    required.extend(path for item in raw["items"] for path in item["evidence"])
    for relative in required:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    monkeypatch.setattr(sync, "ROOT", tmp_path)
    raw["authorization"]["permanent_blind_access_authorized"] = True
    (tmp_path / sync.PLAN).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="authority"):
        sync.load_plan()


def test_stage_0_2_runtime_freezes_finite_grid_and_fact_contracts() -> None:
    raw = json.loads(
        (ROOT / "config/stage-learning/order-proposition-development.v1.json").read_text()
    )
    assert raw["trend_fact_contract_digest"].startswith("sha256:")
    assert raw["order_fact_contract_digest"].startswith("sha256:")
    stage2 = raw["search_space"]["stage_2"]
    stage0 = raw["search_space"]["stage_0"]
    count2 = 1
    for values in stage2.values():
        count2 *= len(values)
    count0 = 1
    for values in stage0.values():
        count0 *= len(values)
    assert count2 == 81
    assert count0 == 27
    assert raw["hard_gates"]["maximum_pair_overlap_count"] == 0
    assert raw["selection"]["no_in_run_hot_fix"] is True
    assert raw["selection"]["no_search_space_expansion_after_results"] is True


@pytest.mark.parametrize(
    ("key", "changed"),
    [("maximum_additional_attempts", 2), ("semantic_changes_authorized", True),
     ("wall_seconds", 5400), ("development_config_digest", "sha256:" + "0" * 64)],
)
def test_runtime_retry_rejects_budget_or_semantic_drift(tmp_path, monkeypatch, key, changed):
    raw = sync.load_plan()
    assert raw["execution_attempts"][0]["job_id"] == "stage_64da9bea52984726ab32c10bfdd36d4c"
    assert raw["runtime_accepted"] is False
    required = [sync.PLAN, raw["design"], raw["review"], raw["runtime_config"], raw["panel_config"]]
    required.extend(path for item in raw["items"] for path in item["evidence"])
    for relative in required:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    raw["technical_retry"][key] = changed
    (tmp_path / sync.PLAN).write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(sync, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="retry"):
        sync.load_plan()
