from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base23_financial_native as adapter
from quanthus.pipelines.securities_warehouse.financial_reporting import (
    FinancialCompanyIdentity,
    FinancialCompanyResolver,
    FinancialReportingSource,
)


def case():
    raw = dict(security_id="000001", stock_code="000001", report_date="20260630",
               publish_date="20260901", update_time="2026-09-01", revenue="1.00000000001",
               net_profit="-0.00000", parent_net_profit=None, currency="CNY", is_audit="",
               audit_opinion="", source=adapter.NAMESPACE, source_revision="provider-v1",
               acquired_at="2026-09-02T01:27:12.123456+08:00")
    source = FinancialReportingSource(
        "weekly_full_financial_quarterly", "quarterly_projection", "frozen",
        Path("/must-not-open-source"), "artifacts/financial_quarterly.parquet",
        "sha256:" + "a" * 64, "retained-release", "parent",
    )
    resolver = FinancialCompanyResolver([
        FinancialCompanyIdentity("000001", "security-1", "company-1",
                                 datetime(1991, 1, 1, tzinfo=timezone.utc), None)
    ])
    return raw, source, resolver, datetime(2026, 10, 9, tzinfo=timezone.utc)


def test_exact_quarterly_keeps_tokens_and_actual_clock_without_date_only_publication():
    raw, source, resolver, observed = case()
    rows, proof = adapter.quarterly_observation(raw, source=source, resolver=resolver,
                                               observed_at=observed)
    a, b, c = rows[adapter.TABLES[2]]
    assert a["value_decimal"] is None and a["value_text"] == raw["revenue"]
    assert b["value_decimal"] == Decimal(raw["net_profit"])
    assert c["is_nil"] and c["value_text"] is None
    assert all(r["knowledge_from"] == observed for rs in rows.values() for r in rs)
    assert all(r["published_at"] is None for r in rows[adapter.TABLES[0]])
    assert proof["actual_acquisition_instant"].microsecond == 123456
    assert not proof["provider_PIT_qualified"]


@pytest.mark.parametrize("field,value", [("currency", None), ("currency", ""),
                                         ("source", "another-provider"),
                                         ("security_id", "600001"),
                                         ("acquired_at", "2026-10-10T00:00:00Z"),
                                         ("publish_date", "2026-09-03T00:00:00Z")])
def test_unqualified_unit_source_or_time_fails(field, value):
    raw, source, resolver, observed = case()
    raw[field] = value
    with pytest.raises(RuntimeError):
        adapter.quarterly_observation(raw, source=source, resolver=resolver,
                                      observed_at=observed)


def test_future_business_period_is_accounted_without_an_output():
    raw, source, resolver, observed = case()
    raw["report_date"] = "20261001"
    rows, proof = adapter.quarterly_observation(raw, source=source, resolver=resolver,
                                               observed_at=observed)
    assert not any(rows.values())
    assert proof["quality_exceptions"][0]["code"] == "AFTER_THROUGH_DATE"


def test_abstract_keeps_reversed_real_clocks_and_does_not_infer_currency_or_publication():
    raw = dict.fromkeys(adapter.ABSTRACT_FIELDS, "")
    raw.update(stock_code="000001", provider_dataset="financial-disclosures",
               acquired_at="2026-09-02T19:44:46+08:00",
               provider_acquired_at="2026-09-02T19:44:53+08:00",
               publish_deadline="2026-09-02", report_date="2026-06-30T00:00:00")
    proof = adapter.abstract_disposition(raw, observed_at=case()[3])
    assert "PROVIDER_AND_COLLECTOR_CLOCK_ORDER_UNPROVEN" in proof["reasons"]
    assert proof["raw_provider_acquired_at"] == raw["provider_acquired_at"]
    assert proof["typed_outputs"] == 0 and proof["published_at"] is None
    assert not proof["publish_deadline_is_publication"]


def test_latest_quarterly_capture_is_order_independent_and_equal_clock_conflicts_fail():
    a, _, _, _ = case()
    b = dict(a, acquired_at="2026-09-05T00:00:00Z", revenue="2")
    sources = [dict(sha256="sha256:" + c * 64, rows=1, release=c) for c in "ab"]
    observations = [(a, sources[0], 0), (b, sources[1], 0)]
    selected = adapter.latest_quarterly_captures(observations)
    assert selected == adapter.latest_quarterly_captures(reversed(observations))
    assert selected[0][0] == b
    b["acquired_at"] = a["acquired_at"]
    with pytest.raises(RuntimeError, match="EQUAL_CLOCK_BODY_CONFLICT"):
        adapter.latest_quarterly_captures(observations)
