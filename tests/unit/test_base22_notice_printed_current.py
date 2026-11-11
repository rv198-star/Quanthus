from copy import deepcopy
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_printed_current import (
    parse_printed_current_cash,
    select_printed_current_candidates,
)

TARGET = {"action_id": "a", "stock_code": "600001", "ex_date": "2026-08-03"}
TABLE = (
    "股份类别 股权登记日 最后交易日 除权(息)日 现金红利发放日\nA股 2026/7/31 - 2026/8/3 2026/8/3"
)
BODY = (
    "证券代码:600001\n重要内容提示:\nA股每股现金红利0.35元(含税),"
    "其中年度权益分派A股每股现金红利0.15元(含税),特别分红A股每股现金红利0.20元(含税)。\n"
    + TABLE
    + "\n一、通过分配方案的股东大会届次和日期\n方案已由股东大会审议通过。\n"
    "二、分配方案\n本公司全体A股股东。公司回购专用证券账户中的股份不参与本次利润分配。"
    "本次差异化分红方案以扣减回购专用证券账户中股份后的总股本为基数,"
    "向全体股东每股派发2025年度现金红利0.15元(含税);"
    "向全体股东每股派发2026年特别分红现金红利0.20元(含税),"
    "合计向全体股东每股派发现金红利0.35元(含税)。"
    "虚拟分派每股现金红利0.34元。\n三、相关日期\n" + TABLE
)


def doc(text=BODY):
    return {
        "document_id": "123",
        "target_action_ids": ["a"],
        "text": text,
        "publisher_security_code": "600001",
        "publisher_title": "A股权益分派实施公告",
    }


def test_original_combined_is_not_summed_or_replaced_by_diluted_rate():
    original = doc()
    frozen = deepcopy(original)
    atom = parse_printed_current_cash(original, TARGET)
    assert original == frozen
    assert atom["cash_per_share"] == Decimal("0.35")
    assert atom["aggregate_computed"] is False
    assert atom["printed_current_scope_evidence"]["diluted_amount_used"] is False
    assert parse_printed_current_cash(doc(BODY.replace("0.34元", "0.33元")), TARGET)[
        "cash_per_share"
    ] == Decimal("0.35")


@pytest.mark.parametrize(
    "defect", ["publisher", "date", "primary", "gross", "total", "class", "USD"]
)
def test_missing_current_total_scope_or_exact_identity_fails(defect):
    d = doc()
    if defect == "publisher":
        d["publisher_security_code"] = "600002"
    elif defect == "primary":
        d["publisher_title"] = "利润分配预案公告"
    else:
        a, b = {
            "date": ("2026/8/3", "2026/8/4"),
            "gross": ("(含税)", "(扣税后)"),
            "total": ("合计向全体股东每股派发现金红利0.35", "合计向全体股东每股派发现金红利0.36"),
            "class": ("本公司全体A股股东", "社会公众股和上市前原三家股东"),
            "USD": ("0.35元", "0.35美元"),
        }[defect]
        d["text"] = d["text"].replace(a, b)
    with pytest.raises(BoundedError):
        parse_printed_current_cash(d, TARGET)


def test_other_current_gross_claim_cannot_hide_behind_one_printed_total():
    broken = BODY.replace("虚拟分派", "向全体股东每股派发现金红利0.36元(含税)。虚拟分派")
    with pytest.raises(BoundedError):
        parse_printed_current_cash(doc(broken), TARGET)


def test_all_primaries_agree_and_unknown_foreign_scope_is_retained_as_held():
    bad = {**doc(BODY.replace("0.35元", "0.36元")), "document_id": "124"}
    chosen, _ = select_printed_current_candidates([TARGET], [doc(), bad])
    assert not chosen
    with pytest.raises(BoundedError):
        parse_printed_current_cash(doc(BODY + "H股另有安排。"), TARGET)


def test_literal_combined_header_needs_independent_gross_witness_and_two_current_scopes():
    text = (
        "证券代码:600001\nA股每股现金红利0.35元,为2025年度和2026年一季度合并后的分配比例,"
        "其中2025年度每股现金红利0.15元、2026年一季度每股现金红利0.20元。\n"
        + TABLE
        + "\n一、通过分配方案的股东大会届次和日期\n方案已审议通过。\n"
        "二、分配方案\n(1)2025年度利润分配方案\n扣除回购专户中的股份,"
        "向全体股东每10股派发现金红利1.5元(含税)。\n"
        "(2)2026年一季度利润分配方案\n扣除回购专户中的股份,"
        "向全体股东每10股派发现金红利2元(含税)。\n"
        "(3)本次差异化分红除权除息的计算依据\n虚拟分派每股现金红利0.34元。\n"
        "三、相关日期\n"
        + TABLE
        + "\n四、分配实施办法\n对于其他机构投资者及法人股东,其所得税自行缴纳,"
        "实际派发每股现金红利为人民币0.35元。\n五、有关咨询办法\n合成地址。"
    )
    atom = parse_printed_current_cash(doc(text), TARGET)
    assert atom["cash_per_share"] == Decimal("0.35")
    assert atom["aggregate_computed"] is False
    for a, b in [
        ("所得税自行缴纳", "纳税情况未知"),
        ("实际派发每股现金红利为人民币0.35", "实际派发每股现金红利为人民币0.36"),
        ("2026年一季度利润分配方案", "未明示的方案"),
        ("合并后的分配比例", "未实施的分配比例"),
    ]:
        with pytest.raises(BoundedError):
            parse_printed_current_cash(doc(text.replace(a, b)), TARGET)


def test_current_A_declaration_classifies_only_literal_prior_H_capital_observation():
    text = (
        "证券代码:600001\n"
        "一、股东会审议通过的权益分派方案情况\n公司总股本(其中A股总股本为100股,H股总股本为20股)。\n"
        "二、本次实施的权益分派方案\n公司实施的2025年度A股权益分派方案为:"
        "向全体股东每10股派发现金分红人民币1.5元(含税)。\n"
        "三、股权登记日与除权除息日\n股权登记日为2026年7月31日,除权除息日为2026年8月3日。"
    )
    atom = parse_printed_current_cash(doc(text), TARGET)
    assert atom["cash_per_share"] == Decimal("0.15")
    assert atom["printed_current_scope_evidence"]["original_roles"][0]["role"] == (
        "PRIOR_APPROVED_TOTAL_CAPITAL_ONLY"
    )
    for broken in [text + "H股另有事项。", text.replace("A股权益分派方案为", "股份权益分派方案为")]:
        with pytest.raises(BoundedError):
            parse_printed_current_cash(doc(broken), TARGET)


def test_A_current_gross_cannot_adopt_H_tax_amount_or_unclassified_H_clause():
    text = BODY.replace("合计向全体股东", "合计向全体股东") + (
        "\n四、分配实施办法\n(5)对于上海证券交易所投资者投资香港联交所本公司H股股票,"
        "港股通H股股票现金红利派发协议,港股通H股股票投资者的现金红利以人民币派发,"
        "请参见原香港公告。\n五、有关咨询办法\n合成地址。"
    )
    atom = parse_printed_current_cash(doc(text), TARGET)
    assert atom["cash_per_share"] == Decimal("0.35")
    assert atom["printed_current_scope_evidence"]["foreign_entitlements_qualified"] is False
    with pytest.raises(BoundedError):
        parse_printed_current_cash(doc(text.replace("请参见", "H股每股派1元(含税),请参见")), TARGET)
    with pytest.raises(BoundedError):
        parse_printed_current_cash(doc(text + "H股另一项观察。"), TARGET)
