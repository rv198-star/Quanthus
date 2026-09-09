from __future__ import annotations

import json
import sys

import pytest

from quanthus.policy import find_project_root

ROOT = find_project_root()
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import sync_structure_f9_issues as sync  # noqa: E402


def test_f9_plan_opens_only_cold_qualification_and_keeps_runtime_unaccepted():
    raw = sync.load_plan()
    assert [item["id"] for item in raw["items"]] == [
        "P3-STRUCT-005",
        "P3-STRUCT-005-A",
        "P3-STRUCT-005-B",
        "P3-STRUCT-005-C",
        "P3-STRUCT-005-D",
    ]
    by_id = {item["id"]: item for item in raw["items"]}
    assert by_id["P3-STRUCT-005-A"]["status"] == "closed"
    assert by_id["P3-STRUCT-005-B"]["status"] == "closed"
    assert by_id["P3-STRUCT-005-C"]["status"] == "ready"
    assert by_id["P3-STRUCT-005-D"]["status"] == "blocked"
    assert by_id["P3-STRUCT-005"]["status"] == "blocked"
    assert raw["runtime_accepted"] is False
    assert raw["runtime_qualification"] is None


def test_f9_runtime_request_is_bounded_and_never_opens_sealed_or_official_labels():
    raw = json.loads((ROOT / "config/structure-f9-run.development.v1.json").read_text())
    assert raw["security_codes"] == ["000001", "300750", "600519"]
    assert raw["report_start"] == "2020-01-02"
    assert raw["report_end"] == "2020-03-31"
    assert raw["cold_result_job_budget"] == 2
    assert raw["projection"]["official_label"] is False
    assert raw["projection"]["strategy_consumption_authorized"] is False
    assert raw["sealed_partition_access_authorized"] is False
    assert raw["permanent_blind_access_authorized"] is False
    assert raw["research_only"] is True
    assert raw["execution_authorized"] is False


def test_f9_plan_cannot_close_parent_before_real_dwd_runtime(tmp_path, monkeypatch):
    raw = sync.load_plan()
    paths = [sync.PLAN, raw["design"], raw["runtime_request"]]
    paths.extend(path for item in raw["items"] for path in item["evidence"])
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    monkeypatch.setattr(sync, "ROOT", tmp_path)
    raw["items"][0]["status"] = "closed"
    (tmp_path / sync.PLAN).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="closure|parent"):
        sync.load_plan()


def test_f9_catalog_registers_process_and_rebound_without_runtime_aliasing():
    catalog = json.loads((ROOT / "config/structure-semantic-catalog.v1.json").read_text())
    concepts = {item["concept_id"]: item for item in catalog["concepts"]}
    assert concepts["structure.geometry.post-break-rebound"]["registration"] == "reserved"
    assert concepts["structure.episode.support-failure-sequence"]["registration"] == "reserved"
    assert all(item["relationship"] == "related_not_equivalent" for item in catalog["legacy_links"])
