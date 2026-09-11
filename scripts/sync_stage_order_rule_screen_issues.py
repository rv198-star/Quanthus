#!/usr/bin/env python3
"""Validate and synchronize only the owner-approved P3-STAGE-007 rule-screen plan."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

from quanthus.stage_learning.order_rule_prepare import load_prepare_config
from quanthus.stage_learning.order_rule_resolve_score import (
    CONFIG_DIGEST as RESOLVE_SCORE_DIGEST,
)
from quanthus.stage_learning.order_rule_resolve_score import (
    load_config as load_resolve_score_config,
)
from quanthus.stage_learning.order_rule_screen import RULE_DIGEST, load_rule
from quanthus.stage_learning.order_rule_validation import VALIDATION_DIGEST
from quanthus.structure_v2_inputs import digest

ROOT = Path(__file__).resolve().parents[1]
PLAN = "config/stage-order-rule-screen-delivery.v1.json"
PARENT = "P3-STAGE-007"
IDS = (PARENT, *(f"{PARENT}-{suffix}" for suffix in "ABCD"))
REPO = "rv198-star/Quanthus"


def validate_plan(raw: dict) -> None:
    """Validate the frozen screen and its evidence-backed terminal disposition."""
    if (raw["schema_version"] != "quanthus.stage-order-rule-screen-delivery.v1"
            or raw["parent_id"] != PARENT
            or tuple(item["id"] for item in raw["items"]) != IDS):
        raise ValueError("screen_plan_identity")
    if raw["milestone_completed"] is not True:
        raise ValueError("screen_terminal_closeout_missing")
    receipts = raw.get("runtime_receipts")
    if not isinstance(receipts, dict) or set(receipts) != {
        "failed_prepare", "successful_prepare", "resolve_score",
    }:
        raise ValueError("screen_runtime_receipts_missing")
    failed_receipt = receipts["failed_prepare"]
    success_receipt = receipts["successful_prepare"]
    if failed_receipt != {
        "job_id": "stage_7ef751fa63664175b49eaa3459c83ca3",
        "code_revision": "b7042cf0683b817a2ed2b3a59a00d5d6159cf5b6",
        "input_digest": "sha256:d46f8d3f990890abdf49d0ae0fba12c0b9a5a2629e70faba56a15d15acf5dedb",
        "state": "failed",
        "error": "stage_order_rule_prepare_process_failed",
        "exit_code": 1,
        "completed_at": "2026-09-11T05:28:35.396338+00:00",
    }:
        raise ValueError("screen_failed_prepare_receipt_drift")
    if success_receipt != {
        "job_id": "stage_95f9b48bd96049b184e0cbf856b9a5e8",
        "technical_retry_of": "stage_7ef751fa63664175b49eaa3459c83ca3",
        "code_revision": "ae99d4973f605c3a6e7044cb6c5f88fd91f98874",
        "input_digest": "sha256:4dbe116bc5221eb178414f66ada5f589e8f08849a9d440569cd468a3ae20e4cd",
        "state": "succeeded",
        "exit_code": 0,
        "started_at": "2026-09-11T07:59:45.393801+00:00",
        "completed_at": "2026-09-11T08:05:35.136275+00:00",
        "run_id": "stage-order-rule-prepare-20260911T080532Z",
        "manifest_sha256": (
            "sha256:0331ba6a82db7b4fb60cfbc03f328d899b7939c43936b414d9a4ffbce30b4db7"
        ),
        "selection_digest": (
            "sha256:75c5d7c1a0711482abf503d9c17b71f0470ba256d6b66fd15ce9ff4f645d7373"
        ),
        "evidence_digest": (
            "sha256:6a9df1869ff4f532715b212e41b9b6ada34d72835152d7318f4ac662acbf8dcf"
        ),
    }:
        raise ValueError("screen_successful_prepare_receipt_drift")
    resolve_receipt = receipts["resolve_score"]
    if resolve_receipt != {
        "job_id": "stage_e8d174dd13db412e94c8f4bdbf70c7f5",
        "code_revision": "38b965aea678d180a12299b53db3f4db4a1ce256",
        "input_digest": "sha256:d169d5673430e3ca4fd5962d8f2e834e4ccb5676c0db51f54ce0cfd0491a7cc3",
        "state": "succeeded",
        "exit_code": 0,
        "started_at": "2026-09-11T13:45:17.270315+00:00",
        "completed_at": "2026-09-11T13:45:36.734284+00:00",
        "run_id": "stage-order-rule-resolve-score-20260911T134519Z",
        "manifest_sha256": (
            "sha256:a7dbf622fce034b7b5d88d1bf3cee46b5e6e52786ea786a56e97bd0069e1a931"
        ),
        "reference_manifest_sha256": (
            "sha256:d87572762af7c6397290cf7ca731f213f9ce5674e4034aa2cb17427c07fbce40"
        ),
        "case_count": 160,
        "opinion_count": 320,
        "primary_reference_counts": {
            "STAGE_0": 30, "STAGE_2": 46, "NEITHER": 36, "AMBIGUOUS": 48,
        },
        "gate_disposition": "REVISE_TARGET_OR_PARK",
        "gate_reasons": [
            "STAGE_0:precision_fails",
            "STAGE_0:definite_negative_false_positive_rate_fails",
            "ambiguous_assertion_rate_fails",
        ],
    }:
        raise ValueError("screen_resolve_score_receipt_drift")
    prepare_runtime = raw.get("completed_prepare_contract")
    if not isinstance(prepare_runtime, dict) or prepare_runtime != {
        "path": "config/stage-learning/order-rule-prepare.v1.json",
        "digest": "sha256:b27bbc1d3d54f9e410a8ca7b931af63f62690be9b911d538aeb0cda5666e9bd9",
        "kind": "quanthus.stage-order-rule-prepare.v1",
        "phase": "prepare_only",
        "maximum_jobs": 1,
        "maximum_technical_retries": 1,
        "opinion_generation": False,
        "candidate_scoring": False,
    }:
        raise ValueError("screen_completed_prepare_runtime_contract")
    prepare_runtime_path = (ROOT / prepare_runtime["path"]).resolve()
    if not prepare_runtime_path.is_relative_to(ROOT) or not prepare_runtime_path.is_file():
        raise ValueError("screen_completed_prepare_contract_missing")
    if digest(load_prepare_config(prepare_runtime_path)) != prepare_runtime["digest"]:
        raise ValueError("screen_completed_prepare_contract_digest")
    runtime = raw.get("completed_resolve_score_contract")
    if not isinstance(runtime, dict) or runtime != {
        "path": "config/stage-learning/order-rule-resolve-score.v1.json",
        "digest": RESOLVE_SCORE_DIGEST,
        "kind": "quanthus.stage-order-rule-resolve-score.v1",
        "phase": "seal_then_score",
        "maximum_jobs": 1,
        "maximum_technical_retries": 0,
        "case_count": 160,
        "opinion_count": 320,
        "new_market_data_read": False,
        "parameter_search": False,
    }:
        raise ValueError("screen_resolve_score_runtime_contract")
    runtime_path = (ROOT / runtime["path"]).resolve()
    if not runtime_path.is_relative_to(ROOT) or not runtime_path.is_file():
        raise ValueError("screen_resolve_score_runtime_contract_missing")
    if digest(load_resolve_score_config(runtime_path)) != runtime["digest"]:
        raise ValueError("screen_resolve_score_runtime_contract_digest")
    if raw.get("runtime_contract") is not None:
        raise ValueError("screen_terminal_runtime_contract_must_be_closed")
    if raw["rule_digest"] != RULE_DIGEST:
        raise ValueError("screen_rule_binding")
    load_rule(ROOT)
    expected_auth = {key: False for key in (
        "runtime_job_submission", "new_market_data_read", "parameter_search", "arbitration_access",
        "permanent_blind_access", "official_labels", "daily_or_strategy", "trading",
    )}
    expected_auth["implementation_and_synthetic_tests"] = True
    if raw["authorization"] != expected_auth:
        raise ValueError("screen_unauthorized_activation")
    states = ("closed", "closed", "closed", "closed", "closed")
    checkpoint = raw.get("preparation_checkpoint")
    if not isinstance(checkpoint, dict) or checkpoint != {
        "state": "PREPARE_ACCEPTED",
        "reason_code": "SOURCE_ISOLATION_AND_FIXED_SELECTION_VERIFIED",
        "read_attempt_exit_code": 0,
        "source_isolation_verified": True,
        "source_membership_verified": True,
        "old_cohort_size": 512,
        "excluded_economic_identity_count": 515,
        "candidate_population_count": 3917,
        "pool_size": 512,
        "selected_case_count": 160,
        "selection_shortfalls": {
            "coverage": 0, "order_like": 0, "instability_like": 0, "boundary": 0,
        },
        "evidence_source_row_count": 9760,
        "prepare_attempts_with_development_source_read": 2,
        "result_bearing_prepare_successes": 1,
        "component": "src/quanthus/stage_learning/order_rule_prepare.py",
        "tests": "tests/unit/test_stage_order_rule_prepare.py",
        "evidence": "docs/acceptance/stage-order-rule-screen-b-prepare-success-20260911.md",
    }:
        raise ValueError("screen_preparation_checkpoint_claim")
    for key in ("component", "tests", "evidence"):
        path = (ROOT / checkpoint[key]).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError("screen_preparation_evidence_missing")
    retry = raw.get("technical_retry")
    if retry != {
        "authorization": "config/stage-learning/order-rule-prepare-retry.v1.json",
        "authorized": False,
        "consumed": True,
        "maximum_retries": 1,
        "actual_retries": 1,
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
        or failure.get("job_id") != failed_receipt["job_id"]
        or failure.get("code_revision") != failed_receipt["code_revision"]
        or failure.get("input_digest") != failed_receipt["input_digest"]
        or failure.get("worker_error") != failed_receipt["error"]
        or failure.get("root_cause") != "post_selection_sampling_coordinate_canonicalization"
        or failure.get("failed_check") != "selected_observation_differs_from_retained_evidence"
        or policy.get("maximum_technical_retries") != 1
        or policy.get("counts_against_result_bearing_job_budget") is not False
        or bindings.get("prepare_config_digest") != prepare_runtime["digest"]
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
    reference = raw.get("reference_checkpoint")
    if reference != {
        "state": "REVIEW_LEDGER_FROZEN",
        "review_decisions": "config/stage-learning/order-rule-review-decisions.v1.tsv",
        "review_decisions_sha256": (
            "sha256:493933e614fccd8c84e9066182f29b11ca80570396ea697d014337fda9504fd7"
        ),
        "case_count": 160,
        "opinion_count": 320,
        "provenance_kind": "self_simulated",
        "origin_id": "chatgpt-owner-session-stage007",
        "session_id": "stage007-reference-review-20260911",
        "candidate_output_seen_during_case_review": False,
        "cognitive_blinding_claimed": False,
        "resolved_preview": {
            "STAGE_0": 30, "STAGE_2": 46, "NEITHER": 36, "AMBIGUOUS": 48,
        },
    }:
        raise ValueError("screen_reference_checkpoint")
    review_path = (ROOT / reference["review_decisions"]).resolve()
    if not review_path.is_relative_to(ROOT) or not review_path.is_file():
        raise ValueError("screen_review_ledger_missing")
    observed_review = "sha256:" + hashlib.sha256(review_path.read_bytes()).hexdigest()
    if observed_review != reference["review_decisions_sha256"]:
        raise ValueError("screen_review_ledger_drift")
    final = raw.get("final_disposition")
    if not isinstance(final, dict) or (
        final.get("state") != "CLOSED"
        or final.get("decision") != "REVISE_TARGET_OR_PARK"
        or final.get("combined_candidate_broader_validation_authorized") is not False
        or final.get("stage_0", {}).get("decision") != "REVISE_TARGET"
        or final.get("stage_0", {}).get("precision") != "0.70"
        or final.get("stage_0", {}).get("definite_negative_false_positive_rate")
        != "0.1463414634146341463414634146"
        or final.get("stage_0", {}).get("false_positive") != 12
        or final.get("stage_2", {}).get("decision")
        != "PROMISING_EXPLORATORY_SUBRESULT_ONLY"
        or final.get("stage_2", {}).get("precision") != "1"
        or final.get("stage_2", {}).get("false_positive") != 0
        or final.get("stage_2", {}).get("broader_validation_authorized") is not False
        or final.get("reference_quality", {}).get("clear_recent_window_primary_veto_deviations")
        != 21
        or final.get("reference_quality", {}).get("ambiguity_metric_clean_rule_quality_evidence")
        is not False
        or final.get("reference_quality", {}).get(
            "definite_stage_0_failure_independent_of_ambiguity_defect"
        ) is not True
        or final.get("successor_direction", {}).get("rerun_same_batch") is not False
        or final.get("successor_direction", {}).get("threshold_tuning_on_same_batch") is not False
        or final.get("successor_direction", {}).get("consume_2025_now") is not False
    ):
        raise ValueError("screen_final_disposition_drift")
    final_evidence = (ROOT / str(final.get("evidence") or "")).resolve()
    if not final_evidence.is_relative_to(ROOT) or not final_evidence.is_file():
        raise ValueError("screen_final_disposition_evidence_missing")
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
            "## Boundary\nOne candidate; no 004/005/006 rerun. The bounded runtime contracts are "
            "consumed and no further Stage-007 Job is authorized. Same-origin simulation remains "
            "exploratory only. 2022-2024 and 2025 stay closed; no official label, daily, strategy "
            "or trading.",
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
        if raw.get("final_disposition") is not None:
            sections.append("## Final disposition\n" + json.dumps(
                raw["final_disposition"], sort_keys=True,
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
