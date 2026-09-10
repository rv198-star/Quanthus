#!/usr/bin/env python3
"""Project only P3-STAGE-005 scale-explicit reference-mechanism issues."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

ROOT = Path(__file__).resolve().parents[1]
REPO = "rv198-star/Quanthus"
PARENT = "P3-STAGE-005"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in "ABC"))
PLAN = "config/stage-order-reference-delivery.v1.json"
EXTERNAL = {"P3-STAGE-004"}


def load_plan() -> dict:
    raw = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    if raw.get("schema_version") != "1.0.0" or raw.get("parent_id") != PARENT:
        raise ValueError("reference delivery identity differs")
    expected_scope = "scale_explicit_reference_mechanism_and_synthetic_engineering_acceptance_only"
    if raw.get("scope") != expected_scope:
        raise ValueError("reference delivery scope differs")
    if (raw.get("market_reference_validated") is not False
            or raw.get("old_experiment_reopened") is not False):
        raise ValueError("reference delivery expands research authority")
    if tuple(item["id"] for item in raw.get("items", [])) != IDS:
        raise ValueError("reference delivery must use its exact issue set")
    for key in ("design", "review", "protocol"):
        path = (ROOT / raw[key]).resolve()
        if ROOT.resolve() not in path.parents or not path.is_file():
            raise ValueError("reference delivery file missing or outside repository")
    protocol = json.loads((ROOT / raw["protocol"]).read_text(encoding="utf-8"))
    allowed = {"mechanism_implementation", "synthetic_engineering_validation"}
    authority_keys = allowed | {
        "market_data_acquisition", "market_reference_pilot", "parameter_search",
        "future_outcome_access", "arbitration_access", "permanent_blind_access",
        "official_labels", "daily_schedule", "strategy_consumption", "trading",
    }
    if protocol.get("authority") != {key: key in allowed for key in authority_keys}:
        raise ValueError("reference protocol authorizes more than engineering")
    by_id = {item["id"]: item for item in raw["items"]}
    if raw.get("engineering_accepted") is True:
        expected = {key: "closed" for key in IDS}
    elif raw.get("engineering_accepted") is False:
        expected = dict(zip(IDS, ("ready", "closed", "ready", "blocked"), strict=True))
    else:
        raise ValueError("reference engineering acceptance must be explicit")
    if {key: item["status"] for key, item in by_id.items()} != expected:
        raise ValueError("reference statuses differ from engineering gate")
    seen = set(EXTERNAL)
    for item in raw["items"][1:]:
        if item["kind"] != "task" or not set(item["depends_on"]) <= seen:
            raise ValueError("reference dependency unknown or out of order")
        if not item["scope"] or not item["acceptance"]:
            raise ValueError("reference task contract incomplete")
        if item["status"] == "closed" and not item["evidence"]:
            raise ValueError("closed reference task lacks evidence")
        for evidence in item["evidence"]:
            path = (ROOT / evidence).resolve()
            if ROOT.resolve() not in path.parents or not path.is_file():
                raise ValueError("reference task evidence missing")
        seen.add(item["id"])
    parent = by_id[PARENT]
    if parent["kind"] != "epic" or set(parent["depends_on"]) != EXTERNAL:
        raise ValueError("reference parent differs")
    if raw["engineering_accepted"] and not parent["evidence"]:
        raise ValueError("accepted reference parent lacks evidence")
    return raw


def issue_body(item: dict, raw: dict, mapping: dict) -> str:
    def link(key: str) -> str:
        return f"#{mapping[key]['number']} (`{key}`)" if key in mapping else f"`{key}`"

    sections = [
        f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
        f"## Scope\n{item['scope']}",
        "## Design\n" + "\n".join(f"- `{raw[key]}`" for key in ("design", "review", "protocol")),
        "## Dependencies\n" + ", ".join(link(key) for key in item["depends_on"]),
        "## Acceptance\n" + "\n".join(f"- {value}" for value in item["acceptance"]),
        f"## Status\n{item['status']}",
        "## Evidence\n" + ("\n".join(f"- `{path}`" for path in item["evidence"]) or "Pending."),
        "## Boundary\nSynthetic engineering mechanism only. No new market run, resampling, "
        "parameter search, 2022-2024 reread, 2025 access, official label, daily, "
        "strategy or trading. "
        "P3-STAGE-004 remains closed and INCONCLUSIVE. Role names do not prove independence.",
    ]
    sections.append(
        "## Children\n" + "\n".join(link(key) for key in IDS[1:])
        if item["id"] == PARENT else "## Parent\n" + link(PARENT)
    )
    return "\n\n".join(sections) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raw = load_plan()
    if not args.apply:
        print(json.dumps({"valid": True, "items": raw["items"]}, ensure_ascii=False))
        return 0
    github = GitHub(REPO)
    mapping = issue_by_stable_id(github.issues(), set(IDS) | EXTERNAL)
    ordered = [*raw["items"][1:], raw["items"][0]]
    for item in ordered:
        labels = [f"type:{item['kind']}", f"status:{item['status']}", "phase:P3", "area:labels"]
        observed, _ = github.upsert_issue(
            mapping.get(item["id"]), f"[{item['id']}] {item['title']}",
            issue_body(item, raw, mapping), labels, None,
        )
        mapping[item["id"]] = observed
    results = []
    for item in ordered:
        current = mapping[item["id"]]
        body = issue_body(item, raw, mapping)
        state = "closed" if item["status"] == "closed" else "open"
        endpoint = f"/repos/{REPO}/issues/{current['number']}"
        if current.get("body") != body or current["state"] != state:
            github.api("PATCH", endpoint, {"body": body, "state": state})
        checked = github.api("GET", endpoint)
        if checked["body"] != body or checked["state"] != state:
            raise RuntimeError("reference issue readback differs")
        results.append({"id": item["id"], "number": checked["number"], "state": state})
    print(json.dumps({"issues": results, "unrelated_issues_changed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
