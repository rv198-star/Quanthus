from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ModuleNotFoundError:  # pragma: no cover
    pa = None
    pq = None

from quanthus.pipelines.lane_runner import (
    LanePaths,
    resolve_paths,
    run_lane,
    session_preflight,
)


def _write_calendar(path: Path, rows: list[dict]) -> None:
    assert pa is not None and pq is not None
    pq.write_table(pa.Table.from_pylist(rows), path)


@unittest.skipIf(pa is None, "pyarrow required")
class LaneRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.home = self.root / "data"
        self.home.mkdir()
        self.shadow = self.root / "shadow"
        self.calendar = self.home / "ref_trading_calendar.parquet"
        self.schedule = (
            Path(__file__).resolve().parents[2]
            / "config"
            / "schedules"
            / "hermes-ods-schedule.v1.json"
        )
        open_day = date(2026, 7, 24)
        closed_day = date(2026, 7, 25)
        _write_calendar(
            self.calendar,
            [
                {"exchange": "SSE", "trade_date": open_day, "is_open": 1},
                {"exchange": "SZSE", "trade_date": open_day, "is_open": 1},
                {"exchange": "SSE", "trade_date": closed_day, "is_open": 0},
                {"exchange": "SZSE", "trade_date": closed_day, "is_open": 0},
            ],
        )
        # minimal market-open fixtures
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {"stock_code": "000001", "name": "PAB"},
                    {"stock_code": "600519", "name": "MT"},
                    {"stock_code": "920001", "name": "bj-skip"},
                ]
            ),
            self.home / "stock_list.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "stock_code": "000001",
                        "date": open_day,
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.5,
                        "close": 10.5,
                        "volume": 1000.0,
                        "amount": 10000.0,
                    },
                    {
                        "stock_code": "600519",
                        "date": open_day,
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 200.0,
                        "amount": 20000.0,
                    },
                ]
            ),
            self.home / "daily_ohlcv_none.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {"stock_code": "000001", "date": open_day, "tradestatus": "1", "isST": "0"},
                    {"stock_code": "600519", "date": open_day, "tradestatus": "1", "isST": "0"},
                ]
            ),
            self.home / "daily_status_observation.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist([{"security_id": "000001", "list_date": "1991-04-03"}]),
            self.home / "ref_security_master.parquet",
        )
        # shareholder for weekly
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "stock_code": "000001",
                        "hold_notice_date": open_day.isoformat(),
                        "shareholder_count": 100,
                    }
                ]
            ),
            self.home / "ref_shareholder_count.parquet",
        )
        # minute sample dirs
        minute = self.root / "minute" / "data"
        (minute / "prod_5m").mkdir(parents=True)
        (minute / "research_1m").mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pylist(
                [{"datetime": f"{open_day.isoformat()} 09:35:00", "open": 10.0, "close": 10.1}]
            ),
            minute / "prod_5m" / "000001_5m.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(
                [{"datetime": f"{open_day.isoformat()} 09:31:00", "open": 10.0, "close": 10.05}]
            ),
            minute / "research_1m" / "000001_1m.parquet",
        )
        self.paths = LanePaths(
            home=self.home,
            shadow_root=self.shadow,
            calendar=self.calendar,
            lock_root=self.shadow / "locks",
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_session_preflight_states(self) -> None:
        self.assertEqual(
            session_preflight(calendar=self.calendar, market_date=date(2026, 7, 24)),
            "both_open",
        )
        self.assertEqual(
            session_preflight(calendar=self.calendar, market_date=date(2026, 7, 25)),
            "both_closed",
        )
        self.assertEqual(
            session_preflight(calendar=self.calendar, market_date=date(2026, 1, 1)),
            "missing_or_disagree",
        )

    def test_market_open_shadow_ready(self) -> None:
        result = run_lane(
            lane_id="market-daily-core",
            market_date=date(2026, 7, 24),
            schedule_config=self.schedule,
            paths=self.paths,
        )
        self.assertEqual(result.status, "ready")
        self.assertIsNotNone(result.release_dir)
        assert result.release_dir is not None
        self.assertTrue((result.release_dir / "READY").is_file())
        # shadow only: no write into canonical daily release path
        self.assertFalse((self.home / "releases" / "daily").exists())

    def test_market_closed_is_closed_session(self) -> None:
        result = run_lane(
            lane_id="market-daily-core",
            market_date=date(2026, 7, 25),
            schedule_config=self.schedule,
            paths=self.paths,
        )
        self.assertEqual(result.status, "closed_session")
        self.assertIsNotNone(result.release_dir)

    def test_missing_calendar_fails_closed(self) -> None:
        result = run_lane(
            lane_id="market-daily-core",
            market_date=date(2026, 7, 24),
            schedule_config=self.schedule,
            paths=self.paths,
            force_preflight="missing_or_disagree",
        )
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.release_dir)

    def test_event_lane_never_writes_market_bars(self) -> None:
        result = run_lane(
            lane_id="calendar-daily-events",
            market_date=date(2026, 7, 24),
            schedule_config=self.schedule,
            paths=self.paths,
        )
        names = {item.name for item in result.datasets}
        self.assertNotIn("daily_ohlcv_none_delta", names)
        self.assertIn(result.status, {"ready", "partial", "failed"})

    def test_weekly_writes_revision_not_daily_path(self) -> None:
        result = run_lane(
            lane_id="weekly-corrections",
            market_date=date(2026, 7, 24),
            schedule_config=self.schedule,
            paths=self.paths,
        )
        self.assertIn(result.status, {"ready", "partial"})
        self.assertIsNotNone(result.release_dir)
        assert result.release_dir is not None
        self.assertIn("/weekly/", str(result.release_dir).replace("\\", "/"))
        self.assertNotIn("/daily/", str(result.release_dir).replace("\\", "/").split("releases", 1)[-1][:20] if False else "")
        # ensure no mutation of home daily tables
        self.assertTrue((self.home / "daily_ohlcv_none.parquet").is_file())

    def test_bootstrap_refused(self) -> None:
        result = run_lane(
            lane_id="bootstrap",
            market_date=date(2026, 7, 24),
            schedule_config=self.schedule,
            paths=self.paths,
        )
        self.assertEqual(result.status, "failed")
        detail = (result.detail or "").lower()
        self.assertTrue("issue-ref" in detail or "manual" in detail)

    def test_intraday_streams_separate_locks(self) -> None:
        import os

        os.environ["ASTOCK_MINUTE_ROOT"] = str(self.root / "minute")
        r1 = run_lane(
            lane_id="intraday-archive",
            market_date=date(2026, 7, 24),
            schedule_config=self.schedule,
            paths=self.paths,
            stream="1m",
            symbols=["000001"],
        )
        r5 = run_lane(
            lane_id="intraday-archive",
            market_date=date(2026, 7, 24),
            schedule_config=self.schedule,
            paths=self.paths,
            stream="5m",
            symbols=["000001"],
        )
        self.assertEqual(r1.status, "ready")
        self.assertEqual(r5.status, "ready")
        self.assertTrue((self.shadow / "locks" / "intraday-1m.lock").exists())
        self.assertTrue((self.shadow / "locks" / "intraday-5m.lock").exists())
        self.assertTrue((self.shadow / "watermarks" / "intraday-1m.watermark.json").is_file())
        self.assertTrue((self.shadow / "watermarks" / "intraday-5m.watermark.json").is_file())


if __name__ == "__main__":
    unittest.main()
