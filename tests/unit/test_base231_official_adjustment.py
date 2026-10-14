from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base231_official_adjustment import parse_adjustment

BODY = """证券代码003018权益分派实施公告 公司现有总股本100股
二、本次实施的利润分配及资本公积金转增股本方案
向全体股东每10股派2.50元人民币现金（含税），每10股转增2.25股，不送红股
三、股权登记日与除权除息日
股权登记日为：2026年7月14日，除权除息日为：2026年7月15日
本次权益分派实施后的除权除息参考价＝（除权除息日前一交易日收盘价-0.2498765元/股）/（1+0.2249876股）。
"""


def parse(body=BODY):
    return parse_adjustment(body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15))


def test_explicit_adjustment_coefficients_remain_distinct_from_eligible_payouts():
    row, proof = parse()
    assert row["cash_per_share"] == Decimal("0.2498765")
    assert row["transfer_shares"] == Decimal("0.2249876")
    assert not proof["coefficients_equal_eligible_distribution"]
    assert proof["basis"] == "ALL_ISSUED_PRE_EVENT_SHARES"
    assert proof["derived_price_factor_admission"] == "NOT_GRANTED"
    assert not proof["prior_values_overwritten"]


def test_repeated_identical_expression_reconfirms_without_extra_action():
    row, _ = parse(BODY + BODY[BODY.index("本次权益分派实施后") :])
    assert row["transfer_shares"] == Decimal("0.2249876")


@pytest.mark.parametrize("fault", ["formula", "denominator", "conflict", "precision", "zero"])
def test_no_guess_or_payout_fallback_for_unqualified_adjustment(fault):
    body = BODY
    if fault == "formula":
        body = body.replace("除权除息参考价", "预案讨论")
    elif fault == "denominator":
        body = body.replace("公司现有总股本100股", "资格未知")
    elif fault == "conflict":
        body += "除权除息参考价=（除权除息日前收盘价-0.1元）÷（1+0.2）"
    elif fault == "precision":
        body = body.replace("0.2249876", "0.12345678901")
    else:
        body = body.replace(
            "（除权除息日前一交易日收盘价-0.2498765元/股）/（1+0.2249876股）",
            "股权登记日股票收盘价÷（1+0.2249876）",
        )
    with pytest.raises(RuntimeError):
        parse(body)


def test_explicit_non_cash_all_share_formula_preserves_zero():
    body = BODY.replace("每10股派2.50元人民币现金（含税）", "不派发现金红利")
    body = body.replace(
        "（除权除息日前一交易日收盘价-0.2498765元/股）/（1+0.2249876股）",
        "股权登记日股票收盘价÷（1+0.2249876）",
    )
    row, _ = parse(body)
    assert row["cash_per_share"] == 0


def test_explicit_sse_nondifferentiated_all_share_base():
    body = """688115上海证券交易所权益分派实施公告 是否涉及差异化分红送转：否
公司总股本100股为基数 每股分配比例每股现金红利0.12元 每股派送红股0.2股 相关日期
股权登记日 除权（息）日 现金红利发放日 新股上市日
2026/7/14 2026/7/15 2026/7/15 2026/7/15 一、股东会
"""
    row, proof = parse_adjustment(
        body, code="688115", venue="SSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["bonus_shares"] == Decimal("0.2")
    assert proof["coefficients_equal_eligible_distribution"]
    with pytest.raises(RuntimeError):
        parse_adjustment(
            body.replace("差异化分红送转：否", "差异化分红送转：是"),
            code="688115",
            venue="SSE",
            expected_ex_date=date(2026, 7, 15),
        )


def test_unknown_bonus_stays_unknown_despite_explicit_reference_formula():
    row, proof = parse(BODY.replace("不送红股", ""))
    assert row["bonus_shares"] is None
    assert proof["unknown_components"] == ["bonus_shares"]
    assert proof["derived_price_factor_admission"] == "NOT_GRANTED"


def test_materialization_preserves_exact_declared_publication_without_pit_inference():
    from datetime import datetime, timedelta, timezone

    from quanthus.pipelines.securities_warehouse.base231_official_adjustment import (
        materialize_adjustment,
    )

    row, _ = parse()
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    args = dict(
        announcement_id="123",
        observed_at=now,
        received_at=now,
        pdf_sha256="sha256:" + "a" * 64,
        company_id="c",
        security_id="s",
        vocabulary={
            "action_types": {"mixed": "MIXED"},
            "action_statuses": {"实施": "IMPLEMENTED"},
            "term_types": {
                "cash_per_share": "CASH_AMOUNT_PER_SHARE",
                "bonus_shares": "BONUS_SHARES_PER_SHARE",
                "transfer_shares": "TRANSFER_SHARES_PER_SHARE",
            },
        },
    )
    ms = 1784000000123
    output = materialize_adjustment(row, declared_publication_ms=ms, **args)
    expected = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)
    assert all(
        r["announced_at"] == expected and r["knowledge_from"] == now
        for rows in output.values()
        for r in rows
    )
    for bad in ["123", True, 9999999999999]:
        with pytest.raises(RuntimeError):
            materialize_adjustment(row, declared_publication_ms=bad, **args)
