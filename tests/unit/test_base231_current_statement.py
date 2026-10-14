import ast
import gzip
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from quanthus.pipelines.securities_warehouse import base231_current_statement as adapter
from tests.unit.test_base23_financial import case


def current(token="1.00000000001"):
    raw, source, resolver, _ = case(token)
    raw.update(
        market_date="2026-10-14",
        acquired_at="2026-10-14T09:00:00.123456+08:00",
        source="sina.CompanyFinanceService.getFinanceReport2022",
        source_revision=adapter.CONTRACT_ID,
        provider_source="fzb",
    )
    body = json.loads(gzip.decompress(ast.literal_eval(raw["raw_payload_gzip"].decode())))
    body["result"]["data"]["report_list"]["20260630"]["rCurrency"] = "CNY"
    encode(raw, body)
    return raw, source, resolver, datetime(2026, 10, 14, 2, tzinfo=timezone.utc)


def encode(raw, body):
    payload = json.dumps(body).encode()
    raw.update(
        raw_payload_gzip=repr(gzip.compress(payload, mtime=0)).encode(),
        raw_payload_bytes=len(payload),
        raw_payload_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
    )


@pytest.mark.parametrize("token", ["1.00000000001", "-0.00000", "0", None, "", "literal"])
def test_current_capture_keeps_literal_currency_and_all_real_clocks(token):
    raw, source, resolver, observed = current(token)
    before = deepcopy(raw)
    with patch.object(
        adapter, "decode_financial_envelope", wraps=adapter.decode_financial_envelope
    ) as decode:
        rows, proof = adapter.current_statement(
            raw, source=source, resolver=resolver, observed_at=observed
        )
    assert decode.call_count == 1
    assert proof["actual_capture_date"] == "2026-10-14"
    assert proof["original_acquired_at"] == raw["acquired_at"]
    assert proof["current_capture_not_September"] is True
    assert proof["historical_PIT_qualified"] is False
    assert rows["dwd_financial_fact"][0]["unit_code"] == "CNY"
    assert rows["dwd_financial_fact"][0]["knowledge_from"] == observed
    assert rows["dwd_financial_fact"][0]["published_at"] is None
    assert raw == before
    if token == "1.00000000001":
        assert rows["dwd_financial_fact"][0]["value_text"] == token
        assert rows["dwd_financial_fact"][0]["value_decimal"] is None


@pytest.mark.parametrize("fault", ["capture", "future", "source", "currency", "numeric", "count"])
def test_unknown_currency_or_fabricated_capture_never_qualifies(fault):
    raw, source, resolver, observed = current()
    body = json.loads(gzip.decompress(ast.literal_eval(raw["raw_payload_gzip"].decode())))
    report = body["result"]["data"]["report_list"]["20260630"]
    if fault == "capture":
        raw["market_date"] = "2026-09-30"
    elif fault == "future":
        raw["acquired_at"] = "2027-01-01T00:00:00+08:00"
    elif fault == "source":
        raw["source"] = "other"
    elif fault == "currency":
        report["rCurrency"] = None
    elif fault == "numeric":
        report["data"][0]["item_value"] = 1.2
    else:
        raw["period_count"] += 1
    encode(raw, body)
    with pytest.raises(RuntimeError):
        adapter.current_statement(raw, source=source, resolver=resolver, observed_at=observed)


def test_october_report_period_is_held_without_editing_original_envelope():
    raw, source, resolver, observed = current("0")
    body = json.loads(gzip.decompress(ast.literal_eval(raw["raw_payload_gzip"].decode())))
    body["result"]["data"]["report_list"]["20261031"] = deepcopy(
        body["result"]["data"]["report_list"]["20260630"]
    )
    raw["period_count"] = 2
    encode(raw, body)
    rows, proof = adapter.current_statement(
        raw, source=source, resolver=resolver, observed_at=observed
    )
    assert len(rows["dwd_financial_report"]) == 1
    assert proof["after_through_reports_held"] == 1
    assert raw["period_count"] == 2


def test_equal_concepts_share_only_business_equivalent_metadata():
    raw, source, resolver, observed = current("0")
    rows, _ = adapter.current_statement(raw, source=source, resolver=resolver, observed_at=observed)
    a = rows["dwd_financial_concept"][0]
    b = dict(a, source_record_id="other-original", source_artifact_sha256="sha256:" + "b" * 64)
    chosen, proof = adapter.merge_equal_concepts([a, b])
    assert len(chosen) == 1 and len(proof["records"][0]["source_rows_sha256"]) == 2
    assert adapter.merge_equal_concepts([b, a]) == (chosen, proof)
    b["concept_name"] = "conflicting business concept"
    with pytest.raises(RuntimeError, match="BUSINESS_CONFLICT"):
        adapter.merge_equal_concepts([a, b])


def test_date_only_publication_does_not_become_invented_midnight():
    raw, source, resolver, observed = current("0")
    rows, proof = adapter.current_statement(
        raw, source=source, resolver=resolver, observed_at=observed
    )
    assert rows["dwd_financial_report"][0]["published_at"] is None
    assert proof["scalar_classifications"]["PUBLICATION_DATE_ONLY_INSTANT_UNKNOWN"] > 0
    body = json.loads(gzip.decompress(ast.literal_eval(raw["raw_payload_gzip"].decode())))
    body["result"]["data"]["report_list"]["20260630"]["publish_date"] = "20261015"
    encode(raw, body)
    with pytest.raises(RuntimeError, match="PUBLICATION_AFTER_CAPTURE"):
        adapter.current_statement(raw, source=source, resolver=resolver, observed_at=observed)


def test_new_wrapper_round_trips_complete_json_without_backdating_capture():
    raw, _, _, _ = current("0")
    payload = gzip.decompress(ast.literal_eval(raw["raw_payload_gzip"].decode()))
    body = json.loads(payload)
    body["result"]["status"] = {"code": "0"}
    payload = json.dumps(body).encode()
    receipt = dict(
        code="000001",
        provider_source="fzb",
        URI="https://quotes.sina.cn/cn/api/openapi.php/"
        "CompanyFinanceService.getFinanceReport2022?paperCode=sz000001&source=fzb&"
        "type=0&page=1&num=12",
        completed_at="2026-10-14T09:00:00.123456+08:00",
        http_status="200",
        curl_exit=0,
        body_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
    )
    wrapped = adapter.wrap_current_envelope(payload, receipt)
    assert gzip.decompress(ast.literal_eval(wrapped["raw_payload_gzip"].decode())) == payload
    assert wrapped["market_date"] == "2026-10-14"
    assert wrapped["acquired_at"] == receipt["completed_at"]
    receipt["code"] = "000002"
    with pytest.raises(RuntimeError, match="REQUEST_COORDINATE"):
        adapter.wrap_current_envelope(payload, receipt)
