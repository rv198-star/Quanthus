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
from quanthus.pipelines.daily_warehouse import (
    DailyWarehouseError,
    build_daily_dwd_revision,
    check_daily_dwd_revision,
    market_session_is_open,
    promote_daily_dwd_revision,
)


@unittest.skipIf(pa is None or pq is None, "P1 dependencies are not installed")
class DailyWarehouseTests(unittest.TestCase):
    def _mapping(self, root: Path) -> Path:
        path = root / "code-validity.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "mapping_version": "test-code-validity.v1",
                    "status": "frozen",
                    "source_revision": "test-evidence-v1",
                    "intervals": [
                        {
                            "security_id": "000001",
                            "stock_code": "000001",
                            "exchange": "SZSE",
                            "valid_from": "1991-04-03",
                            "valid_to": None,
                            "source": "test",
                            "evidence_locator": "test://000001",
                            "evidence_sha256": f"sha256:{'a' * 64}",
                            "source_revision": "test-evidence-v1",
                            "knowledge_cutoff": "1991-04-03T00:00:00+08:00",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def _release(
        self,
        root: Path,
        market_date: date,
        *,
        close: float = 10.1,
        status_code: str = "000001",
    ) -> Path:
        assert pa is not None
        assert pq is not None
        root.mkdir(parents=True, exist_ok=True)
        inputs = root / "inputs"
        inputs.mkdir()
        bars = inputs / "daily_ohlcv_none_delta.parquet"
        statuses = inputs / "daily_status_observation_delta.parquet"
        master = inputs / "ref_security_master.parquet"
        calendar = inputs / "ref_trading_calendar.parquet"
        stock_list = inputs / "stock_list.parquet"
        built_at = datetime(2026, 7, 24, 8, tzinfo=timezone.utc)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "stock_code": "000001",
                        "date": market_date,
                        "open": 10.0,
                        "high": 10.2,
                        "low": 9.9,
                        "close": close,
                        "volume": 1000.0,
                        "amount": 10100.0,
                        "turnover_rate": 0.2,
                        "pct_change": 1.0,
                        "adj_mode": "none",
                        "adj_version": "none_v1",
                        "source": "test",
                        "built_at": built_at,
                    }
                ]
            ),
            bars,
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "stock_code": status_code,
                        "date": market_date,
                        "tradestatus": "1",
                        "isST": "0",
                        "source": "test",
                        "built_at": built_at,
                    }
                ]
            ),
            statuses,
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "security_id": "000001",
                        "stock_code": "000001",
                        "exchange": "SZSE",
                        "name": "TEST",
                        "list_date": date(1991, 4, 3),
                        "delist_date": None,
                        "status": "listed",
                        "bs_code": "sz.000001",
                        "as_of": market_date,
                        "source": "test",
                        "built_at": built_at,
                    }
                ]
            ),
            master,
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {"exchange": "SSE", "trade_date": market_date, "is_open": 1},
                    {"exchange": "SZSE", "trade_date": market_date, "is_open": 1},
                ]
            ),
            calendar,
        )
        pq.write_table(
            pa.Table.from_pylist(
                [{"stock_code": "000001", "stock_name": "TEST", "market": "deep-A"}]
            ),
            stock_list,
        )
        spec = root / "spec.json"
        spec.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_id": f"hermes-daily-{market_date.isoformat()}-v1",
                    "schedule": "daily",
                    "market_date": market_date.isoformat(),
                    "producer": "hermes-test",
                    "artifacts": [
                        {
                            "name": "daily_ohlcv_none_delta",
                            "source_path": str(bars),
                            "relative_path": "artifacts/daily_ohlcv_none_delta.parquet",
                            "source_revision": "daily-test-v1",
                            "knowledge_cutoff": built_at.isoformat(),
                        },
                        {
                            "name": "daily_status_observation_delta",
                            "source_path": str(statuses),
                            "relative_path": "artifacts/daily_status_observation_delta.parquet",
                            "source_revision": "status-test-v1",
                            "knowledge_cutoff": built_at.isoformat(),
                        },
                        {
                            "name": "security_master",
                            "source_path": str(master),
                            "relative_path": "artifacts/ref_security_master.parquet",
                            "source_revision": "master-test-v1",
                            "knowledge_cutoff": built_at.isoformat(),
                        },
                        {
                            "name": "trading_calendar",
                            "source_path": str(calendar),
                            "relative_path": "artifacts/ref_trading_calendar.parquet",
                            "source_revision": "calendar-test-v1",
                            "knowledge_cutoff": built_at.isoformat(),
                        },
                        {
                            "name": "stock_list",
                            "source_path": str(stock_list),
                            "relative_path": "artifacts/stock_list.parquet",
                            "source_revision": "stock-list-test-v1",
                            "knowledge_cutoff": built_at.isoformat(),
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        release = root / "release"
        publish_ods_release(spec_path=spec, output=release)
        return release

    def test_build_check_and_atomic_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            revision = "daily-market-test-20260724-v1"
            output = root / "revisions" / revision
            base_pointer = root / "revisions" / "daily_ohlcv_none_CURRENT.json"
            base_pointer.parent.mkdir(parents=True)
            base_pointer.write_text(
                json.dumps(
                    {
                        "revision": "p1-history-base-v1",
                        "meta_sha256": f"sha256:{'b' * 64}",
                    }
                ),
                encoding="utf-8",
            )

            built = build_daily_dwd_revision(
                release_dir=self._release(root, market_date),
                output=output,
                revision=revision,
                market_date=market_date,
                universe_policy_path=Path("config/p1-universe.v2.json"),
                code_validity_mapping_path=self._mapping(root),
                code_revision="c" * 40,
                base_pointer=base_pointer,
            )
            checked = check_daily_dwd_revision(output)
            promoted = promote_daily_dwd_revision(
                revision_dir=output,
                pointer=root / "revisions" / "daily_market_CURRENT.json",
            )

            self.assertEqual(built.row_count, 1)
            self.assertEqual(checked.checksums, built.checksums)
            self.assertEqual(promoted.market_date, market_date)
            pointer = json.loads(
                (root / "revisions" / "daily_market_CURRENT.json").read_text(encoding="utf-8")
            )
            self.assertEqual(pointer["revision"], revision)
            self.assertTrue((output / "market_date=2026-07-24" / "part-00000.parquet").is_file())

    def test_invalid_ohlc_fails_closed_before_revision_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            output = root / "revisions" / "bad-v1"
            base_pointer = root / "base.json"
            base_pointer.write_text(
                json.dumps(
                    {
                        "revision": "p1-history-base-v1",
                        "meta_sha256": f"sha256:{'b' * 64}",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(DailyWarehouseError, "invalid rows"):
                build_daily_dwd_revision(
                    release_dir=self._release(root, market_date, close=10.3),
                    output=output,
                    revision="bad-v1",
                    market_date=market_date,
                    universe_policy_path=Path("config/p1-universe.v2.json"),
                    code_validity_mapping_path=self._mapping(root),
                    code_revision="c" * 40,
                    base_pointer=base_pointer,
                )

            self.assertFalse(output.exists())

    def test_market_session_requires_complete_consistent_exchange_evidence(self) -> None:
        assert pa is not None
        assert pq is not None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            calendar = root / "calendar.parquet"
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {"exchange": "SSE", "trade_date": market_date, "is_open": 1},
                        {"exchange": "SZSE", "trade_date": market_date, "is_open": 1},
                    ]
                ),
                calendar,
            )
            self.assertTrue(market_session_is_open(calendar=calendar, market_date=market_date))

            pq.write_table(
                pa.Table.from_pylist(
                    [{"exchange": "SSE", "trade_date": market_date, "is_open": 1}]
                ),
                calendar,
            )
            with self.assertRaisesRegex(DailyWarehouseError, "no complete SSE/SZSE evidence"):
                market_session_is_open(calendar=calendar, market_date=market_date)

    def test_missing_in_scope_status_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            base_pointer = root / "base.json"
            base_pointer.write_text(
                json.dumps(
                    {
                        "revision": "p1-history-base-v1",
                        "meta_sha256": f"sha256:{'b' * 64}",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(DailyWarehouseError, "status coverage is missing"):
                build_daily_dwd_revision(
                    release_dir=self._release(root, market_date, status_code="999999"),
                    output=root / "revisions" / "missing-status-v1",
                    revision="missing-status-v1",
                    market_date=market_date,
                    universe_policy_path=Path("config/p1-universe.v2.json"),
                    code_validity_mapping_path=self._mapping(root),
                    code_revision="c" * 40,
                    base_pointer=base_pointer,
                )

    def test_same_day_rerun_replaces_partition_without_chaining_duplicate_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_date = date(2026, 7, 24)
            revisions = root / "revisions"
            revisions.mkdir()
            base_pointer = revisions / "daily_ohlcv_none_CURRENT.json"
            base_pointer.write_text(
                json.dumps(
                    {
                        "revision": "p1-history-base-v1",
                        "meta_sha256": f"sha256:{'b' * 64}",
                    }
                ),
                encoding="utf-8",
            )
            pointer = revisions / "daily_market_CURRENT.json"
            first_revision = "daily-market-test-20260724-v1"
            first_output = revisions / first_revision
            build_daily_dwd_revision(
                release_dir=self._release(root / "first", market_date),
                output=first_output,
                revision=first_revision,
                market_date=market_date,
                universe_policy_path=Path("config/p1-universe.v2.json"),
                code_validity_mapping_path=self._mapping(root),
                code_revision="c" * 40,
                parent_pointer=pointer,
                base_pointer=base_pointer,
            )
            promote_daily_dwd_revision(revision_dir=first_output, pointer=pointer)

            second_revision = "daily-market-test-20260724-v2"
            second_output = revisions / second_revision
            build_daily_dwd_revision(
                release_dir=self._release(root / "second", market_date),
                output=second_output,
                revision=second_revision,
                market_date=market_date,
                universe_policy_path=Path("config/p1-universe.v2.json"),
                code_validity_mapping_path=self._mapping(root),
                code_revision="d" * 40,
                parent_pointer=pointer,
                base_pointer=base_pointer,
            )

            manifest = json.loads((second_output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["lineage"]["parent"]["kind"], "historical_base")
            self.assertEqual(
                manifest["lineage"]["parent"]["revision"], "p1-history-base-v1"
            )


if __name__ == "__main__":
    unittest.main()
