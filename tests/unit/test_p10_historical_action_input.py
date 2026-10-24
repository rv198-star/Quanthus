"""A new exact action may change only its declared reference fields."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import p10_historical_action_input as p
from quanthus.pipelines.securities_warehouse.p10_historical_action_source import ActionFacts


@pytest.fixture
def facts():
    return (
        ActionFacts(
            "123456",
            date(2012, 5, 30),
            date(2012, 5, 31),
            date(2012, 5, 31),
            date(2012, 5, 31),
            date(2012, 5, 31),
            Decimal("0.25"),
            Decimal("0.50"),
            datetime(2026, 10, 24, 16, tzinfo=timezone.utc),
            "synthetic-source",
            "a" * 64,
            "b" * 64,
        ),
    )


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / "original.parquet"
    table = pa.table(
        {
            "security_id": ["123456", "other"],
            "execution_date": [date(2012, 5, 31)] * 2,
            "price_reference": pa.array([Decimal("12.3400")] * 2, type=pa.decimal128(18, 4)),
            "price_reference_kind": ["prior_trading_close"] * 2,
            "upper_limit_pct": pa.array([Decimal("10.00")] * 2, type=pa.decimal128(18, 2)),
            "lower_limit_pct": pa.array([Decimal("10.00")] * 2, type=pa.decimal128(18, 2)),
            "upper_limit_price": pa.array([Decimal("13.57")] * 2, type=pa.decimal128(18, 2)),
            "lower_limit_price": pa.array([Decimal("11.11")] * 2, type=pa.decimal128(18, 2)),
            "knowledge_cutoff": pa.array(
                [datetime(2026, 7, 29, tzinfo=timezone.utc)] * 2, type=pa.timestamp("us", tz="UTC")
            ),
            "revision": ["original"] * 2,
            "observed_tradestatus": ["1", "0"],
            "is_executable": [True, False],
            "reference_exact_numerator": pa.array([None, None], type=pa.string()),
            "reference_exact_denominator": pa.array([None, None], type=pa.string()),
            "exact_precision": pa.array([Decimal("1.23000000001")] * 2, type=pa.decimal128(24, 11)),
        }
    )
    pq.write_table(table, source)
    return source, tmp_path / "output.parquet"


def test_bounded_native_rewrite_exact_ratio_keeps_source_and_other_values(inputs, facts):
    source, target = inputs
    before = source.read_bytes()
    assert p.rewrite_limits(source, target, facts) == {
        "rows": 2,
        "changed_keys": 1,
        "unchanged_rows": 1,
    }
    assert source.read_bytes() == before
    out = pq.read_table(target).to_pylist()
    assert out[0]["reference_exact_numerator"] == "403"
    assert out[0]["reference_exact_denominator"] == "50"
    assert out[0]["price_reference"] == Decimal("8.0600")
    assert out[0]["upper_limit_price"] == Decimal("8.87")
    assert out[1] == pq.read_table(source).to_pylist()[1]


@pytest.mark.parametrize(
    "column,index,value",
    [
        ("upper_limit_price", 1, Decimal("10.00")),
        ("upper_limit_price", 0, Decimal("8.88")),
        ("is_executable", 1, True),
        ("exact_precision", 0, Decimal("1.23000000002")),
        ("reference_exact_numerator", 1, "1"),
        ("revision", 0, "forged"),
    ],
)
def test_full_resealed_parquet_rejects_undeclared_or_wrong_diffs(
    inputs, facts, column, index, value
):
    source, target = inputs
    p.rewrite_limits(source, target, facts)
    t = pq.read_table(target)
    i = t.schema.get_field_index(column)
    values = t[column].to_pylist()
    values[index] = value
    pq.write_table(
        t.set_column(i, t.schema.field(i), pa.array(values, type=t.schema.field(i).type)), target
    )
    with pytest.raises(ValueError, match="UNAPPROVED_VALUE_CHANGE"):
        p.rewrite_limits(source, target, facts, verify_only=True)


def test_no_original_alias_overwrite_or_duplicate_selection(inputs, facts):
    source, target = inputs
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        p.rewrite_limits(source, source, facts)
    with pytest.raises(ValueError, match="DUPLICATE_KEY"):
        p.rewrite_limits(source, target, facts + facts)
