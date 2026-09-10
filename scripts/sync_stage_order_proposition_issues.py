#!/usr/bin/env python3
"""Project only P3-STAGE-004 Stage-0/2 development-proposition issues."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

ROOT = Path(__file__).resolve().parents[1]
REPO = "rv198-star/Quanthus"
PARENT = "P3-STAGE-004"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in "ABCD"))
EXTERNAL = {"P3-STRUCT-006", "P3-STRUCT-007", "P3-STAGE-003"}
PLAN = "config/stage-order-proposition-delivery.v1.json"


def load_plan() -> dict:
    raw = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    if raw.get("schema_version") != "1.0.0" or raw.get("parent_id") != PARENT:
        raise ValueError("unexpected Stage-0/2 plan identity")
    if tuple(item["id"] for item in raw.get("items", [])) != IDS:
        raise ValueError("Stage-0/2 plan must contain exact ordered issue set")
    for key in ("design", "review", "runtime_config", "panel_config"):
        path = (ROOT / raw[key]).resolve()
        if ROOT.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"Stage-0/2 plan path missing: {key}")
    denied = {
        "future_outcome_access_authorized": False,
        "arbitration_access_authorized": False,
        "permanent_blind_access_authorized": False,
        "holdout_population_selection_authorized": False,
        "official_label_card_publication_authorized": False,
        "primary_stage_arbitration_authorized": False,
        "daily_schedule_authorized": False,
        "strategy_consumption_authorized": False,
        "trading_authorized": False,
    }
    if raw.get("runtime_accepted") is True:
        expected_authority = {
            "scope": "stage_0_2_development_experiment_closed_no_active_search",
            "order_proposition_implementation_authorized": False,
            "development_data_read_authorized": False,
            "synthetic_reference_authorized": False,
            "limited_parameter_search_authorized": False,
            "development_candidate_artifact_authorized": False,
            **denied,
        }
        expected_status = {key: "closed" for key in IDS}
        if raw.get("runtime_qualification") is None:
            raise ValueError("accepted Stage-0/2 plan requires runtime qualification")
    else:
        expected_authority = {
            "scope": "stage_0_2_bounded_development_proposition_experiment_only",
            "order_proposition_implementation_authorized": True,
            "development_data_read_authorized": True,
            "synthetic_reference_authorized": True,
            "limited_parameter_search_authorized": True,
            "development_candidate_artifact_authorized": True,
            **denied,
        }
        expected_status = {
            PARENT: "ready",
            f"{PARENT}-A": "closed",
            f"{PARENT}-B": "closed",
            f"{PARENT}-C": "ready",
            f"{PARENT}-D": "blocked",
        }
        if raw.get("runtime_qualification") is not None:
            raise ValueError("unaccepted Stage-0/2 plan cannot have runtime qualification")
    if raw.get("authorization") != expected_authority:
        raise ValueError("Stage-0/2 plan authority differs")
    retry = raw.get("technical_retry")
    if retry is not None:
        attempts = raw.get("execution_attempts", [])
        if (
            not attempts
            or attempts[0].get("state") != "failed"
            or attempts[0].get("error") != "stage_worker_wall_budget_exceeded"
            or attempts[0].get("result_ref") is not None
            or retry.get("scope") != "runtime_only_same_frozen_experiment"
            or retry.get("maximum_additional_attempts") != 1
            or retry.get("requires_prior_terminal_failure") is not True
            or retry.get("recover_existing_sealed_artifact_first") is not True
            or retry.get("semantic_changes_authorized") is not False
            or retry.get("wall_seconds") != 2700
            or not (ROOT / retry["evidence"]).is_file()
            or (
                retry.get("authorized") is True
                and (raw.get("runtime_accepted") is True or retry.get("job_id") is not None)
            )
        ):
            raise ValueError("Stage-0/2 technical retry authority differs")
        for digest_key, path_key in (
            ("development_config_digest", "runtime_config"),
            ("panel_config_digest", "panel_config"),
        ):
            payload = json.loads((ROOT / raw[path_key]).read_text(encoding="utf-8"))
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=True, allow_nan=False).encode()
            if retry.get(digest_key) != "sha256:" + hashlib.sha256(encoded).hexdigest():
                raise ValueError("Stage-0/2 retry changes a frozen semantic contract")
    by_id = {item["id"]: item for item in raw["items"]}
    if {key: by_id[key]["status"] for key in IDS} != expected_status:
        raise ValueError("Stage-0/2 plan status differs")
    seen = set(EXTERNAL)
    for item in raw["items"][1:]:
        if item["kind"] != "task" or item["status"] not in {"closed", "ready", "blocked"}:
            raise ValueError("invalid Stage-0/2 child kind/status")
        if not set(item["depends_on"]) <= seen:
            raise ValueError("Stage-0/2 dependency unknown or out of order")
        if not item["scope"] or not item["acceptance"]:
            raise ValueError("Stage-0/2 child contract incomplete")
        if item["status"] == "closed" and not item["evidence"]:
            raise ValueError("closed Stage-0/2 child requires evidence")
        if any(not (ROOT / path).is_file() for path in item["evidence"]):
            raise ValueError("Stage-0/2 evidence path missing")
        if item["status"] == "closed" and any(
            dependency in by_id and by_id[dependency]["status"] != "closed"
            for dependency in item["depends_on"]
        ):
            raise ValueError("Stage-0/2 child cannot close before predecessor")
        seen.add(item["id"])
    parent = by_id[PARENT]
    if parent["kind"] != "epic" or set(parent["depends_on"]) != EXTERNAL:
        raise ValueError("Stage-0/2 parent contract differs")
    if parent["status"] == "closed" and not parent["evidence"]:
        raise ValueError("closed Stage-0/2 parent requires evidence")
    return raw


def issue_body(item: dict, raw: dict, mapping: dict) -> str:
    def link(key: str) -> str:
        return f"#{mapping[key]['number']} (`{key}`)" if key in mapping else f"`{key}`"

    evidence = (
        "\n".join(f"- `{path}`" for path in item["evidence"])
        if item["evidence"]
        else "Pending exact development Run evidence."
    )
    authority = (
        "The Stage-0/2 development experiment is closed and no active implementation, data-read, "
        "reference or search authority remains. No holdout, LabelCard, primary-stage, daily, "
        "strategy or trading authority."
        if raw.get("runtime_accepted") is True
        else (
            "Only the frozen 512-security 2020-2021 development experiment is authorized. "
            "References are T-only and synthetic; no future/holdout data, official LabelCard, "
            "primary-stage, daily, strategy or trading authority."
        )
    )
    body = [
        f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
        f"## Scope\n{item['scope']}",
        "## Design\n"
        f"- `{raw['design']}`\n- `{raw['review']}`\n"
        f"- `{raw['runtime_config']}`\n- `{raw['panel_config']}`",
        "## Dependencies\n" + ", ".join(link(key) for key in item["depends_on"]),
        "## Acceptance\n" + "\n".join(f"- {value}" for value in item["acceptance"]),
        f"## Status\n{item['status']}",
        "## Evidence\n" + evidence,
        "## Authority boundary\n" + authority,
    ]
    retry = raw.get("technical_retry")
    if retry is not None and item["id"] in {PARENT, f"{PARENT}-C", f"{PARENT}-D"}:
        previous = raw["execution_attempts"][0]
        body.append(
            "## Execution checkpoint\n"
            f"Original Job `{previous['job_id']}` failed technically: `{previous['error']}`. "
            "This is not a semantic INCONCLUSIVE result. The retry preserves all frozen "
            "reference/search/Fact contracts and the 2700-second limit. "
            f"Additional-attempt cap: {retry['maximum_additional_attempts']}; "
            f"retry authorized: {retry['authorized']}; retry Job: {retry['job_id']}. "
            f"Evidence: `{retry['evidence']}`."
        )
    if item["id"] == PARENT:
        body.append("## Children\n" + "\n".join(link(key) for key in IDS[1:]))
    else:
        body.append("## Parent\n" + link(PARENT))
    return "\n\n".join(body) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raw = load_plan()
    if not args.apply:
        print(json.dumps({"valid": True, "items": raw["items"]}, ensure_ascii=False, indent=2))
        return 0
    github = GitHub(REPO)
    mapping = issue_by_stable_id(github.issues(), set(IDS) | EXTERNAL)
    ordered = [*raw["items"][1:], raw["items"][0]]
    results = []
    for item in ordered:
        key = item["id"]
        labels = [f"type:{item['kind']}", f"status:{item['status']}", "phase:P3", "area:labels"]
        observed, _ = github.upsert_issue(
            mapping.get(key),
            f"[{key}] {item['title']}",
            issue_body(item, raw, mapping),
            labels,
            None,
        )
        mapping[key] = observed
    for item in ordered:
        key = item["id"]
        current = mapping[key]
        desired_body = issue_body(item, raw, mapping)
        desired_state = "closed" if item["status"] == "closed" else "open"
        if current.get("body") != desired_body or current["state"] != desired_state:
            github.api(
                "PATCH",
                f"/repos/{REPO}/issues/{current['number']}",
                {"body": desired_body, "state": desired_state},
            )
        checked = github.api("GET", f"/repos/{REPO}/issues/{current['number']}")
        if checked["body"] != desired_body or checked["state"] != desired_state:
            raise RuntimeError(f"Stage-0/2 issue readback differs: {key}")
        results.append({"id": key, "number": checked["number"], "state": checked["state"]})
    print(json.dumps({"issues": results, "unrelated_issues_changed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
