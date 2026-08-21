from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "ops" / "bin" / "render-ods-node-units"
WINDOW_CHECKER = ROOT / "ops" / "bin" / "check-ods-collection-window"


class OdsUnitRendererTests(unittest.TestCase):
    def _render(self, node_id: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = Path(directory.name)
        subprocess.run(
            [str(RENDERER), "--node-id", node_id, "--output", str(output)],
            check=True,
            text=True,
            capture_output=True,
        )
        return output

    def test_renderer_creates_deterministic_cn01_units(self) -> None:
        output = self._render("cn01")

        units = sorted(output.iterdir())
        self.assertEqual(len(units), 17)
        node_units = [path for path in units if path.name != "quanthus-pipeline-failure@.service"]
        self.assertTrue(
            all(path.name.startswith(("cn01-ods-", "cn01-dwd-")) for path in node_units)
        )
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o644 for path in units))

        orchestrator_timer = (output / "cn01-ods-daily-orchestrator.timer").read_text()
        orchestrator_service = (output / "cn01-ods-daily-orchestrator.service").read_text()
        self.assertIn("OnCalendar=Mon..Fri *-*-* 18:30:00 Asia/Shanghai", orchestrator_timer)
        self.assertIn("READY-gated", orchestrator_timer)
        self.assertIn("Unit=cn01-ods-daily-orchestrator.service", orchestrator_timer)
        self.assertIn("run-cn01-ods-daily", orchestrator_service)
        self.assertIn("EnvironmentFile=-/etc/quanthus/cn01-ods-lane.env", orchestrator_service)
        self.assertIn("Environment=HERMES_ODS_NODE_ID=cn01", orchestrator_service)
        self.assertIn(
            "Environment=HERMES_ODS_ENV_FILE=/etc/quanthus/cn01-ods-lane.env",
            orchestrator_service,
        )
        self.assertIn("TimeoutStartSec=220min", orchestrator_service)
        self.assertIn("StartLimitBurst=3", orchestrator_service)
        self.assertIn("Restart=on-failure", orchestrator_service)
        self.assertIn("RestartSec=10min", orchestrator_service)
        self.assertIn("OnSuccess=cn01-dwd-success-notification.service", orchestrator_service)

        weekly_service = (output / "cn01-ods-weekly-corrections.service").read_text()
        self.assertIn("TimeoutStartSec=8h", weekly_service)
        self.assertIn("Restart=on-failure", weekly_service)

        prune_service = (output / "cn01-ods-shadow-prune.service").read_text()
        prune_timer = (output / "cn01-ods-shadow-prune.timer").read_text()
        self.assertIn("prune-ods-shadow --node-id cn01", prune_service)
        self.assertIn("--failed-bulk-days 3 --audit-days 90 --apply", prune_service)
        self.assertIn("EnvironmentFile=-/etc/quanthus/cn01-ods-lane.env", prune_service)
        self.assertIn("OnCalendar=*-*-* 04:30:00 Asia/Shanghai", prune_timer)
        self.assertIn("Persistent=true", prune_timer)
        self.assertIn("Unit=cn01-ods-shadow-prune.service", prune_timer)

        success_notification = (output / "cn01-dwd-success-notification.service").read_text()
        failure_notification = (output / "cn01-dwd-failure-notification.service").read_text()
        self.assertIn("--event cn01_dwd_completed", success_notification)
        self.assertIn("--node CN01", success_notification)
        self.assertIn("--event cn01_dwd_failed", failure_notification)
        self.assertIn("--failure-receipt", failure_notification)
        self.assertIn("shadow/cn01-ods-v1", failure_notification)

        market_timer = (output / "cn01-ods-market-daily-core.timer").read_text()
        market_service = (output / "cn01-ods-market-daily-core.service").read_text()
        self.assertIn(
            "ConditionPathExists=/etc/quanthus/cn01-ods-legacy-timers-enabled",
            market_timer,
        )
        self.assertIn(
            "ConditionPathExists=/etc/quanthus/cn01-ods-legacy-timers-enabled",
            market_service,
        )
        self.assertIn("OnCalendar=Mon..Fri *-*-* 18:30:00 Asia/Shanghai", market_timer)
        self.assertIn("contract 18:30", market_timer)
        self.assertIn("Description=CN01 ODS", market_timer)
        self.assertNotIn("Description=Hermes ODS", market_timer)
        self.assertNotIn("contract 17:30", market_timer)
        self.assertIn("Unit=cn01-ods-market-daily-core.service", market_timer)
        self.assertIn("EnvironmentFile=-/etc/quanthus/cn01-ods-lane.env", market_service)
        self.assertIn("Environment=HERMES_ODS_NODE_ID=cn01", market_service)
        self.assertIn("check-ods-collection-window --node-id cn01", market_service)
        self.assertIn("TimeoutStartSec=120min", market_service)
        self.assertIn("ops/bin/run-hermes-ods-lane", market_service)
        self.assertNotIn("ops/bin/run-cn01-ods-lane", market_service)
        self.assertIn("date +%%F", market_service)
        self.assertNotIn("hermes-ods", market_timer)

        failure_handler = output / "quanthus-pipeline-failure@.service"
        self.assertEqual(
            failure_handler.read_bytes(),
            (ROOT / "ops/systemd/quanthus-pipeline-failure@.service").read_bytes(),
        )

    def test_hermes_render_uses_the_same_orchestrator_and_gates_lane_timers(self) -> None:
        output = self._render("hermes")

        rendered = (output / "hermes-ods-market-daily-core.timer").read_text()
        orchestrator = (output / "hermes-ods-daily-orchestrator.timer").read_text()
        success_notification = (output / "hermes-dwd-success-notification.service").read_text()
        self.assertIn(
            "ConditionPathExists=/etc/quanthus/hermes-ods-legacy-timers-enabled",
            rendered,
        )
        self.assertIn("OnCalendar=Mon..Fri *-*-* 19:30:00 Asia/Shanghai", orchestrator)
        orchestrator_service = (output / "hermes-ods-daily-orchestrator.service").read_text()
        self.assertIn("Restart=on-failure", orchestrator_service)
        self.assertIn("--event hermes_dwd_completed", success_notification)
        self.assertIn("--node Hermes", success_notification)
        prune_service = (output / "hermes-ods-shadow-prune.service").read_text()
        self.assertIn("prune-ods-shadow --node-id hermes", prune_service)

    def test_collection_window_checker_enforces_each_node_start(self) -> None:
        config = ROOT / "config/schedules/hermes-ods-schedule.v1.json"

        cases = [
            ("hermes", "2026-07-29T17:30:00+08:00", 0),
            ("hermes", "2026-07-29T18:20:00+08:00", 1),
            ("cn01", "2026-07-29T15:40:00+08:00", 1),
            ("cn01", "2026-07-29T18:34:59+08:00", 0),
            ("cn01", "2026-07-29T18:35:00+08:00", 1),
        ]
        for node_id, observed_at, expected in cases:
            with self.subTest(node_id=node_id, observed_at=observed_at):
                result = subprocess.run(
                    [
                        str(WINDOW_CHECKER),
                        "--node-id",
                        node_id,
                        "--config",
                        str(config),
                        "--at",
                        observed_at,
                    ],
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_late_schedule_moves_only_the_hermes_collection_window(self) -> None:
        config = ROOT / "config/schedules/hermes-ods-schedule.late.v1.json"

        cases = [
            ("hermes", "2026-08-17T19:30:00+08:00", 0),
            ("hermes", "2026-08-17T19:35:00+08:00", 1),
            ("cn01", "2026-08-17T18:30:00+08:00", 0),
            ("cn01", "2026-08-17T18:35:00+08:00", 1),
        ]
        for node_id, observed_at, expected in cases:
            with self.subTest(node_id=node_id, observed_at=observed_at):
                result = subprocess.run(
                    [
                        str(WINDOW_CHECKER),
                        "--node-id",
                        node_id,
                        "--config",
                        str(config),
                        "--at",
                        observed_at,
                    ],
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_collection_window_checker_fails_closed_for_unknown_node(self) -> None:
        result = subprocess.run(
            [
                str(WINDOW_CHECKER),
                "--node-id",
                "unknown",
                "--config",
                str(ROOT / "config/schedules/hermes-ods-schedule.v1.json"),
                "--at",
                "2026-07-29T15:40:00+08:00",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 255)
        self.assertIn("configuration error", result.stderr)


if __name__ == "__main__":
    unittest.main()
