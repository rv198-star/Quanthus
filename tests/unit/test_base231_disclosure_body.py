from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base231_disclosure_body import project_body
from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity


def sample():
    published = datetime(2026, 9, 29, 16, tzinfo=timezone.utc)
    actual = dict(
        secCode="000400",
        announcementId="123",
        announcementTitle="合成公告",
        announcementTime=int(published.timestamp() * 1000),
        adjunctUrl="finalpage/2026-09-30/123.PDF",
    )
    old = dict(
        source_document_id="123",
        company_id="c",
        disclosure_id="d",
        business_date="2026-09-30",
        explicit_references=[dict(security_id="s")],
    )
    uri = "https://static.cninfo.com.cn/" + actual["adjunctUrl"]
    selection = dict(
        original_disclosure=old,
        actual_index_row=actual,
        actual_document_URI=uri,
        index_sha256="sha256:" + "a" * 64,
    )
    receipt = dict(
        URI=uri,
        completed_at="2026-10-14T00:00:00+00:00",
        curl_exit=0,
        http_status="200",
        body_sha256="sha256:" + "b" * 64,
    )
    parent = FinancialCompanyIdentity(
        "000400", "s", "c", datetime(1990, 1, 1, tzinfo=timezone.utc), None
    )
    return (
        selection,
        receipt,
        dict(
            index=dict(announcements=[actual]),
            body_sha256=receipt["body_sha256"],
            body_text="证券代码：000400 合成正文",
            parent=parent,
            observed_at=datetime(2026, 10, 14, 1, tzinfo=timezone.utc),
        ),
    )


def test_new_capture_body_observation_preserves_original_identity_and_publication():
    selection, receipt, args = sample()
    before = deepcopy(selection)
    rows, proof = project_body(selection, receipt, **args)
    assert rows["dwd_disclosure"][0]["disclosure_id"] == "d"
    assert rows["dwd_disclosure"][0]["announced_at"].date() == date(2026, 9, 29)
    assert rows["dwd_disclosure"][0]["knowledge_from"] == args["observed_at"]
    assert proof["original_PIT_restored"] is proof["weekly_fields_or_no_change_qualified"] is False
    assert selection == before


@pytest.mark.parametrize(
    "fault",
    [
        "code",
        "company",
        "security",
        "document",
        "index",
        "hash",
        "url",
        "clock",
        "September_boundary",
        "body_code",
    ],
)
def test_unknown_identity_changed_body_or_date_is_held(fault):
    selection, receipt, args = sample()
    if fault == "code":
        selection["actual_index_row"]["secCode"] = "000401"
    elif fault == "company":
        selection["original_disclosure"]["company_id"] = "other"
    elif fault == "security":
        selection["original_disclosure"]["explicit_references"][0]["security_id"] = "other"
    elif fault == "document":
        selection["original_disclosure"]["source_document_id"] = "124"
    elif fault == "index":
        args["index"] = {}
    elif fault == "hash":
        args["body_sha256"] = "sha256:" + "c" * 64
    elif fault == "url":
        receipt["URI"] += "x"
    elif fault == "clock":
        receipt["completed_at"] = "2026-10-15T00:00:00+00:00"
    elif fault == "September_boundary":
        selection["original_disclosure"]["business_date"] = "2026-10-01"
        selection["actual_index_row"]["announcementTime"] = int(
            datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000
        )
    else:
        args["body_text"] = "证券代码：000401"
    with pytest.raises(RuntimeError):
        project_body(selection, receipt, **args)
