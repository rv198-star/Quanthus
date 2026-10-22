import hashlib
import json
from pathlib import Path

import pytest

from scripts.sync_github_planning import GitHub, task_body, work_items

ROOT = Path(__file__).resolve().parents[2]


def test_owner_closeout_cancels_future_work_without_accepting_it():
    decision = json.loads(
        (ROOT / "config/forward-validation/entry/p6-owner-closeout-20261023.v1.json").read_text()
    )
    assert decision["programme_status"] == "completed_under_owner_revised_scope"
    assert decision["prospective_work_disposition"] == "cancelled_not_accepted"
    assert decision["retired_work_items"] == ["P6-ACTIVATE-001", "P6-REVIEW-001"]
    assert decision["historical_mechanism_accepted"] is True
    assert not any(decision["additional_authority"].values())
    assert decision["p10_issue_538_disposition"] == "independent_branch_remains_open"
    for binding in decision["preserved_bindings"]:
        observed = hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest()
        assert observed == binding["sha256"]
    items = {item.stable_id: item for item in work_items(ROOT)}
    for key in ("P6-000", *decision["retired_work_items"]):
        assert items[key].status == "closed"
        assert "Owner scope closeout" in task_body(items[key], "rv198-star/Quanthus")
    assert all(item.status == "blocked" for item in items.values() if item.phase == "P7")


@pytest.mark.parametrize("key", ["P6-ACTIVATE-001", "P6-REVIEW-001"])
@pytest.mark.parametrize("prior_state", ["open", "closed"])
def test_sync_records_cancelled_p6_as_not_planned(key, prior_state):
    existing = {
        "number": 209,
        "title": f"[{key}] Original objective",
        "body": "Original evidence retained",
        "labels": [{"name": "status:closed"}],
        "milestone": None,
        "state": prior_state,
        "state_reason": "completed" if prior_state == "closed" else None,
    }
    calls = []
    github = GitHub("rv198-star/Quanthus")

    def fake_api(method, endpoint, payload):
        calls.append((method, payload))
        return {
            **existing, **payload,
            "labels": [{"name": label} for label in payload["labels"]],
        }

    github.api = fake_api
    result, _ = github.upsert_issue(
        existing, existing["title"], existing["body"], ["status:closed"], None
    )
    assert calls and result["state"] == "closed"
    assert result["state_reason"] == "not_planned"
    calls.clear()
    _, disposition = github.upsert_issue(
        result, result["title"], result["body"], ["status:closed"], None
    )
    assert disposition == "reused" and not calls
