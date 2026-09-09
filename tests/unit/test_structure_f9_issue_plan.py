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


def test_f9_plan_closes_only_after_exact_bounded_runtime_acceptance():
    raw = sync.load_plan()
    assert [item["id"] for item in raw["items"]] == [
        "P3-STRUCT-005",
        "P3-STRUCT-005-A",
        "P3-STRUCT-005-B",
        "P3-STRUCT-005-C",
        "P3-STRUCT-005-D",
    ]
    assert all(item["status"] == "closed" for item in raw["items"])
    assert raw["runtime_accepted"] is True
    qualified = raw["runtime_qualification"]
    assert qualified["decision"] == "PASS_BOUNDED_ROUTE_A"
    assert qualified["code_revision"] == "44571610b1da1792a80bbd45ee7f180d59e8495a"
    assert qualified["provider_builds_per_job"] == 1
    assert qualified["cross_job_cache_hits"] == 0
    assert qualified["outcome_blind_review"] == (
        "PASS_12_OF_12_NO_OBVIOUS_SEMANTIC_CONTRADICTION"
    )


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


def test_f9_plan_cannot_claim_closed_parent_without_runtime_acceptance(tmp_path, monkeypatch):
    raw = sync.load_plan()
    paths = [sync.PLAN, raw["design"], raw["runtime_request"]]
    paths.extend(path for item in raw["items"] for path in item["evidence"])
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    monkeypatch.setattr(sync, "ROOT", tmp_path)
    raw["runtime_accepted"] = False
    raw["runtime_qualification"] = None
    (tmp_path / sync.PLAN).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="parent"):
        sync.load_plan()


def test_f9_catalog_and_blueprint_bind_only_accepted_continuous_concepts():
    catalog = json.loads((ROOT / "config/structure-semantic-catalog.v1.json").read_text())
    concepts = {item["concept_id"]: item for item in catalog["concepts"]}
    assert concepts["structure.geometry.post-break-rebound"]["registration"] == "reserved"
    assert concepts["structure.episode.support-failure-sequence"]["registration"] == "reserved"
    assert all(item["relationship"] == "related_not_equivalent" for item in catalog["legacy_links"])
    blueprint = json.loads((ROOT / "config/structure-facts-v2-blueprint.v1.json").read_text())
    assert blueprint["current_slice"] == "B1-B5-accepted-bounded"
    bindings = {item["concept_id"]: item for item in blueprint["semantic_runtime_bindings"]}
    assert bindings["structure.geometry.post-break-rebound"] == {
        "concept_id": "structure.geometry.post-break-rebound",
        "capability_id": "B5-F9",
        "accepted_scope": "bounded_retrospective_f9_support_process",
    }
    assert bindings["structure.episode.support-failure-sequence"]["capability_id"] == "B5-F9"
    capability = next(item for item in blueprint["capabilities"] if item["id"] == "B5-F9")
    assert capability["implementation_status"] == "accepted_bounded_v2"
    assert capability["verification_status"] == "real_dwd_ab"
