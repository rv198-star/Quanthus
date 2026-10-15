from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base231_current_action import (
    CONTRACT_ID,
    current_reference,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialCompanyIdentity


def sample():
    clock = datetime(2026, 10, 16, tzinfo=timezone.utc)
    actual = dict(
        secCode="003018",
        announcementId="456",
        announcementTitle="合成实施公告",
        announcementTime=int(datetime(2026, 9, 10, tzinfo=timezone.utc).timestamp() * 1000),
        adjunctUrl="finalpage/2026-09-10/456.PDF",
    )
    args = dict(
        parent=FinancialCompanyIdentity(
            "003018", "s", "c", datetime(1990, 1, 1, tzinfo=timezone.utc), None
        ),
        index_sha256="sha256:" + "a" * 64,
        index_received_at=clock - timedelta(minutes=1),
        observed_at=clock,
    )
    return actual, args


def test_new_official_reference_has_actual_id_current_knowledge_and_no_old_claim():
    actual, args = sample()
    before = deepcopy((actual, args))
    row = current_reference(actual, **args)
    assert row["source_document_id"] == "456"
    assert row["reference_origin"] == row["source_revision"] == CONTRACT_ID
    assert row["knowledge_from"] == args["observed_at"]
    assert row["source_observed_at"] == args["index_received_at"]
    assert row["announced_at"].date().isoformat() == "2026-09-10"
    assert row["historical_PIT_restored"] is False
    assert row["old_reference_recovered"] is row["old_base_acceptance_claimed"] is False
    assert (actual, args) == before


@pytest.mark.parametrize(
    "fault",
    [
        "code",
        "id",
        "numeric_id",
        "uri",
        "title",
        "publication",
        "september",
        "capture",
        "hash",
        "listing",
    ],
)
def test_current_official_reference_rejects_false_identity_and_clocks(fault):
    actual, args = sample()
    if fault == "code":
        actual["secCode"] = "000001"
    elif fault == "id":
        actual["announcementId"] = "123"
    elif fault == "numeric_id":
        actual["announcementId"] = 456
    elif fault == "uri":
        actual["adjunctUrl"] = "https://unqualified.invalid/456.PDF"
    elif fault == "title":
        actual["announcementTitle"] = ""
    elif fault == "publication":
        actual["announcementTime"] = str(actual["announcementTime"])
    elif fault == "september":
        actual["announcementTime"] = int(
            datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000
        )
        actual["adjunctUrl"] = "finalpage/2026-10-01/456.PDF"
    elif fault == "capture":
        args["index_received_at"] = args["observed_at"] + timedelta(seconds=1)
    elif fault == "hash":
        args["index_sha256"] = "unknown"
    else:
        args["parent"] = FinancialCompanyIdentity("003018", "s", "c", args["observed_at"], None)
    with pytest.raises(RuntimeError):
        current_reference(actual, **args)


def test_current_official_replay_requires_its_own_code_binding_before_sources(tmp_path):
    import json

    from quanthus.pipelines.securities_warehouse.base231_current_action import replay_current_action

    (tmp_path / "INPUTS.json").write_text(
        json.dumps(
            dict(
                current_reference_contract=CONTRACT_ID,
                selection=[{}],
                code_inputs={},
            )
        )
    )
    with pytest.raises(RuntimeError, match="CURRENT_OFFICIAL_REFERENCE_CONTRACT_REQUIRED"):
        replay_current_action(tmp_path)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "origin",
        "index_hash",
        "index_failure",
        "truncated",
        "transport_failure",
        "bytes",
        "clock",
        "receipt_hash",
        "pretend_curl",
    ],
)
def test_current_receipts_bind_actual_transport_and_do_not_invent_curl(fault):
    from quanthus.pipelines.securities_warehouse.base231_current_action import (
        verify_current_receipts,
    )

    sha = "sha256:" + "a" * 64
    uri = "https://static.cninfo.com.cn/finalpage/2026-09-10/456.PDF"
    index = dict(
        URI="https://www.cninfo.com.cn/new/hisAnnouncement/query",
        http_status=200,
        error=None,
        response_cap_reached=False,
        body_sha256=sha,
    )
    transport = dict(
        status=200,
        error=None,
        limit_reached=False,
        URI=uri,
        body_sha256=sha[7:],
        body_bytes=100,
        UTC="2026-10-16T00:00:00+00:00",
    )
    normalized = dict(
        completed_at=transport["UTC"],
        raw_transport_receipt_sha256=sha,
        actual_transport="urllib.request",
        curl_was_executed=False,
    )
    if fault == "origin":
        index["URI"] = "https://unqualified.invalid/index"
    elif fault == "index_hash":
        index["body_sha256"] = "unknown"
    elif fault == "index_failure":
        index["http_status"] = 404
    elif fault == "truncated":
        transport["limit_reached"] = True
    elif fault == "transport_failure":
        transport["error"] = "TIMEOUT"
    elif fault == "bytes":
        transport["body_bytes"] = 99
    elif fault == "clock":
        normalized["completed_at"] = "2026-09-10T00:00:00+00:00"
    elif fault == "receipt_hash":
        normalized["raw_transport_receipt_sha256"] = "unknown"
    elif fault == "pretend_curl":
        normalized["curl_was_executed"] = True
    args = dict(
        index_sha256=sha,
        document_sha256=sha,
        document_bytes=100,
        transport_receipt_sha256=sha,
        document_uri=uri,
    )
    if fault is None:
        verify_current_receipts(index, transport, normalized, **args)
    else:
        with pytest.raises(RuntimeError, match="CURRENT_OFFICIAL_SOURCE_RECEIPT_CHANGED"):
            verify_current_receipts(index, transport, normalized, **args)
