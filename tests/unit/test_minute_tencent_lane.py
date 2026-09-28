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
            "HERMES_ODS_MINUTE_TIMEOUT_SEC": "3600",
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
            "quanthus.pipelines.lane_runner._sealed_minute_reference_artifacts",
            side_effect=LaneRunError("minute references require same-date READY market release"),
        ), self.assertRaisesRegex(LaneRunError, "same-date READY market"):
            self.invoke()
        self.assertFalse(self.raw_home.exists())

    def test_passes_both_sealed_artifacts_to_versioned_collector(self):
        daily = self.paths.shadow_root / "daily.parquet"
        status = self.paths.shadow_root / "status.parquet"
        observed = []

        def artifacts(**_kwargs):
            return {
                "sealed_daily": (daily, {"artifact_sha256": "a" * 64}),
                "sealed_status": (status, {"artifact_sha256": "b" * 64}),
            }

        def run(command, **_kwargs):
            observed.extend(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.dict(os.environ, self.env), patch(
            "quanthus.pipelines.lane_runner._sealed_minute_reference_artifacts",
            side_effect=artifacts,
        ), patch("subprocess.run", side_effect=run):
            result = self.invoke()
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["timeout_seconds"], 3600)
        self.assertEqual(observed[observed.index("--tencent-daily-path") + 1], str(daily))
        self.assertEqual(observed[observed.index("--tencent-status-path") + 1], str(status))

    def test_paid_audit_replay_is_explicit_and_never_passes_a_key(self):
        audit = self.paths.shadow_root / "audited-raw"
        audit.mkdir(parents=True)
        observed = []

        five = self.paths.shadow_root / "native-5m.parquet"
        def artifacts(**kwargs):
            self.assertTrue(kwargs["require_five"])
            return {
                "sealed_daily": (
                    self.paths.shadow_root / "daily.parquet", {"artifact_sha256": "a" * 64}
                ),
                "sealed_status": (
                    self.paths.shadow_root / "status.parquet", {"artifact_sha256": "b" * 64}
                ),
                "sealed_native_5m": (five, {"artifact_sha256": "c" * 64}),
            }

        def run(command, **_kwargs):
            observed.extend(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.dict(
            os.environ,
            {**self.env, "HERMES_ODS_MINUTE_PAID_AUDIT_DIR": str(audit)},
        ), patch(
            "quanthus.pipelines.lane_runner._sealed_minute_reference_artifacts",
            side_effect=artifacts,
        ), patch("subprocess.run", side_effect=run):
            self.invoke()
        self.assertEqual(observed[observed.index("--paid-audit-dir") + 1], str(audit.resolve()))
        self.assertEqual(observed[observed.index("--sealed-five-path") + 1], str(five))
        self.assertIn("--reference-sha256-json", observed)
        self.assertFalse(any("key" in argument.lower() for argument in observed))


if __name__ == "__main__":
    unittest.main()
