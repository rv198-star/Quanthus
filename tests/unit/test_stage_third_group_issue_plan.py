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
import sync_stage_third_group_issues as sync  # noqa: E402


def test_third_group_plan_closes_after_one_profile_and_freezes_successor_route():
    raw = sync.load_plan()
    by_id = {item["id"]: item for item in raw["items"]}
    assert [item["id"] for item in raw["items"]] == [
        "P3-STAGE-003",
        "P3-STAGE-003-A",
        "P3-STAGE-003-B",
        "P3-STAGE-003-C",
        "P3-STAGE-003-D",
    ]
    assert all(by_id[key]["status"] == "closed" for key in by_id)
    assert raw["runtime_accepted"] is True
    qualified = raw["runtime_qualification"]
    assert qualified["decision"] == "PASS_PROFILE_WITH_DIMENSIONAL_RECLASSIFICATION"
    assert qualified["job_id"] == "stage_313d48cfc06948e0a7fc7a3bd77b2657"
    assert qualified["profile_observation_count"] == 225246
    assert qualified["successor_order"] == [
        "trend_evolution_fact_family",
        "structure_order_fact_family",
        "high_side_activity_relation_if_still_required",
    ]
    assert qualified["subgroup_disposition"]["8_9"] == (
        "preserve_codes_reclassify_as_modifier_hypotheses"
    )
    auth = raw["authorization"]
    assert auth["scope"] == "third_group_profile_closed_no_active_search_or_fact_authority"
    for key, value in auth.items():
        if key.endswith("_authorized"):
            assert value is False


def test_third_group_runtime_config_stays_development_only_and_aggregate_only():
    raw = json.loads(
        (ROOT / "config/stage-learning/third-group-profile.v1.json").read_text(encoding="utf-8")
    )
    assert raw["development_window"]["report_start"] == "2020-01-02"
    assert raw["development_window"]["report_end"] == "2021-12-31"
    assert raw["population"]["cohort_size"] == 512
    assert raw["artifact"]["summary_only"] is True
    assert raw["artifact"]["security_codes_published"] is False
    assert raw["artifact"]["market_prices_published"] is False
    assert raw["artifact"]["observation_rows_published"] is False
    assert raw["artifact"]["official_stage_labels_published"] is False
    assert raw["measurements"]["no_stage_threshold_is_derived_inside_this_job"] is True
    assert raw["measurements"]["no_state_or_modifier_is_selected_inside_this_job"] is True


def test_third_group_plan_fails_closed_if_search_is_opened(tmp_path: Path, monkeypatch):
    raw = sync.load_plan()
    required = [
        sync.PLAN,
        raw["design"],
        raw["review"],
        raw["post_run_review"],
        raw["runtime_config"],
        raw["runtime_qualification"]["evidence"],
    ]
    required.extend(path for item in raw["items"] for path in item["evidence"])
    for relative in required:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    monkeypatch.setattr(sync, "ROOT", tmp_path)
    raw["authorization"]["parameter_search_authorized"] = True
    (tmp_path / sync.PLAN).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="authority"):
        sync.load_plan()
