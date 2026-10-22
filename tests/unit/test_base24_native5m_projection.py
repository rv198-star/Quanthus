"""Base 2.4 projection keeps exact source values and excludes incidental dates."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa

from quanthus.pipelines.securities_warehouse.base24_native5m_projection import (
    Native5mProjectionError,
    audit_projection,
    projection_query,
    seal_projection,
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


def _package(root: Path) -> None:
    rows = (
        [(9, minute) for minute in range(35, 60, 5)]
        + [(10, minute) for minute in range(0, 60, 5)]
        + [(11, minute) for minute in range(0, 31, 5)]
        + [(13, minute) for minute in range(5, 60, 5)]
        + [(14, minute) for minute in range(0, 60, 5)]
        + [(15, 0)]
    )
    table = pa.Table.from_pylist(
        [
            {
                "stock_code": "000001",
                "ts": datetime(2020, 1, 2, hour, minute),
                "market_date": datetime(2020, 1, 2).date(),
                "bar_size": "5m",
                "source_values": ["1", "1", "1", "1", "0", "0"],
                "open": Decimal("1.0000000000"),
                "high": Decimal("1.0000000000"),
                "low": Decimal("1.0000000000"),
                "close": Decimal("1.0000000000"),
                "volume": Decimal("0E-10"),
                "amount": Decimal("0E-10"),
                "acquired_at": datetime(2026, 10, 16, tzinfo=timezone.utc),
                "input_partition_id": "a" * 64,
                "input_artifact_sha256": "sha256:" + "b" * 64,
                "observation_year": 2020,
                "observation_month": 1,
            }
            for hour, minute in rows
        ]
    )
    connection = duckdb.connect()
    connection.register("projected", table)
    try:
        connection.execute(
            f"""COPY (
              SELECT stock_code,ts::TIMESTAMP ts,market_date,bar_size,source_values,
                     open::DECIMAL(38,10) open,high::DECIMAL(38,10) high,
                     low::DECIMAL(38,10) low,"close"::DECIMAL(38,10) "close",
                     volume::DECIMAL(38,10) volume,amount::DECIMAL(38,10) amount,
                     acquired_at::TIMESTAMPTZ acquired_at,input_partition_id,
                     input_artifact_sha256,observation_year,observation_month
              FROM projected
            ) TO '{root / "payload"}' """
            "(FORMAT PARQUET,PARTITION_BY(observation_year,observation_month),COMPRESSION ZSTD)"
        )
    finally:
        connection.close()
    (root / "REGISTRATION.json").write_text(json.dumps({"contract": "test"}) + "\n")


def test_sealed_projection_cold_audits_and_rejects_extra_physical_member(tmp_path):
    _package(tmp_path)
    manifest = seal_projection(
        tmp_path,
        input_identity={"plan_SHA256": "a" * 64, "ledger_manifest_SHA256": "b" * 64},
        code_revision="test",
    )
    assert manifest["summary"]["rows"] == 48
    assert manifest["summary"]["security_days"] == 1
    assert audit_projection(tmp_path) == manifest
    (tmp_path / "payload/extra.txt").write_text("unexpected")
    try:
        audit_projection(tmp_path)
    except Native5mProjectionError as exc:
        assert str(exc) == "PROJECTION_PHYSICAL_FILE_SET_INVALID"
    else:
        raise AssertionError("extra physical member must fail closed")


def test_projection_rejects_incomplete_bucket_day_before_ready(tmp_path):
    _package(tmp_path)
    parquet = next((tmp_path / "payload").rglob("*.parquet"))
    table = __import__("pyarrow.parquet", fromlist=["read_table"]).read_table(parquet)
    __import__("pyarrow.parquet", fromlist=["write_table"]).write_table(table.slice(0, 47), parquet)
    try:
        seal_projection(tmp_path, input_identity={}, code_revision="test")
    except Native5mProjectionError as exc:
        assert str(exc) == "PROJECTION_KEY_OR_BUCKET_CENSUS_INVALID"
    else:
        raise AssertionError("incomplete day must fail closed")
    assert not (tmp_path / "READY").exists()


def test_projection_rejects_row_in_wrong_physical_month(tmp_path):
    _package(tmp_path)
    parquet = next((tmp_path / "payload").rglob("*.parquet"))
    wrong = tmp_path / "payload/observation_year=2020/observation_month=2/data.parquet"
    wrong.parent.mkdir()
    parquet.rename(wrong)
    try:
        seal_projection(tmp_path, input_identity={}, code_revision="test")
    except Native5mProjectionError as exc:
        assert str(exc) == "PROJECTION_ROW_CONTRACT_INVALID"
    else:
        raise AssertionError("physical partition mismatch must fail closed")
