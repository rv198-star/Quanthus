from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_equivalent_scope import (
    parse_equivalent_scope_cash,
)
from tests.unit.test_base22_notice_scheme import body


def parse(text):
    return parse_equivalent_scope_cash(
        text,
        stock_code="002001",
        ex_date=date(2026, 7, 10),
        publisher_title="2025年度权益分派实施公告",
    )


@pytest.mark.parametrize(
    "declaration",
    [
        "公司2025年度及2026年第一季度权益分派方案为",
        "公司2025年度、2026年第一次中期权益分配方案为",
    ],
)
def test_equivalent_period_words_do_not_add_periods_or_combine_cash(declaration):
    text = body().replace("本公司2025年度权益分派方案为", declaration)
    result = parse(text)
    assert result["cash_per_share"] == Decimal("0.1234567")
    assert result["original_text_sha256"] == digest(text)


def test_explicit_scheme_implementation_adjustment_is_literal_not_computed():
    text = body().replace(
        "三、股权",
        "公司2025年度权益分派方案的实施调整为：每10股派3.456789元人民币现金（含税）；\n三、股权",
    )
    assert parse(text)["cash_per_share"] == Decimal("0.3456789")


def test_identical_printed_code_values_require_original_label_and_whitespace():
    text = body().replace("证券代码：002001", "证券代码：002001   002001")
    result = parse(text)
    assert result["cash_per_share"] == Decimal("0.1234567")
    assert result["equivalent_scope_evidence"]["identical_issuer_repetitions"] == 1


@pytest.mark.parametrize("header", ["002001 002001", "证券代码：002001 002002"])
def test_unlabeled_or_different_repeated_issuer_is_not_repaired(header):
    with pytest.raises(BoundedError):
        parse(body().replace("证券代码：002001", header))


def test_reflowed_exchange_table_preserves_all_four_columns_and_dates():
    text = body().replace(
        "除权除息日：2026年7月10日",
        "现金红利\n股份类别 股权登记日 最后交易日 除权（息）日\n发放日\n"
        "Ａ股 2026/7/9 － 2026/7/10 2026/7/10",
    )
    result = parse(text)
    assert result["cash_per_share"] == Decimal("0.1234567")
    assert result["ex_date"] == date(2026, 7, 10)


def test_partial_or_conflicting_date_table_remains_blocked():
    text = body().replace(
        "除权除息日：2026年7月10日",
        "现金红利 股份类别 股权登记日 除权（息）日 发放日\nＡ股 2026/7/9 － 2026/8/3 2026/8/3",
    )
    with pytest.raises(BoundedError):
        parse(text)
