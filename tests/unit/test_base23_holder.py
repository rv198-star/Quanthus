from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse import base23_holder as adapter
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
)


def case(token="1.1234567890123"):
    raw = dict.fromkeys(adapter.RAW_FIELDS, "")
    raw.update(code="000001", source="eastmoney_rpt_holdernum_det",
               provider_dataset="shareholder-count", end_date="2026-07-23",
               avg_hold_num=token, shareholder_count="1000", revision_retained=False,
               lookback_days=400, acquired_at="2026-09-05T10:00:00.123456+08:00",
               provider_acquired_at="2026-09-05T10:00:01+08:00", hold_notice_date="2026-07-24")
    resolver = FinancialCompanyResolver([
        FinancialCompanyIdentity("000001", "security-1", "company-1",
                                 datetime(1991, 1, 1, tzinfo=timezone.utc), None)
    ])
    return raw, resolver, datetime(2026, 10, 9, tzinfo=timezone.utc)


@pytest.mark.parametrize("token", ["1.1234567890123", "0.00000", None, ""])
def test_new_observation_exact13_keeps_original_token_and_does_not_infer_publication(token):
    raw, resolver, observed = case(token)
    rows, proof = adapter.holder_observation(raw, source_sha256="sha256:" + "a" * 64,
                                            revision="retained-v1", resolver=resolver,
                                            observed_at=observed)
    row = rows[adapter.TABLE][0]
    assert row["raw_average_holding"] == token
    assert row["average_holding_exact"] == (Decimal(token) if token not in (None, "") else None)
    if token == "1.1234567890123":
        assert row["average_holding"] is None
    assert row["knowledge_from"] == observed and row["published_at"] is None
    assert proof["provider_clock_order"] == "UNPROVEN"
    assert not proof["published_at_inferred"] and not proof["provider_PIT_qualified"]


@pytest.mark.parametrize("field,value", [("avg_hold_num", "1.12345678901234"),
                                         ("avg_hold_num", "NaN"),
                                         ("shareholder_count", "1.5"),
                                         ("shareholder_count", "-1"),
                                         ("provider_dataset", "unqualified"),
                                         ("acquired_at", "2026-10-10T00:00:00Z")])
def test_source_precision_and_time_invalidity_fail_without_changing_raw(field, value):
    raw, resolver, observed = case()
    raw[field] = value
    before = dict(raw)
    with pytest.raises(RuntimeError):
        adapter.holder_observation(raw, source_sha256="sha256:" + "a" * 64,
                                   revision="retained-v1", resolver=resolver, observed_at=observed)
    assert raw == before


def test_october_business_observation_is_accounted_and_not_emitted():
    raw, resolver, observed = case()
    raw["end_date"] = "2026-10-01"
    rows, proof = adapter.holder_observation(raw, source_sha256="sha256:" + "a" * 64,
                                            revision="retained-v1", resolver=resolver,
                                            observed_at=observed)
    assert not rows[adapter.TABLE] and proof["held_reason"] == "AFTER_IMAGE_CUTOFF"


def test_same_provider_count_selection_preserves_latest_real_capture_and_equal_clock_conflict():
    a, _, _ = case()
    b = dict(a, acquired_at="2026-09-06T00:00:00Z", avg_hold_num="2")
    sources = [dict(sha256="sha256:" + c * 64, release=c, rows=1) for c in "ab"]
    observations = [(a, sources[0], 0), (b, sources[1], 0)]
    latest = adapter.latest_holder_captures(observations)
    assert latest == adapter.latest_holder_captures(reversed(observations))
    assert latest[0][0] == b
    b["acquired_at"] = a["acquired_at"]
    with pytest.raises(RuntimeError, match="EQUAL_CLOCK_BODY_CONFLICT"):
        adapter.latest_holder_captures(observations)
