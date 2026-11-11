"""Different source holder classes cannot become one average/dividend scalar."""

from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_class_terms import parse_class_cash_terms

BODY = (
    "证券代码:600001\n一、分配方案\n"
    "3.差异化分红送转方案:(1)本次差异化分红的依据。"
    "(2)本次差异化分红的计算:公式为捐赠总额乘以股数。"
    "根据上述计算额度进行补偿后，限售股股东每股应派发现金红利人民币0元(含税)，"
    "流通股股东每股应派发现金红利人民币1.2345678901元(含税)。"
    "(3)每股平均分红计算公式:平均每股分配金额为0.5元。\n"
    "二、日期\n除息日:2026年8月3日"
)


def parse(body=BODY):
    return parse_class_cash_terms(
        body, stock_code="600001", ex_date=date(2026, 8, 3), publisher_title="年度利润分配实施公告"
    )


def test_distinct_literal_gross_classes_keep_zero_and_precision_without_uniform_cash():
    proof = parse()
    terms = proof["scheme"]["terms"]
    assert {t["amount_per_share"] for t in terms} == {Decimal(0), Decimal("1.2345678901")}
    assert {t["source_class_label"] for t in terms} == {"限售股股东", "流通股股东"}
    assert proof["general_cash_scalar_selected"] is False
    assert proof["virtual_or_average_cash_computed"] is False
    assert proof["consumer_admission"] == "NOT_GRANTED"


@pytest.mark.parametrize(
    "pair",
    [
        ("限售股股东", "无限售股股东"),
        ("大股东", "中小股东"),
    ],
)
def test_exact_other_literal_pairs_keep_the_publishers_own_class_names(pair):
    proof = parse(BODY.replace("限售股股东", pair[0]).replace("流通股股东", pair[1]))
    assert {t["source_class_label"] for t in proof["scheme"]["terms"]} == set(pair)
    assert proof["actual_holder_membership_inferred"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "only_one_class",
        "conflict",
        "net",
        "no_CNY",
        "no_current",
        "no_boundary",
        "partial_class",
        "foreign",
        "identity",
        "date",
        "precision",
        "unknown_class",
    ],
)
def test_every_class_current_coordinate_unit_and_literal_boundary_is_required(defect):
    body = BODY
    if defect == "only_one_class":
        body = body.replace("流通股股东每股应派发现金红利人民币1.2345678901元(含税)。", "")
    elif defect == "conflict":
        body = body.replace(
            "(3)每股平均", "限售股股东每股应派发现金红利人民币2元(含税)。(3)每股平均"
        )
    elif defect == "net":
        body = body.replace("含税", "税后")
    elif defect == "no_CNY":
        body = body.replace("人民币", "")
    elif defect == "no_current":
        body = body.replace("根据上述计算额度进行补偿后", "根据以前的方案")
    elif defect == "no_boundary":
        body = body.replace("(3)每股平均分红计算公式", "其他未审定内容")
    elif defect == "partial_class":
        body = body.replace("流通股股东", "部分流通股股东")
    elif defect == "foreign":
        body += "\nB股美元另行办理。"
    elif defect == "identity":
        body = body.replace("600001", "600002")
    elif defect == "date":
        body = body.replace("8月3日", "8月4日")
    elif defect == "precision":
        body = body.replace("1.2345678901", "1.234567890123")
    else:
        body = body.replace("流通股股东", "不明股东类别")
    with pytest.raises(BoundedError):
        parse(body)


def test_averages_and_equations_are_not_promoted_to_an_actual_class_cash_atom():
    proof = parse(BODY.replace("平均每股分配金额为0.5元", "平均每股分配金额为999元"))
    assert {t["amount_per_share"] for t in proof["scheme"]["terms"]} == {
        Decimal(0),
        Decimal("1.2345678901"),
    }


def test_explicit_total_then_average_boundary_never_admits_the_average_or_total():
    body = BODY.replace("(3)每股平均分红计算公式:", "共计派发现金股利100,000.01元。")
    body = body.replace("平均每股分配金额为0.5元", "平均每股分配金额为888元")
    proof = parse(body)
    assert len(proof["scheme"]["terms"]) == 2
    assert {t["amount_per_share"] for t in proof["scheme"]["terms"]} == {
        Decimal(0),
        Decimal("1.2345678901"),
    }


def test_unparseable_or_conflicting_primary_document_blocks_the_entire_key():
    from quanthus.pipelines.securities_warehouse.base22_notice_class_terms import (
        select_class_cash_candidates,
    )

    targets = [{"action_id": "a", "stock_code": "600001", "ex_date": "2026-08-03"}]
    document = {
        "document_id": "123",
        "target_action_ids": ["a"],
        "publisher_security_code": "600001",
        "publisher_title": "利润分配实施公告",
        "text": BODY,
    }
    selected, proof = select_class_cash_candidates(targets, [document])
    assert set(selected) == {"a"}
    selected, proof = select_class_cash_candidates(
        targets,
        [document, {**document, "document_id": "124", "text": BODY.replace("含税", "税后")}],
    )
    assert not selected and proof["key_dispositions"]["a"] == "PRIMARY_CLASS_SCOPE_UNRESOLVED"
    selected, proof = select_class_cash_candidates(
        targets,
        [document, {**document, "document_id": "124", "text": BODY.replace("1.2345678901", "2")}],
    )
    assert not selected and proof["key_dispositions"]["a"] == "PRIMARY_CLASS_TERMS_CONFLICT"
