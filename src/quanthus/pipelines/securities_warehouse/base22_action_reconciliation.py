"""Compare retained typed action observations without deciding source priority."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError

CONTRACT_ID = "p10-base22-action-source-comparison-v1"
_IDENTITY = ("company_id", "security_id", "effective_at", "ex_date")
_SEMANTICS = ("action_type", "action_status")
_DATES = ("record_date", "payment_date")
_NUMBERS = (
    "amount_per_share",
    "ratio_numerator",
    "ratio_denominator",
    "subscription_price",
)


def _terms(
    action: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[tuple[str, str | None], tuple[Any, ...]]:
    signatures = {}
    for row in rows:
        required = (
            "corporate_action_id",
            "term_type",
            "option_id",
            "currency",
            *_NUMBERS,
            "record_source",
            "source_revision",
            "knowledge_from",
        )
        if any(field not in row for field in required):
            raise BoundedError("ACTION_COMPARE_TERM_FIELDS_MISSING")
        if (
            row["corporate_action_id"] != action["corporate_action_id"]
            or row["record_source"] != action["record_source"]
            or row["source_revision"] != action["source_revision"]
            or row["knowledge_from"] != action["knowledge_from"]
        ):
            raise BoundedError("ACTION_COMPARE_TERM_PARENT_OR_SOURCE_MISMATCH")
        if not isinstance(row["term_type"], str) or not row["term_type"]:
            raise BoundedError("ACTION_COMPARE_TERM_TYPE_INVALID")
        option = row["option_id"]
        if option is not None and (not isinstance(option, str) or not option):
            raise BoundedError("ACTION_COMPARE_OPTION_INVALID")
        for field in _NUMBERS:
            value = row[field]
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise BoundedError("ACTION_COMPARE_EXACT_NUMERIC_VALUE_REQUIRED")
        key = row["term_type"], option
        if key in signatures:
            raise BoundedError("ACTION_COMPARE_DUPLICATE_SEMANTIC_TERM")
        signatures[key] = tuple(row[field] for field in ("currency", *_NUMBERS))
    if not signatures:
        raise BoundedError("ACTION_COMPARE_EMPTY_TERM_SET")
    return signatures


def compare_action_observations(
    left: dict[str, Any],
    left_terms: list[dict[str, Any]],
    right: dict[str, Any],
    right_terms: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return only hashes, dispositions and true clocks, never financial values.

    Missing dates remain missing. Equal amounts or a newer warehouse observation
    do not establish source priority, publication availability or legacy acceptance.
    Physical schema/source/raw closure verification remains the caller's job.
    """
    required = (
        "corporate_action_id",
        "record_source",
        "source_revision",
        "knowledge_from",
        *_IDENTITY,
        *_SEMANTICS,
        *_DATES,
    )
    for action in (left, right):
        if any(field not in action for field in required):
            raise BoundedError("ACTION_COMPARE_ACTION_FIELDS_MISSING")
        for field in ("knowledge_from", "effective_at"):
            value = action[field]
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise BoundedError("ACTION_COMPARE_TIME_INSTANT_REQUIRED")
        for field in ("ex_date", *_DATES):
            value = action[field]
            if field == "ex_date" and value is None:
                raise BoundedError("ACTION_COMPARE_BUSINESS_DATE_REQUIRED")
            if value is not None and (
                type(value) is not date or (field == "ex_date" and value > THROUGH)
            ):
                raise BoundedError("ACTION_COMPARE_DATE_INVALID_OR_OUTSIDE_SCOPE")
    lterms, rterms = _terms(left, left_terms), _terms(right, right_terms)
    identity_equal = all(left[f] == right[f] for f in _IDENTITY)
    semantics_equal = all(left[f] == right[f] for f in _SEMANTICS)
    date_conflicts = [
        f for f in _DATES if left[f] is not None and right[f] is not None and left[f] != right[f]
    ]
    missing = [f for f in _DATES if (left[f] is None) != (right[f] is None)]
    terms_equal = lterms == rterms
    reason = (
        "IDENTITY_CONFLICT"
        if not identity_equal
        else "ACTION_OR_TERMS_CONFLICT"
        if not semantics_equal or not terms_equal
        else "KNOWN_DATE_CONFLICT"
        if date_conflicts
        else "SHARED_FACTS_EQUAL_OPTIONAL_DATE_UNAVAILABLE"
        if missing
        else "SHARED_FACTS_EQUAL"
    )
    return {
        "contract_id": CONTRACT_ID,
        "classification": reason,
        "identity_equal": identity_equal,
        "action_semantics_equal": semantics_equal,
        "exact_terms_equal": terms_equal,
        "known_date_conflicts": date_conflicts,
        "one_source_optional_date_unavailable": missing,
        "left_action_sha256": digest(left),
        "right_action_sha256": digest(right),
        "left_term_rows_sha256": sorted(digest(row) for row in left_terms),
        "right_term_rows_sha256": sorted(digest(row) for row in right_terms),
        "left_true_knowledge_from": left["knowledge_from"],
        "right_true_knowledge_from": right["knowledge_from"],
        "source_priority_granted": False,
        "missing_date_inferred": False,
        "original_normalization_restored": False,
    }


def compare_normalized_totals(
    normalized: dict[str, Any],
    action: dict[str, Any],
    terms: list[dict[str, Any]],
    *,
    company_id: str,
    security_id: str,
) -> dict[str, Any]:
    """Compare archived cash/combined-share totals to a real typed observation.

    This is evidence comparison, not replay of a missing normalization manifest.
    A combined old share ratio cannot prove the bonus/transfer allocation. An
    unmentioned component is not a source zero; only an explicit normalized zero
    can be compared to that absence, with the limitation recorded in the proof.
    Dates, identities and raw closure must also be checked by the bounded caller.
    """
    values = [normalized.get(f) for f in ("cash_dividend_per_share", "bonus_share_ratio")]
    expected = []
    for value in values:
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            raise BoundedError("ACTION_TOTALS_EXACT_NONNEGATIVE_DECIMAL_REQUIRED")
        expected.append(Fraction(*value.as_integer_ratio()))
    required = (
        "corporate_action_id",
        "record_source",
        "source_revision",
        "knowledge_from",
        "company_id",
        "security_id",
        "ex_date",
        "action_status",
    )
    if any(f not in action for f in required) or any(
        not isinstance(v, str) or not v for v in (company_id, security_id)
    ):
        raise BoundedError("ACTION_TOTALS_ACTION_OR_PARENT_FIELDS_INVALID")
    day = normalized.get("effective_date")
    if type(day) is not date or day > THROUGH or type(action.get("ex_date")) is not date:
        raise BoundedError("ACTION_TOTALS_BUSINESS_DATE_INVALID")
    clock = action.get("knowledge_from")
    if not isinstance(clock, datetime) or clock.utcoffset() is None:
        raise BoundedError("ACTION_TOTALS_TRUE_KNOWLEDGE_INSTANT_REQUIRED")
    signatures = _terms(action, terms)
    cash = Fraction(0)
    shares = Fraction(0)
    cash_present = shares_present = False
    for (typ, option), (
        currency,
        amount,
        numerator,
        denominator,
        subscription,
    ) in signatures.items():
        if option is not None or subscription is not None:
            raise BoundedError("ACTION_TOTALS_UNSUPPORTED_OPTION_OR_SUBSCRIPTION")
        if typ == "CASH_AMOUNT_PER_SHARE":
            if (
                currency != "CNY"
                or amount is None
                or amount < 0
                or numerator is not None
                or denominator is not None
            ):
                raise BoundedError("ACTION_TOTALS_CASH_UNIT_OR_FIELDS_INVALID")
            cash = Fraction(amount)
            cash_present = True
        elif typ in {"BONUS_SHARES_PER_SHARE", "TRANSFER_SHARES_PER_SHARE"}:
            if (
                currency is not None
                or amount is not None
                or numerator is None
                or denominator is None
                or numerator < 0
                or denominator <= 0
            ):
                raise BoundedError("ACTION_TOTALS_SHARE_UNIT_OR_FIELDS_INVALID")
            shares += Fraction(numerator) / Fraction(denominator)
            shares_present = True
        else:
            raise BoundedError("ACTION_TOTALS_UNSUPPORTED_TERM_TYPE")
    unmentioned = []
    if not cash_present:
        unmentioned.append("cash")
    if not shares_present:
        unmentioned.append("combined_shares")
    identity_equal = (
        action.get("company_id") == company_id and action.get("security_id") == security_id
    )
    date_equal = action["ex_date"] == day
    semantics_equal = action.get("action_status") == "IMPLEMENTED"
    cash_equal, shares_equal = cash == expected[0], shares == expected[1]
    return {
        "contract_id": "p10-base22-normalized-total-comparison-v1",
        "classification": "IDENTITY_OR_DATE_CONFLICT"
        if not identity_equal or not date_equal
        else "ACTION_STATUS_CONFLICT"
        if not semantics_equal
        else "EXACT_TOTAL_CONFLICT"
        if not cash_equal or not shares_equal
        else "SHARED_TOTALS_EQUAL_UNMENTIONED_ZERO_COMPONENT"
        if unmentioned
        else "SHARED_TOTALS_EQUAL",
        "identity_equal": identity_equal,
        "business_date_equal": date_equal,
        "cash_total_equal": cash_equal,
        "combined_share_ratio_equal": shares_equal,
        "F10_unmentioned_components": unmentioned,
        "unmentioned_component_changed_to_zero": False,
        "bonus_transfer_allocation_proven": False,
        "normalized_row_sha256": digest(normalized),
        "actual_action_sha256": digest(action),
        "actual_term_row_sha256": sorted(digest(row) for row in terms),
        "actual_knowledge_from": clock,
        "source_priority_granted": False,
        "original_normalization_restored": False,
    }
