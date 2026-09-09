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


def test_third_group_plan_opens_only_one_profile_run():
    raw = sync.load_plan()
    by_id = {item["id"]: item for item in raw["items"]}
    assert [item["id"] for item in raw["items"]] == [
        "P3-STAGE-003",
        "P3-STAGE-003-A",
        "P3-STAGE-003-B",
        "P3-STAGE-003-C",
        "P3-STAGE-003-D",
    ]
    assert by_id["P3-STAGE-003-A"]["status"] == "closed"
    assert by_id["P3-STAGE-003-B"]["status"] == "closed"
    assert by_id["P3-STAGE-003-C"]["status"] == "ready"
    assert by_id["P3-STAGE-003-D"]["status"] == "blocked"
    auth = raw["authorization"]
    assert auth["third_group_development_profile_run_authorized"] is True
    assert auth["development_data_read_authorized"] is True
    for key in (
        "parameter_search_authorized",
        "stage_label_output_authorized",
        "formal_structure_fact_registration_authorized",
        "future_outcome_access_authorized",
        "arbitration_access_authorized",
        "permanent_blind_access_authorized",
        "holdout_population_selection_authorized",
        "primary_stage_arbitration_authorized",
        "daily_schedule_authorized",
        "strategy_consumption_authorized",
        "trading_authorized",
    ):
        assert auth[key] is False


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
    required = [sync.PLAN, raw["design"], raw["review"], raw["runtime_config"]]
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
