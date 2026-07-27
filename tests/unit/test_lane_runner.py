"""Unit tests for Hermes ODS five-lane runner (audit-fix v2)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import quanthus.pipelines.lane_runner as lane_runner
from quanthus.pipelines.lane_runner import (
    LanePaths,
    expected_timer_oncalendar,
    run_lane,
    session_preflight,
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
    assert expected["market-daily-core"] == "Mon..Fri *-*-* 15:40:00 Asia/Shanghai"
    assert expected["calendar-daily-events"] == "*-*-* 19:30:00 Asia/Shanghai"
    assert expected["weekly-corrections"] == "Sat *-*-* 08:00:00 Asia/Shanghai"
    assert expected["intraday-1m"] == "Mon..Fri *-*-* 20:30:00 Asia/Shanghai"
    assert expected["intraday-5m"] == "Mon..Fri *-*-* 23:00:00 Asia/Shanghai"

    root = Path("ops/systemd")
    assert "15:40:00 Asia/Shanghai" in (root / "hermes-ods-market-daily-core.timer").read_text()
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


def test_provider_market_collects_indices_without_current_local_index_data(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale node-local index seed cannot block a live provider market attempt."""

    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    monkeypatch.delenv("HERMES_ODS_SYMBOL_LIMIT", raising=False)
    paths = _paths(fixture_home, tmp_path / "shadow")
    (fixture_home / "index_daily.parquet").unlink()

    def fetch_stocks(codes, market_date):
        return lane_runner._local_market_acquire(
            paths=paths,
            market_date=market_date,
            codes=codes,
        )

    def fetch_indices(market_date):
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


def test_provider_market_requires_the_complete_index_contract(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_ODS_ACQUIRE_MODE", "provider")
    monkeypatch.delenv("HERMES_ODS_SYMBOL_LIMIT", raising=False)
    paths = _paths(fixture_home, tmp_path / "shadow")

    def fetch_stocks(codes, market_date):
        return lane_runner._local_market_acquire(
            paths=paths,
            market_date=market_date,
            codes=codes,
        )

    def fetch_incomplete_indices(market_date):
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

    index = next(item for item in result.datasets if item.name == "index_daily_delta")
    assert index.status == "failed"
    assert index.detail == "missing_index_coverage=399001.SZ"
    assert result.release_dir is None
    assert not list((tmp_path / "shadow").rglob("READY"))


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
    assert attempt["schedule_contract_revision"] == "calendar-refresh-first-20260727"
    assert attempt["schedule_config_sha256"] == expected_schedule_sha
    release = json.loads((result.release_dir / "release.json").read_text(encoding="utf-8"))
    assert release["schedule_contract_id"] == "hermes-ods-schedule-v1"
    assert release["schedule_contract_revision"] == "calendar-refresh-first-20260727"
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


def test_provider_minute_is_attempt_local_and_symbol_limit_is_smoke_only(
    fixture_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    assert bars.status == "smoke_only"
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
