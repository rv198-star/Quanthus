from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.pipelines.securities_warehouse.tradability_state import (
    _audit_outputs,
    _configure_connection,
    _daily_status_relation_query,
    _expected_tradability_rows,
    _normalize_output,
    _price_regime_internal_query,
    _schemas,
    _security_status_query,
    _state_resolved_query,
    _state_rule_candidates_query,
    _verify_model_contract,
)


class TradabilityStateQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = duckdb.connect()
        self.connection.execute(
            "CREATE OR REPLACE MACRO build_cutoff() AS TIMESTAMPTZ '2026-08-02T05:16:00+00:00'"
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_connection_uses_bounded_spill_friendly_settings(self) -> None:
        scratch = Path("/tmp/tradability-state-test-scratch")
        _configure_connection(self.connection, scratch)
        settings = self.connection.execute(
            """
            SELECT current_setting('threads'),
                   current_setting('preserve_insertion_order'),
                   current_setting('memory_limit'),
                   current_setting('temp_directory')
            """
        ).fetchone()
        self.assertEqual(settings, (1, False, "1.4 GiB", str(scratch)))

    def test_expected_rows_uses_only_listing_calendar_cardinality(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE ref_listing AS SELECT
              'listing-1' listing_id,'venue-1' venue_id,
              TIMESTAMPTZ '2026-01-01T00:00:00Z' effective_from,
              TIMESTAMPTZ '2026-01-03T00:00:00Z' effective_to
            """
        )
        self.connection.execute(
            """
            CREATE TABLE ref_calendar AS SELECT * FROM (VALUES
              ('venue-1','REGULAR_DAY',true,TIMESTAMPTZ '2026-01-02T01:30:00Z'),
              ('venue-1','REGULAR_DAY',true,TIMESTAMPTZ '2026-01-03T01:30:00Z'),
              ('venue-1','REGULAR_DAY',false,TIMESTAMPTZ '2026-01-02T01:30:00Z')
            ) t(venue_id,session_type,is_open,open_at)
            """
        )
        self.assertEqual(_expected_tradability_rows(self.connection), 1)

    def test_output_audit_is_partition_bounded_and_never_rebuilds_state_base(self) -> None:
        class Result:
            def __init__(self, *, rows: list[tuple[object, ...]] | None = None) -> None:
                self.rows = rows or []

            def fetchone(self) -> tuple[int]:
                return (0,)

            def fetchall(self) -> list[tuple[object, ...]]:
                return self.rows

        class AuditConnection:
            def __init__(self) -> None:
                self.queries: list[str] = []

            def execute(self, query: str) -> Result:
                self.queries.append(query)
                if "state_base" in query:
                    raise AssertionError("audit must not rebuild state_base")
                if "gap_reason,count(*)" in query:
                    return Result(rows=[("NO_SOURCE_OBSERVATION", 2)])
                return Result()

        tables = (
            "dwd_security_status_interval",
            "dwd_listing_status_interval",
            "dwd_price_limit_regime",
            "dwd_tradability_state",
            "dwd_observation_gap",
        )
        with tempfile.TemporaryDirectory() as raw_root:
            work = Path(raw_root)
            states = {}
            for table in tables:
                path = work / f"{table}.parquet"
                path.touch()
                states[table] = [
                    {
                        "path": path.relative_to(work).as_posix(),
                        "row_count": 7 if table == "dwd_tradability_state" else 2,
                    }
                ]
            connection = AuditConnection()
            audit = _audit_outputs(
                connection,
                work,
                states,
                expected_state_rows=7,
            )
        self.assertEqual(audit["expected_tradability_rows"], 7)
        self.assertEqual(audit["actual_tradability_rows"], 7)
        self.assertEqual(audit["gap_reason_counts"], {"NO_SOURCE_OBSERVATION": 2})
        self.assertEqual(len(connection.queries), 11)

    def test_normalizer_keeps_partition_values_in_path_only(self) -> None:
        schema = _schemas()["dwd_tradability_state"]
        raw_schema = pa.schema(
            [*schema, pa.field("observation_start_year", pa.string())]
        )
        timestamp = datetime(2026, 7, 31, tzinfo=timezone.utc)
        raw_table = pa.Table.from_pylist(
            [
                {
                    "record_source": "audit",
                    "source_record_id": "audit-1",
                    "source_revision": "v1",
                    "source_artifact_sha256": "sha256:source",
                    "knowledge_from": timestamp,
                    "observation_start": timestamp,
                    "listing_id": "listing-1",
                    "calendar_session_id": "session-1",
                    "is_tradable": True,
                    "can_buy": True,
                    "can_sell": True,
                    "reason_code": "REGULAR_TRADING",
                    "input_revision_digest": "sha256:input",
                    "observation_start_year": "2026",
                }
            ],
            schema=raw_schema,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw/observation_start_year=2026/observation_start_month=07"
            raw.mkdir(parents=True)
            pq.write_table(raw_table, raw / "part-0.parquet")
            final = root / "final"
            states = _normalize_output("dwd_tradability_state", root / "raw", final)
            output = (
                final
                / "observation_start_year=2026/observation_start_month=07/part-00000.parquet"
            )

            self.assertEqual(len(states), 1)
            self.assertTrue(output.is_file())
            self.assertTrue(pq.ParquetFile(output).schema_arrow.equals(schema))
            self.assertNotIn(
                "observation_start_year", pq.ParquetFile(output).schema_arrow.names
            )

    def _register(self, name: str, rows: list[dict[str, object]]) -> None:
        self.connection.register(name, pa.Table.from_pylist(rows))

    def test_daily_status_parent_delta_wins_over_hotfix(self) -> None:
        common = {
            "stock_code": "000001",
            "date": "2026-07-31",
            "isST": "0",
            "tradestatus": "1",
            "__dataset_id": "delta",
            "__relative_path": "delta.parquet",
            "__source_sha256": "sha256:delta",
        }
        self._register("src_daily_status_observation_delta", [common])
        self._register(
            "src_daily_status_observation_hotfix",
            [
                {
                    **common,
                    "market_date": "2026-07-31",
                    "isST": "1",
                    "__dataset_id": "hotfix",
                    "__relative_path": "hotfix.parquet",
                }
            ],
        )
        self._register(
            "src_st_status_observation_revision",
            [
                {
                    **common,
                    "code": "sz.000001",
                    "exchange": "SZSE",
                    "raw_shard": "raw.json",
                    "__dataset_id": "weekly",
                    "__relative_path": "weekly.parquet",
                }
            ],
        )
        row = self.connection.execute(_daily_status_relation_query()).fetchone()
        columns = [item[0] for item in self.connection.description]
        value = dict(zip(columns, row, strict=True))
        self.assertEqual(value["__dataset_id"], "delta")
        self.assertEqual(value["is_st"], "0")
        self.assertEqual(value["source_priority"], 30)

    def test_price_regime_preserves_scopes_and_optional_listing_override(self) -> None:
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._register(
            "ref_venue",
            [
                {
                    "venue_id": "venue-sz",
                    "mic_code": "XSHE",
                    "effective_from": at,
                    "effective_to": None,
                }
            ],
        )
        self._register(
            "ref_listing",
            [
                {
                    "listing_id": "listing-1",
                    "security_id": "security-1",
                    "listing_code": "300001",
                    "venue_id": "venue-sz",
                    "effective_from": at,
                    "effective_to": None,
                }
            ],
        )
        base = {
            "interval_id": "base",
            "stock_code": None,
            "exchange": "SZSE",
            "board": "ChiNext",
            "effective_start": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "effective_end": datetime(2026, 12, 31, tzinfo=timezone.utc),
            "upper_limit_pct": 20.0,
            "lower_limit_pct": 20.0,
            "listing_window_rule": None,
            "no_limit_days": 0.0,
            "rounding_rule": "round_to_tick_0.01",
            "rule_basis": "ChiNext",
            "priority": 60,
            "source_revision": "v1",
            "__dataset_id": "regime",
            "__relative_path": "regime.parquet",
            "__source_sha256": "sha256:regime",
        }
        self._register(
            "src_price_limit_regime",
            [
                base,
                {
                    **base,
                    "interval_id": "ipo",
                    "stock_code": "300001",
                    "rule_basis": "IPO_window",
                    "priority": 40,
                    "no_limit_days": 5.0,
                    "upper_limit_pct": None,
                    "lower_limit_pct": None,
                },
                {
                    **base,
                    "interval_id": "st-general",
                    "board": "ST_overlay",
                    "rule_basis": "ST",
                    "priority": 20,
                    "upper_limit_pct": 5.0,
                    "lower_limit_pct": 5.0,
                },
                {
                    **base,
                    "interval_id": "st-chinext",
                    "board": "ST_overlay_ChiNext",
                    "rule_basis": "ST",
                    "priority": 20,
                },
            ],
        )
        self.connection.execute(
            """
            CREATE VIEW code_listing_resolution AS
            SELECT c.listing_code AS source_code,l.*
            FROM (SELECT listing_code,security_id FROM ref_listing GROUP BY 1,2) c
            JOIN ref_listing l USING (security_id)
            """
        )
        rows = self.connection.execute(_price_regime_internal_query()).fetchall()
        columns = [item[0] for item in self.connection.description]
        by_id = {
            dict(zip(columns, row, strict=True))["price_limit_regime_id"]: dict(
                zip(columns, row, strict=True)
            )
            for row in rows
        }
        base_row = next(
            value
            for value in by_id.values()
            if value["source_stock_code"] is None and value["priority"] == 60
        )
        ipo_row = next(value for value in by_id.values() if value["source_stock_code"] == "300001")
        self.assertIsNone(base_row["listing_id"])
        self.assertEqual(base_row["board_code"], "ChiNext")
        self.assertEqual(ipo_row["listing_id"], "listing-1")
        self.assertEqual(ipo_row["ipo_phase"], "INITIAL_LISTING_WINDOW")
        self.assertEqual(ipo_row["regime_type"], "NO_LIMIT")
        generic_st = next(
            value
            for value in by_id.values()
            if value["security_status"] == "ST" and value["board_code"] is None
        )
        chinext_st = next(
            value
            for value in by_id.values()
            if value["security_status"] == "ST" and value["board_code"] == "ChiNext"
        )
        self.assertEqual(generic_st["priority"], 20)
        self.assertEqual(chinext_st["priority"], 19)

    def test_security_status_deduplicates_identical_canonical_facts(self) -> None:
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        source = {
            "stock_code": "000001",
            "exchange": "SZSE",
            "effective_start": at,
            "effective_end": datetime(2026, 12, 31, tzinfo=timezone.utc),
            "source_revision": "v1",
            "__relative_path": "status.parquet",
            "__source_sha256": "sha256:status",
            "status_label": "unknown",
            "is_st": 0,
            "confidence": "not_proven",
            "event_kind": "baseline",
        }
        self._register(
            "src_st_status_pit",
            [{**source, "interval_id": "a"}, {**source, "interval_id": "b"}],
        )
        self._register("ref_venue", [{"venue_id": "venue-sz", "mic_code": "XSHE"}])
        self._register(
            "code_listing_resolution",
            [
                {
                    "source_code": "000001",
                    "listing_code": "000001",
                    "security_id": "security-1",
                    "listing_id": "listing-1",
                    "venue_id": "venue-sz",
                    "effective_from": datetime(2025, 1, 1, tzinfo=timezone.utc),
                    "effective_to": None,
                }
            ],
        )
        self.connection.execute(
            """
            CREATE TABLE daily_status_mapped (
              mapping_rank INTEGER, security_id VARCHAR, status_date DATE,
              is_st VARCHAR, trade_status VARCHAR, __dataset_id VARCHAR,
              __relative_path VARCHAR, __source_sha256 VARCHAR,
              listing_effective_to TIMESTAMPTZ
            )
            """
        )
        rows = self.connection.execute(_security_status_query()).fetchall()
        columns = [item[0] for item in self.connection.description]
        self.assertEqual(len(rows), 1)
        value = dict(zip(columns, rows[0], strict=True))
        self.assertTrue(value["source_record_id"].endswith("#interval_id=a"))
        self.assertEqual(value["status_value"], "UNKNOWN")

    def test_rule_resolution_selects_lowest_priority_and_exposes_ties(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE state_base AS SELECT
              'listing-1' listing_id,'session-1' calendar_session_id,
              'security-1' security_id,'venue-sz' venue_id,'300001' listing_code,
              'ChiNext' board_code,DATE '2026-01-05' trade_date,
              TIMESTAMPTZ '2026-01-05T01:30:00Z' open_at,
              TIMESTAMPTZ '2026-01-05T07:00:00Z' close_at,
              2 listing_day_number,'ST' resolved_security_status,
              NULL::VARCHAR canonical_status_id,NULL::VARCHAR canonical_status_value,
              NULL::VARCHAR provider_is_st,NULL::VARCHAR provider_trade_status,
              NULL::VARCHAR provider_status_sha256,NULL::VARCHAR suspension_status_id,
              NULL::DECIMAL(38,10) observed_close,NULL::VARCHAR bar_revision,
              NULL::VARCHAR bar_sha256,10::DECIMAL(38,10) previous_close
            """
        )
        self.connection.execute(
            """
            CREATE TABLE price_regime_internal AS
            SELECT * FROM (VALUES
              ('base',60,NULL,NULL,NULL,20::DECIMAL(18,12),20::DECIMAL(18,12)),
              ('st-a',20,NULL,'ST',NULL,5::DECIMAL(18,12),5::DECIMAL(18,12)),
              ('st-b',20,NULL,'ST',NULL,5::DECIMAL(18,12),5::DECIMAL(18,12))
            ) t(price_limit_regime_id,priority,listing_id,security_status,ipo_phase,
                upper_limit_ratio,lower_limit_ratio)
            """
        )
        self.connection.execute(
            "ALTER TABLE price_regime_internal ADD COLUMN venue_id VARCHAR DEFAULT 'venue-sz'"
        )
        self.connection.execute("ALTER TABLE price_regime_internal ADD COLUMN board_code VARCHAR")
        self.connection.execute(
            "ALTER TABLE price_regime_internal ADD COLUMN effective_from "
            "TIMESTAMPTZ DEFAULT TIMESTAMPTZ '2020-01-01T00:00:00Z'"
        )
        self.connection.execute(
            "ALTER TABLE price_regime_internal ADD COLUMN effective_to TIMESTAMPTZ"
        )
        self.connection.execute(
            "ALTER TABLE price_regime_internal ADD COLUMN no_limit_days INTEGER DEFAULT 0"
        )
        self.connection.execute(
            "ALTER TABLE price_regime_internal ADD COLUMN regime_type "
            "VARCHAR DEFAULT 'PERCENT_LIMIT'"
        )
        self.connection.execute(
            "ALTER TABLE price_regime_internal ADD COLUMN reference_price_method "
            "VARCHAR DEFAULT 'PREVIOUS_CLOSE'"
        )
        self.connection.execute(
            "ALTER TABLE price_regime_internal ADD COLUMN source_revision VARCHAR DEFAULT 'v1'"
        )
        self.connection.execute(
            "ALTER TABLE price_regime_internal ADD COLUMN source_artifact_sha256 "
            "VARCHAR DEFAULT 'sha256:r'"
        )
        self.connection.execute("CREATE VIEW candidates AS " + _state_rule_candidates_query())
        tied = self.connection.execute(
            "SELECT count(*) FROM candidates WHERE priority=best_priority"
        ).fetchone()[0]
        self.assertEqual(tied, 2)
        self.connection.execute(
            "DELETE FROM price_regime_internal WHERE price_limit_regime_id='st-b'"
        )
        self.connection.execute(
            "CREATE VIEW state_rule_candidates AS " + _state_rule_candidates_query()
        )
        resolved = self.connection.execute(_state_resolved_query()).fetchone()
        columns = [item[0] for item in self.connection.description]
        self.assertEqual(dict(zip(columns, resolved, strict=True))["price_limit_regime_id"], "st-a")

    def test_physical_schemas_match_approved_model(self) -> None:
        model = json.loads(
            (Path(__file__).parents[2] / "config/pipelines/dwd-domain-model.v1.json").read_text()
        )
        _verify_model_contract(model)
        self.assertEqual(
            set(_schemas()),
            {
                "dwd_security_status_interval",
                "dwd_listing_status_interval",
                "dwd_price_limit_regime",
                "dwd_tradability_state",
                "dwd_observation_gap",
            },
        )
        self.assertNotIn(
            "observation_start_year", _schemas()["dwd_tradability_state"].names
        )
        self.assertNotIn(
            "observation_start_month", _schemas()["dwd_tradability_state"].names
        )
        self.assertNotIn(
            "affected_effective_at_year", _schemas()["dwd_observation_gap"].names
        )
        self.assertNotIn(
            "affected_effective_at_month", _schemas()["dwd_observation_gap"].names
        )


if __name__ == "__main__":
    unittest.main()
