from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext

import pytest

from quanthus.pipelines.securities_warehouse.base22_action_reconciliation import (
    compare_normalized_totals,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError


def fixture():
    original = {
        "effective_date": date(2026, 7, 30),
        "cash_dividend_per_share": Decimal("0.0123456789"),
        "bonus_share_ratio": Decimal("0"),
    }
    action = {
        "company_id": "company",
        "security_id": "security",
        "corporate_action_id": "action",
        "ex_date": original["effective_date"],
        "action_status": "IMPLEMENTED",
        "record_source": "source",
        "source_revision": "revision",
        "knowledge_from": datetime(2026, 10, 5, tzinfo=timezone.utc),
    }
    term = {
        **{
            f: action[f]
            for f in ("corporate_action_id", "record_source", "source_revision", "knowledge_from")
        },
        "term_type": "CASH_AMOUNT_PER_SHARE",
        "option_id": None,
        "currency": "CNY",
        "amount_per_share": original["cash_dividend_per_share"],
        "ratio_numerator": None,
        "ratio_denominator": None,
        "subscription_price": None,
    }
    return original, action, [term]


def compare(original, action, terms):
    return compare_normalized_totals(
        original, action, terms, company_id="company", security_id="security"
    )


def test_shared_cash_and_source_zero_keep_unknown_allocation_and_all_originals():
    inputs = fixture()
    before = deepcopy(inputs)
    proof = compare(*inputs)
    assert proof["classification"] == "SHARED_PRESENT_TOTALS_EQUAL_ZERO_COMPONENT_UNMENTIONED"
    assert proof["combined_share_ratio_equal"] is None
    assert proof["F10_unmentioned_components"] == ["combined_shares"]
    assert not proof["bonus_transfer_allocation_proven"]
    assert not proof["unmentioned_component_changed_to_zero"]
    assert not proof["source_priority_granted"]
    assert not proof["original_normalization_restored"]
    assert "0.0123456789" not in str(proof)
    assert inputs == before


def test_combined_ratios_are_compared_without_ambient_decimal_rounding():
    original, action, terms = fixture()
    original["bonus_share_ratio"] = Decimal("0.125")
    share = {
        **terms[0],
        "term_type": "BONUS_SHARES_PER_SHARE",
        "currency": None,
        "amount_per_share": None,
        "ratio_numerator": Decimal("1"),
        "ratio_denominator": Decimal("16"),
    }
    terms += [share, {**share, "term_type": "TRANSFER_SHARES_PER_SHARE"}]
    with localcontext() as context:
        context.prec = 2
        result = compare(original, action, terms)
    assert result["classification"] == "SHARED_TOTALS_EQUAL"
    assert not result["bonus_transfer_allocation_proven"]


def test_sub_quantum_numeric_conflict_is_never_rounded_away():
    original, action, terms = fixture()
    terms[0]["amount_per_share"] += Decimal("0.0000000001")
    result = compare(original, action, terms)
    assert result["classification"] == "EXACT_TOTAL_CONFLICT"
    assert not result["cash_total_equal"]


def test_unmentioned_positive_component_is_unavailable_instead_of_a_zero_conflict():
    original, action, terms = fixture()
    original["bonus_share_ratio"] = Decimal("0.1")
    proof = compare(original, action, terms)
    assert proof["classification"] == "NORMALIZED_NONZERO_COMPONENT_UNAVAILABLE_IN_F10"
    assert proof["combined_share_ratio_equal"] is None
    assert proof["cash_total_equal"] is True


@pytest.mark.parametrize("value", [None, 0.1, Decimal("NaN"), Decimal("-1")])
def test_unknown_or_invalid_old_total_does_not_become_zero(value):
    original, action, terms = fixture()
    original["bonus_share_ratio"] = value
    with pytest.raises(BoundedError, match="EXACT_NONNEGATIVE"):
        compare(original, action, terms)


@pytest.mark.parametrize(
    "field,value",
    [("company_id", "other"), ("security_id", "other"), ("ex_date", date(2026, 8, 3))],
)
def test_existing_same_day_or_wrong_parent_is_not_a_qualified_binding(field, value):
    original, action, terms = fixture()
    action[field] = value
    assert compare(original, action, terms)["classification"] == "IDENTITY_OR_DATE_CONFLICT"


@pytest.mark.parametrize(
    "mutation",
    [
        "zero_denominator",
        "option",
        "foreign_currency",
        "unknown_term",
        "duplicate",
        "bad_clock",
        "missing_field",
    ],
)
def test_invalid_units_clocks_and_closure_fail_closed(mutation):
    original, action, terms = fixture()
    if mutation == "zero_denominator":
        terms[0].update(
            term_type="BONUS_SHARES_PER_SHARE",
            currency=None,
            amount_per_share=None,
            ratio_numerator=Decimal("1"),
            ratio_denominator=Decimal("0"),
        )
    elif mutation == "option":
        terms[0]["option_id"] = "choice"
    elif mutation == "foreign_currency":
        terms[0]["currency"] = "USD"
    elif mutation == "unknown_term":
        terms[0]["term_type"] = "UNKNOWN"
    elif mutation == "duplicate":
        terms.append(deepcopy(terms[0]))
    elif mutation == "bad_clock":
        action["knowledge_from"] = datetime(2026, 10, 5)
    else:
        del action["source_revision"]
    with pytest.raises(BoundedError):
        compare(original, action, terms)
