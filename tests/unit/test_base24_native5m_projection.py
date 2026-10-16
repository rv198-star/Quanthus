"""Base 2.4 projection keeps exact source values and excludes incidental dates."""

from datetime import datetime, timezone
from decimal import Decimal

import duckdb
import pyarrow as pa

from quanthus.pipelines.securities_warehouse.base24_native5m_projection import (
    projection_query,
)
from quanthus.providers.native_5m_exact import SHANGHAI


def test_projection_keeps_only_frozen_missing_dates_and_actual_acquisition():
    connection = duckdb.connect()
    exact = pa.Table.from_pylist(
        [
            {
                "code": "sz.000001",
                "timestamp": datetime(2020, 1, day, 9, 35, tzinfo=SHANGHAI),
                "source_values": ["1.000", "2.00", "0.50", "1.5", "10", "20.000"],
                "open": Decimal("1.000"),
                "high": Decimal("2.00"),
                "low": Decimal("0.50"),
                "close": Decimal("1.5"),
                "volume": Decimal("10"),
                "amount": Decimal("20.000"),
                "filename": "/source/a.parquet",
            }
            for day in (2, 3)
        ]
    )
    identity = pa.Table.from_pylist(
        [
            {
                "filename": "/source/a.parquet",
                "partition_id": "a" * 64,
                "source_artifact_sha256": "b" * 64,
                "acquired_at": "2026-10-16T06:39:37+00:00",
            }
        ]
    )
    required = pa.Table.from_pylist(
        [{"stock_code": "000001", "trade_date": datetime(2020, 1, 3).date()}]
    )
    connection.register("exact_rows", exact)
    connection.register("source_identity", identity)
    connection.register("required_missing", required)
    try:
        rows = connection.execute(projection_query()).to_arrow_table().to_pylist()
    finally:
        connection.close()
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "000001"
    assert rows[0]["market_date"].isoformat() == "2020-01-03"
    assert rows[0]["ts"] == datetime(2020, 1, 3, 9, 35)
    assert rows[0]["source_values"] == ["1.000", "2.00", "0.50", "1.5", "10", "20.000"]
    assert rows[0]["acquired_at"].astimezone(timezone.utc).isoformat() == (
        "2026-10-16T06:39:37+00:00"
    )
    assert rows[0]["bar_size"] == "5m"
    assert rows[0]["input_partition_id"] == "a" * 64


def test_projection_rejects_missing_or_naive_acquisition_clock():
    connection = duckdb.connect()
    connection.execute(
        """
        CREATE TABLE exact_rows AS SELECT 'sz.000001' code,
          TIMESTAMPTZ '2020-01-02 09:35:00+08:00' AS "timestamp",
          ['1','1','1','1','0','0'] source_values,
          1::DECIMAL(2,0) open,1::DECIMAL(2,0) high,1::DECIMAL(2,0) low,
          1::DECIMAL(2,0) AS "close",0::DECIMAL(2,0) volume,0::DECIMAL(2,0) amount,
          '/source/a.parquet' filename
        """
    )
    connection.execute(
        """
        CREATE TABLE source_identity AS SELECT '/source/a.parquet' filename,
          repeat('a',64) partition_id,repeat('b',64) source_artifact_sha256,
          NULL::VARCHAR acquired_at
        """
    )
    connection.execute(
        "CREATE TABLE required_missing AS SELECT '000001' stock_code, DATE '2020-01-02' trade_date"
    )
    try:
        try:
            connection.execute(projection_query()).fetchall()
        except duckdb.Error as exc:
            assert "BASE24_NATIVE5M_ACQUIRED_AT_INVALID" in str(exc)
        else:
            raise AssertionError("missing acquisition clock must fail")
    finally:
        connection.close()
