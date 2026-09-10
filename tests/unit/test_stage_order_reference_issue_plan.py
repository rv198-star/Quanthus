from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module():
    import sys

    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "stage_order_reference_issue_plan", ROOT / "scripts/sync_stage_order_reference_issues.py",
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_exact_successor_issue_scope_and_paths():
    script = module()
    plan = script.load_plan()
    assert tuple(item["id"] for item in plan["items"]) == (
        "P3-STAGE-005", "P3-STAGE-005-A", "P3-STAGE-005-B", "P3-STAGE-005-C",
    )
    assert plan["old_experiment_reopened"] is False
    assert plan["market_reference_validated"] is False
    for item in plan["items"]:
        body = script.issue_body(item, plan, {})
        assert f"id={item['id']}" in body
        assert "No new market run" in body
        assert "P3-STAGE-004 remains closed" in body


@pytest.mark.parametrize("key", ["market_reference_validated", "old_experiment_reopened"])
def test_plan_cannot_promote_synthetic_engineering_to_market_qualification(tmp_path, key):
    script = module()
    plan = script.load_plan()
    plan[key] = True
    file = tmp_path / "plan.json"
    file.write_text(json.dumps(plan))
    with (
        patch.object(script, "PLAN", str(file)),
        pytest.raises(ValueError, match="research authority"),
    ):
        script.load_plan()


def test_governing_lld_and_index_statuses_match_machine_plan():
    script = module()
    from sync_github_planning import work_items

    observed = {item.stable_id: item.status for item in work_items(ROOT)}
    for item in script.load_plan()["items"]:
        assert observed[item["id"]] == item["status"]


def test_old_experiment_still_closed_and_inconclusive():
    old = json.loads((ROOT / "config/stage-order-proposition-delivery.v1.json").read_text())
    assert old["runtime_qualification"]["decision"] == "INCONCLUSIVE_REFERENCE_SUFFICIENCY"
    assert old["runtime_qualification"]["development_candidate_accepted"] is False
    assert all(item["status"] == "closed" for item in old["items"])
    assert not old["authorization"]["limited_parameter_search_authorized"]
    assert not old["technical_retry"]["authorized"]


def test_source_protocol_has_no_runtime_worker_or_market_access_authority():
    path = ROOT / "config/stage-learning/order-reference-protocol.v1.json"
    protocol = json.loads(path.read_text())
    assert {key for key, value in protocol["authority"].items() if value} == {
        "mechanism_implementation", "synthetic_engineering_validation",
    }
    assert protocol["review_roles"] == ["short_term", "fund"]
    assert protocol["profile"]["recent_disagreement_changes_primary"] is False
    assert protocol["profile"]["both_false_allowed"] is True
    assert "threshold" not in json.dumps(protocol)
