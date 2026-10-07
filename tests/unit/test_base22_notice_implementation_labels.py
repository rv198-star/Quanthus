"""A company implementation-scheme label is not an authority or amount override."""

from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_implementation_labels import (
    CONTRACT_ID,
    parse_implementation_label_cash,
)
from tests.unit.test_base22_notice_scheme import body
from tests.unit.test_base22_notice_scheme import parse as previous


def text():
    return body().replace("本公司2025年度权益分派方案为", "本公司2025年度权益分派实施方案为")


def parse(t):
    return parse_implementation_label_cash(
        t,
        stock_code="002001",
        ex_date=date(2026, 7, 10),
        publisher_title="2025年度权益分派实施公告",
    )


def test_company_implementation_label_preserves_exact_gross_cash_and_every_numeric_token():
    t = text()
    with pytest.raises(BoundedError, match="SECTION_UNAVAILABLE"):
        previous(t)
    atom = parse(t)
    assert atom["contract_id"] == CONTRACT_ID
    assert atom["cash_per_share"] == Decimal("0.1234567")
    assert atom["original_text_sha256"] == digest(t)
    assert atom["implementation_label_evidence"]["numeric_tokens_changed"] is False
    assert atom["historical_publication_or_acquisition_inferred"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "proposal",
        "old-heading",
        "compound-year",
        "wrong-date",
        "wrong-code",
        "mixed-currency",
        "net-only",
        "conflict",
    ],
)
def test_no_label_normalization_waives_the_original_scope_or_numeric_guards(defect):
    t = text()
    if defect == "proposal":
        t = t.replace("本公司2025年度", "公司拟按2025年度")
    elif defect == "old-heading":
        t = t.replace("二、本次实施的利润分配方案", "二、股东大会审议通过的方案")
    elif defect == "compound-year":
        t = t.replace("本公司2025年度", "本公司2025年度及2026年第一次中期")
    elif defect == "wrong-date":
        t += "除权除息日：2026年8月3日"
    elif defect == "wrong-code":
        t = t.replace("002001", "002002")
    elif defect == "mixed-currency":
        t += "H股另有港币方案"
    elif defect == "net-only":
        t = t.replace("含税", "扣税后")
    else:
        t = t.replace("三、股权", "每股派发现金红利0.777元（含税）\n三、股权")
    with pytest.raises(BoundedError, match="NOTICE"):
        parse(t)


def test_profile_is_opt_in_and_the_original_selector_remains_unchanged():
    from quanthus.pipelines.securities_warehouse.base22_notice_action import (
        select_notice_cash_candidates,
    )

    targets = [{"action_id": "a", "stock_code": "002001", "ex_date": "2026-07-10"}]
    docs = [
        {
            "document_id": "1",
            "target_action_ids": ["a"],
            "publisher_security_code": "002001",
            "publisher_title": "2025年度权益分派实施公告",
            "text": text(),
        }
    ]
    assert not select_notice_cash_candidates(targets, docs)[0]
    selected, evidence = select_notice_cash_candidates(targets, docs, parser_profile=CONTRACT_ID)
    assert selected["a"]["atom"]["cash_per_share"] == Decimal("0.1234567")
    assert evidence["contract_id"] == CONTRACT_ID


def test_a_restated_literal_atom_retains_original_event_versions_and_actual_acquisition():
    from tests.unit.test_base22_notice_restatement import case, project

    values = list(case())
    atom = parse_implementation_label_cash(
        text().replace("2026年7月10日", "2019年6月3日"),
        stock_code="002001",
        ex_date=date(2019, 6, 3),
        publisher_title="年度权益分派实施公告",
    )
    values[3] = atom
    values[4]["notice_atom_sha256"] = digest(atom)
    actions, terms, proof = project(values)
    assert terms[-1]["amount_per_share"] == Decimal("0.1234567")
    assert actions[-1]["knowledge_from"] >= proof["actual_new_acquisition"]
