from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib import request as urlrequest

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ModuleNotFoundError:  # pragma: no cover - exercised by the P1 dependency job
    pa = None
    pq = None

from quanthus.pipelines.contracts import publish_ods_release
from quanthus.pipelines.runner import (
    PipelineConfigError,
    PipelineRunError,
    load_pipeline_config,
    run_pipeline,
)

ROOT = Path(__file__).resolve().parents[2]


class PipelineRunnerTests(unittest.TestCase):
    def test_repository_pipeline_uses_ready_gated_stage_slas(self) -> None:
        config = load_pipeline_config(ROOT / "config" / "pipelines" / "t-close-plus-5h.v1.json")

        self.assertEqual(config.close_time.isoformat(), "15:30:00")
        self.assertEqual(config.completion_sla_minutes, 300)
        self.assertEqual(config.stage_sla_margin_percent, 100)
        assert config.email_notification is not None
        self.assertEqual(config.email_notification.sender, "report@mindthus.com")
        self.assertEqual(config.email_notification.recipients, ("61718@qq.com",))
        self.assertEqual(config.email_notification.api_key_environment, "QUANTHUS_RESEND_API_KEY")
        wait_stage = config.stages[0]
        self.assertEqual(wait_stage.stage_id, "wait_daily_ods_source_ready")
        self.assertEqual(wait_stage.observed_max_duration_seconds, 7380)
        self.assertEqual(wait_stage.sla_seconds, 14760)
        self.assertEqual(
            [stage.stage_id for stage in config.stages][-5:],
            [
                "build_daily_dws_summary",
                "gate_daily_dwd_partition",
                "gate_daily_dws_summary",
                "publish_daily_dwd_current",
                "publish_daily_dws_current",
            ],
        )

    def _with_email_notification(self, config_path: Path, *, retries: int = 0) -> None:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload["notifications"] = {
            "email": {
                "provider": "resend",
                "api_url": "https://api.resend.com/emails",
                "api_key_environment": "QUANTHUS_TEST_RESEND_API_KEY",
                "from": "report@mindthus.com",
                "to": ["61718@qq.com"],
                "timeout_seconds": 1,
                "retries": retries,
            }
        }
        config_path.write_text(json.dumps(payload), encoding="utf-8")

    def _release(self, root: Path, market_date: date) -> Path:
        source = root / "source.parquet"
        source.write_bytes(b"daily")
        spec = root / "spec.json"
        spec.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_id": f"daily-{market_date.isoformat()}-v1",
                    "schedule": "daily",
                    "market_date": market_date.isoformat(),
                    "producer": "test",
                    "artifacts": [
                        {
                            "name": "daily",
                            "source_path": str(source),
                            "relative_path": "daily.parquet",
                            "source_revision": "source-v1",
                            "knowledge_cutoff": datetime.now(timezone.utc).isoformat(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        release = root / "release"
        publish_ods_release(spec_path=spec, output=release)
        return release

    def _config(
        self,
        root: Path,
        release: Path,
        *,
        command: str = "/bin/true",
    ) -> Path:
        path = root / "pipeline.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "pipeline_id": "test-t5h-v1",
                    "timezone": "Asia/Shanghai",
                    "close_time": "15:00:00",
                    "completion_sla_minutes": 300,
                    "run_root": str(root / "runs"),
                    "lock_path": str(root / "pipeline.lock"),
                    "variables": {
                        "release": {
                            "environment": "QUANTHUS_TEST_UNUSED_RELEASE",
                            "default": str(release),
                        }
                    },
                    "stages": [
                        {
                            "id": "verify_ods",
                            "phase": "verify",
                            "kind": "verify_ods_release",
                            "release_dir": "{release}",
                        },
                        {
                            "id": "quality_gate",
                            "phase": "gate",
                            "kind": "command",
                            "argv": [command],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_success_writes_durable_ledger_and_last_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            config = load_pipeline_config(self._config(root, self._release(root, market_date)))

            result = run_pipeline(config, market_date=market_date)

            ledger = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertTrue(result.status.startswith("succeeded"))
            self.assertEqual(
                [stage["status"] for stage in ledger["stages"]],
                [
                    "succeeded",
                    "succeeded",
                ],
            )
            self.assertTrue((root / "runs" / "test-t5h-v1" / "LAST_SUCCESS.json").is_file())

    def test_stage_sla_is_durable_and_uses_the_configured_margin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            config_path = self._config(root, self._release(root, market_date))
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["stage_sla_margin_percent"] = 100
            payload["stages"][0]["observed_max_duration_seconds"] = 2
            config_path.write_text(json.dumps(payload), encoding="utf-8")

            result = run_pipeline(load_pipeline_config(config_path), market_date=market_date)

            ledger = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
            stage = ledger["stages"][0]
            self.assertEqual(stage["observed_max_duration_seconds"], 2.0)
            self.assertEqual(stage["sla_seconds"], 4)
            self.assertIn("elapsed_seconds", stage)
            self.assertTrue(stage["sla_met"])
            self.assertTrue(ledger["stage_sla_met"])

    def test_transfer_sla_comes_from_the_selected_source_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            selection = root / "selection.json"
            selection.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_id": "cn01",
                        "schedule": "daily",
                        "market_date": market_date.isoformat(),
                        "selection": "attempt",
                        "selected_at": "2026-07-24T08:00:00+00:00",
                        "attempt_id": "20260724-20260724T080000000000Z",
                        "transfer": {
                            "elapsed_seconds": 3.649,
                            "sla_qualified": True,
                            "observed_max_duration_seconds": 3.649,
                            "sla_seconds": 8,
                            "sla_met": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            config_path = self._config(root, self._release(root, market_date))
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["stages"][0]["dynamic_sla_receipt_path"] = str(selection)
            config_path.write_text(json.dumps(payload), encoding="utf-8")

            result = run_pipeline(load_pipeline_config(config_path), market_date=market_date)

            ledger = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
            stage = ledger["stages"][0]
            self.assertEqual(stage["handoff_source_id"], "cn01")
            self.assertEqual(stage["handoff_attempt_id"], "20260724-20260724T080000000000Z")
            self.assertEqual(stage["source_transfer_elapsed_seconds"], 3.649)
            self.assertEqual(stage["sla_seconds"], 8)
            self.assertTrue(stage["sla_qualified"])

    def test_failed_gate_never_writes_last_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            config = load_pipeline_config(
                self._config(root, self._release(root, market_date), command="/bin/false")
            )

            with self.assertRaisesRegex(PipelineRunError, "quality_gate failed"):
                run_pipeline(config, market_date=market_date)

            run_dirs = list((root / "runs" / "test-t5h-v1").glob("2026*"))
            self.assertEqual(len(run_dirs), 1)
            ledger = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["status"], "failed")
            self.assertFalse((root / "runs" / "test-t5h-v1" / "LAST_SUCCESS.json").exists())

    def test_successful_pipeline_sends_completed_email_after_writing_ledger(self) -> None:
        class Response:
            def getcode(self) -> int:
                return 201

            def close(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            config_path = self._config(root, self._release(root, market_date))
            self._with_email_notification(config_path)
            requests: list[urlrequest.Request] = []

            def send(request: urlrequest.Request, *, timeout: float) -> Response:
                requests.append(request)
                self.assertEqual(timeout, 1)
                return Response()

            with (
                patch.dict(
                    os.environ,
                    {"QUANTHUS_TEST_RESEND_API_KEY": "test-notification-key"},
                    clear=False,
                ),
                patch("quanthus.pipelines.runner.urlrequest.urlopen", side_effect=send),
            ):
                result = run_pipeline(load_pipeline_config(config_path), market_date=market_date)

            ledger_text = (result.run_dir / "run.json").read_text(encoding="utf-8")
            ledger = json.loads(ledger_text)
            self.assertEqual(ledger["notifications"]["email"]["event"], "completed")
            self.assertEqual(ledger["notifications"]["email"]["status"], "sent")
            self.assertEqual(len(requests), 1)
            request_body = json.loads(requests[0].data.decode("utf-8"))
            self.assertEqual(request_body["from"], "report@mindthus.com")
            self.assertEqual(request_body["to"], ["61718@qq.com"])
            self.assertEqual(
                requests[0].get_header("User-agent"),
                "Quanthus-Pipeline-Notifier/1.0",
            )
            self.assertIn("清洗完成", request_body["subject"])
            self.assertIn("DWD/DWS", request_body["html"])
            self.assertNotIn("test-notification-key", ledger_text)

    def test_failed_pipeline_sends_failed_email(self) -> None:
        class Response:
            def getcode(self) -> int:
                return 200

            def close(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            config_path = self._config(
                root,
                self._release(root, market_date),
                command="/bin/false",
            )
            self._with_email_notification(config_path)
            requests: list[urlrequest.Request] = []

            def send(request: urlrequest.Request, *, timeout: float) -> Response:
                requests.append(request)
                self.assertEqual(timeout, 1)
                return Response()

            with (
                patch.dict(
                    os.environ,
                    {"QUANTHUS_TEST_RESEND_API_KEY": "test-notification-key"},
                    clear=False,
                ),
                patch("quanthus.pipelines.runner.urlrequest.urlopen", side_effect=send),
                self.assertRaisesRegex(PipelineRunError, "quality_gate failed"),
            ):
                run_pipeline(load_pipeline_config(config_path), market_date=market_date)

            run_dir = next((root / "runs" / "test-t5h-v1").glob("2026*"))
            ledger = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["status"], "failed")
            self.assertEqual(ledger["notifications"]["email"]["event"], "failed")
            self.assertEqual(ledger["notifications"]["email"]["status"], "sent")
            request_body = json.loads(requests[0].data.decode("utf-8"))
            self.assertIn("清洗失败", request_body["subject"])
            self.assertIn("失败详情", request_body["html"])

    def test_notification_failure_does_not_change_successful_pipeline_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            config_path = self._config(root, self._release(root, market_date))
            self._with_email_notification(config_path, retries=2)

            with (
                patch.dict(
                    os.environ,
                    {"QUANTHUS_TEST_RESEND_API_KEY": "test-notification-key"},
                    clear=False,
                ),
                patch(
                    "quanthus.pipelines.runner.urlrequest.urlopen",
                    side_effect=OSError("network unavailable"),
                ) as send,
                patch("quanthus.pipelines.runner.time.sleep"),
            ):
                result = run_pipeline(load_pipeline_config(config_path), market_date=market_date)

            ledger = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertTrue(result.status.startswith("succeeded"))
            self.assertEqual(ledger["status"], result.status)
            self.assertEqual(ledger["notifications"]["email"]["status"], "failed")
            self.assertEqual(ledger["notifications"]["email"]["attempts"], 3)
            self.assertIn("network unavailable", ledger["notifications"]["email"]["error"])
            self.assertEqual(send.call_count, 3)

    def test_release_for_wrong_market_date_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = self._release(root, date(2026, 7, 23))
            config = load_pipeline_config(self._config(root, release))

            with self.assertRaisesRegex(PipelineRunError, "market_date does not match"):
                run_pipeline(config, market_date=date(2026, 7, 24))

    def test_publish_phase_requires_a_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "pipeline_id": "bad",
                        "timezone": "UTC",
                        "close_time": "15:00",
                        "completion_sla_minutes": 300,
                        "run_root": "/tmp/runs",
                        "lock_path": "/tmp/lock",
                        "stages": [
                            {
                                "id": "publish",
                                "phase": "publish",
                                "kind": "command",
                                "argv": ["/bin/true"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PipelineConfigError, "requires at least one gate"):
                load_pipeline_config(path)

    @unittest.skipIf(pa is None or pq is None, "P1 dependencies are not installed")
    def test_closed_session_skips_build_and_does_not_advance_last_success(self) -> None:
        assert pa is not None
        assert pq is not None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 25)
            release = self._release(root, market_date)
            config_path = self._config(root, release)
            calendar = root / "calendar.parquet"
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {"exchange": "SSE", "trade_date": market_date, "is_open": 0},
                        {"exchange": "SZSE", "trade_date": market_date, "is_open": 0},
                    ]
                ),
                calendar,
            )
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["stages"].insert(
                1,
                {
                    "id": "market_session",
                    "phase": "verify",
                    "kind": "market_session_check",
                    "calendar_path": str(calendar),
                },
            )
            config_path.write_text(json.dumps(payload), encoding="utf-8")

            result = run_pipeline(load_pipeline_config(config_path), market_date=market_date)

            self.assertEqual(result.status, "skipped_closed_session")
            ledger = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(ledger["stages"][-1]["status"], "skipped")
            self.assertFalse((root / "runs" / "test-t5h-v1" / "LAST_SUCCESS.json").exists())


if __name__ == "__main__":
    unittest.main()
