from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from quanthus.pipelines.securities_warehouse.weekly_ods_catalog import (
    WeeklyOdsCatalogError,
    build_weekly_ods_catalog,
    verify_weekly_ods_catalog,
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class WeeklyOdsCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.release_root = self.root / "releases"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _release(self, created: str, *, ready: bool = True) -> Path:
        release = self.release_root / "weekly" / "2026-08-01" / created.replace(":", "")
        artifacts = release / "artifacts"
        artifacts.mkdir(parents=True)
        payload = artifacts / "financial.parquet"
        payload.write_bytes(created.encode("ascii"))
        manifest = {
            "schema_version": 1,
            "release_id": "hermes-weekly-corrections-20260801-v2",
            "schedule": "weekly",
            "market_date": "2026-08-01",
            "created_at": created,
            "producer": "test",
            "status": "ready",
            "schedule_contract_id": "schedule-v1",
            "schedule_contract_revision": "revision-v1",
            "schedule_config_sha256": "sha256:" + "1" * 64,
            "artifacts": [
                {
                    "name": "financial",
                    "relative_path": "artifacts/financial.parquet",
                    "sha256": _sha(payload),
                    "size_bytes": payload.stat().st_size,
                    "source_revision": "source-v1",
                    "knowledge_cutoff": created,
                }
            ],
        }
        manifest_path = release / "release.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="ascii")
        if ready:
            (release / "READY").write_text(_sha(manifest_path) + "\n", encoding="ascii")
        return release

    def test_seals_newest_weekly_release(self) -> None:
        self._release("2026-08-01T08:00:00+00:00")
        self._release("2026-08-01T09:00:00+00:00")
        output = self.root / "catalog"
        result = build_weekly_ods_catalog(
            release_root=self.release_root,
            market_date=date(2026, 8, 1),
            output=output,
        )
        self.assertEqual(result.release_id, "hermes-weekly-corrections-20260801-v2")
        self.assertEqual(result, verify_weekly_ods_catalog(output))

    def test_fails_closed_when_weekly_release_is_absent(self) -> None:
        with self.assertRaisesRegex(WeeklyOdsCatalogError, "absent"):
            build_weekly_ods_catalog(
                release_root=self.release_root,
                market_date=date(2026, 8, 1),
                output=self.root / "catalog",
            )

    def test_rejects_catalog_output_inside_ods_root(self) -> None:
        with self.assertRaisesRegex(WeeklyOdsCatalogError, "outside the ODS"):
            build_weekly_ods_catalog(
                release_root=self.release_root,
                market_date=date(2026, 8, 1),
                output=self.release_root / "catalog",
            )


if __name__ == "__main__":
    unittest.main()
