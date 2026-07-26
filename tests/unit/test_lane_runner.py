"""Unit tests for Hermes ODS five-lane runner (audit-fix v2)."""

from __future__ import annotations

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
                "code": "000001",
                "date": open_day.isoformat(),
                "close": 3000.0,
            }
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

    # minute partitions (must include expected last bar 15:00 for session completeness)
    minute = tmp_path / "minute" / "data"
    for layer, bar in (("research_1m", "1m"), ("prod_5m", "5m")):
        d = minute / layer
        d.mkdir(parents=True)
        for c in codes:
            _write_parquet(
                d / f"{c}_{bar}.parquet",
                [
                    {
                        "stock_code": c,
                        "ts": f"{open_day.isoformat()} 09:31:00",
                        "open": 10.0,
                        "high": 10.1,
                        "low": 9.9,
                        "close": 10.0,
                        "volume": 100.0,
                    },
                    {
                        "stock_code": c,
                        "ts": f"{open_day.isoformat()} 15:00:00",
                        "open": 10.0,
                        "high": 10.2,
                        "low": 9.8,
                        "close": 10.1,
                        "volume": 200.0,
                    },
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
    assert expected["market-daily-core"] == "Mon..Fri *-*-* 15:10:00 Asia/Shanghai"
    assert expected["calendar-daily-events"] == "*-*-* 19:30:00 Asia/Shanghai"
    assert expected["weekly-corrections"] == "Sat *-*-* 08:00:00 Asia/Shanghai"
    assert expected["intraday-1m"] == "Mon..Fri *-*-* 20:30:00 Asia/Shanghai"
    assert expected["intraday-5m"] == "Mon..Fri *-*-* 23:00:00 Asia/Shanghai"

    root = Path("ops/systemd")
    assert "15:10:00 Asia/Shanghai" in (root / "hermes-ods-market-daily-core.timer").read_text()
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
        assert "date +%F" in text


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
    # no READY anywhere under shadow for this attempt
    assert not list(shadow.rglob("READY"))


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
                    "ts": "2026-07-24 15:00:00",
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 1.0,
                }
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
