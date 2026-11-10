from copy import deepcopy
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_notice_h_reference import (
    declared_h_reference_view,
    parse_declared_h_excluded_cash,
    select_h_excluded_cash_candidates,
)
from tests.unit.test_base22_notice_identity_scope import document
from tests.unit.test_base22_notice_issuer_titles import BODY, TARGETS

EXCLUDED = BODY.replace("全体股东", "全体A股股东") + (
    "\nH股股东的现金红利发放不适用本公告。"
    "H股股息及其派发事项，请参见本公司于2026年6月1日在香港联合交易所有限公司"
    "网站http://www.hkexnews.hk发布的公告。"
)


def test_original_H_references_are_classified_without_granting_foreign_cash():
    inputs = [document(EXCLUDED)]
    original = deepcopy(inputs)
    chosen, proof = select_h_excluded_cash_candidates(TARGETS, inputs)
    assert inputs == original and set(chosen) == {"a"}
    atom = chosen["a"]["atom"]
    assert atom["cash_per_share"] == Decimal("0.15")
    assert atom["H_reference_scope_evidence"]["all_H_references_accounted"] is True
    assert atom["H_reference_scope_evidence"]["foreign_entitlements_qualified"] is False
    assert proof["historical_PIT_admission"] is False


@pytest.mark.parametrize("change", ["A", "exclude", "grant", "unknown_H", "USD", "B", "tax"])
def test_every_H_clause_requires_exact_non_entitlement_role_and_positive_A_scope(change):
    text = EXCLUDED
    title = "2025年度A股权益分派实施公告"
    if change == "A":
        text = text.replace("全体A股股东", "全体股东")
        title = title.replace("A股", "")
    elif change == "exclude":
        text = text.replace("不适用本公告", "现金另行发放")
    elif change == "grant":
        text += "H股每10股现金红利2元(含税)。"
    elif change == "unknown_H":
        text += "H股参与本次分配。"
    elif change == "USD":
        text += "美元另行处理。"
    elif change == "B":
        text += "B股另行处理。"
    else:
        text = text.replace("派发事项，请参见", "派发事项，每10股现金红利2元，请参见")
    with pytest.raises(BoundedError):
        parse_declared_h_excluded_cash(document(text, title), TARGETS[0])


def test_H_identity_header_and_explicit_exclusion_are_not_H_entitlements():
    text = EXCLUDED.replace("证券代码:600001", "A股代码:600001;H股代码:123;H股简称:合成公司")
    atom = parse_declared_h_excluded_cash(document(text), TARGETS[0])
    assert atom["cash_per_share"] == Decimal("0.15")


def test_literal_year_in_current_gross_claim_preserves_year_and_exact_unit():
    text = EXCLUDED.replace("每10股派1.5元人民币现金", "每10股派发2025年度现金红利人民币1.5元")
    frozen = document(text)
    original = deepcopy(frozen)
    atom = parse_declared_h_excluded_cash(frozen, TARGETS[0])
    assert frozen == original and atom["cash_per_share"] == Decimal("0.15")
    assert atom["literal_year_claim_sha256"]
    _, proof = declared_h_reference_view(frozen)
    assert proof["numeric_tokens_changed"] is False


@pytest.mark.parametrize("change", ["net", "no_unit", "conflict", "date", "code", "publisher"])
def test_new_literal_year_grammar_keeps_all_identity_time_gross_and_amount_gates(change):
    text = EXCLUDED.replace("每10股派1.5元人民币现金", "每10股派发2025年度现金红利人民币1.5元")
    code = "600001"
    if change == "net":
        text = text.replace("含税", "扣税后")
    elif change == "no_unit":
        text = text.replace("现金红利人民币", "现金红利")
    elif change == "conflict":
        text += "每10股派发2025年度现金红利人民币2元(含税)。"
    elif change == "date":
        text = text.replace("8月3日", "8月4日")
    elif change == "code":
        text = text.replace("600001", "600002")
    else:
        code = "600002"
    with pytest.raises(BoundedError):
        parse_declared_h_excluded_cash(document(text, code=code), TARGETS[0])


def test_literal_ex_date_label_variant_requires_same_exact_business_date():
    text = EXCLUDED.replace("除权除息日", "除权（除息）日")
    assert parse_declared_h_excluded_cash(document(text), TARGETS[0])["cash_per_share"] == Decimal(
        "0.15"
    )
    with pytest.raises(BoundedError):
        parse_declared_h_excluded_cash(document(text.replace("8月3日", "8月4日")), TARGETS[0])


def test_one_valid_original_cannot_hide_an_unreadable_or_conflicting_primary():
    docs = [
        document(EXCLUDED),
        {**document(EXCLUDED.replace("派1.5", "派1.6")), "document_id": "124"},
    ]
    chosen, _ = select_h_excluded_cash_candidates(TARGETS, docs)
    assert not chosen
    docs[1]["text"] = EXCLUDED + "H股未知方案。"
    chosen, _ = select_h_excluded_cash_candidates(TARGETS, docs)
    assert not chosen


@pytest.mark.parametrize(
    "claim",
    [
        "每10股派发2025年度现金红利人民币2元(含税)。",
        "每10股派发2025年度现金红利2元。",
    ],
)
def test_successful_old_claim_family_cannot_hide_other_current_year_claim(claim):
    with pytest.raises(BoundedError):
        parse_declared_h_excluded_cash(document(EXCLUDED + claim), TARGETS[0])


def test_unchanged_exact_cash_profiles_keep_their_original_cash_atom():
    from quanthus.pipelines.securities_warehouse.base22_notice_identity_scope import (
        select_exact_identity_candidates,
    )
    from tests.unit.test_base22_notice_gross_layout import BODY as COMBINED

    docs = [document(COMBINED)]
    old, _ = select_exact_identity_candidates(TARGETS, docs)
    new, _ = select_h_excluded_cash_candidates(TARGETS, docs)
    assert set(new) == set(old) == {"a"}
    assert new["a"]["atom"]["cash_atom_sha256"] == old["a"]["atom"]["cash_atom_sha256"]
