from datetime import date
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_action import (
    parse_implemented_notice_cash,
    select_notice_cash_candidates,
)


def text(cash="每10股派发现金红利1.50元（含税）", day="2026年7月10日"):
    return f"证券代码：002001\n权益分派实施公告\n{cash}\n除权除息日：{day}"


def parse(body, title="2025年度权益分派实施公告"):
    return parse_implemented_notice_cash(
        body, stock_code="002001", ex_date=date(2026, 7, 10), publisher_title=title
    )


def test_exact_decimal_and_no_time_or_priority_inference():
    result = parse(text())
    assert result["cash_per_share"] == Decimal("0.15")
    assert result["currency"] == "CNY"
    assert result["source_priority_granted"] is False
    assert result["historical_publication_or_acquisition_inferred"] is False
    assert result["other_action_terms_qualified"] is False


def test_whitespace_fullwidth_and_iso_date():
    result = parse(text("每 １０ 股 派发 现金红利 １.５０ 元 （含税）", "2026-07-10"))
    assert result["cash_per_share"] == Decimal("0.15")


def test_per_share_unit_and_repeated_identical_atom():
    body = text("每1股派发现金红利0.15元（含税）") + text()
    assert parse(body)["cash_per_share"] == Decimal("0.15")


@pytest.mark.parametrize(
    "cash",
    [
        "每10股派1.50元人民币现金（含税；扣税后另行计算）",
        "每股派发现金红利0.15元（含税）",
        "每股派发现金股利人民币0.15元（含税）",
    ],
)
def test_explicit_alternate_gross_grammars(cash):
    assert parse(text(cash))["cash_per_share"] == Decimal("0.15")


def test_labeled_parenthesized_ex_date_and_slash_date():
    assert parse(text().replace("除权除息日：2026年7月10日", "除权（息）日：2026/7/10"))[
        "cash_per_share"
    ] == Decimal("0.15")


def table(day="2026/7/10"):
    return (
        "股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日\n"
        f"A股 2026/7/9 - {day} 2026/7/10"
    )


def test_complete_ordered_exchange_table_proves_ex_date():
    body = text().split("除权除息日")[0] + table()
    assert parse(body)["cash_per_share"] == Decimal("0.15")


@pytest.mark.parametrize(
    "suffix", [table("2026/8/3"), table("2026/2/30"), "除权（息）日 A股 2026/7/10"]
)
def test_incomplete_or_wrong_table_cannot_prove_ex_date(suffix):
    with pytest.raises(BoundedError, match="EX_DATE"):
        parse(text().split("除权除息日")[0] + suffix)


def test_conflicting_label_and_table_are_rejected():
    with pytest.raises(BoundedError, match="EX_DATE"):
        parse(text() + table("2026/8/3"))


@pytest.mark.parametrize(
    "title", ["年度利润分配方案实施公告", "年度权益分派实施的公告", "派发现金红利实施公告"]
)
def test_explicit_implementation_title_variants(title):
    assert parse(text(), title)["cash_per_share"] == Decimal("0.15")


@pytest.mark.parametrize("amount", ["0.00", "-1", "NaN", "1.50000000001"])
def test_invalid_or_nonexact_cash_is_withheld(amount):
    with pytest.raises(BoundedError):
        parse(text(f"每1股派发现金红利{amount}元（含税）"))


@pytest.mark.parametrize(
    "cash",
    [
        "每10股派发现金红利1.50元",
        "每10股派发现金红利1.50元（扣税后）",
        "每100股派发现金红利1.50元（含税）",
    ],
)
def test_no_tax_or_unit_fit(cash):
    with pytest.raises(BoundedError, match="UNIQUE_GROSS_CASH"):
        parse(text(cash))


def test_conflicting_cash_atoms_withheld():
    with pytest.raises(BoundedError, match="UNIQUE_GROSS_CASH"):
        parse(text() + text("每10股派发现金红利2.00元（含税）"))


@pytest.mark.parametrize(
    "body",
    [text(day="2026年8月3日"), text() + "除权除息日：2026年8月3日", text(day="2026年2月30日")],
)
def test_wrong_or_ambiguous_date_withheld(body):
    with pytest.raises(BoundedError, match="EX_DATE"):
        parse(body)


def test_security_identity_must_be_explicit():
    with pytest.raises(BoundedError, match="SECURITY_CODE"):
        parse(text().replace("002001", "002002"))


@pytest.mark.parametrize("label", ["H股", "B股", "港币", "港元", "美元"])
def test_mixed_security_currency_is_withheld(label):
    with pytest.raises(BoundedError, match="MIXED_SECURITY_OR_CURRENCY"):
        parse(text() + label)


def test_proposal_is_not_implementation():
    with pytest.raises(BoundedError, match="IMPLEMENTED_STATUS"):
        parse(text(), "2025年度利润分配预案")


def test_september_business_observation_is_rejected():
    with pytest.raises(BoundedError, match="BUSINESS_DATE_AFTER_IMAGE_CUTOFF"):
        parse_implemented_notice_cash(
            text(day="2026年9月1日"),
            stock_code="002001",
            ex_date=date(2026, 9, 1),
            publisher_title="权益分派实施公告",
        )


def document(doc="1", body=None, title="权益分派实施公告"):
    return {
        "document_id": doc, "target_action_ids": ["target"], "publisher_title": title,
        "publisher_security_code": "002001", "text": text() if body is None else body,
    }


def select(documents):
    return select_notice_cash_candidates(
        [{"action_id": "target", "stock_code": "002001", "ex_date": "2026-07-10"}],
        documents,
    )


def test_ancillary_opinion_is_disclosed_without_blocking_exact_issuer_notice():
    selected, proof = select([document(), document("2", title="律师核查意见")])
    assert selected["target"]["atom"]["cash_per_share"] == Decimal("0.15")
    assert proof["document_dispositions"][1]["status"] == "HELD"


@pytest.mark.parametrize("body", [text(day="2026年8月3日"), text("无法辨明金额")])
def test_unresolved_primary_document_blocks_another_success(body):
    selected, proof = select([document(), document("2", body)])
    assert selected == {}
    assert proof["key_dispositions"]["target"] == "PRIMARY_IMPLEMENTATION_UNRESOLVED"


def test_conflicting_primary_documents_are_not_selected_by_id_or_recency():
    selected, proof = select([document(), document("2", text("每股现金红利0.2元（含税）"))])
    assert selected == {}
    assert proof["key_dispositions"]["target"] == "PRIMARY_IMPLEMENTATION_CASH_CONFLICT"


@pytest.mark.parametrize("change", ["document_duplicate", "unknown_target", "duplicate_target"])
def test_selection_identity_is_closed(change):
    doc = document()
    docs = [doc]
    if change == "document_duplicate":
        docs.append(document())
    elif change == "unknown_target":
        doc["target_action_ids"] = ["unknown"]
    else:
        doc["target_action_ids"] *= 2
    with pytest.raises(BoundedError, match="NOTICE_DOCUMENT"):
        select(docs)
