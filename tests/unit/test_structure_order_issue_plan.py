from __future__ import annotations

import json
import sys

import pytest

from quanthus.policy import find_project_root

ROOT = find_project_root()
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import sync_structure_order_issues as sync  # noqa: E402


def test_structure_order_plan_closes_after_immutable_source_qualification():
    raw = sync.load_plan()
    by_id = {item["id"]: item for item in raw["items"]}
    assert [item["id"] for item in raw["items"]] == [
        "P3-STRUCT-007",
        "P3-STRUCT-007-A",
        "P3-STRUCT-007-B",
        "P3-STRUCT-007-C",
        "P3-STRUCT-007-D",
    ]
    assert raw["runtime_accepted"] is True
    assert all(by_id[key]["status"] == "closed" for key in by_id)
    qualified = raw["runtime_qualification"]
    assert qualified["decision"] == "PASS_BOUNDED_STRUCTURE_ORDER_FACT_FAMILY"
    assert qualified["job_id"] == "job_87fe531cec73477fbcc92df65a43a137"
    assert qualified["available_observation_count"] == 174
    assert qualified["measurement_count"] == 1218
    assert qualified["market_data_reads"] == 0
    assert qualified["dwd_provider_opens"] == 0
    auth = raw["authorization"]
    assert auth["scope"] == "structure_order_fact_family_closed_no_active_qualification"
    for key, value in auth.items():
        if key.endswith("_authorized"):
            assert value is False


def test_structure_order_plan_fails_closed_if_stage_search_is_opened(tmp_path, monkeypatch):
    raw = sync.load_plan()
    required = [sync.PLAN, raw["design"], raw["fact_contract"], raw["qualification_request"]]
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


def test_structure_order_concepts_are_bound_only_after_qualification():
    catalog = json.loads((ROOT / "config/structure-semantic-catalog.v1.json").read_text())
    concepts = {item["concept_id"] for item in catalog["concepts"]}
    expected = {
        "structure.geometry.range-width",
        "structure.geometry.width-change",
        "structure.geometry.range-center-shift",
    }
    assert expected <= concepts
    blueprint = json.loads((ROOT / "config/structure-facts-v2-blueprint.v1.json").read_text())
    bound = {
        item["concept_id"]: item
        for item in blueprint["semantic_runtime_bindings"]
        if item["concept_id"] in expected
    }
    assert set(bound) == expected
    assert {item["capability_id"] for item in bound.values()} == {"B6-ORDER"}
    assert {item["accepted_scope"] for item in bound.values()} == {
        "bounded_retrospective_structure_order"
    }
    all_bound = {item["concept_id"] for item in blueprint["semantic_runtime_bindings"]}
    assert "structure.geometry.directional-efficiency" in all_bound
