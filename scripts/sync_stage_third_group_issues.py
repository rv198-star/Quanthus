#!/usr/bin/env python3
"""Project only P3-STAGE-003 third-group morphology-profile issues."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

ROOT = Path(__file__).resolve().parents[1]
REPO = "rv198-star/Quanthus"
PARENT = "P3-STAGE-003"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in "ABCD"))
EXTERNAL = {"P3-STAGE-002", "P3-STRUCT-005"}
PLAN = "config/stage-third-group-delivery.v1.json"


def load_plan() -> dict:
    raw = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    if raw.get("schema_version") != "1.0.0" or raw.get("parent_id") != PARENT:
        raise ValueError("unexpected third-group plan identity")
    if tuple(item["id"] for item in raw.get("items", [])) != IDS:
        raise ValueError("third-group plan must contain exact ordered issue set")
    required_paths = ["design", "review", "runtime_config"]
    if raw.get("runtime_accepted") is True:
        required_paths.append("post_run_review")
    for key in required_paths:
        path = (ROOT / raw[key]).resolve()
        if ROOT.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"third-group plan path missing: {key}")

    denied = {
        "parameter_search_authorized": False,
        "stage_label_output_authorized": False,
        "formal_structure_fact_registration_authorized": False,
        "future_outcome_access_authorized": False,
        "arbitration_access_authorized": False,
        "permanent_blind_access_authorized": False,
        "holdout_population_selection_authorized": False,
        "primary_stage_arbitration_authorized": False,
        "daily_schedule_authorized": False,
        "strategy_consumption_authorized": False,
        "trading_authorized": False,
    }
    if raw.get("runtime_accepted") is True:
        expected_authority = {
            "scope": "third_group_profile_closed_no_active_search_or_fact_authority",
            "third_group_profile_implementation_authorized": False,
            "third_group_development_profile_run_authorized": False,
            "development_data_read_authorized": False,
            "continuous_profile_output_authorized": False,
            **denied,
        }
    else:
        expected_authority = {
            "scope": "third_group_development_profile_only_no_labels_no_search",
            "third_group_profile_implementation_authorized": True,
            "third_group_development_profile_run_authorized": True,
            "development_data_read_authorized": True,
            "continuous_profile_output_authorized": True,
            **denied,
        }
    if raw.get("authorization") != expected_authority:
        raise ValueError("third-group plan authority differs")

    by_id = {item["id"]: item for item in raw["items"]}
    if raw.get("runtime_accepted") is True:
        expected_status = {key: "closed" for key in IDS}
        qualification = raw.get("runtime_qualification")
        if not isinstance(qualification, dict):
            raise ValueError("accepted third-group plan requires qualification")
        required_qualification = {
            "decision": "PASS_PROFILE_WITH_DIMENSIONAL_RECLASSIFICATION",
            "code_revision": "0a15ccc48ad00ab3f170f903e2ee0d27a385c26d",
            "job_id": "stage_313d48cfc06948e0a7fc7a3bd77b2657",
            "run_id": "stage-third-group-profile-20260910T031429Z",
            "manifest_sha256": (
                "sha256:351f749db94742e847cb873796105c2d855bdc402ed664400e04678ac9a121c7"
            ),
            "input_digest": (
                "sha256:3a362fb1cb1664e4eeb894c49cd3b75e7b556f67eac3914320ace76da638e340"
            ),
            "profile_observation_count": 225246,
        }
        for key, value in required_qualification.items():
            if qualification.get(key) != value:
                raise ValueError(f"accepted third-group qualification differs: {key}")
        if qualification.get("successor_order") != [
            "trend_evolution_fact_family",
            "structure_order_fact_family",
            "high_side_activity_relation_if_still_required",
        ]:
            raise ValueError("third-group successor order differs")
        evidence = qualification.get("evidence")
        if not isinstance(evidence, str) or not (ROOT / evidence).is_file():
            raise ValueError("third-group qualification evidence missing")
    else:
        if raw.get("runtime_qualification") is not None:
            raise ValueError("unaccepted third-group profile cannot have qualification")
        expected_status = {
            PARENT: "blocked",
            f"{PARENT}-A": "closed",
            f"{PARENT}-B": "closed",
            f"{PARENT}-C": "ready",
            f"{PARENT}-D": "blocked",
        }
    if {key: by_id[key]["status"] for key in IDS} != expected_status:
        raise ValueError("third-group status differs")

    seen = set(EXTERNAL)
    for item in raw["items"][1:]:
        if item["kind"] != "task" or item["status"] not in {"closed", "ready", "blocked"}:
            raise ValueError("invalid third-group child kind/status")
        if not set(item["depends_on"]) <= seen:
            raise ValueError("third-group dependency unknown or out of order")
        if not item["scope"] or not item["acceptance"]:
            raise ValueError("third-group child contract incomplete")
        if item["status"] == "closed" and not item["evidence"]:
            raise ValueError("closed third-group child requires evidence")
        if any(not (ROOT / path).is_file() for path in item["evidence"]):
            raise ValueError("third-group evidence path missing")
        if item["status"] == "closed" and any(
            dependency in by_id and by_id[dependency]["status"] != "closed"
            for dependency in item["depends_on"]
        ):
            raise ValueError("third-group child cannot close before predecessor")
        seen.add(item["id"])
    parent = by_id[PARENT]
    if parent["kind"] != "epic" or set(parent["depends_on"]) != EXTERNAL:
        raise ValueError("third-group parent contract differs")
    if parent["status"] == "closed" and not parent["evidence"]:
        raise ValueError("closed third-group parent requires evidence")
    return raw


def issue_body(item: dict, raw: dict, mapping: dict) -> str:
    def link(key: str) -> str:
        return f"#{mapping[key]['number']} (`{key}`)" if key in mapping else f"`{key}`"

    evidence = (
        "\n".join(f"- `{path}`" for path in item["evidence"])
        if item["evidence"]
        else "Pending runtime/profile evidence."
    )
    if raw.get("runtime_accepted") is True:
        authority = (
            "The third-group morphology profile and post-run dimensional review are closed. "
            "No active profile run, Stage-label search, formal Facts registration, holdout, "
            "primary-stage arbitration, daily, strategy or trading authority remains."
        )
    else:
        authority = (
            "Only one 512-security 2020-2021 development morphology profile is authorized. "
            "No Stage labels, threshold search, B6/B7 formalization, future/holdout data, "
            "primary-stage arbitration, daily, strategy or trading authority."
        )
    design_lines = [
        f"- `{raw['design']}`",
        f"- `{raw['review']}`",
        f"- `{raw['runtime_config']}`",
    ]
    if raw.get("post_run_review"):
        design_lines.append(f"- `{raw['post_run_review']}`")
    body = [
        f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
        f"## Scope\n{item['scope']}",
        "## Design\n" + "\n".join(design_lines),
        "## Dependencies\n" + ", ".join(link(key) for key in item["depends_on"]),
        "## Acceptance\n" + "\n".join(f"- {value}" for value in item["acceptance"]),
        f"## Status\n{item['status']}",
        "## Evidence\n" + evidence,
        "## Authority boundary\n" + authority,
    ]
    if raw.get("runtime_accepted") is True:
        body.append(
            "## Frozen successor route\n"
            "1. Trend Evolution Fact Family / 趋势演化事实族\n"
            "2. Structure Order Fact Family / 结构秩序事实族\n"
            "3. High-side activity relation only if still required for Stage 9"
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
            raise RuntimeError(f"third-group issue readback differs: {key}")
        results.append({"id": key, "number": checked["number"], "state": checked["state"]})
    print(json.dumps({"issues": results, "unrelated_issues_changed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
