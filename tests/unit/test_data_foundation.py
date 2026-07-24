import hashlib
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from quanthus.contracts import SnapshotRef
from quanthus.data_foundation import DatasetManifest, PublishedSnapshot, SnapshotCatalog


def checksum(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def dataset_manifest(*, knowledge_cutoff: datetime) -> DatasetManifest:
    return DatasetManifest(
        dataset="bars_daily",
        version="normalized-v1",
        source_revisions={"hermes-astock": "2026-07-23T00:00:00Z"},
        row_count=2,
        partitions=("exchange=SSE/year=2025",),
        schema_hash=checksum("bars schema"),
        content_checksums={"exchange=SSE/year=2025/part-000.parquet": checksum("part")},
        created_at=datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc),
        knowledge_cutoff=knowledge_cutoff,
    )


class DataFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cutoff = datetime(2025, 1, 2, 8, 0, tzinfo=timezone.utc)
        self.reference = SnapshotRef(
            market_date=date(2025, 1, 2),
            knowledge_cutoff=self.cutoff,
            source_revision="hermes-20260723",
            snapshot_id="p1-2025-01-02-v1",
        )

    def test_manifest_identity_is_stable_across_mapping_order(self) -> None:
        first = dataset_manifest(knowledge_cutoff=self.cutoff)
        second = DatasetManifest(
            dataset=first.dataset,
            version=first.version,
            source_revisions={"hermes-astock": "2026-07-23T00:00:00Z"},
            row_count=first.row_count,
            partitions=first.partitions,
            schema_hash=first.schema_hash,
            content_checksums=dict(first.content_checksums),
            created_at=first.created_at,
            knowledge_cutoff=first.knowledge_cutoff,
        )

        self.assertEqual(first.manifest_id, second.manifest_id)

    def test_manifest_rejects_invalid_content_checksum(self) -> None:
        with self.assertRaisesRegex(ValueError, "content_checksums"):
            DatasetManifest(
                dataset="bars_daily",
                version="normalized-v1",
                source_revisions={},
                row_count=0,
                partitions=("empty",),
                schema_hash="schema-v1",
                content_checksums={"part-000": "not-a-checksum"},
                created_at=self.cutoff,
                knowledge_cutoff=self.cutoff,
            )

    def test_snapshot_publish_is_atomic_idempotent_and_loadable(self) -> None:
        manifests = (dataset_manifest(knowledge_cutoff=self.cutoff),)
        published = PublishedSnapshot(self.reference, manifests)
        with tempfile.TemporaryDirectory() as directory:
            catalog = SnapshotCatalog(Path(directory) / "snapshots")
            location = catalog.publish(published)
            loaded = catalog.load(self.reference.snapshot_id)

            self.assertEqual(location, catalog.publish(published))
            self.assertEqual(loaded.to_dict(), published.to_dict())
            self.assertTrue((location / "snapshot.json").is_file())

    def test_snapshot_refuses_a_manifest_known_after_the_cutoff(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceed snapshot knowledge_cutoff"):
            future_cutoff = datetime(2025, 1, 2, 8, 1, tzinfo=timezone.utc)
            PublishedSnapshot(
                self.reference,
                (dataset_manifest(knowledge_cutoff=future_cutoff),),
            )

    def test_snapshot_id_cannot_be_reused_for_different_contents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = SnapshotCatalog(Path(directory) / "snapshots")
            original = PublishedSnapshot(
                self.reference,
                (dataset_manifest(knowledge_cutoff=self.cutoff),),
            )
            catalog.publish(original)
            altered = DatasetManifest(
                dataset="bars_daily",
                version="normalized-v2",
                source_revisions={"hermes-astock": "2026-07-23T00:00:00Z"},
                row_count=2,
                partitions=("exchange=SSE/year=2025",),
                schema_hash=checksum("bars schema"),
                content_checksums={"exchange=SSE/year=2025/part-000.parquet": checksum("part")},
                created_at=datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc),
                knowledge_cutoff=self.cutoff,
            )

            with self.assertRaises(FileExistsError):
                catalog.publish(PublishedSnapshot(self.reference, (altered,)))


if __name__ == "__main__":
    unittest.main()
