"""Opt-in wiring must bind 1m acquisition to a same-date READY market release."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from quanthus.pipelines.lane_runner import LanePaths, LaneRunError, _run_minute_collector


class TencentMinuteLaneTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.paths = LanePaths(root / "home", root / "shadow", root / "calendar", root / "locks")
        self.raw_home = root / "raw"
        self.date = date(2026, 9, 22)
        self.env = {
            "HERMES_ODS_MINUTE_TENCENT_FALLBACK": "1",
            "ASTOCK_MINUTE_PYTHON": sys.executable,
            "HERMES_ODS_MINUTE_MODE": "incr",
        }

    def invoke(self):
        return _run_minute_collector(
            paths=self.paths,
            market_date=self.date,
            bar_size="1m",
            symbols=["000001"],
            raw_home=self.raw_home,
        )

    def test_requires_ready_daily_and_status(self):
        with patch.dict(os.environ, self.env), patch(
            "quanthus.pipelines.lane_runner._latest_market_artifact",
            return_value=None,
        ):
            with self.assertRaisesRegex(LaneRunError, "same-date READY market"):
                self.invoke()
        self.assertFalse(self.raw_home.exists())

    def test_passes_both_sealed_artifacts_to_versioned_collector(self):
        daily = self.paths.shadow_root / "daily.parquet"
        status = self.paths.shadow_root / "status.parquet"
        observed = []

        def artifact(*, artifact_name, **_kwargs):
            return (daily if artifact_name == "daily_ohlcv_none_delta" else status, {})

        def run(command, **_kwargs):
            observed.extend(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.dict(os.environ, self.env), patch(
            "quanthus.pipelines.lane_runner._latest_market_artifact",
            side_effect=artifact,
        ), patch("subprocess.run", side_effect=run):
            result = self.invoke()
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(observed[observed.index("--tencent-daily-path") + 1], str(daily))
        self.assertEqual(observed[observed.index("--tencent-status-path") + 1], str(status))


if __name__ == "__main__":
    unittest.main()
