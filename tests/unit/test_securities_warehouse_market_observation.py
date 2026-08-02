from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse.market_observation import (
    MarketObservationBuildError,
    _audit_outputs,
    _bar_query,
    _canonical_digest,
    _copy_query,
    _create_scratch_directory,
    _expected_row_counts,
    _index_bar_query,
    _normalize_outputs,
    _partition_digest,
    _register_sources,
    _repair_july_2026_minute_labels,
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
            date(2026, 7, 23),
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

    def test_scratch_directory_uses_explicit_physical_root(self) -> None:
        scratch_root = self.root / "scratch"
        scratch_root.mkdir()
        scratch = _create_scratch_directory(
            self.root / "candidate", "build-identity", scratch_root
        )
        try:
            self.assertEqual(scratch.parent, scratch_root.resolve())
            self.assertTrue(scratch.name.endswith(".duckdb-tmp"))
        finally:
            shutil.rmtree(scratch)

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
            exceptions = _audit_outputs(
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
        self.assertEqual(exceptions, [])
        self.assertEqual(sum(state["row_count"] for state in states), 8)
        self.assertEqual(len(states), 3)
        self.assertEqual({row["price_basis"] for row in rows}, {"UNADJUSTED"})
        first = next(
            row
            for row in rows
            if row["interval_size"] == "1m"
            and row["source_record_id"].endswith("09:31:00")
        )
        start_utc = first["observation_start"].astimezone(timezone.utc)
        end_utc = first["observation_end"].astimezone(timezone.utc)
        self.assertEqual(start_utc.hour, 1)
        self.assertEqual(start_utc.minute, 30)
        self.assertEqual(end_utc.minute, 31)

    def test_bar_query_normalizes_mislabeled_midday_boundary(self) -> None:
        self._write_source("daily_ohlcv_unadjusted", [self._bar_row("2026-01-02")])
        self._write_source(
            "minute_1m_research_archive",
            [self._minute_row("2026-01-05 13:01:00", bar_size="1m")],
            name="standard-end-labeled",
        )
        self._write_source(
            "minute_1m_research_archive",
            [
                self._minute_row("2026-07-23 13:00:00", bar_size="1m"),
                self._minute_row("2026-07-23 13:01:00", bar_size="1m"),
                self._minute_row("2026-07-23 15:00:00", bar_size="1m"),
            ],
            name="midday-boundary-mislabeled",
        )
        connection = self._connection()
        try:
            rows = connection.execute(
                _bar_query(self.registry, self.listing, [self.calendar], self.cutoff)
            ).to_arrow_table().to_pylist()
        finally:
            connection.close()

        standard = next(row for row in rows if "2026-01-05" in row["source_record_id"])
        self.assertEqual(
            standard["observation_start"].astimezone(timezone.utc),
            datetime(2026, 1, 5, 5, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            standard["observation_end"].astimezone(timezone.utc),
            datetime(2026, 1, 5, 5, 1, tzinfo=timezone.utc),
        )
        partial = sorted(
            (row for row in rows if "2026-07-23" in row["source_record_id"]),
            key=lambda row: row["source_record_id"],
        )
        self.assertEqual(
            [row["source_record_id"].rsplit("&ts=", 1)[1] for row in partial],
            [
                "2026-07-23T13:00:00",
                "2026-07-23T13:01:00",
                "2026-07-23T15:00:00",
            ],
        )
        self.assertEqual(
            [row["observation_start"].astimezone(timezone.utc) for row in partial],
            [
                datetime(2026, 7, 23, 3, 29, tzinfo=timezone.utc),
                datetime(2026, 7, 23, 5, 0, tzinfo=timezone.utc),
                datetime(2026, 7, 23, 6, 59, tzinfo=timezone.utc),
            ],
        )
        self.assertEqual(
            [row["observation_end"].astimezone(timezone.utc) for row in partial],
            [
                datetime(2026, 7, 23, 3, 30, tzinfo=timezone.utc),
                datetime(2026, 7, 23, 5, 1, tzinfo=timezone.utc),
                datetime(2026, 7, 23, 7, 0, tzinfo=timezone.utc),
            ],
        )
        business_keys = {
            (row["listing_id"], row["observation_start"], row["interval_size"])
            for row in rows
        }
        self.assertEqual(len(business_keys), len(rows))

    def test_recovery_query_pushes_july_scope_into_1m_source_scan(self) -> None:
        self._write_source("daily_ohlcv_unadjusted", [self._bar_row("2026-01-02")])
        one_minute = self._write_source(
            "minute_1m_research_archive",
            [self._minute_row("2026-07-23 13:00:00", bar_size="1m")],
        )
        five_minute = self._write_source(
            "minute_5m_formal_archive",
            [self._minute_row("2026-07-23 13:05:00", bar_size="5m")],
        )

        query = _bar_query(
            self.registry,
            self.listing,
            [self.calendar],
            self.cutoff,
            minute_start="2026-07-01 00:00:00",
            minute_end="2026-08-01 00:00:00",
            minute_interval="1m",
        )

        self.assertIn(str(one_minute.resolve()), query)
        self.assertNotIn(str(five_minute.resolve()), query)
        self.assertIn("TIMESTAMP '2026-07-01 00:00:00'", query)
        self.assertIn("TIMESTAMP '2026-08-01 00:00:00'", query)

    def test_checkpoint_repair_rebuilds_only_legacy_july_1m_partition(self) -> None:
        self._write_source("daily_ohlcv_unadjusted", [self._bar_row("2026-01-02")])
        self._write_source(
            "minute_1m_research_archive",
            [
                self._minute_row("2026-07-23 13:00:00", bar_size="1m"),
                self._minute_row("2026-07-23 13:01:00", bar_size="1m"),
                self._minute_row("2026-07-23 15:00:00", bar_size="1m"),
            ],
        )
        connection = self._connection()
        query = _bar_query(
            self.registry,
            self.listing,
            [self.calendar],
            self.cutoff,
            minute_start="2026-07-01 00:00:00",
            minute_end="2026-08-01 00:00:00",
            minute_interval="1m",
        )
        corrected = [
            row
            for row in connection.execute(query).to_arrow_table().to_pylist()
            if row["interval_size"] == "1m"
        ]
        local_timezone = timezone(timedelta(hours=8))
        legacy = []
        for row in corrected:
            raw_label = datetime.fromisoformat(
                row["source_record_id"].rsplit("&ts=", 1)[1]
            ).replace(tzinfo=local_timezone)
            observation_start = raw_label.astimezone(timezone.utc)
            legacy.append(
                {
                    **row,
                    "observation_start": observation_start,
                    "observation_end": observation_start + timedelta(minutes=1),
                }
            )
        work = self.root / "work"
        path = (
            work
            / "payload/security_market_observation/dwd_security_bar"
            / "interval_size=1m/observation_start_year=2026/observation_start_month=07"
            / "part-00000.parquet"
        )
        path.parent.mkdir(parents=True)
        schema = _schemas()["dwd_security_bar"]
        pq.write_table(pa.Table.from_pylist(legacy, schema=schema), path)
        digest, rows = _partition_digest(path, schema)
        partitions = [
            {
                "path": path.relative_to(work).as_posix(),
                "row_count": rows,
                "semantic_digest": digest,
                "schema_digest": _canonical_digest(str(schema)),
                "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        ]
        try:
            repaired = _repair_july_2026_minute_labels(
                connection, work, partitions, query
            )
            self.assertIsNotNone(repaired)
            assert repaired is not None
            repaired_rows = pq.ParquetFile(path).read().to_pylist()
            self.assertEqual(
                [row["observation_end"].astimezone(timezone.utc) for row in repaired_rows],
                [
                    datetime(2026, 7, 23, 3, 30, tzinfo=timezone.utc),
                    datetime(2026, 7, 23, 5, 1, tzinfo=timezone.utc),
                    datetime(2026, 7, 23, 7, 0, tzinfo=timezone.utc),
                ],
            )
            self.assertEqual(
                [
                    row["source_record_id"].rsplit("&ts=", 1)[1]
                    for row in repaired_rows
                ],
                [
                    "2026-07-23T13:00:00",
                    "2026-07-23T13:01:00",
                    "2026-07-23T15:00:00",
                ],
            )
            self.assertIsNone(
                _repair_july_2026_minute_labels(connection, work, repaired, query)
            )
        finally:
            connection.close()

    def test_zero_trade_daily_invalid_ohlc_is_retained_as_quality_exception(self) -> None:
        self._write_source(
            "daily_ohlcv_unadjusted",
            [
                self._bar_row(
                    "2026-01-02",
                    open=51.82,
                    high=51.82,
                    low=51.82,
                    close=49.05,
                    volume=0.0,
                    amount=0.0,
                )
            ],
        )
        self._write_source(
            "minute_1m_research_archive",
            [self._minute_row("2026-01-02 09:31:00", bar_size="1m")],
        )
        connection = self._connection()
        try:
            query = _bar_query(
                self.registry, self.listing, [self.calendar], self.cutoff
            )
            work = self.root / "work"
            raw_root = work / "payload/security_market_observation/dwd_security_bar"
            _copy_query(
                connection,
                query,
                raw_root,
                ("interval_size", "observation_start_year", "observation_start_month"),
            )
            states = _normalize_outputs("dwd_security_bar", raw_root, work)
            exceptions = _audit_outputs(
                connection,
                work,
                {"dwd_security_bar": states},
                listing_path=self.listing,
                index_path=self.listing,
                calendar_paths=[self.calendar],
            )
        finally:
            connection.close()
        self.assertEqual(len(exceptions), 1)
        self.assertEqual(exceptions[0]["code"], "INVALID_OHLC_ZERO_TRADE_RETAINED")
        self.assertIn(
            "stock_code=000001&date=2026-01-02",
            exceptions[0]["source_record_id"],
        )

    def test_traded_daily_invalid_ohlc_still_fails(self) -> None:
        self._write_source(
            "daily_ohlcv_unadjusted",
            [self._bar_row("2026-01-02", low=11.0, close=10.5)],
        )
        self._write_source(
            "minute_1m_research_archive",
            [self._minute_row("2026-01-02 09:31:00", bar_size="1m")],
        )
        connection = self._connection()
        try:
            query = _bar_query(
                self.registry, self.listing, [self.calendar], self.cutoff
            )
            work = self.root / "work"
            raw_root = work / "payload/security_market_observation/dwd_security_bar"
            _copy_query(
                connection,
                query,
                raw_root,
                ("interval_size", "observation_start_year", "observation_start_month"),
            )
            states = _normalize_outputs("dwd_security_bar", raw_root, work)
            with self.assertRaisesRegex(
                MarketObservationBuildError, "unaccepted invalid bars"
            ):
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
            row = {**base, "date": day}
            if identity != "valuation_daily":
                row["security_id"] = None
            self._write_source(identity, [row])
        connection = self._connection()
        try:
            rows = connection.execute(
                _valuation_query(self.registry, self.listing, self.cutoff)
            ).to_arrow_table().to_pylist()
        finally:
            connection.close()
        self.assertEqual(len(rows), 24)
        self.assertEqual({row["security_id"] for row in rows}, {"security-1"})
        self.assertEqual({row["listing_id"] for row in rows}, {"listing-1"})
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

    def test_normalize_outputs_merges_multiple_files_in_one_partition(self) -> None:
        work = self.root / "work"
        partition = (
            work
            / "payload/security_market_observation/dwd_security_bar"
            / "interval_size=1m/observation_start_year=2026/observation_start_month=1"
        )
        partition.mkdir(parents=True)
        schema = _schemas()["dwd_security_bar"]
        raw_schema = schema.remove(schema.get_field_index("interval_size"))
        for number, minute in enumerate((31, 32)):
            row = {
                "record_source": "minute_1m_research_archive",
                "source_record_id": f"000001:2026-01-02T09:{minute}:00",
                "source_revision": "fixture",
                "source_artifact_sha256": "sha256:" + str(number) * 64,
                "knowledge_from": datetime.fromisoformat(self.cutoff),
                "knowledge_to": None,
                "observation_start": datetime(
                    2026, 1, 2, 1, minute - 1, tzinfo=timezone.utc
                ),
                "observation_end": datetime(
                    2026, 1, 2, 1, minute, tzinfo=timezone.utc
                ),
                "listing_id": "listing-1",
                "calendar_session_id": "session-2026-01-02",
                "price_basis": "UNADJUSTED",
                "currency": "CNY",
                "open": Decimal("10.0000000000"),
                "high": Decimal("11.0000000000"),
                "low": Decimal("9.0000000000"),
                "close": Decimal("10.5000000000"),
                "volume": Decimal("100.0000000000"),
                "turnover": Decimal("1000.0000000000"),
                "trade_count": None,
            }
            pq.write_table(
                pa.Table.from_pylist([row], schema=raw_schema),
                partition / f"part-{number}.parquet",
            )

        states = _normalize_outputs(
            "dwd_security_bar",
            work / "payload/security_market_observation/dwd_security_bar",
            work,
        )

        final = partition / "part-00000.parquet"
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["row_count"], 2)
        self.assertEqual(list(partition.glob("*.parquet")), [final])
        rows = pq.ParquetFile(final).read().to_pylist()
        self.assertEqual([row["interval_size"] for row in rows], ["1m", "1m"])
        self.assertEqual(
            [row["source_record_id"] for row in rows],
            [
                "000001:2026-01-02T09:31:00",
                "000001:2026-01-02T09:32:00",
            ],
        )

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
