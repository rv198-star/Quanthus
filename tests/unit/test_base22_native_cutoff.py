from datetime import date, datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import (
    BoundedError,
    Budget,
    GlobalScan,
    Limits,
)
from quanthus.pipelines.securities_warehouse.base22_native_keys import NativeGlobalScan


def check(tmp_path, values, datatype, scan_type, *, nullable=True, interval=False):
    fields = [pa.field("at", datatype, nullable=nullable)]
    arrays = [pa.array(values, type=datatype)]
    if interval:
        fields += [pa.field("knowledge_from", datatype), pa.field("knowledge_to", datatype)]
        arrays += [pa.array(values, type=datatype), pa.array(values, type=datatype)]
    schema = pa.schema(fields)
    # The row-rule unit can apply a stricter required-field schema to nullable
    # storage; the separate image schema gate also rejects that physical drift.
    storage_schema = pa.schema([pa.field(f.name, f.type) for f in fields])
    queries = []
    with Budget(
        tmp_path / scan_type.__name__ / "audit",
        [],
        Limits(free_floor_bytes=1, allocated_bytes=64 * 1024**2, temp_bytes=32 * 1024**2),
    ) as budget, scan_type(budget) as scan:
        path = budget.root / "part.parquet"
        pq.write_table(pa.Table.from_arrays(arrays, schema=storage_schema), path)
        scan.add("t", [path])
        original = scan.reject

        def observe(query, reason):
            queries.append(query)
            return original(query, reason)

        scan.reject = observe
        scan.row_rules("t", schema, {"cutoff_field": "at"})
    return queries


def test_native_timestamp_cutoff_uses_constant_instant_not_per_row_timezone(tmp_path):
    queries = check(
        tmp_path,
        [datetime(2026, 8, 31, 15, 59, 59, 999999, tzinfo=timezone.utc)],
        pa.timestamp("us", tz="UTC"),
        NativeGlobalScan,
    )
    assert all("timezone(" not in query for query in queries)
    assert any("TIMESTAMPTZ" in query and ">=" in query for query in queries)


@pytest.mark.parametrize("tz", ["UTC", "Asia/Shanghai"])
@pytest.mark.parametrize("hour,valid", [(15, True), (16, False)])
def test_native_and_legacy_have_same_shanghai_cutoff_verdict(tmp_path, tz, hour, valid):
    values = [datetime(2026, 8, 31, hour, tzinfo=timezone.utc)]
    for scan in [GlobalScan, NativeGlobalScan]:
        if valid:
            check(tmp_path, values, pa.timestamp("us", tz=tz), scan)
        else:
            with pytest.raises(BoundedError, match="BUSINESS_DATE_AFTER_CUTOFF"):
                check(tmp_path, values, pa.timestamp("us", tz=tz), scan)


@pytest.mark.parametrize("value,valid", [(date(2026, 8, 31), True), (date(2026, 9, 1), False)])
def test_date_columns_keep_same_business_cutoff(tmp_path, value, valid):
    for scan in [GlobalScan, NativeGlobalScan]:
        if valid:
            check(tmp_path, [value], pa.date32(), scan)
        else:
            with pytest.raises(BoundedError, match="BUSINESS_DATE_AFTER_CUTOFF"):
                check(tmp_path, [value], pa.date32(), scan)


def test_optional_unknown_and_required_unknown_keep_legacy_verdict(tmp_path):
    for scan in [GlobalScan, NativeGlobalScan]:
        check(tmp_path / "optional", [None], pa.timestamp("us", tz="UTC"), scan)
        with pytest.raises(BoundedError, match="REQUIRED_FIELD_UNKNOWN"):
            check(tmp_path / "required", [None], pa.timestamp("us", tz="UTC"), scan, nullable=False)


def test_interval_checks_remain_independent_of_cutoff_projection(tmp_path):
    for scan in [GlobalScan, NativeGlobalScan]:
        with pytest.raises(BoundedError, match="INVALID_TIME_INTERVAL"):
            check(
                tmp_path,
                [datetime(2026, 8, 31, 15, tzinfo=timezone.utc)],
                pa.timestamp("us", tz="UTC"),
                scan,
                interval=True,
            )
