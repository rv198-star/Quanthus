#!/usr/bin/env python3
"""Selectively route the reviewed open data issues to permanent P10.

Default is offline preview. Apply preserves legacy scope/evidence/status and journals each
verified mutation. It does not execute collection, source recovery, deployment or data repair.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

if __package__:
    from .sync_github_planning import (
        DEFAULT_REPOSITORY,
        LABELS,
        P10_DATA_MILESTONE,
        P10_EPIC_ID,
        GitHub,
        load_p10_policy,
    )
else:
    from sync_github_planning import (
        DEFAULT_REPOSITORY,
        LABELS,
        P10_DATA_MILESTONE,
        P10_EPIC_ID,
        GitHub,
        load_p10_policy,
    )

NOTICE = re.compile(
    r"\n\n<!-- quanthus-data-(?:owner|consumer):[^\n]* -->\n.*?"
    r"<!-- /quanthus-data-(?:owner|consumer) -->",
    re.DOTALL,
)


def migration_payload(
    policy: dict[str, Any], issue: dict[str, Any], epic_url: str, milestone: int
) -> dict[str, Any] | None:
    if issue["state"].casefold() != "open":
        return None
    number = issue["number"]
    route = next(
        (row for row in policy["issue_migration"]["issues"] if row["number"] == number), None
    )
    consumer = next((row for row in policy["consumer_issues"] if row["number"] == number), None)
    if route is None and consumer is None:
        raise ValueError(f"issue #{number} is outside the reviewed P10 plan")
    old_labels = {label["name"] for label in issue["labels"]}
    if route is not None:
        if issue["title"] != route["title"]:
            raise ValueError(
                f"issue #{number} title changed; review its ownership before migration"
            )
        labels = {name for name in old_labels if not name.startswith("phase:")}
        labels |= {"phase:P10", "area:data"}
        kind, owner = "owner", "P10"
        scope = (
            "采集、来源恢复、数据质量调查/修复、治理及版本化供给统一由 P10 承接。"
            "保留本 issue 的原编号、证据、依赖及执行/发布门禁；迁移不代表已修复或验收。"
        )
    else:
        assert consumer is not None
        labels = {
            name
            for name in old_labels
            if not name.startswith(("phase:", "status:")) and name != "area:data"
        }
        labels |= {*consumer["labels"], "status:blocked"}
        kind, owner = "consumer", consumer["owner"]
        dependencies = ", ".join(f"#{n}" for n in consumer["input_dependencies"])
        choice = {
            "bypass": "绕行",
            "temporarily_ignore": "暂时忽略",
            "accept_known_limitation": "接受已知限制",
        }[consumer["selected_business_disposition"]]
        scope = (
            f"本 issue 保留 {owner} 业务与消费准入职责；数据问题只登记到 P10（{dependencies}），"
            "不得自行查源、补采、补正或维护临时数据修复。业务选择并记录绕行、暂时忽略或"
            "接受已知限制，聚焦业务本身。当前 blocked 仅指未满足既有硬门禁的步骤，"
            "不冻结整条业务线，也不改变已封存证据或自动开放训练/回测。"
            f"当前选择：{choice}。{consumer['business_disposition_note']}"
        )
    notice = (
        f"<!-- quanthus-data-{kind}:{owner} policy={policy['policy_version']} -->\n"
        f"## 当前数据职责（Owner 2026-10-03）\n\n"
        f"永久数据线：[{P10_EPIC_ID}]({epic_url})。{scope}\n\n"
        "非数据线遇到质量/治理问题只能提 issue，不自行处理；业务选择绕行、暂时忽略或"
        "接受已知限制并记录影响。PIT、保护分区、新入场安全及既有激活门禁仍有效。"
        "P10 issue 关闭不自动证明消费输入合格。\n"
        f"<!-- /quanthus-data-{kind} -->"
    )
    original = issue.get("body") or ""
    if "<!-- quanthus-data-" in original and not NOTICE.search(original):
        raise ValueError(f"issue #{number} has an incomplete ownership notice")
    body = NOTICE.sub("", original) + "\n\n" + notice
    payload: dict[str, Any] = {"labels": sorted(labels), "body": body}
    if route is not None:
        payload["milestone"] = milestone
    return payload


def epic_body(policy: dict[str, Any], repository: str) -> str:
    groups: dict[str, list[str]] = {}
    for row in policy["issue_migration"]["issues"]:
        groups.setdefault(row["category"], []).append(f"#{row['number']}")
    rows = "\n".join(f"- {group}: {', '.join(numbers)}" for group, numbers in groups.items())
    return (
        f"<!-- quanthus-planning:v2 id={P10_EPIC_ID} kind=epic -->\n"
        "永久数据线，负责数据采集、来源恢复、质量治理、不可变发布与业务供给。"
        "本 epic 长期开放；有限修复子任务按各自证据关闭，不自动关闭永久数据线。\n\n"
        "非数据线发现质量/治理问题只能登记 issue，不自行处理；业务选择并记录绕行、"
        "暂时忽略或接受已知限制，聚焦业务目标。现有硬性时点、安全和激活门禁保留。\n\n"
        f"[职责设计](https://github.com/{repository}/blob/main/{policy['governing_design']}) · "
        f"[版本化归属](https://github.com/{repository}/blob/main/config/data-ownership.v1.json)\n\n"
        "当前迁移范围（保留原编号/证据/依赖，迁移不代表验收）：\n\n" + rows + "\n\n"
        "#424 保留原运维子计划及其任务级 SSH/部署门禁。P3 #412 和 P5 #402 保留业务消费准入，"
        "不再承担数据修复；P3/P5 输入仍未验收。此迁移不启动采集、部署、清理或修改 P6。"
    )


def journal(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def apply_plan(root: Path, repository: str, receipt: Path) -> dict[str, Any]:
    policy = load_p10_policy(root)
    github = GitHub(repository)
    labels = {item["name"].casefold(): item for item in github.labels()}
    for spec in LABELS:
        if spec.name in {"phase:P10", "area:data"}:
            github.upsert_label(spec, labels)
    milestones = {item["title"]: item for item in github.milestones()}
    milestone, _ = github.upsert_milestone(P10_DATA_MILESTONE, milestones)
    routed_issues = github.pages(f"/repos/{repository}/issues?state=all&labels=phase%3AP10")
    epics = [x for x in routed_issues if x["title"].startswith(f"[{P10_EPIC_ID}]")]
    if len(epics) > 1:
        raise ValueError("duplicate permanent P10 epics")
    if epics:
        epic = epics[0]
        if epic["state"] != "open":
            raise ValueError("the standing P10 epic is closed; do not create a replacement")
    else:
        epic = github.api(
            "POST",
            f"/repos/{repository}/issues",
            {
                "title": (
                    f"[{P10_EPIC_ID}] Coordinate permanent data acquisition, quality and governance"
                ),
                "body": epic_body(policy, repository),
                "labels": ["type:epic", "status:ready", "priority:high", "phase:P10", "area:data"],
                "milestone": milestone,
            },
        )
    journal(receipt, {"event": "epic", "number": epic["number"], "url": epic["html_url"]})
    numbers = [x["number"] for x in policy["issue_migration"]["issues"]]
    numbers += [x["number"] for x in policy["consumer_issues"]]
    changed, reused, skipped = [], [], []
    for number in numbers:
        endpoint = f"/repos/{repository}/issues/{number}"
        before = github.api("GET", endpoint)
        payload = migration_payload(policy, before, epic["html_url"], milestone)
        if payload is None:
            skipped.append(number)
            journal(receipt, {"event": "closed_preserved", "number": number})
            continue
        current = github.api("GET", endpoint)
        if current["updated_at"] != before["updated_at"] or current["body"] != before["body"]:
            raise RuntimeError(
                f"issue #{number} changed during preparation; rerun with fresh state"
            )
        unchanged = (
            sorted(x["name"] for x in before["labels"]) == payload["labels"]
            and before["body"] == payload["body"]
            and (
                "milestone" not in payload
                or (before.get("milestone") or {}).get("number") == payload["milestone"]
            )
        )
        journal(
            receipt,
            {
                "event": "prepared",
                "number": number,
                "body_sha256": hashlib.sha256((before["body"] or "").encode()).hexdigest(),
                "labels": [x["name"] for x in before["labels"]],
                "milestone": (before.get("milestone") or {}).get("number"),
            },
        )
        if not unchanged:
            github.api("PATCH", endpoint, payload)
        after = github.api("GET", endpoint)
        if (
            after["title"] != before["title"]
            or after["state"] != before["state"]
            or sorted(x["name"] for x in after["labels"]) != payload["labels"]
            or after["body"] != payload["body"]
            or (
                "milestone" in payload
                and (after.get("milestone") or {}).get("number") != payload["milestone"]
            )
        ):
            raise RuntimeError(f"issue #{number} migration verification failed")
        (reused if unchanged else changed).append(number)
        journal(
            receipt,
            {
                "event": "verified",
                "number": number,
                "changed": not unchanged,
                "state": after["state"],
                "title_preserved": True,
                "body_sha256": hashlib.sha256(after["body"].encode()).hexdigest(),
                "labels": payload["labels"],
            },
        )
    return {
        "epic": epic["html_url"],
        "changed": changed,
        "reused": reused,
        "closed_preserved": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPOSITORY)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.apply:
        if args.receipt is None:
            parser.error("--apply requires an external --receipt journal")
        result = apply_plan(root, args.repo, args.receipt)
    else:
        policy = load_p10_policy(root)
        result = {
            "epic": P10_EPIC_ID,
            "policy": policy["policy_version"],
            "data_issue_numbers": [x["number"] for x in policy["issue_migration"]["issues"]],
            "consumer_issue_numbers": [x["number"] for x in policy["consumer_issues"]],
            "business_dispositions": sorted(policy["business_dispositions"]),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
