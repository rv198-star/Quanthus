from __future__ import annotations

import ast
import gzip
import hashlib
import json
from unittest.mock import patch

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base23_financial_reconfirmation import (
    declared_statement_identity,
    statement_fingerprint,
)
from quanthus.pipelines.securities_warehouse.financial_reporting import FinancialReportingBuildError


def fixture(value="1.00000000001"):
    body = json.dumps({"result": {"data": {"report_list": {
        "20260630": {"publish_date": "2026-08-31", "data": [
            {"item_field": "F", "item_title": "Fact", "item_value": value}
        ]}
    }}}}).encode()
    return {
        "security_id": "000001", "stock_code": "000001", "statement_type": "balance_sheet",
        "provider_source": "fixture-provider", "requested_periods": 3, "period_count": 1,
        "earliest_report_date": "2026-06-30", "latest_report_date": "2026-06-30",
        "latest_publish_date": "2026-08-31", "latest_update_time": "2026-08-31T13:00:00+08:00",
        "raw_payload_gzip": repr(gzip.compress(body, mtime=1)).encode(),
        "raw_payload_sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
        "raw_payload_bytes": len(body), "market_date": "2026-09-01",
        "source": "fixture-source", "source_revision": "fixture-v1",
        "acquired_at": "2026-09-01T13:00:00.123456+08:00",
    }


def test_capture_only_reconfirmation_keeps_clocks_and_precision():
    before = fixture()
    after = dict(before, market_date="2026-09-05", acquired_at="2026-09-05T13:00:00+08:00")
    body = gzip.decompress(ast.literal_eval(before["raw_payload_gzip"].decode("ascii")))
    after["raw_payload_gzip"] = repr(gzip.compress(body, mtime=2)).encode()
    a, b = statement_fingerprint(before), statement_fingerprint(after)
    assert a["complete_envelope_fingerprint"] == b["complete_envelope_fingerprint"]
    assert a["compressed_representation_sha256"] != b["compressed_representation_sha256"]
    assert a["report_body_sha256s"] == b["report_body_sha256s"]
    assert a["acquisition_instant"].microsecond == 123456
    assert a["raw_acquired_at"] != b["raw_acquired_at"]
    assert not b["native_admission_asserted"] and not b["provider_availability_asserted"]


@pytest.mark.parametrize("field,value", [
    ("provider_source", "other-provider"), ("source_revision", "fixture-v2"),
    ("latest_publish_date", "2026-09-01"), ("latest_update_time", "2026-09-01T00:00:00+08:00"),
    ("requested_periods", 4),
])
def test_business_or_source_metadata_changes_are_not_ignored(field, value):
    raw = fixture()
    assert (statement_fingerprint(raw)["complete_envelope_fingerprint"]
            != statement_fingerprint(dict(raw, **{field: value}))["complete_envelope_fingerprint"])


def test_precision_change_changes_body_identity():
    a, b = statement_fingerprint(fixture()), statement_fingerprint(fixture("1.00000000000"))
    assert a["report_body_sha256s"] != b["report_body_sha256s"]


def test_baseline_metadata_reuse_is_not_an_incoming_raw_validation():
    raw = fixture()
    with patch("quanthus.pipelines.securities_warehouse.base23_financial_reconfirmation."
               "decode_financial_envelope", side_effect=AssertionError("redundant decode")):
        declared = declared_statement_identity(raw)
    checked = statement_fingerprint(raw)
    assert declared["complete_envelope_fingerprint"] == checked["complete_envelope_fingerprint"]
    assert not declared["complete_envelope_bytes_verified_here"]
    assert checked["complete_envelope_bytes_verified_here"]
    assert not declared["native_admission_asserted"]


def test_corrupt_bytes_cannot_reuse_declared_body_hash():
    raw = fixture()
    raw["raw_payload_gzip"] = fixture("2")["raw_payload_gzip"]
    with pytest.raises(FinancialReportingBuildError):
        statement_fingerprint(raw)


@pytest.mark.parametrize("change,reason", [
    ({"unexpected_field": "must not ignore"}, "EXACT_SCHEMA"),
    ({"security_id": "000002"}, "SOURCE_IDENTITY"),
    ({"period_count": 2}, "PERIOD_CENSUS"),
    ({"market_date": "2026-10-01"}, "AFTER_THROUGH"),
    ({"acquired_at": "2026-09-01T13:00:00.1234567+08:00"}, "CLOCK_PRECISION"),
])
def test_fail_closed_contract(change, reason):
    with pytest.raises(BoundedError, match=reason):
        statement_fingerprint(dict(fixture(), **change))
