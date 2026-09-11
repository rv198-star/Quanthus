#!/usr/bin/env python3
"""Run P3-STAGE-008-C from the existing read-only Stage-007 review transport."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import socket
from pathlib import Path
from typing import Any

from quanthus.stage_learning.directional_boundary_conformance import (
    analyze_exposed_pages,
    load_config,
)


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
        raise RuntimeError("review_transport_missing")
    compressed = base64.b64decode(transport["payload"])
    raw = gzip.decompress(compressed)
    observed = "sha256:" + hashlib.sha256(raw).hexdigest()
    if observed != transport.get("raw_sha256"):
        raise RuntimeError("review_transport_digest_mismatch")
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--socket", default="/run/quanthus-mcp-production/control.sock", type=Path,
    )
    parser.add_argument("--caller", default="stage008-c-conformance")
    args = parser.parse_args()
    config = load_config()
    parent_job = config["bindings"]["stage_007_prepare"]["job_id"]
    parent = request_status(args.socket, parent_job, args.caller)
    index = parent.get("review_transport_index")
    if not isinstance(index, dict):
        raise RuntimeError("review_transport_index_missing")
    expected = config["bindings"]["stage_007_prepare"]
    if (
        index.get("parent_job_id") != parent_job
        or index.get("manifest_sha256") != expected["manifest_sha256"]
        or index.get("evidence_file_sha256") != expected["evidence_sha256"]
        or index.get("case_count") != expected["case_count"]
        or index.get("page_count") != expected["page_count"]
        or index.get("candidate_output_included") is not False
        or index.get("future_rows_included") is not False
    ):
        raise RuntimeError("review_transport_index_binding_mismatch")
    pages = []
    for item in index["pages"]:
        page_response = request_status(args.socket, item["job_id"], args.caller)
        pages.append(decode_page(page_response))
    result = analyze_exposed_pages(pages)
    result["read_only_status_calls"] = 1 + len(pages)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
