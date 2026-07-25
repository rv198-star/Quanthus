from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

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


class PipelineRunnerTests(unittest.TestCase):
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
            self.assertEqual([stage["status"] for stage in ledger["stages"]], [
                "succeeded",
                "succeeded",
            ])
            self.assertTrue((root / "runs" / "test-t5h-v1" / "LAST_SUCCESS.json").is_file())

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
