"""Domestic scope is explicit, and never hides another current gross claim."""

from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_domestic_scope import (
    parse_domestic_scope_cash,
)

BODY = (
    "证券代码:600001\n一、分配方案\n"
    "分派对象为本公司全体A股股东。"
    "本次利润分配以方案实施前总股本为基数，每股派发现金红利人民币1.2345678901元(含税)。\n"
    "二、境外安排\nH股股东另行发放港币，不适用本公告的A股安排。\n"
    "三、日期\n除息日:2026年8月3日"
)


def parse(body=BODY):
    return parse_domestic_scope_cash(
        body, stock_code="600001", ex_date=date(2026, 8, 3), publisher_title="年度利润分配实施公告"
    )


def test_explicit_current_domestic_gross_has_exact_currency_precision_and_separate_foreign_scope():
    atom = parse()
    assert atom["cash_per_share"] == Decimal("1.2345678901")
    assert atom["currency"] == "CNY"
    assert atom["domestic_scope_evidence"]["other_classes"] == "NOT_GRANTED"
    assert atom["historical_publication_or_acquisition_inferred"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "no_A",
        "currency_missing",
        "scheme_foreign",
        "conflicting_current",
        "nonuniform",
        "wrong_code",
        "wrong_date",
        "net_only",
        "precision",
    ],
)
def test_class_issuer_date_unit_or_conflicting_scope_remains_fail_closed(defect):
    body = BODY
    if defect == "no_A":
        body = body.replace("全体A股股东", "全体股东")
    elif defect == "currency_missing":
        body = body.replace("人民币1", "1")
    elif defect == "scheme_foreign":
        body = body.replace("人民币1", "美元1")
    elif defect == "conflicting_current":
        body = body.replace("二、境外安排", "每股派发现金红利人民币2元(含税)。\n二、境外安排")
    elif defect == "nonuniform":
        body = body.replace("本次利润分配", "本次差异化分红本次利润分配")
    elif defect == "wrong_code":
        body = body.replace("600001", "600002")
    elif defect == "wrong_date":
        body = body.replace("8月3日", "8月4日")
    elif defect == "net_only":
        body = body.replace("含税", "税后")
    else:
        body = body.replace("1.2345678901", "1.234567890123")
    with pytest.raises(BoundedError):
        parse(body)


def test_old_parser_keeps_mixed_class_fail_closed_and_cannot_admit_with_publisher_title_alone():
    from quanthus.pipelines.securities_warehouse.base22_notice_date_layout import (
        parse_exdate_layout_cash,
    )

    with pytest.raises(BoundedError, match="MIXED"):
        parse_exdate_layout_cash(
            BODY,
            stock_code="600001",
            ex_date=date(2026, 8, 3),
            publisher_title="年度利润分配实施公告",
        )
