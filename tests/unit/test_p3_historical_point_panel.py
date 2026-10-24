"""Point reads isolate unavailable rows without changing eligible feature values."""

from datetime import date
from decimal import Decimal

import pytest

from quanthus.expert_strategy import p3_historical_point_panel as p


def raw(security="healthy", close=Decimal("10")):
    return {
        "market_date": date(2020, 1, 2),
        "security_id": security,
        "close_price": close,
        **{
            key: Decimal("1.00")
            for key in (
                "total_return_index",
                "sma20",
                "sma60",
                "momentum20",
                "prior_high20",
                "rsi14",
                "rsi14_previous",
                "volume_ratio20",
                "realized_volatility20",
                "daily_total_return",
                "average_amount20",
            )
        },
        "listing_sessions": 100,
        "feature_ready": close is not None,
        "entry_eligible": close is not None,
    }


def probe(rows):
    panel = object.__new__(p.PointPanel)
    calls = []

    def read(query, parameters):
        calls.append(parameters)
        assert "market_date=? AND security_id=?" in query
        return [r for r in rows if (r["market_date"], r["security_id"]) == tuple(parameters)]

    panel._rows = read
    return panel, calls


def test_null_unrequested_security_does_not_block_healthy_key():
    panel, _ = probe([raw(), raw("unavailable", None)])
    expected = p.h.BaselineFeatureRow.from_mapping(raw())
    assert panel.feature(date(2020, 1, 2), "healthy") == expected
    assert panel.feature(date(2020, 1, 2), "unavailable") is None


@pytest.mark.parametrize(
    "missing",
    [None, "sma20", "sma60", "momentum20", "rsi14", "realized_volatility20", "total_return_index"],
)
def test_nonnull_features_exactly_equal_original_with_same_entry_readiness(missing):
    row = raw()
    if missing:
        row[missing] = None
    panel, _ = probe([row])
    value = panel.feature(row["market_date"], row["security_id"])
    expected = p.h.BaselineFeatureRow.from_mapping(row)
    if missing:
        from dataclasses import replace

        expected = replace(expected, entry_eligible=False)
    assert value == expected


def test_repeated_key_converts_once_and_date_change_invalidates_cache():
    row = raw()
    panel, calls = probe([row])
    day = row["market_date"]
    first = panel.feature(day, "healthy")
    assert panel.feature(day, "healthy") is first
    assert len(calls) == 1
    assert panel.feature(date(2020, 1, 3), "healthy") is None
    assert panel.feature(day, "healthy") == first
    assert len(calls) == 3


@pytest.mark.parametrize(
    "close", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), Decimal("0")]
)
def test_selected_invalid_price_still_fails_closed(close):
    panel, _ = probe([raw(close=close)])
    with pytest.raises(ValueError, match="HISTORY_POINT_CLOSE_INVALID"):
        panel.feature(date(2020, 1, 2), "healthy")


def test_duplicate_selected_key_fails_even_without_native_unique_index():
    panel, _ = probe([raw(), raw()])
    with pytest.raises(ValueError, match="HISTORY_FEATURE_DUPLICATE"):
        panel.feature(date(2020, 1, 2), "healthy")


def test_null_price_cannot_be_eligible_and_unavailable_is_counted_once():
    row = raw("unavailable", None)
    panel, _ = probe([row])
    assert panel.feature(row["market_date"], row["security_id"]) is None
    assert panel.feature(row["market_date"], row["security_id"]) is None
    assert panel.unavailable_observations == 1
    row["entry_eligible"] = True
    panel, _ = probe([row])
    with pytest.raises(ValueError, match="HISTORY_POINT_NULL_CLOSE_ELIGIBLE"):
        panel.feature(row["market_date"], row["security_id"])


def test_native_facade_borrows_one_table_and_retains_owner_lifecycle():
    from types import SimpleNamespace

    import duckdb
    import pyarrow as pa

    connection = duckdb.connect(":memory:")
    try:
        table = pa.Table.from_pylist([raw(), raw("unavailable", None)])
        connection.register("synthetic", table)
        connection.execute(
            "CREATE TABLE feature_panel AS SELECT *, false AS exception_active, "
            "false AS unexplained_price_transition, false AS scope_continuity_excluded "
            "FROM synthetic"
        )
        connection.unregister("synthetic")
        connection.execute(
            "CREATE UNIQUE INDEX feature_panel_key ON feature_panel(market_date,security_id)"
        )
        owner = SimpleNamespace(path=None, report=None, _connection=connection)
        panel = p.PointPanel(owner)
        assert panel._connection is owner._connection
        assert connection.execute("SHOW TABLES").fetchall() == [("feature_panel",)]
        assert panel.feature(date(2020, 1, 2), "healthy").close_price == Decimal("10")
        assert panel.feature(date(2020, 1, 2), "unavailable") is None
        assert connection.execute("SELECT count(*) FROM duckdb_indexes()").fetchone()[0] == 2
        assert owner._connection.execute("SELECT count(*) FROM feature_panel").fetchone()[0] == 2
    finally:
        connection.close()
