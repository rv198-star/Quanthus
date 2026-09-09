#!/usr/bin/env python3
"""Project only P3-STAGE-002 Stage-10/11 Projection Contract issues."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

ROOT = Path(__file__).resolve().parents[1]
REPO = "rv198-star/Quanthus"
PARENT = "P3-STAGE-002"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in "ABCD"))
EXTERNAL = {"P3-STRUCT-005", "P3-STAGE-001-H"}
PLAN = "config/stage-10-11-projection-delivery.v1.json"


def load_plan() -> dict:
    raw = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    if raw.get("schema_version") != "1.0.0" or raw.get("parent_id") != PARENT:
        raise ValueError("unexpected Stage projection plan identity")
    if tuple(item["id"] for item in raw.get("items", [])) != IDS:
        raise ValueError("Stage projection plan must contain the exact ordered issue set")
    for key in ("design", "review", "projection_contract", "qualification_request"):
        path = (ROOT / raw[key]).resolve()
        if ROOT.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"Stage projection plan path missing: {key}")

    authority = raw.get("authorization")
    expected_authority = {
        "scope": "formal_projection_contract_immutable_f9_qualification_only",
        "projection_contract_implementation_authorized": True,
        "immutable_f9_artifact_qualification_authorized": True,
        "market_data_read_authorized": False,
        "dwd_provider_open_authorized": False,
        "future_outcome_access_authorized": False,
        "sealed_partition_access_authorized": False,
        "permanent_blind_access_authorized": False,
        "primary_stage_arbitration_authorized": False,
        "official_label_card_publication_authorized": False,
        "daily_schedule_authorized": False,
        "strategy_consumption_authorized": False,
        "trading_authorized": False,
    }
    if authority != expected_authority:
        raise ValueError("Stage projection plan authority differs")

    by_id = {item["id"]: item for item in raw["items"]}
    seen = set(EXTERNAL)
    for item in raw["items"][1:]:
        if item["kind"] != "task" or item["status"] not in {"ready", "blocked", "closed"}:
            raise ValueError("invalid Stage projection child kind/status")
        if not set(item["depends_on"]) <= seen:
            raise ValueError("Stage projection child dependency is unknown or out of order")
        if not item["scope"] or not item["acceptance"]:
            raise ValueError("Stage projection child contract is incomplete")
        if item["status"] == "closed" and not item["evidence"]:
            raise ValueError("closed Stage projection child requires evidence")
        if any(not (ROOT / path).is_file() for path in item["evidence"]):
            raise ValueError("Stage projection evidence path is missing")
        if item["status"] == "closed" and any(
            dependency in by_id and by_id[dependency]["status"] != "closed"
            for dependency in item["depends_on"]
        ):
            raise ValueError("Stage projection child cannot close before predecessor")
        seen.add(item["id"])

    parent = by_id[PARENT]
    if parent["kind"] != "epic" or parent["status"] not in {"ready", "blocked", "closed"}:
        raise ValueError("invalid Stage projection parent kind/status")
    if set(parent["depends_on"]) != EXTERNAL:
        raise ValueError("Stage projection parent dependencies differ")
    if raw["runtime_accepted"] is False:
        if raw["runtime_qualification"] is not None:
            raise ValueError("unaccepted Stage projection runtime cannot have qualification")
        expected = {
            PARENT: "ready",
            f"{PARENT}-A": "closed",
            f"{PARENT}-B": "closed",
            f"{PARENT}-C": "ready",
            f"{PARENT}-D": "blocked",
        }
        if {key: by_id[key]["status"] for key in IDS} != expected:
            raise ValueError("pre-qualification Stage projection status differs")
    else:
        qualification = raw.get("runtime_qualification")
        if not isinstance(qualification, dict) or qualification.get("decision") != (
            "PASS_FORMAL_PROJECTION_CONTRACT"
        ):
            raise ValueError("accepted Stage projection runtime lacks PASS qualification")
        if any(by_id[key]["status"] != "closed" for key in IDS):
            raise ValueError("accepted Stage projection milestone must be fully closed")
        if not parent["evidence"] or any(
            not (ROOT / path).is_file() for path in parent["evidence"]
        ):
            raise ValueError("closed Stage projection parent requires evidence")
    return raw


def issue_body(item: dict, raw: dict, mapping: dict) -> str:
    def link(key: str) -> str:
        return f"#{mapping[key]['number']} (`{key}`)" if key in mapping else f"`{key}`"

    evidence = (
        "\n".join(f"- `{path}`" for path in item["evidence"])
        if item["evidence"]
        else "Pending exact runtime acceptance evidence."
    )
    body = [
        f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
        f"## Scope\n{item['scope']}",
        "## Design\n"
        f"- `{raw['design']}`\n"
        f"- `{raw['review']}`\n"
        f"- `{raw['projection_contract']}`\n"
        f"- `{raw['qualification_request']}`",
        "## Dependencies\n" + ", ".join(link(key) for key in item["depends_on"]),
        "## Acceptance\n" + "\n".join(f"- {value}" for value in item["acceptance"]),
        f"## Status\n{item['status']}",
        "## Evidence\n" + evidence,
        "## Authority boundary\n"
        "Only the formal development Projection Contract and immutable accepted-F9 artifact "
        "qualification are authorized. No market-data/DWD read, sealed partition, future outcome, "
        "primary-stage arbitration, official LabelCard, daily schedule, strategy or "
        "trading authority.",
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
    requested = set(IDS) | EXTERNAL
    mapping = issue_by_stable_id(github.issues(), requested)
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
            raise RuntimeError(f"Stage projection issue readback differs: {key}")
        results.append({"id": key, "number": checked["number"], "state": checked["state"]})
    print(json.dumps({"issues": results, "unrelated_issues_changed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
