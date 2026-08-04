from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from quanthus.pipelines.securities_warehouse.dwd_schedule import (
    DwdScheduleError,
    builder_contract,
    prepare_dwd_schedule_run,
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class DwdScheduleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.release_root = self.root / "releases"
        self.ods_base = self.root / "ods-base"
        self.dwd_base = self.root / "dwd-base"
        self.config = self.root / "release.json"
        self._base(self.ods_base, "ods-base-202607-v2", "ods_base_rollup")
        self._base(self.dwd_base, "dwd-base-202607-v2", "dwd_domain_base_package")
        config = {
            "layers": {
                "ods": self._binding(self.ods_base),
                "dwd": self._binding(self.dwd_base),
            }
        }
        self.config.write_text(json.dumps(config), encoding="ascii")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _binding(self, root: Path) -> dict[str, str]:
        manifest = json.loads((root / "manifest.json").read_text(encoding="ascii"))
        return {
            "package_id": manifest["package_id"],
            "manifest_sha256": _sha(root / "manifest.json"),
            "content_digest": manifest["content_digest"],
        }

    def _base(self, root: Path, package_id: str, kind: str) -> None:
        root.mkdir(parents=True)
        manifest = {
            "schema_version": 1,
            "kind": kind,
            "package_id": package_id,
            "content_digest": "sha256:" + "1" * 64,
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="ascii")
        (root / "READY").write_text(_sha(manifest_path) + "\n", encoding="ascii")

    def test_ods_bundle_id_is_accepted_as_package_identity(self) -> None:
        manifest_path = self.ods_base / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="ascii"))
        manifest["bundle_id"] = manifest.pop("package_id")
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="ascii")
        (self.ods_base / "READY").write_text(_sha(manifest_path) + "\n", encoding="ascii")
        config = json.loads(self.config.read_text(encoding="ascii"))
        config["layers"]["ods"]["manifest_sha256"] = _sha(manifest_path)
        self.config.write_text(json.dumps(config, sort_keys=True), encoding="ascii")
        self._weekly_release()

        result = prepare_dwd_schedule_run(
            mode="weekly",
            market_date=date(2026, 8, 1),
            release_root=self.release_root,
            ods_base=self.ods_base,
            dwd_base=self.dwd_base,
            release_config=self.config,
            output=self.root / "runs" / "bundle-id",
            run_id="bundle-id",
            code_revision="a" * 40,
        )
        self.assertEqual(result.status, "prepared")

    def _weekly_release(self) -> None:
        release = self.release_root / "weekly" / "2026-08-01" / "r1"
        artifacts = release / "artifacts"
        artifacts.mkdir(parents=True)
        payload = artifacts / "raw_f10.parquet"
        payload.write_bytes(b"weekly")
        manifest = {
            "schema_version": 1,
            "release_id": "hermes-weekly-corrections-20260801-v2",
            "schedule": "weekly",
            "market_date": "2026-08-01",
            "created_at": "2026-08-01T09:00:00+00:00",
            "producer": "test",
            "status": "ready",
            "schedule_contract_id": "schedule-v1",
            "schedule_contract_revision": "revision-v1",
            "schedule_config_sha256": "sha256:" + "1" * 64,
            "artifacts": [
                {
                    "name": "raw_f10",
                    "relative_path": "artifacts/raw_f10.parquet",
                    "sha256": _sha(payload),
                    "size_bytes": payload.stat().st_size,
                    "source_revision": "source-v1",
                    "knowledge_cutoff": "2026-08-01T09:00:00+00:00",
                }
            ],
        }
        manifest_path = release / "release.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="ascii")
        (release / "READY").write_text(_sha(manifest_path) + "\n", encoding="ascii")

    def test_contract_has_eight_logical_builders(self) -> None:
        self.assertEqual([item["issue"] for item in builder_contract()], list(range(57, 65)))
        self.assertEqual(builder_contract()[2]["internal_steps"], ["trading_calendar_backbone"])

    def test_weekly_run_is_sealed_and_idempotent(self) -> None:
        self._weekly_release()
        output = self.root / "runs" / "weekly" / "2026-08-01" / "run-1"
        result = prepare_dwd_schedule_run(
            mode="weekly",
            market_date=date(2026, 8, 1),
            release_root=self.release_root,
            ods_base=self.ods_base,
            dwd_base=self.dwd_base,
            release_config=self.config,
            output=output,
            run_id="run-1",
            code_revision="a" * 40,
        )
        self.assertEqual(result.status, "prepared")
        self.assertEqual(result.builder_count, 8)
        self.assertEqual(result, prepare_dwd_schedule_run(
            mode="weekly",
            market_date=date(2026, 8, 1),
            release_root=self.release_root,
            ods_base=self.ods_base,
            dwd_base=self.dwd_base,
            release_config=self.config,
            output=output,
            run_id="run-1",
            code_revision="a" * 40,
        ))

    def test_output_inside_base_is_rejected(self) -> None:
        with self.assertRaisesRegex(DwdScheduleError, "outside the ODS Base"):
            prepare_dwd_schedule_run(
                mode="weekly",
                market_date=date(2026, 8, 1),
                release_root=self.release_root,
                ods_base=self.ods_base,
                dwd_base=self.dwd_base,
                release_config=self.config,
                output=self.ods_base / "runs",
                run_id="run-1",
                code_revision="a" * 40,
            )


if __name__ == "__main__":
    unittest.main()
