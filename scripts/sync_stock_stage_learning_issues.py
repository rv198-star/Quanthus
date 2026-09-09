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
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in "ABCDEFGHIJ"))
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
HARNESS_FLAG = "stage_harness_implementation_authorized"
REFERENCE_FLAG = "stage_reference_pilot_implementation_authorized"
DEVELOPMENT_SETTLEMENT_FLAG = "development_future_settlement_authorized"
LEARNING_IMPL_FLAG = "development_learning_implementation_authorized"
SYNTHETIC_REFERENCE_FLAG = "synthetic_expert_development_reference_authorized"
SYNTHETIC_LEARNING_FLAG = "synthetic_expert_development_learning_authorized"
BOUNDED_ARBITRATION_FLAG = "bounded_arbitration_qualification_authorized"
BOUNDED_ARBITRATION_CONFIG = "bounded_arbitration_qualification_config"
PERMANENT_BLIND_QUALIFICATION_FLAG = "permanent_blind_qualification_authorized"
AUTH_SCOPE = "development_closed_arbitration_consumed_inconclusive_no_holdout_authority"


def load_plan() -> dict:
    plan = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    if plan.get("schema_version") != "1.0.0" or plan.get("parent_id") != PARENT:
        raise ValueError("unexpected learning plan schema or parent")
    if tuple(item["id"] for item in plan["items"]) != IDS:
        raise ValueError("unexpected learning issue scope")

    auth = plan["authorization"]
    expected_auth_fields = DENIED_AUTH_FLAGS | {
        CAPACITY_FLAG,
        HARNESS_FLAG,
        REFERENCE_FLAG,
        DEVELOPMENT_SETTLEMENT_FLAG,
        LEARNING_IMPL_FLAG,
        SYNTHETIC_REFERENCE_FLAG,
        SYNTHETIC_LEARNING_FLAG,
        BOUNDED_ARBITRATION_FLAG,
        BOUNDED_ARBITRATION_CONFIG,
        PERMANENT_BLIND_QUALIFICATION_FLAG,
        "scope",
    }
    if set(auth) != expected_auth_fields or auth["scope"] != AUTH_SCOPE:
        raise ValueError("unexpected authorization scope")
    if any(
        auth[key] is not True
        for key in (
            CAPACITY_FLAG,
            HARNESS_FLAG,
            REFERENCE_FLAG,
            DEVELOPMENT_SETTLEMENT_FLAG,
            LEARNING_IMPL_FLAG,
            SYNTHETIC_REFERENCE_FLAG,
            SYNTHETIC_LEARNING_FLAG,
        )
    ):
        raise ValueError(
            "capacity, harness, reference, synthetic-panel and learning authorizations "
            "must be explicit"
        )
    if auth[BOUNDED_ARBITRATION_FLAG] is not False:
        raise ValueError("consumed arbitration qualification cannot remain authorized")
    if auth[PERMANENT_BLIND_QUALIFICATION_FLAG] is not False:
        raise ValueError("permanent-blind qualification cannot be opened by planning sync")
    qualification_config = auth.get(BOUNDED_ARBITRATION_CONFIG)
    if qualification_config != "config/stage-learning/qualification.v1.json":
        raise ValueError("bounded arbitration qualification config differs")
    qualification_path = (ROOT / qualification_config).resolve()
    if ROOT.resolve() not in qualification_path.parents or not qualification_path.is_file():
        raise ValueError("bounded arbitration qualification config is missing or unsafe")
    qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
    qualification_authority = qualification.get("authority", {})
    if (
        qualification.get("work_item") != f"{PARENT}-F"
        or qualification_authority.get(
            "one_time_arbitration_semantic_evaluation_authorized"
        )
        is not True
        or qualification_authority.get("arbitration_access_budget_runs") != 1
        or qualification_authority.get("permanent_blind_access_authorized") is not False
        or qualification_authority.get("parameter_search_authorized") is not False
        or qualification_authority.get("qualified_label_publication_authorized") is not False
    ):
        raise ValueError("bounded arbitration qualification authority differs")
    p3_gate = json.loads((ROOT / "config/p3-gates.v1.json").read_text(encoding="utf-8"))
    bounded = p3_gate.get("bounded_partition_exceptions", [])
    if (
        not isinstance(bounded, list)
        or len(bounded) != 1
        or bounded[0].get("id") != f"{PARENT}-F"
        or bounded[0].get("state") != "consumed_inconclusive"
        or bounded[0].get("remaining_access_budget_runs") != 0
        or bounded[0].get("consumed_job_id")
        != "stage_cf5b89c16c5b4fce8b3c5cdc00a156f7"
    ):
        raise ValueError("consumed arbitration qualification evidence differs")
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
        if item["status"] == "ready":
            raise ValueError(
                "no stock-stage task may be ready while successor holdout is unauthorized"
            )
        if (
            item["id"] == f"{PARENT}-F"
            and item["status"] == "ready"
            and any(by_id[key]["status"] != "closed" for key in (f"{PARENT}-D", f"{PARENT}-E"))
        ):
            raise ValueError("held-out qualification cannot be ready before D/E close")
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
        "P3-STAGE-001-D/E development selection 已关闭并冻结候选；"
        "P3-STAGE-001-F 的唯一2022-2024 arbitration访问预算已由"
        " `stage_cf5b89c16c5b4fce8b3c5cdc00a156f7` 消耗并以sample-feasibility "
        "INCONCLUSIVE关闭。不得重跑该分区并称fresh holdout。"
        "P3-STAGE-001-I 已关闭protocol-only采样修复，P3-STAGE-001-J保持blocked，"
        "尚未选择或授权新的holdout。2025 permanent-blind仍未开放；当前不授权新的"
        "sealed-partition读取、参数搜索、结果后改候选、QUALIFIED LabelCard、日更、"
        "策略消费、云GPU或交易。"
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
