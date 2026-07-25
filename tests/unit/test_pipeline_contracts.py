from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quanthus.pipelines.contracts import (
    OdsReleaseError,
    publish_ods_release,
    verify_ods_release,
)


class OdsReleaseContractTests(unittest.TestCase):
    def _write_spec(self, root: Path, source: Path, *, market_date: str = "2026-07-24") -> Path:
        spec = root / "spec.json"
        spec.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_id": f"hermes-daily-{market_date}-v1",
                    "schedule": "daily",
                    "market_date": market_date,
                    "producer": "hermes-test",
                    "artifacts": [
                        {
                            "name": "daily_ohlcv_none",
                            "source_path": str(source),
                            "relative_path": "artifacts/daily_ohlcv_none.parquet",
                            "source_revision": "test-source-v1",
                            "knowledge_cutoff": datetime(
                                2026, 7, 24, 8, tzinfo=timezone.utc
                            ).isoformat(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return spec

    def test_publish_writes_ready_last_and_verifies_every_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.parquet"
            source.write_bytes(b"immutable-test-payload")
            output = root / "release"

            published = publish_ods_release(
                spec_path=self._write_spec(root, source),
                output=output,
            )
            verified = verify_ods_release(output)

            self.assertEqual(published.manifest_sha256, verified.manifest_sha256)
            self.assertEqual(verified.release.release_id, "hermes-daily-2026-07-24-v1")
            self.assertEqual(verified.verified_bytes, len(b"immutable-test-payload"))
            self.assertEqual((output / "READY").read_text().strip(), verified.manifest_sha256)

    def test_changed_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.parquet"
            source.write_bytes(b"original")
            output = root / "release"
            publish_ods_release(spec_path=self._write_spec(root, source), output=output)

            (output / "artifacts" / "daily_ohlcv_none.parquet").write_bytes(b"changed")

            with self.assertRaisesRegex(OdsReleaseError, "artifact size mismatch"):
                verify_ods_release(output)

    def test_ready_marker_must_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.parquet"
            source.write_bytes(b"payload")
            output = root / "release"
            publish_ods_release(spec_path=self._write_spec(root, source), output=output)
            (output / "READY").write_text(f"sha256:{'0' * 64}\n", encoding="ascii")

            with self.assertRaisesRegex(OdsReleaseError, "READY marker does not match"):
                verify_ods_release(output)

    def test_path_traversal_is_rejected_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.parquet"
            source.write_bytes(b"payload")
            spec_path = self._write_spec(root, source)
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            spec["artifacts"][0]["relative_path"] = "../outside.parquet"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            with self.assertRaisesRegex(OdsReleaseError, "unsafe relative_path"):
                publish_ods_release(spec_path=spec_path, output=root / "release")

    def test_existing_release_rejects_a_reused_id_with_changed_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.parquet"
            source.write_bytes(b"payload")
            spec_path = self._write_spec(root, source)
            output = root / "release"
            publish_ods_release(spec_path=spec_path, output=output)
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            spec["artifacts"][0]["source_revision"] = "different-source-v2"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            with self.assertRaisesRegex(OdsReleaseError, "does not match the requested spec"):
                publish_ods_release(spec_path=spec_path, output=output)


if __name__ == "__main__":
    unittest.main()
