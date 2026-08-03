from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from quanthus.pipelines.securities_warehouse.daily_ods_catalog import (
    DailyOdsCatalogError,
    build_daily_ods_catalog,
    verify_daily_ods_catalog,
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class DailyOdsCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.release_root = self.root / "releases"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _release(self, lane_root: str, prefix: str, created: str) -> Path:
        release = self.release_root / lane_root / "2026-08-03" / created.replace(":", "")
        artifacts = release / "artifacts"
        artifacts.mkdir(parents=True)
        payload = artifacts / "payload.parquet"
        payload.write_bytes((prefix + created).encode("ascii"))
        manifest = {
            "schema_version": 1,
            "release_id": prefix + "20260803-v2",
            "schedule": "daily",
            "market_date": "2026-08-03",
            "created_at": created,
            "producer": "test",
            "status": "ready",
            "schedule_contract_id": "schedule-v1",
            "schedule_contract_revision": "revision-v1",
            "schedule_config_sha256": "sha256:" + "1" * 64,
            "artifacts": [
                {
                    "name": "payload",
                    "relative_path": "artifacts/payload.parquet",
                    "sha256": _sha(payload),
                    "size_bytes": payload.stat().st_size,
                    "source_revision": "source-v1",
                    "knowledge_cutoff": created,
                }
            ],
        }
        manifest_path = release / "release.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="ascii")
        (release / "READY").write_text(_sha(manifest_path) + "\n", encoding="ascii")
        return release

    def _complete(self) -> None:
        self._release("daily", "hermes-market-daily-core-", "2026-08-03T11:00:00+00:00")
        self._release(
            "daily", "hermes-calendar-daily-events-", "2026-08-03T12:00:00+00:00"
        )
        self._release(
            "daily/1m", "hermes-intraday-archive-1m-", "2026-08-03T13:00:00+00:00"
        )
        self._release(
            "daily/5m", "hermes-intraday-archive-5m-", "2026-08-03T15:00:00+00:00"
        )

    def test_seals_exact_four_lane_catalog(self) -> None:
        self._complete()
        output = self.root / "catalog"
        result = build_daily_ods_catalog(
            release_root=self.release_root,
            market_date=date(2026, 8, 3),
            output=output,
        )
        self.assertEqual(result.lane_count, 4)
        self.assertEqual(result.artifact_count, 4)
        self.assertEqual(result, verify_daily_ods_catalog(output))

    def test_fails_closed_when_one_lane_is_absent(self) -> None:
        self._complete()
        missing = next((self.release_root / "daily/5m/2026-08-03").iterdir())
        (missing / "READY").unlink()
        with self.assertRaisesRegex(DailyOdsCatalogError, "intraday_5m"):
            build_daily_ods_catalog(
                release_root=self.release_root,
                market_date=date(2026, 8, 3),
                output=self.root / "catalog",
            )


if __name__ == "__main__":
    unittest.main()
