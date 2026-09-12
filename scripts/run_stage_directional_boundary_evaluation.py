#!/usr/bin/env python3
"""Replay P3-STAGE-008-D4 from the sealed D3 Primary-only transport and evaluation ledger."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import socket
from pathlib import Path
from typing import Any

from quanthus.stage_learning.directional_boundary_evaluation import evaluate, load_ledger

D3_JOB = "stage_7617aafa1f3149c0a65935ef291fc521"
D3_MANIFEST = "sha256:a77bc2b439da05da5d10b7291c64caf5d680148c4c69126ba31ea39ac7a99827"
D3_EVIDENCE = "sha256:912a11480d6e12a3dcc3862bcdb0900592623a419e5282e5bb2500e2ad5086da"
D3_PATH_FACTS = "sha256:71294c12fb9bc8f74aa6fcd494e935016d680761e6d21d681d8a7b6776e148f0"


def request_status(socket_path: Path, job_id: str, caller: str) -> dict[str, Any]:
    request = {
        "operation": "stage_worker_status",
        "payload": {"job_id": job_id, "caller_identity": caller},
    }
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
    if response.get("success") is not True or response.get("job_id") != job_id:
        raise RuntimeError("stage_status_read_failed:" + str(response.get("error")))
    return response


def decode_page(response: dict[str, Any]) -> dict[str, Any]:
    transport = response.get("review_transport")
    if not isinstance(transport, dict) or transport.get("encoding") != "gzip+base64":
        raise RuntimeError("evaluation_review_transport_missing")
    raw = gzip.decompress(base64.b64decode(transport["payload"]))
    if "sha256:" + hashlib.sha256(raw).hexdigest() != transport.get("raw_sha256"):
        raise RuntimeError("evaluation_review_transport_digest_mismatch")
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default="/run/quanthus-mcp-production/control.sock")
    parser.add_argument("--caller", default="stage008-d4-evaluation-replay")
    args = parser.parse_args()
    parent = request_status(args.socket, D3_JOB, args.caller)
    index = parent.get("review_transport_index")
    if not isinstance(index, dict):
        raise RuntimeError("d3_primary_review_index_missing")
    if (
        index.get("manifest_sha256") != D3_MANIFEST
        or index.get("evidence_file_sha256") != D3_EVIDENCE
        or index.get("path_facts_file_sha256") != D3_PATH_FACTS
        or index.get("case_count") != 95
        or index.get("page_count") != 12
        or index.get("candidate_output_included") is not False
        or index.get("future_rows_included") is not False
        or index.get("explicit_recent_derived_facts_included") is not False
    ):
        raise RuntimeError("d3_primary_review_index_binding_mismatch")
    cases = []
    for page_ref in index["pages"]:
        page = decode_page(request_status(args.socket, page_ref["job_id"], args.caller))
        page_cases = page.get("cases")
        if not isinstance(page_cases, list) or any(
            "recent" in case or "recent_available" in case for case in page_cases
        ):
            raise RuntimeError("explicit_recent_fact_leaked_into_evaluation_review")
        cases.extend(page_cases)
    result = evaluate(cases, load_ledger())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
