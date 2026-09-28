"""Describe numeric differences, without repairing, choosing or qualifying a source.

The price floor is an explicit diagnostic partition, not a tolerance. Being below
it does not prove floating-point provenance or authorize changing a candidate.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

from quanthus.providers.minute_source_selection import MinuteCandidate

SCHEMA = "quanthus.minute-source-difference.v1"
PRICE_FLOOR = Decimal("1e-10")
FIELDS = ("open", "high", "low", "close", "volume")


def _number(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("difference input must be finite numeric observations")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("difference input must be finite numeric observations") from exc
    if not result.is_finite():
        raise ValueError("difference input must be finite numeric observations")
    return result


def describe_pair(left: MinuteCandidate, right: MinuteCandidate) -> dict[str, Any]:
    """Pure aligned-row description; callers still own exact qualification.

    No candidate is mutated and no projected/corrected candidate is returned.
    Unknown or misaligned input raises instead of guessing a time shift.
    """
    if left.provider == right.provider or len(left.rows) != len(right.rows):
        raise ValueError("difference inputs need distinct providers and aligned rows")
    fields: Counter[str] = Counter()
    small: Counter[str] = Counter()
    larger: Counter[str] = Counter()
    minutes: Counter[str] = Counter()
    volume_minutes = 0
    max_price = Decimal(0)
    max_volume = Decimal(0)
    samples: list[dict[str, Any]] = []
    for a, b in zip(left.rows, right.rows, strict=True):
        if (
            not a.get("ts")
            or not a.get("stock_code")
            or (a["ts"], a["stock_code"]) != (b.get("ts"), b.get("stock_code"))
        ):
            raise ValueError("difference inputs have unmatched minute/security identity")
        changed: dict[str, Any] = {}
        for name in FIELDS:
            first, second = _number(a.get(name)), _number(b.get(name))
            delta = second - first
            if delta == 0:
                continue
            absolute = abs(delta)
            fields[name] += 1
            if name == "volume":
                volume_minutes += 1
                max_volume = max(max_volume, absolute)
            else:
                max_price = max(max_price, absolute)
                (small if absolute < PRICE_FLOOR else larger)[name] += 1
            changed[name] = {
                "left": str(first),
                "right": str(second),
                "right_minus_left": str(delta),
            }
        if changed:
            minutes[str(a["ts"])] += len(changed)
            if len(samples) < 3:
                samples.append({"ts": str(a["ts"]), "fields": changed})
    category = (
        "exact_numeric_equal"
        if not fields
        else "only_price_deltas_below_diagnostic_floor"
        if not larger and not volume_minutes
        else "other_price_or_volume_differences"
    )
    return {
        "schema": SCHEMA,
        "left_provider": left.provider,
        "right_provider": right.provider,
        "compared_rows": len(left.rows),
        "category": category,
        "price_abs_floor": str(PRICE_FLOOR),
        "differing_minutes": len(minutes),
        "field_difference_counts": dict(sorted(fields.items())),
        "small_price_difference_counts": dict(sorted(small.items())),
        "larger_price_difference_counts": dict(sorted(larger.items())),
        "minute_difference_counts": dict(sorted(minutes.items())),
        "volume_difference_minutes": volume_minutes,
        "max_abs_price_difference": str(max_price),
        "max_abs_share_difference": str(max_volume),
        "first_three_differing_minutes": samples,
        "cause": "not_determined_by_numeric_description",
        "gate_changed": False,
        "publication_authorized": False,
    }
