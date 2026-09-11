#!/usr/bin/env python3
"""Replay P3-STAGE-008-D calibration from the sealed D1 Primary-only transport."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import socket
from pathlib import Path
from typing import Any

from quanthus.stage_learning.directional_boundary_calibration import calibrate, load_ledger

D1_JOB = "stage_7ffc681cd5254aa0ab3e0d98026c42d7"
D1_MANIFEST = "sha256:2ddc4f90376e2061932862c2ef1a8cfd27f8774d1df9bdb59de8bf4e3bf1f9a3"
D1_EVIDENCE = "sha256:86091171863673e1fad9c41083719e8f48d9e6bc2ba86d4c7eb58b5fb6db0982"


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
        raise RuntimeError("calibration_review_transport_missing")
    raw = gzip.decompress(base64.b64decode(transport["payload"]))
    if "sha256:" + hashlib.sha256(raw).hexdigest() != transport.get("raw_sha256"):
        raise RuntimeError("calibration_review_transport_digest_mismatch")
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default="/run/quanthus-mcp-production/control.sock")
    parser.add_argument("--caller", default="stage008-d-calibration-replay")
    args = parser.parse_args()
    parent = request_status(args.socket, D1_JOB, args.caller)
    index = parent.get("review_transport_index")
    if not isinstance(index, dict):
        raise RuntimeError("d1_primary_review_index_missing")
    if (
        index.get("manifest_sha256") != D1_MANIFEST
        or index.get("evidence_file_sha256") != D1_EVIDENCE
        or index.get("case_count") != 64
        or index.get("page_count") != 8
        or index.get("candidate_output_included") is not False
        or index.get("future_rows_included") is not False
        or index.get("explicit_recent_derived_facts_included") is not False
    ):
        raise RuntimeError("d1_primary_review_index_binding_mismatch")
    cases = []
    for page_ref in index["pages"]:
        page = decode_page(request_status(args.socket, page_ref["job_id"], args.caller))
        if "recent" in json.dumps(page, sort_keys=True):
            raise RuntimeError("explicit_recent_fact_leaked_into_calibration_review")
        cases.extend(page["cases"])
    result = calibrate(cases, load_ledger())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
