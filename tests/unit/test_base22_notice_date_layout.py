from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_date_layout import (
    parse_exdate_layout_cash,
)
from tests.unit.test_base22_notice_scheme import body


def parse(text):
    return parse_exdate_layout_cash(
        text,
        stock_code="002001",
        ex_date=date(2026, 7, 10),
        publisher_title="2025年度权益分派实施公告",
    )


@pytest.mark.parametrize(
    "replacement",
    [
        "除权( 除息 ) 日：2026年7月10日",
        "除权(息) 现金红利发\n股份类别 股权登记日 最后交易日 日 放日\n"
        "A股 2026/7/9 - 2026/7/10 2026/7/10",
    ],
)
def test_complete_labels_preserve_explicit_date_and_exact_cash(replacement):
    text = body().replace("除权除息日：2026年7月10日", replacement)
    atom = parse(text)
    assert atom["cash_per_share"] == Decimal("0.1234567")
    assert atom["ex_date"] == date(2026, 7, 10)
    assert atom["original_text_sha256"] == digest(text)
    assert atom["date_layout_evidence"]["numbers_dates_columns_inferred"] is False


@pytest.mark.parametrize("defect", ["missing-label", "missing-column", "wrong-date", "second-date"])
def test_no_nearby_date_or_incomplete_or_conflicting_table_is_admitted(defect):
    replacement = (
        "除权(息) 现金红利发\n股份类别 股权登记日 最后交易日 日 放日\n"
        "A股 2026/7/9 - 2026/7/10 2026/7/10"
    )
    if defect == "missing-label":
        replacement = replacement.replace("除权(息)", "未知")
    elif defect == "missing-column":
        replacement = replacement.replace("最后交易日", "")
    elif defect == "wrong-date":
        replacement = replacement.replace("2026/7/10", "2026/8/3")
    else:
        replacement += "\n除权( 除息 ) 日：2026年8月3日"
    with pytest.raises(BoundedError):
        parse(body().replace("除权除息日：2026年7月10日", replacement))


@pytest.mark.parametrize("label", ["现金股息 人民币", "现\n金 股 息 人民币", "现金 分 红 人民币"])
def test_whitespace_in_literal_gross_cash_label_preserves_exact_value(label):
    text = body(f"每10股派发{label}1.234567元（含税）")
    atom = parse(text)
    assert atom["cash_per_share"] == Decimal("0.1234567")
    assert atom["original_text_sha256"] == digest(text)
    assert atom["date_layout_evidence"]["numbers_dates_columns_inferred"] is False


@pytest.mark.parametrize("defect", ["net-only", "other-currency", "conflicting-value"])
def test_cash_label_reflow_does_not_supply_gross_currency_or_priority(defect):
    text = body("每10股派发现\n金 股 息 人民币1.234567元（含税）")
    if defect == "net-only":
        text = text.replace("含税", "税后")
    elif defect == "other-currency":
        text = text.replace("人民币", "美元")
    else:
        text = body(
            "每10股派发现\n金 股 息 人民币1.234567元（含税）"
            "及每10股派发现金 股 息 人民币2.0元（含税）。"
        )
    with pytest.raises(BoundedError):
        parse(text)
