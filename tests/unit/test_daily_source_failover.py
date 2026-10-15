import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded
from quanthus.providers import daily_source_failover as router
from quanthus.providers import tencent_daily as tx

DAY = date(2026, 10, 15)
STAMP = "2026-10-15T15:00:00+00:00"


def test_blacklist_opens_circuit_without_retry_and_next_job_skips(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(router.time, "sleep", lambda _: pytest.fail("denial never retries"))

    def denied():
        calls.append(1)
        raise RuntimeError("BAOSTOCK_ACCESS_DENIED:10001011")

    now = datetime.now(timezone.utc)
    with pytest.raises(router.SourceUnavailable):
        router.invoke("baostock", "daily", tmp_path, denied, attempts=2, now=now)
    with pytest.raises(router.SourceUnavailable, match="CIRCUIT_OPEN"):
        router.invoke("baostock", "daily", tmp_path, denied, now=now + timedelta(minutes=1))
    assert calls == [1]
    assert (
        router.invoke("baostock", "daily", tmp_path, lambda: "ok", now=now + timedelta(hours=2))
        == "ok"
    )


def test_transient_retries_bounded_and_programming_fault_never_switches(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(router.time, "sleep", lambda _: None)

    def failed():
        calls.append(1)
        raise TimeoutError("timed out")

    with pytest.raises(router.SourceUnavailable):
        router.invoke("tencent", "daily", tmp_path, failed, attempts=2)
    assert len(calls) == 2
    with pytest.raises(TypeError):
        router.invoke("baostock", "calendar", tmp_path, lambda: len(None))
    assert not (tmp_path / "baostock-calendar.json").exists()


def saved(root, *, rows=None, stamp=STAMP):
    rows = rows if rows is not None else [{"trade_date": str(DAY), "is_open": "1"}]
    atomic(
        root / f"{DAY}.json",
        {
            "market_date": str(DAY),
            "provider": "baostock-trading-calendar",
            "rows": rows,
            "rows_sha256": digest(encoded(rows)),
            "fetched_at": stamp,
        },
    )


def test_saved_calendar_keeps_original_clock_and_is_not_refresh(tmp_path):
    saved(tmp_path)
    rows, proof = router.saved_calendar(tmp_path, DAY, DAY, observed_day=DAY)
    assert rows == [{"trade_date": str(DAY), "is_open": "1"}]
    assert proof["original_fetched_at"] == STAMP and proof["is_new_refresh"] is False
    assert proof["new_source_requests"] == 0


@pytest.mark.parametrize("fault", ["wrong_date", "hash", "duplicate", "missing", "naive"])
def test_invalid_saved_calendar_never_guesses_weekdays(tmp_path, fault):
    saved(tmp_path)
    p = tmp_path / f"{DAY}.json"
    v = json.loads(p.read_text())
    if fault == "wrong_date":
        v["market_date"] = "2026-10-14"
    if fault == "hash":
        v["rows_sha256"] = "sha256:" + "0" * 64
    if fault == "duplicate":
        v["rows"] *= 2
        v["rows_sha256"] = digest(encoded(v["rows"]))
    if fault == "missing":
        v["rows"] = []
        v["rows_sha256"] = digest(encoded([]))
    if fault == "naive":
        v["fetched_at"] = "2026-10-15T10:00:00"
    atomic(p, v)
    with pytest.raises((ValueError, router.SourceUnavailable)):
        router.saved_calendar(tmp_path, DAY, DAY, observed_day=DAY)


def payload(*, symbol="sz000001", day=str(DAY), quote_day="20261015", key="day"):
    quote = [""] * 88
    quote[2] = symbol[2:]
    quote[30] = quote_day + "150000"
    quote[39] = "12.30"
    quote[46] = "1.20"
    return encoded(
        {
            "code": 0,
            "data": {
                symbol: {
                    key: [[day, "1.01", "1.02", "1.03", "1.00", "12.3456", {}, "0.12", "1.234567"]],
                    "qt": {symbol: quote},
                }
            },
        }
    )


def test_native_daily_decimal_units_and_absent_fields_are_explicit():
    v = tx.decode(payload(), "sz000001", DAY, acquired_at=STAMP)
    assert v["volume"] == "1234.5600" and v["amount"] == "12345.670000"
    assert Decimal(v["open"]) == Decimal("1.01")
    assert all(v["_valuation_" + f] is None for f in ["pe_ttm", "pb", "ps_ttm", "pcf_ttm"])
    assert "isST_source_absent" in v["_source_limitations"]
    assert v["source"].startswith("tencent.") and v["adj_mode"] == "none"


def test_science_board_native_volume_is_shares_not_lots():
    v = tx.decode(payload(symbol="sh688001"), "sh688001", DAY, acquired_at=STAMP)
    assert v["volume"] == "12.3456"


def test_historical_kline_never_uses_current_quote_valuation():
    v = tx.decode(payload(day="2026-10-14"), "sz000001", date(2026, 10, 14), acquired_at=STAMP)
    assert all(v["_valuation_" + f] is None for f in ["pe_ttm", "pb", "ps_ttm", "pcf_ttm"])


@pytest.mark.parametrize(
    "fault", ["date", "symbol", "adjusted", "duplicate", "ohlc", "amount", "index"]
)
def test_invalid_tencent_candidate_cannot_be_ready(fault):
    raw = payload()
    symbol = "sz000001"
    v = json.loads(raw)
    if fault == "date":
        v["data"][symbol]["day"][0][0] = "2026-10-14"
    if fault == "symbol":
        symbol = "sz000002"
    if fault == "adjusted":
        v["data"][symbol]["qfqday"] = v["data"][symbol].pop("day")
    if fault == "duplicate":
        v["data"][symbol]["day"] *= 2
    if fault == "ohlc":
        v["data"][symbol]["day"][0][3] = "0.9"
    if fault == "amount":
        v["data"][symbol]["day"][0][8] = ""
    if fault == "index":
        symbol = "sh000001"
        v = json.loads(payload(symbol=symbol))
    with pytest.raises(ValueError):
        tx.decode(encoded(v), symbol, DAY, acquired_at=STAMP)


def test_unknown_st_is_not_normal_or_buyable_in_actual_sql():
    import duckdb

    from quanthus.pipelines.securities_warehouse.daily_core_domain import ST_BUY_SQL, ST_REASON_SQL

    c = duckdb.connect()
    row = c.execute(
        f"SELECT {ST_BUY_SQL}, {ST_REASON_SQL} "
        "FROM (VALUES ('1',NULL::VARCHAR)) o(tradestatus,isST)"
    ).fetchone()
    assert row == (False, "ST_UNKNOWN")
    row = c.execute(
        f"SELECT {ST_BUY_SQL}, {ST_REASON_SQL} FROM (VALUES ('1','0')) o(tradestatus,isST)"
    ).fetchone()
    assert row == (True, "NORMAL")
    c.close()


def test_provider_denial_applies_across_calendar_and_daily(tmp_path):
    now = datetime.now(timezone.utc)

    def denied():
        raise RuntimeError("BAOSTOCK_ACCESS_DENIED:10001011")

    with pytest.raises(router.SourceUnavailable):
        router.invoke("baostock", "calendar", tmp_path, denied, now=now)
    with pytest.raises(router.SourceUnavailable, match="ACCESS_CIRCUIT_OPEN"):
        router.invoke(
            "baostock", "daily", tmp_path, lambda: pytest.fail("no extra banned call"), now=now
        )


def test_expired_job_budget_is_not_a_supplier_outage(tmp_path):
    with pytest.raises(router.BudgetExhausted):
        tx.fetch(
            "000001",
            DAY,
            root=tmp_path,
            deadline=datetime.now(timezone.utc) - timedelta(seconds=1),
            opener=lambda *a, **kw: pytest.fail("no request after deadline"),
        )
    assert not list(tmp_path.glob("*.json.gz"))


def test_tencent_reuse_keeps_source_bytes_clock_and_no_requests(tmp_path):
    from quanthus.providers.acquisition_evidence import capture

    raw = payload()
    old = (
        tmp_path
        / "shadow/attempts/market-daily-core"
        / str(DAY)
        / "first/landing/original-responses"
    )
    capture(
        root=old,
        request={
            "provider": "tencent",
            "code": "sz000001",
            "basis": "none",
            "market_date": str(DAY),
        },
        raw=raw,
        started_at=STAMP,
        completed_at=STAMP,
        status="200",
    )
    v = tx.reuse(tmp_path / "shadow", tmp_path / "new", DAY, {"000001"})
    bar, receipt = v["000001"]
    assert bar["acquired_at"] == STAMP and receipt["new_source_requests"] == 0
    target = tmp_path / "new/original-responses" / receipt["raw_receipt"]["capture_file"]
    assert target.read_bytes() == next(old.glob("*.gz")).read_bytes()


def test_tencent_http_errors_preserve_actual_body_and_retry_budget(tmp_path, monkeypatch):
    import io
    from urllib.error import HTTPError

    from quanthus.providers.acquisition_evidence import verify_capture

    calls = []
    monkeypatch.setattr(tx.time, "sleep", lambda _: None)

    def failed(*a, **kw):
        calls.append(1)
        raise HTTPError(
            "https://example.invalid", 503, "unavailable", {}, io.BytesIO(b"service unavailable")
        )

    with pytest.raises(HTTPError):
        tx.fetch(
            "000001",
            DAY,
            root=tmp_path,
            deadline=datetime.now(timezone.utc) + timedelta(seconds=20),
            opener=failed,
        )
    captures = [verify_capture(p) for p in tmp_path.glob("*.gz")]
    assert len(calls) == len(captures) == 2
    assert all(c["status"] == "503" and c["raw_bytes"] == 19 for c in captures)


def routing_fixture(tmp_path, monkeypatch, *, indices=True):
    from types import SimpleNamespace

    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.pipelines import lane_runner as lane
    from quanthus.providers import daily_market_routing as market

    root = tmp_path / "home/source-universe-v1" / str(DAY)
    root.mkdir(parents=True)
    population = [
        {"stock_code": c, "tradestatus": s, "warehouse_observed_at": STAMP}
        for c, s in [("000001", "1"), ("600000", "1"), ("000002", "0")]
    ]
    pq.write_table(pa.Table.from_pylist(population), root / "stock_list.parquet")
    universe = {"market_date": str(DAY), "universe_digest": "sha256:" + "a" * 64}
    monkeypatch.setattr(market, "verify", lambda *a: universe)
    monkeypatch.setattr(tx, "reuse", lambda *a: {})
    deadlines = []
    calls = []
    monkeypatch.setattr(
        lane,
        "_baostock_wait_for_index_day",
        lambda *a, **kw: (
            [{"index_code": code, "date": str(DAY)} for code in lane.INDEX_CODES]
            if indices
            else [],
            [],
        ),
    )

    def primary(*a, **kw):
        deadlines.append(kw["deadline_at"])
        return (
            [{"stock_code": "000001", "source": "baostock", "acquired_at": STAMP}],
            [
                {"stock_code": "000001", "tradestatus": "1", "isST": "0"},
                {"stock_code": "000002", "tradestatus": "0", "isST": "0"},
            ],
            [],
        )

    monkeypatch.setattr(lane, "_baostock_fetch_day", primary)

    def fallback(code, *a, **kw):
        calls.append(code)
        return (
            {
                "stock_code": code,
                "source": "tencent.native-unadjusted-daily-v1",
                "acquired_at": STAMP,
            },
            {"provider": "tencent", "acquired_at": STAMP, "response_error_code": "0"},
        )

    monkeypatch.setattr(tx, "fetch", fallback)
    args = dict(
        paths=SimpleNamespace(home=tmp_path / "home", shadow_root=tmp_path / "shadow"),
        market_date=DAY,
        codes=["000001", "600000", "000002"],
        landing=tmp_path / "landing",
        readiness={
            "primary_scan_seconds": 900,
            "poll_interval_seconds": 30,
            "socket_timeout_seconds": 15,
            "missing_set_retry_rounds": 1,
            "missing_set_retry_delay_seconds": 30,
        },
        readiness_deadline=datetime.now(timezone.utc) + timedelta(hours=1),
        completion_deadline=datetime.now(timezone.utc) + timedelta(hours=1),
        captured={},
        universe=universe,
        converter_sha256="sha256:" + "b" * 64,
    )
    return market, args, calls, deadlines


def test_real_router_preserves_good_primary_suspension_and_reserves_fallback_time(
    tmp_path, monkeypatch
):
    market, args, calls, deadlines = routing_fixture(tmp_path, monkeypatch)
    bars, states, indices, logs = market.acquire(**args)
    assert calls == ["600000"]
    assert len(bars) == 2 and len(states) == 3 and len(indices) == 3
    assert next(r for r in states if r["stock_code"] == "000002")["isST"] == "0"
    assert next(r for r in states if r["stock_code"] == "600000")["isST"] is None
    assert deadlines[0] < args["completion_deadline"] - timedelta(minutes=20)
    decision = logs[-1]["decision"]
    assert decision["st_unknown"] == 1 and decision["source_counts"]["baostock"] == 1


def test_missing_required_index_stops_before_any_new_tencent_collection(tmp_path, monkeypatch):
    market, args, calls, deadlines = routing_fixture(tmp_path, monkeypatch, indices=False)
    with pytest.raises(router.SourceUnavailable, match="INDEX_EVIDENCE_MISSING"):
        market.acquire(**args)
    assert calls == []


def test_old_bad_200_candidate_does_not_block_new_good_response(tmp_path):
    from quanthus.providers.acquisition_evidence import capture

    root = (
        tmp_path
        / "shadow/attempts/market-daily-core"
        / str(DAY)
        / "first/landing/original-responses"
    )
    request = {"provider": "tencent", "code": "sz000001", "basis": "none", "market_date": str(DAY)}
    capture(
        root=root,
        request=request,
        raw=payload(day="2026-10-14"),
        started_at=STAMP,
        completed_at=STAMP,
        status="200",
    )
    capture(
        root=root,
        request=request,
        raw=payload(),
        started_at="2026-10-15T15:01:00+00:00",
        completed_at="2026-10-15T15:01:00+00:00",
        status="200",
    )
    result = tx.reuse(tmp_path / "shadow", tmp_path / "new", DAY, {"000001"})
    assert result["000001"][0]["acquired_at"] == "2026-10-15T15:01:00+00:00"
    proof = json.loads((tmp_path / "new/tencent-reuse-rejections.json").read_text())
    assert len(proof["rejections"]) == 1
    assert len(list((tmp_path / "new/original-responses").glob("*.gz"))) == 2


def test_only_bad_200_candidate_allows_fresh_request_for_missing_code(tmp_path, monkeypatch):
    from quanthus.providers.acquisition_evidence import capture

    original = tx.reuse
    market, args, calls, deadlines = routing_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(tx, "reuse", original)
    root = (
        tmp_path
        / "shadow/attempts/market-daily-core"
        / str(DAY)
        / "first/landing/original-responses"
    )
    capture(
        root=root,
        request={
            "provider": "tencent",
            "code": "sh600000",
            "basis": "none",
            "market_date": str(DAY),
        },
        raw=payload(symbol="sh600000", day="2026-10-14"),
        started_at=STAMP,
        completed_at=STAMP,
        status="200",
    )
    bars, states, indices, logs = market.acquire(**args)
    assert calls == ["600000"] and len(bars) == 2


@pytest.mark.parametrize("fault", ["clock", "policy", "exchange"])
def test_tencent_envelope_metadata_tampering_is_hard_failure(tmp_path, fault):
    import gzip

    from quanthus.providers.acquisition_evidence import capture

    root = (
        tmp_path
        / "shadow/attempts/market-daily-core"
        / str(DAY)
        / "first/landing/original-responses"
    )
    capture(
        root=root,
        request={
            "provider": "tencent",
            "code": "sz000001",
            "basis": "none",
            "market_date": str(DAY),
        },
        raw=payload(),
        started_at=STAMP,
        completed_at=STAMP,
        status="200",
    )
    p = next(root.glob("*.gz"))
    v = json.loads(gzip.decompress(p.read_bytes()))
    if fault == "clock":
        v["response_completed_at"] = "2026-10-15T14:00:00+00:00"
    if fault == "policy":
        v["policy_id"] = "forged"
    if fault == "exchange":
        v["request"]["code"] = "sh000001"
    p.write_bytes(gzip.compress(encoded(v)))
    with pytest.raises(ValueError, match="ENVELOPE_IDENTITY_DIFFERS"):
        tx.reuse(tmp_path / "shadow", tmp_path / "new", DAY, {"000001"})


@pytest.mark.parametrize("fault", ["duplicate", "wrong_date"])
def test_nonexact_index_evidence_blocks_tencent_before_collection(tmp_path, monkeypatch, fault):
    from quanthus.pipelines import lane_runner as lane

    market, args, calls, deadlines = routing_fixture(tmp_path, monkeypatch)
    rows = [{"index_code": c, "date": str(DAY)} for c in lane.INDEX_CODES]
    if fault == "duplicate":
        rows.append(rows[0])
    else:
        rows[0]["date"] = "2026-10-14"
    monkeypatch.setattr(lane, "_baostock_wait_for_index_day", lambda *a, **kw: (rows, []))
    with pytest.raises(router.SourceUnavailable, match="INDEX_EVIDENCE_MISSING"):
        market.acquire(**args)
    assert calls == []
