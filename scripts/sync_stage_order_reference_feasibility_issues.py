#!/usr/bin/env python3
"""Synchronize only the bounded P3-STAGE-006 reference-feasibility milestone."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from sync_github_planning import GitHub, issue_by_stable_id

ROOT = Path(__file__).resolve().parents[1]
PLAN = "config/stage-order-reference-feasibility-delivery.v1.json"
PARENT = "P3-STAGE-006"
IDS = (PARENT, *(f"{PARENT}-{c}" for c in "ABCD"))
REPO = "rv198-star/Quanthus"


def validate_completed(raw):
    """Freeze receipt consistency and claim ceiling, not independent market validity."""
    receipts = raw["runtime_receipts"]
    if not isinstance(receipts, dict) or set(receipts) != {
        "code_revision", "config_digest", "prepare", "resolve", "summary",
    }:
        raise ValueError("reference_feasibility_receipt_fields")
    config = json.loads((ROOT / raw["runtime_config"]).read_text())
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode()
    if (receipts["config_digest"] != "sha256:" + hashlib.sha256(encoded).hexdigest()
            or receipts["code_revision"] != "bf5d8b11c3869bdb7c1205943779ce3f5619cbe4"):
        raise ValueError("reference_feasibility_frozen_execution_binding")
    prepare, resolved = receipts["prepare"], receipts["resolve"]
    if (prepare["job_id"] == resolved["job_id"]
            or prepare["manifest_sha256"] != resolved["parent_manifest_sha256"]
            or prepare["evidence_digest"] != resolved["evidence_digest"]
            or prepare["source_inputs_digest"] != config["source_inputs_digest"]
            or (prepare["source_row_count"], prepare["case_count"]) != (357, 12)):
        raise ValueError("reference_feasibility_source_parent_binding")
    for phase in ("prepare", "resolve"):
        receipt = receipts[phase]
        if (receipt["phase"] != phase or receipt["state"] != "succeeded"
                or receipt["attempt"] != 1 or receipt["error_reason"] is not None
                or receipt["run_id"] != "run_" + receipt["job_id"].removeprefix("job_")):
            raise ValueError("reference_feasibility_terminal_receipt")
        start, end = (datetime.fromisoformat(receipt[key])
                      for key in ("started_at", "completed_at"))
        if start.tzinfo is None or end.tzinfo is None or end <= start or abs(
            (end - start).total_seconds() - receipt["worker_elapsed_seconds"]
        ) > 0.000001:
            raise ValueError("reference_feasibility_elapsed_binding")
    result = receipts["summary"]
    if (result["collection_disposition"] != "PASS_REAL_INPUT_REFERENCE_COLLECTION"
            or result["coverage_disposition"] != "DESCRIPTIVE_REFERENCE_COVERAGE_OBSERVED"
            or result["primary_reference_counts"] != {
                "AMBIGUOUS": 4, "NEITHER": 3, "STAGE_0": 4, "STAGE_2": 1,
            }
            or (result["case_count"], result["opinion_count"], result["security_count"],
                result["date_count"], result["recent_disagreement_count"]) != (12, 24, 3, 4, 1)
            or result["distinct_origin_count"] != 1
            or result["review_source_kinds"] != ["self_simulated"]
            or result["source_kind"] != "retrospective_development"
            or result["dwd_provider_opens"] != 0 or result["primary_stage"] is not None):
        raise ValueError("reference_feasibility_result_or_provenance")
    if any(result[key] is not False for key in (
        "human_expert_validated", "independent_validation", "full_context_blinding_claimed",
        "market_reference_validated", "market_representativeness",
        "new_parameter_candidate_accepted",
        "parameter_search_authorized", "official_label_authorized",
    )):
        raise ValueError("reference_feasibility_claim_ceiling")
    if raw["authorization"]["scope"] != (
        "reference_feasibility_completed_no_additional_jobs_or_search"
    ):
        raise ValueError("reference_feasibility_closeout_scope")


def load_plan():
    raw = json.loads((ROOT / PLAN).read_text())
    if raw["parent_id"] != PARENT or tuple(item["id"] for item in raw["items"]) != IDS:
        raise ValueError("reference_feasibility_issue_identity")
    for key in ("design", "runtime_config", "implementation", "evidence"):
        path = (ROOT / raw[key]).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError("reference_feasibility_issue_path")
    auth = raw["authorization"]
    if any(auth[k] is not False for k in (
        "new_dwd_read", "parameter_search", "arbitration", "permanent_blind",
        "official_labels", "daily_or_strategy", "trading",
    )) or (auth["maximum_jobs"], auth["maximum_cases"], auth["maximum_opinions"]) != (2, 12, 24):
        raise ValueError("reference_feasibility_issue_scope")
    if raw["runtime_completed"]:
        if (not raw["runtime_receipts"] or auth["prepare_job"] or auth["resolve_job"]
                or any(item["status"] != "closed" for item in raw["items"])):
            raise ValueError("reference_feasibility_closeout_unbound")
        validate_completed(raw)
    elif raw["runtime_receipts"] is not None:
        raise ValueError("reference_feasibility_pending_result_claim")
    for item in raw["items"]:
        if item["status"] == "closed" and not item["evidence"]:
            raise ValueError("reference_feasibility_closed_without_evidence")
        if any(not (ROOT / p).is_file() for p in item["evidence"]):
            raise ValueError("reference_feasibility_missing_evidence")
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raw = load_plan()
    if not args.apply:
        print(json.dumps({"valid": True, "items": raw["items"]}))
        return 0
    gh = GitHub(REPO)
    mapping = issue_by_stable_id(gh.issues(), {*IDS, "P3-STAGE-005"})

    def body(item):
        def link(key):
            return f"#{mapping[key]['number']} (`{key}`)" if key in mapping else f"`{key}`"
        return "\n\n".join([
            f"<!-- quanthus-planning:v2 id={item['id']} kind={item['kind']} -->",
            "## Scope\n" + item["scope"], "## Status\n" + item["status"],
            "## Dependencies\n" + ", ".join(map(link, item["depends_on"])),
            "## Design\n`" + raw["design"] + "`\n`" + raw["runtime_config"] + "`",
            "## Acceptance\n" + "\n".join("- " + s for s in item["acceptance"]),
            "## Evidence\n" + "\n".join("- `" + p + "`" for p in item["evidence"]),
            "## Authority\n" + json.dumps(raw["authorization"], sort_keys=True),
            "## Runtime receipts\n" + json.dumps(raw["runtime_receipts"], sort_keys=True),
            ("## Children\n" + ", ".join(map(link, IDS[1:])) if item["id"] == PARENT
             else "## Parent\n" + link(PARENT)),
        ]) + "\n"

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
            raise RuntimeError("reference_feasibility_issue_readback")
        receipts.append({"id": item["id"], "number": checked["number"], "state": state})
    print(json.dumps({"issues": receipts, "unrelated_issues_changed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
