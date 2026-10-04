"""Safe, exact representability evidence; never change a frozen physical schema.

Profiles contain hashes and digit counts, not the underlying financial values.
Decimal tuple arithmetic avoids context-dependent normalize/quantize rounding.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any


def decimal_profile(value: Any, *, precision: int, scale: int) -> dict[str, Any]:
    """Classify exact Decimal fit, separating unsupported precision from bad values."""
    if not 0 <= scale <= precision or precision < 1:
        raise ValueError("DECIMAL_TARGET_INVALID")
    result: dict[str, Any] = {"input_type": type(value).__name__}
    if value is None or isinstance(value, str) and not value.strip():
        return {**result, "classification": "NULL", "fits": None}
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal, float)):
        return {**result, "classification": "INVALID_TYPE", "fits": False}
    if isinstance(value, float):
        return {
            **result,
            "classification": "FLOAT_ORIGINAL_DECIMAL_UNAVAILABLE"
            if math.isfinite(value)
            else "NONFINITE",
            "fits": False,
        }
    raw = str(value)
    result["raw_utf8_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    try:
        number = Decimal(raw.strip())
    except InvalidOperation:
        return {**result, "classification": "INVALID_NUMBER", "fits": False}
    if not number.is_finite():
        return {**result, "classification": "NONFINITE", "fits": False}
    parts = number.as_tuple()
    exponent = int(parts.exponent)
    digits = list(parts.digits)
    raw_scale = max(0, -exponent)
    raw_precision = len(digits)
    if number.is_zero():
        integer_digits = minimal_scale = 0
    else:
        integer_digits = max(0, len(digits) + exponent)
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        minimal_scale = max(0, -exponent)
    fits = minimal_scale <= scale and integer_digits <= precision - scale
    return {
        **result,
        "classification": "EXACTLY_REPRESENTABLE" if fits else "FINITE_SCHEMA_LIMIT",
        "fits": fits,
        "zero": number.is_zero(),
        "negative": number.is_signed() and not number.is_zero(),
        "raw_precision_digits": raw_precision,
        "raw_scale": raw_scale,
        "integer_digits": integer_digits,
        "minimal_exact_scale": minimal_scale,
        "losslessly_removable_fractional_zeros": raw_scale - minimal_scale,
        "minimal_fixed_precision": max(1, integer_digits + minimal_scale),
    }


def summarize_profiles(
    profiles: Iterable[dict[str, Any]], *, precision: int, scale: int
) -> dict[str, Any]:
    """Compute a proposal bound preserving the old schema's integer capacity.

    A sample-only smaller integer range is reported separately; it is not an
    additive-compatible widening. Neither proposal authorizes a schema change.
    """
    if not 0 <= scale <= precision or precision < 1:
        raise ValueError("DECIMAL_TARGET_INVALID")
    counts: Counter[str] = Counter()
    scales: Counter[str] = Counter()
    integers: Counter[str] = Counter()
    raw_scales: Counter[str] = Counter()
    maximum_scale = maximum_integer = finite = removable = 0
    for profile in profiles:
        classification = profile["classification"]
        counts[classification] += 1
        if classification not in {"EXACTLY_REPRESENTABLE", "FINITE_SCHEMA_LIMIT"}:
            continue
        finite += 1
        s, i = profile["minimal_exact_scale"], profile["integer_digits"]
        maximum_scale, maximum_integer = max(maximum_scale, s), max(maximum_integer, i)
        scales[str(s)] += 1
        integers[str(i)] += 1
        raw_scales[str(profile["raw_scale"])] += 1
        removable += profile["losslessly_removable_fractional_zeros"] > 0
    widened_scale = max(scale, maximum_scale)
    widened_precision = max(precision - scale, maximum_integer) + widened_scale
    return {
        "target": {"precision": precision, "scale": scale},
        "rows": sum(counts.values()),
        "classifications": dict(sorted(counts.items())),
        "finite_rows": finite,
        "raw_scale_counts": dict(sorted(raw_scales.items(), key=lambda x: int(x[0]))),
        "minimal_exact_scale_counts": dict(sorted(scales.items(), key=lambda x: int(x[0]))),
        "integer_digit_counts": dict(sorted(integers.items(), key=lambda x: int(x[0]))),
        "losslessly_removable_fractional_zero_rows": removable,
        "maximum_minimal_exact_scale": maximum_scale,
        "maximum_integer_digits": maximum_integer,
        "sample_only_minimal_type": {
            "precision": max(1, maximum_integer + maximum_scale),
            "scale": maximum_scale,
            "preserves_previous_integer_capacity": maximum_integer >= precision - scale,
        },
        "additive_capacity_preserving_proposal": {
            "precision": widened_precision,
            "scale": widened_scale,
            "fits_decimal128": widened_precision <= 38,
            "fits_decimal256": widened_precision <= 76,
            "authorized": False,
        },
        "no_rounding_or_schema_change": True,
    }
