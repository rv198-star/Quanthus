from copy import deepcopy
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_ab_scope import (
    parse_selected_a_cash,
    select_a_b_cash_candidates,
)

TARGETS = [{"action_id": "a", "stock_code": "600001", "ex_date": "2026-08-03"}]
BODY = (
    "证券代码:600001 900001 证券简称:合成股份 合成B股 公告编号:2026-012\n"
    "重要内容提示:\nA股每股现金红利(税前)0.15元。A股每股现金红利(税后)0.12元。"
    "B股每股现金红利(税前)0.02美元。\n"
    "除权（除息）日:2026年8月3日。\n"
    "一、通过利润分配方案的股东大会\n方案已经年度股东大会审议通过。\n"
    "二、分配方案\n本次分配以总股本为基数，向全体股东每10股派1.5元人民币现金(含税)。"
    "B股股利折算成美元支付。\n三、实施日期\n除权（除息）日:2026年8月3日。"
)


def document(text=BODY, code="600001", title="年度利润分配实施公告"):
    return {
        "document_id": "123",
        "target_action_ids": ["a"],
        "publisher_security_code": code,
        "publisher_title": title,
        "text": text,
    }


def test_selected_A_literal_gross_unit_does_not_convert_foreign_B_payment():
    inputs = [document()]
    frozen = deepcopy(inputs)
    selected, proof = select_a_b_cash_candidates(TARGETS, inputs)
    assert inputs == frozen and set(selected) == {"a"}
    atom = selected["a"]["atom"]
    assert atom["cash_per_share"] == Decimal("0.15") and atom["currency"] == "CNY"
    assert atom["selected_A_scope_evidence"]["B_payment_currency_conversion_performed"] is False
    assert atom["selected_A_scope_evidence"]["foreign_B_terms_qualified"] is False
    assert proof["historical_PIT_admission"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "publisher",
        "first_code",
        "A_net",
        "A_missing",
        "amount",
        "foreign_unit",
        "date",
        "nonprimary",
        "precision",
    ],
)
def test_wrong_market_identity_gross_currency_or_date_cannot_use_B_or_old_sources(defect):
    text, code, title = BODY, "600001", "年度利润分配实施公告"
    if defect == "publisher":
        code = "900001"
    elif defect == "first_code":
        text = text.replace("600001 900001", "900001 600001")
    elif defect == "A_net":
        text = text.replace("A股每股现金红利(税前)", "A股每股现金红利(税后)")
    elif defect == "A_missing":
        text = text.replace("A股每股现金红利", "B股每股现金红利")
    elif defect == "amount":
        text = text.replace("派1.5元", "派1.6元")
    elif defect == "foreign_unit":
        text = text.replace("(税前)0.15元", "(税前)0.15美元")
    elif defect == "date":
        text = text.replace("8月3日", "8月4日")
    elif defect == "nonprimary":
        title = "年度利润分配预案公告"
    else:
        text = text.replace("0.15元", "0.12345678901元").replace("1.5元", "1.2345678901元")
    with pytest.raises(BoundedError):
        parse_selected_a_cash(document(text, code, title), TARGETS[0])


def test_explicit_A_B_dates_are_kept_separate_without_inventing_shared_market_date():
    text = BODY.replace("除权（除息）日:2026年8月3日。", "")
    text += (
        "A股股权登记日为2026年7月31日(T日)，除息日为2026年8月3日(T+1日);"
        "B股最后交易日为2026年7月31日(T日)，除息日为2026年8月4日(T+2日)。"
    )
    atom = parse_selected_a_cash(document(text), TARGETS[0])
    assert atom["ex_date"].isoformat() == "2026-08-03"
    assert atom["selected_A_scope_evidence"]["other_market_date_reused"] is False
    with pytest.raises(BoundedError):
        parse_selected_a_cash(document(text), [{**TARGETS[0], "ex_date": "2026-08-04"}][0])


def test_exchange_A_row_requires_full_calendar_column_header():
    text = BODY.replace("除权（除息）日:2026年8月3日。", "")
    table = (
        "股份类别 股权登记日 最后交易日 除权(息)日 现金红利发放日\n"
        "A股 2026/7/31 - 2026/8/3 2026/8/3\nB股 2026/8/4 2026/7/31 2026/8/3 2026/8/7"
    )
    atom = parse_selected_a_cash(document(text + table), TARGETS[0])
    assert atom["cash_per_share"] == Decimal("0.15")
    with pytest.raises(BoundedError):
        parse_selected_a_cash(
            document(text + table.replace("股份类别 股权登记日", "未知栏目")), TARGETS[0]
        )


def test_B_literal_amount_and_foreign_payment_change_cannot_change_selected_A_atom():
    atom = parse_selected_a_cash(document(), TARGETS[0])
    changed = parse_selected_a_cash(document(BODY.replace("0.02美元", "0.03美元")), TARGETS[0])
    assert atom["cash_atom_sha256"] == changed["cash_atom_sha256"]
    assert atom["original_text_sha256"] != changed["original_text_sha256"]


def test_literal_ordinary_gross_yuan_column_ends_before_foreign_B_columns():
    text = (
        BODY.replace(
            "A股每股现金红利(税前)0.15元。A股每股现金红利(税后)0.12元。",
            "单位:元\n每股现金红利(扣税前)0.15\n每股现金红利(扣税后)0.12\n",
        )
        + "A股现金红利发放日:2026年8月3日。"
    )
    atom = parse_selected_a_cash(document(text), TARGETS[0])
    assert atom["cash_per_share"] == Decimal("0.15")
    assert atom["selected_A_scope_evidence"]["literal_generic_yuan_gross_column"] is True
    for broken in [
        text.replace("单位:元", "单位:美元"),
        text.replace("扣税前", "扣税后"),
        text.replace("(扣税前)0.15", "(扣税前)0.15美元"),
    ]:
        with pytest.raises(BoundedError):
            parse_selected_a_cash(document(broken), TARGETS[0])


def test_T_offset_is_a_source_annotation_not_an_imposed_market_calendar_rule():
    text = BODY.replace("除权（除息）日:2026年8月3日。", "") + (
        "A股股权登记日为2026年7月31日(T日)，除息日为2026年8月3日(T+1日);"
        "B股最后交易日为2026年7月31日(T日)，除息日为2026年8月3日(T+1日)。"
    )
    atom = parse_selected_a_cash(document(text), TARGETS[0])
    assert atom["ex_date"].isoformat() == "2026-08-03"
    with pytest.raises(BoundedError, match="RECORD_AFTER_EX_DATE"):
        parse_selected_a_cash(
            document(text.replace("A股股权登记日为2026年7月31日", "A股股权登记日为2026年8月4日")),
            TARGETS[0],
        )


def test_all_primary_A_claims_must_agree_and_no_primary_is_discarded():
    other = {
        **document(BODY.replace("0.15元", "0.16元").replace("1.5元", "1.6元")),
        "document_id": "124",
    }
    selected, _ = select_a_b_cash_candidates(TARGETS, [document(), other])
    assert not selected
    other["text"] = BODY.replace("A股每股现金红利", "未知每股现金红利")
    selected, _ = select_a_b_cash_candidates(TARGETS, [document(), other])
    assert not selected


def test_selected_A_restatement_preserves_old_values_and_true_unknown_clocks():
    from quanthus.pipelines.securities_warehouse.base22 import digest
    from tests.unit.test_base22_notice_restatement import case, project

    c = case()
    body = BODY.replace("600001", "002001").replace("2026年8月3日", "2019年6月3日")
    target = {"action_id": "a", "stock_code": "002001", "ex_date": "2019-06-03"}
    c = (*c[:3], parse_selected_a_cash(document(body, code="002001"), target), c[4])
    c[4]["notice_atom_sha256"] = digest(c[3])
    frozen = deepcopy(c)
    actions, terms, proof = project(c, clock="2026-10-08T01:40:00+00:00")
    assert c == frozen
    assert actions[0] == {**c[1], "knowledge_to": actions[1]["knowledge_from"]}
    assert actions[1]["announced_at"] is None
    assert actions[1]["effective_at"] == c[1]["effective_at"]
    assert actions[1]["knowledge_from"].isoformat() == "2026-10-08T01:40:00+00:00"
    assert terms[0]["amount_per_share"].as_tuple() == c[2][0]["amount_per_share"].as_tuple()
    assert terms[-1]["amount_per_share"] == Decimal("0.15")
    assert proof["historical_PIT_admission"] is False
