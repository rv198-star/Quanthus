from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_nonuniform_facts import (
    parse_nonuniform_facts,
)

TARGET = {"action_id": "a", "stock_code": "600001", "ex_date": "2026-08-03"}
NAMES = "中国华能集团有限公司、云南省能源投资集团有限公司、云南合和(集团)股份有限公司"
TEXT = (
    "证券代码:600001\n重要内容提示:社会公众股每股实际分派现金红利0.175元;"
    "上市前原三家大股东" + NAMES + "每股实际分派现金红利0.12675元。\n"
    "一、通过分配方案的股东大会届次和日期\n方案已批准。\n"
    "二、分配方案\n差异化分红送转方案。上市前原三家股东捐赠转送后,"
    "社会公众股实际每股分配金额为0.175元;原三家实际每股分配金额为0.12675元。\n"
    "三、相关日期\n股份类别 股权登记日 最后交易日 除权(息)日 现金红利发放日\n"
    "A股 2026/7/31 - 2026/8/3 2026/8/3"
)


def doc(text=TEXT):
    return {
        "document_id": "123",
        "publisher_security_code": "600001",
        "publisher_title": "年度权益分派实施公告",
        "text": text,
    }


def test_literal_named_source_classes_do_not_infer_membership_or_gross_tax_basis():
    source = doc()
    frozen = deepcopy(source)
    result = parse_nonuniform_facts(source, TARGET)
    assert source == frozen
    assert len(result["facts"]) == 2
    assert [r["literal_numeric_token"] for r in result["facts"]] == ["0.175", "0.12675"]
    assert all(r["tax_basis"] == "SOURCE_NOT_EXPLICITLY_DECLARED" for r in result["facts"])
    assert result["canonical_DWD_repair_qualified"] is False
    assert result["actual_holder_membership_inferred"] is False
    assert result["consumer_admission"] == "NOT_GRANTED"


@pytest.mark.parametrize(
    "change", ["header", "publisher", "date", "class", "name", "primary", "foreign"]
)
def test_missing_literal_identity_complete_named_scope_or_date_is_not_repaired(change):
    d = doc()
    if change == "publisher":
        d["publisher_security_code"] = "600002"
    elif change == "primary":
        d["publisher_title"] = "利润分配预案公告"
    else:
        a, b = {
            "header": ("600001", "600002"),
            "date": ("2026/8/3", "2026/8/4"),
            "class": ("社会公众股", "部分股东"),
            "name": (NAMES, "未知三家股东"),
            "foreign": ("现金红利0.175元", "现金红利0.175美元"),
        }[change]
        d["text"] = d["text"].replace(a, b)
    with pytest.raises(BoundedError):
        parse_nonuniform_facts(d, TARGET)


def test_mixed_cash_and_transfer_retain_original_per_ten_units_and_scope():
    text = (
        "证券代码:600001\n本公司2011年半年度权益分派方案为:\n"
        "1、原有股份扣除非公开发行新增股份,每10股派0.125元(含税)。\n"
        "2、以公司现有总股本为基数,向全体股东每10股派1元(含税),"
        "同时,以资本公积金向全体股东每10股转增10股。\n"
        "二、权益登记日与除权除息日\n除权除息日为2026年8月3日。"
    )
    result = parse_nonuniform_facts(doc(text), TARGET)
    assert len(result["facts"]) == 3
    assert result["canonical_DWD_repair_qualified"] is False
    assert [r["literal_denominator"] for r in result["facts"]] == [10, 10, 10]
    assert result["facts"][-1]["literal_numeric_token"] == "10"
    for broken in [
        text.replace("转增10股", "转增股份未知"),
        text.replace("非公开发行", "来源未知"),
    ]:
        with pytest.raises(BoundedError):
            parse_nonuniform_facts(doc(broken), TARGET)


def test_literal_numbered_note_is_a_separate_printed_scope_recap_not_a_third_plan_item():
    text = (
        "证券代码:600001\n本公司2011年半年度权益分派方案为:\n"
        "1、原有股份扣除非公开发行新增股份,每10股派0.125元(含税)。\n"
        "2、以公司现有总股本为基数,向全体股东每10股派1元(含税),"
        "同时,以资本公积金向全体股东每10股转增10股。"
        "注:以上1、2项合计共向原文具名范围每10股转增10股派发现金0.5625元(含税)。\n"
        "二、权益登记日与除权除息日\n除权除息日为2026年8月3日。"
    )
    result = parse_nonuniform_facts(doc(text), TARGET)
    assert len(result["facts"]) == 4
    assert result["facts"][-1]["kind"] == "DECLARED_PRINTED_SCOPE_RECAP_CASH"
    assert result["facts"][-1]["literal_numeric_token"] == "0.5625"
    assert result["aggregate_computed"] is False
    assert result["canonical_DWD_repair_qualified"] is False
    with pytest.raises(BoundedError):
        parse_nonuniform_facts(doc(text.replace("以上1、2项合计共向", "未知来源事项")), TARGET)
