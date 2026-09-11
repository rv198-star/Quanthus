#!/usr/bin/env python3
"""Validate and synchronize only the owner-approved P3-STAGE-007 rule-screen plan."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

from quanthus.stage_learning.order_rule_prepare import load_prepare_config
from quanthus.stage_learning.order_rule_screen import RULE_DIGEST, load_rule
from quanthus.stage_learning.order_rule_validation import VALIDATION_DIGEST
from quanthus.structure_v2_inputs import digest

ROOT = Path(__file__).resolve().parents[1]
PLAN = "config/stage-order-rule-screen-delivery.v1.json"
PARENT = "P3-STAGE-007"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in "ABCD"))
REPO = "rv198-star/Quanthus"


def validate_plan(raw: dict) -> None:
    """Validate the frozen screen and one authorized prepare run, never invent a runtime PASS."""
    if (raw["schema_version"] != "quanthus.stage-order-rule-screen-delivery.v1"
            or raw["parent_id"] != PARENT
            or tuple(item["id"] for item in raw["items"]) != IDS):
        raise ValueError("screen_plan_identity")
    if raw["milestone_completed"] is not False or raw["runtime_receipts"] is not None:
        raise ValueError("screen_unexecuted_runtime_claim")
    runtime = raw.get("runtime_contract")
    if not isinstance(runtime, dict) or runtime != {
        "path": "config/stage-learning/order-rule-prepare.v1.json",
        "digest": "sha256:b27bbc1d3d54f9e410a8ca7b931af63f62690be9b911d538aeb0cda5666e9bd9",
        "kind": "quanthus.stage-order-rule-prepare.v1",
        "phase": "prepare_only",
        "maximum_jobs": 1,
        "maximum_technical_retries": 1,
        "opinion_generation": False,
        "candidate_scoring": False,
    }:
        raise ValueError("screen_prepare_runtime_contract")
    runtime_path = (ROOT / runtime["path"]).resolve()
    if not runtime_path.is_relative_to(ROOT) or not runtime_path.is_file():
        raise ValueError("screen_prepare_runtime_contract_missing")
    if digest(load_prepare_config(runtime_path)) != runtime["digest"]:
        raise ValueError("screen_prepare_runtime_contract_digest")
    if raw["rule_digest"] != RULE_DIGEST:
        raise ValueError("screen_rule_binding")
    load_rule(ROOT)
    expected_auth = {key: False for key in (
        "parameter_search", "arbitration_access", "permanent_blind_access", "official_labels",
        "daily_or_strategy", "trading",
    )}
    expected_auth.update({
        "implementation_and_synthetic_tests": True,
        "runtime_job_submission": True,
        "new_market_data_read": True,
    })
    if raw["authorization"] != expected_auth:
        raise ValueError("screen_unauthorized_activation")
    states = ("ready", "closed", "ready", "blocked", "blocked")
    checkpoint = raw.get("preparation_checkpoint")
    if not isinstance(checkpoint, dict) or (
        checkpoint.get("state") != "TECHNICAL_RETRY_READY"
        or checkpoint.get("reason_code")
        != "POST_SELECTION_SAMPLING_COORDINATE_CANONICALIZATION"
        or checkpoint.get("read_attempt_exit_code") != 0
        or checkpoint.get("source_isolation_verified") is not False
        or checkpoint.get("source_membership_verified") is not False
        or checkpoint.get("old_cohort_digest") is not None
        or checkpoint.get("new_market_data_reads") != 1
        or checkpoint.get("new_jobs_submitted") != 1
        or checkpoint.get("failed_job_id") != "stage_7ef751fa63664175b49eaa3459c83ca3"
        or checkpoint.get("failed_job_revision")
        != "b7042cf0683b817a2ed2b3a59a00d5d6159cf5b6"
        or checkpoint.get("failed_input_digest")
        != "sha256:d46f8d3f990890abdf49d0ae0fba12c0b9a5a2629e70faba56a15d15acf5dedb"
        or checkpoint.get("failed_worker_error") != "stage_order_rule_prepare_process_failed"
    ):
        raise ValueError("screen_preparation_checkpoint_claim")
    for key in ("component", "tests", "evidence"):
        path = (ROOT / checkpoint[key]).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError("screen_preparation_evidence_missing")
    retry = raw.get("technical_retry")
    if retry != {
        "authorization": "config/stage-learning/order-rule-prepare-retry.v1.json",
        "authorized": True,
        "maximum_retries": 1,
        "counts_against_result_bearing_job_budget": False,
        "semantic_result_exposed_before_failure": False,
        "opinion_count_before_failure": 0,
        "candidate_scored_before_failure": False,
        "parameter_search_before_failure": False,
        "selection_case_count": 160,
        "selection_shortfalls": {
            "coverage": 0, "order_like": 0, "instability_like": 0, "boundary": 0,
        },
    }:
        raise ValueError("screen_technical_retry_claim")
    retry_path = (ROOT / retry["authorization"]).resolve()
    if not retry_path.is_relative_to(ROOT) or not retry_path.is_file():
        raise ValueError("screen_technical_retry_authorization_missing")
    retry_auth = json.loads(retry_path.read_text(encoding="utf-8"))
    failure = retry_auth.get("failure")
    policy = retry_auth.get("authorization")
    bindings = retry_auth.get("bindings")
    if (
        retry_auth.get("schema_version") != "quanthus.stage-order-rule-prepare-retry.v1"
        or retry_auth.get("work_item") != "P3-STAGE-007-B"
        or not all(isinstance(value, dict) for value in (failure, policy, bindings))
        or failure.get("job_id") != checkpoint["failed_job_id"]
        or failure.get("code_revision") != checkpoint["failed_job_revision"]
        or failure.get("input_digest") != checkpoint["failed_input_digest"]
        or failure.get("worker_error") != checkpoint["failed_worker_error"]
        or failure.get("root_cause") != "post_selection_sampling_coordinate_canonicalization"
        or failure.get("failed_check") != "selected_observation_differs_from_retained_evidence"
        or policy.get("maximum_technical_retries") != 1
        or policy.get("counts_against_result_bearing_job_budget") is not False
        or bindings.get("prepare_config_digest") != runtime["digest"]
        or bindings.get("rule_digest") != RULE_DIGEST
        or bindings.get("validation_digest") != VALIDATION_DIGEST
    ):
        raise ValueError("screen_technical_retry_binding")
    fix_path = (ROOT / str(bindings.get("fix_path") or "")).resolve()
    if not fix_path.is_relative_to(ROOT) or not fix_path.is_file():
        raise ValueError("screen_technical_retry_fix_missing")
    observed_fix = "sha256:" + hashlib.sha256(fix_path.read_bytes()).hexdigest()
    if observed_fix != bindings.get("fix_file_sha256"):
        raise ValueError("screen_technical_retry_fix_drift")
    expected_states = dict(zip(IDS, states, strict=True))
    if {item["id"]: item["status"] for item in raw["items"]} != expected_states:
        raise ValueError("screen_status_requires_real_evidence")
    for key in ("design", "rule_contract", "implementation"):
        path = (ROOT / raw[key]).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError("screen_missing_governing_file")
    requirements = raw["runtime_activation_requirements"]
    if len(requirements) != 8 or len(set(requirements)) != 8:
        raise ValueError("screen_activation_requirements")
    validation = raw["validation"]
    if digest(validation) != VALIDATION_DIGEST:
        raise ValueError("screen_frozen_validation_drift")
    if (validation["candidate_count"] != 1 or validation["data_years_allowed"] != [2019, 2020, 2021]
            or validation["report_year"] != 2021 or validation["excluded_old_cohort_size"] != 512
            or validation["maximum_new_pool_securities"] != 512
            or validation["maximum_cases"] != 160
            or validation["maximum_cases_per_economic_identity"] != 1):
        raise ValueError("screen_population_or_candidate_scope")
    for key in ("cross_bucket_backfill", "result_dependent_replacement",
                "representativeness_claimed", "formal_population_confidence_claimed"):
        if validation[key] is not False:
            raise ValueError("screen_false_scope_claim")
    panels = validation["panels"]
    if (panels["coverage"]["target"] != 64 or panels["coverage"]["per_quarter"] != 16
            or panels["challenge"]["target"] != 96
            or panels["challenge"]["bucket_targets"] != [48, 32, 16]
            or validation["report_panels_and_quarters_separately"] is not True):
        raise ValueError("screen_panel_denominators")
    gates = {
        "minimum_complete_cases": 120, "minimum_definite_positives_per_class": 20,
        "minimum_definite_negatives_per_class": 20, "minimum_definite_predictions_per_class": 20,
        "minimum_ambiguous_references": 10, "minimum_benchmark_precision": "0.80",
        "minimum_positive_reference_coverage": "0.55",
        "maximum_definite_negative_false_positive_rate": "0.10",
        "maximum_ambiguous_assertion_rate": "0.20", "maximum_same_scale_conflicts": 0,
        "coverage_panel_veto_min_predictions": 10, "coverage_panel_veto_precision_below": "0.65",
    }
    if any(validation.get(key) != value for key, value in gates.items()):
        raise ValueError("screen_predeclared_gate_drift")
    if (validation["sample_seed"] != "stage007-frozen-screen-20260911-v1"
            or validation["excluded_stage006_codes_to_resolve_to_economic_identities"]
            != ["000001", "300750", "600519"]):
        raise ValueError("screen_selection_or_exclusion_drift")
    budgets = raw["budgets"]
    if budgets != {
        "maximum_new_jobs": 2, "maximum_worker_wall_seconds_per_job": 2700,
        "maximum_raw_opinions": 320, "maximum_successful_external_review_responses": 320,
        "maximum_failed_transport_retries": 8, "semantic_answer_retries": 0,
        "automatic_supplement_rounds": 0, "parameter_trials": 1,
    }:
        raise ValueError("screen_budget_expansion")
    seen = {"P3-STAGE-006"}
    for item in raw["items"]:
        if not set(item["depends_on"]) <= seen or not item["scope"] or not item["acceptance"]:
            raise ValueError("screen_dependency_or_task_incomplete")
        if item["status"] == "closed" and not item["evidence"]:
            raise ValueError("screen_closed_without_evidence")
        for relative in item["evidence"]:
            path = (ROOT / relative).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():
                raise ValueError("screen_evidence_missing")
        seen.add(item["id"])


def load_plan() -> dict:
    raw = json.loads((ROOT / PLAN).read_text(encoding="utf-8"))
    validate_plan(raw)
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raw = load_plan()
    if not args.apply:
        print(json.dumps({"valid": True, "items": raw["items"]}, ensure_ascii=False))
        return 0
    gh = GitHub(REPO)
    mapping = issue_by_stable_id(gh.issues(), {*IDS, "P3-STAGE-006"})

    def body(item: dict) -> str:
        def link(key: str) -> str:
            return f"#{mapping[key]['number']} (`{key}`)" if key in mapping else f"`{key}`"
        sections = [
            f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
            "## Scope\n" + item["scope"], "## Status\n" + item["status"],
            "## Dependencies\n" + ", ".join(map(link, item["depends_on"])),
            f"## Governing design\n`{raw['design']}`\n`{raw['rule_contract']}`\n`{PLAN}`",
            "## Acceptance\n" + "\n".join("- " + text for text in item["acceptance"]),
            "## Evidence\n" + ("\n".join("- `" + p + "`" for p in item["evidence"])
                               or "Pending actual delivery evidence; no runtime PASS."),
            "## Boundary\nOne candidate; no 004/005/006 rerun. New source isolation and a frozen "
            "runtime contract must precede any Job. Same-origin simulation is exploratory only. "
            "2022-2024 and 2025 stay closed; no official label, daily, strategy or trading.",
            "## Budgets\n" + json.dumps(raw["budgets"], sort_keys=True),
            "## Runtime activation prerequisites\n" + "\n".join(
                "- " + text for text in raw["runtime_activation_requirements"]
            ),
            ("## Children\n" + ", ".join(map(link, IDS[1:])) if item["id"] == PARENT
             else "## Parent\n" + link(PARENT)),
        ]
        if raw.get("preparation_checkpoint") is not None:
            sections.append("## Preparation checkpoint\n" + json.dumps(
                raw["preparation_checkpoint"], sort_keys=True,
            ))
        return "\n\n".join(sections) + "\n"

    ordered = [*raw["items"][1:], raw["items"][0]]
    for item in ordered:
        mapping[item["id"]], _ = gh.upsert_issue(
            mapping.get(item["id"]), f"[{item['id']}] {item['title']}", body(item),
            [f"type:{item['kind']}", f"status:{item['status']}", "phase:P3", "area:labels"], None,
        )
    receipts = []
    for item in ordered:
        path = f"/repos/{REPO}/issues/{mapping[item['id']]['number']}"
        state = "closed" if item["status"] == "closed" else "open"
        gh.api("PATCH", path, {"body": body(item), "state": state})
        checked = gh.api("GET", path)
        if checked["body"] != body(item) or checked["state"] != state:
            raise RuntimeError("screen_issue_readback_differs")
        receipts.append({"id": item["id"], "number": checked["number"], "state": state})
    print(json.dumps({"issues": receipts, "unrelated_issues_changed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
