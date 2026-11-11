from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base231_cash_plan import cash_plan_inputs
from quanthus.pipelines.securities_warehouse.base231_official_adjustment import parse_adjustment

BODY = """A股代码：601390 权益分派实施公告
每股分配比例
A股每股现金红利0.086元（含税）
相关日期
股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日
Ａ股 2026/7/30 － 2026/7/31 2026/7/31
差异化分红送转：否
一、通过分配方案的股东会
上海证券交易所
二、分配方案
3.分配方案：本次利润分配以方案实施前的公司总股本100股为基数，
每股派发现金红利0.086元（含税），共计派发现金红利8.6元（含税）。
三、相关日期
股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日
Ａ股 2026/7/30 － 2026/7/31 2026/7/31
四、分配实施办法
"""
RULE = """4.3.2除权（息）参考价格的计算公式为：
除权（息）参考价格＝[（前收盘价格-现金红利）+配股价格×流通股份变动比例]/（1+流通股份变动比例）。
4.3.3除权（息）日证券买卖按参考价作为基准。"""


def call(body=BODY, rule=RULE):
    record, proof = parse_adjustment(
        body, code="601390", venue="SSE", expected_ex_date=date(2026, 7, 31)
    )
    return cash_plan_inputs(
        record,
        proof,
        official_body=body,
        rule_body=rule,
        rule_member_sha256="sha256:" + "a" * 64,
        rule_URI="https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/"
        "universal/c/10816492/files/58bd5ce4dfc94b57a5391f60eb2719ab.docx",
        rule_effective_date=date(2026, 7, 6),
    )


def test_complete_cash_plan_supplies_combined_zero_without_filling_component_nulls():
    result = call()
    assert result["combined_share_increment"] == Decimal(0)
    assert result["unknown_components"] == ["bonus_shares", "transfer_shares"]
    assert result["component_nulls_preserved"]
    assert result["complete_cash_scheme_source_qualified"]


@pytest.mark.parametrize("fault", ["mixed", "cash", "extra", "rule", "differential"])
def test_partial_scheme_conflicting_cash_or_unsupported_rule_is_held(fault):
    body, rule = BODY, RULE
    if fault == "mixed":
        body = body.replace("共计派发", "每股转增1股，共计派发")
    elif fault == "cash":
        body = body.replace("8.6元", "9.6元")
    elif fault == "extra":
        body = body.replace("三、相关日期", "另有股份变动待定。\n三、相关日期")
    elif fault == "rule":
        rule = RULE.replace("现金红利", "其他值")
    else:
        body = body.replace("分红送转：否", "分红送转：是")
    with pytest.raises(RuntimeError):
        call(body, rule)
