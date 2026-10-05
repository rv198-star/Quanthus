from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_action_reconciliation import (
    compare_action_observations,
)
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError


def observations():
    clock = datetime(2026, 10, 5, tzinfo=timezone.utc)
    left = {
        "corporate_action_id": "left",
        "record_source": "first",
        "source_revision": "a",
        "company_id": "c",
        "security_id": "s",
        "effective_at": clock - timedelta(days=60),
        "ex_date": date(2026, 8, 6),
        "knowledge_from": clock,
        "action_type": "CASH_DIVIDEND",
        "action_status": "IMPLEMENTED",
        "record_date": date(2026, 8, 5),
        "payment_date": None,
    }
    right = {
        **left,
        "corporate_action_id": "right",
        "record_source": "second",
        "source_revision": "b",
        "knowledge_from": clock + timedelta(seconds=1),
    }
    terms = []
    for row in (left, right):
        terms.append(
            [
                {
                    "corporate_action_id": row["corporate_action_id"],
                    "record_source": row["record_source"],
                    "source_revision": row["source_revision"],
                    "knowledge_from": row["knowledge_from"],
                    "term_type": "CASH_AMOUNT_PER_SHARE",
                    "option_id": None,
                    "currency": "CNY",
                    "amount_per_share": Decimal("0.0123456789"),
                    "ratio_numerator": None,
                    "ratio_denominator": None,
                    "subscription_price": None,
                }
            ]
        )
    return left, terms[0], right, terms[1]


def test_equal_terms_do_not_grant_priority_or_change_true_times_or_inputs():
    inputs = observations()
    before = deepcopy(inputs)
    proof = compare_action_observations(*inputs)
    assert inputs == before
    assert proof["classification"] == "SHARED_FACTS_EQUAL"
    assert not proof["source_priority_granted"]
    assert proof["left_true_knowledge_from"] != proof["right_true_knowledge_from"]
    assert "0.0123456789" not in str(proof)


@pytest.mark.parametrize("value", [Decimal("0.0123456788"), Decimal("0"), None])
def test_numeric_disagreement_and_zero_null_difference_are_retained(value):
    left, lt, right, rt = observations()
    rt[0]["amount_per_share"] = value
    result = compare_action_observations(left, lt, right, rt)
    assert result["classification"] == "ACTION_OR_TERMS_CONFLICT"
    assert not result["exact_terms_equal"] and not result["source_priority_granted"]


def test_missing_optional_date_is_never_filled_and_known_date_conflict_is_separate():
    left, lt, right, rt = observations()
    right["record_date"] = None
    result = compare_action_observations(left, lt, right, rt)
    assert result["classification"] == "SHARED_FACTS_EQUAL_OPTIONAL_DATE_UNAVAILABLE"
    assert right["record_date"] is None and not result["missing_date_inferred"]
    right["record_date"] = date(2026, 8, 4)
    assert (
        compare_action_observations(left, lt, right, rt)["classification"] == "KNOWN_DATE_CONFLICT"
    )


def test_same_business_date_does_not_accept_conflicting_parent_identity():
    left, lt, right, rt = observations()
    right["company_id"] = "another"
    assert compare_action_observations(left, lt, right, rt)["classification"] == "IDENTITY_CONFLICT"


@pytest.mark.parametrize(
    "mutation", ["duplicate", "wrong_parent", "float", "nan", "missing", "empty"]
)
def test_bad_term_closure_fails_closed(mutation):
    left, lt, right, rt = observations()
    if mutation == "duplicate":
        rt.append(deepcopy(rt[0]))
    elif mutation == "wrong_parent":
        rt[0]["corporate_action_id"] = "other"
    elif mutation == "float":
        rt[0]["amount_per_share"] = 0.1
    elif mutation == "nan":
        rt[0]["amount_per_share"] = Decimal("NaN")
    elif mutation == "missing":
        del rt[0]["currency"]
    else:
        rt.clear()
    with pytest.raises(BoundedError):
        compare_action_observations(left, lt, right, rt)
