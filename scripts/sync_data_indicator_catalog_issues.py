#!/usr/bin/env python3
"""Project only the approved P3-DICT-001 Catalog epic and five children to GitHub."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

ROOT = Path(__file__).resolve().parents[1]
REPO = "rv198-star/Quanthus"
PARENT = "P3-DICT-001"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in ("A", "B", "C", "D", "E")))


def load_plan() -> dict:
    raw = json.loads((ROOT / "config/data-indicator-catalog-delivery.v1.json").read_text())
    if raw.get("parent_id") != PARENT or tuple(item["id"] for item in raw.get("items", [])) != IDS:
        raise ValueError("catalog delivery plan must contain exactly the admitted ordered IDs")
    if not (ROOT / raw["design"]).is_file():
        raise ValueError("catalog governing design is missing")
    by_id = {item["id"]: item for item in raw["items"]}
    for item in raw["items"]:
        if item["status"] not in {"ready", "blocked", "closed"}:
            raise ValueError("unknown catalog delivery status")
        if item["status"] == "closed" and not item["evidence"]:
            raise ValueError("closed catalog issue requires evidence")
        if any(not (ROOT / path).is_file() for path in item["evidence"]):
            raise ValueError("catalog closure evidence path is missing")
        if item["status"] == "closed" and any(
            dependency in by_id and by_id[dependency]["status"] != "closed"
            for dependency in item["depends_on"]
        ):
            raise ValueError("catalog child cannot close before its predecessor")
    if by_id[PARENT]["status"] == "closed" and any(
        by_id[key]["status"] != "closed" for key in IDS[1:]
    ):
        raise ValueError("catalog parent cannot close before all children")
    return raw


def issue_body(item: dict, raw: dict, mapping: dict) -> str:
    def link(key: str) -> str:
        return f"#{mapping[key]['number']} ({key})" if key in mapping else f"`{key}`"

    content = [
        f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
        f"## Scope\n{item['scope']}",
        f"## Design\n`{raw['design']}`\n\nhttps://github.com/{REPO}/blob/main/{raw['design']}",
        f"## Dependencies\n{', '.join(link(key) for key in item['depends_on']) or 'None'}",
        "## Acceptance\n" + "\n".join(
            f"- [{'x' if item['status'] == 'closed' else ' '}] {gate}"
            for gate in item["acceptance"]
        ),
        (
            f"## Current disposition\n{item['status']}; "
            "catalog delivery grants no formula, model or execution authority."
        ),
        "## Evidence\n" + (
            "\n".join(f"- `{path}`" for path in item["evidence"])
            if item["evidence"]
            else "Not accepted; implementation does not replace evidence."
        ),
        (
            "## Boundaries\nRead-only catalog projection only. Existing "
            "ODS/DWD/Feature/Label/Semantic sources remain authoritative; no online authoring, "
            "market-value mutation, 13-state/ML, Shadow or live activation."
        ),
    ]
    if item["id"] == PARENT:
        content.append("## Children\n" + "\n".join(
            (
                f"- [{'x' if child['status'] == 'closed' else ' '}] "
                f"{link(child['id'])}: {child['title']}"
            )
            for child in raw["items"][1:]
        ))
    else:
        content.append(f"## Parent\n{link(PARENT)}")
    return "\n\n".join(content) + "\n"


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
            raise RuntimeError(f"catalog issue readback differs for {key}")
        result.append(
            {
                "id": key,
                "number": checked["number"],
                "state": checked["state"],
                "url": checked["html_url"],
            }
        )
    print(
        json.dumps(
            {"repository": REPO, "issues": result, "unrelated_issues_changed": False},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
