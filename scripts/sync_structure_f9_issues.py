#!/usr/bin/env python3
"""Synchronize only the exact P3-STRUCT-005 Route-A F9 issue set."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

ROOT = Path(__file__).resolve().parents[1]
REPO = "rv198-star/Quanthus"
PARENT = "P3-STRUCT-005"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in "ABCD"))
PLAN = "config/structure-f9-delivery.v1.json"
EXTERNAL = {"P3-STRUCT-004", "P3-STAGE-001-H"}


def load_plan() -> dict:
    raw = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    if (
        raw.get("schema_version") != "1.0.0"
        or raw.get("parent_id") != PARENT
        or tuple(item["id"] for item in raw.get("items", ())) != IDS
    ):
        raise ValueError("unexpected F9 delivery plan identity")
    for key in ("design", "runtime_request"):
        path = (ROOT / raw[key]).resolve()
        if ROOT.resolve() not in path.parents or not path.is_file():
            raise ValueError("F9 governed path missing or unsafe")
    by_id = {item["id"]: item for item in raw["items"]}
    seen = set(EXTERNAL)
    for item in (*raw["items"][1:], raw["items"][0]):
        if item["status"] not in {"ready", "blocked", "closed"}:
            raise ValueError("invalid F9 work-item status")
        if item["kind"] != ("epic" if item["id"] == PARENT else "task"):
            raise ValueError("invalid F9 work-item kind")
        if not item["depends_on"] or not set(item["depends_on"]) <= seen:
            raise ValueError("unknown or out-of-order F9 dependency")
        for field in ("title", "scope", "acceptance"):
            if not item.get(field):
                raise ValueError(f"empty F9 work-item contract: {field}")
        for evidence in item["evidence"]:
            path = (ROOT / evidence).resolve()
            if ROOT.resolve() not in path.parents or not path.is_file():
                raise ValueError("missing F9 evidence")
        internal_open = any(
            dependency in by_id and by_id[dependency]["status"] != "closed"
            for dependency in item["depends_on"]
        )
        if item["status"] == "closed" and (not item["evidence"] or internal_open):
            raise ValueError("F9 closure requires evidence and closed internal dependencies")
        if item["status"] == "ready" and item["id"] != f"{PARENT}-C":
            raise ValueError("only F9 cold qualification may be ready before runtime acceptance")
        seen.add(item["id"])
    if by_id[PARENT]["status"] == "closed" and (
        raw.get("runtime_accepted") is not True
        or any(by_id[key]["status"] != "closed" for key in IDS[1:])
    ):
        raise ValueError("F9 parent cannot close ahead of runtime and children")
    return raw


def issue_body(item: dict, raw: dict, mapping: dict) -> str:
    def link(key: str) -> str:
        return f"#{mapping[key]['number']} (`{key}`)" if key in mapping else f"`{key}`"

    sections = [
        f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
        f"## Scope\n{item['scope']}",
        f"## Design\n`{raw['design']}`",
        "## Dependencies\n" + ", ".join(link(key) for key in item["depends_on"]),
        "## Acceptance\n"
        + "\n".join(
            f"- [{'x' if item['status'] == 'closed' else ' '}] {gate}"
            for gate in item["acceptance"]
        ),
        f"## Status\n{item['status']}",
        "## Evidence\n"
        + ("\n".join(f"- `{path}`" for path in item["evidence"]) or "Not accepted yet."),
        (
            "## Boundaries\nThis Route-A slice is continuous F9 Structure facts only. It does not "
            "open B6/B7, official Stage labels, economic tuning, sealed partitions, daily/strategy "
            "consumption or trading. Stage 5/6/7 remain parked."
        ),
    ]
    if item["id"] == PARENT:
        sections.append("## Children\n" + "\n".join(link(key) for key in IDS[1:]))
    else:
        sections.append("## Parent\n" + link(PARENT))
    return "\n\n".join(sections) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raw = load_plan()
    if not args.apply:
        print(json.dumps({"apply": False, "items": raw["items"]}, ensure_ascii=False, indent=2))
        return 0
    github = GitHub(REPO)
    mapping = issue_by_stable_id(github.issues(), set(IDS))
    ordered = [*raw["items"][1:], raw["items"][0]]
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
    result = []
    for item in ordered:
        key = item["id"]
        current = mapping[key]
        body = issue_body(item, raw, mapping)
        desired = "closed" if item["status"] == "closed" else "open"
        if current.get("body") != body or current["state"] != desired:
            github.api(
                "PATCH",
                f"/repos/{REPO}/issues/{current['number']}",
                {"body": body, "state": desired},
            )
        checked = github.api("GET", f"/repos/{REPO}/issues/{current['number']}")
        if checked["body"] != body or checked["state"] != desired:
            raise RuntimeError(f"F9 GitHub readback differs for {key}")
        result.append({"id": key, "number": checked["number"], "state": checked["state"]})
    print(json.dumps({"issues": result, "unrelated_issues_changed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
