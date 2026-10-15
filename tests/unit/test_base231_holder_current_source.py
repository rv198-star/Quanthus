from copy import deepcopy
from datetime import datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base231_official_holder_replay import (
    verify_current_document_receipts,
)


def source():
    day = datetime(2026, 4, 20, tzinfo=timezone.utc)
    actual = dict(
        secCode="600001",
        orgId="official-org",
        announcementId="12345",
        announcementTime=int(day.timestamp() * 1000),
        adjunctUrl="finalpage/2026-04-20/12345.PDF",
    )
    common = dict(
        http_status=200,
        error=None,
        response_cap_reached=False,
        actual_transport="urllib.request",
        completed_at="2026-10-16T00:00:00+00:00",
    )
    return dict(
        actual=actual,
        index_receipt=dict(
            common,
            URI="https://www.cninfo.com.cn/new/hisAnnouncement/query",
            request_payload=dict(stock="600001,official-org"),
            body_sha256="sha256:" + "a" * 64,
            actual_response_body_bytes=100,
        ),
        PDF_receipt=dict(
            common,
            URI="https://static.cninfo.com.cn/" + actual["adjunctUrl"],
            body_sha256="sha256:" + "b" * 64,
            actual_response_body_bytes=200,
        ),
        index_sha256="sha256:" + "a" * 64,
        index_bytes=100,
        PDF_sha256="sha256:" + "b" * 64,
        PDF_bytes=200,
        observed_at=datetime(2026, 10, 16, 1, tzinfo=timezone.utc),
    )


def test_actual_current_receipts_do_not_require_or_claim_old_curl_acceptance():
    args = source()
    before = deepcopy(args)
    verify_current_document_receipts(**args)
    assert args == before


@pytest.mark.parametrize(
    "fault",
    [
        "issuer",
        "URI",
        "PDF_URI",
        "index_hash",
        "PDF_hash",
        "index_size",
        "PDF_size",
        "status",
        "error",
        "cap",
        "transport",
        "future",
        "publication",
        "date_identity",
    ],
)
def test_current_holder_receipts_fail_closed_on_actual_source_disagreement(fault):
    args = source()
    if fault == "issuer":
        args["index_receipt"]["request_payload"]["stock"] = "600002,official-org"
    elif fault == "URI":
        args["index_receipt"]["URI"] = "https://invalid.test/query"
    elif fault == "PDF_URI":
        args["PDF_receipt"]["URI"] = "https://invalid.test/document.PDF"
    elif fault == "index_hash":
        args["index_sha256"] = "sha256:" + "c" * 64
    elif fault == "PDF_hash":
        args["PDF_sha256"] = "sha256:" + "c" * 64
    elif fault == "index_size":
        args["index_bytes"] += 1
    elif fault == "PDF_size":
        args["PDF_bytes"] += 1
    elif fault == "status":
        args["PDF_receipt"]["http_status"] = 404
    elif fault == "error":
        args["PDF_receipt"]["error"] = "Interrupted"
    elif fault == "cap":
        args["PDF_receipt"]["response_cap_reached"] = True
    elif fault == "transport":
        args["PDF_receipt"]["actual_transport"] = "claimed-curl"
    elif fault == "future":
        args["PDF_receipt"]["completed_at"] = "2026-10-17T00:00:00+00:00"
    elif fault == "publication":
        args["PDF_receipt"]["completed_at"] = "2025-01-01T00:00:00+00:00"
    else:
        args["actual"]["adjunctUrl"] = "finalpage/2026-04-21/12345.PDF"
    with pytest.raises(RuntimeError):
        verify_current_document_receipts(**args)
