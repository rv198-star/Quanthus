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
    denied = {
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
    if raw["runtime_accepted"] is False:
        expected_authority = {
            "scope": "formal_projection_contract_immutable_f9_qualification_only",
            "projection_contract_implementation_authorized": True,
            "immutable_f9_artifact_qualification_authorized": True,
            **denied,
        }
    else:
        expected_authority = {
            "scope": "formal_projection_contract_closed_no_active_qualification",
            "projection_contract_implementation_authorized": False,
            "immutable_f9_artifact_qualification_authorized": False,
            **denied,
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
        required_qualification = {
            "decision": "PASS_FORMAL_PROJECTION_CONTRACT",
            "code_revision": "1cccd0ac230bcb39d89b4514fd08c8dfed639215",
            "job_id": "job_5e2d82adb2a8492488725fcda379424f",
            "run_id": "run_5e2d82adb2a8492488725fcda379424f",
            "input_digest": (
                "sha256:fe32ef536d4494f269b6e829b2dbaead797528181f61b275639c2c2f39ce2db3"
            ),
            "manifest_sha256": (
                "sha256:ce9418eed4861bcdf17d38eea9dcee297a4921ac33fd34e0714b40c3a6d85b94"
            ),
            "projection_contract_digest": (
                "sha256:4452283774f1c0a30faa6f95b03076f3191b5fd0036835f71046ba047490876d"
            ),
            "formal_projection_digest": (
                "sha256:47797766b987d7886de2be86587c7cbd4d7af8d9551d4dc38cd96eb3c6f8a221"
            ),
            "source_support_facts_digest": (
                "sha256:6abbd976591739ede4451fd15ad2a11e8dd98519cc8039c2374f575c0035aff8"
            ),
            "result_digest": (
                "sha256:2e1ae9ffceb500948dc08401d3b8672c0f267b659ea2e71f2837e43138f22dea"
            ),
            "row_count": 241,
            "source_run_count": 2,
            "stage_10_confirmation_count": 2,
            "stage_10_context_count": 10,
            "stage_11_rebound_condition_count": 6,
            "nested_10_11_candidate_count": 6,
            "primary_stage_non_null_count": 0,
            "legacy_predicate_compatibility": True,
            "market_data_reads": 0,
            "dwd_provider_opens": 0,
            "evidence": "docs/acceptance/stage-10-11-projection-qualification-20260910.md",
        }
        if qualification != required_qualification:
            raise ValueError("accepted Stage projection runtime qualification differs")
        if not (ROOT / qualification["evidence"]).is_file():
            raise ValueError("accepted Stage projection runtime evidence is missing")
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
    authority_text = (
        "The formal development Projection Contract is accepted and no active implementation or "
        "qualification authority remains. No market-data/DWD read, sealed partition, future "
        "outcome, primary-stage arbitration, official LabelCard, daily schedule, strategy or "
        "trading authority."
        if raw["runtime_accepted"] is True
        else (
            "Only the formal development Projection Contract and immutable accepted-F9 artifact "
            "qualification are authorized. No market-data/DWD read, sealed partition, future "
            "outcome, primary-stage arbitration, official LabelCard, daily schedule, strategy or "
            "trading authority."
        )
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
        "## Authority boundary\n" + authority_text,
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
