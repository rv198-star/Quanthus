import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

P1_STACK_AVAILABLE = all(
    __import__("importlib").util.find_spec(module) is not None for module in ("pyarrow",)
)

if P1_STACK_AVAILABLE:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.data_foundation import SnapshotCatalog
    from quanthus.snapshot_publish import load_snapshot_plan, publish_snapshot


@unittest.skipUnless(P1_STACK_AVAILABLE, "P1 optional dependencies are not installed")
class SnapshotPublicationTests(unittest.TestCase):
    def test_publication_hard_links_and_verifies_a_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.parquet"
            pq.write_table(pa.Table.from_pylist([{"stock_code": "600000", "close": 10.0}]), source)
            plan_path = root / "plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "snapshot": {
                            "market_date": "2025-01-02",
                            "knowledge_cutoff": "2025-01-02T08:00:00+00:00",
                            "source_revision": "hermes-v1",
                            "snapshot_id": "p1-2025-01-02-v1",
                        },
                        "universe_policy_version": "p1-universe-v1",
                        "datasets": [
                            {
                                "dataset": "bars_daily",
                                "version": "curated-none-v1",
                                "path": "source.parquet",
                                "source_revisions": {"hermes": "daily-none-v1"},
                                "knowledge_cutoff": "2025-01-02T08:00:00+00:00",
                                "created_at": "2025-01-03T08:00:00+00:00",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            catalog = SnapshotCatalog(root / "snapshots")

            first = publish_snapshot(catalog, load_snapshot_plan(plan_path))
            second = publish_snapshot(catalog, load_snapshot_plan(plan_path))
            partition = first.location / "datasets/bars_daily/source.parquet"

            self.assertEqual(first.location, second.location)
            self.assertEqual(
                hashlib.sha256(partition.read_bytes()).hexdigest(),
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )
            self.assertEqual(os.stat(partition).st_ino, os.stat(source).st_ino)
            self.assertEqual(first.snapshot.universe_policy_version, "p1-universe-v1")

    def test_plan_rejects_a_dataset_newer_than_snapshot_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.parquet"
            pq.write_table(pa.Table.from_pylist([{"stock_code": "600000"}]), source)
            plan_path = root / "plan.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "snapshot": {
                            "market_date": "2025-01-02",
                            "knowledge_cutoff": "2025-01-02T08:00:00+00:00",
                            "source_revision": "hermes-v1",
                            "snapshot_id": "p1-2025-01-02-v1",
                        },
                        "universe_policy_version": "p1-universe-v1",
                        "datasets": [
                            {
                                "dataset": "bars_daily",
                                "version": "curated-none-v1",
                                "path": "source.parquet",
                                "source_revisions": {"hermes": "daily-none-v1"},
                                "knowledge_cutoff": "2025-01-02T08:01:00+00:00",
                                "created_at": "2025-01-03T08:00:00+00:00",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "exceed snapshot knowledge_cutoff"):
                publish_snapshot(SnapshotCatalog(root / "snapshots"), load_snapshot_plan(plan_path))


if __name__ == "__main__":
    unittest.main()
