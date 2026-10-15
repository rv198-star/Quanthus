"""The domain continuation must preserve the collector's explicit missing ST."""

import pytest

from quanthus.pipelines.securities_warehouse.daily_core_domain import ST_BUY_SQL, ST_REASON_SQL


@pytest.mark.parametrize(
    "status,st,buy,reason",
    [
        ("1", None, False, "ST_UNKNOWN"),
        ("1", "", False, "ST_UNKNOWN"),
        ("1", "0", True, "NORMAL"),
        ("1", "1", True, "ST_TRADABLE"),
        ("0", None, False, "SOURCE_NOT_TRADABLE"),
    ],
)
def test_actual_domain_sql_preserves_unknown(status, st, buy, reason):
    import duckdb

    con = duckdb.connect()
    row = con.execute(
        f"SELECT {ST_BUY_SQL}, {ST_REASON_SQL} FROM (VALUES (?,?)) o(tradestatus,isST)",
        [status, st],
    ).fetchone()
    assert row == (buy, reason)
    con.close()
