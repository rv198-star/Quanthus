"""A versioned, explicitly headed implementation never chooses a fitting rate."""

from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_action import (
    select_notice_cash_candidates,
)
from quanthus.pipelines.securities_warehouse.base22_notice_scheme import (
    CONTRACT_ID,
    parse_current_scheme_cash,
)
from tests.unit.test_base22_notice_action import text


def body(current="每10股派1.234567元人民币现金（含税；扣税后另行处理）"):
    return (
        text("每10股派发现金红利1元（含税）")
        + "\n一、股东大会审议通过的分配方案\n旧计划每10股派发现金红利1元（含税）\n"
        + "二、本次实施的利润分配方案\n"
        + f"本公司2025年度权益分派方案为：以公司现有股本为基数，{current}\n"
        + "三、股权登记日与除权除息日\n除权除息日：2026年7月10日\n"
    )


def parse(t):
    return parse_current_scheme_cash(
        t,
        stock_code="002001",
        ex_date=date(2026, 7, 10),
        publisher_title="2025年度权益分派实施公告",
    )


def test_explicit_current_scheme_differs_from_approved_historical_plan_without_rounding():
    result = parse(body())
    assert result["contract_id"] == CONTRACT_ID
    assert result["cash_per_share"] == Decimal("0.1234567")
    assert len(result["explicit_current_sections"]) == 1
    assert result["historical_publication_or_acquisition_inferred"] is False


@pytest.mark.parametrize("heading", ["一、权益分派方案", "二、本次实施的权益分派方案"])
def test_exact_implemented_heading_and_company_declaration_are_both_required(heading):
    t = body().replace("二、本次实施的利润分配方案", heading)
    assert parse(t)["cash_per_share"] == Decimal("0.1234567")


@pytest.mark.parametrize(
    "defect",
    [
        "no-heading",
        "approved-heading",
        "no-company",
        "proposed-company",
        "within-conflict",
        "two-current-conflict",
        "wrong-date",
        "wrong-code",
        "mixed-currency",
        "precision",
    ],
)
def test_a_lone_matching_paragraph_cannot_evade_scope_or_old_global_guards(defect):
    t = body()
    if defect == "no-heading":
        t = t.replace("二、本次实施的利润分配方案", "普通段落")
    elif defect == "approved-heading":
        t = t.replace("二、本次实施的利润分配方案", "二、股东大会审议通过的方案")
    elif defect == "no-company":
        t = t.replace("本公司2025年度权益分派方案为", "这一方案")
    elif defect == "proposed-company":
        t = t.replace("本公司2025年度权益分派方案为", "公司拟按2025年度权益分派方案为")
    elif defect == "within-conflict":
        t = body("每10股派1.234567元人民币现金（含税）与每10股派2元人民币现金（含税）")
    elif defect == "two-current-conflict":
        t += "\n四、权益分派方案\n公司2025年度权益分派方案为：每10股派3元人民币现金（含税）"
    elif defect == "wrong-date":
        t += "除权除息日：2026年8月3日"
    elif defect == "wrong-code":
        t = t.replace("002001", "002002")
    elif defect == "mixed-currency":
        t += "H股每股派发港币现金"
    else:
        t = body("每10股派0.00000000001元人民币现金（含税）")
    with pytest.raises(BoundedError, match="NOTICE"):
        parse(t)


def test_equal_current_sections_can_agree_but_unparseable_current_section_blocks():
    t = (
        body()
        + "\n四、权益分派方案\n公司2025年度权益分派方案为：每10股派1.234567元人民币现金（含税）"
    )
    assert parse(t)["cash_per_share"] == Decimal("0.1234567")
    with pytest.raises(BoundedError, match="CASH_CONFLICT"):
        parse(body() + "\n四、权益分派方案\n公司2025年度权益分派方案为：未知非空说明")


def test_default_selector_preserves_legacy_contract_and_new_profile_is_explicit():
    targets = [{"action_id": "a", "stock_code": "002001", "ex_date": "2026-07-10"}]
    documents = [
        {
            "document_id": "1",
            "target_action_ids": ["a"],
            "publisher_title": "权益分派实施公告",
            "publisher_security_code": "002001",
            "text": body(),
        }
    ]
    selected, legacy = select_notice_cash_candidates(targets, documents)
    assert not selected and legacy["key_dispositions"]["a"] == "PRIMARY_IMPLEMENTATION_UNRESOLVED"
    selected, successor = select_notice_cash_candidates(
        targets, documents, parser_profile=CONTRACT_ID
    )
    assert selected["a"]["atom"]["cash_per_share"] == Decimal("0.1234567")
    assert successor["contract_id"] == CONTRACT_ID
    with pytest.raises(BoundedError, match="PROFILE_UNKNOWN"):
        select_notice_cash_candidates(targets, documents, parser_profile="invented")


def test_unresolved_other_primary_still_blocks_a_matching_current_scheme():
    targets = [{"action_id": "a", "stock_code": "002001", "ex_date": "2026-07-10"}]
    doc = {
        "document_id": "1",
        "target_action_ids": ["a"],
        "publisher_title": "权益分派实施公告",
        "publisher_security_code": "002001",
        "text": body(),
    }
    selected, receipt = select_notice_cash_candidates(
        targets, [doc, {**doc, "document_id": "2", "text": "不完整"}], parser_profile=CONTRACT_ID
    )
    assert not selected and receipt["key_dispositions"]["a"] == "PRIMARY_IMPLEMENTATION_UNRESOLVED"


def test_section_atom_restatement_still_binds_old_versions_and_real_new_acquisition():
    from quanthus.pipelines.securities_warehouse.base22 import digest
    from tests.unit.test_base22_notice_restatement import case, project

    values = list(case())
    t = body().replace("2026年7月10日", "2019年6月3日")
    atom = parse_current_scheme_cash(
        t, stock_code="002001", ex_date=date(2019, 6, 3), publisher_title="权益分派实施公告"
    )
    values[3] = atom
    values[4]["notice_atom_sha256"] = digest(atom)
    actions, terms, proof = project(values)
    assert terms[-1]["amount_per_share"] == Decimal("0.1234567")
    assert actions[-1]["knowledge_from"] >= proof["actual_new_acquisition"]
    values[3]["contract_id"] = "unreviewed-parser"
    values[4]["notice_atom_sha256"] = digest(values[3])
    with pytest.raises(BoundedError, match="EXACT_OLD_EVENT"):
        project(values)
