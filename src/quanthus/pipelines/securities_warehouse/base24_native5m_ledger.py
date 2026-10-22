"""Evidence-bound native-5m security-day classification for Base 2.4."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

CONTRACT = "p10-base24-native5m-coverage-ledger-v1"
VENUES = frozenset({"SSE", "SZSE"})


class Native5mLedgerError(ValueError):
    """Stable fail-closed reason without source prices."""


def _clock(value: Any) -> str:
    if not isinstance(value, str):
        raise Native5mLedgerError("EVIDENCE_IDENTITY_INVALID")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise Native5mLedgerError("EVIDENCE_IDENTITY_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Native5mLedgerError("EVIDENCE_IDENTITY_INVALID")
    return value


def _evidence(
    rows: Sequence[Mapping[str, Any]], stock_code: str, business_date: date
) -> tuple[Mapping[str, Any], ...]:
    output = []
    for row in rows:
        digest = str(row.get("source_sha256", "")).removeprefix("sha256:")
        if (
            row.get("stock_code") != stock_code
            or row.get("business_date") != business_date.isoformat()
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise Native5mLedgerError("EVIDENCE_IDENTITY_INVALID")
        _clock(row.get("acquired_at"))
        output.append(row)
    return tuple(output)


def _activity(rows: Sequence[Mapping[str, Any]]) -> bool | None | str:
    values = set()
    for row in rows:
        try:
            volume, amount = Decimal(str(row["volume"])), Decimal(str(row["amount"]))
        except (KeyError, InvalidOperation) as exc:
            raise Native5mLedgerError("DAILY_EVIDENCE_VALUE_INVALID") from exc
        if not volume.is_finite() or not amount.is_finite() or volume < 0 or amount < 0:
            raise Native5mLedgerError("DAILY_EVIDENCE_VALUE_INVALID")
        values.add(volume > 0 or amount > 0)
    if len(values) > 1:
        return "conflict"
    return next(iter(values)) if values else None


def classify_security_day(
    *,
    venue: str,
    stock_code: str,
    business_date: date,
    row_count: int,
    distinct_buckets: int,
    invalid_buckets: int,
    invalid_values: int,
    status_evidence: Sequence[Mapping[str, Any]] = (),
    daily_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Classify one frozen denominator unit without removing unresolved gaps."""
    if (
        venue not in VENUES
        or not isinstance(stock_code, str)
        or len(stock_code) != 6
        or not stock_code.isascii()
        or not stock_code.isdigit()
        or not isinstance(business_date, date)
        or hasattr(business_date, "hour")
        or any(
            type(value) is not int or value < 0
            for value in (row_count, distinct_buckets, invalid_buckets, invalid_values)
        )
        or distinct_buckets > row_count
    ):
        raise Native5mLedgerError("REQUIRED_UNIT_INVALID")
    statuses = _evidence(status_evidence, stock_code, business_date)
    daily = _evidence(daily_evidence, stock_code, business_date)
    status_values = {row.get("tradestatus") for row in statuses}
    if not status_values <= {"0", "1"}:
        raise Native5mLedgerError("STATUS_EVIDENCE_VALUE_INVALID")
    status_conflict = len(status_values) > 1
    tradable = None if not status_values or status_conflict else next(iter(status_values)) == "1"
    activity = _activity(daily)
    key = {
        "venue": venue,
        "stock_code": stock_code,
        "business_date": business_date.isoformat(),
    }
    if row_count:
        exact = (
            row_count == 48
            and distinct_buckets == 48
            and invalid_buckets == 0
            and invalid_values == 0
        )
        result = {
            **key,
            "disposition": "OBSERVED" if exact else "UNRESOLVED",
            "rating": "R2" if exact else "R3",
            "reason": (
                "native_48_bucket_grid"
                if exact
                else "observed_semantic_defect"
                if invalid_buckets or invalid_values
                else "observed_bucket_gap"
            ),
        }
        if exact and tradable is False:
            result["evidence_warning"] = "status_observation_conflict"
        elif status_conflict:
            result["evidence_warning"] = "status_evidence_conflict"
        return result
    if distinct_buckets or invalid_buckets or invalid_values:
        raise Native5mLedgerError("EMPTY_OBSERVATION_CENSUS_CONFLICT")
    if status_conflict:
        return {
            **key,
            "disposition": "UNRESOLVED",
            "rating": "R3",
            "reason": "status_evidence_conflict",
        }
    if activity == "conflict":
        return {
            **key,
            "disposition": "UNRESOLVED",
            "rating": "R3",
            "reason": "daily_evidence_conflict",
        }
    if tradable is False:
        if activity is True:
            return {
                **key,
                "disposition": "UNRESOLVED",
                "rating": "R3",
                "reason": "status_daily_conflict",
            }
        return {
            **key,
            "disposition": "JUSTIFIED_NO_DATA",
            "rating": None,
            "reason": "daily_status_not_tradable",
        }
    if activity is False:
        return {
            **key,
            "disposition": "JUSTIFIED_NO_DATA",
            "rating": None,
            "reason": "zero_turnover_daily_bar",
        }
    return {
        **key,
        "disposition": "UNRESOLVED",
        "rating": "R3",
        "reason": (
            "missing_despite_daily_activity" if activity is True else "missing_without_evidence"
        ),
    }


def summarize_classifications(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Produce safe aggregate and a deterministic digest while retaining R3 units."""
    counts: Counter[str] = Counter()
    by_month_venue: Counter[tuple[str, str, str]] = Counter()
    seen = set()
    previous = None
    digest = hashlib.sha256()
    for row in rows:
        key = (row["venue"], row["stock_code"], row["business_date"])
        if key in seen:
            raise Native5mLedgerError("DUPLICATE_REQUIRED_KEY")
        if previous is not None and key < previous:
            raise Native5mLedgerError("REQUIRED_KEY_ORDER_INVALID")
        seen.add(key)
        previous = key
        reason = row["reason"]
        rating = row["rating"]
        disposition = row["disposition"]
        if rating not in {None, "R2", "R3"} or disposition not in {
            "OBSERVED",
            "JUSTIFIED_NO_DATA",
            "UNRESOLVED",
        }:
            raise Native5mLedgerError("CLASSIFICATION_INVALID")
        counts[disposition] += 1
        if rating:
            counts[rating] += 1
        if row.get("evidence_warning"):
            counts["evidence_warning"] += 1
        by_month_venue[(row["business_date"][:7], row["venue"], reason)] += 1
        digest.update(
            json.dumps(
                [*key, disposition, rating, reason, row.get("evidence_warning")],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
    return {
        "contract": CONTRACT,
        "required_security_days": len(seen),
        "observed_R2_security_days": counts["R2"],
        "justified_no_data_security_days": counts["JUSTIFIED_NO_DATA"],
        "R3_security_days": counts["R3"],
        "evidence_warning_security_days": counts["evidence_warning"],
        "default_native5m_product_pass": bool(seen) and counts["R3"] == 0,
        "classification_digest_sha256": digest.hexdigest(),
        "month_venue_reason_counts": [
            {"month": month, "venue": venue, "reason": reason, "units": units}
            for (month, venue, reason), units in sorted(by_month_venue.items())
        ],
    }
