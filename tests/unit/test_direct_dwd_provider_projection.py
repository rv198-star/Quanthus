from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.backtest.direct_dwd_provider import (
    DATASETS,
    _copy,
    _materialize,
    materialize_sharded_direct_dwd,
    merge_direct_dwd_date_partitions,
)
from quanthus.event_library.sharded_batch import SHARDED_DWD_DAILY_DATASETS

SSE = "venue_bec59e31eb6bb28111a10d7e6d2c689f"


class DirectDwdProjectionTests(unittest.TestCase):
    def test_sharded_batch_merges_every_date_partitioned_dataset(self) -> None:
        self.assertEqual(
            set(SHARDED_DWD_DAILY_DATASETS),
            {
                "daily_ohlcv_none",
                "daily_universe",
                "price_limit_execution_daily",
                "trading_calendar",
            },
        )

    def test_merge_reconstitutes_canonical_row_groups_across_partitions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            rows_per_partition = 40_000
            for partition in range(3):
                start = partition * rows_per_partition
                path = root / f"part-{partition}.parquet"
                pq.write_table(
                    pa.table(
                        {
                            "security_id": [
                                f"{index:06d}" for index in range(start, start + rows_per_partition)
                            ],
                            "date": [date(2012, 1, 4)] * rows_per_partition,
                            "value": list(range(start, start + rows_per_partition)),
                        }
                    ),
                    path,
                    compression="zstd",
                    row_group_size=50_000,
                )
                paths.append(path)

            standard = root / "standard.parquet"
            connection = duckdb.connect(":memory:")
            try:
                # Parameters cannot bind inside COPY, so use DuckDB's normalized path literals.
                quoted = ",".join("'" + str(path).replace("'", "''") + "'" for path in paths)
                _copy(
                    connection,
                    f"SELECT * FROM read_parquet([{quoted}]) ORDER BY date,security_id",
                    standard,
                )
            finally:
                connection.close()

            merged = root / "merged.parquet"
            merge_direct_dwd_date_partitions(
                "daily_ohlcv_none",
                tuple(str(path) for path in paths),
                str(merged),
                str(root / "spill"),
            )

            self.assertEqual(merged.read_bytes(), standard.read_bytes())

    def test_materialization_retains_additive_p4_identity_decimal_and_lineage_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dwd = root / "dwd"
            correction = root / "correction"
            output = root / "output"
            output.mkdir()
            day = date(2012, 1, 4)
            known = datetime(2026, 8, 3, 23, 59, tzinfo=timezone.utc)
            listing_effective = datetime(2010, 1, 1, tzinfo=timezone.utc)
            security_effective = datetime(2009, 1, 1, tzinfo=timezone.utc)
            superseded_security_effective = datetime(2000, 1, 1, tzinfo=timezone.utc)

            self._write(
                dwd / "payload/listed_company/dwd_security.parquet",
                [
                    {
                        "security_id": "security-001",
                        "company_id": "company-old",
                        "instrument_type": "common_equity",
                        "effective_from": superseded_security_effective,
                        "effective_to": security_effective,
                        "knowledge_from": known,
                        "knowledge_to": None,
                    },
                    {
                        "security_id": "security-001",
                        "company_id": "company-001",
                        "instrument_type": "common_equity",
                        "effective_from": security_effective,
                        "effective_to": None,
                        "knowledge_from": known,
                        "knowledge_to": None,
                    },
                ],
            )
            self._write(
                dwd / "payload/listed_company/dwd_listing.parquet",
                [
                    {
                        "listing_id": "listing-001",
                        "security_id": "security-001",
                        "venue_id": SSE,
                        "listing_code": "600001",
                        "board_code": "MAIN",
                        "effective_from": listing_effective,
                        "effective_to": None,
                        "knowledge_from": known,
                        "knowledge_to": None,
                        "source_artifact_sha256": "sha256:" + "a" * 64,
                        "source_record_id": "listing-source-001",
                        "source_revision": "listing-v1",
                    }
                ],
            )
            self._write(
                dwd
                / "payload/security_market_observation/dwd_security_bar"
                / "interval_size=1d/observation_start_year=2012"
                / "observation_start_month=1/part-00000.parquet",
                [
                    {
                        "listing_id": "listing-001",
                        "calendar_session_id": "session-001",
                        "observation_start": datetime(2012, 1, 4, 7, 0, tzinfo=timezone.utc),
                        "interval_size": "1d",
                        "price_basis": "UNADJUSTED",
                        "open": Decimal("10.1234567890"),
                        "high": Decimal("10.5000000000"),
                        "low": Decimal("10.0000000000"),
                        "close": Decimal("10.3000000000"),
                        "volume": Decimal("1000.0000000000"),
                        "turnover": Decimal("10123.4567890000"),
                        "knowledge_from": known,
                        "knowledge_to": None,
                        "source_record_id": "bar-source-001",
                        "source_revision": "bar-v1",
                        "source_artifact_sha256": "sha256:" + "b" * 64,
                    }
                ],
            )
            self._write(
                dwd
                / "payload/tradability_state/dwd_trading_calendar"
                / "trade_date_year=2012/part-00000.parquet",
                [
                    {
                        "calendar_session_id": "session-001",
                        "venue_id": SSE,
                        "trade_date": day,
                        "is_open": True,
                        "knowledge_from": known,
                        "knowledge_to": None,
                    }
                ],
            )
            state = {
                "listing_id": "listing-001",
                "calendar_session_id": "session-001",
                "observation_start": datetime(2012, 1, 4, 7, 0, tzinfo=timezone.utc),
                "is_tradable": True,
                "can_buy": True,
                "can_sell": True,
                "reason_code": "TRADABLE_OBSERVED",
                "upper_price": Decimal("11.00"),
                "lower_price": Decimal("9.00"),
                "source_revision": "state-v1",
                "source_record_id": "state-source-001",
                "knowledge_from": known,
                "knowledge_to": None,
            }
            self._write(
                dwd
                / "payload/tradability_state/dwd_tradability_state"
                / "observation_start_year=2012/observation_start_month=1"
                / "part-00000.parquet",
                [state],
            )
            self._write_empty_state(
                correction / "payload/tradability_state/dwd_tradability_state_replacement.parquet"
            )
            self._write(
                correction / "payload/replay/corporate_actions_normalized.parquet",
                [
                    {
                        "security_code": "600001",
                        "effective_date": day,
                        "action_id": "action-001",
                    }
                ],
            )
            self._write_empty_exceptions(
                correction / "payload/replay/corporate_action_exceptions.parquet"
            )

            class TrackedConnection:
                def __init__(self, connection: object) -> None:
                    self.connection = connection
                    self.queries: list[str] = []

                def execute(self, query: str) -> object:
                    self.queries.append(query)
                    return self.connection.execute(query)

                def close(self) -> None:
                    self.connection.close()

            opened_connections: list[TrackedConnection] = []
            actual_connect = duckdb.connect

            def tracked_connect(*args: object, **kwargs: object) -> object:
                connection = TrackedConnection(actual_connect(*args, **kwargs))
                opened_connections.append(connection)
                return connection

            with mock.patch("duckdb.connect", side_effect=tracked_connect):
                counts = _materialize(
                    output,
                    dwd_root=dwd,
                    correction_root=correction,
                    history_start=day,
                    execution_end=day,
                )

            self.assertEqual(counts["daily_ohlcv_none"], 1)
            self.assertEqual(len(opened_connections), 4)
            for connection, memory_limit in zip(
                opened_connections,
                ("6GB", "12GB", "12GB", "6GB"),
                strict=True,
            ):
                self.assertEqual(
                    connection.queries[:3],
                    [
                        f"SET memory_limit='{memory_limit}'",
                        "SET threads=1",
                        "SET preserve_insertion_order=false",
                    ],
                )
                self.assertTrue(connection.queries[3].startswith("SET temp_directory="))
                with self.assertRaises(duckdb.Error):
                    connection.connection.execute("SELECT 1")
            self.assertEqual(
                list(output.glob(".duckdb-materialize-spill-*")),
                [],
            )
            bar = pq.read_table(output / "tables/daily_ohlcv_none.parquet").to_pylist()[0]
            self.assertEqual(bar["dwd_security_id"], "security-001")
            self.assertEqual(bar["dwd_company_id"], "company-001")
            self.assertEqual(bar["dwd_listing_id"], "listing-001")
            self.assertEqual(bar["dwd_instrument_type"], "common_equity")
            self.assertEqual(bar["dwd_open_decimal"], Decimal("10.1234567890"))
            self.assertEqual(bar["dwd_turnover_decimal"], Decimal("10123.4567890000"))
            self.assertEqual(bar["dwd_bar_source_record_id"], "bar-source-001")

            universe = pq.read_table(output / "tables/daily_universe.parquet").to_pylist()[0]
            self.assertEqual(universe["dwd_security_id"], "security-001")
            self.assertEqual(universe["dwd_security_effective_from"], security_effective)
            self.assertEqual(universe["dwd_listing_effective_from"], listing_effective)

            limits = pq.read_table(
                output / "tables/price_limit_execution_daily.parquet"
            ).to_pylist()[0]
            self.assertEqual(limits["dwd_security_id"], "security-001")
            self.assertEqual(limits["dwd_state_knowledge_from"], known)

            sharded_output = root / "sharded-output"
            sharded_counts = materialize_sharded_direct_dwd(
                sharded_output,
                dwd_root=dwd,
                correction_root=correction,
                history_start=day,
                execution_end=day,
                workers=1,
            )
            self.assertEqual(sharded_counts, counts)
            for dataset in DATASETS:
                self.assertEqual(
                    (sharded_output / "tables" / f"{dataset}.parquet").read_bytes(),
                    (output / "tables" / f"{dataset}.parquet").read_bytes(),
                )

            self._write_empty_actions(
                correction / "payload/replay/corporate_actions_normalized.parquet"
            )
            empty_action_partition = root / "empty-action-partition"
            empty_action_partition.mkdir()
            partition_counts = _materialize(
                empty_action_partition,
                dwd_root=dwd,
                correction_root=correction,
                history_start=day,
                execution_end=day,
                allow_empty_non_partitioned=True,
            )
            self.assertEqual(partition_counts["corporate_actions_normalized"], 0)
            self.assertEqual(partition_counts["daily_ohlcv_none"], 1)

    @staticmethod
    def _write(path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), path)

    @staticmethod
    def _write_empty_state(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        schema = pa.schema(
            [
                ("listing_id", pa.string()),
                ("calendar_session_id", pa.string()),
                ("observation_start", pa.timestamp("us", tz="UTC")),
                ("is_tradable", pa.bool_()),
                ("can_buy", pa.bool_()),
                ("can_sell", pa.bool_()),
                ("reason_code", pa.string()),
                ("upper_price", pa.decimal128(38, 10)),
                ("lower_price", pa.decimal128(38, 10)),
                ("source_revision", pa.string()),
                ("source_record_id", pa.string()),
                ("knowledge_from", pa.timestamp("us", tz="UTC")),
                ("knowledge_to", pa.timestamp("us", tz="UTC")),
            ]
        )
        pq.write_table(pa.Table.from_pylist([], schema=schema), path)

    @staticmethod
    def _write_empty_exceptions(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        schema = pa.schema(
            [
                ("security_code", pa.string()),
                ("blocked_from", pa.date32()),
                ("blocked_through", pa.date32()),
                ("reason_code", pa.string()),
            ]
        )
        pq.write_table(pa.Table.from_pylist([], schema=schema), path)

    @staticmethod
    def _write_empty_actions(path: Path) -> None:
        schema = pa.schema(
            [
                ("security_code", pa.string()),
                ("effective_date", pa.date32()),
                ("action_id", pa.string()),
            ]
        )
        pq.write_table(pa.Table.from_pylist([], schema=schema), path)


if __name__ == "__main__":
    unittest.main()
