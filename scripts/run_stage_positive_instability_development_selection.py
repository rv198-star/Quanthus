#!/usr/bin/env python3
"""Replay P3-STAGE-009-D2 from sealed D1 review and post-review selection transports."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import socket
from pathlib import Path
from typing import Any

from quanthus.stage_learning.positive_instability_development_review import (
    load_ledger,
    select_candidate,
)

D1_JOB = "stage_93c9590d5df34f5e8f38e242077c1e3c"
D1_MANIFEST = "sha256:c49bbf50f01a5319634bdd0e54a1a3716f14e78b462d5570bf3f6c028e64c6b5"
D1_EVIDENCE = "sha256:136d362f24c65b3fdbd99341b0a48ad9a9de6f3197f58ba3d1d5f1a8ad2187e1"
D1_CASES = 122


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


def decode(envelope: Any, label: str) -> dict[str, Any]:
    if not isinstance(envelope, dict) or envelope.get("encoding") != "gzip+base64":
        raise RuntimeError(label + "_transport_missing")
    raw = gzip.decompress(base64.b64decode(envelope["payload"]))
    if "sha256:" + hashlib.sha256(raw).hexdigest() != envelope.get("raw_sha256"):
        raise RuntimeError(label + "_transport_digest_mismatch")
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default="/run/quanthus-mcp-production/control.sock")
    parser.add_argument("--caller", default="stage009-d2-selection")
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
    cases = []
    for page_ref in index["pages"]:
        page = decode(
            request_status(args.socket, page_ref["job_id"], args.caller).get("review_transport"),
            "review",
        )
        cases.extend(page["cases"])
    selection = decode(parent.get("selection_transport"), "selection")
    if (
        selection.get("parent_job_id") != D1_JOB
        or selection.get("manifest_sha256") != D1_MANIFEST
        or selection.get("review_ledger_must_be_sealed_before_use") is not True
        or selection.get("evaluation_data_read") is not False
        or selection.get("candidate_output_included") is not False
        or selection.get("parameter_search_performed") is not False
    ):
        raise RuntimeError("d1_selection_transport_binding_mismatch")
    result = select_candidate(cases, load_ledger(), selection["threshold_basis"])
    result["source_job"] = D1_JOB
    result["source_manifest_sha256"] = D1_MANIFEST
    result["development_evidence_digest"] = D1_EVIDENCE
    result["selection_transport_raw_sha256"] = parent["selection_transport"]["raw_sha256"]
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
