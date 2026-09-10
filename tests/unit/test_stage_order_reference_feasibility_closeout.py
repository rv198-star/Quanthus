from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
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
        "stage_reference_feasibility_plan",
        ROOT / "scripts/sync_stage_order_reference_feasibility_issues.py",
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_real_reference_collection_completed_without_new_candidate_or_search():
    script = module()
    plan = script.load_plan()
    assert plan["runtime_completed"] is True
    assert all(item["status"] == "closed" for item in plan["items"])
    assert plan["authorization"]["maximum_jobs"] == 2
    assert plan["runtime_receipts"]["summary"]["primary_stage"] is None
    from sync_github_planning import work_items

    statuses = {item.stable_id: item.status for item in work_items(ROOT)}
    assert all(statuses[item["id"]] == "closed" for item in plan["items"])
    old = json.loads((ROOT / "config/stage-order-proposition-delivery.v1.json").read_text())
    assert old["runtime_qualification"]["decision"] == "INCONCLUSIVE_REFERENCE_SUFFICIENCY"
    assert old["runtime_qualification"]["development_candidate_accepted"] is False


@pytest.mark.parametrize("key", [
    "human_expert_validated", "independent_validation", "full_context_blinding_claimed",
    "market_reference_validated", "market_representativeness", "new_parameter_candidate_accepted",
    "parameter_search_authorized", "official_label_authorized",
])
def test_collection_success_never_promotes_its_evidence_tier(key):
    script = module()
    plan = script.load_plan()
    plan["runtime_receipts"]["summary"][key] = True
    with pytest.raises(ValueError, match="claim_ceiling"):
        script.validate_completed(plan)


@pytest.mark.parametrize("change", [
    "parent", "source", "phase", "attempt", "elapsed", "counts",
])
def test_closeout_does_not_accept_inconsistent_receipts(change):
    script = module()
    plan = deepcopy(script.load_plan())
    receipts = plan["runtime_receipts"]
    if change == "parent":
        receipts["resolve"]["parent_manifest_sha256"] = "sha256:" + "0" * 64
    elif change == "source":
        receipts["prepare"]["source_inputs_digest"] = "sha256:" + "0" * 64
    elif change == "phase":
        receipts["prepare"]["phase"] = "resolve"
    elif change == "attempt":
        receipts["resolve"]["attempt"] = 2
    elif change == "elapsed":
        receipts["prepare"]["worker_elapsed_seconds"] = 0
    else:
        receipts["summary"]["primary_reference_counts"]["STAGE_2"] = 20
    with pytest.raises(ValueError):
        script.validate_completed(plan)


@pytest.mark.parametrize("permission", ["prepare_job", "resolve_job"])
def test_finished_milestone_cannot_reopen_job_permissions(tmp_path, permission):
    script = module()
    plan = script.load_plan()
    plan["authorization"][permission] = True
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    with patch.object(script, "PLAN", str(path)), pytest.raises(ValueError, match="unbound"):
        script.load_plan()


def test_exact_terminal_artifact_identities_are_preserved_without_market_rows():
    receipts = module().load_plan()["runtime_receipts"]
    assert receipts["prepare"]["job_id"] == "job_fec6e60e382f47bc8dcc796919470748"
    assert receipts["resolve"]["job_id"] == "job_2a380e24cb5b4f8e8816558b6fbabb5d"
    assert receipts["prepare"]["manifest_sha256"] == (
        "sha256:fdb7cc8b3c34566fee18ea5ee00589ad12e1da6dbe74e2f624bb030d10acf5ec"
    )
    assert receipts["resolve"]["manifest_sha256"] == (
        "sha256:ed8252670525962f224dee6f0bfb6c2e622b8c9f0ebc729df529a7bfe4ecfed9"
    )
    assert receipts["resolve"]["opinion_digest"] == (
        "sha256:d281a2b37e522c44413f6738d045deac642e9b021e1c6fc3d93f789db87a488c"
    )
    assert "opinions" not in json.dumps(receipts)
