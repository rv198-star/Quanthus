"""Synthetic contract tests, including real Parquet when DuckDB is installed."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from quanthus.forward_validation.native_seed import (
    _PARTITIONS,
    KIND,
    P6BaseSeedBinding,
    P6SeedError,
    _hash,
    build_seed,
    verify_seed_artifact,
    verify_seed_source,
)
from quanthus.pipelines.securities_warehouse.native_base23_reader import NativeBase23Error

ROOT = Path(__file__).resolve().parents[2]
BINDING = ROOT / "config/forward-validation/seed/base23-offline.v1.json"


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")


def seal(root, counts):
    layout, summaries = {}, {}
    for table, partition in _PARTITIONS.items():
        relative = f"payload/{table}/{partition}/part-000000.parquet"
        file = root / relative
        layout[table] = {
            "files": [
                {
                    "table": table,
                    "partition": partition,
                    "path": relative,
                    "rows": counts[table],
                    "size_bytes": file.stat().st_size,
                    "sha256": _hash(file),
                }
            ],
            "expected_partition_rows": {partition: counts[table]},
        }
        summaries[table] = {partition: {"rows": counts[table]}}
    # Deliberately unreadable/unavailable protected input: no stat/read is allowed.
    layout["dwd_security_bar"]["files"].append(
        {
            "table": "dwd_security_bar",
            "partition": "interval_size=1d/observation_start_year=2025",
            "path": (
                "payload/dwd_security_bar/interval_size=1d/"
                "observation_start_year=2025/never-open.parquet"
            ),
            "rows": 1,
            "size_bytes": 42,
            "sha256": "sha256:" + "5" * 64,
        }
    )
    purpose = "p10_governance_baseline_with_disclosed_coverage"
    write(root / "MATERIALIZATION.json", {"kind": KIND, "purpose": purpose, "layout": layout})
    manifest = {
        "kind": KIND,
        "version": "2.3",
        "business_through": "2026-09-30",
        "business_timezone": "Asia/Shanghai",
        "purpose": purpose,
        "consumer_admission": "NOT_GRANTED",
        "content_digest": "sha256:" + "a" * 64,
        "materialization_sha256": _hash(root / "MATERIALIZATION.json"),
        "summaries": summaries,
    }
    write(root / "manifest.json", manifest)
    (root / "READY").write_text(_hash(root / "manifest.json") + "\n")
    config = json.loads(BINDING.read_text())
    config["source"].update({k: manifest[k] for k in ("content_digest", "materialization_sha256")})
    config["source"]["manifest_sha256"] = _hash(root / "manifest.json")
    return P6BaseSeedBinding.from_dict(config)


class SourceContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "source"
        for table, partition in _PARTITIONS.items():
            file = self.root / f"payload/{table}/{partition}/part-000000.parquet"
            file.parent.mkdir(parents=True)
            file.write_bytes(b"synthetic metadata fixture: " + table.encode())
        self.binding = seal(self.root, dict.fromkeys(_PARTITIONS, 1))

    def test_exact_source_subset_does_not_open_blind_files(self):
        manifest, paths, counts = verify_seed_source(self.root, self.binding)
        self.assertEqual(manifest["consumer_admission"], "NOT_GRANTED")
        self.assertEqual(set(paths), set(_PARTITIONS))
        self.assertEqual(sum(counts.values()), 6)
        self.assertFalse(any("2025" in str(p) for pp in paths.values() for p in pp))

    def test_native_month_leaf_partitions(self):
        layout = json.loads((self.root / "MATERIALIZATION.json").read_text())
        manifest = json.loads((self.root / "manifest.json").read_text())
        for table in ("dwd_security_bar", "dwd_tradability_state"):
            partition = _PARTITIONS[table]
            entry = layout["layout"][table]
            original = entry["files"].pop(0)
            old_file = self.root / original["path"]
            content = old_file.read_bytes()
            old_file.unlink()
            entry["expected_partition_rows"] = {}
            manifest["summaries"][table] = {}
            for month in (1, 9):
                leaf = f"{partition}/observation_start_month={month}"
                relative = f"payload/{table}/{leaf}/part-000000.parquet"
                file = self.root / relative
                file.parent.mkdir(parents=True)
                file.write_bytes(content)
                entry["files"].append(dict(original, path=relative, partition=leaf))
                entry["expected_partition_rows"][leaf] = 1
                manifest["summaries"][table][leaf] = {"rows": 1}

        def reseal():
            write(self.root / "MATERIALIZATION.json", layout)
            manifest["materialization_sha256"] = _hash(self.root / "MATERIALIZATION.json")
            write(self.root / "manifest.json", manifest)
            (self.root / "READY").write_text(_hash(self.root / "manifest.json") + "\n")
            raw = json.loads(BINDING.read_text())
            raw["source"].update(
                content_digest=manifest["content_digest"],
                materialization_sha256=manifest["materialization_sha256"],
                manifest_sha256=_hash(self.root / "manifest.json"),
            )
            return P6BaseSeedBinding.from_dict(raw)

        _, paths, counts = verify_seed_source(self.root, reseal())
        self.assertEqual(counts["dwd_security_bar"], 2)
        self.assertEqual(counts["dwd_tradability_state"], 2)
        self.assertEqual(len(paths["dwd_security_bar"]), 2)
        self.assertFalse(any("2025" in str(p) for pp in paths.values() for p in pp))
        entry = layout["layout"]["dwd_security_bar"]
        leaf = _PARTITIONS["dwd_security_bar"] + "/observation_start_month=9"
        entry["expected_partition_rows"][leaf] = 2
        with self.assertRaisesRegex(P6SeedError, "partition_count_mismatch"):
            verify_seed_source(self.root, reseal())
        entry["expected_partition_rows"][leaf] = 1
        manifest["summaries"]["dwd_security_bar"][leaf]["rows"] = 2
        with self.assertRaisesRegex(P6SeedError, "partition_count_mismatch"):
            verify_seed_source(self.root, reseal())
        manifest["summaries"]["dwd_security_bar"][leaf]["rows"] = 1
        entry["files"][-1]["partition"] = leaf.replace("month=9", "month=1")
        with self.assertRaisesRegex(P6SeedError, "member_mismatch"):
            verify_seed_source(self.root, reseal())

    def test_source_digest_and_ready_fail_closed(self):
        for name in ("READY", "manifest.json", "MATERIALIZATION.json"):
            with self.subTest(name=name):
                path = self.root / name
                before = path.read_bytes()
                path.write_bytes(before + b"tampered")
                with self.assertRaises((P6SeedError, NativeBase23Error)):
                    verify_seed_source(self.root, self.binding)
                path.write_bytes(before)

    def test_wrong_or_missing_payload_and_extra_member_rejected(self):
        file = next((self.root / "payload/dwd_listing").rglob("*.parquet"))
        before = file.read_bytes()
        file.write_bytes(b"broken")
        with self.assertRaisesRegex(P6SeedError, "member_mismatch"):
            verify_seed_source(self.root, self.binding)
        file.write_bytes(before)
        extra = file.with_name("unlisted.parquet")
        extra.write_bytes(b"extra")
        with self.assertRaisesRegex(P6SeedError, "physical_inventory"):
            verify_seed_source(self.root, self.binding)
        extra.unlink()
        file.unlink()
        with self.assertRaises(NativeBase23Error):
            verify_seed_source(self.root, self.binding)

    def test_symlink_rejected(self):
        file = next((self.root / "payload/dwd_listing").rglob("*.parquet"))
        outside = self.root.parent / "outside"
        outside.write_bytes(file.read_bytes())
        file.unlink()
        file.symlink_to(outside)
        with self.assertRaises(NativeBase23Error):
            verify_seed_source(self.root, self.binding)

    def test_unknown_version_protected_scope_and_grant_rejected(self):
        cases = [
            ("source", "version", "2.2"),
            ("scope", "history_start", "2025-12-31"),
            ("scope", "history_end", "2026-10-01"),
            ("scope", "price_basis", "none"),
            ("scope", "minimum_open_sessions", 1),
            ("scope", "venues", ["BSE"]),
            ("authority", "official_cycle_authorized", True),
        ]
        for group, key, value in cases:
            with self.subTest(key=key):
                raw = json.loads(BINDING.read_text())
                raw[group][key] = value
                with self.assertRaises(P6SeedError):
                    P6BaseSeedBinding.from_dict(raw)

    def test_binding_mutation_rejected(self):
        self.binding.raw["scope"]["history_start"] = "2025-01-01"
        with self.assertRaisesRegex(P6SeedError, "binding_mutated"):
            verify_seed_source(self.root, self.binding)

    def test_outputs_cannot_overlap_source_or_replace_existing(self):
        with self.assertRaisesRegex(P6SeedError, "overlaps_source"):
            build_seed(
                root=self.root,
                binding=self.binding,
                output=self.root / "out",
                code_revision="b" * 40,
            )
        out = self.root.parent / "existing"
        out.mkdir()
        with self.assertRaisesRegex(P6SeedError, "output_exists"):
            build_seed(root=self.root, binding=self.binding, output=out, code_revision="b" * 40)


@unittest.skipUnless(
    importlib.util.find_spec("duckdb"), "DuckDB unavailable; Parquet tests not run"
)
class ParquetConstruction(unittest.TestCase):
    def setUp(self):
        import duckdb

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "source"
        con = duckdb.connect(":memory:")
        self.addCleanup(con.close)
        self.con = con
        lineage = "'sha256:" + "c" * 64 + "' AS source_artifact_sha256"
        con.execute("""
            CREATE TABLE dates AS SELECT CAST(d AS DATE) AS d,
              timezone('Asia/Shanghai', CAST(d AS DATE)+TIME '15:00:00') AS close_at
            FROM generate_series(DATE '2026-01-01', DATE '2026-09-30', INTERVAL 1 DAY) t(d)
            WHERE isodow(d)<6;
        """)
        venue = "venue_bec59e31eb6bb28111a10d7e6d2c689f"
        queries = {
            "dwd_trading_calendar": (
                f"SELECT '{venue}' AS venue_id, CAST(d AS VARCHAR) AS calendar_session_id, "
                "d AS trade_date, true AS is_open, close_at-INTERVAL 1 DAY AS knowledge_from, "
                f"NULL::TIMESTAMPTZ AS knowledge_to, {lineage} FROM dates"
            ),
            "dwd_security_bar": (
                "SELECT 'l1' AS listing_id, CAST(d AS VARCHAR) AS calendar_session_id, "
                "close_at-INTERVAL 5 HOUR AS observation_start, '1d' AS interval_size, "
                "'UNADJUSTED' AS price_basis, 10.00::DECIMAL(18,4) AS open, "
                "11.00::DECIMAL(18,4) AS high, 9.00::DECIMAL(18,4) AS low, "
                "10.50::DECIMAL(18,4) AS close, 10000::BIGINT AS volume, "
                "105000.0::DECIMAL(24,4) AS turnover, "
                "close_at-INTERVAL 1 SECOND AS knowledge_from, "
                "NULL::TIMESTAMPTZ AS knowledge_to, CAST(d AS VARCHAR) AS source_record_id, "
                f"'r1' AS source_revision, {lineage} FROM dates"
            ),
            "dwd_tradability_state": (
                "SELECT 'l1' AS listing_id, CAST(d AS VARCHAR) AS calendar_session_id, "
                "close_at-INTERVAL 5 HOUR AS observation_start, true AS is_tradable, "
                "true AS can_buy, true AS can_sell, close_at-INTERVAL 1 SECOND AS knowledge_from, "
                f"NULL::TIMESTAMPTZ AS knowledge_to, {lineage} FROM dates"
            ),
            "dwd_listing": (
                f"SELECT 'l1' AS listing_id, 's1' AS security_id, '{venue}' AS venue_id, "
                "'600001' AS listing_code, TIMESTAMPTZ '2025-12-01 00:00:00+08' AS effective_from, "
                "NULL::TIMESTAMPTZ AS effective_to, "
                "TIMESTAMPTZ '2025-12-01 00:00:00+08' AS knowledge_from, "
                f"NULL::TIMESTAMPTZ AS knowledge_to, {lineage}"
            ),
            "dwd_security": (
                "SELECT 's1' AS security_id, 'co1' AS company_id, 'A_SHARE' AS instrument_type, "
                "TIMESTAMPTZ '2025-12-01 00:00:00+08' AS effective_from, "
                "NULL::TIMESTAMPTZ AS effective_to, "
                "TIMESTAMPTZ '2025-12-01 00:00:00+08' AS knowledge_from, "
                f"NULL::TIMESTAMPTZ AS knowledge_to, {lineage}"
            ),
            "dwd_adjustment_factor": (
                "SELECT 's1' AS security_id, "
                "TIMESTAMPTZ '2026-06-01 00:00:00+08' AS effective_from, "
                "NULL::TIMESTAMPTZ AS effective_to, 1.0::DECIMAL(18,8) AS factor_value, "
                "TIMESTAMPTZ '2026-05-31 00:00:00+08' AS knowledge_from, "
                f"NULL::TIMESTAMPTZ AS knowledge_to, {lineage} WHERE false"
            ),
        }
        for table, query in queries.items():
            con.execute(f"CREATE TABLE {table} AS {query}")
        self.reseal()

    def reseal(self):
        counts = {}
        for table, partition in _PARTITIONS.items():
            file = self.root / f"payload/{table}/{partition}/part-000000.parquet"
            file.parent.mkdir(parents=True, exist_ok=True)
            file.unlink(missing_ok=True)
            self.con.execute(f"COPY {table} TO '{file}' (FORMAT PARQUET)")
            counts[table] = self.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        self.binding = seal(self.root, counts)

    def build(self, name="out"):
        return build_seed(
            root=self.root,
            binding=self.binding,
            output=self.root.parent / name,
            code_revision="b" * 40,
        )

    def test_construct_cold_reproduce_and_verify_real_parquet(self):
        from quanthus.concentration import StaticIndustryMembership
        from quanthus.event_library.physical_scan import (
            _CANDIDATE_SCAN_COLUMNS,
            iter_projected_candidate_series,
        )
        from quanthus.forward_validation.prospective_source import P6ProspectiveVisibility
        from quanthus.industry_mapping import IndustryMappingSource, StaticIndustryMapping

        first, second = self.build(), self.build("cold")
        self.assertEqual(first["content_digest"], second["content_digest"])
        self.assertTrue(first["diagnostics"]["input_validation_passed"])
        self.assertFalse(first["consumer_authorized"])
        self.assertEqual(first["official_observations_added"], 0)
        file = self.root.parent / "out/candidate-daily.parquet"
        columns = {
            r[0]
            for r in self.con.execute(f"DESCRIBE SELECT * FROM read_parquet('{file}')").fetchall()
        }
        self.assertTrue(set(_CANDIDATE_SCAN_COLUMNS) <= columns)
        price, observed, cutoff = self.con.execute(
            "SELECT close_price,dwd_bar_knowledge_from,knowledge_cutoff "
            f"FROM read_parquet('{file}') LIMIT 1"
        ).fetchone()
        self.assertEqual(str(price), "10.5000")
        self.assertLess(observed, cutoff)
        # Execute the existing P6 consumer over actual synthetic Parquet, not just
        # a column-name comparison. No activation/official date is manufactured.
        industry = StaticIndustryMapping(
            path=self.root.parent / "synthetic-industry.json",
            schema_version="1.0.0",
            mapping_version="synthetic",
            artifact_digest="sha256:" + "a" * 64,
            source=IndustryMappingSource(
                "synthetic", "fixture", datetime(2026, 1, 1, tzinfo=timezone.utc), "r1"
            ),
            memberships=(
                StaticIndustryMembership("s1", "industry1", "synthetic", "r1", "fixture"),
            ),
        )
        series = tuple(
            iter_projected_candidate_series(
                SimpleNamespace(candidate_path=file),
                source_policy=SimpleNamespace(
                    historical_visibility=P6ProspectiveVisibility(),
                    semantic_digest=first["content_digest"],
                ),
                industry_mapping=industry,
            )
        )
        self.assertEqual(len(series), 1)
        self.assertEqual(len(series[0]), first["diagnostics"]["rows"])
        self.assertEqual(series[0][0].close_price, price)
        self.assertEqual(
            verify_seed_artifact(file.parent)["content_digest"], first["content_digest"]
        )
        file.write_bytes(b"corruption")
        with self.assertRaisesRegex(P6SeedError, "file_mismatch"):
            verify_seed_artifact(file.parent)

    def test_late_knowledge_is_missing_not_retimed(self):
        self.con.execute(
            "UPDATE dwd_security_bar SET knowledge_from=knowledge_from+INTERVAL 1 DAY "
            "WHERE calendar_session_id='2026-09-30'"
        )
        self.reseal()
        result = self.build()
        self.assertFalse(result["diagnostics"]["input_validation_passed"])
        self.assertEqual(result["diagnostics"]["reason_counts"]["bar_missing_or_not_visible"], 1)
        self.assertFalse((self.root.parent / "out/OFFLINE_VALIDATED").exists())

    def test_asof_selects_visible_old_version_not_latest(self):
        self.con.execute(
            "CREATE TABLE old AS SELECT * FROM dwd_security_bar "
            "WHERE calendar_session_id='2026-09-30'"
        )
        self.con.execute(
            "UPDATE dwd_security_bar SET knowledge_to=knowledge_from+INTERVAL 1 DAY "
            "WHERE calendar_session_id='2026-09-30'"
        )
        self.con.execute(
            "INSERT INTO dwd_security_bar SELECT * REPLACE (knowledge_from+INTERVAL 1 "
            "DAY AS knowledge_from, 20::DECIMAL(18,4) AS close) FROM old"
        )
        self.reseal()
        result = self.build()
        self.assertTrue(result["diagnostics"]["input_validation_passed"])

    def test_duplicate_identity_or_bar_blocks_validation(self):
        self.con.execute("INSERT INTO dwd_security_bar SELECT * FROM dwd_security_bar LIMIT 1")
        self.reseal()
        self.assertEqual(
            self.build()["diagnostics"]["reason_counts"]["duplicate_listing_session"], 1
        )

    def test_wrong_price_basis_rejected_before_artifact_publication(self):
        self.con.execute("UPDATE dwd_security_bar SET price_basis='FORWARD_ADJUSTED'")
        self.reseal()
        with self.assertRaisesRegex(P6SeedError, "price_basis"):
            self.build()
        self.assertFalse((self.root.parent / "out").exists())

    def test_missing_whole_open_session_cannot_disappear(self):
        for table in ("dwd_security_bar", "dwd_tradability_state"):
            self.con.execute(f"DELETE FROM {table} WHERE calendar_session_id='2026-06-01'")
        self.reseal()
        result = self.build()
        self.assertEqual(result["diagnostics"]["reason_counts"]["open_session_without_seed"], 1)
        self.assertFalse(result["diagnostics"]["input_validation_passed"])


if __name__ == "__main__":
    unittest.main()
