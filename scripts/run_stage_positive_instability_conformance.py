#!/usr/bin/env python3
"""Replay P3-STAGE-009-C diagnostic conformance from sealed P3-STAGE-008 D3 evidence."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import socket
import time
from pathlib import Path
from typing import Any

from quanthus.stage_learning.directional_boundary_evaluation import load_ledger, resolve_references
from quanthus.stage_learning.positive_instability_conformance import (
    analyze_consumed_evidence,
    load_config,
)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default="/run/quanthus-mcp-production/control.sock")
    parser.add_argument("--caller", default="stage009-c-positive-instability-conformance")
    args = parser.parse_args()

    config = load_config()
    binding = config["bindings"]["d3_prepare"]
    parent = request_status(args.socket, binding["job_id"], args.caller)
    index = parent.get("review_transport_index")
    if not isinstance(index, dict):
        raise RuntimeError("d3_primary_review_index_missing")
    if (
        index.get("manifest_sha256") != binding["manifest_sha256"]
        or index.get("evidence_file_sha256") != binding["evidence_sha256"]
        or index.get("case_count") != binding["case_count"]
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
                raise RuntimeError("explicit_recent_fact_leaked_into_009c")
        cases.extend(page["cases"])
    references = resolve_references(cases, load_ledger())["references"]
    result = analyze_consumed_evidence(cases, references)
    result["read_only_stage_status_calls"] = 1 + len(index["pages"])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
