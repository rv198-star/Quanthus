import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quanthus.backtest import (
    HistoricalReplayBundleError,
    load_historical_replay_bundle,
)
from quanthus.contracts import SnapshotRef
from quanthus.corporate_action_normalization import (
    CorporateActionNormalizationPolicy,
    load_corporate_action_normalization_policy,
)
from quanthus.data_foundation import SnapshotCatalog
from quanthus.snapshot_publish import (
    SnapshotDatasetPlan,
    SnapshotPublicationPlan,
    publish_snapshot,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
REQUIRED_DATASETS = (
    "corporate_action_exceptions",
    "corporate_actions_normalized",
    "daily_ohlcv_none",
    "daily_universe",
    "price_limit_execution_daily",
    "security_code_validity",
    "trading_calendar",
)
P1_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(module) is not None for module in ("duckdb", "pyarrow")
)


@unittest.skipUnless(P1_DEPENDENCIES_AVAILABLE, "P1 dependencies are not installed")
class HistoricalReplayBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.catalog = SnapshotCatalog(self.root / "snapshots")
        self.policy = load_corporate_action_normalization_policy()

    def source_revisions(self, dataset: str, exception_count: str) -> dict[str, str]:
        if dataset not in {"corporate_actions_normalized", "corporate_action_exceptions"}:
            return {"source": f"test-{dataset}-v1"}
        lineage = {
            "normalization_policy_version": self.policy.policy_version,
            "normalization_report_digest": f"sha256:{'1' * 64}",
            "corporate_action_source_digest": f"sha256:{'2' * 64}",
            "authority_source_digest": f"sha256:{'3' * 64}",
            "exception_ledger_digest": f"sha256:{'4' * 64}",
            "exception_ledger_version": str(self.policy.exception_ledger_version),
        }
        if dataset == "corporate_action_exceptions":
            return {**lineage, "replay_disposition": "security_fail_closed"}
        return {
            **lineage,
            "normalization_exception_count": exception_count,
        }

    def publish(
        self,
        datasets: tuple[str, ...],
        *,
        exception_count: str = "0",
        ledger_row_count: int | None = None,
        snapshot_id: str = "historical-replay-bundle-v1",
    ) -> Path:
        import pyarrow as pa
        import pyarrow.parquet as pq

        plans = []
        cutoff = datetime(2026, 7, 28, 18, tzinfo=SHANGHAI)
        for index, dataset in enumerate(datasets):
            source = self.root / f"{dataset}.parquet"
            if dataset == "corporate_action_exceptions":
                count = int(exception_count) if ledger_row_count is None else ledger_row_count
                table = pa.table(
                    {
                        "exception_id": pa.array(
                            [f"exception-{item}" for item in range(count)],
                            type=pa.string(),
                        )
                    }
                )
            else:
                table = pa.Table.from_pylist([{"row_id": index + 1, "dataset": dataset}])
            pq.write_table(table, source)
            plans.append(
                SnapshotDatasetPlan(
                    dataset=dataset,
                    version="test-v1",
                    source_path=source,
                    source_revisions=self.source_revisions(dataset, exception_count),
                    knowledge_cutoff=cutoff,
                    created_at=cutoff,
                )
            )
        materialized = publish_snapshot(
            self.catalog,
            SnapshotPublicationPlan(
                reference=SnapshotRef(
                    market_date=date(2026, 7, 28),
                    knowledge_cutoff=cutoff,
                    source_revision="historical-replay-test-v1",
                    snapshot_id=snapshot_id,
                ),
                universe_policy_version="test-universe-v1",
                datasets=tuple(plans),
            ),
        )
        return materialized.location

    def test_complete_checksum_verified_bundle_is_ready_without_execution_authority(self) -> None:
        location = self.publish(REQUIRED_DATASETS)

        first = load_historical_replay_bundle(location, self.policy)
        second = load_historical_replay_bundle(location, self.policy)

        self.assertTrue(first.ready_for_frame_construction)
        self.assertFalse(first.execution_authorized)
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(
            first.partition_paths("trading_calendar"),
            (location / "datasets" / "trading_calendar" / "trading_calendar.parquet",),
        )

    def test_missing_calendar_and_normalized_actions_fail_before_frame_read(self) -> None:
        location = self.publish(
            tuple(
                dataset
                for dataset in REQUIRED_DATASETS
                if dataset not in {"corporate_actions_normalized", "trading_calendar"}
            )
        )

        with self.assertRaisesRegex(
            HistoricalReplayBundleError,
            "corporate_actions_normalized, trading_calendar",
        ):
            load_historical_replay_bundle(location, self.policy)

    def test_versioned_exceptions_are_accepted_but_lineage_drift_is_blocked(self) -> None:
        location = self.publish(REQUIRED_DATASETS, exception_count="1")
        bundle = load_historical_replay_bundle(location, self.policy)
        self.assertTrue(bundle.ready_for_frame_construction)

        mismatched = self.publish(
            REQUIRED_DATASETS,
            exception_count="1",
            ledger_row_count=0,
            snapshot_id="historical-replay-bundle-ledger-mismatch-v1",
        )
        with self.assertRaisesRegex(HistoricalReplayBundleError, "row count does not reconcile"):
            load_historical_replay_bundle(mismatched, self.policy)

        clean_location = self.publish(
            REQUIRED_DATASETS,
            snapshot_id="historical-replay-bundle-policy-drift-v1",
        )
        changed_policy = CorporateActionNormalizationPolicy(
            policy_version="changed-policy-v1",
            maximum_factor_ratio_error=self.policy.maximum_factor_ratio_error,
            minimum_candidate_error_separation=self.policy.minimum_candidate_error_separation,
            cross_check_baselines=self.policy.cross_check_baselines,
        )
        with self.assertRaisesRegex(HistoricalReplayBundleError, "policy does not match"):
            load_historical_replay_bundle(clean_location, changed_policy)

    def test_tampered_partition_fails_integrity_check(self) -> None:
        location = self.publish(REQUIRED_DATASETS)
        calendar = location / "datasets" / "trading_calendar" / "trading_calendar.parquet"
        calendar.write_bytes(calendar.read_bytes() + b"tampered")

        with self.assertRaisesRegex(HistoricalReplayBundleError, "integrity failed"):
            load_historical_replay_bundle(location, self.policy)

    def test_partition_path_cannot_escape_snapshot_directory(self) -> None:
        location = self.publish(REQUIRED_DATASETS)
        manifest_path = location / "snapshot.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        calendar = next(
            item for item in payload["datasets"] if item["dataset"] == "trading_calendar"
        )
        checksum = next(iter(calendar["content_checksums"].values()))
        calendar["partitions"] = ["../outside.parquet"]
        calendar["content_checksums"] = {"../outside.parquet": checksum}
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(HistoricalReplayBundleError, "integrity failed"):
            load_historical_replay_bundle(location, self.policy)


if __name__ == "__main__":
    unittest.main()
