"""Bounded retained August dividend projection, separate from frozen Daily gates."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from typing import Any

from .base22 import THROUGH, business_date, digest
from .base22_sources import SourceAssessmentError
from .disclosure_event import materialize_corporate_action_rows


def _clock(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise SourceAssessmentError("DIVIDEND_ACQUISITION_INVALID") from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SourceAssessmentError("DIVIDEND_ACQUISITION_UNKNOWN")
    return value


def dividend_date(value: Any) -> date:
    """The retained provider date column also serializes dates as naive midnight.

    Accept only its exact calendar-date encodings. A timed observation must not be
    silently truncated to fit this contract; source bytes/precision stay in evidence.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            try:
                instant = datetime.fromisoformat(value)
                if instant.tzinfo is None and instant.time() == datetime.min.time():
                    return instant.date()
            except ValueError:
                pass
    raise SourceAssessmentError("DIVIDEND_DATE_INVALID_OR_TIMED")


def project_dividend(
    row: dict[str, Any],
    *,
    source_sha256: str,
    source_revision: str,
    company_id: str,
    security_id: str,
    parent_knowledge_from: datetime,
    vocabulary: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Use real acquisition clocks, date precision and explicit dividend field aliases.

    No legacy built_at/announcement-date fallback becomes knowledge availability.
    Date-only publication stays unavailable as an instant. All original fields and
    their clocks are retained by evidence hash/field provenance. Actual callers must
    separately qualify dated identity and the one repaired-history dependency.
    """
    acquired = _clock(row.get("acquired_at"))
    provider = _clock(row.get("provider_acquired_at"))
    if parent_knowledge_from.tzinfo is None:
        raise SourceAssessmentError("DIVIDEND_PARENT_KNOWLEDGE_UNKNOWN")
    knowledge = max(acquired, provider, parent_knowledge_from)
    try:
        announced = dividend_date(row["announce_date"])
        ex_date = dividend_date(row["ex_dividend_date"]) if row.get("ex_dividend_date") else None
    except (ValueError, KeyError):
        raise SourceAssessmentError("DIVIDEND_DATE_INVALID") from None
    metrics = {
        "original_row_sha256": digest(row),
        "source_artifact_sha256": source_sha256,
        "provider_acquired_at": provider.isoformat(),
        "acquired_at": acquired.isoformat(),
        "parent_knowledge_from": parent_knowledge_from.isoformat(),
        "announce_date": announced.isoformat(),
        "original_announce_date": row["announce_date"],
        "original_ex_dividend_date": row.get("ex_dividend_date"),
        "ex_dividend_date": str(ex_date) if ex_date else None,
        "announced_instant": "UNAVAILABLE_DATE_PRECISION_ONLY",
        "knowledge_rule": (
            "max(real provider acquisition, real artifact acquisition, parent knowledge)"
        ),
        "fields": {},
        "consumer_admission": "NOT_GRANTED",
    }
    effective = ex_date or announced
    if effective > THROUGH:
        return {}, {**metrics, "disposition": "EXCLUDED_SEPTEMBER_OR_LATER_BUSINESS_DATE"}
    mapped = dict(row)
    mapped["ex_date"] = row.get("ex_dividend_date")
    mapped["built_at"] = knowledge  # explicit mapping of actual clocks, not package creation
    mapped["source_row_key"] = digest([row["stock_code"], announced, ex_date])
    with localcontext() as context:
        context.prec = 50
        for field in ("dividend_per_share", "bonus_shares", "transfer_shares"):
            value = row.get(field)
            if isinstance(value, float):
                raise SourceAssessmentError("DIVIDEND_FLOAT_NUMERIC_SOURCE")
            classification = (
                "null" if value in (None, "") else "zero" if Decimal(str(value)) == 0 else "nonzero"
            )
            metrics["fields"][field] = classification
        result = materialize_corporate_action_rows(
            mapped,
            source_id="base22_august/dividends_delta",
            source_sha256=source_sha256,
            source_revision=source_revision,
            company_id=company_id,
            security_id=security_id,
            vocabulary=vocabulary,
            parent_knowledge_from=parent_knowledge_from,
        )
    for rows in result.values():
        for output in rows:
            output["knowledge_from"] = knowledge.astimezone(timezone.utc)
            output["announced_at"] = None  # source date is retained; a clock is not inferred
            if business_date(output["effective_at"]) != effective:
                raise SourceAssessmentError("DIVIDEND_EFFECTIVE_DATE_CHANGED")
    # Decimal values must fit the frozen schema without implicit quantization.
    for term in result["dwd_corporate_action_term"]:
        for field in ("amount_per_share", "ratio_numerator", "ratio_denominator"):
            value = term.get(field)
            if value is not None and value.as_tuple().exponent < -10:
                raise SourceAssessmentError("DIVIDEND_DECIMAL_SCALE_LOSS")
    metrics["disposition"] = "TYPED_PROJECTION_PENDING_DOMAIN_CLOSURE"
    metrics["output_row_digest"] = digest(result)
    metrics["output_rows"] = {t: len(rows) for t, rows in result.items()}
    return result, metrics
