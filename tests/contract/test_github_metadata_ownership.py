from copy import deepcopy
from pathlib import Path

import pytest

from scripts import sync_github_planning as planning


class MemoryGitHub(planning.GitHub):
    def __init__(self, *, refresh_metadata=False):
        super().__init__("test/project", refresh_metadata=refresh_metadata)
        self.calls = []

    def api(self, method, endpoint, payload=None):
        self.calls.append((method, endpoint, deepcopy(payload)))
        issue = {"number": 1, "html_url": "https://example.test/1", "state": "open", **payload}
        issue["labels"] = [{"name": value} for value in payload.get("labels", [])]
        issue["milestone"] = (
            None if payload.get("milestone") is None else {"number": payload["milestone"]}
        )
        issue["type"] = None if payload.get("type") is None else {"name": payload["type"]}
        return issue


@pytest.mark.parametrize("kind", ["Epic", "Feature", "Bug", "Task"])
def test_four_native_types_and_compatibility_labels_are_idempotent(kind):
    github = MemoryGitHub()
    labels = [f"type:{kind.casefold()}", "status:ready", "area:docs"]
    issue, result = github.upsert_issue(None, "[TEST-001] Goal", "Scope", labels, None)
    assert result == "created" and issue["type"]["name"] == kind
    github.calls.clear()
    _, result = github.upsert_issue(issue, issue["title"], issue["body"], labels, None)
    assert result == "reused" and github.calls == []


def test_normal_sync_preserves_live_type_status_scope_and_milestone():
    github = MemoryGitHub()
    existing = {
        "number": 1,
        "state": "open",
        "type": {"name": "Feature"},
        "title": "[TEST-001] Live scope",
        "body": "Live acceptance",
        "labels": [
            {"name": name}
            for name in ["type:task", "status:in-progress", "priority:high", "phase:P3", "custom"]
        ],
        "milestone": {"number": 999},
    }
    issue, _ = github.upsert_issue(
        existing,
        "[TEST-001] Old scope",
        "Old acceptance",
        ["type:task", "status:closed", "priority:normal", "phase:P1", "area:docs"],
        3,
    )
    payload = github.calls[0][2]
    assert payload["title"] == existing["title"] and payload["body"] == existing["body"]
    assert payload["milestone"] == 999 and "state" not in payload
    assert payload["type"] == "Feature"
    assert payload["labels"] == sorted(
        ["type:feature", "status:in-progress", "priority:high", "phase:P3", "custom", "area:docs"]
    )
    github.calls.clear()
    _, result = github.upsert_issue(
        issue,
        "[TEST-001] Old",
        "Old",
        ["type:task", "status:closed", "priority:normal", "phase:P1", "area:docs"],
        3,
    )
    assert result == "reused" and not github.calls


def test_legacy_type_is_adopted_and_missing_status_cannot_accidentally_close_live_work():
    github = MemoryGitHub()
    existing = {
        "number": 1,
        "title": "[TEST-001] Bug",
        "body": "Live",
        "state": "open",
        "labels": [{"name": "type:bug"}],
        "milestone": None,
    }
    issue, _ = github.upsert_issue(
        existing, existing["title"], "Stale", ["type:task", "status:closed"], None
    )
    assert issue["type"]["name"] == "Bug" and issue["state"] == "open"
    assert "status:triage" in [value["name"] for value in issue["labels"]]
    github.calls.clear()
    _, result = github.upsert_issue(
        issue, existing["title"], "Stale", ["type:task", "status:closed"], None
    )
    assert result == "reused" and not github.calls


def test_explicit_refresh_uses_plan_but_preserves_v2_contract_and_unmanaged_labels():
    github = MemoryGitHub(refresh_metadata=True)
    existing = {
        "number": 1,
        "title": "[TEST-001] Old",
        "state": "open",
        "body": "<!-- quanthus-planning:v2 id=TEST-001 -->\nAcceptance",
        "labels": [{"name": "custom"}, {"name": "status:in-progress"}],
        "type": {"name": "Task"},
        "milestone": {"number": 999},
    }
    issue, _ = github.upsert_issue(
        existing,
        "[TEST-001] New",
        "<!-- quanthus-planning:v1 -->",
        ["type:bug", "status:closed"],
        3,
    )
    assert issue["type"]["name"] == "Bug" and issue["title"] == "[TEST-001] New"
    assert issue["body"] == existing["body"] and issue["milestone"]["number"] == 3
    assert issue["state"] == "closed"
    assert {value["name"] for value in issue["labels"]} == {"custom", "type:bug", "status:closed"}


@pytest.mark.parametrize("refresh", [False, True])
def test_closed_history_retains_not_planned_reason(refresh):
    github = MemoryGitHub(refresh_metadata=refresh)
    existing = {
        "number": 1,
        "title": "[TEST-001] Cancelled",
        "state": "closed",
        "state_reason": "not_planned",
        "body": "Original",
        "labels": [],
        "type": {"name": "Feature"},
        "milestone": None,
    }
    issue, result = github.upsert_issue(
        existing, "[TEST-001] Generated", "Generated", ["type:task", "status:closed"], 3
    )
    assert issue == existing and result == "reused" and not github.calls


@pytest.mark.parametrize("refresh", [False, True])
def test_migrated_p10_contract_keeps_live_native_type_and_matching_label(refresh):
    github = MemoryGitHub(refresh_metadata=refresh)
    existing = {
        "number": 1,
        "title": "[TEST-001] Migrated data",
        "state": "open",
        "body": "<!-- quanthus-data-owner:P10 id=TEST-001 -->\nLive gates",
        "labels": [
            {"name": name} for name in ["type:task", "phase:P10", "area:data", "status:in-progress"]
        ],
        "type": {"name": "Bug"},
        "milestone": {"number": 88},
    }
    issue, _ = github.upsert_issue(
        existing,
        "[TEST-001] Generic",
        "Generic",
        ["type:task", "status:closed", "phase:P10", "area:data"],
        3,
    )
    assert issue["body"] == existing["body"] and issue["title"] == existing["title"]
    assert issue["type"]["name"] == "Bug" and issue["milestone"]["number"] == 88
    assert issue["state"] == "open"
    assert {label["name"] for label in issue["labels"]} == {
        "type:bug",
        "phase:P10",
        "area:data",
        "status:in-progress",
    }


def test_explicit_source_column_accepts_four_types_and_rejects_typos(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    row = next(row for row in planning.index_rows(root) if row["ID"] != planning.P10_EPIC_ID)
    for kind in ("Epic", "Feature", "Bug", "Task"):
        monkeypatch.setattr(planning, "index_rows", lambda _, kind=kind: [{**row, "Type": kind}])
        assert planning.work_items(root)[0].issue_type == kind.casefold()
    monkeypatch.setattr(planning, "index_rows", lambda _: [{**row, "Type": "Typo"}])
    with pytest.raises(ValueError, match="invalid Issue Type"):
        planning.work_items(root)
