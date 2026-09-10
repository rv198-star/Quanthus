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


def test_structure_order_plan_opens_only_immutable_source_qualification():
    raw = sync.load_plan()
    by_id = {item["id"]: item for item in raw["items"]}
    assert [item["id"] for item in raw["items"]] == [
        "P3-STRUCT-007",
        "P3-STRUCT-007-A",
        "P3-STRUCT-007-B",
        "P3-STRUCT-007-C",
        "P3-STRUCT-007-D",
    ]
    assert raw["runtime_accepted"] is False
    assert by_id["P3-STRUCT-007-A"]["status"] == "closed"
    assert by_id["P3-STRUCT-007-B"]["status"] == "closed"
    assert by_id["P3-STRUCT-007-C"]["status"] == "ready"
    assert by_id["P3-STRUCT-007-D"]["status"] == "blocked"
    auth = raw["authorization"]
    assert auth["formal_structure_fact_implementation_authorized"] is True
    assert auth["immutable_accepted_source_artifact_qualification_authorized"] is True
    for key, value in auth.items():
        if key.endswith("_authorized") and key not in {
            "formal_structure_fact_implementation_authorized",
            "immutable_accepted_source_artifact_qualification_authorized",
        }:
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


def test_structure_order_concepts_are_declared_but_not_bound_before_qualification():
    catalog = json.loads((ROOT / "config/structure-semantic-catalog.v1.json").read_text())
    concepts = {item["concept_id"] for item in catalog["concepts"]}
    expected = {
        "structure.geometry.range-width",
        "structure.geometry.width-change",
        "structure.geometry.range-center-shift",
    }
    assert expected <= concepts
    blueprint = json.loads((ROOT / "config/structure-facts-v2-blueprint.v1.json").read_text())
    bound = {item["concept_id"] for item in blueprint["semantic_runtime_bindings"]}
    assert expected.isdisjoint(bound)
    assert "structure.geometry.directional-efficiency" in bound
