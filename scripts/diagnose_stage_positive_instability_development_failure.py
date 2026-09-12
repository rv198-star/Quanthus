#!/usr/bin/env python3
"""Post-result read-only diagnosis for P3-STAGE-009-D development failure.

This script does not search thresholds, alter the frozen grid, replace a candidate, read evaluation
market data, or make a qualification claim. It reuses the sealed D1 Primary-only transport and the
sealed development review ledger to explain why the nearest-to-passing frozen candidate lost
Stage-0 positive-reference coverage.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import socket
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.stage_learning.directional_boundary_development import relocation_guard
from quanthus.stage_learning.positive_instability import assess_positive_instability
from quanthus.stage_learning.positive_instability_development import (
    FROZEN_008_RELOCATION,
    InstabilityCandidate,
    primitive_evidence_for_case,
    unchanged_stage2,
)
from quanthus.stage_learning.positive_instability_development_review import (
    load_ledger,
    resolve_references,
)

D1_JOB = "stage_93c9590d5df34f5e8f38e242077c1e3c"
D1_MANIFEST = "sha256:c49bbf50f01a5319634bdd0e54a1a3716f14e78b462d5570bf3f6c028e64c6b5"
D1_EVIDENCE = "sha256:136d362f24c65b3fdbd99341b0a48ad9a9de6f3197f58ba3d1d5f1a8ad2187e1"
D1_CASES = 122

NEAREST = InstabilityCandidate(
    core_profile_id="core-lenient",
    expansion_profile_id="expansion-lenient",
    reversal_profile_id="reversal-balanced",
    range_width_min=Decimal("0.1852970795568982880161127895"),
    positive_width_change_min=Decimal("0.474834437086092715231788080"),
    absolute_directional_efficiency_max=Decimal("0.1960252935862691960252935863"),
    range_traversal_max=Decimal("0.4461538461538461538461538462"),
    counter_direction_excursion_min=Decimal("0.6363636363636363636363636364"),
)
EXPECTED_NEAREST_ID = "sha256:f46c13b8f6c12fd953d8a4e1973eacf8c35f56cfd7c85d3a1ea8211ab4af7408"


def request_status(socket_path: Path, job_id: str, caller: str) -> dict[str, Any]:
    request = {
        "operation": "stage_worker_status",
        "payload": {"job_id": job_id, "caller_identity": caller},
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(30)
        stream.connect(str(socket_path))
        stream.sendall((json.dumps(request, sort_keys=True) + "\n").encode("utf-8"))
        chunks = bytearray()
        while not chunks.endswith(b"\n"):
            block = stream.recv(1024 * 1024)
            if not block:
                break
            chunks.extend(block)
    response = json.loads(bytes(chunks))
    if response.get("success") is not True or response.get("job_id") != job_id:
        raise RuntimeError("stage_status_read_failed:" + str(response.get("error")))
    return response


def decode(envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, dict) or envelope.get("encoding") != "gzip+base64":
        raise RuntimeError("review_transport_missing")
    raw = gzip.decompress(base64.b64decode(envelope["payload"]))
    if "sha256:" + hashlib.sha256(raw).hexdigest() != envelope.get("raw_sha256"):
        raise RuntimeError("review_transport_digest_mismatch")
    return json.loads(raw)


def _primitive_false_reasons(case: dict[str, Any]) -> tuple[str, ...]:
    primitive = primitive_evidence_for_case(case, NEAREST)
    assessment = assess_positive_instability(primitive)
    reasons: list[str] = []
    if primitive.range_width_material_20 != "TRUE":
        reasons.append("range_width_core_false")
    if primitive.low_directional_efficiency_20 != "TRUE":
        reasons.append("low_directional_efficiency_core_false")
    if assessment.expanding_disorder_20.status != "TRUE":
        reasons.append("expanding_channel_false_or_unknown")
    if assessment.broad_reversal_disorder_20.status != "TRUE":
        reasons.append("broad_reversal_channel_false_or_unknown")
    if relocation_guard(case, FROZEN_008_RELOCATION):
        reasons.append("frozen_008_relocation_guard_positive")
    if unchanged_stage2(case):
        reasons.append("unchanged_stage2_positive")
    return tuple(reasons)


def main() -> int:
    if NEAREST.candidate_id != EXPECTED_NEAREST_ID:
        raise RuntimeError("nearest_candidate_identity_drift")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default="/run/quanthus-mcp-production/control.sock")
    parser.add_argument("--caller", default="stage009-d2-failure-diagnostic")
    args = parser.parse_args()

    parent = request_status(args.socket, D1_JOB, args.caller)
    index = parent.get("review_transport_index")
    if not isinstance(index, dict):
        raise RuntimeError("d1_review_index_missing")
    if (
        index.get("manifest_sha256") != D1_MANIFEST
        or index.get("evidence_file_sha256") != D1_EVIDENCE
        or index.get("case_count") != D1_CASES
        or index.get("candidate_output_included") is not False
        or index.get("threshold_basis_included") is not False
        or index.get("future_rows_included") is not False
    ):
        raise RuntimeError("d1_review_index_binding_mismatch")

    cases: list[dict[str, Any]] = []
    for page_ref in index["pages"]:
        page_status = request_status(args.socket, page_ref["job_id"], args.caller)
        page = decode(page_status.get("review_transport"))
        cases.extend(page["cases"])
    if len(cases) != D1_CASES:
        raise RuntimeError("d1_case_count_drift")

    references = resolve_references(cases, load_ledger())["references"]
    false_negatives = []
    reason_counts: Counter[str] = Counter()
    core_fail_counts: Counter[str] = Counter()
    channel_activation_counts: Counter[str] = Counter()
    for index_value, case in enumerate(cases):
        if references[case["case_id"]] != "STAGE_0":
            continue
        primitive = primitive_evidence_for_case(case, NEAREST)
        assessment = assess_positive_instability(primitive)
        machine_stage0 = (
            assessment.status == "TRUE"
            and not relocation_guard(case, FROZEN_008_RELOCATION)
            and not unchanged_stage2(case)
        )
        for channel in assessment.activated_positive_channels:
            channel_activation_counts[channel] += 1
        if machine_stage0:
            continue
        reasons = _primitive_false_reasons(case)
        reason_counts.update(reasons)
        core_false = []
        if primitive.range_width_material_20 != "TRUE":
            core_false.append("range_width")
        if primitive.low_directional_efficiency_20 != "TRUE":
            core_false.append("directional_efficiency")
        if not core_false:
            core_false.append("none")
        core_fail_counts.update(["+".join(core_false)])
        false_negatives.append({
            "index": index_value,
            "case_id": case["case_id"],
            "security_code": case.get("security_code"),
            "market_date": case.get("market_date"),
            "reasons": list(reasons),
            "primitive_evidence": primitive.to_dict(),
            "channel_status": {
                "expanding_disorder_20": assessment.expanding_disorder_20.status,
                "broad_reversal_disorder_20": assessment.broad_reversal_disorder_20.status,
            },
        })

    output = {
        "schema_version": "quanthus.stage-positive-instability-development-failure-diagnostic.v1",
        "status": "POST_D2_DIAGNOSTIC_ONLY",
        "source_job": D1_JOB,
        "source_manifest_sha256": D1_MANIFEST,
        "development_evidence_digest": D1_EVIDENCE,
        "nearest_candidate_id": NEAREST.candidate_id,
        "nearest_candidate": NEAREST.to_dict(),
        "definite_stage0_reference_count": sum(value == "STAGE_0" for value in references.values()),
        "nearest_candidate_false_negative_count": len(false_negatives),
        "false_negative_reason_counts": dict(sorted(reason_counts.items())),
        "false_negative_core_fail_counts": dict(sorted(core_fail_counts.items())),
        "stage0_reference_channel_activation_counts_before_guards": dict(
            sorted(channel_activation_counts.items())
        ),
        "false_negatives": false_negatives,
        "threshold_search_count": 0,
        "grid_expansion": False,
        "candidate_replacement": False,
        "evaluation_data_read": False,
        "qualification_claim": False,
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
