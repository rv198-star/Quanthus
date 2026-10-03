import copy
import json
from pathlib import Path

import pytest

from scripts.sync_github_planning import (
    LABELS,
    P10_DATA_MILESTONE,
    P10_EPIC_ID,
    GitHub,
    load_p10_policy,
    task_body,
    work_items,
)
from scripts.sync_p10_data_issues import epic_body, migration_payload

ROOT = Path(__file__).resolve().parents[2]
POLICY = load_p10_policy(ROOT)
EPIC_URL = "https://github.com/rv198-star/Quanthus/issues/999"


def _issue(number: int, *, state: str = "open") -> dict:
    row = next((x for x in POLICY["issue_migration"]["issues"] if x["number"] == number), None)
    return {
        "number": number,
        "title": row["title"] if row else f"[consumer-{number}] admission",
        "state": state,
        "body": "Original evidence and accepted hashes.\n\nKeep parent #424 and gates.\n",
        "labels": [
            {"name": x} for x in ["bug", "status:triage", "phase:meta", "area:observability"]
        ],
        "milestone": {"number": 7},
    }


def test_p10_is_parallel_permanent_and_has_a_standing_planning_epic() -> None:
    items = {item.stable_id: item for item in work_items(ROOT)}
    item = items[P10_EPIC_ID]
    assert (item.phase, item.status, item.issue_type, item.milestone) == (
        "P10",
        "ready",
        "epic",
        P10_DATA_MILESTONE,
    )
    assert POLICY["sequential_phase"] is False and POLICY["auto_close_epic"] is False
    assert "phase:P10" in {label.name for label in LABELS}
    assert "not be automatically closed" in task_body(item, "rv198-star/Quanthus")
    assert items["META-OPS-RELIABILITY-001"].phase == "P10"
    assert items["META-DATA-PIPE-002"].phase == "P10"
    assert items["P1-DATA-004"].phase == "P1" and items["P1-DATA-004"].status == "closed"
    assert items["P3-STRAT-003-B-R1"].phase == "P3"
    assert items["P3-STRAT-003-B-R1"].status == "blocked"
    assert items["P6-DATA-001"].phase == "P6"


@pytest.mark.parametrize("row", POLICY["issue_migration"]["issues"])
def test_each_reviewed_open_data_issue_preserves_history_and_existing_gates(row: dict) -> None:
    original = _issue(row["number"])
    before = copy.deepcopy(original)
    payload = migration_payload(POLICY, original, EPIC_URL, 88)
    assert original == before
    assert payload is not None
    assert payload["body"].startswith(original["body"])
    assert {"phase:P10", "area:data", "bug", "status:triage", "area:observability"} == set(
        payload["labels"]
    )
    assert payload["milestone"] == 88
    assert "state" not in payload and "title" not in payload
    after = {**original, **payload, "labels": [{"name": x} for x in payload["labels"]]}
    assert migration_payload(POLICY, after, EPIC_URL, 88) == payload


@pytest.mark.parametrize("number", [402, 412])
def test_consumers_keep_domain_ownership_and_record_three_business_choices(number: int) -> None:
    issue = _issue(number)
    issue["labels"].append({"name": "area:data"})
    payload = migration_payload(POLICY, issue, EPIC_URL, 88)
    assert payload is not None
    assert "phase:P10" not in payload["labels"] and "area:data" not in payload["labels"]
    assert "status:blocked" in payload["labels"]
    assert "milestone" not in payload  # preserve its existing research milestone
    assert payload["body"].startswith(issue["body"])
    assert all(word in payload["body"] for word in ["绕行", "暂时忽略", "接受已知限制"])
    assert "不冻结整条业务线" in payload["body"]
    assert "不得自行查源" in payload["body"]
    assert "当前选择：暂时忽略" in payload["body"]


def test_closed_history_and_unreviewed_consumer_issues_cannot_be_migrated() -> None:
    assert migration_payload(POLICY, _issue(474, state="closed"), EPIC_URL, 88) is None
    with pytest.raises(ValueError, match="outside the reviewed"):
        migration_payload(POLICY, _issue(119), EPIC_URL, 88)
    issue = _issue(474)
    issue["title"] = "Changed scope"
    with pytest.raises(ValueError, match="title changed"):
        migration_payload(POLICY, issue, EPIC_URL, 88)


def test_incomplete_notice_fails_instead_of_overwriting_original_evidence() -> None:
    issue = _issue(474)
    issue["body"] += "\n\n<!-- quanthus-data-owner:P10 broken -->\npartial"
    with pytest.raises(ValueError, match="incomplete ownership notice"):
        migration_payload(POLICY, issue, EPIC_URL, 88)


@pytest.mark.parametrize(
    "case", ["duplicate", "consumer_overlap", "auto_close", "missing_choice", "invalid_choice"]
)
def test_invalid_ownership_policy_fails_before_remote_updates(tmp_path: Path, case: str) -> None:
    policy = copy.deepcopy(POLICY)
    if case == "duplicate":
        policy["issue_migration"]["issues"].append(policy["issue_migration"]["issues"][0])
    elif case == "consumer_overlap":
        policy["consumer_issues"][0]["number"] = 474
    elif case == "auto_close":
        policy["auto_close_epic"] = True
    elif case == "invalid_choice":
        policy["consumer_issues"][0]["selected_business_disposition"] = "repair_privately"
    else:
        del policy["business_dispositions"]["temporarily_ignore"]
    (tmp_path / "config").mkdir()
    (tmp_path / "config/data-ownership.v1.json").write_text(json.dumps(policy))
    with pytest.raises(ValueError):
        load_p10_policy(tmp_path)


def test_broad_planning_refresh_cannot_erase_a_migrated_data_issue_or_close_it() -> None:
    class RecordingGitHub(GitHub):
        def api(self, method: str, endpoint: str, payload: dict | None = None):
            raise AssertionError("an unchanged legacy route must not call GitHub")

    existing = _issue(474)
    payload = migration_payload(POLICY, existing, EPIC_URL, 88)
    existing.update(payload)
    existing["labels"] = [{"name": x} for x in payload["labels"]]
    existing["milestone"] = {"number": 88}
    observed, status = RecordingGitHub("rv198-star/Quanthus").upsert_issue(
        existing,
        "[old-id] new generated title",
        "new generated body",
        ["phase:meta", "status:closed"],
        None,
    )
    assert observed == existing and status == "reused"


def test_permanent_epic_cannot_be_closed_by_a_planning_refresh() -> None:
    with pytest.raises(ValueError, match="cannot be automatically closed"):
        GitHub("rv198-star/Quanthus").upsert_issue(
            None, f"[{P10_EPIC_ID}] Permanent data", "body", ["status:closed"], 88
        )
    body = epic_body(POLICY, "rv198-star/Quanthus")
    assert "quanthus-planning:v2" in body and "长期开放" in body
    assert "#424" in body and "#470" in body and "#474" in body


def test_planning_does_not_relabel_a_legacy_issue_closed_before_migration() -> None:
    class NoUpdates(GitHub):
        def api(self, method: str, endpoint: str, payload: dict | None = None):
            raise AssertionError("closed historical issue must remain untouched")

    existing = _issue(474, state="closed")
    observed, status = NoUpdates("rv198-star/Quanthus").upsert_issue(
        existing, existing["title"], "new generated body", ["phase:P10", "area:data"], 88
    )
    assert observed == existing and status == "reused"
