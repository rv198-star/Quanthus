from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base231_official_action import parse_distribution

BODY = """证券代码003018 2026权益分派实施公告
特别提示：除权参考价格每10股转增1.5股
一、原预案每10股转增9股
二、本次实施的利润分配及资本公积金转增股本方案
向全体股东每10股派2.50元人民币现金（含税），每10股转增2.25股，不送红股
三、股权登记日与除权除息日
股权登记日为：2026年7月14日，除权除息日为：2026年7月15日
"""


def test_distribution_uses_implemented_section_and_preserves_distinct_adjustment_basis():
    row, proof = parse_distribution(
        BODY, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["transfer_shares"] == Decimal("0.225")
    assert row["cash_per_share"] == Decimal("0.25")
    assert row["bonus_shares"] == 0
    assert row["announce_date"] is None
    assert proof["derived_price_factor_admission"] == "NOT_GRANTED"


@pytest.mark.parametrize("fault", ["section", "tax", "date", "conflict", "code", "precision"])
def test_unsupported_official_body_is_not_a_partial_success(fault):
    body = BODY
    if fault == "section":
        body = body.replace("二、本次实施", "二、拟实施")
    elif fault == "tax":
        body = body.replace("现金（含税", "现金（扣税后")
    elif fault == "date":
        body = body.replace("除权除息日为：2026年7月15日", "除权除息日为：2026年7月16日")
    elif fault == "conflict":
        body = body.replace("不送红股", "每10股转增3股，不送红股")
    elif fault == "code":
        body = body.replace("003018", "003019")
    else:
        body = body.replace("2.25股", "2.12345678901股")
    with pytest.raises(RuntimeError):
        parse_distribution(body, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15))


def test_sse_per_share_terms_are_not_divided_by_ten_again():
    body = """688115上海证券交易所年度权益分派实施公告
    每股分配比例每股现金红利0.067元 每股派送红股0.4股 相关日期
    股权登记日 除权（息）日 现金红利发放日 新股上市日
    2026/7/14 2026/7/15 2026/7/15 2026/7/15 一、股东会
    """
    row, _ = parse_distribution(
        body, code="688115", venue="SSE", expected_ex_date=date(2026, 7, 15)
    )
    assert row["cash_per_share"] == Decimal("0.067")
    assert row["bonus_shares"] == Decimal("0.4")


def test_per_ten_conversion_keeps_digits_beyond_ambient_precision():
    from quanthus.pipelines.securities_warehouse.base231_official_action import _per_ten

    value = _per_ten("1234567890123456789012345678.123456789")
    assert value == Decimal("123456789012345678901234567.8123456789")


def test_materialization_has_actual_new_knowledge_and_date_only_publication():
    from quanthus.pipelines.securities_warehouse.base231_official_action import (
        materialize_distribution,
    )

    row, _ = parse_distribution(
        BODY, code="003018", venue="SZSE", expected_ex_date=date(2026, 7, 15)
    )
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    args = dict(
        announcement_id="doc",
        observed_at=now,
        pdf_received_at=now,
        pdf_sha256="sha256:" + "a" * 64,
        company_id="company",
        security_id="security",
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
    output = materialize_distribution(row, **args)
    assert output["dwd_corporate_action"][0]["knowledge_from"] == now
    assert output["dwd_corporate_action"][0]["announced_at"] is None
    assert len(output["dwd_corporate_action_term"]) == 2
