"""Scoped exploration memory and proposal checks; never execution authority.

TPlan owns execution/budgets and blueprint_progress owns current module maturity.
This module checks recorded claims and decisions, not their scientific truth.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from quanthus.blueprint_progress import (
    ProgressError,
    cell,
    digest,
    encoded,
    indexed,
    nonempty,
    require,
    timestamp,
    verify_evidence,
)

LEDGER = "config/planning/exploration-conclusions.v1.json"
VIEW = "docs/work-items/exploration-decisions.md"
NEXT = "config/planning/next-delivery-request.v1.json"
VERDICTS = {"supported", "not_met", "inconclusive", "not_evaluated"}
DISPOSITIONS = {"candidate_for_adoption", "reject_candidate", "park", "investigate"}


def strings(value: Any, label: str) -> list[str]:
    require(isinstance(value, list) and bool(value), f"missing {label}")
    for item in value:
        nonempty(item, label)
    require(len(set(value)) == len(value), f"duplicate {label}")
    return list(value)


def amount(value: Any, label: str, positive: bool = False) -> None:
    require(
        type(value) in {int, float}
        and (type(value) is int or math.isfinite(value))
        and (value > 0 if positive else value >= 0),
        f"invalid {label}",
    )


def validate_memory(
    memory: dict[str, Any],
    catalog: dict[str, Any],
    blueprint: dict[str, Any],
    root: Path,
) -> None:
    require(memory.get("schema_version") == "quanthus.exploration-memory.v1", "memory schema")
    require(memory.get("grants_authority") is False, "memory cannot grant authority")
    records = indexed(memory.get("records"), "conclusions")
    work = indexed(catalog.get("work_items"), "work items")
    checkpoints = indexed(blueprint.get("records"), "blueprint checkpoints")
    seen: dict[str, dict[str, Any]] = {}
    replaced: set[str] = set()
    last_time = None
    evidence_cache: set[bytes] = set()
    for key, record in records.items():
        observed = timestamp(record.get("recorded_at"))
        require(last_time is None or observed >= last_time, "conclusion time moved backwards")
        last_time = observed
        for field in (
            "actor",
            "question_id",
            "family",
            "question",
            "consumer",
            "scope",
            "claim_ceiling",
            "decision_effect",
            "cost_review",
        ):
            nonempty(record.get(field), field)
        item = work.get(record.get("work_item", ""))
        require(item is not None, "unknown conclusion work item")
        modules = strings(record.get("modules"), "conclusion modules")
        require(item is not None and set(modules) <= set(item["modules"]), "module scope")
        refs = strings(record.get("blueprint_checkpoints"), "blueprint checkpoints")
        require(set(refs) <= checkpoints.keys(), "unknown blueprint checkpoint")
        covered = {update["module"] for ref in refs for update in checkpoints[ref]["updates"]}
        require(set(modules) <= covered, "conclusion/blueprint scope mismatch")
        require(
            record.get("execution_status") in {"completed", "blocked", "invalid"},
            "execution status",
        )
        require(record.get("verdict") in VERDICTS, "conclusion verdict")
        require(record.get("disposition") in DISPOSITIONS, "decision disposition")
        if record["execution_status"] != "completed":
            require(record["verdict"] == "not_evaluated", "failed execution is not a result")
        if record["disposition"] == "reject_candidate":
            require(record["verdict"] == "not_met", "rejection needs scoped unmet gate")
        if record["disposition"] == "candidate_for_adoption":
            require(record["verdict"] == "supported", "adoption candidate needs support")
        require(
            record.get("consumer_admission") == "not_granted_by_this_record",
            "conclusion cannot grant admission",
        )
        strings(record.get("reopen_conditions"), "reopen conditions")
        strings(record.get("retained_assets"), "retained asset dispositions")
        evidence = indexed(record.get("evidence"), "conclusion evidence")
        for entry in evidence.values():
            fingerprint = encoded(entry)
            if fingerprint not in evidence_cache:
                verify_evidence(entry, root)
                evidence_cache.add(fingerprint)
        cost = record.get("effort_seconds")
        if cost is not None:
            amount(cost, "effort seconds")
            strings(record.get("attempt_refs"), "attempt receipts")
            require(record.get("cost_status") == "recorded", "recorded cost status required")
        else:
            require(record.get("cost_status") == "unknown_not_zero", "unknown cost is not zero")
        prior = record.get("supersedes")
        if prior is not None:
            require(prior in seen and prior not in replaced, "stale conclusion supersession")
            require(
                all(record[field] == seen[prior][field] for field in ("question_id", "family")),
                "supersession changes the question family",
            )
            replaced.add(prior)
        seen[key] = record


def check_history(old: dict[str, Any], new: dict[str, Any]) -> None:
    require(
        {k: v for k, v in old.items() if k != "records"}
        == {k: v for k, v in new.items() if k != "records"},
        "memory header changed without a versioned migration",
    )
    require(
        new["records"][: len(old["records"])] == old["records"],
        "conclusion history rewritten or removed",
    )


def context(memory: dict[str, Any], family: str, modules: list[str]) -> dict[str, Any]:
    # Broader module matches are review candidates, not automatic scientific vetoes.
    require(bool(family or modules), "family or module filter required")
    replaced = {row.get("supersedes") for row in memory["records"]}
    related = [
        {**row, "current": row["id"] not in replaced}
        for row in memory["records"]
        if row["family"] == family or set(modules).intersection(row["modules"])
    ]
    return {
        "memory_sha256": digest(encoded(memory)),
        "related": related,
        "unknown_cost_records": [r["id"] for r in related if r.get("effort_seconds") is None],
        "grants_authority": False,
        "claim_ceiling": "Related evidence requires an applicability judgment; not a global ban.",
    }


def validate_request(
    request: dict[str, Any], memory: dict[str, Any], catalog: dict[str, Any], root: Path
) -> dict[str, Any]:
    require(request.get("schema_version") == "quanthus.exploration-request.v1", "request schema")
    require(request.get("grants_authority") is False, "proposal cannot grant authority")
    require(request.get("status") == "proposed", "proposal is not an activation")
    require(request.get("kind") in {"delivery", "repair", "exploration"}, "proposal kind")
    for field in (
        "id",
        "family",
        "question",
        "consumer",
        "scope",
        "deliverable",
        "decision_if_pass",
        "decision_if_fail",
        "cumulative_cost_review",
        "runtime_authority_boundary",
    ):
        nonempty(request.get(field), field)
    strings(request.get("acceptance"), "acceptance")
    strings(request.get("stop_conditions"), "stop conditions")
    modules = strings(request.get("modules"), "request modules")
    work = indexed(catalog["work_items"], "work items")
    item = work.get(request.get("work_item", ""))
    require(item is not None and set(modules) <= set(item["modules"]), "request module scope")
    prior = context(memory, request["family"], modules)
    require(request.get("memory_sha256") == prior["memory_sha256"], "stale memory binding")
    required = {row["id"]: row for row in prior["related"] if row["current"]}
    reviews = request.get("prior_reviews")
    if not isinstance(reviews, list):
        raise ProgressError("prior reviews required")
    reviewed: set[str] = set()
    for review in reviews:
        require(isinstance(review, dict), "invalid prior review")
        key = review.get("conclusion_id")
        require(
            isinstance(key, str) and key in required and key not in reviewed,
            "unknown or duplicate prior review",
        )
        nonempty(review.get("rationale"), "prior applicability rationale")
        relation = review.get("relation")
        require(relation in {"respect", "different_scope", "reopen"}, "prior relation")
        if relation == "different_scope":
            nonempty(review.get("scope_difference"), "specific scope difference")
        elif relation == "reopen":
            nonempty(review.get("material_change"), "material reopening change")
            for entry in indexed(review.get("evidence"), "reopening evidence").values():
                verify_evidence(entry, root)
        elif request["kind"] == "exploration" and required[key]["family"] == request["family"]:
            require(
                required[key]["disposition"] not in {"park", "reject_candidate"},
                "same stopped exploration needs a justified reopening",
            )
        reviewed.add(key)
    require(reviewed == required.keys(), "unreviewed prior conclusions")
    budget = request.get("budget")
    if not isinstance(budget, dict):
        raise ProgressError("finite budget required")
    for name in ("wall_seconds", "max_new_bytes"):
        amount(budget.get(name), name, positive=True)
    require(
        type(budget.get("network_requests")) is int and budget["network_requests"] >= 0,
        "invalid network budget",
    )
    nonempty(budget.get("accounting_owner"), "budget accounting owner")
    return {
        "status": "PROPOSAL_CHECKED_NOT_AUTHORIZED",
        "request_id": request["id"],
        "memory_sha256": prior["memory_sha256"],
        "reviewed_conclusions": sorted(reviewed),
        "grants_authority": False,
        "limitations": [
            "Mechanical completeness is not evidence of ROI or scientific truth.",
            "Existing domain authority, data admission and TPlan cumulative budgets still apply.",
            "No experiments, network access, production changes or timers are launched.",
        ],
    }


def render(memory: dict[str, Any]) -> str:
    replaced = {row.get("supersedes") for row in memory["records"]}
    lines = [
        "# 探索结论与后续选择",
        "",
        "<!-- Generated by scripts/exploration_governance.py render; do not edit. -->",
        "",
        "历史结论保留适用范围；本表不是模块成熟度或执行授权。",
        "[当前蓝图](learning-blueprint-progress.md) · "
        "[工程要求](../design/exploration-governance.md) · "
        "[下一阶段计划](exploration-next-20261029.md)",
        "",
        "| 结论 | 范围内判断 | 后续选择 | 重开条件 |",
        "| --- | --- | --- | --- |",
    ]
    for row in memory["records"]:
        if row["id"] in replaced:
            continue
        values = [
            row["id"],
            f"{row['verdict']}：{row['scope']}",
            row["decision_effect"],
            "；".join(row["reopen_conditions"]),
        ]
        lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    lines.extend(["", "未知历史投入不记作零；新方向预算不抹掉旧尝试。", ""])
    return "\n".join(lines)
