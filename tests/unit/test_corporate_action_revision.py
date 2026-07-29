import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quanthus.contracts import SnapshotRef
from quanthus.corporate_action_corpus import CorporateActionCorpusPaths
from quanthus.corporate_action_normalization import load_corporate_action_normalization_policy
from quanthus.corporate_action_revision import (
    CHANGE_FILE,
    EXCEPTION_FILE,
    NORMALIZED_FILE,
    CorporateActionRevisionError,
    build_corporate_action_revision,
    check_corporate_action_revision,
    promote_corporate_action_revision,
    require_known_exception_reasons,
)
from quanthus.data_foundation import SnapshotCatalog
from quanthus.historical_replay_snapshot import publish_historical_replay_snapshot
from quanthus.snapshot_publish import (
    SnapshotDatasetPlan,
    SnapshotPublicationPlan,
    publish_snapshot,
)

P1_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(module) is not None for module in ("duckdb", "pyarrow")
)
SHANGHAI = ZoneInfo("Asia/Shanghai")

if P1_DEPENDENCIES_AVAILABLE:
    import pyarrow as pa
    import pyarrow.parquet as pq


@unittest.skipUnless(P1_DEPENDENCIES_AVAILABLE, "P1 dependencies are not installed")
class CorporateActionRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot = self.root / "snapshot"
        self.snapshot.mkdir()
        self.policy = load_corporate_action_normalization_policy()
        self.actions = self.root / "corporate-actions.parquet"
        self.authority = self.write_authority()
        self.write_snapshot()

    def write_snapshot(self) -> None:
        datasets = {
            "daily_ohlcv_none": (
                "datasets/raw.parquet",
                [{"stock_code": "000001", "date": date(2020, 5, 27), "close": 13.0}],
            ),
            "daily_ohlcv_qfq": (
                "datasets/qfq.parquet",
                [{"stock_code": "000001", "date": date(2020, 5, 27), "close": 13.0}],
            ),
            "adjustment_factors": (
                "datasets/factors.parquet",
                [
                    {
                        "stock_code": "000001",
                        "effective_date": date(2020, 5, 28),
                        "fore_adjust_factor": 1.0,
                        "knowledge_cutoff": date(2020, 5, 22),
                    }
                ],
            ),
        }
        manifests = []
        for dataset, (relative, rows) in datasets.items():
            path = self.snapshot / relative
            path.parent.mkdir(exist_ok=True)
            pq.write_table(pa.Table.from_pylist(rows), path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifests.append(
                {
                    "dataset": dataset,
                    "partitions": [relative],
                    "content_checksums": {relative: f"sha256:{digest}"},
                }
            )
        (self.snapshot / "snapshot.json").write_text(
            json.dumps(
                {
                    "snapshot": {"snapshot_id": "corporate-action-revision-test"},
                    "datasets": manifests,
                }
            ),
            encoding="utf-8",
        )

    def write_authority(self) -> Path:
        revision = self.root / "authority-revision"
        raw = revision / "em_raw"
        raw.mkdir(parents=True)
        (revision / "META.json").write_text(
            json.dumps({"revision": "test-eastmoney-implementation-v1"}),
            encoding="utf-8",
        )
        (raw / "000001.json").write_text(
            json.dumps(
                {
                    "code": "000001",
                    "rows": [
                        {
                            "SECURITY_CODE": "000001",
                            "EX_DIVIDEND_DATE": "2020-05-28 00:00:00",
                            "NOTICE_DATE": "2020-05-22 00:00:00",
                            "PRETAX_BONUS_RMB": 2.18,
                            "BONUS_RATIO": None,
                            "IT_RATIO": None,
                            "ASSIGN_PROGRESS": "实施分配",
                            "IMPL_PLAN_PROFILE": "10派2.18元(含税)",
                        }
                    ],
                    "error": None,
                    "fetched_at": "2026-07-29T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        return raw

    @staticmethod
    def valid_action() -> dict[str, object]:
        return {
            "action_id": "action-valid",
            "stock_code": "000001",
            "ex_date": date(2020, 5, 28),
            "announce_date": date(2020, 5, 22),
            "cash_per_share": 0.218,
            "bonus_shares": None,
            "transfer_shares": None,
            "progress": "实施",
            "source": "test.corporate-actions",
            "source_row_key": "row:valid",
        }

    @staticmethod
    def missing_date_action() -> dict[str, object]:
        return {
            "action_id": "action-missing-date",
            "stock_code": "000002",
            "ex_date": None,
            "announce_date": date(2020, 5, 23),
            "cash_per_share": None,
            "bonus_shares": 1.0,
            "transfer_shares": None,
            "progress": "实施",
            "source": "test.corporate-actions",
            "source_row_key": "row:missing-date",
        }

    def build(
        self,
        *,
        revision: str,
        rows: list[dict[str, object]],
        previous: Path | None = None,
    ):
        pq.write_table(pa.Table.from_pylist(rows), self.actions)
        return build_corporate_action_revision(
            paths=CorporateActionCorpusPaths(
                snapshot_dir=self.snapshot,
                corporate_actions=self.actions,
                eastmoney_raw_dir=self.authority,
            ),
            policy=self.policy,
            scope_start=date(2020, 1, 1),
            scope_end=date(2020, 12, 31),
            output=self.root / revision,
            revision=revision,
            knowledge_cutoff=datetime(2026, 7, 29, 12, tzinfo=SHANGHAI),
            previous_revision=previous,
        )

    def publish_base_snapshot(self) -> Path:
        cutoff = datetime(2026, 7, 28, 18, tzinfo=SHANGHAI)
        plans = []
        for index, dataset in enumerate(
            (
                "daily_ohlcv_none",
                "daily_universe",
                "price_limit_execution_daily",
                "security_code_validity",
            )
        ):
            source = self.root / f"base-{dataset}.parquet"
            pq.write_table(pa.Table.from_pylist([{"row_id": index + 1}]), source)
            plans.append(
                SnapshotDatasetPlan(
                    dataset=dataset,
                    version=f"test-{dataset}-v1",
                    source_path=source,
                    source_revisions={"source": f"test-{dataset}-v1"},
                    knowledge_cutoff=cutoff,
                    created_at=cutoff,
                )
            )
        materialized = publish_snapshot(
            SnapshotCatalog(self.root / "base-catalog"),
            SnapshotPublicationPlan(
                reference=SnapshotRef(
                    market_date=date(2026, 7, 28),
                    knowledge_cutoff=cutoff,
                    source_revision="base-snapshot-v1",
                    snapshot_id="base-snapshot-v1",
                ),
                universe_policy_version="test-universe-v1",
                datasets=tuple(plans),
            ),
        )
        return materialized.location

    def test_build_materializes_versioned_rows_and_detects_later_resolution(self) -> None:
        first = self.build(
            revision="corporate-actions-v1",
            rows=[self.valid_action(), self.missing_date_action()],
        )

        self.assertEqual(first.normalized_count, 1)
        self.assertEqual(first.exception_count, 1)
        self.assertEqual(pq.ParquetFile(first.output / NORMALIZED_FILE).metadata.num_rows, 1)
        self.assertEqual(pq.ParquetFile(first.output / EXCEPTION_FILE).metadata.num_rows, 1)
        first_changes = json.loads((first.output / CHANGE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(
            first_changes["counts"],
            {"current": 1, "new": 1, "persistent": 0, "resolved": 0},
        )
        self.assertFalse(first_changes["execution_authorized"])

        second = self.build(
            revision="corporate-actions-v2",
            rows=[self.valid_action()],
            previous=first.output,
        )
        second_changes = json.loads((second.output / CHANGE_FILE).read_text(encoding="utf-8"))

        self.assertEqual(second.exception_count, 0)
        self.assertEqual(
            second_changes["counts"],
            {"current": 0, "new": 0, "persistent": 0, "resolved": 1},
        )
        normalized_lineage = second.snapshot_source_revisions["corporate_actions_normalized"]
        self.assertEqual(normalized_lineage["normalization_exception_count"], "0")
        self.assertEqual(
            normalized_lineage["exception_ledger_digest"], second.exception_ledger_digest
        )

    def test_unknown_exception_reason_requires_policy_review(self) -> None:
        with self.assertRaisesRegex(CorporateActionRevisionError, "requires review"):
            require_known_exception_reasons({"new_vendor_semantic"})

    def test_check_rejects_tampering_and_promote_writes_a_verified_pointer(self) -> None:
        revision = self.build(revision="corporate-actions-v1", rows=[self.valid_action()])
        pointer = self.root / "corporate_action_CURRENT.json"

        promoted = promote_corporate_action_revision(
            revision_dir=revision.output,
            pointer=pointer,
        )
        payload = json.loads(pointer.read_text(encoding="utf-8"))

        self.assertEqual(promoted.revision, revision.revision)
        self.assertEqual(payload["revision"], revision.revision)
        self.assertFalse(payload["execution_authorized"])

        normalized = revision.output / NORMALIZED_FILE
        normalized.write_bytes(normalized.read_bytes() + b"tampered")
        with self.assertRaisesRegex(CorporateActionRevisionError, "checksum mismatch"):
            check_corporate_action_revision(revision.output)

    def test_publish_extends_base_and_gates_complete_historical_bundle(self) -> None:
        revision = self.build(
            revision="corporate-actions-v1",
            rows=[self.valid_action(), self.missing_date_action()],
        )
        base = self.publish_base_snapshot()
        calendar = self.root / "trading-calendar.parquet"
        pq.write_table(
            pa.Table.from_pylist(
                [{"exchange": "SSE", "trade_date": date(2026, 7, 28), "is_open": 1}]
            ),
            calendar,
        )
        cutoff = datetime(2026, 7, 29, 13, tzinfo=SHANGHAI)

        published = publish_historical_replay_snapshot(
            catalog=SnapshotCatalog(self.root / "replay-catalog"),
            base_snapshot_dir=base,
            corporate_action_revision_dir=revision.output,
            trading_calendar=calendar,
            calendar_source_revision="test-calendar-v1",
            calendar_knowledge_cutoff=cutoff,
            snapshot_id="historical-replay-v1",
            source_revision="historical-replay-test-v1",
            knowledge_cutoff=cutoff,
            policy=self.policy,
        )

        self.assertTrue(published.bundle.ready_for_frame_construction)
        self.assertFalse(published.bundle.execution_authorized)
        self.assertEqual(len(published.bundle.datasets), 7)
        self.assertEqual(
            published.bundle.partition_paths("corporate_action_exceptions"),
            (
                published.materialized.location
                / "datasets"
                / "corporate_action_exceptions"
                / EXCEPTION_FILE,
            ),
        )


if __name__ == "__main__":
    unittest.main()
