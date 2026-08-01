from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse.market_observation import (
    _audit_outputs,
    _bar_query,
    _canonical_digest,
    _copy_query,
    _expected_row_counts,
    _index_bar_query,
    _normalize_outputs,
    _partition_digest,
    _register_sources,
    _schemas,
    _stable_index_id,
    _table_digest,
    _valuation_query,
    _verify_existing_output,
    _write_index_seed,
)


class MarketObservationQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cutoff = "2026-08-01T18:35:00+00:00"
        self.registry: list[dict[str, object]] = []
        self._write_references()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_source(
        self, identity: str, rows: list[dict[str, object]], *, name: str | None = None
    ) -> Path:
        path = self.root / "ods" / f"{name or identity}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.registry.append(
            {
                "absolute_path": str(path.resolve()),
                "relative_path": f"payload/{path.name}",
                "dataset_id": identity,
                "mapping_identity": identity,
                "source_sha256": f"sha256:{digest}",
                "declared_rows": len(rows),
            }
        )
        return path

    def _write_references(self) -> None:
        self.listing = self.root / "dwd_listing.parquet"
        self.venue = self.root / "dwd_trading_venue.parquet"
        self.calendar = self.root / "calendar.parquet"
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "listing_id": "listing-1",
                        "security_id": "security-1",
                        "venue_id": "venue-sh",
                        "listing_code": "000001",
                        "effective_from": datetime(1990, 1, 1, tzinfo=timezone.utc),
                        "effective_to": None,
                        "knowledge_from": datetime.fromisoformat(self.cutoff),
                        "knowledge_to": None,
                    }
                ]
            ),
            self.listing,
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "venue_id": "venue-sh",
                        "mic_code": "XSHG",
                    },
                    {
                        "venue_id": "venue-sz",
                        "mic_code": "XSHE",
                    },
                ]
            ),
            self.venue,
        )
        calendar_rows = []
        for trade_date in (
            date(2026, 1, 2),
            date(2026, 1, 5),
            date(2026, 1, 6),
            date(2026, 1, 7),
        ):
            calendar_rows.append(
                {
                    "calendar_session_id": f"session-{trade_date}",
                    "venue_id": "venue-sh",
                    "trade_date": trade_date,
                    "session_type": "REGULAR_DAY",
                    "is_open": True,
                    "open_at": datetime(
                        trade_date.year,
                        trade_date.month,
                        trade_date.day,
                        1,
                        30,
                        tzinfo=timezone.utc,
                    ),
                    "close_at": datetime(
                        trade_date.year,
                        trade_date.month,
                        trade_date.day,
                        7,
                        tzinfo=timezone.utc,
                    ),
                }
            )
        pq.write_table(pa.Table.from_pylist(calendar_rows), self.calendar)

    @staticmethod
    def _bar_row(day: str, **extra: object) -> dict[str, object]:
        return {
            "stock_code": "000001",
            "date": day,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 100.0,
            "amount": 1000.0,
            "adj_mode": "none",
            **extra,
        }

    @staticmethod
    def _minute_row(label: str, **extra: object) -> dict[str, object]:
        value = MarketObservationQueryTests._bar_row("2026-01-02", **extra)
        value.pop("date")
        value["ts"] = label
        return value

    def _connection(self) -> duckdb.DuckDBPyConnection:
        connection = duckdb.connect()
        connection.execute("SET threads=1")
        _register_sources(connection, self.registry)
        return connection

    def test_bar_query_normalizes_daily_and_minute_intervals(self) -> None:
        self._write_source("daily_ohlcv_unadjusted", [self._bar_row("2026-01-02")])
        self._write_source("daily_ohlcv_none_delta", [self._bar_row("2026-01-05")])
        self._write_source("daily_ohlcv_none_hotfix", [self._bar_row("2026-01-06")])
        self._write_source(
            "minute_1m_research_archive", [self._minute_row("2026-01-02 09:31:00")]
        )
        self._write_source("minute_1m_hotfix", [self._minute_row("2026-01-02 09:32:00")])
        self._write_source(
            "minute_5m_formal_archive", [self._minute_row("2026-01-02 09:35:00")]
        )
        self._write_source("minute_5m_hotfix", [self._minute_row("2026-01-02 09:40:00")])
        self._write_source(
            "intraday_bars",
            [self._minute_row("2026-01-02T09:33:00", bar_size="1m")],
        )
        connection = self._connection()
        try:
            query = _bar_query(
                self.registry, self.listing, [self.calendar], self.cutoff
            )
            rows = connection.execute(query).to_arrow_table().to_pylist()
            work = self.root / "work"
            raw_root = work / "payload/security_market_observation/dwd_security_bar"
            _copy_query(
                connection,
                query,
                raw_root,
                ("interval_size", "observation_start_year", "observation_start_month"),
            )
            states = _normalize_outputs("dwd_security_bar", raw_root, work)
            _audit_outputs(
                connection,
                work,
                {"dwd_security_bar": states},
                listing_path=self.listing,
                index_path=self.listing,
                calendar_paths=[self.calendar],
            )
        finally:
            connection.close()
        self.assertEqual(len(rows), 8)
        self.assertEqual(sum(state["row_count"] for state in states), 8)
        self.assertEqual(len(states), 3)
        self.assertEqual({row["price_basis"] for row in rows}, {"UNADJUSTED"})
        first = next(
            row
            for row in rows
            if row["interval_size"] == "1m"
            and row["source_record_id"].endswith("09:31:00")
        )
        self.assertEqual(first["observation_start"].hour, 1)
        self.assertEqual(first["observation_start"].minute, 30)
        self.assertEqual(first["observation_end"].minute, 31)

    def test_valuation_query_expands_and_normalizes_provider_units(self) -> None:
        base = {
            "stock_code": "000001",
            "pe_ttm": 1.0,
            "pb": 2.0,
            "ps_ttm": 3.0,
            "pcf_ttm": 4.0,
            "total_share": 5.0,
            "total_mv": 6.0,
        }
        for identity, day in (
            ("valuation_daily", "2026-01-02"),
            ("valuation_history_july_repair", "2026-01-05"),
            ("valuation_daily_delta", "2026-01-06"),
            ("valuation_history_hotfix", "2026-01-07"),
        ):
            self._write_source(identity, [{**base, "date": day}])
        connection = self._connection()
        try:
            rows = connection.execute(
                _valuation_query(self.registry, self.listing, self.cutoff)
            ).to_arrow_table().to_pylist()
        finally:
            connection.close()
        self.assertEqual(len(rows), 24)
        share = next(row for row in rows if row["measure_type"] == "TOTAL_SHARE")
        market_value = next(
            row for row in rows if row["measure_type"] == "TOTAL_MARKET_VALUE"
        )
        self.assertEqual(str(share["measure_value"]), "50000.0000000000")
        self.assertEqual(str(market_value["measure_value"]), "60000.0000000000")
        self.assertEqual(market_value["currency"], "CNY")

    def test_index_query_uses_deterministic_identity_and_calendar(self) -> None:
        self._write_source(
            "index_daily",
            [
                {
                    "index_code": "000001.SH",
                    "date": "2026-01-02",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 100.0,
                    "amount": None,
                }
            ],
        )
        self._write_source(
            "index_daily_delta",
            [
                {
                    "index_code": "000001.SH",
                    "date": "2026-01-05",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 100.0,
                    "amount": 1000.0,
                }
            ],
        )
        metadata = self.root / "index-meta.json"
        metadata.write_text(
            json.dumps(
                {
                    "per_code": {
                        code: {"min": "1990-12-19"}
                        for code in ("000001.SH", "000300.SH", "399001.SZ")
                    },
                    "universe": [
                        {"index_code": "000001.SH", "name": "SSE Composite"},
                        {"index_code": "000300.SH", "name": "CSI 300"},
                        {"index_code": "399001.SZ", "name": "SZSE Component"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        index_path = self.root / "dwd_market_index.parquet"
        _write_index_seed(
            index_path,
            metadata_record={
                "path": "index-meta.json",
                "sha256": "sha256:" + "a" * 64,
            },
            ods_package=self.root,
            knowledge_cutoff=datetime.fromisoformat(self.cutoff),
        )
        connection = self._connection()
        try:
            rows = connection.execute(
                _index_bar_query(
                    self.registry,
                    index_path,
                    self.venue,
                    [self.calendar],
                    self.cutoff,
                )
            ).to_arrow_table().to_pylist()
        finally:
            connection.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["index_id"] for row in rows}, {_stable_index_id("000001.SH")})
        self.assertTrue(all(row["calendar_session_id"] for row in rows))

    def test_schemas_match_four_owned_object_shapes(self) -> None:
        schemas = _schemas()
        self.assertEqual(
            set(schemas),
            {
                "dwd_security_bar",
                "dwd_security_valuation",
                "dwd_market_index",
                "dwd_index_bar",
            },
        )
        self.assertFalse(schemas["dwd_security_bar"].field("listing_id").nullable)
        self.assertTrue(schemas["dwd_security_valuation"].field("measure_value").nullable)

    def test_source_key_reconciliation_matches_selected_rows(self) -> None:
        for identity, day in (
            ("daily_ohlcv_unadjusted", "2026-01-02"),
            ("daily_ohlcv_none_delta", "2026-01-05"),
            ("daily_ohlcv_none_hotfix", "2026-01-06"),
        ):
            self._write_source(identity, [self._bar_row(day)])
        for identity, label in (
            ("minute_1m_research_archive", "2026-01-02 09:31:00"),
            ("minute_1m_hotfix", "2026-01-02 09:32:00"),
            ("minute_5m_formal_archive", "2026-01-02 09:35:00"),
            ("minute_5m_hotfix", "2026-01-02 09:40:00"),
        ):
            self._write_source(identity, [self._minute_row(label)])
        self._write_source(
            "intraday_bars",
            [self._minute_row("2026-01-02 09:33:00", bar_size="1m")],
        )
        valuation = {
            "stock_code": "000001",
            "pe_ttm": 1.0,
            "pb": 2.0,
            "ps_ttm": 3.0,
            "pcf_ttm": 4.0,
            "total_share": 5.0,
            "total_mv": 6.0,
        }
        for identity, day in (
            ("valuation_daily", "2026-01-02"),
            ("valuation_history_july_repair", "2026-01-05"),
            ("valuation_daily_delta", "2026-01-06"),
            ("valuation_history_hotfix", "2026-01-07"),
        ):
            self._write_source(identity, [{**valuation, "date": day}])
        for identity, day in (
            ("index_daily", "2026-01-02"),
            ("index_daily_delta", "2026-01-05"),
        ):
            self._write_source(
                identity,
                [
                    {
                        "index_code": "000001.SH",
                        "date": day,
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 1.0,
                        "amount": 1.0,
                    }
                ],
            )
        connection = self._connection()
        try:
            expected, audit = _expected_row_counts(connection, self.registry)
        finally:
            connection.close()
        self.assertEqual(
            expected,
            {
                "dwd_security_bar": 8,
                "dwd_security_valuation": 24,
                "dwd_market_index": 3,
                "dwd_index_bar": 2,
            },
        )
        self.assertTrue(
            all(value["conflicting_business_keys"] == 0 for value in audit.values())
        )

    def test_existing_candidate_identity_contract_is_deep_verified(self) -> None:
        candidate = self.root / "candidate"
        candidate.mkdir()
        partitions: dict[str, list[dict[str, object]]] = {}
        for table, schema in _schemas().items():
            path = candidate / f"payload/security_market_observation/{table}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_batches([], schema=schema), path)
            digest, rows = _partition_digest(path, schema)
            partitions[table] = [
                {
                    "path": path.relative_to(candidate).as_posix(),
                    "row_count": rows,
                    "semantic_digest": digest,
                    "schema_digest": _canonical_digest(str(schema)),
                    "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            ]
        identity = {
            "run_id": "fixture-run",
            "input_plan_digest": "sha256:" + "1" * 64,
            "model_sha256": "sha256:" + "2" * 64,
            "source_set_digest": "sha256:" + "3" * 64,
            "listed_company_semantic_digest": "sha256:" + "4" * 64,
            "listed_company_manifest_sha256": "sha256:" + "5" * 64,
            "trading_calendar_semantic_digest": "sha256:" + "6" * 64,
            "trading_calendar_manifest_sha256": "sha256:" + "7" * 64,
        }
        table_digests = {
            table: _table_digest(values) for table, values in partitions.items()
        }
        row_counts = {table: 0 for table in partitions}
        semantic = _canonical_digest({**identity, "table_digests": table_digests})
        checkpoint = {
            "schema_version": 1,
            "kind": "dwd_market_observation_checkpoint",
            **identity,
            "semantic_digest": semantic,
            "completed_tables": {
                table: {"table_digest": table_digests[table], "partitions": values}
                for table, values in partitions.items()
            },
        }
        receipt = {
            "schema_version": 1,
            "kind": "dwd_market_observation_build_receipt",
            "status": "built",
            **identity,
            "semantic_digest": semantic,
            "table_digests": table_digests,
            "row_counts": row_counts,
        }
        metadata = {
            "checkpoint.json": checkpoint,
            "receipts/market-observation-build.json": receipt,
            "reconciliation/source-row-reconciliation.json": {"schema_version": 1},
        }
        for relative, value in metadata.items():
            path = candidate / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        files = sorted(
            [state[0]["path"] for state in partitions.values()] + list(metadata)
        )
        manifest = {
            "schema_version": 1,
            "kind": "dwd_market_observation_candidate",
            "owned_tables": list(partitions),
            "row_counts": row_counts,
            "semantic_digest": semantic,
            "files": [
                {
                    "path": relative,
                    "sha256": "sha256:"
                    + hashlib.sha256((candidate / relative).read_bytes()).hexdigest(),
                    "size_bytes": (candidate / relative).stat().st_size,
                }
                for relative in files
            ],
        }
        manifest_path = candidate / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        (candidate / "READY").write_text(
            "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n",
            encoding="ascii",
        )
        result = _verify_existing_output(candidate, **identity)
        self.assertEqual(result.semantic_digest, semantic)
        self.assertEqual(result.row_counts, row_counts)


if __name__ == "__main__":
    unittest.main()
