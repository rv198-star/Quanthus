from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base231_distribution_currency import (
    qualify_currency_source,
)


def sample():
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    aid = "456"
    actual = dict(
        secCode="003018",
        orgId="original-org",
        announcementId=aid,
        announcementTitle="关于2026年半年度利润分配方案的公告",
        announcementTime=int(datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp() * 1000),
        adjunctUrl="finalpage/2026-08-20/456.PDF",
    )
    sha = "sha256:" + "a" * 64

    def receipt(uri):
        return dict(
            URI=uri,
            http_status=200,
            error=None,
            response_cap_reached=False,
            actual_transport="urllib.request",
            body_sha256=sha,
            actual_response_body_bytes=100,
            completed_at=(now - timedelta(minutes=1)).isoformat(),
        )

    e = dict(
        actual_index_row=actual,
        index=dict(announcements=[actual]),
        index_receipt=receipt("https://www.cninfo.com.cn/new/hisAnnouncement/query"),
        PDF_receipt=receipt("https://static.cninfo.com.cn/finalpage/2026-08-20/456.PDF"),
        PDF_sha256=sha,
        index_sha256=sha,
        PDF_bytes=100,
        index_bytes=100,
        body="证券代码003018 2026年半年度利润分配方案：每10股派发人民币1元（含税）",
    )
    e["index_receipt"]["request_payload"] = dict(stock="003018,original-org")
    original = dict(
        secCode="003018",
        orgId="original-org",
        announcementTime=int(datetime(2026, 9, 10, tzinfo=timezone.utc).timestamp() * 1000),
    )
    return e, dict(implementation=original, observed_at=now)


def test_currency_source_has_actual_new_clock_and_no_old_acceptance():
    evidence, args = sample()
    before = deepcopy((evidence, args))
    proof = qualify_currency_source(evidence, **args)
    assert proof["observed_at"] == args["observed_at"]
    assert not proof["historical_PIT_restored"] and not proof["old_acceptance_claimed"]
    assert not proof["source_priority_granted"]
    assert proof["factor_admission"] == "NOT_GRANTED"
    assert (evidence, args) == before


@pytest.mark.parametrize(
    "fault",
    [
        "code",
        "org",
        "missing_index",
        "wrong_period",
        "cancelled",
        "date",
        "fake_uri",
        "future_capture",
        "fake_hash",
        "truncated",
        "transport",
        "bytes",
        "query",
        "status",
    ],
)
def test_currency_corroboration_rejects_wrong_event_or_incomplete_actual_receipt(fault):
    e, args = sample()
    if fault == "code":
        e["actual_index_row"]["secCode"] = "000001"
    elif fault == "org":
        e["actual_index_row"]["orgId"] = "other-org"
    elif fault == "missing_index":
        e["index"]["announcements"] = []
    elif fault == "wrong_period":
        e["actual_index_row"]["announcementTitle"] = "2025年度利润分配方案公告"
    elif fault == "cancelled":
        e["actual_index_row"]["announcementTitle"] += "取消公告"
    elif fault == "date":
        args["implementation"]["announcementTime"] = 0
    elif fault == "fake_uri":
        e["actual_index_row"]["adjunctUrl"] = "finalpage/2026-08-20/123.PDF"
    elif fault == "future_capture":
        e["PDF_receipt"]["completed_at"] = (args["observed_at"] + timedelta(seconds=1)).isoformat()
    elif fault == "fake_hash":
        e["PDF_receipt"]["body_sha256"] = "unknown"
    elif fault == "truncated":
        e["PDF_receipt"]["response_cap_reached"] = True
    elif fault == "transport":
        e["PDF_receipt"]["actual_transport"] = "invented"
    elif fault == "bytes":
        e["PDF_bytes"] += 1
    elif fault == "query":
        e["index_receipt"]["request_payload"]["stock"] = "000001,other-org"
    else:
        e["PDF_receipt"]["http_status"] = 404
    with pytest.raises(RuntimeError):
        qualify_currency_source(e, **args)
