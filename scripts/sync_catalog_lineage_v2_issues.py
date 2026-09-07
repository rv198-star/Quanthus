#!/usr/bin/env python3
"""Synchronize only the P3-DICT-003 Catalog Lineage V2 epic and children."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

ROOT = Path(__file__).resolve().parents[1]
REPO = "rv198-star/Quanthus"
PARENT = "P3-DICT-003"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in "ABCD"))
PLAN = "config/data-catalog-lineage-v2-delivery.v1.json"


def load_plan() -> dict:
    plan = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    if plan.get("parent_id") != PARENT or tuple(i["id"] for i in plan["items"]) != IDS:
        raise ValueError("unexpected lineage-v2 issue scope")
    by_id = {item["id"]: item for item in plan["items"]}
    for item in plan["items"]:
        if item["status"] not in {"ready", "blocked", "closed"}:
            raise ValueError("invalid issue state")
        if any(not (ROOT / path).is_file() for path in item["evidence"]):
            raise ValueError("missing evidence")
        if item["status"] == "closed" and (not item["evidence"] or any(
            by_id[dep]["status"] != "closed" for dep in item["depends_on"] if dep in by_id
        )):
            raise ValueError("closure requires evidence and closed dependencies")
    if by_id[PARENT]["status"] == "closed" and any(by_id[key]["status"] != "closed" for key in IDS[1:]):
        raise ValueError("parent cannot close before children")
    return plan


def body(item: dict, plan: dict, mapping: dict) -> str:
    def link(key: str) -> str:
        return f"#{mapping[key]['number']} (`{key}`)" if key in mapping else f"`{key}`"
    sections = [
        f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
        f"## 范围\n{item['scope']}",
        f"## 设计\n`{plan['design']}`\n\n`{PLAN}`",
        "## 依赖\n" + ", ".join(link(key) for key in item["depends_on"]),
        "## 验收\n" + "\n".join(f"- [{'x' if item['status'] == 'closed' else ' '}] {text}" for text in item["acceptance"]),
        f"## 状态\n{item['status']}",
        "## 证据\n" + ("\n".join(f"- `{p}`" for p in item["evidence"]) or "尚未验收"),
        "## 边界\n只读 Catalog/血缘整改；不修改 ODS/DWD/ADM 物理数据、公式、策略或执行权限。",
    ]
    if item["id"] == PARENT:
        sections.append("## 子项\n" + "\n".join(link(key) for key in IDS[1:]))
    else:
        sections.append("## 父工单\n" + link(PARENT))
    return "\n\n".join(sections) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = load_plan()
    if not args.apply:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    github = GitHub(REPO)
    mapping = issue_by_stable_id(github.issues(), set(IDS))
    ordered = [*plan["items"][1:], plan["items"][0]]
    for item in ordered:
        labels = [f"type:{item['kind']}", f"status:{item['status']}", "phase:P3", "area:labels"]
        observed, _ = github.upsert_issue(mapping.get(item["id"]), f"[{item['id']}] {item['title']}", body(item, plan, mapping), labels, None)
        mapping[item["id"]] = observed
    result = []
    for item in ordered:
        current = mapping[item["id"]]
        desired = "closed" if item["status"] == "closed" else "open"
        expected = body(item, plan, mapping)
        github.api("PATCH", f"/repos/{REPO}/issues/{current['number']}", {"body": expected, "state": desired})
        checked = github.api("GET", f"/repos/{REPO}/issues/{current['number']}")
        if checked["body"] != expected or checked["state"] != desired:
            raise RuntimeError("issue readback differs")
        result.append({"id": item["id"], "number": checked["number"], "state": desired})
    print(json.dumps({"issues": result, "unrelated_issues_changed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
