#!/usr/bin/env python3
"""Project only P3-STRUCT-006 Trend Evolution Fact Family issues."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

ROOT = Path(__file__).resolve().parents[1]
REPO = "rv198-star/Quanthus"
PARENT = "P3-STRUCT-006"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in "ABCD"))
EXTERNAL = {"P3-STAGE-003", "P3-STRUCT-005"}
PLAN = "config/structure-trend-evolution-delivery.v1.json"


def load_plan() -> dict:
    raw = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    if raw.get("schema_version") != "1.0.0" or raw.get("parent_id") != PARENT:
        raise ValueError("unexpected Trend Evolution plan identity")
    if tuple(item["id"] for item in raw.get("items", [])) != IDS:
        raise ValueError("Trend Evolution plan must contain exact ordered issue set")
    for key in ("design", "fact_contract", "qualification_request"):
        path = (ROOT / raw[key]).resolve()
        if ROOT.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"Trend Evolution plan path missing: {key}")

    denied = {
        "market_data_read_authorized": False,
        "dwd_provider_open_authorized": False,
        "stage_projection_authorized": False,
        "parameter_search_authorized": False,
        "official_label_card_publication_authorized": False,
        "future_outcome_access_authorized": False,
        "sealed_partition_access_authorized": False,
        "permanent_blind_access_authorized": False,
        "primary_stage_arbitration_authorized": False,
        "daily_schedule_authorized": False,
        "strategy_consumption_authorized": False,
        "trading_authorized": False,
    }
    if raw.get("runtime_accepted") is True:
        expected_authority = {
            "scope": "trend_evolution_fact_family_closed_no_active_qualification",
            "formal_structure_fact_implementation_authorized": False,
            "immutable_accepted_source_artifact_qualification_authorized": False,
            **denied,
        }
    else:
        expected_authority = {
            "scope": "trend_evolution_fact_implementation_and_immutable_source_qualification_only",
            "formal_structure_fact_implementation_authorized": True,
            "immutable_accepted_source_artifact_qualification_authorized": True,
            **denied,
        }
    if raw.get("authorization") != expected_authority:
        raise ValueError("Trend Evolution plan authority differs")

    by_id = {item["id"]: item for item in raw["items"]}
    if raw.get("runtime_accepted") is True:
        if raw.get("runtime_qualification") is None:
            raise ValueError("accepted Trend Evolution plan requires qualification")
        expected_status = {key: "closed" for key in IDS}
    else:
        if raw.get("runtime_qualification") is not None:
            raise ValueError("unaccepted Trend Evolution plan cannot have qualification")
        expected_status = {
            PARENT: "ready",
            f"{PARENT}-A": "closed",
            f"{PARENT}-B": "closed",
            f"{PARENT}-C": "ready",
            f"{PARENT}-D": "blocked",
        }
    if {key: by_id[key]["status"] for key in IDS} != expected_status:
        raise ValueError("Trend Evolution status differs")

    seen = set(EXTERNAL)
    for item in raw["items"][1:]:
        if item["kind"] != "task" or item["status"] not in {"closed", "ready", "blocked"}:
            raise ValueError("invalid Trend Evolution child kind/status")
        if not set(item["depends_on"]) <= seen:
            raise ValueError("Trend Evolution dependency unknown or out of order")
        if not item["scope"] or not item["acceptance"]:
            raise ValueError("Trend Evolution child contract incomplete")
        if item["status"] == "closed" and not item["evidence"]:
            raise ValueError("closed Trend Evolution child requires evidence")
        if any(not (ROOT / path).is_file() for path in item["evidence"]):
            raise ValueError("Trend Evolution evidence path missing")
        if item["status"] == "closed" and any(
            dependency in by_id and by_id[dependency]["status"] != "closed"
            for dependency in item["depends_on"]
        ):
            raise ValueError("Trend Evolution child cannot close before predecessor")
        seen.add(item["id"])
    parent = by_id[PARENT]
    if parent["kind"] != "epic" or set(parent["depends_on"]) != EXTERNAL:
        raise ValueError("Trend Evolution parent contract differs")
    if parent["status"] == "closed" and not parent["evidence"]:
        raise ValueError("closed Trend Evolution parent requires evidence")
    return raw


def issue_body(item: dict, raw: dict, mapping: dict) -> str:
    def link(key: str) -> str:
        return f"#{mapping[key]['number']} (`{key}`)" if key in mapping else f"`{key}`"

    evidence = (
        "\n".join(f"- `{path}`" for path in item["evidence"])
        if item["evidence"]
        else "Pending exact immutable-source qualification evidence."
    )
    authority = (
        "Trend Evolution is accepted and no active implementation/qualification authority "
        "remains. No DWD/provider, Stage projection/search, LabelCard, holdout, daily, strategy "
        "or trading authority."
        if raw.get("runtime_accepted") is True
        else (
            "Only Trend Evolution Fact implementation and qualification from the two frozen "
            "immutable accepted source artifacts are authorized. No DWD/provider, Stage "
            "projection/search, "
            "LabelCard, holdout, daily, strategy or trading authority."
        )
    )
    body = [
        f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
        f"## Scope\n{item['scope']}",
        "## Design\n"
        f"- `{raw['design']}`\n- `{raw['fact_contract']}`\n- `{raw['qualification_request']}`",
        "## Dependencies\n" + ", ".join(link(key) for key in item["depends_on"]),
        "## Acceptance\n" + "\n".join(f"- {value}" for value in item["acceptance"]),
        f"## Status\n{item['status']}",
        "## Evidence\n" + evidence,
        "## Authority boundary\n" + authority,
    ]
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
            raise RuntimeError(f"Trend Evolution issue readback differs: {key}")
        results.append({"id": key, "number": checked["number"], "state": checked["state"]})
    print(json.dumps({"issues": results, "unrelated_issues_changed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
