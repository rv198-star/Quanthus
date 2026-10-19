"""Base 2.4 native 5m DWD projection preserves clocks and session closure."""

from datetime import datetime, timezone
from decimal import Decimal

import duckdb
import pyarrow as pa

from quanthus.pipelines.securities_warehouse.base24_native5m_dwd import (
    Native5mDwdError,
    dwd_native5m_query,
    merged_default_bar_query,
)


def _register(
    connection: duckdb.DuckDBPyConnection,
    *,
    venue: str = "SSE",
    session_type: str = "REGULAR_DAY",
) -> None:
    connection.register(
        "projected_native5m",
        pa.Table.from_pylist(
            [
                {
                    "stock_code": "600000",
                    "ts": datetime(2020, 1, 2, 9, 35),
                    "market_date": datetime(2020, 1, 2).date(),
                    "bar_size": "5m",
                    "open": Decimal("1.0000000000"),
                    "high": Decimal("2.0000000000"),
                    "low": Decimal("0.5000000000"),
                    "close": Decimal("1.5000000000"),
                    "volume": Decimal("10.0000000000"),
                    "amount": Decimal("20.0000000000"),
                    "acquired_at": datetime(2026, 10, 16, tzinfo=timezone.utc),
                    "input_partition_id": "a" * 64,
                    "input_artifact_sha256": "sha256:" + "b" * 64,
                }
            ]
        ),
    )
    connection.execute(
        f"""
        CREATE TABLE dwd_listing AS SELECT 'listing-1' listing_id,'600000' listing_code,
          '{venue}' venue_id,TIMESTAMPTZ '1990-01-01' effective_from,
          NULL::TIMESTAMPTZ effective_to,TIMESTAMPTZ '2019-01-01' knowledge_from,
          NULL::TIMESTAMPTZ knowledge_to
        """
    )
    connection.execute(
        f"""
        CREATE TABLE dwd_trading_calendar AS SELECT 'session-1' calendar_session_id,
          'SSE' venue_id,DATE '2020-01-02' trade_date,'{session_type}' session_type,true is_open,
          TIMESTAMPTZ '2019-01-01' knowledge_from,NULL::TIMESTAMPTZ knowledge_to
        """
    )


def test_native5m_dwd_resolves_date_venue_interval_and_actual_knowledge_time():
    connection = duckdb.connect()
    _register(connection)
    try:
        row = connection.execute(dwd_native5m_query()).to_arrow_table().to_pylist()[0]
    finally:
        connection.close()
    assert row["listing_id"] == "listing-1"
    assert row["calendar_session_id"] == "session-1"
    assert row["currency"] == "CNY"
    assert row["interval_size"] == "5m"
    assert row["observation_start"].astimezone(timezone.utc).minute == 30
    assert row["observation_end"].astimezone(timezone.utc).minute == 35
    assert row["knowledge_from"] == datetime(2026, 10, 16, tzinfo=timezone.utc)


def test_native5m_dwd_wrong_venue_or_date_does_not_resolve():
    for venue in ("SZSE", "SSE"):
        connection = duckdb.connect()
        _register(connection, venue=venue)
        if venue == "SSE":
            connection.execute("UPDATE dwd_trading_calendar SET trade_date=DATE '2020-01-03'")
        try:
            assert connection.execute(dwd_native5m_query()).fetchall() == []
        finally:
            connection.close()


def test_native5m_dwd_explicit_build_knowledge_cutoff_admits_late_listing_fact():
    connection = duckdb.connect()
    _register(connection)
    connection.execute(
        "UPDATE dwd_listing SET knowledge_from="
        "TIMESTAMPTZ '2026-10-14T06:10:33+08:00'"
    )
    try:
        assert connection.execute(
            dwd_native5m_query(knowledge_cutoff="2026-09-30T23:59:59+08:00")
        ).fetchall() == []
        row = connection.execute(
            dwd_native5m_query(knowledge_cutoff="2026-10-19T23:59:59+08:00")
        ).to_arrow_table().to_pylist()[0]
    finally:
        connection.close()
    assert row["listing_id"] == "listing-1"
    assert row["knowledge_from"] == datetime(2026, 10, 16, tzinfo=timezone.utc)


def test_native5m_dwd_accepts_governed_daily_session_alias():
    connection = duckdb.connect()
    _register(connection, session_type="day")
    try:
        row = connection.execute(dwd_native5m_query()).fetchone()
    finally:
        connection.close()
    assert row is not None


def test_native5m_dwd_selects_one_active_calendar_knowledge_version():
    connection = duckdb.connect()
    _register(connection, session_type="day")
    connection.execute(
        "UPDATE dwd_trading_calendar SET knowledge_from="
        "TIMESTAMPTZ '2026-10-09T12:00:00+08:00',knowledge_to="
        "TIMESTAMPTZ '2026-10-09T13:00:00+08:00'"
    )
    connection.execute(
        "INSERT INTO dwd_trading_calendar SELECT 'session-1','SSE',"
        "DATE '2020-01-02','day',true,TIMESTAMPTZ '2026-10-09T13:00:00+08:00',"
        "NULL::TIMESTAMPTZ"
    )
    try:
        rows = connection.execute(
            dwd_native5m_query(knowledge_cutoff="2026-10-19T23:59:59+08:00")
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 1


def test_native5m_dwd_rejects_relation_injection():
    try:
        dwd_native5m_query(projected_relation="x;drop table y")
    except Native5mDwdError as exc:
        assert str(exc) == "RELATION_NAME_INVALID"
    else:
        raise AssertionError("unsafe relation must fail closed")


def test_merge_removes_1m_and_replaces_whole_supplemented_security_day():
    connection = duckdb.connect()
    connection.execute(
        """
        CREATE TABLE predecessor_bar AS
        SELECT * FROM (VALUES
          ('listing-1',TIMESTAMPTZ '2020-01-02 01:30:00+00','1m','old-1m'),
          ('listing-1',TIMESTAMPTZ '2020-01-02 01:30:00+00','5m','old-partial-a'),
          ('listing-1',TIMESTAMPTZ '2020-01-02 01:35:00+00','5m','old-partial-b'),
          ('listing-1',TIMESTAMPTZ '2020-01-03 01:30:00+00','5m','old-good'),
          ('listing-1',TIMESTAMPTZ '2020-01-02 01:30:00+00','1d','old-daily')
        ) t(listing_id,observation_start,interval_size,marker)
        """
    )
    connection.execute(
        """
        CREATE TABLE native5m_supplement AS
        SELECT * FROM (VALUES
          ('listing-1',TIMESTAMPTZ '2020-01-02 01:30:00+00','5m','new-a'),
          ('listing-1',TIMESTAMPTZ '2020-01-02 01:35:00+00','5m','new-b')
        ) t(listing_id,observation_start,interval_size,marker)
        """
    )
    try:
        rows = connection.execute(merged_default_bar_query()).fetchall()
    finally:
        connection.close()
    assert {row[3] for row in rows} == {"new-a", "new-b", "old-good", "old-daily"}
