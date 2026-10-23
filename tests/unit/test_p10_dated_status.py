from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse.p10_dated_status import raw_observations


def response():
    return {
        "stock_code": "000001",
        "exchange": "SZSE",
        "bs_code": "sz.000001",
        "request": {
            "code": "sz.000001",
            "fields": "date,code,isST,tradestatus",
            "frequency": "d",
            "adjustflag": "3",
            "start_date": "2026-07-01",
            "end_date": "2026-07-25",
            "fetched_at": "2026-07-24T17:42:21.016785Z",
        },
        "window": {"start": "2026-07-01", "end": "2026-07-25"},
        "error_code": "0",
        "rows": [
            {"date": "2026-07-10", "code": "sz.000001", "isST": "0", "tradestatus": "1"},
            {"date": "2026-07-13", "code": "sz.000001", "isST": None, "tradestatus": "1"},
        ],
        "n_rows": 2,
    }


def test_true_acquisition_z_time_and_unknown_preserved():
    r = response()
    before = deepcopy(r)
    out = raw_observations(
        r, {("000001", "2026-07-10"), ("000001", "2026-07-13")}, "sha256:" + "a" * 64
    )
    assert r == before
    assert out[0]["status_value"] == "NORMAL"
    assert out[0]["acquired_at"] == r["request"]["fetched_at"]
    assert out[1]["status_value"] is None and out[1]["reason"] == "STATUS_UNKNOWN"


@pytest.mark.parametrize(
    "key,value,reason",
    [
        ("fetched_at", "2026-07-01T00:00:00Z", "CAPTURE_BEFORE_OBSERVATION"),
        ("fetched_at", None, "CAPTURE_MISSING"),
        ("fields", "date,code,close", "REQUEST_SCHEMA"),
        ("code", "sh.000001", "SOURCE_IDENTITY"),
    ],
)
def test_raw_contract_not_inferred(key, value, reason):
    r = response()
    r["request"][key] = value
    with pytest.raises(ValueError, match=reason):
        raw_observations(r, {("000001", "2026-07-10")}, "sha256:" + "a" * 64)


def test_effective_status_does_not_fill_unknown_or_choose_equal_time_conflict():
    from datetime import datetime

    import duckdb

    from quanthus.pipelines.securities_warehouse import p10_dated_status as s

    with duckdb.connect() as c:
        c.execute("""CREATE TABLE observations(stock_code VARCHAR,market_date DATE,
            status_value VARCHAR,reason VARCHAR,acquired_at TIMESTAMPTZ,
            source_sha256 VARCHAR,source_row BIGINT)""")
        for code, val, stamp in [
            ("000001", "NORMAL", "2026-08-04"),
            ("000001", None, "2026-08-05"),
            ("000002", "ST", "2026-08-04"),
            ("000002", "NORMAL", "2026-08-04"),
            ("000003", "NORMAL", "2026-08-04"),
        ]:
            c.execute(
                "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    code,
                    "2026-08-03",
                    val,
                    "STATUS_UNKNOWN" if val is None else "PROVIDER_TRADING",
                    stamp + "T12:00:00Z",
                    "sha256:" + "a" * 64,
                    0,
                ],
            )
        rows = c.execute(
            s.effective_query("observations", datetime.fromisoformat("2026-08-06T00:00:00+00:00"))
        ).fetchall()
        by_code = {r[0]: r for r in rows}
        assert by_code["000001"][3:5] == (None, "STATUS_UNKNOWN")
        assert by_code["000002"][3:5] == (None, "EQUAL_ACQUISITION_CONFLICT")
        assert by_code["000003"][3:5] == ("NORMAL", "PROVIDER_TRADING")
