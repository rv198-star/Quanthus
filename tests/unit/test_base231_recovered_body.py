from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse.base231_recovered_body import (
    CONTRACT_ID,
    verify_detail_normalization,
)


def sample():
    sha = "sha256:" + "a" * 64
    actual = dict(announcementId="456", adjunctUrl="finalpage/2026-09-08/456.PDF")
    uri = "https://static.cninfo.com.cn/" + actual["adjunctUrl"]
    receipt = dict(
        URI=uri,
        body_sha256=sha,
        completed_at="2026-10-16T00:00:00+00:00",
        raw_transport_receipt_sha256=sha,
        actual_transport="urllib.request",
        curl_was_executed=False,
    )
    return dict(
        detail=dict(announcement=actual),
        detail_receipt=dict(
            URI="https://www.cninfo.com.cn/new/announcement/bulletin_detail?announceId=456&flag=false&announceTime=",
            http_status=200,
            error=None,
            response_cap_reached=False,
            body_sha256=sha,
            actual_response_body_bytes=100,
        ),
        normalized_index=dict(
            announcements=[actual],
            normalization_contract=CONTRACT_ID,
            raw_detail_sha256=sha,
            raw_detail_receipt_sha256=sha,
        ),
        normalized_receipt=receipt,
        transport_receipt=dict(
            URI=uri,
            status=200,
            error=None,
            limit_reached=False,
            body_sha256=sha[7:],
            body_bytes=100,
            UTC=receipt["completed_at"],
        ),
        detail_sha256=sha,
        detail_bytes=100,
        detail_receipt_sha256=sha,
        transport_receipt_sha256=sha,
        PDF_sha256=sha,
        PDF_bytes=100,
    )


def test_exact_detail_is_normalized_without_claiming_historical_transport():
    args = sample()
    before = deepcopy(args)
    verify_detail_normalization(**args)
    assert args == before


@pytest.mark.parametrize(
    "fault",
    [
        "ID",
        "origin",
        "index",
        "detail_receipt",
        "detail_bytes",
        "detail_hash",
        "PDF_hash",
        "PDF_status",
        "PDF_bytes",
        "fake_curl",
        "clock",
        "truncated",
    ],
)
def test_actual_exact_detail_and_PDF_receipts_cannot_be_replaced_by_normalized_claim(fault):
    args = sample()
    if fault == "ID":
        args["detail_receipt"]["URI"] = args["detail_receipt"]["URI"].replace("456", "457")
    elif fault == "origin":
        args["detail_receipt"]["URI"] = args["detail_receipt"]["URI"].replace(
            "www.cninfo.com.cn", "invalid.test"
        )
    elif fault == "index":
        args["normalized_index"]["announcements"] = []
    elif fault == "detail_receipt":
        args["normalized_index"]["raw_detail_receipt_sha256"] = "unknown"
    elif fault == "detail_bytes":
        args["detail_bytes"] += 1
    elif fault == "detail_hash":
        args["detail_sha256"] = "unknown"
    elif fault == "PDF_hash":
        args["PDF_sha256"] = "unknown"
    elif fault == "PDF_status":
        args["transport_receipt"]["status"] = 404
    elif fault == "PDF_bytes":
        args["PDF_bytes"] += 1
    elif fault == "fake_curl":
        args["normalized_receipt"]["curl_was_executed"] = True
    elif fault == "clock":
        args["normalized_receipt"]["completed_at"] = "2026-09-08T00:00:00+00:00"
    else:
        args["detail_receipt"]["response_cap_reached"] = True
    with pytest.raises(RuntimeError):
        verify_detail_normalization(**args)
