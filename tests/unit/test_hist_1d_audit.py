from __future__ import annotations

import pytest

from quanthus.pipelines.hist_1d_audit import coverage_query, overlap_query


def test_overlap_compares_decimal_and_nulls_and_both_missing_sides() -> None:
    duckdb = pytest.importorskip("duckdb")
    c = duckdb.connect()
    c.execute("""CREATE TABLE old_daily(listing_id VARCHAR,observation_start TIMESTAMPTZ,
        interval_size VARCHAR,price_basis VARCHAR,open DECIMAL(38,10),high DECIMAL(38,10),
        low DECIMAL(38,10),close DECIMAL(38,10),volume DECIMAL(38,10),
        turnover DECIMAL(38,10),currency VARCHAR,trade_date DATE)""")
    c.execute("""INSERT INTO old_daily VALUES
        ('equal','2020-01-02 01:30:00Z','1d','UNADJUSTED',1,1,1,1,100,NULL,'CNY','2020-01-02'),
        ('changed','2020-01-02 01:30:00Z','1d','UNADJUSTED',1,1,1,1,100,NULL,'CNY','2020-01-02'),
        ('old-only','2020-01-02 01:30:00Z','1d','UNADJUSTED',1,1,1,1,100,NULL,'CNY','2020-01-02')
        """)
    c.execute("CREATE TABLE new_daily AS SELECT * FROM old_daily WHERE listing_id<>'old-only'")
    c.execute("UPDATE new_daily SET turnover=0 WHERE listing_id='changed'")
    c.execute(
        "INSERT INTO new_daily SELECT 'new-only',* EXCLUDE(listing_id) FROM old_daily LIMIT 1"
    )
    result = c.execute(overlap_query()).fetchall()
    assert {(r[0], r[2]) for r in result} == {
        ("changed", "VALUE_DIFFERENCE"),
        ("old-only", "ABSENT_IN_BASE251"),
        ("new-only", "ABSENT_IN_BASE23"),
    }
    c.close()


def test_coverage_does_not_infer_suspension_or_turn_null_volume_into_zero() -> None:
    duckdb = pytest.importorskip("duckdb")
    c = duckdb.connect()
    c.execute("""CREATE TABLE expected AS SELECT listing_id,DATE '2005-01-04' AS trade_date,
        TIMESTAMPTZ '2005-01-04 01:30:00Z' AS open_at FROM
        (VALUES ('zero'),('suspended'),('unknown'),('null-volume'),('old-state')) t(listing_id)""")
    c.execute("""CREATE TABLE bar_presence AS SELECT * FROM
        (VALUES ('zero',DATE '2005-01-04',1,1),('null-volume',DATE '2005-01-04',1,0))
        t(listing_id,trade_date,rows,zero_rows)""")
    c.execute("""CREATE TABLE lifecycle AS SELECT listing_id,'SUSPENSION' AS status_type,
        'SUSPENDED' AS status_value,TIMESTAMPTZ '2005-01-01 00:00:00Z' AS effective_from,
        NULL::TIMESTAMPTZ AS effective_to,knowledge_to FROM
        (VALUES ('suspended',NULL::TIMESTAMPTZ),('old-state',TIMESTAMPTZ '2006-01-01 00:00:00Z'))
        t(listing_id,knowledge_to)""")
    result = {row[0]: row[-1] for row in c.execute(coverage_query()).fetchall()}
    assert result == {
        "zero": "SOURCE_ZERO_VOLUME_BAR",
        "suspended": "EVIDENCED_SUSPENSION",
        "unknown": "MISSING",
        "null-volume": "OBSERVED_BAR",
        "old-state": "MISSING",
    }
    c.close()
