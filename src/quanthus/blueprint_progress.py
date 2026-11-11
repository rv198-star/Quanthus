"""Evidence-bound project progress; never a research or execution authority.

The catalog owns topology and work-item links. The append-only ledger owns reported
module outcomes. GitHub owns issue lifecycle. Views reconcile these without deriving
scientific acceptance from issue closure or counting optional heads as mandatory gates.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

CATALOG = "config/planning/learning-blueprint.v1.json"
LEDGER = "config/planning/learning-progress.v1.json"
VIEW = "docs/work-items/learning-blueprint-progress.md"
REPOSITORY = "rv198-star/Quanthus"
FACETS = {
    "engineering": {"unassessed", "not_started", "partial", "accepted", "blocked"},
    "quality": {"unassessed", "research_only", "accepted", "inconclusive", "rejected", "blocked"},
    "value": {"unassessed", "not_evaluated", "accepted", "inconclusive", "rejected", "blocked"},
    "admission": {"unassessed", "not_granted", "development_only", "research_only", "accepted"},
}
LABELS = {
    "unassessed": "未评估",
    "not_started": "未开始",
    "partial": "部分完成",
    "accepted": "限定范围通过",
    "blocked": "受阻",
    "research_only": "仅研究",
    "inconclusive": "未得明确结论",
    "rejected": "本候选拒绝",
    "not_evaluated": "未验证",
    "not_granted": "未授予",
    "development_only": "仅开发",
}
FACET_LABELS = {
    "engineering": "工程",
    "quality": "识别/研究质量",
    "value": "用途价值",
    "admission": "使用资格",
}
ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")


class ProgressError(ValueError):
    """Invalid linkage or unsupported progress claim."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProgressError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result

    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ProgressError("expected JSON object")
    return result


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def nonempty(value: Any, name: str) -> None:
    require(isinstance(value, str) and bool(value.strip()), f"missing {name}")


def timestamp(value: Any) -> datetime:
    nonempty(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProgressError("invalid timestamp") from exc
    require(parsed.utcoffset() is not None, "timestamp requires timezone")
    return parsed


def indexed(rows: Any, name: str) -> dict[str, dict[str, Any]]:
    require(isinstance(rows, list) and bool(rows), f"empty {name}")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        require(isinstance(row, dict), f"invalid {name} row")
        key = row.get("id", "")
        require(isinstance(key, str) and bool(ID.fullmatch(key)), f"invalid {name} id")
        require(key not in result, f"duplicate {name}: {key}")
        result[key] = row
    return result


def acyclic(graph: dict[str, list[str]]) -> None:
    active: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> None:
        require(node not in active, f"module cycle: {node}")
        if node in done:
            return
        active.add(node)
        for dependency in graph[node]:
            require(dependency in graph, f"unknown module: {dependency}")
            visit(dependency)
        active.remove(node)
        done.add(node)

    for node in graph:
        visit(node)


def validate_catalog(catalog: dict[str, Any]) -> None:
    require(catalog.get("schema_version") == "quanthus.blueprint.v1", "catalog schema")
    require(catalog.get("repository") == REPOSITORY, "repository identity mismatch")
    require(catalog.get("grants_authority") is False, "blueprint cannot grant authority")
    modules = indexed(catalog.get("modules"), "modules")
    work = indexed(catalog.get("work_items"), "work items")
    graph: dict[str, list[str]] = {}
    numbers: set[int] = set()
    for key, module in modules.items():
        nonempty(module.get("name"), "module name")
        nonempty(module.get("purpose"), "module purpose")
        require(module.get("focus_facet") in FACETS, "module focus facet")
        for pattern in module.get("watch_paths", []):
            require(
                isinstance(pattern, str)
                and pattern.startswith(("src/", "config/", "scripts/"))
                and ".." not in PurePosixPath(pattern).parts,
                "invalid impact path",
            )
        require(
            module.get("kind") in {"capability", "state", "control", "dependency"},
            f"module kind: {key}",
        )
        parent = module.get("parent")
        require(parent is None or parent in modules, f"unknown parent: {key}")
        dependencies = module.get("depends_on", [])
        require(isinstance(dependencies, list), "dependencies must be a list")
        graph[key] = dependencies
    acyclic(graph)
    acyclic({key: [m["parent"]] if m.get("parent") else [] for key, m in modules.items()})
    for item in work.values():
        number = item.get("issue")
        if type(number) is not int or number <= 0 or number in numbers:
            raise ProgressError("duplicate/invalid issue number")
        numbers.add(number)
        require(
            bool(item.get("modules")) and set(item["modules"]) <= modules.keys(),
            "work item has unknown/empty module links",
        )
        nonempty(item.get("contribution"), "work contribution")


def verify_evidence(evidence: dict[str, Any], root: Path) -> None:
    require(bool(SHA.fullmatch(str(evidence.get("sha256", "")))), "evidence SHA256 required")
    nonempty(evidence.get("claim_ceiling"), "evidence claim ceiling")
    require(evidence.get("kind") in {"acceptance", "assessment", "authority"}, "evidence kind")
    if evidence.get("location") == "git":
        revision = evidence.get("revision", "")
        path = evidence.get("path", "")
        require(bool(REVISION.fullmatch(str(revision))), "full evidence Git SHA required")
        parts = PurePosixPath(path).parts
        require(
            bool(parts) and parts[0] in {"docs", "config"} and ".." not in parts,
            "evidence must reference repository governance metadata",
        )
        require(path.endswith((".md", ".json")), "non-metadata evidence path")
        result = subprocess.run(
            ["git", "show", f"{revision}:{path}"], cwd=root, capture_output=True, check=False
        )
        require(result.returncode == 0, f"unreadable evidence: {revision}:{path}")
        require(digest(result.stdout) == evidence["sha256"], f"evidence hash mismatch: {path}")
    else:
        require(evidence.get("location") == "external_receipt", "evidence location")
        nonempty(evidence.get("uri"), "receipt locator")
        require(
            evidence.get("verification") == "recorded_not_reopened_by_ci",
            "external receipt is a recorded identity, not CI re-verification",
        )


def validate_observation(row: dict[str, Any], work: dict[str, dict[str, Any]]) -> None:
    item = work.get(row.get("work_item", ""))
    require(item is not None and row.get("number") == item["issue"], "issue identity mismatch")
    require(row.get("state") in {"open", "closed"}, "issue state")
    require(str(row.get("title", "")).startswith(f"[{row['work_item']}]"), "issue title identity")
    timestamp(row.get("updated_at"))
    require(bool(SHA.fullmatch(str(row.get("body_sha256", "")))), "issue body digest")
    require(
        row.get("state_reason") in {None, "completed", "not_planned", "reopened"},
        "issue state reason",
    )


def validate_ledger(catalog: dict[str, Any], ledger: dict[str, Any], root: Path) -> None:
    validate_catalog(catalog)
    require(ledger.get("schema_version") == "quanthus.blueprint-progress.v1", "ledger schema")
    records = indexed(ledger.get("records"), "checkpoints")
    work = indexed(catalog["work_items"], "work items")
    current: dict[tuple[str, str], str] = {}
    last_time: datetime | None = None
    verified: set[bytes] = set()
    for index, (record_id, record) in enumerate(records.items()):
        recorded = timestamp(record.get("recorded_at"))
        require(last_time is None or recorded >= last_time, "checkpoint time moved backwards")
        last_time = recorded
        nonempty(record.get("actor"), "checkpoint actor")
        nonempty(record.get("summary"), "checkpoint summary")
        item = work.get(record.get("work_item", ""))
        if item is None:
            raise ProgressError("checkpoint work item not mapped")
        outcome = record.get("outcome")
        require(
            outcome
            in {
                "baseline",
                "progress",
                "completed",
                "blocked",
                "rejected",
                "deferred",
                "reopened",
                "no_module_change",
            },
            "checkpoint outcome",
        )
        require(outcome != "baseline" or index == 0, "baseline only allowed once")
        evidence = indexed(record.get("evidence"), "evidence")
        for entry in evidence.values():
            fingerprint = encoded(entry)
            if fingerprint not in verified:
                verify_evidence(entry, root)
                verified.add(fingerprint)
        updates = record.get("updates")
        if not isinstance(updates, list):
            raise ProgressError("checkpoint updates required")
        if not updates:
            nonempty(record.get("no_change_reason"), "explicit no-module-change rationale")
        reviewed = record.get("reviewed_modules", [])
        require(
            isinstance(reviewed, list) and set(reviewed) <= set(item["modules"]),
            "reviewed module outside work-item scope",
        )
        if reviewed:
            nonempty(record.get("no_change_reason"), "unchanged module rationale")
        seen: set[tuple[str, str]] = set()
        for update in updates:
            module, facet = update.get("module"), update.get("facet")
            require(module in item["modules"], "module outside work-item impact scope")
            require(facet in FACETS and update.get("status") in FACETS[facet], "facet/status")
            key = (module, facet)
            require(key not in seen, "duplicate module/facet update")
            seen.add(key)
            require(update.get("supersedes") == current.get(key), "stale module checkpoint")
            refs = update.get("evidence")
            require(
                isinstance(refs, list) and bool(refs) and set(refs) <= evidence.keys(),
                "module outcome requires referenced evidence",
            )
            for field in ("scope", "remaining", "next_action"):
                nonempty(update.get(field), field)
            if facet == "admission" and update["status"] == "accepted":
                require(
                    any(evidence[ref]["kind"] == "authority" for ref in refs),
                    "accepted admission requires explicit authority evidence",
                )
            current[key] = record_id
        observations = record.get("issue_observations", [])
        require(isinstance(observations, list), "issue observations must be a list")
        observed: set[int] = set()
        for observation in observations:
            validate_observation(observation, work)
            require(observation["number"] not in observed, "duplicate issue observation")
            observed.add(observation["number"])
            require(
                outcome == "baseline" or observation["number"] == item["issue"],
                "checkpoint may acknowledge only its own issue",
            )


def current_updates(ledger: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    current: dict[tuple[str, str], dict[str, Any]] = {}
    for record in ledger["records"]:
        for update in record["updates"]:
            current[(update["module"], update["facet"])] = {
                **update,
                "checkpoint": record["id"],
                "work_item": record["work_item"],
            }
    return current


def observations(ledger: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        row["number"]: row
        for record in ledger["records"]
        for row in record.get("issue_observations", [])
    }


def reconcile(
    catalog: dict[str, Any], ledger: dict[str, Any], live: list[dict[str, Any]]
) -> list[str]:
    work = indexed(catalog["work_items"], "work items")
    expected = {item["issue"] for item in work.values()}
    actual = [row.get("number") for row in live]
    require(
        len(actual) == len(set(actual)) and set(actual) == expected,
        "live snapshot must include every mapped issue exactly once",
    )
    previous = observations(ledger)
    drift = []
    for row in live:
        validate_observation(row, work)
        old = previous.get(row["number"])
        if old != row:
            drift.append(f"#{row['number']} {row['work_item']}: 待对账 ({row['state']})")
    return drift


def check_append_only(old: dict[str, Any], new: dict[str, Any]) -> None:
    require(old.get("schema_version") == new.get("schema_version"), "ledger schema rewritten")
    prefix = old.get("records", [])
    require(new["records"][: len(prefix)] == prefix, "prior checkpoints edited or removed")


def check_change_impact(
    catalog: dict[str, Any], old: dict[str, Any], new: dict[str, Any], changed_paths: list[str]
) -> None:
    """Delivery check, using BASE catalog so deleting a watch cannot bypass it."""
    affected = {
        m["id"]
        for m in catalog["modules"]
        if any(
            fnmatch.fnmatchcase(path, pattern)
            for path in changed_paths
            for pattern in m.get("watch_paths", [])
        )
    }
    acknowledged: set[str] = set()
    for record in new["records"][len(old["records"]) :]:
        acknowledged.update(update["module"] for update in record["updates"])
        acknowledged.update(record.get("reviewed_modules", []))
    require(
        affected <= acknowledged,
        "changed modules require a checkpoint or explicit no-change review: "
        + ", ".join(sorted(affected - acknowledged)),
    )


def cell(value: str) -> str:
    return value.replace("|", "／").replace("\n", " ").replace("\r", " ")


def render(catalog: dict[str, Any], ledger: dict[str, Any]) -> str:
    current = current_updates(ledger)
    observed = observations(ledger)
    lines = [
        "# 学习系统蓝图与模块进度",
        "",
        "<!-- Generated by scripts/blueprint_progress.py; do not edit. -->",
        "",
        f"记录截至：{ledger['records'][-1]['recorded_at']}；这不是实时 GitHub 状态。",
        "",
        "任务关闭、工程完成、研究质量、用途价值和使用资格分别记账。",
        "本视图不授予训练、数据访问、P6 或生产权限；不会按 issue 关闭比例计算产品完成率。",
        "",
        "[更新机制](../design/blueprint-progress.md) · "
        "[总任务 #540](https://github.com/rv198-star/Quanthus/issues/540)",
        "",
        "```mermaid",
        "flowchart TD",
    ]
    for module in catalog["modules"]:
        if module["kind"] != "state":
            lines.append(f'    {module["id"]}["{cell(module["name"])}"]')
    drawn = {m["id"] for m in catalog["modules"] if m["kind"] != "state"}
    for module in catalog["modules"]:
        for dependency in module.get("depends_on", []):
            if module["id"] in drawn and dependency in drawn:
                lines.append(f"    {dependency} --> {module['id']}")
    lines.extend(
        [
            "```",
            "",
            "## 模块能力（范围以最新记录为准）",
            "",
            "| 模块 | 工程 | 研究质量 | 用途价值 | 使用资格 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for module in catalog["modules"]:
        labels = [
            LABELS[current.get((module["id"], facet), {}).get("status", "unassessed")]
            for facet in FACETS
        ]
        lines.append(f"| {module['id']} · {cell(module['name'])} | " + " | ".join(labels) + " |")
    lines.extend(
        [
            "",
            "## 当前缺口与下一步",
            "",
            "每个模块展示当前重点缺口；全部维度可通过 context 查看，旧记录保留在账本。",
            "",
            "| 模块 / 维度 | 已核实范围 | 剩余缺口 | 下一步 | 记录 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for module in catalog["modules"]:
        facet = module["focus_facet"]
        update = current.get((module["id"], facet))
        if update is None:
            continue
        lines.append(
            f"| {module['id']} / {FACET_LABELS[facet]} | {cell(update['scope'])} | "
            f"{cell(update['remaining'])} | {cell(update['next_action'])} | "
            f"{update['checkpoint']} |"
        )
    lines.extend(
        [
            "",
            "## 工作项到蓝图的关系",
            "",
            "GitHub 状态只表示任务生命周期；实时检查：`make blueprint-audit`。",
            "",
            "| 工作项 | 影响模块 | 对整体目标的贡献 | 最近登记的 issue 状态 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in catalog["work_items"]:
        row = observed.get(item["issue"], {})
        lines.append(
            f"| [{item['id']} / #{item['issue']}](https://github.com/{REPOSITORY}/issues/"
            f"{item['issue']}) | {', '.join(item['modules'])} | "
            f"{cell(item['contribution'])} | {row.get('state', '未对账')} |"
        )
    lines.extend(
        [
            "",
            "## 证据索引",
            "",
            "Git 文档按完整 SHA 和内容哈希核验。外部回执仅登记身份，CI 不重新打开来源。",
            "",
        ]
    )
    seen: set[bytes] = set()
    for record in ledger["records"]:
        for entry in record["evidence"]:
            identity = encoded(entry)
            if identity in seen:
                continue
            seen.add(identity)
            locator = (
                f"{entry['revision']}:{entry['path']}"
                if entry["location"] == "git"
                else entry["uri"]
            )
            lines.append(
                f"- `{entry['id']}`：`{locator}`；SHA256 `{entry['sha256']}`。"
                f"{cell(entry['claim_ceiling'])}"
            )
    return "\n".join(lines) + "\n"


def work_context(
    catalog: dict[str, Any], ledger: dict[str, Any], issue: int, module_id: str | None = None
) -> dict[str, Any]:
    item = next((item for item in catalog["work_items"] if item["issue"] == issue), None)
    if item is None:
        raise ProgressError("issue not mapped; register its module contribution first")
    modules = indexed(catalog["modules"], "modules")
    current = current_updates(ledger)
    selected = item["modules"]
    if module_id is not None:
        require(module_id in selected, "requested module is outside this work item")
        selected = [module_id]
    return {
        "work_item": item,
        "modules": [
            {
                "id": key,
                "purpose": modules[key]["purpose"],
                "parent": modules[key].get("parent"),
                "depends_on": modules[key].get("depends_on", []),
                "facets": {facet: current.get((key, facet)) for facet in FACETS},
            }
            for key in selected
        ],
        "grants_authority": False,
    }


def github_snapshot(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Read only exact registered issues. Never execute or interpolate issue text."""
    result = []
    for item in catalog["work_items"]:
        response = subprocess.run(
            ["gh", "api", f"repos/{REPOSITORY}/issues/{item['issue']}"],
            capture_output=True,
            check=False,
            timeout=45,
        )
        require(response.returncode == 0, f"cannot read issue #{item['issue']}")
        row = json.loads(response.stdout)
        result.append(
            {
                "work_item": item["id"],
                "number": row["number"],
                "title": row["title"],
                "state": row["state"],
                "state_reason": row.get("state_reason"),
                "updated_at": row["updated_at"],
                "body_sha256": digest((row.get("body") or "").encode()),
            }
        )
    return result


def event_scope(catalog: dict[str, Any], event: dict[str, Any]) -> str:
    """Old/unrelated issues stay outside; newly declared blueprint work cannot be orphaned."""
    issue = event.get("issue")
    if not issue:
        return "tracked"
    if issue.get("number") in {item["issue"] for item in catalog["work_items"]}:
        return "tracked"
    body = issue.get("body") or ""
    section = re.search(r"### Blueprint impact\s*\n(.*?)(?=\n### |\Z)", body, re.S)
    declares = "<!-- quanthus-blueprint:learning -->" in body
    if section:
        value = section[1].strip()
        declares = declares or bool(value and not value.lower().startswith("outside-scope"))
    return "unmapped" if declares else "outside"
