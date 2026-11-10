"""Retained implementation-plan observations with exact units and honest clocks.

This is a new P10 source scope, not a reconstruction of normalization v3. Date
columns stay dates; a retrieval clock is not supplier publication or acquisition.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from quanthus.corporate_action_normalization import _IMPLEMENTATION_PLAN

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .base22_precision import decimal_profile
from .disclosure_event import materialize_corporate_action_rows

CONTRACT_ID = "p10-base22-retained-implementation-plan-exact-v1"
SOURCE_FAMILY = "p10_retained_eastmoney_implementation"


def _date_column(value: Any) -> date:
    # Retained Eastmoney fields are business-date columns encoded as ISO/SQL strings.
    # An optional midnight is serialization of the date, not an availability time.
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ]00:00:00)?", value):
        raise BoundedError("ACTION_AUTHORITY_DATE_COLUMN_INVALID")
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise BoundedError("ACTION_AUTHORITY_DATE_COLUMN_INVALID") from None


def parse_implemented_action(
    raw: dict[str, Any], *, source_row_ref: str, fetched_at: Any = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Accept only exact implemented text/structured agreement; no tolerance fit.

    The JSON caller must preserve numeric tokens with parse_float=Decimal. Missing
    fields remain missing and explicit zeros remain zero, even for an absent term.
    Every unsupported selected row must receive a retained rejection disposition.
    """
    if (
        raw.get("ASSIGN_PROGRESS") != "实施分配"
        or not isinstance(raw.get("SECURITY_CODE"), str)
        or not re.fullmatch(r"\d{6}", raw["SECURITY_CODE"])
        or not source_row_ref
    ):
        raise BoundedError("ACTION_AUTHORITY_IMPLEMENTED_IDENTITY_INVALID")
    effective = _date_column(raw.get("EX_DIVIDEND_DATE"))
    notice = _date_column(raw.get("NOTICE_DATE"))
    if effective > THROUGH or notice >= effective:
        raise BoundedError("ACTION_AUTHORITY_DATE_SCOPE_OR_NOTICE_INVALID")
    plan = raw.get("IMPL_PLAN_PROFILE")
    match = _IMPLEMENTATION_PLAN.fullmatch(plan) if isinstance(plan, str) else None
    if match is None or not any(match.group(name) for name in ("cash", "bonus", "transfer")):
        raise BoundedError("ACTION_AUTHORITY_STANDARD_PLAN_INVALID")
    values = {}
    classes = {}
    with localcontext() as context:
        context.prec = 64
        for original, token, target in (
            ("PRETAX_BONUS_RMB", "cash", "cash_per_share"),
            ("BONUS_RATIO", "bonus", "bonus_shares"),
            ("IT_RATIO", "transfer", "transfer_shares"),
        ):
            value = raw.get(original)
            text = match.group(token)
            if value is None:
                if text is not None:
                    raise BoundedError("ACTION_AUTHORITY_STRUCTURED_TERM_MISSING")
                converted = None
            else:
                if isinstance(value, (bool, float)) or not isinstance(value, (Decimal, int, str)):
                    raise BoundedError("ACTION_AUTHORITY_ORIGINAL_NUMERIC_TOKEN_REQUIRED")
                try:
                    number = Decimal(value)
                except (ValueError, ArithmeticError):
                    raise BoundedError("ACTION_AUTHORITY_NUMERIC_TOKEN_INVALID") from None
                if not number.is_finite() or number < 0:
                    raise BoundedError("ACTION_AUTHORITY_NUMERIC_TOKEN_INVALID")
                if number != Decimal(text or "0"):
                    raise BoundedError("ACTION_AUTHORITY_TEXT_STRUCTURED_DISAGREEMENT")
                converted = number / Decimal(10)
                if not decimal_profile(converted, precision=38, scale=10)["fits"]:
                    raise BoundedError("ACTION_AUTHORITY_DECIMAL_NOT_EXACTLY_REPRESENTABLE")
            values[target] = converted
            classes[target] = "NULL" if converted is None else "ZERO" if converted == 0 else "EXACT"
    positive = [field for field, value in values.items() if value is not None and value > 0]
    if not positive:
        raise BoundedError("ACTION_AUTHORITY_EMPTY_ACTION")
    record = {
        "stock_code": raw["SECURITY_CODE"],
        "source_row_key": source_row_ref,
        "ex_date": effective,
        "announce_date": notice,
        "record_date": _date_column(raw["EQUITY_RECORD_DATE"])
        if raw.get("EQUITY_RECORD_DATE") is not None
        else None,
        "payment_date": None,
        "action_type": "mixed"
        if len(positive) > 1
        else {"cash_per_share": "cash_div", "bonus_shares": "bonus", "transfer_shares": "transfer"}[
            positive[0]
        ],
        "progress": "实施",
        **values,
    }
    proof = {
        "contract_id": CONTRACT_ID,
        "stock_code": record["stock_code"],
        "source_row_ref": source_row_ref,
        "effective_date": effective,
        "notice_date": notice,
        "original_row_sha256": digest(raw),
        "parsed_record_sha256": digest(record),
        "source_per_share_conversion_denominator": 10,
        "numeric_classes": classes,
        "response_fetched_at": _clock(fetched_at) if fetched_at is not None else None,
        "provider_acquisition": None,
        "publication_instant": None,
        "original_normalization_manifest_restored": False,
        "historical_accepted_child": "NOT_ASSERTED",
    }
    return record, proof


def materialize_implemented_action(
    record: dict[str, Any],
    *,
    observed_at: Any,
    source_sha256: str,
    revision: str,
    parent: Any,
    vocabulary: dict[str, Any],
    schemas: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """New actual observation time, exact typed values and explicit term units."""
    import pyarrow as pa  # type: ignore[import-untyped]

    knowledge = max(_clock(observed_at), parent.knowledge_from)
    with localcontext() as context:
        context.prec = 64
        out = materialize_corporate_action_rows(
            {**record, "built_at": knowledge},
            source_id=SOURCE_FAMILY,
            source_sha256=source_sha256,
            source_revision=revision,
            company_id=parent.company_id,
            security_id=parent.security_id,
            vocabulary={**vocabulary, "share_quantity_unit": "per_share"},
            parent_knowledge_from=parent.knowledge_from,
        )
        expected = {
            vocabulary["term_types"][key]: value
            for key, value in record.items()
            if key in vocabulary["term_types"] and value is not None and value != 0
        }
        actual = {
            r["term_type"]: r["amount_per_share"]
            if r["amount_per_share"] is not None
            else r["ratio_numerator"] / r["ratio_denominator"]
            for r in out["dwd_corporate_action_term"]
        }
    if actual != expected:
        raise BoundedError("ACTION_AUTHORITY_TYPED_TERM_SEMANTICS_CHANGED")
    for table, rows in out.items():
        for row in rows:
            row["announced_at"] = None
            if pa.Table.from_pylist([row], schema=schemas[table]).to_pylist()[0] != row:
                raise BoundedError("ACTION_AUTHORITY_TYPED_VALUE_CHANGED")
    return out
