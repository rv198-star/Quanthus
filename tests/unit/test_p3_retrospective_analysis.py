from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from quanthus.expert_strategy.p3_retrospective_analysis import (
    VARIANTS,
    analyze_returns,
    classify_headline,
    path_metrics,
)


def test_compounding_and_drawdown_include_initial_capital():
    result = path_metrics([D("-0.1"), D("0.2"), D("-0.25")])
    assert result == {"net_total_return": D("-0.190"), "maximum_drawdown": D("0.25")}


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "-2"])
def test_invalid_returns_fail_closed(bad):
    with pytest.raises(ValueError, match="INVALID_RETURN"):
        path_metrics([D(bad)])


def test_reduced_drawdown_does_not_support_increment_when_return_loss_is_material():
    result = classify_headline(
        {"net_total_return": D("-0.04"), "maximum_drawdown": D("-0.03")},
        {
            "net_total_return": {"lower": D("-0.1"), "upper": D("0.1")},
            "maximum_drawdown": {"lower": D("-0.08"), "upper": D("-0.01")},
        },
    )
    assert result["drawdown_increment_supported"] is False
    assert result["disposition"] == "INCONCLUSIVE_OR_ECONOMICALLY_NEGLIGIBLE"


@pytest.mark.parametrize("lower,upper,supported", [("0", "0.2", False), ("0.0001", "0.2", True)])
def test_return_support_requires_strictly_positive_interval(lower, upper, supported):
    result = classify_headline(
        {"net_total_return": D("0.01"), "maximum_drawdown": D("0.01")},
        {
            "net_total_return": {"lower": D(lower), "upper": D(upper)},
            "maximum_drawdown": {"lower": D("0"), "upper": D("0.1")},
        },
    )
    assert result["return_increment_supported"] is supported


def data(count=23):
    return {
        v: [
            {"market_date": str(date(2025, 1, 1) + timedelta(days=i)), "return": "0.01"}
            for i in range(count)
        ]
        for v in VARIANTS
    }


def points(series):
    return {v: path_metrics([D(row["return"]) for row in rows]) for v, rows in series.items()}


def test_paired_bootstrap_exact_seed_determinism_and_incomplete_block_disclosure():
    series = data()
    result = analyze_returns("frozen-id", series, points(series))
    assert result == analyze_returns("frozen-id", series, points(series))
    assert result["full_window_sessions"] == 23
    assert result["complete_block_count"] == 1
    assert result["bootstrap_excluded_trailing_dates"] == ["2025-01-23"]
    for contrast in result["contrasts"].values():
        assert all(D(n) == 0 for n in contrast["point_delta"].values())
        for interval in contrast["paired_block_95_interval"].values():
            assert D(interval["lower"]) == D(interval["upper"]) == 0
    assert result["original_D_activated"] is False


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("missing", "VARIANT_SET"),
        ("mismatch", "PAIRED_DATES"),
        ("duplicate", "PAIRED_DATES"),
        ("outside", "WINDOW"),
        ("short", "NO_COMPLETE_BLOCK"),
    ],
)
def test_unpaired_or_unauthorized_scope_rejected(mutation, reason):
    series = data(2 if mutation == "short" else 23)
    if mutation == "missing":
        del series["T"]
    elif mutation == "mismatch":
        series["T"][0]["market_date"] = "2025-01-02"
    elif mutation == "duplicate":
        for rows in series.values():
            rows[0]["market_date"] = rows[1]["market_date"]
    elif mutation == "outside":
        for rows in series.values():
            rows[-1]["market_date"] = "2026-09-19"
    with pytest.raises(ValueError, match=reason):
        analyze_returns("frozen-id", series, points(series))


def test_sign_is_intervention_minus_baseline_and_not_difference_of_mean_returns():
    series = data(22)
    for v in ("T", "TR"):
        for row in series[v]:
            row["return"] = "0.02"
    result = analyze_returns("frozen-id", series, points(series))
    tr = result["contrasts"]["TR-B"]
    exact = D("1.02") ** 22 - D("1.01") ** 22
    assert abs(D(tr["point_delta"]["net_total_return"]) - exact) < D("1e-25")
    assert D(tr["paired_block_95_interval"]["net_total_return"]["lower"]) > 0
    assert tr["decision"]["return_increment_supported"] is True
    assert result["contrasts"]["T-B"]["role"] == "diagnostic_only"
    assert D(result["contrasts"]["TR-T-R+B"]["point_delta"]["net_total_return"]) == 0
