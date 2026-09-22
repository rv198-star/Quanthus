"""Unit tests for Hermes ODS five-lane runner (audit-fix v2)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import quanthus.pipelines.lane_runner as lane_runner
from quanthus.pipelines.lane_runner import (
    LanePaths,
    LaneRunError,
    expected_timer_oncalendar,
    run_lane,
    session_preflight,
)
from quanthus.pipelines.securities_warehouse.daily_ods_catalog import (
    find_ready_daily_ods_lanes,
)

SCHEDULE = Path("config/schedules/hermes-ods-schedule.v1.json")


def _write_parquet(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


@pytest.fixture
def fixture_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    open_day = date(2026, 7, 24)
    closed_day = date(2026, 7, 25)
    cal_rows = []
    for day, is_open in ((open_day, 1), (closed_day, 0)):
        for exchange in ("SSE", "SZSE"):
            cal_rows.append(
                {
                    "exchange": exchange,
                    "trade_date": day.isoformat(),
                    "is_open": is_open,
                }
            )
    # more open sessions for weekly window
    for offset in range(1, 25):
        d = date(2026, 6, 20)
        from datetime import timedelta

        day = open_day - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for exchange in ("SSE", "SZSE"):
            cal_rows.append(
                {
                    "exchange": exchange,
                    "trade_date": day.isoformat(),
                    "is_open": 1,
                }
            )
    _write_parquet(home / "ref_trading_calendar.parquet", cal_rows)

    codes = ["000001", "600519"]
    stock_rows = [{"stock_code": c, "security_id": c, "name": c} for c in codes]
    _write_parquet(home / "stock_list.parquet", stock_rows)
    _write_parquet(
        home / "ref_security_master.parquet",
        [{"security_id": c, "stock_code": c, "list_date": "2000-01-01"} for c in codes],
    )

    none_rows = []
    st_rows = []
    for day_offset in range(0, 30):
        from datetime import timedelta

        day = open_day - timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue
        for c in codes:
            none_rows.append(
                {
                    "stock_code": c,
                    "date": day.isoformat(),
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 1000.0,
                    "amount": 10000.0,
                }
            )
            st_rows.append(
                {
                    "stock_code": c,
                    "date": day.isoformat(),
                    "tradestatus": "1",
                    "isST": "0",
                }
            )
    _write_parquet(home / "daily_ohlcv_none.parquet", none_rows)
    _write_parquet(home / "daily_status_observation.parquet", st_rows)
    _write_parquet(
        home / "valuation_daily.parquet",
        [
            {
                "stock_code": code,
                "date": open_day.isoformat(),
                "pe_ttm": 10.0,
                "pb": 1.0,
                "ps_ttm": 2.0,
                "pcf_ttm": 3.0,
            }
            for code in codes
        ],
    )
    _write_parquet(
        home / "index_daily.parquet",
        [
            {
                "index_code": code,
                "date": open_day.isoformat(),
                "close": 3000.0,
            }
            for code in lane_runner.INDEX_CODES
        ],
    )
    _write_parquet(
        home / "ref_adjustment_factors.parquet",
        [
            {
                "stock_code": "000001",
                "effective_date": open_day.isoformat(),
                "fore_adjust_factor": 1.0,
                "back_adjust_factor": 1.0,
            }
        ],
    )
    # announcements via sqlite dump for unit fixtures when present; else parquet
    ann_db = home / "info_archive.db"
    if not ann_db.exists():
        import sqlite3

        con = sqlite3.connect(ann_db)
        con.execute(
            "CREATE TABLE announcements (code TEXT, name TEXT, title TEXT, "
            "publish_time TEXT, ann_id TEXT, url TEXT, category TEXT, impact TEXT)"
        )
        con.execute(
            "INSERT INTO announcements VALUES (?,?,?,?,?,?,?,?)",
            (
                "000001",
                "t",
                "title",
                f"{open_day.isoformat()} 00:00:00",
                "1",
                "http://x",
                "g",
                "r",
            ),
        )
        con.commit()
        con.close()
    for name in (
        "ref_suspensions.parquet",
        "ref_corporate_actions.parquet",
        "dividend_history.parquet",
        "financial_quarterly.parquet",
        "ref_shareholder_count.parquet",
        "ref_st_status_pit.parquet",
        "ref_industry_map.parquet",
    ):
        _write_parquet(
            home / name,
            [
                {
                    "stock_code": "000001",
                    "date": open_day.isoformat(),
                    "ann_date": open_day.isoformat(),
                    "ex_date": open_day.isoformat(),
                    "effective_date": open_day.isoformat(),
                    "announce_date": open_day.isoformat(),
                    "hold_notice_date": open_day.isoformat(),
                    "end_date": open_day.isoformat(),
                    "notice_date": open_day.isoformat(),
                    "suspend_start": open_day.isoformat(),
                    "publish_deadline": open_day.isoformat(),
                    "report_date": open_day.isoformat(),
                    "action_type": "div",
                    "shareholder_count": 1,
                }
            ],
        )

    _write_parquet(
        home / "raw_f10.parquet",
        [
            {
                "security_id": "000001",
                "category": "company_profile",
                "content": "fixture F10",
                "market_date": open_day.isoformat(),
            }
        ],
    )
    _write_parquet(
        home / "financial_statements.parquet",
        [
            {
                "security_id": "000001",
                "statement_type": "balance_sheet",
                "market_date": open_day.isoformat(),
                "raw_payload_gzip": b"fixture",
            }
        ],
    )
    _write_parquet(
        home / "fundamentals_receipts.parquet",
        [
            {
                "dataset": "raw_f10",
                "security_id": "000001",
                "status": "success_nonempty",
            },
            {
                "dataset": "financial_statements",
                "security_id": "000001",
                "status": "success_nonempty",
            },
            {
                "dataset": "industry_membership",
                "security_id": "000001",
                "status": "success_nonempty",
            },
        ],
    )
    _write_parquet(
        home / "after_hours_fixed_price_trades.parquet",
        [
            {
                "security_id": "000001",
                "market_date": open_day.isoformat(),
                "match_time": f"{open_day.isoformat()}T15:06:00+08:00",
                "price": 10.0,
                "quantity_units": 100.0,
            }
        ],
    )
    _write_parquet(
        home / "after_hours_fixed_price_receipts.parquet",
        [
            {
                "dataset": "after_hours_fixed_price_trades",
                "security_id": code,
                "status": "success_nonempty" if code == "000001" else "justified_empty",
            }
            for code in codes
        ],
    )

    # Complete A-share minute grids for both intraday streams.
    minute = tmp_path / "minute" / "data"
    for layer, bar in (("research_1m", "1m"), ("prod_5m", "5m")):
        d = minute / layer
        d.mkdir(parents=True)
        timestamps = sorted(lane_runner._expected_bar_timestamps(open_day, bar))
        for c in codes:
            _write_parquet(
                d / f"{c}_{bar}.parquet",
                [
                    {
                        "stock_code": c,
                        "ts": timestamp,
                        "open": 10.0,
                        "high": 10.1,
                        "low": 9.9,
                        "close": 10.0,
                        "volume": 100.0,
                    }
                    for timestamp in timestamps
                ],
            )
    os.environ["ASTOCK_MINUTE_ROOT"] = str(tmp_path / "minute")
    os.environ["HERMES_ODS_ACQUIRE_MODE"] = "local"
    os.environ["HERMES_ODS_SYMBOL_LIMIT"] = "2"
    return home


def _paths(home: Path, shadow: Path) -> LanePaths:
    return LanePaths(
        home=home,
        shadow_root=shadow,
        calendar=home / "ref_trading_calendar.parquet",
        lock_root=shadow / "locks",
    )


def test_timer_oncalendar_matches_schedule_contract() -> None:
    expected = expected_timer_oncalendar(SCHEDULE)
    cn01_expected = expected_timer_oncalendar(SCHEDULE, node_id="cn01")
    assert expected["market-daily-core"] == "Mon..Fri *-*-* 17:30:00 Asia/Shanghai"
    assert cn01_expected["market-daily-core"] == "Mon..Fri *-*-* 18:30:00 Asia/Shanghai"
    assert expected["calendar-daily-events"] == "*-*-* 19:30:00 Asia/Shanghai"
    assert expected["weekly-corrections"] == "Sat *-*-* 08:00:00 Asia/Shanghai"
    assert expected["intraday-1m"] == "Mon..Fri *-*-* 20:30:00 Asia/Shanghai"
    assert expected["intraday-5m"] == "Mon..Fri *-*-* 23:00:00 Asia/Shanghai"

    root = Path("ops/systemd")
    assert "17:30:00 Asia/Shanghai" in (root / "hermes-ods-market-daily-core.timer").read_text()
    assert "15:40:00 Asia/Shanghai" in (root / "quanthus-t-close-plus-5h.timer").read_text()
    assert "19:30:00 Asia/Shanghai" in (root / "hermes-ods-calendar-daily-events.timer").read_text()
    assert "08:00:00 Asia/Shanghai" in (root / "hermes-ods-weekly-corrections.timer").read_text()
    assert "20:30:00 Asia/Shanghai" in (root / "hermes-ods-intraday-1m.timer").read_text()
    assert "23:00:00 Asia/Shanghai" in (root / "hermes-ods-intraday-5m.timer").read_text()
    # services use same-day Asia/Shanghai date, not yesterday
    for name in (
        "hermes-ods-market-daily-core.service",
        "hermes-ods-calendar-daily-events.service",
        "hermes-ods-intraday-1m.service",
        "hermes-ods-intraday-5m.service",
    ):
        text = (root / name).read_text()
        assert 'date -d "yesterday"' not in text
        assert "date +%%F" in text
        assert "date +%F" not in text.replace("date +%%F", "")


def test_session_preflight(fixture_home: Path) -> None:
    cal = fixture_home / "ref_trading_calendar.parquet"
    assert session_preflight(calendar=cal, market_date=date(2026, 7, 24)) == "both_open"
    assert session_preflight(calendar=cal, market_date=date(2026, 7, 25)) == "both_closed"
    assert session_preflight(calendar=cal, market_date=date(2099, 1, 1)) == "missing_or_disagree"


def test_market_open_ready_and_ready_last(fixture_home: Path, tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        force_preflight="both_open",
    )
    assert result.status == "ready"
    assert result.release_dir is not None
    ready = result.release_dir / "READY"
    assert ready.is_file()
    # READY last: mtime of READY >= every artifact under release
    ready_mtime = ready.stat().st_mtime
    for path in result.release_dir.rglob("*"):
        if path.is_file() and path.name != "READY":
            assert path.stat().st_mtime <= ready_mtime + 1e-3
    current = json.loads(
        (shadow / "releases" / "daily" / "2026-07-24" / "CURRENT.json").read_text(
            encoding="utf-8"
        )
    )
    assert current["attempt_id"] == result.attempt_id
    assert current["release_id"] == json.loads(
        (result.release_dir / "release.json").read_text(encoding="utf-8")
    )["release_id"]
    # no post-READY sidecar at release root other than contract files
    root_names = {p.name for p in result.release_dir.iterdir()}
    assert "lane_status.json" not in root_names or "lane_status.json" in {
        p.name for p in (result.release_dir / "artifacts").iterdir()
    }
    # acquisition log has request provenance
    log = next(p for p in result.datasets if p.name == "acquisition_log")
    assert log.status == "ready"
    payload = json.loads(Path(log.path).read_text(encoding="utf-8"))
    assert payload["requests"]
    assert "request" in payload["requests"][0]


def test_partial_never_ready(fixture_home: Path, tmp_path: Path) -> None:
    # remove index so mandatory fails
    (fixture_home / "index_daily.parquet").unlink()
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        force_preflight="both_open",
    )
    assert result.status in {"partial", "failed"}
    assert result.release_dir is None
    index = next(item for item in result.datasets if item.name == "index_daily_delta")
    assert index.status == "failed"
    assert "missing_index_coverage=" in (index.detail or "")
    # no READY anywhere under shadow for this attempt
    assert not list(shadow.rglob("READY"))


def test_baostock_index_fetch_maps_the_contract_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Login:
        error_code = "0"
        error_msg = "ok"

    class Query:
        error_code = "0"
        error_msg = "ok"

        def __init__(self, row: list[str]) -> None:
            self.row = row
            self.pending = True

        def next(self) -> bool:
            pending, self.pending = self.pending, False
            return pending

        def get_row_data(self) -> list[str]:
            return self.row

    class FakeBaostock:
        calls: list[str] = []
        logged_out = False

        @staticmethod
        def login() -> Login:
            return Login()

        @classmethod
        def query_history_k_data_plus(cls, code, *_args, **_kwargs) -> Query:
            cls.calls.append(code)
            return Query(
                ["2026-07-27", code, "1", "2", "0.5", "1.5", "100", "200", "1.2"]
            )

        @classmethod
        def logout(cls) -> None:
            cls.logged_out = True

    monkeypatch.setitem(__import__("sys").modules, "baostock", FakeBaostock)
    rows, logs = lane_runner._baostock_fetch_index_day(date(2026, 7, 27))

    assert [row["index_code"] for row in rows] == list(lane_runner.INDEX_CODES)
    assert FakeBaostock.calls == list(lane_runner.INDEX_CODES.values())
    assert {log["request"]["code"] for log in logs} == set(FakeBaostock.calls)
    assert all(row["date"] == "2026-07-27" for row in rows)
    assert FakeBaostock.logged_out


def test_baostock_daily_fetch_preserves_dwd_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Login:
        error_code = "0"
        error_msg = "ok"

    class Query:
        error_code = "0"
        error_msg = "ok"

        def __init__(self, row: list[str]) -> None:
            self.row = row
            self.pending = True

        def next(self) -> bool:
            pending, self.pending = self.pending, False
            return pending

        def get_row_data(self) -> list[str]:
            return self.row

    class FakeBaostock:
        fields: list[str] = []
        logged_out = False

        @staticmethod
        def login() -> Login:
            return Login()

        @classmethod
        def query_history_k_data_plus(cls, code: str, fields: str, **_kwargs: object) -> Query:
            cls.fields.append(fields)
            return Query(
                [
                    "2026-07-27",
                    code,
                    "1",
                    "2",
                    "0.5",
                    "1.5",
                    "100",
                    "200",
                    "1.2",
                    "-0.5",
                    "1",
                    "0",
                    "12.3",
                    "1.4",
                    "2.5",
                    "3.6",
                ]
            )

        @classmethod
        def logout(cls) -> None:
            cls.logged_out = True

    monkeypatch.setitem(__import__("sys").modules, "baostock", FakeBaostock)
    bars, statuses, logs = lane_runner._baostock_fetch_day(["000001"], date(2026, 7, 27))

    assert FakeBaostock.fields == [
        "date,code,open,high,low,close,volume,amount,turn,pctChg,tradestatus,isST,"
        "peTTM,pbMRQ,psTTM,pcfNcfTTM"
    ]
    assert FakeBaostock.logged_out
    assert logs[0]["response_error_code"] == "0"
    assert bars[0]["turnover_rate"] == 1.2
    assert bars[0]["pct_change"] == -0.5
    assert bars[0]["_valuation_pe_ttm"] == 12.3
    assert bars[0]["_valuation_pb"] == 1.4
    assert bars[0]["adj_version"] == "baostock.adjustflag=3.v1"
    assert bars[0]["built_at"] == bars[0]["acquired_at"]
    assert statuses[0]["date"] == "2026-07-27"
    assert statuses[0]["built_at"] == statuses[0]["acquired_at"]


def test_baostock_index_fetch_reconnects_after_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Login:
        error_code = "0"
        error_msg = "ok"

    class FailedQuery:
        error_code = "10002007"
        error_msg = "network receive error"

    class Query:
        error_code = "0"
        error_msg = "ok"

        def __init__(self, code: str) -> None:
            self.code = code
            self.pending = True

        def next(self) -> bool:
            pending, self.pending = self.pending, False
            return pending

        def get_row_data(self) -> list[str]:
            return [
                "2026-07-27",
                self.code,
                "1",
                "2",
                "0.5",
                "1.5",
                "100",
                "200",
                "1.2",
            ]

    class FakeBaostock:
        calls: list[str] = []
        login_calls = 0

        @classmethod
        def login(cls) -> Login:
            cls.login_calls += 1
            return Login()

        @staticmethod
        def logout() -> None:
            return None

        @classmethod
        def query_history_k_data_plus(cls, code, *_args, **_kwargs):
            cls.calls.append(code)
            if len(cls.calls) == 1:
                return FailedQuery()
            return Query(code)

    monkeypatch.setattr(lane_runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setitem(__import__("sys").modules, "baostock", FakeBaostock)
    rows, logs = lane_runner._baostock_fetch_index_day(date(2026, 7, 27))

    assert len(rows) == len(lane_runner.INDEX_CODES)
    assert FakeBaostock.calls[:2] == ["sh.000001", "sh.000001"]
    assert FakeBaostock.login_calls == 2
    assert logs[0]["attempts"] == 2
    assert logs[0]["retry_errors"] == [
        {
            "attempt": "1",
            "error_code": "10002007",
            "error_msg": "network receive error",
        }
    ]


def test_baostock_query_keeps_reconnect_failure_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Login:
        error_code = "10002007"
        error_msg = "network receive error"

    class FailedQuery:
        error_code = "10002007"
        error_msg = "network receive error"

    class FakeBaostock:
        login_calls = 0
        query_calls = 0

        @classmethod
        def login(cls) -> Login:
            cls.login_calls += 1
            return Login()

        @staticmethod
        def logout() -> None:
            return None

        @classmethod
        def query_history_k_data_plus(cls, *_args, **_kwargs) -> FailedQuery:
            cls.query_calls += 1
            return FailedQuery()

    monkeypatch.setattr(lane_runner.time, "sleep", lambda _seconds: None)
    rows, error_code, error_msg, attempts, retry_errors = (
        lane_runner._baostock_query_day_with_retry(
            FakeBaostock,
            code="sz.000001",
            fields="date,code",
            day="2026-07-28",
        )
    )

    assert rows == []
    assert error_code == "provider_reconnect_failed"
    assert "login failed after 3 attempts" in error_msg
    assert attempts == 3
    assert FakeBaostock.query_calls == 1
    assert FakeBaostock.login_calls == 6
    assert [item["error_code"] for item in retry_errors] == [
        "10002007",
        "provider_reconnect_failed",
        "provider_reconnect_failed",
    ]


def test_baostock_query_reconnects_when_row_iteration_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Login:
        error_code = "0"
        error_msg = "ok"

    class BrokenQuery:
        error_code = "0"
        error_msg = "ok"

        @staticmethod
        def next() -> bool:
            raise ConnectionResetError("connection reset by peer")

    class Query:
        error_code = "0"
        error_msg = "ok"

        def __init__(self) -> None:
            self.pending = True

        def next(self) -> bool:
            pending, self.pending = self.pending, False
            return pending

        @staticmethod
        def get_row_data() -> list[str]:
            return ["2026-07-28", "sz.000001"]

    class FakeBaostock:
        login_calls = 0
        logout_calls = 0
        query_calls = 0

        @classmethod
        def login(cls) -> Login:
            cls.login_calls += 1
            return Login()

        @classmethod
        def logout(cls) -> None:
            cls.logout_calls += 1

        @classmethod
        def query_history_k_data_plus(cls, *_args, **_kwargs):
            cls.query_calls += 1
            if cls.query_calls == 1:
                return BrokenQuery()
            return Query()

    monkeypatch.setattr(lane_runner.time, "sleep", lambda _seconds: None)
    rows, error_code, error_msg, attempts, retry_errors = (
        lane_runner._baostock_query_day_with_retry(
            FakeBaostock,
            code="sz.000001",
            fields="date,code",
            day="2026-07-28",
        )
    )

    assert rows == [["2026-07-28", "sz.000001"]]
    assert error_code == "0"
    assert error_msg == "ok"
    assert attempts == 2
    assert FakeBaostock.query_calls == 2
    assert FakeBaostock.logout_calls == 1
    assert FakeBaostock.login_calls == 1
    assert retry_errors == [
        {
            "attempt": "1",
            "error_code": "provider_exception",
            "error_msg": "connection reset by peer",
        }
    ]


def test_baostock_empty_response_does_not_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Login:
        error_code = "0"
        error_msg = "ok"

    class EmptyQuery:
        error_code = "0"
        error_msg = "ok"

        @staticmethod
        def next() -> bool:
            return False

    class FakeBaostock:
        login_calls = 0
        query_calls = 0

        @classmethod
        def login(cls) -> Login:
            cls.login_calls += 1
            return Login()

        @staticmethod
        def logout() -> None:
            return None

        @classmethod
        def query_history_k_data_plus(cls, *_args, **_kwargs) -> EmptyQuery:
            cls.query_calls += 1
            return EmptyQuery()

    monkeypatch.setitem(__import__("sys").modules, "baostock", FakeBaostock)
    bars, statuses, logs = lane_runner._baostock_fetch_day(
        ["000001"],
        date(2026, 7, 27),
        missing_set_retry_rounds=0,
    )

    assert bars == []
    assert statuses == []
    assert FakeBaostock.login_calls == 1
    assert FakeBaostock.query_calls == 1
    assert logs[0]["response_error_code"] == "empty_response"
    assert logs[0]["attempts"] == 1
    assert logs[0]["retry_errors"] == []


def test_baostock_missing_set_retry_repairs_only_early_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Login:
        error_code = "0"
        error_msg = "ok"

    class Query:
        error_code = "0"
        error_msg = "ok"

        def __init__(self, row: list[str] | None) -> None:
            self.row = row
            self.pending = row is not None

        def next(self) -> bool:
            pending, self.pending = self.pending, False
            return pending

        def get_row_data(self) -> list[str]:
            assert self.row is not None
            return self.row

    class FakeBaostock:
        calls: list[str] = []
        per_code: dict[str, int] = {}
        login_calls = 0

        @classmethod
        def login(cls) -> Login:
            cls.login_calls += 1
            return Login()

        @staticmethod
        def logout() -> None:
            return None

        @classmethod
        def query_history_k_data_plus(cls, code: str, *_args, **_kwargs) -> Query:
            cls.calls.append(code)
            cls.per_code[code] = cls.per_code.get(code, 0) + 1
            if code == "sz.000001" and cls.per_code[code] == 1:
                return Query(None)
            return Query(
                [
                    "2026-07-27",
                    code,
                    "1",
                    "2",
                    "0.5",
                    "1.5",
                    "100",
                    "200",
                    "1.2",
                    "-0.5",
                    "1",
                    "0",
                ]
            )

    sleeps: list[float] = []
    timestamps = iter(
        [
            "2026-07-27T17:00:01+08:00",
            "2026-07-27T17:00:02+08:00",
            "2026-07-27T17:00:10+08:00",
        ]
    )
    monkeypatch.setattr(lane_runner.time, "sleep", sleeps.append)
    monkeypatch.setattr(lane_runner, "_now_sh_iso", lambda: next(timestamps))
    monkeypatch.setitem(__import__("sys").modules, "baostock", FakeBaostock)
    bars, statuses, logs = lane_runner._baostock_fetch_day(
        ["000001", "000002"],
        date(2026, 7, 27),
        missing_set_retry_rounds=1,
        missing_set_retry_delay_seconds=7,
    )

    assert [row["security_id"] for row in bars] == ["000001", "000002"]
    assert [row["security_id"] for row in statuses] == ["000001", "000002"]
    assert FakeBaostock.calls == ["sz.000001", "sz.000002", "sz.000001"]
    assert FakeBaostock.login_calls == 1
    assert sleeps == [7]
    assert [row["scan_round"] for row in logs] == [1, 1, 2]
    assert [row["acquired_at"] for row in logs] == [
        "2026-07-27T17:00:01+08:00",
        "2026-07-27T17:00:02+08:00",
        "2026-07-27T17:00:10+08:00",
    ]
    assert bars[0]["acquired_at"] == "2026-07-27T17:00:10+08:00"
    assert bars[1]["acquired_at"] == "2026-07-27T17:00:02+08:00"


def test_baostock_readiness_waits_for_all_three_target_indices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fetch_indices(_market_date: date, **_kwargs: object):
        nonlocal calls
        calls += 1
        codes = list(lane_runner.INDEX_CODES)
        if calls == 1:
            codes = codes[:-1]
        return (
            [
                {
                    "index_code": code,
                    "date": "2026-07-27",
                    "acquired_at": "2026-07-27T17:00:00+08:00",
                }
                for code in codes
            ],
            [],
        )

    sleeps: list[float] = []
    monkeypatch.setattr(lane_runner, "_baostock_fetch_index_day", fetch_indices)
    monkeypatch.setattr(
        lane_runner,
        "_now",
        lambda: datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(lane_runner.time, "sleep", sleeps.append)
    rows, _logs = lane_runner._baostock_wait_for_index_day(
        date(2026, 7, 27),
        deadline_at=datetime(2026, 7, 27, 19, 0, tzinfo=lane_runner.SH),
        poll_interval_seconds=60,
        socket_timeout_seconds=30,
        progress_path=tmp_path / "provider_readiness.json",
    )

    assert calls == 2
    assert sleeps == [60]
    assert {row["index_code"] for row in rows} == set(lane_runner.INDEX_CODES)
    progress = json.loads((tmp_path / "provider_readiness.json").read_text())
    assert progress["ready"] is True
    assert progress["polls"] == 2


def test_provider_market_collects_indices_without_current_local_index_data(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale node-local index seed cannot block a live provider market attempt."""

    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    monkeypatch.delenv("HERMES_ODS_SYMBOL_LIMIT", raising=False)
    paths = _paths(fixture_home, tmp_path / "shadow")
    (fixture_home / "index_daily.parquet").unlink()
    call_order: list[str] = []

    def fetch_stocks(codes, market_date, **_kwargs: object):
        call_order.append("stocks")
        return lane_runner._local_market_acquire(
            paths=paths,
            market_date=market_date,
            codes=codes,
        )

    def fetch_indices(market_date, **_kwargs: object):
        call_order.append("indices")
        day = market_date.isoformat()
        return (
            [
                {
                    "index_code": code,
                    "date": day,
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1.0,
                    "amount": 1.0,
                }
                for code in lane_runner.INDEX_CODES
            ],
            [],
        )

    monkeypatch.setattr(lane_runner, "_baostock_fetch_day", fetch_stocks)
    monkeypatch.setattr(lane_runner, "_baostock_fetch_index_day", fetch_indices)
    result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        force_preflight="both_open",
    )

    index = next(item for item in result.datasets if item.name == "index_daily_delta")
    assert result.status == "ready"
    assert index.status == "ready"
    assert index.row_count == len(lane_runner.INDEX_CODES)
    assert '"provider": "baostock"' in str(index.source_request)
    assert call_order == ["indices", "stocks"]
    acquisition_log = next(
        item for item in result.datasets if item.name == "acquisition_log"
    )
    payload = json.loads(Path(acquisition_log.path).read_text(encoding="utf-8"))
    latest_acquired_at = max(
        datetime.fromisoformat(row["acquired_at"])
        for row in payload["requests"]
        if row.get("acquired_at")
    )
    assert datetime.fromisoformat(acquisition_log.knowledge_cutoff) >= latest_acquired_at


def test_provider_market_requires_the_complete_index_contract(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    monkeypatch.delenv("HERMES_ODS_SYMBOL_LIMIT", raising=False)
    paths = _paths(fixture_home, tmp_path / "shadow")
    stock_calls = 0

    def fetch_stocks(codes, market_date, **_kwargs: object):
        nonlocal stock_calls
        stock_calls += 1
        return lane_runner._local_market_acquire(
            paths=paths,
            market_date=market_date,
            codes=codes,
        )

    def fetch_incomplete_indices(market_date, **_kwargs: object):
        return (
            [
                {"index_code": code, "date": market_date.isoformat(), "close": 1.0}
                for code in list(lane_runner.INDEX_CODES)[:-1]
            ],
            [],
        )

    monkeypatch.setattr(lane_runner, "_baostock_fetch_day", fetch_stocks)
    monkeypatch.setattr(
        lane_runner, "_baostock_fetch_index_day", fetch_incomplete_indices
    )
    result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        force_preflight="both_open",
    )

    failure = next(item for item in result.datasets if item.name == "daily_ohlcv_none_delta")
    assert failure.status == "failed"
    assert "provider readiness deadline reached" in (failure.detail or "")
    assert stock_calls == 0
    assert result.release_dir is None
    assert not list((tmp_path / "shadow").rglob("READY"))
    assert (result.attempt_dir / "landing" / "provider_readiness.json").is_file()


def test_all_null_daily_valuation_blocks_ready(fixture_home: Path, tmp_path: Path) -> None:
    _write_parquet(
        fixture_home / "valuation_daily.parquet",
        [
            {
                "stock_code": code,
                "date": "2026-07-24",
                "pe_ttm": None,
                "pb": None,
                "ps_ttm": None,
                "pcf_ttm": None,
            }
            for code in ("000001", "600519")
        ],
    )

    result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, tmp_path / "shadow"),
        force_preflight="both_open",
    )

    valuation = next(item for item in result.datasets if item.name == "valuation_daily_delta")
    assert valuation.status == "failed"
    assert "all valuation fields are null" in (valuation.detail or "")
    assert result.release_dir is None


def test_manual_recovery_uses_absolute_deadline_and_is_excluded_from_experiment(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    monkeypatch.setenv("HERMES_ODS_NODE_ID", "cn01")
    monkeypatch.delenv("HERMES_ODS_SYMBOL_LIMIT", raising=False)
    paths = _paths(fixture_home, tmp_path / "shadow")
    observed = datetime(2026, 7, 28, 11, 10, tzinfo=timezone.utc)
    deadline = datetime(2026, 7, 28, 23, 30, tzinfo=lane_runner.SH)
    deadlines: list[datetime] = []

    def wait_for_indices(market_date: date, *, deadline_at: datetime, **_kwargs: object):
        deadlines.append(deadline_at)
        day = market_date.isoformat()
        return (
            [
                {
                    "index_code": code,
                    "date": day,
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1.0,
                    "amount": 1.0,
                    "acquired_at": "2026-07-28T19:10:00+08:00",
                }
                for code in lane_runner.INDEX_CODES
            ],
            [],
        )

    def fetch_stocks(
        codes: list[str], market_date: date, *, deadline_at: datetime, **_kwargs: object
    ):
        deadlines.append(deadline_at)
        return lane_runner._local_market_acquire(
            paths=paths,
            market_date=market_date,
            codes=codes,
        )

    monkeypatch.setattr(lane_runner, "_now", lambda: observed)
    monkeypatch.setattr(lane_runner, "_baostock_wait_for_index_day", wait_for_indices)
    monkeypatch.setattr(lane_runner, "_baostock_fetch_day", fetch_stocks)
    result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        force_preflight="both_open",
        recovery_deadline_at=deadline,
    )

    assert result.status == "ready"
    assert result.run_mode == "manual_recovery"
    assert result.recovery_deadline_at == deadline
    assert deadlines == [deadline, deadline]
    acquisition_log = next(
        item for item in result.datasets if item.name == "acquisition_log"
    )
    payload = json.loads(Path(acquisition_log.path).read_text(encoding="utf-8"))
    assert payload["run_mode"] == "manual_recovery"
    assert payload["recovery_deadline_at"] == deadline.isoformat()
    assert payload["excluded_from_collection_window_experiment"] is True


def test_manual_recovery_rejects_naive_or_expired_deadline(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = datetime(2026, 7, 28, 11, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(lane_runner, "_now", lambda: observed)
    paths = _paths(fixture_home, tmp_path / "shadow")

    with pytest.raises(LaneRunError, match="timezone-aware"):
        run_lane(
            lane_id="market-daily-core",
            market_date=date(2026, 7, 28),
            schedule_config=SCHEDULE,
            paths=paths,
            recovery_deadline_at=datetime(2026, 7, 28, 23, 30),
        )
    with pytest.raises(LaneRunError, match="in the future"):
        run_lane(
            lane_id="market-daily-core",
            market_date=date(2026, 7, 28),
            schedule_config=SCHEDULE,
            paths=paths,
            recovery_deadline_at=datetime(2026, 7, 28, 18, 0, tzinfo=lane_runner.SH),
        )


def test_missing_preflight_no_ready(fixture_home: Path, tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        force_preflight="missing_or_disagree",
    )
    assert result.status == "failed"
    assert result.release_dir is None
    assert "no READY" in (result.detail or "")


def test_provider_refreshes_attempt_calendar_before_session_preflight(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    real_refresh = lane_runner.refresh_calendar

    def refresh_with_closed_target(**kwargs: object):
        return real_refresh(
            **kwargs,
            fetcher=lambda _start, _end: [
                {"trade_date": "2026-07-26", "is_open": 0},
                {"trade_date": "2026-07-27", "is_open": 0},
            ],
        )

    monkeypatch.setattr(lane_runner, "refresh_calendar", refresh_with_closed_target)
    result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 27),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, tmp_path / "shadow"),
    )

    assert result.status == "closed_session"
    assert result.preflight == "both_closed"
    assert result.preflight_evidence is not None
    assert result.preflight_evidence["status"] == "ready"
    assert result.preflight_evidence["target_date"] == "2026-07-27"
    calendar = next(item for item in result.datasets if item.name == "trading_calendar")
    assert "refresh_then_copy" in str(calendar.source_request)
    assert (
        result.attempt_dir / "landing" / "preflight" / "ref_trading_calendar.parquet"
    ).is_file()
    assert result.release_dir is not None
    assert (result.release_dir / "READY").is_file()


def test_calendar_refresh_failure_blocks_all_market_provider_calls(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    market_called = False

    def fail_refresh(**_kwargs: object):
        raise lane_runner.CalendarRefreshError("injected stale upstream calendar")

    def observe_market(**_kwargs: object):
        nonlocal market_called
        market_called = True
        return []

    monkeypatch.setattr(lane_runner, "refresh_calendar", fail_refresh)
    monkeypatch.setattr(lane_runner, "_build_market_open", observe_market)
    result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 27),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, tmp_path / "shadow"),
    )

    assert result.status == "failed"
    assert result.preflight == "calendar_refresh_failed"
    assert "no market or minute provider calls" in (result.detail or "")
    assert market_called is False
    assert result.release_dir is None
    assert not list((tmp_path / "shadow").rglob("READY"))


def test_calendar_refresh_failure_blocks_all_minute_provider_calls(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    minute_called = False

    def fail_refresh(**_kwargs: object):
        raise lane_runner.CalendarRefreshError("injected stale upstream calendar")

    def observe_minute(**_kwargs: object):
        nonlocal minute_called
        minute_called = True
        return []

    monkeypatch.setattr(lane_runner, "refresh_calendar", fail_refresh)
    monkeypatch.setattr(lane_runner, "_build_intraday_stream", observe_minute)
    result = run_lane(
        lane_id="intraday-archive",
        market_date=date(2026, 7, 27),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, tmp_path / "shadow"),
        stream="1m",
    )

    assert result.status == "failed"
    assert result.preflight == "calendar_refresh_failed"
    assert "no market or minute provider calls" in (result.detail or "")
    assert minute_called is False
    assert result.release_dir is None
    assert not list((tmp_path / "shadow").rglob("READY"))


def test_closed_session(fixture_home: Path, tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 25),
        schedule_config=SCHEDULE,
        paths=paths,
        force_preflight="both_closed",
    )
    assert result.status == "closed_session"
    assert result.release_dir is not None
    assert (result.release_dir / "READY").is_file()


def test_calendar_events(fixture_home: Path, tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    result = run_lane(
        lane_id="calendar-daily-events",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
    )
    assert result.status == "ready"
    assert result.release_dir is not None
    assert not (
        shadow / "releases" / "daily" / "2026-07-24" / "CURRENT.json"
    ).exists()


def test_calendar_events_do_not_replace_market_daily_current(
    fixture_home: Path, tmp_path: Path
) -> None:
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    market_result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        force_preflight="both_open",
    )
    assert market_result.status == "ready"
    current_path = shadow / "releases" / "daily" / "2026-07-24" / "CURRENT.json"
    market_current = current_path.read_bytes()

    event_result = run_lane(
        lane_id="calendar-daily-events",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
    )

    assert event_result.status == "ready"
    assert event_result.release_dir is not None
    assert current_path.read_bytes() == market_current


def test_daily_market_and_calendar_publish_independently_with_same_attempt_id(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    started = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(lane_runner, "_now", lambda: started)

    calendar_result = run_lane(
        lane_id="calendar-daily-events",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
    )
    market_result = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        force_preflight="both_open",
    )

    assert calendar_result.status == "ready"
    assert market_result.status == "ready"
    assert calendar_result.attempt_id == market_result.attempt_id
    assert calendar_result.release_dir is not None
    assert market_result.release_dir is not None
    assert calendar_result.release_dir.name == (
        f"calendar-daily-events-{calendar_result.attempt_id}"
    )
    assert market_result.release_dir.name == market_result.attempt_id
    assert (calendar_result.release_dir / "READY").is_file()
    assert (market_result.release_dir / "READY").is_file()
    ready_lanes = find_ready_daily_ods_lanes(
        release_root=shadow / "releases",
        market_date=date(2026, 7, 24),
    )
    assert ready_lanes["calendar_daily_events"].release_dir == calendar_result.release_dir
    assert ready_lanes["market_daily_core"].release_dir == market_result.release_dir


def test_weekly_reconciliation_classes(fixture_home: Path, tmp_path: Path) -> None:
    os.environ["HERMES_ODS_WEEKLY_INJECT"] = "demo"
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    result = run_lane(
        lane_id="weekly-corrections",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
    )
    assert result.status == "ready"
    recon = next(d for d in result.datasets if d.name == "reconciliation_summary")
    payload = json.loads(Path(recon.path).read_text(encoding="utf-8"))
    counts = payload["counts"]
    assert set(counts) >= {"same", "missing_in_daily", "changed_upstream", "conflict"}
    assert sum(counts.values()) > 0
    # not all-zero placeholder
    assert any(v > 0 for v in counts.values())
    os.environ.pop("HERMES_ODS_WEEKLY_INJECT", None)


def test_intraday_watermark_only_on_ready(fixture_home: Path, tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    result = run_lane(
        lane_id="intraday-archive",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        stream="5m",
        force_preflight="both_open",
        symbols=["000001"],
    )
    assert result.status == "ready"
    wm = shadow / "watermarks" / "intraday-5m.watermark.json"
    assert wm.is_file()
    payload = json.loads(wm.read_text(encoding="utf-8"))
    assert payload["committed_market_date"] == "2026-07-24"
    assert payload["row_count"] > 0
    assert wm.stat().st_mode & 0o777 == 0o644
    assert (result.attempt_dir / "attempt.json").stat().st_mode & 0o777 == 0o644
    assert (result.attempt_dir / "datasets.json").stat().st_mode & 0o777 == 0o644
    assert result.release_dir is not None
    assert result.release_dir.stat().st_mode & 0o777 == 0o755
    expected_schedule_sha = f"sha256:{hashlib.sha256(SCHEDULE.read_bytes()).hexdigest()}"
    attempt = json.loads((result.attempt_dir / "attempt.json").read_text(encoding="utf-8"))
    assert attempt["schedule_id"] == "hermes-ods-schedule-v1"
    assert (
        attempt["schedule_contract_revision"]
        == "hermes-full-ods-scope-cn01-throughput-20260731"
    )
    assert attempt["schedule_config_sha256"] == expected_schedule_sha
    release = json.loads((result.release_dir / "release.json").read_text(encoding="utf-8"))
    assert release["schedule_contract_id"] == "hermes-ods-schedule-v1"
    assert (
        release["schedule_contract_revision"]
        == "hermes-full-ods-scope-cn01-throughput-20260731"
    )
    assert release["schedule_config_sha256"] == expected_schedule_sha
    meta_dataset = next(item for item in result.datasets if item.name == "intraday_meta")
    meta = json.loads(Path(meta_dataset.path).read_text(encoding="utf-8"))
    assert meta["session_complete"] is True
    assert meta["auction_session_complete"] is True
    assert meta["session_phase"] == "auction"
    assert meta["session_scope"] == "continuous_trading_and_closing_call_auction"
    assert meta["covers_after_hours_fixed_price"] is False
    assert meta["after_hours_fixed_price_contract"] == "after_hours_fixed_price_trades.v1"


def test_intraday_timeout_records_terminal_failure(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)

    def timeout(**_kwargs: object) -> list[lane_runner.DatasetStatus]:
        raise subprocess.TimeoutExpired(cmd=["minute-collector"], timeout=600)

    monkeypatch.setattr(lane_runner, "_build_intraday_stream", timeout)

    with pytest.raises(subprocess.TimeoutExpired):
        run_lane(
            lane_id="intraday-archive",
            market_date=date(2026, 7, 24),
            schedule_config=SCHEDULE,
            paths=paths,
            stream="5m",
            force_preflight="both_open",
        )

    attempts = list(
        (shadow / "attempts" / "intraday-archive" / "5m" / "2026-07-24").iterdir()
    )
    assert len(attempts) == 1
    payload = json.loads((attempts[0] / "attempt.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["finished_at"] is not None
    assert payload["release_dir"] is None
    assert payload["datasets"] == []
    assert "TimeoutExpired" in payload["detail"]
    assert json.loads((attempts[0] / "datasets.json").read_text())["datasets"] == []
    assert not list((shadow / "releases").rglob("READY"))
    assert not (shadow / "watermarks" / "intraday-5m.watermark.json").exists()


def test_minute_collector_fetch_mode_is_env_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production default is incr; archive soaks must pin backfill + max_pages."""
    monkeypatch.delenv("HERMES_ODS_MINUTE_MODE", raising=False)
    monkeypatch.delenv("HERMES_ODS_MINUTE_MAX_PAGES", raising=False)

    mode, pages = lane_runner._minute_collector_fetch_mode(
        market_date=date(2026, 7, 20), bar_size="1m", as_of=date(2026, 7, 26)
    )
    assert mode == "incr"
    assert pages is None

    monkeypatch.setenv("HERMES_ODS_MINUTE_MODE", "incr")
    monkeypatch.setenv("HERMES_ODS_MINUTE_MAX_PAGES", "3")
    mode, pages = lane_runner._minute_collector_fetch_mode(
        market_date=date(2026, 7, 20), bar_size="1m"
    )
    assert mode == "incr"
    assert pages is None

    monkeypatch.setenv("HERMES_ODS_MINUTE_MODE", "backfill")
    monkeypatch.setenv("HERMES_ODS_MINUTE_MAX_PAGES", "3")
    mode, pages = lane_runner._minute_collector_fetch_mode(
        market_date=date(2026, 7, 20), bar_size="1m"
    )
    assert mode == "backfill"
    assert pages == 3

    monkeypatch.delenv("HERMES_ODS_MINUTE_MAX_PAGES", raising=False)
    with pytest.raises(lane_runner.LaneRunError, match="MAX_PAGES"):
        lane_runner._minute_collector_fetch_mode(
            market_date=date(2026, 7, 20), bar_size="1m"
        )

    monkeypatch.setenv("HERMES_ODS_MINUTE_MAX_PAGES", "51")
    with pytest.raises(lane_runner.LaneRunError, match="safety cap"):
        lane_runner._minute_collector_fetch_mode(
            market_date=date(2026, 7, 20), bar_size="1m"
        )

    monkeypatch.setenv("HERMES_ODS_MINUTE_MAX_PAGES", "not-an-int")
    with pytest.raises(lane_runner.LaneRunError, match="must be an integer"):
        lane_runner._minute_collector_fetch_mode(
            market_date=date(2026, 7, 20), bar_size="1m"
        )

    monkeypatch.setenv("HERMES_ODS_MINUTE_MODE", "history")
    with pytest.raises(lane_runner.LaneRunError, match="incr|backfill"):
        lane_runner._minute_collector_fetch_mode(
            market_date=date(2026, 7, 20), bar_size="1m"
        )


@pytest.mark.parametrize("stage", ["acquisition", "pre_seal", "transfer"])
def test_deterministic_failure_injection_never_publishes_ready(
    fixture_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    shadow = tmp_path / "shadow" / "matrix"
    paths = _paths(fixture_home, shadow)
    monkeypatch.setenv("HERMES_ODS_ENABLE_FAILURE_INJECTION", "1")
    monkeypatch.setenv("HERMES_ODS_FAILURE_INJECTION_STAGE", stage)

    with pytest.raises(lane_runner.LaneRunError, match=stage):
        run_lane(
            lane_id="calendar-daily-events",
            market_date=date(2026, 7, 24),
            schedule_config=SCHEDULE,
            paths=paths,
        )

    attempts = list((shadow / "attempts").glob("**/attempt.json"))
    assert len(attempts) == 1
    payload = json.loads(attempts[0].read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["release_dir"] is None
    assert not list((shadow / "releases").glob("**/READY"))


def test_failure_injection_requires_explicit_enable(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_FAILURE_INJECTION_STAGE", "acquisition")
    monkeypatch.delenv("HERMES_ODS_ENABLE_FAILURE_INJECTION", raising=False)

    with pytest.raises(lane_runner.LaneRunError, match="requires"):
        run_lane(
            lane_id="calendar-daily-events",
            market_date=date(2026, 7, 24),
            schedule_config=SCHEDULE,
            paths=_paths(fixture_home, tmp_path / "shadow"),
        )


def test_resource_group_lock_blocks_parallel(fixture_home: Path, tmp_path: Path) -> None:
    import fcntl

    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    paths.lock_root.mkdir(parents=True, exist_ok=True)
    lock = paths.lock_root / "resource-group-pve_market_api.lock"
    handle = lock.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(Exception) as exc:
            run_lane(
                lane_id="market-daily-core",
                market_date=date(2026, 7, 24),
                schedule_config=SCHEDULE,
                paths=paths,
                force_preflight="both_open",
            )
        assert "lock busy" in str(exc.value).lower() or "busy" in str(exc.value).lower()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def test_intraday_unexplained_missing_blocks_ready(
    fixture_home: Path, tmp_path: Path
) -> None:
    # only one of two universe codes has a complete partition
    minute = Path(os.environ["ASTOCK_MINUTE_ROOT"]) / "data" / "prod_5m"
    (minute / "600519_5m.parquet").unlink()
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    result = run_lane(
        lane_id="intraday-archive",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        stream="5m",
        force_preflight="both_open",
        symbols=["000001", "600519"],
    )
    assert result.status in {"partial", "failed"}
    assert result.release_dir is None
    assert not list(shadow.rglob("READY"))
    bars = next(d for d in result.datasets if d.name == "intraday_bars")
    assert bars.status == "failed"


def test_intraday_open_ended_suspension_justifies_no_bars(
    fixture_home: Path, tmp_path: Path
) -> None:
    _write_parquet(
        fixture_home / "ref_suspensions.parquet",
        [
            {
                "stock_code": "600519",
                "suspend_start": "2026-07-23",
                "suspend_end": None,
                "announce_date": "2026-07-23",
            }
        ],
    )
    minute = Path(os.environ["ASTOCK_MINUTE_ROOT"]) / "data" / "prod_5m"
    (minute / "600519_5m.parquet").unlink()

    result = run_lane(
        lane_id="intraday-archive",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, tmp_path / "shadow"),
        stream="5m",
        force_preflight="both_open",
        symbols=["000001", "600519"],
    )

    assert result.status == "ready"
    coverage = next(d for d in result.datasets if d.name == "intraday_coverage")
    rows = pq.read_table(Path(coverage.path)).to_pylist()
    suspended = next(row for row in rows if row["security_id"] == "600519")
    assert suspended == {
        "security_id": "600519",
        "status": "justified_no_data",
        "row_count": 0,
        "observed_maximum": None,
        "reason": "suspended",
        "source": "ref_suspensions.parquet",
        "suspend_start": "2026-07-23",
        "suspend_end": None,
        "knowledge_cutoff": "2026-07-23",
    }


def test_intraday_zero_turnover_daily_bar_justifies_no_bars(
    fixture_home: Path, tmp_path: Path
) -> None:
    daily_path = fixture_home / "daily_ohlcv_none.parquet"
    daily_rows = pq.read_table(daily_path).to_pylist()
    for row in daily_rows:
        if row["stock_code"] == "600519" and str(row["date"])[:10] == "2026-07-24":
            row.update(
                {
                    "open": 10.5,
                    "high": 10.5,
                    "low": 10.5,
                    "close": 10.5,
                    "volume": 0.0,
                    "amount": 0.0,
                }
            )
    _write_parquet(daily_path, daily_rows)
    minute = Path(os.environ["ASTOCK_MINUTE_ROOT"]) / "data" / "prod_5m"
    (minute / "600519_5m.parquet").unlink()

    result = run_lane(
        lane_id="intraday-archive",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, tmp_path / "shadow"),
        stream="5m",
        force_preflight="both_open",
        symbols=["000001", "600519"],
    )

    assert result.status == "ready"
    coverage = next(d for d in result.datasets if d.name == "intraday_coverage")
    rows = pq.read_table(Path(coverage.path)).to_pylist()
    zero_turnover = next(row for row in rows if row["security_id"] == "600519")
    assert zero_turnover["status"] == "justified_no_data"
    assert zero_turnover["reason"] == "zero_turnover_daily_bar"
    assert zero_turnover["source"] == "daily_ohlcv_none.parquet"
    assert zero_turnover["volume"] == 0.0
    assert zero_turnover["amount"] == 0.0
    assert zero_turnover["source_sha256"].startswith("sha256:")
    assert len(zero_turnover["source_sha256"]) == len("sha256:") + 64


def test_intraday_missing_middle_bar_blocks_ready(
    fixture_home: Path, tmp_path: Path
) -> None:
    minute = Path(os.environ["ASTOCK_MINUTE_ROOT"]) / "data" / "prod_5m"
    partition = minute / "000001_5m.parquet"
    rows = pq.read_table(partition).to_pylist()
    _write_parquet(partition, [row for row in rows if "10:00:00" not in str(row["ts"])])

    shadow = tmp_path / "shadow"
    result = run_lane(
        lane_id="intraday-archive",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, shadow),
        stream="5m",
        force_preflight="both_open",
        symbols=["000001"],
    )

    assert result.status == "partial"
    assert result.release_dir is None
    coverage = pq.read_table(
        result.attempt_dir / "landing" / "intraday_5m_coverage.parquet"
    ).to_pylist()
    assert coverage[0]["reason"] == "incomplete_session"
    assert coverage[0]["missing_bar_count"] == 1
    assert coverage[0]["observed_maximum"] == "2026-07-24 15:00:00"
    assert not list(shadow.rglob("READY"))


def test_acquire_mode_fail_closed(fixture_home: Path, tmp_path: Path) -> None:
    from quanthus.pipelines.lane_runner import LaneRunError, _acquire_mode

    os.environ["HERMES_ODS_ACQUIRE_MODE"] = "auto"
    with pytest.raises(LaneRunError):
        _acquire_mode()
    os.environ["HERMES_ODS_ACQUIRE_MODE"] = "local"
    assert _acquire_mode() == "local"


def test_event_watermark_committed_on_ready(
    fixture_home: Path, tmp_path: Path
) -> None:
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    result = run_lane(
        lane_id="calendar-daily-events",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
    )
    assert result.status == "ready"
    wm = shadow / "watermarks" / "calendar-daily-events.json"
    assert wm.is_file()
    payload = json.loads(wm.read_text(encoding="utf-8"))
    assert payload.get("committed_watermark")
    assert payload.get("market_date") == "2026-07-24"


def test_weekly_has_independent_correction_revision(
    fixture_home: Path, tmp_path: Path
) -> None:
    os.environ["HERMES_ODS_WEEKLY_INJECT"] = "demo"
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    result = run_lane(
        lane_id="weekly-corrections",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
    )
    assert result.status == "ready"
    names = {d.name for d in result.datasets}
    assert "weekly_correction_revision" in names
    assert "weekly_correction_meta" in names
    meta = next(d for d in result.datasets if d.name == "weekly_correction_meta")
    payload = json.loads(Path(meta.path).read_text(encoding="utf-8"))
    assert payload.get("provider")
    os.environ.pop("HERMES_ODS_WEEKLY_INJECT", None)


def test_bootstrap_refused(fixture_home: Path, tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    result = run_lane(
        lane_id="bootstrap",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        issue_ref="manual/#41",
    )
    assert result.status == "failed"
    assert result.release_dir is None


def test_provider_event_request_failure_never_ready_or_advances_watermark(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    monkeypatch.delenv("HERMES_ODS_PROVIDER_SPEC", raising=False)
    monkeypatch.setattr(
        lane_runner,
        "_cninfo_fetch_announcements",
        lambda **_kwargs: (
            [],
            [
                {
                    "security_id": "000001",
                    "response_error_code": "1",
                    "response_error_msg": "injected timeout",
                }
            ],
        ),
    )
    shadow = tmp_path / "shadow"
    result = run_lane(
        lane_id="calendar-daily-events",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, shadow),
    )
    announcements = next(d for d in result.datasets if d.name == "announcements_delta")
    assert announcements.status == "failed"
    assert result.status in {"partial", "failed"}
    assert result.release_dir is None
    assert not (shadow / "watermarks" / "calendar-daily-events.json").exists()
    assert not list(shadow.rglob("READY"))


def test_cninfo_retries_one_security_without_duplicate_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "announcements": [
                    {
                        "announcementId": "notice-1",
                        "announcementTitle": "Notice",
                        "announcementTime": 1786406400000,
                        "secName": "Example",
                    }
                ],
                "hasMore": False,
            }

    class FakeSession:
        def post(self, *_args: object, **_kwargs: object) -> FakeResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("injected timeout")
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setenv("ASTOCK_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(Session=FakeSession))

    rows, logs = lane_runner._cninfo_fetch_announcements(
        codes=["000001"],
        start=date(2026, 8, 7),
        end=date(2026, 8, 7),
    )

    assert calls == 2
    assert len(rows) == 1
    assert rows[0]["ann_id"] == "notice-1"
    assert logs[0]["attempts"] == 2
    assert logs[0]["response_error_code"] == "0"
    assert logs[0]["response_rows"] == 1


def test_cninfo_missing_set_retry_repairs_only_failed_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class FakeResponse:
        status_code = 200

        def __init__(self, code: str) -> None:
            self.code = code

        def json(self) -> dict[str, object]:
            return {
                "announcements": [
                    {
                        "announcementId": f"notice-{self.code}",
                        "announcementTitle": "Notice",
                        "announcementTime": 1786406400000,
                        "secName": "Example",
                    }
                ],
                "hasMore": False,
            }

    class FakeSession:
        def post(self, *_args: object, **kwargs: object) -> FakeResponse:
            code = str(kwargs["data"]["stock"]).split(",", 1)[0]
            calls.append(code)
            if code == "000002" and calls.count(code) <= 3:
                raise TimeoutError("injected timeout")
            return FakeResponse(code)

        def close(self) -> None:
            return None

    monkeypatch.setenv("ASTOCK_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(Session=FakeSession))

    rows, logs = lane_runner._cninfo_fetch_announcements(
        codes=["000001", "000002"],
        start=date(2026, 9, 18),
        end=date(2026, 9, 18),
        missing_set_retry_delay_seconds=0,
    )

    assert calls == ["000001", "000002", "000002", "000002", "000002"]
    assert [row["ann_id"] for row in rows] == ["notice-000001", "notice-000002"]
    assert len(logs) == 2
    repaired = logs[1]
    assert repaired["response_error_code"] == "0"
    assert repaired["attempts"] == 4
    assert repaired["scan_round"] == 2
    assert repaired["missing_set_retry_errors"] == [
        {
            "scan_round": 1,
            "response_error_code": "1",
            "response_error_msg": "injected timeout",
        }
    ]


def test_cninfo_missing_set_retry_exhaustion_remains_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    class FakeSession:
        def post(self, *_args: object, **_kwargs: object) -> None:
            nonlocal calls
            calls += 1
            raise TimeoutError("persistent timeout")

        def close(self) -> None:
            return None

    monkeypatch.setenv("ASTOCK_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(Session=FakeSession))

    rows, logs = lane_runner._cninfo_fetch_announcements(
        codes=["000001"],
        start=date(2026, 9, 18),
        end=date(2026, 9, 18),
        missing_set_retry_delay_seconds=0,
    )

    assert rows == []
    assert calls == 6
    assert len(logs) == 1
    assert logs[0]["response_error_code"] == "1"
    assert logs[0]["attempts"] == 6
    assert logs[0]["scan_round"] == 2


def test_weekly_provider_error_is_not_a_business_conflict(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider_spec = tmp_path / "providers.json"
    provider_spec.write_text(
        json.dumps(
            {
                "lanes": {
                    "weekly-corrections": {
                        name: {
                            "argv": ["/bin/false", "{output}"],
                            "provider": "injected-failure",
                        }
                        for name in (
                            "shareholder_count",
                            "financial_quarterly",
                            "corporate_actions",
                            "dividends",
                            "adjustment_evidence",
                            "st_announcement_trail",
                        )
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    monkeypatch.setenv("HERMES_ODS_PROVIDER_SPEC", str(provider_spec))

    class FailedQuery:
        error_code = "1"
        error_msg = "injected provider failure"

    class Login:
        error_code = "0"
        error_msg = "ok"

    class FakeBaostock:
        @staticmethod
        def login():
            return Login()

        @staticmethod
        def logout():
            return None

        @staticmethod
        def query_history_k_data_plus(*_args, **_kwargs):
            return FailedQuery()

    monkeypatch.setitem(__import__("sys").modules, "baostock", FakeBaostock)
    result = run_lane(
        lane_id="weekly-corrections",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, tmp_path / "shadow"),
    )
    assert result.status in {"partial", "failed"}
    assert result.release_dir is None
    recon = next(d for d in result.datasets if d.name == "reconciliation_keys")
    assert recon.status == "failed"
    assert "acquisition failed" in (recon.detail or "")
    assert not list((tmp_path / "shadow").rglob("READY"))


@pytest.mark.parametrize(
    ("provider", "native_frequency", "expected_status"),
    [
        ("sina-minute-jsonp", "5m", "smoke_only"),
        ("tencent-minute-query", "1m", "failed"),
    ],
)
def test_provider_minute_is_attempt_local_and_symbol_limit_is_smoke_only(
    fixture_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    native_frequency: str,
    expected_status: str,
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    monkeypatch.setenv("HERMES_ODS_SYMBOL_LIMIT", "1")
    observed_raw_homes: list[Path] = []

    def fake_collect(**kwargs):
        raw_home = Path(kwargs["raw_home"])
        observed_raw_homes.append(raw_home)
        _write_parquet(
            raw_home / "000001_5m.parquet",
            [
                {
                    "stock_code": "000001",
                    "ts": timestamp,
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1.0,
                }
                for timestamp in sorted(
                    lane_runner._expected_bar_timestamps(date(2026, 7, 24), "5m")
                )
            ],
        )
        (raw_home / "checkpoint.json").write_text(
            json.dumps(
                {
                    "symbols": {
                        "000001": {
                            "status": "done",
                            "provider": provider,
                            "native_frequency": native_frequency,
                            "derived_from": "1m" if native_frequency == "1m" else None,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return {
            "exit_code": 0,
            "layer_dir": str(raw_home),
            "isolation": "attempt-local",
        }

    monkeypatch.setattr(lane_runner, "_run_minute_collector", fake_collect)
    shadow = tmp_path / "shadow"
    result = run_lane(
        lane_id="intraday-archive",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, shadow),
        stream="5m",
        force_preflight="both_open",
    )
    assert observed_raw_homes
    assert "attempts/intraday-archive/5m" in str(observed_raw_homes[0])
    bars = next(d for d in result.datasets if d.name == "intraday_bars")
    assert bars.status == expected_status
    assert result.status == "partial"
    assert result.release_dir is None
    assert not list(shadow.rglob("READY"))


def test_minute_no_data_requires_structured_pit_evidence(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "local")
    minute = Path(os.environ["ASTOCK_MINUTE_ROOT"]) / "data" / "prod_5m"
    (minute / "600519_5m.parquet").unlink()
    status_path = fixture_home / "daily_status_observation.parquet"
    status_rows = pq.read_table(status_path).to_pylist()
    for row in status_rows:
        if row["stock_code"] == "600519" and str(row["date"])[:10] == "2026-07-24":
            row["tradestatus"] = "0"
    _write_parquet(status_path, status_rows)
    result = run_lane(
        lane_id="intraday-archive",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, tmp_path / "shadow"),
        stream="5m",
        force_preflight="both_open",
        symbols=["000001", "600519"],
    )
    assert result.status == "ready"
    coverage = next(d for d in result.datasets if d.name == "intraday_coverage")
    rows = pq.read_table(Path(coverage.path)).to_pylist()
    suspended = next(row for row in rows if row["security_id"] == "600519")
    assert suspended["status"] == "justified_no_data"
    assert suspended["source"] == "daily_status_observation.parquet"


def test_minute_uses_verified_same_day_market_release_status(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "local")
    status_path = fixture_home / "daily_status_observation.parquet"
    status_rows = pq.read_table(status_path).to_pylist()
    for row in status_rows:
        if row["stock_code"] == "600519" and str(row["date"])[:10] == "2026-07-24":
            row["tradestatus"] = "0"
    _write_parquet(status_path, status_rows)

    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    market = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        force_preflight="both_open",
    )
    assert market.status == "ready"
    assert market.release_dir is not None

    # The minute lane must consume the immutable market release, not depend on
    # a mutable node-local status snapshot remaining in place.
    status_path.unlink()
    minute = Path(os.environ["ASTOCK_MINUTE_ROOT"]) / "data" / "prod_5m"
    suspended_partition = minute / "600519_5m.parquet"
    suspended_rows = pq.read_table(suspended_partition).to_pylist()
    for row in suspended_rows:
        row["volume"] = 5.877471754111438e-39
        if str(row["ts"]).endswith("11:30:00"):
            row["ts"] = "2026-07-24 13:00:00"
    _write_parquet(suspended_partition, suspended_rows)
    result = run_lane(
        lane_id="intraday-archive",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        stream="5m",
        force_preflight="both_open",
        symbols=["000001", "600519"],
    )

    assert result.status == "ready"
    coverage = next(d for d in result.datasets if d.name == "intraday_coverage")
    rows = pq.read_table(Path(coverage.path)).to_pylist()
    suspended = next(row for row in rows if row["security_id"] == "600519")
    assert suspended["status"] == "justified_no_data"
    assert suspended["source"] == "daily_status_observation_delta.parquet"
    assert suspended["release_id"].startswith("hermes-market-daily-core-")
    assert suspended["provider_rows_discarded"] == 48
    bars = next(d for d in result.datasets if d.name == "intraday_bars")
    assert bars.row_count == 48


def test_minute_rejects_corrupt_market_release_status(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "local")
    status_path = fixture_home / "daily_status_observation.parquet"
    status_rows = pq.read_table(status_path).to_pylist()
    for row in status_rows:
        if row["stock_code"] == "600519" and str(row["date"])[:10] == "2026-07-24":
            row["tradestatus"] = "0"
    _write_parquet(status_path, status_rows)

    shadow = tmp_path / "shadow"
    paths = _paths(fixture_home, shadow)
    market = run_lane(
        lane_id="market-daily-core",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        force_preflight="both_open",
    )
    assert market.release_dir is not None
    (market.release_dir / "artifacts" / "daily_status_observation_delta.parquet").write_bytes(
        b"corrupt"
    )

    status_path.unlink()
    minute = Path(os.environ["ASTOCK_MINUTE_ROOT"]) / "data" / "prod_5m"
    (minute / "600519_5m.parquet").unlink()
    result = run_lane(
        lane_id="intraday-archive",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=paths,
        stream="5m",
        force_preflight="both_open",
        symbols=["000001", "600519"],
    )

    assert result.status == "partial"
    assert result.release_dir is None
    bars = next(d for d in result.datasets if d.name == "intraday_bars")
    assert "unexplained_missing=1" in (bars.detail or "")


def test_ad_hoc_nodata_marker_does_not_justify_missing_bars(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "local")
    minute = Path(os.environ["ASTOCK_MINUTE_ROOT"]) / "data" / "prod_5m"
    partition = minute / "600519_5m.parquet"
    _write_parquet(partition, [])
    partition.with_suffix(".parquet.nodata").write_text("no data", encoding="utf-8")
    result = run_lane(
        lane_id="intraday-archive",
        market_date=date(2026, 7, 24),
        schedule_config=SCHEDULE,
        paths=_paths(fixture_home, tmp_path / "shadow"),
        stream="5m",
        force_preflight="both_open",
        symbols=["000001", "600519"],
    )
    assert result.status in {"partial", "failed"}
    assert result.release_dir is None
    bars = next(d for d in result.datasets if d.name == "intraday_bars")
    assert "unexplained_missing=1" in (bars.detail or "")
