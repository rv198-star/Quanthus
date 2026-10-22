from decimal import Decimal, localcontext

import pytest

from quanthus.pipelines.securities_warehouse.base22_precision import (
    decimal_profile,
    summarize_profiles,
)


@pytest.mark.parametrize(
    ("raw", "classification", "fits"),
    [
        (None, "NULL", None),
        (" ", "NULL", None),
        (True, "INVALID_TYPE", False),
        ({}, "INVALID_TYPE", False),
        ("unknown", "INVALID_NUMBER", False),
        ("NaN", "NONFINITE", False),
        ("Infinity", "NONFINITE", False),
        (float("nan"), "NONFINITE", False),
        (1.25, "FLOAT_ORIGINAL_DECIMAL_UNAVAILABLE", False),
        ("1.23456789012", "FINITE_SCHEMA_LIMIT", False),
        ("1.2345678901000", "EXACTLY_REPRESENTABLE", True),
        ("9" * 28 + "." + "9" * 10, "EXACTLY_REPRESENTABLE", True),
        ("9" * 29, "FINITE_SCHEMA_LIMIT", False),
        ("0E-10000", "EXACTLY_REPRESENTABLE", True),
        ("1e10000", "FINITE_SCHEMA_LIMIT", False),
        (-12, "EXACTLY_REPRESENTABLE", True),
        (Decimal("1.2"), "EXACTLY_REPRESENTABLE", True),
    ],
)
def test_exact_representability_without_silent_conversion(raw, classification, fits):
    result = decimal_profile(raw, precision=38, scale=10)
    assert result["classification"] == classification
    assert result["fits"] is fits
    assert "raw_value" not in result


def test_analysis_does_not_normalize_using_a_rounding_context():
    raw = "1234567890123456789012345678.123456789120000"
    with localcontext() as context:
        context.prec = 4
        result = decimal_profile(raw, precision=38, scale=10)
    assert result["integer_digits"] == 28
    assert result["minimal_exact_scale"] == 11
    assert result["losslessly_removable_fractional_zeros"] == 4
    assert result["classification"] == "FINITE_SCHEMA_LIMIT"


def test_fractional_leading_zeros_are_part_of_fixed_scale():
    result = decimal_profile("0.0000012300", precision=10, scale=8)
    assert result["integer_digits"] == 0
    assert result["minimal_exact_scale"] == 8
    assert result["minimal_fixed_precision"] == 8
    assert result["fits"] is True


def test_sample_fit_is_not_an_additive_compatible_schema_change():
    profile = decimal_profile("123.123456789012", precision=38, scale=10)
    summary = summarize_profiles([profile], precision=38, scale=10)
    assert summary["sample_only_minimal_type"]["precision"] == 15
    assert not summary["sample_only_minimal_type"]["preserves_previous_integer_capacity"]
    assert summary["additive_capacity_preserving_proposal"] == {
        "precision": 40,
        "scale": 12,
        "fits_decimal128": False,
        "fits_decimal256": True,
        "authorized": False,
    }


@pytest.mark.parametrize(("precision", "scale"), [(0, 0), (3, 4), (3, -1)])
def test_invalid_target_rejected(precision, scale):
    with pytest.raises(ValueError, match="DECIMAL_TARGET_INVALID"):
        decimal_profile("1", precision=precision, scale=scale)
    with pytest.raises(ValueError, match="DECIMAL_TARGET_INVALID"):
        summarize_profiles([], precision=precision, scale=scale)
