from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base231_event_factor import (
    factor_observation,
    reference_inputs,
)
from quanthus.pipelines.securities_warehouse.base231_official_adjustment import parse_adjustment

BODY = """证券代码003018权益分派实施公告 公司现有总股本100股
二、本次实施的利润分配及资本公积金转增股本方案
向全体股东每10股派2.50元人民币现金（含税），每10股转增2.25股
三、股权登记日与除权除息日
股权登记日为：2026年7月14日，除权除息日为：2026年7月15日
本次权益分派实施后的除权除息参考价＝（除权除息日前一交易日收盘价-0.2498765元/股）/（1+0.2249876股）。
"""
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def inputs():
    record, proof = parse_adjustment(
        BODY, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    return reference_inputs(record, proof)


def args():
    return dict(
        prior_close=Decimal("10.0000000000"),
        prior_business_date=date(2026, 7, 14),
        expected_prior_session=date(2026, 7, 14),
        price_basis="UNADJUSTED", currency="CNY", security_id="s", action_id="a",
        observed_at=NOW, source_known_at=NOW, price_known_at=NOW,
        source_sha256="sha256:" + "a" * 64, price_row_sha256="sha256:" + "b" * 64,
        price_member_sha256="sha256:" + "c" * 64,
    )


def test_literal_formula_is_complete_without_inventing_unknown_bonus():
    result = inputs()
    assert result["combined_share_increment"] == Decimal("0.2249876")
    assert result["cash_per_share"] == Decimal("0.2498765")
    assert result["unknown_components"] == ["bonus_shares"]
    assert result["component_nulls_preserved"]


def test_current_event_factor_has_separate_method_and_full_input_closure():
    rows, proof = factor_observation(inputs(), **args())
    factor = rows["dwd_adjustment_factor"][0]
    assert factor["adjustment_method"] == "OFFICIAL_EVENT_REFERENCE_PRICE_RATIO_V1"
    assert factor["knowledge_from"] == NOW
    assert factor["factor_value"].as_tuple().exponent == -18
    assert len(rows["dwd_adjustment_factor_input"]) == 2
    assert proof["rounding"] == "ROUND_HALF_EVEN"
    assert proof["historical_PIT_admission"] == "NOT_GRANTED"
    assert proof["cumulative_qfq_hfq_admission"] == "NOT_GRANTED"
    assert proof["old_values_overwritten"] is False


@pytest.mark.parametrize("fault", ["basis", "currency", "prior", "future", "negative", "hash"])
def test_invalid_price_reference_fails_closed(fault):
    kw = args()
    if fault == "basis":
        kw["price_basis"] = "QFQ"
    elif fault == "currency":
        kw["currency"] = None
    elif fault == "prior":
        kw["prior_business_date"] = date(2026, 7, 13)
    elif fault == "future":
        kw["price_known_at"] = NOW.replace(day=15)
    elif fault == "negative":
        kw["prior_close"] = Decimal("0.1")
    else:
        kw["price_member_sha256"] = "unknown"
    with pytest.raises(RuntimeError):
        factor_observation(inputs(), **kw)


def test_terms_or_proof_changed_are_not_accepted():
    record, proof = parse_adjustment(
        BODY, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    record["cash_per_share"] = Decimal("999")
    with pytest.raises(RuntimeError):
        reference_inputs(record, proof)


def test_incomplete_nonformula_distribution_is_held():
    record, proof = parse_adjustment(
        BODY, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    proof["method"] = "EXPLICIT_ALL_ISSUED_IMPLEMENTATION_BASE"
    with pytest.raises(RuntimeError, match="INCOMPLETE"):
        reference_inputs(record, proof)


def test_decimal_result_does_not_depend_on_ambient_context():
    from decimal import localcontext

    a, _ = factor_observation(inputs(), **args())
    with localcontext() as context:
        context.prec = 6
        b, _ = factor_observation(inputs(), **args())
    assert a == b


def test_exact_issued_totals_supply_combined_ratio_without_zero_component():
    body = """688115上海证券交易所权益分派实施公告 是否涉及差异化分红送转：否
公司总股本100股为基数 每股分配比例每股现金红利0.12元 每股派送红股0.2股 相关日期
股权登记日 除权（息）日 现金红利发放日 新股上市日
2026/7/14 2026/7/15 2026/7/15 2026/7/15 一、股东会
方案实施前的公司总股本100股，本次分配后总股本为120股。
"""
    record, proof = parse_adjustment(
        body, code="688115", venue="SSE", expected_ex_date=date(2026, 7, 15)
    )
    result = reference_inputs(record, proof, official_body=body)
    assert result["combined_share_increment"] == Decimal("0.2")
    assert result["unknown_components"] == ["transfer_shares"]
    with pytest.raises(RuntimeError):
        reference_inputs(record, proof, official_body=body.replace("120股", "110股"))
