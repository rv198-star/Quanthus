#!/usr/bin/env python3
"""Validate/synchronize only the planning-scoped stock-stage learning epic and eight tasks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

ROOT = Path(__file__).resolve().parents[1]
REPO = "rv198-star/Quanthus"
PARENT = "P3-STAGE-001"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in "ABCDEFGH"))
PLAN = "config/stock-stage-learning-delivery.v1.json"
EXTERNAL = {"P3-STRUCT-004", "P3-DICT-003"}
AUTH_FLAGS = {
    "training_authorized", "market_result_access_authorized",
    "sealed_partition_access_authorized", "daily_schedule_authorized",
    "cloud_purchase_authorized",
}


def load_plan() -> dict:
    plan = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    if plan.get("schema_version") != "1.0.0" or plan.get("parent_id") != PARENT:
        raise ValueError("unexpected learning plan schema or parent")
    if tuple(item["id"] for item in plan["items"]) != IDS:
        raise ValueError("unexpected learning issue scope")
    auth = plan["authorization"]
    if set(auth) != AUTH_FLAGS | {"scope"} or auth["scope"] != "design_review_issue_planning_and_read_only_capacity":
        raise ValueError("unexpected authorization scope")
    if any(auth[key] is not False for key in AUTH_FLAGS):
        raise ValueError("planning synchronization cannot grant execution or data access")
    for key in ("design", "compute_design", "review", "work_items"):
        path = (ROOT / plan[key]).resolve()
        if ROOT.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"missing planning document: {key}")
    policy = json.loads((ROOT / "config/p0-policy.v1.json").read_text())["validation"]
    for key in ("development_years", "arbitration_years", "permanent_blind_years"):
        if plan["data_policy"][key] != [policy[key]["start"], policy[key]["end"]]:
            raise ValueError("learning plan conflicts with frozen data partitions")
    seen = set(EXTERNAL)
    by_id = {item["id"]: item for item in plan["items"]}
    for item in (*plan["items"][1:], plan["items"][0]):
        if item["status"] not in {"ready", "blocked", "closed"}:
            raise ValueError("invalid issue status")
        if item["kind"] != ("epic" if item["id"] == PARENT else "task"):
            raise ValueError("invalid issue kind")
        if not item["depends_on"] or not set(item["depends_on"]) <= seen:
            raise ValueError("unknown, cyclic, or out-of-order dependency")
        for key in ("title", "scope", "inputs", "deliverables", "acceptance", "blocked_on"):
            if not item.get(key):
                raise ValueError(f"empty work-item contract: {key}")
        if item["status"] == "ready" and item["id"] != f"{PARENT}-A":
            raise ValueError("only the design-freeze task may be ready in this plan")
        for evidence in item["evidence"]:
            path = (ROOT / evidence).resolve()
            if ROOT.resolve() not in path.parents or not path.is_file():
                raise ValueError("missing or unsafe evidence path")
        if item["status"] == "closed" and (not item["evidence"] or any(
            by_id[dep]["status"] != "closed" for dep in item["depends_on"] if dep in by_id
        )):
            raise ValueError("closure requires evidence and closed dependencies")
        seen.add(item["id"])
    if by_id[PARENT]["status"] == "closed" and any(by_id[key]["status"] != "closed" for key in IDS[1:]):
        raise ValueError("parent cannot close before all children")
    return plan


def body(item: dict, plan: dict, mapping: dict) -> str:
    def link(key: str) -> str:
        return f"#{mapping[key]['number']} (`{key}`)" if key in mapping else f"`{key}`"
    sections = [
        f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
        f"## 范围\n{item['scope']}",
        "## 设计与评审\n" + "\n".join(f"- `{plan[key]}`" for key in ("design", "compute_design", "review", "work_items")),
        "## 依赖\n" + ", ".join(link(key) for key in item["depends_on"]),
        "## 输入\n" + "\n".join(f"- {value}" for value in item["inputs"]),
        "## 交付物\n" + "\n".join(f"- {value}" for value in item["deliverables"]),
        "## 验收\n" + "\n".join(f"- [{'x' if item['status'] == 'closed' else ' '}] {value}" for value in item["acceptance"]),
        "## 执行前提\n" + "\n".join(f"- {value}" for value in item["blocked_on"]),
        f"## 状态\n{item['status']}",
        "## 证据\n" + ("\n".join(f"- `{p}`" for p in item["evidence"]) or "未执行，尚无任务验收证据"),
        "## 权限边界\n本次只批准方案、角色评审、工单与只读容量核查。工单创建不是模型训练、市场结果访问、2022-2025分区开放、每日调度、租机或交易授权。",
    ]
    sections.append("## 子项\n" + "\n".join(link(key) for key in IDS[1:]) if item["id"] == PARENT else "## 父工单\n" + link(PARENT))
    return "\n\n".join(sections) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="synchronize only the exact nine planning issues")
    args = parser.parse_args()
    plan = load_plan()
    if not args.apply:
        print(json.dumps({"valid": True, "ids": IDS, "authorization": plan["authorization"]}, ensure_ascii=False))
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
