import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base231_financial_summary as adapter
from tests.unit.test_base22_f10_ownership import resolve
from tests.unit.test_base23_ownership_reconfirmation import fixture

BODY = (
    "【主要财务指标】\n┌────┐\n"
    "｜财务指标｜2026-06-30｜2026-03-31｜\n├────┤\n"
    "｜净利润(元)｜1.23456789012万｜-0.00000｜\n"
    "｜加权净资产收益率(%)｜1.234567890123｜-｜\n"
    "｜经营活动现金净流量增长｜2.5｜P｜\n"
    "｜率(%)｜｜｜\n└────┘\n【盈利能力指标】\n"
)


def project(body=BODY, **updates):
    raw = fixture()
    raw.update(stock_code="002001", security_id="002001", category="财务分析",
               market_date="2026-09-05", acquired_at="2026-09-05T01:02:03.123456+08:00",
               content=body, content_sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest())
    raw.update(updates)
    return adapter.summary_observation(
        raw, source_sha256="sha256:" + "a" * 64, revision="selected-source",
        resolver=resolve(), observed_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
        contract=adapter.load_contract(Path("config/pipelines/base231-domains.v1.json")),
    )


def test_exact_literals_units_symbols_and_real_governance_time():
    rows, proof = project()
    facts = rows["dwd_financial_fact"]
    assert len(facts) == 6
    assert facts[0]["unit_code"] == "SOURCE_LITERAL_YUAN"
    assert facts[0]["value_decimal"] == Decimal("12345.6789012")
    assert facts[1]["value_decimal"] == Decimal("-0.00000")
    assert facts[2]["value_decimal"] is None
    assert facts[2]["value_text"] == "1.234567890123"
    assert not facts[3]["is_nil"] and facts[3]["value_text"] == "-"
    assert facts[5]["value_text"] == "P" and not facts[5]["is_nil"]
    assert all(r["published_at"] is None for r in facts)
    assert all(r["knowledge_from"] == datetime(2026, 10, 12, tzinfo=timezone.utc) for r in facts)
    assert proof["raw_acquired_at"] == "2026-09-05T01:02:03.123456+08:00"
    assert not proof["currency_CNY_inferred"] and not proof["provider_PIT_qualified"]


def test_source_spacer_rows_have_exact_width_and_no_fabricated_fact():
    rows, proof = project(BODY.replace("├────┤", "├────┤\n｜｜｜｜"))
    assert len(rows["dwd_financial_fact"]) == 6 and not proof["held_frames"]
    rows, proof = project(BODY.replace("├────┤", "├────┤\n｜｜｜"))
    assert not rows["dwd_financial_fact"]
    assert proof["held_frames"][0]["reason"] == "FINANCIAL_SUMMARY_BLANK_ROW_WIDTH_INVALID"


def test_audit_opinion_continuation_is_accounted_without_becoming_numeric_fact():
    body = BODY.replace("├────┤", "├────┤\n｜审计意见｜标准无保留｜标准无保留｜\n｜｜意见｜意见｜")
    rows, proof = project(body)
    assert len(rows["dwd_financial_fact"]) == 6 and not proof["held_frames"]
    assert proof["unsupported_cells"] == 2
    body = BODY.replace("├────┤", "├────┤\n｜净利润(元)｜1｜2｜\n｜｜3｜4｜")
    rows, proof = project(body)
    assert not rows["dwd_financial_fact"] and proof["held_frames"]


def test_truncated_frame_is_held_without_losing_another_complete_frame():
    body = BODY.replace("【盈利能力指标】", "") + (
        "┌────┐\n｜财务指标｜2025-12-31｜\n｜净利润(元)｜2｜\n"
    )
    rows, proof = project(body)
    assert len(rows["dwd_financial_fact"]) == 6
    assert proof["held_frames"][0]["reason"] == "FINANCIAL_SUMMARY_FRAME_INCOMPLETE"


def test_same_period_field_conflict_does_not_choose_first():
    body = BODY.replace("【盈利能力指标】", "") + (
        "┌────┐\n｜财务指标｜2026-06-30｜\n｜净利润(元)｜2｜\n└────┘\n"
    )
    rows, proof = project(body)
    assert len(rows["dwd_financial_fact"]) == 5
    assert proof["conflicting_coordinates"] == 1


def test_unknown_nonempty_metric_is_accounted_not_assigned_a_unit():
    rows, proof = project(BODY.replace("└────┘", "｜未经定义指标｜123｜4｜\n└────┘"))
    assert len(rows["dwd_financial_fact"]) == 6
    assert proof["unsupported_cells"] == 2


def test_after_september_business_period_does_not_enter_output():
    rows, proof = project(BODY.replace("2026-06-30", "2026-10-01"))
    assert len(rows["dwd_financial_fact"]) == 3
    assert proof["excluded_after_cutoff_cells"] == 3


@pytest.mark.parametrize("updates", [
    {"content_sha256": "sha256:" + "b" * 64},
    {"acquired_at": "2026-10-13T00:00:00Z"},
    {"acquired_at": "2026-09-05T01:02:03"},
    {"market_date": "2026-10-01"},
    {"source": "unqualified-source"},
])
def test_invalid_source_hash_clock_and_capture_fail_closed(updates):
    with pytest.raises(RuntimeError):
        project(**updates)


def test_duplicate_date_headers_do_not_create_duplicate_keys():
    rows, proof = project(BODY.replace("2026-03-31", "2026-06-30"))
    assert not rows["dwd_financial_fact"]
    assert proof["held_frames"][0]["reason"] == "FINANCIAL_SUMMARY_DATE_HEADER_INVALID"
