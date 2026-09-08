#!/usr/bin/env python3
"""Validate/synchronize only the stock-stage learning epic and eight bounded tasks."""
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
DENIED_AUTH_FLAGS = {
    "training_authorized",
    "market_result_access_authorized",
    "sealed_partition_access_authorized",
    "daily_schedule_authorized",
    "cloud_purchase_authorized",
}
CAPACITY_FLAG = "capacity_benchmark_authorized"
AUTH_SCOPE = "design_review_issue_planning_and_capacity_benchmark"


def load_plan() -> dict:
    plan = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    if plan.get("schema_version") != "1.0.0" or plan.get("parent_id") != PARENT:
        raise ValueError("unexpected learning plan schema or parent")
    if tuple(item["id"] for item in plan["items"]) != IDS:
        raise ValueError("unexpected learning issue scope")

    auth = plan["authorization"]
    expected_auth_fields = DENIED_AUTH_FLAGS | {CAPACITY_FLAG, "scope"}
    if set(auth) != expected_auth_fields or auth["scope"] != AUTH_SCOPE:
        raise ValueError("unexpected authorization scope")
    if auth[CAPACITY_FLAG] is not True:
        raise ValueError("capacity benchmark authorization must be explicit")
    if any(auth[key] is not False for key in DENIED_AUTH_FLAGS):
        raise ValueError(
            "planning synchronization cannot grant training, result, schedule or purchase access"
        )

    for key in ("design", "compute_design", "review", "work_items"):
        path = (ROOT / plan[key]).resolve()
        if ROOT.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"missing planning document: {key}")

    policy = json.loads((ROOT / "config/p0-policy.v1.json").read_text())["validation"]
    for key in ("development_years", "arbitration_years", "permanent_blind_years"):
        expected = [policy[key]["start"], policy[key]["end"]]
        if plan["data_policy"][key] != expected:
            raise ValueError("learning plan conflicts with frozen data partitions")

    seen = set(EXTERNAL)
    by_id = {item["id"]: item for item in plan["items"]}
    for item in (*plan["items"][1:], plan["items"][0]):
        if item["status"] not in {"ready", "blocked", "closed"}:
            raise ValueError("invalid issue status")
        expected_kind = "epic" if item["id"] == PARENT else "task"
        if item["kind"] != expected_kind:
            raise ValueError("invalid issue kind")
        if not item["depends_on"] or not set(item["depends_on"]) <= seen:
            raise ValueError("unknown, cyclic, or out-of-order dependency")
        for key in (
            "title",
            "scope",
            "inputs",
            "deliverables",
            "acceptance",
            "blocked_on",
        ):
            if not item.get(key):
                raise ValueError(f"empty work-item contract: {key}")
        if item["status"] == "ready" and item["id"] != f"{PARENT}-B":
            raise ValueError("only the capacity task may be ready after A freeze")
        if (
            item["id"] == f"{PARENT}-B"
            and item["status"] == "ready"
            and by_id[f"{PARENT}-A"]["status"] != "closed"
        ):
            raise ValueError("capacity task cannot be ready before A closes")
        for evidence in item["evidence"]:
            path = (ROOT / evidence).resolve()
            if ROOT.resolve() not in path.parents or not path.is_file():
                raise ValueError("missing or unsafe evidence path")
        internal_open_dependency = any(
            by_id[dependency]["status"] != "closed"
            for dependency in item["depends_on"]
            if dependency in by_id
        )
        if item["status"] == "closed" and (
            not item["evidence"] or internal_open_dependency
        ):
            raise ValueError("closure requires evidence and closed dependencies")
        seen.add(item["id"])

    child_open = any(by_id[key]["status"] != "closed" for key in IDS[1:])
    if by_id[PARENT]["status"] == "closed" and child_open:
        raise ValueError("parent cannot close before all children")
    return plan


def body(item: dict, plan: dict, mapping: dict) -> str:
    def link(key: str) -> str:
        if key in mapping:
            return f"#{mapping[key]['number']} (`{key}`)"
        return f"`{key}`"

    design_paths = ("design", "compute_design", "review", "work_items")
    acceptance = "\n".join(
        f"- [{'x' if item['status'] == 'closed' else ' '}] {value}"
        for value in item["acceptance"]
    )
    evidence = "\n".join(f"- `{path}`" for path in item["evidence"])
    if not evidence:
        evidence = "未执行，尚无任务验收证据"
    authority = (
        "## 权限边界\n"
        "本次允许P3-STAGE-001-B的CPU-first容量benchmark；不授权市场分类器训练、"
        "未来结果访问、2022-2025开发读取、每日调度、云GPU采购或交易。"
    )
    sections = [
        f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
        f"## 范围\n{item['scope']}",
        "## 设计与评审\n"
        + "\n".join(f"- `{plan[key]}`" for key in design_paths),
        "## 依赖\n" + ", ".join(link(key) for key in item["depends_on"]),
        "## 输入\n" + "\n".join(f"- {value}" for value in item["inputs"]),
        "## 交付物\n" + "\n".join(f"- {value}" for value in item["deliverables"]),
        "## 验收\n" + acceptance,
        "## 执行前提\n" + "\n".join(f"- {value}" for value in item["blocked_on"]),
        f"## 状态\n{item['status']}",
        "## 证据\n" + evidence,
        authority,
    ]
    if item["id"] == PARENT:
        sections.append("## 子项\n" + "\n".join(link(key) for key in IDS[1:]))
    else:
        sections.append("## 父工单\n" + link(PARENT))
    return "\n\n".join(sections) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="synchronize only the exact nine planning issues",
    )
    args = parser.parse_args()
    plan = load_plan()
    if not args.apply:
        output = {"valid": True, "ids": IDS, "authorization": plan["authorization"]}
        print(json.dumps(output, ensure_ascii=False))
        return 0

    github = GitHub(REPO)
    mapping = issue_by_stable_id(github.issues(), set(IDS))
    ordered = [*plan["items"][1:], plan["items"][0]]
    for item in ordered:
        labels = [
            f"type:{item['kind']}",
            f"status:{item['status']}",
            "phase:P3",
            "area:labels",
        ]
        observed, _ = github.upsert_issue(
            mapping.get(item["id"]),
            f"[{item['id']}] {item['title']}",
            body(item, plan, mapping),
            labels,
            None,
        )
        mapping[item["id"]] = observed

    result = []
    for item in ordered:
        current = mapping[item["id"]]
        desired = "closed" if item["status"] == "closed" else "open"
        expected = body(item, plan, mapping)
        github.api(
            "PATCH",
            f"/repos/{REPO}/issues/{current['number']}",
            {"body": expected, "state": desired},
        )
        checked = github.api("GET", f"/repos/{REPO}/issues/{current['number']}")
        if checked["body"] != expected or checked["state"] != desired:
            raise RuntimeError("issue readback differs")
        result.append(
            {"id": item["id"], "number": checked["number"], "state": desired}
        )
    print(
        json.dumps(
            {"issues": result, "unrelated_issues_changed": False}, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
