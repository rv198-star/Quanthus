from __future__ import annotations

import json
import sys

import pytest

from quanthus.policy import find_project_root

ROOT = find_project_root()
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import sync_stage_10_11_projection_issues as sync  # noqa: E402


def test_projection_plan_opens_only_immutable_f9_qualification_scope():
    raw = sync.load_plan()
    assert [item["id"] for item in raw["items"]] == [
        "P3-STAGE-002",
        "P3-STAGE-002-A",
        "P3-STAGE-002-B",
        "P3-STAGE-002-C",
        "P3-STAGE-002-D",
    ]
    by_id = {item["id"]: item for item in raw["items"]}
    assert by_id["P3-STAGE-002"]["status"] == "ready"
    assert by_id["P3-STAGE-002-A"]["status"] == "closed"
    assert by_id["P3-STAGE-002-B"]["status"] == "closed"
    assert by_id["P3-STAGE-002-C"]["status"] == "ready"
    assert by_id["P3-STAGE-002-D"]["status"] == "blocked"
    assert raw["runtime_accepted"] is False
    assert raw["runtime_qualification"] is None
    authority = raw["authorization"]
    assert authority["projection_contract_implementation_authorized"] is True
    assert authority["immutable_f9_artifact_qualification_authorized"] is True
    for key in (
        "market_data_read_authorized",
        "dwd_provider_open_authorized",
        "future_outcome_access_authorized",
        "sealed_partition_access_authorized",
        "permanent_blind_access_authorized",
        "primary_stage_arbitration_authorized",
        "official_label_card_publication_authorized",
        "daily_schedule_authorized",
        "strategy_consumption_authorized",
        "trading_authorized",
    ):
        assert authority[key] is False


def test_projection_plan_fails_closed_if_market_or_label_authority_is_opened(tmp_path, monkeypatch):
    raw = sync.load_plan()
    paths = [
        sync.PLAN,
        raw["design"],
        raw["review"],
        raw["projection_contract"],
        raw["qualification_request"],
        *[path for item in raw["items"] for path in item["evidence"]],
    ]
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    monkeypatch.setattr(sync, "ROOT", tmp_path)
    plan_path = tmp_path / sync.PLAN
    raw["authorization"]["market_data_read_authorized"] = True
    plan_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="authority"):
        sync.load_plan()


def test_projection_contract_and_qualification_keep_primary_stage_unopened():
    contract = json.loads((ROOT / "config/stage-10-11-projection.v1.json").read_text())
    assert contract["relationship"]["candidate_set_may_contain_10_and_11_together"] is True
    assert contract["relationship"]["primary_stage_output_must_be_null"] is True
    assert contract["authority"]["primary_stage_arbitration_authorized"] is False
    qualification = json.loads(
        (ROOT / "config/stage-10-11-projection-qualification.v1.json").read_text()
    )
    assert qualification["authority"]["market_data_read_authorized"] is False
    assert qualification["authority"]["dwd_provider_open_authorized"] is False
    assert qualification["acceptance"]["primary_stage_must_be_null_for_every_observation"] is True
