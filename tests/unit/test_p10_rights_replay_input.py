"""The native rewrite changes one declared reference, preserving every raw field."""

from datetime import date
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse import p10_rights_replay_input as p


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / "original.parquet"
    table = pa.table(
        {
            "security_id": ["other", "000901", "000901"],
            "execution_date": [date(2014, 10, 24), date(2014, 10, 24), date(2014, 10, 27)],
            "price_reference": pa.array([Decimal("10.0000")] * 3, type=pa.decimal128(18, 4)),
            "price_reference_kind": ["original"] * 3,
            "lower_limit_price": pa.array([Decimal("9.00")] * 3, type=pa.decimal128(18, 2)),
            "upper_limit_price": pa.array([Decimal("11.00")] * 3, type=pa.decimal128(18, 2)),
            "knowledge_cutoff": [date(2026, 7, 29)] * 3,
            "revision": ["original"] * 3,
            "observed_tradestatus": ["1", "1", "0"],
            "is_executable": [True, True, False],
            "raw_precision": pa.array([Decimal("1.23000000001")] * 3, type=pa.decimal128(22, 11)),
        }
    )
    pq.write_table(table, source)
    reference = {
        "security_code": "000901",
        "execution_date": "2014-10-24",
        "reference_numerator": "1001",
        "reference_denominator": "120",
        "lower_limit_price": "7.51",
        "upper_limit_price": "9.18",
        "source_information_cutoff": "2026-10-24T13:13:38.139930+00:00",
    }
    return source, tmp_path / "effective.parquet", reference


def test_one_native_reference_changes_with_exact_fraction_and_raw_precision_kept(inputs):
    source, target, ref = inputs
    before = source.read_bytes()
    stats = p.rewrite_limits(source, target, ref)
    assert stats == {"rows": 3, "changed_keys": 1, "all_other_original_values_checked": 2}
    assert source.read_bytes() == before
    rows = pq.read_table(target).to_pylist()
    assert rows[1]["reference_exact_numerator"] == "1001"
    assert rows[1]["reference_exact_denominator"] == "120"
    assert rows[1]["price_reference"] == Decimal("8.3417")
    assert rows[1]["lower_limit_price"] == Decimal("7.51")
    assert all(str(r["raw_precision"]) == "1.23000000001" for r in rows)
    assert rows[2]["is_executable"] is False
    assert rows[0]["reference_exact_numerator"] is None
    assert p.verify_limits(source, target, ref) == stats


@pytest.mark.parametrize(
    "column,index,value",
    [
        ("upper_limit_price", 1, Decimal("9.19")),
        ("upper_limit_price", 0, Decimal("9.18")),
        ("observed_tradestatus", 1, "0"),
        ("is_executable", 1, False),
        ("knowledge_cutoff", 1, date(2014, 10, 24)),
        ("reference_exact_numerator", 1, "1002"),
        ("reference_exact_denominator", 0, "120"),
        ("raw_precision", 2, Decimal("1.23000000002")),
    ],
)
def test_fully_rewritten_valid_parquet_cannot_hide_unauthorized_diff(inputs, column, index, value):
    source, target, ref = inputs
    p.rewrite_limits(source, target, ref)
    table = pq.read_table(target)
    i = table.schema.get_field_index(column)
    values = table[column].to_pylist()
    values[index] = value
    table = table.set_column(
        i, table.schema.field(i), pa.array(values, type=table.schema.field(i).type)
    )
    pq.write_table(table, target)
    with pytest.raises(ValueError, match="UNAPPROVED_VALUE_CHANGE|RATIONAL_CLOSURE"):
        p.verify_limits(source, target, ref)


@pytest.mark.parametrize(
    "kind", ["missing-key", "duplicate-key", "missing-row", "reorder", "schema"]
)
def test_native_physical_key_row_order_and_schema_fail_closed(inputs, kind):
    source, target, ref = inputs
    table = pq.read_table(source)
    if kind in {"missing-key", "duplicate-key"}:
        table = table.take(pa.array([0, 2] if kind == "missing-key" else [0, 1, 1, 2]))
        pq.write_table(table, source)
        with pytest.raises(ValueError, match="REPLACEMENT_KEY_COUNT"):
            p.rewrite_limits(source, target, ref)
        return
    p.rewrite_limits(source, target, ref)
    out = pq.read_table(target)
    if kind == "missing-row":
        out = out.slice(0, 2)
    if kind == "reorder":
        out = out.take(pa.array([1, 0, 2]))
    if kind == "schema":
        out = out.drop(["reference_exact_denominator"])
    pq.write_table(out, target)
    with pytest.raises(ValueError):
        p.verify_limits(source, target, ref)


def test_no_overwrite_and_no_original_alias(inputs):
    source, target, ref = inputs
    before = source.read_bytes()
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        p.rewrite_limits(source, source, ref)
    target.symlink_to(source)
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        p.rewrite_limits(source, target, ref)
    assert source.read_bytes() == before


def test_scanning_error_is_not_a_complete_file_set(tmp_path, monkeypatch):
    def denied(root, onerror):
        onerror(PermissionError("synthetic scan failure"))
        yield str(root), [], []

    monkeypatch.setattr(p.os, "walk", denied)
    with pytest.raises(PermissionError, match="synthetic"):
        p._files(tmp_path)


def test_output_overlap_is_rejected_before_any_mkdir(tmp_path):
    roots = [tmp_path / name for name in ["parent", "issuer", "rule", "product"]]
    for root in roots:
        root.mkdir()
        (root / "sentinel").write_bytes(b"original")
    for target in [roots[0], roots[0] / "nested", tmp_path]:
        with pytest.raises(ValueError, match="OUTPUT_ISOLATION"):
            p.materialize(*roots, target)
    assert not (roots[0] / "nested").exists()
    assert all((r / "sentinel").read_bytes() == b"original" for r in roots)
