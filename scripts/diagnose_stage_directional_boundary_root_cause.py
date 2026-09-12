#!/usr/bin/env python3
"""Read-only post-closeout diagnosis for P3-STAGE-008 evaluation failure.

This script does not search thresholds, replace the frozen candidate, read new market data, or make
qualification claims. It reuses the sealed D3 Primary-only review transport and sealed evaluation
ledger to test whether the remaining Stage-0 false positives are concentrated in the old R0
`center_shift OR width_expansion` instability branch.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import socket
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.stage_learning.directional_boundary_development import (
    RelocationCandidate,
    candidate_stage0,
    relocation_guard,
)
from quanthus.stage_learning.directional_boundary_evaluation import load_ledger, resolve_references

D3_JOB = "stage_7617aafa1f3149c0a65935ef291fc521"
D3_MANIFEST = "sha256:a77bc2b439da05da5d10b7291c64caf5d680148c4c69126ba31ea39ac7a99827"
D3_EVIDENCE = "sha256:912a11480d6e12a3dcc3862bcdb0900592623a419e5282e5bb2500e2ad5086da"
D3_CASES = 95
FROZEN_CANDIDATE = RelocationCandidate(Decimal("0.10"), Decimal("0.50"), Decimal("0.60"))


def request_status(socket_path: Path, job_id: str, caller: str) -> dict[str, Any]:
    request = {
        "operation": "stage_worker_status",
        "payload": {"job_id": job_id, "caller_identity": caller},
    }
    last_error: str | None = None
    for attempt in range(4):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.connect(str(socket_path))
            stream.sendall((json.dumps(request, sort_keys=True) + "\n").encode("utf-8"))
            chunks = bytearray()
            while not chunks.endswith(b"\n"):
                block = stream.recv(1024 * 1024)
                if not block:
                    break
                chunks.extend(block)
        response = json.loads(bytes(chunks))
        if response.get("success") is True and response.get("job_id") == job_id:
            return response
        last_error = str(response.get("error"))
        if attempt < 3:
            time.sleep(1)
    raise RuntimeError("stage_status_read_failed:" + str(last_error))


def decode_page(response: dict[str, Any]) -> dict[str, Any]:
    transport = response.get("review_transport")
    if not isinstance(transport, dict) or transport.get("encoding") != "gzip+base64":
        raise RuntimeError("evaluation_review_transport_missing")
    raw = gzip.decompress(base64.b64decode(transport["payload"]))
    observed = "sha256:" + hashlib.sha256(raw).hexdigest()
    if observed != transport.get("raw_sha256"):
        raise RuntimeError("evaluation_review_transport_digest_mismatch")
    return json.loads(raw)


def primary_numbers(case: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    primary = case["primary"]
    return (
        abs(Decimal(primary["directional_efficiency"]["value"])),
        Decimal(primary["width_change"]["value"]),
        abs(Decimal(primary["center_shift"]["value"])),
    )


def branch(case: dict[str, Any]) -> str:
    efficiency, width_change, center_shift = primary_numbers(case)
    if efficiency > Decimal("0.40"):
        return "outside_old_stage0_efficiency"
    width = width_change >= Decimal("0.20")
    center = center_shift >= Decimal("0.07")
    if width and center:
        return "width_and_center"
    if width:
        return "width_only"
    if center:
        return "center_only"
    return "neither_old_instability_branch"


def semantic_counterfactual(case: dict[str, Any]) -> bool:
    """Diagnostic only: center shift cannot independently assert instability."""
    efficiency, width_change, _center_shift = primary_numbers(case)
    return (
        efficiency <= Decimal("0.40")
        and width_change >= Decimal("0.20")
        and not relocation_guard(case, FROZEN_CANDIDATE)
    )


def metrics(
    cases: list[dict[str, Any]], references: dict[str, str], predicate: Any
) -> dict[str, Any]:
    tp = fp = fn = positive = negative = predictions = 0
    for case in cases:
        reference = references[case["case_id"]]
        if reference == "AMBIGUOUS":
            continue
        prediction = bool(predicate(case))
        if reference == "STAGE_0":
            positive += 1
            tp += int(prediction)
            fn += int(not prediction)
        elif reference in {"STAGE_2", "NEITHER"}:
            negative += 1
            fp += int(prediction)
        predictions += int(prediction)
    precision = None if not tp + fp else Decimal(tp) / Decimal(tp + fp)
    coverage = None if not positive else Decimal(tp) / Decimal(positive)
    fpr = None if not negative else Decimal(fp) / Decimal(negative)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "definite_positive_references": positive,
        "definite_negative_references": negative,
        "stage_0_predictions": predictions,
        "precision": None if precision is None else str(precision),
        "positive_reference_coverage": None if coverage is None else str(coverage),
        "definite_negative_false_positive_rate": None if fpr is None else str(fpr),
    }


def analyze(cases: list[dict[str, Any]], references: dict[str, str]) -> dict[str, Any]:
    branch_by_reference: Counter[str] = Counter()
    remaining_fp_indices: list[int] = []
    center_only_fp_indices: list[int] = []
    center_only_tp_indices: list[int] = []
    non_center_fp_details = []
    for index, case in enumerate(cases):
        reference = references[case["case_id"]]
        observed_branch = branch(case)
        branch_by_reference[f"{reference}:{observed_branch}"] += 1
        candidate_prediction = candidate_stage0(case, FROZEN_CANDIDATE)
        if reference == "STAGE_0" and observed_branch == "center_only":
            center_only_tp_indices.append(index)
        if reference in {"NEITHER", "STAGE_2"} and candidate_prediction:
            remaining_fp_indices.append(index)
            if observed_branch == "center_only":
                center_only_fp_indices.append(index)
            else:
                closes = [Decimal(row[4]) for row in case["history"][-21:]]
                absolute_return = abs(closes[-1] / closes[0] - Decimal(1))
                non_center_fp_details.append({
                    "index": index,
                    "branch": observed_branch,
                    "absolute_window_return20": str(absolute_return),
                    "within_8_to_10_percent_return_band": (
                        Decimal("0.08") <= absolute_return < Decimal("0.10")
                    ),
                })
    frozen_metrics = metrics(
        cases, references, lambda case: candidate_stage0(case, FROZEN_CANDIDATE)
    )
    counterfactual_metrics = metrics(cases, references, semantic_counterfactual)
    return {
        "schema_version": "quanthus.stage-directional-boundary-root-cause-diagnostic.v1",
        "status": "POST_CLOSEOUT_DIAGNOSTIC_ONLY",
        "case_count": len(cases),
        "frozen_candidate_id": FROZEN_CANDIDATE.candidate_id,
        "remaining_false_positive_count": len(remaining_fp_indices),
        "remaining_false_positive_indices": remaining_fp_indices,
        "center_shift_only_false_positive_count": len(center_only_fp_indices),
        "center_shift_only_false_positive_indices": center_only_fp_indices,
        "center_shift_only_share_of_remaining_false_positives": str(
            Decimal(len(center_only_fp_indices)) / Decimal(len(remaining_fp_indices))
        ),
        "definite_stage0_center_shift_only_count": len(center_only_tp_indices),
        "definite_stage0_center_shift_only_indices": center_only_tp_indices,
        "non_center_false_positive_details": non_center_fp_details,
        "frozen_candidate_metrics": frozen_metrics,
        "counterfactual_center_shift_modifier_only_metrics": counterfactual_metrics,
        "branch_counts_by_reference": dict(sorted(branch_by_reference.items())),
        "threshold_search_count": 0,
        "candidate_replacement": False,
        "new_market_data_read": False,
        "qualification_claim": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default="/run/quanthus-mcp-production/control.sock")
    parser.add_argument("--caller", default="stage008-root-cause-diagnostic")
    args = parser.parse_args()
    parent = request_status(args.socket, D3_JOB, args.caller)
    index = parent.get("review_transport_index")
    if not isinstance(index, dict):
        raise RuntimeError("d3_primary_review_index_missing")
    if (
        index.get("manifest_sha256") != D3_MANIFEST
        or index.get("evidence_file_sha256") != D3_EVIDENCE
        or index.get("case_count") != D3_CASES
        or index.get("candidate_output_included") is not False
        or index.get("future_rows_included") is not False
        or index.get("explicit_recent_derived_facts_included") is not False
    ):
        raise RuntimeError("d3_primary_review_index_binding_mismatch")
    cases = []
    for page_ref in index["pages"]:
        page = decode_page(request_status(args.socket, page_ref["job_id"], args.caller))
        for case in page["cases"]:
            if "recent" in case or "recent_available" in case:
                raise RuntimeError("explicit_recent_fact_leaked_into_root_cause_diagnostic")
        cases.extend(page["cases"])
    references = resolve_references(cases, load_ledger())["references"]
    print(json.dumps(analyze(cases, references), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
