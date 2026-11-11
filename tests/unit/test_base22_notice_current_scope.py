"""Complete implemented section grammar does not infer an amount or event scope."""

from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22 import digest
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_current_scope import (
    CONTRACT_ID,
    parse_current_scope_cash,
)
from tests.unit.test_base22_notice_scheme import body


def parse(text, **kwargs):
    return parse_current_scope_cash(
        text,
        stock_code="002001",
        ex_date=date(2026, 7, 10),
        publisher_title="2025年度权益分派实施公告",
        **kwargs,
    )


@pytest.mark.parametrize(
    "prefix",
    [
        "2、含税及扣税情况",
        "2、截至2026年7月8日公司已回购股份，因此，",
        "1.本公司2025年度权益分派方案",
    ],
)
def test_current_company_declaration_can_follow_explicit_subheading_or_current_context(prefix):
    text = body().replace("本公司2025年度权益分派方案为", prefix + "本公司2025年度权益分派方案为")
    atom = parse(text)
    assert atom["cash_per_share"] == Decimal("0.1234567")
    assert atom["original_text_sha256"] == digest(text)
    assert atom["source_priority_granted"] is False


def test_adjusted_declaration_overrides_explicit_old_claim_without_computing_dilution():
    text = body().replace("本公司2025年度权益分派方案为", "公司2025年度权益分派方案原为")
    text = text.replace(
        "三、股权",
        "\n公司2025年度权益分派方案调整为：向全体股东每10股派3.456789元人民币现金（含税）；\n三、股权",
    )
    atom = parse(text)
    assert atom["cash_per_share"] == Decimal("0.3456789")
    assert {b["grammar"] for b in atom["explicit_current_blocks"]} == {
        "EXPLICIT_COMPANY_SCHEME_ADJUSTED_TO"
    }


def test_literal_actual_diluted_amount_is_not_derived_from_old_amount_or_share_capital():
    text = body().replace(
        "三、股权",
        "\n按照授予后股本为基数计算，摊薄后实际向全体股东每10股派0.987654元人民币现金（含税）。\n三、股权",
    )
    assert parse(text)["cash_per_share"] == Decimal("0.0987654")


@pytest.mark.parametrize("label", ["现金分红人民币", "现金股息人民币", "现金股利为人民币"])
def test_literal_cash_synonyms_preserve_exact_amount_and_gross_scope(label):
    text = body().replace("现金红利", label)
    assert parse(text)["cash_per_share"] == Decimal("0.1234567")


def test_literal_comma_before_gross_parenthesis_and_combined_declared_scheme():
    text = body().replace(
        "本公司2025年度权益分派方案为", "公司2025年度及2026年一季度A股权益分派实施方案合并为"
    )
    text = text.replace("派发现金红利", "分派现金股利人民币").replace("元（含税", "元，（含税")
    assert parse(text)["cash_per_share"] == Decimal("0.1234567")


def test_explicit_later_historical_approval_does_not_become_current_cash():
    text = body().replace(
        "三、股权",
        "根据公司2025年年度股东大会审议通过的利润分配方案，每股派发现金红利0.7元（含税）。\n三、股权",
    )
    assert parse(text)["cash_per_share"] == Decimal("0.1234567")


def test_exact_equivalent_parenthesized_ex_date_label_does_not_change_a_date():
    text = body().replace("除权除息日", "除权（除息）日")
    atom = parse(text)
    assert atom["ex_date"] == date(2026, 7, 10)
    assert atom["original_text_sha256"] == digest(text)
    assert atom["historical_publication_or_acquisition_inferred"] is False


def test_H_exclusion_requires_positive_A_scope_and_explicit_publisher_exclusion():
    text = body().replace("本公司2025年度", "本公司全体A股股东。\n本公司2025年度", 1)
    text += "\n本公司H股股东的现金红利发放的具体操作不适用本公告。"
    atom = parse(text)
    assert atom["cash_per_share"] == Decimal("0.1234567")
    assert atom["current_scope_evidence"]["H_scope_explicitly_excluded_by_publisher_body"] is True


@pytest.mark.parametrize(
    "defect",
    [
        "proposal",
        "old-heading",
        "wrong-code",
        "wrong-date",
        "net-only",
        "within-current-conflict",
        "nonuniform-entitlement",
        "unexcluded-H",
        "only-H-exclusion",
        "H-and-foreign-currency",
        "only-A-scope",
    ],
)
def test_explicit_successor_scope_cannot_supply_missing_authority_or_waive_body_conflicts(defect):
    text = body().replace(
        "本公司2025年度权益分派方案为", "2、含税及扣税情况本公司2025年度权益分派方案为"
    )
    if defect == "proposal":
        text = text.replace("本公司2025年度", "拟按本公司2025年度")
    elif defect == "old-heading":
        text = text.replace("二、本次实施的利润分配方案", "二、股东大会审议通过的方案")
    elif defect == "wrong-code":
        text = text.replace("002001", "002002")
    elif defect == "wrong-date":
        text += "\n除权（除息）日：2026年8月3日"
    elif defect == "net-only":
        text = text.replace("含税", "扣税后")
    elif defect == "within-current-conflict":
        text = text.replace("三、股权", "每股派发现金红利0.777元（含税）\n三、股权")
    elif defect == "nonuniform-entitlement":
        text = text.replace("二、本次实施的利润分配方案", "二、分配方案\n差异化分红送转方案")
    elif defect == "unexcluded-H":
        text += "\nH股另有方案。"
    elif defect == "only-H-exclusion":
        text += "\n本公司H股股东的现金红利发放的具体操作不适用本公告。"
    elif defect == "only-A-scope":
        text += "\n本公司全体A股股东。H股另有方案。"
    else:
        text += "\n本公司全体A股股东。本公司H股股东的现金红利发放的具体操作不适用本公告。港币。"
    with pytest.raises(BoundedError, match="NOTICE"):
        parse(text)


@pytest.mark.parametrize("text", [None, 1, "", "x" * (1024**2 + 1)])
def test_input_size_and_types_remain_fail_closed(text):
    with pytest.raises((BoundedError, TypeError), match="NOTICE|expected string|encode"):
        parse(text)


def test_new_scope_is_opt_in_and_a_primary_candidate_failure_still_blocks_the_exact_key():
    from quanthus.pipelines.securities_warehouse.base22_notice_action import (
        select_notice_cash_candidates,
    )

    text = body().replace("本公司2025年度权益分派方案为", "2、含税情况本公司2025年度权益分派方案为")
    targets = [{"action_id": "a", "stock_code": "002001", "ex_date": "2026-07-10"}]
    docs = [
        {
            "document_id": "1",
            "target_action_ids": ["a"],
            "publisher_security_code": "002001",
            "publisher_title": "2025年度权益分派实施公告",
            "text": text,
        }
    ]
    assert not select_notice_cash_candidates(targets, docs)[0]
    chosen, proof = select_notice_cash_candidates(targets, docs, parser_profile=CONTRACT_ID)
    assert chosen["a"]["atom"]["cash_per_share"] == Decimal("0.1234567")
    assert proof["contract_id"] == CONTRACT_ID
    docs.append({**docs[0], "document_id": "2", "text": text.replace("含税", "扣税后")})
    assert not select_notice_cash_candidates(targets, docs, parser_profile=CONTRACT_ID)[0]
