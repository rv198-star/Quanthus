"""Recover an exact retained reference through the actual official detail API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .base22 import file_sha
from .base22_bounded import BoundedError
from .base231_disclosure_body import replay_body_domain

CONTRACT_ID = "p10-base231-exact-official-detail-body-recovery-v1"


def verify_detail_normalization(
    *,
    detail: dict[str, Any],
    detail_receipt: dict[str, Any],
    normalized_index: dict[str, Any],
    normalized_receipt: dict[str, Any],
    transport_receipt: dict[str, Any],
    detail_sha256: str,
    detail_bytes: int,
    detail_receipt_sha256: str,
    transport_receipt_sha256: str,
    PDF_sha256: str,
    PDF_bytes: int,
) -> None:
    actual = detail["announcement"]
    aid = str(actual["announcementId"])
    uri = "https://static.cninfo.com.cn/" + actual["adjunctUrl"]
    url = urlsplit(detail_receipt["URI"])
    query = parse_qs(url.query, keep_blank_values=True)
    if (
        url.scheme != "https"
        or url.netloc != "www.cninfo.com.cn"
        or url.path != "/new/announcement/bulletin_detail"
        or query != dict(announceId=[aid], flag=["false"], announceTime=[""])
        or detail_receipt.get("http_status") != 200
        or detail_receipt.get("error") is not None
        or detail_receipt.get("response_cap_reached") is not False
        or detail_receipt.get("body_sha256") != detail_sha256
        or detail_receipt.get("actual_response_body_bytes") != detail_bytes
        or normalized_index
        != dict(
            announcements=[actual],
            normalization_contract=CONTRACT_ID,
            raw_detail_sha256=detail_sha256,
            raw_detail_receipt_sha256=detail_receipt_sha256,
        )
    ):
        raise BoundedError("RECOVERED_BODY_OFFICIAL_DETAIL_NORMALIZATION_UNPROVEN")
    if (
        transport_receipt.get("URI") != uri
        or type(transport_receipt.get("status")) is not int
        or transport_receipt["status"] != 200
        or transport_receipt.get("error") is not None
        or transport_receipt.get("limit_reached") is not False
        or "sha256:" + str(transport_receipt.get("body_sha256")) != PDF_sha256
        or transport_receipt.get("body_bytes") != PDF_bytes
        or normalized_receipt.get("URI") != uri
        or normalized_receipt.get("body_sha256") != PDF_sha256
        or normalized_receipt.get("completed_at") != transport_receipt.get("UTC")
        or normalized_receipt.get("raw_transport_receipt_sha256") != transport_receipt_sha256
        or normalized_receipt.get("actual_transport") != "urllib.request"
        or normalized_receipt.get("curl_was_executed") is not False
    ):
        raise BoundedError("RECOVERED_BODY_ACTUAL_TRANSPORT_UNPROVEN")


def replay_recovered_body(root: Path) -> dict[str, Any]:
    plan = json.loads((root / "INPUTS.json").read_bytes())
    code = Path("src/quanthus/pipelines/securities_warehouse/base231_recovered_body.py")
    if (
        plan.get("recovered_detail_contract") != CONTRACT_ID
        or not plan.get("selection")
        or plan.get("code_inputs", {}).get(str(code)) != file_sha(code)
    ):
        raise BoundedError("RECOVERED_BODY_OFFLINE_CONTRACT_REQUIRED")
    result = replay_body_domain(root)
    for spec in plan["selection"]:
        extra = spec["recovered_source"]
        verify_detail_normalization(
            detail=json.loads((root / extra["detail"]).read_bytes()),
            detail_receipt=json.loads((root / extra["detail_receipt"]).read_bytes()),
            normalized_index=json.loads((root / spec["index"]).read_bytes()),
            normalized_receipt=json.loads((root / spec["receipt"]).read_bytes()),
            transport_receipt=json.loads((root / extra["transport_receipt"]).read_bytes()),
            detail_sha256=file_sha(root / extra["detail"]),
            detail_bytes=(root / extra["detail"]).stat().st_size,
            detail_receipt_sha256=file_sha(root / extra["detail_receipt"]),
            transport_receipt_sha256=file_sha(root / extra["transport_receipt"]),
            PDF_sha256=file_sha(root / spec["PDF"]),
            PDF_bytes=(root / spec["PDF"]).stat().st_size,
        )
    return {**result, "status": "PASS_EXACT_RETAINED_REFERENCE_AND_ACTUAL_DETAIL_BODY_REPLAY"}
