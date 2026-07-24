import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

P1_STACK_AVAILABLE = all(
    importlib.util.find_spec(module) is not None for module in ("duckdb", "pyarrow")
)

if P1_STACK_AVAILABLE:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.identity_coverage import build_identity_coverage


@unittest.skipUnless(P1_STACK_AVAILABLE, "P1 optional dependencies are not installed")
class IdentityCoverageTests(unittest.TestCase):
    def test_reconciles_normalized_universe_without_changing_d2_or_s1_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = root / "normalized"
            revision.mkdir()
            bars = pa.Table.from_pylist(
                [
                    {
                        "security_id": "SSE-600001",
                        "stock_code": "600001",
                        "date": date(2020, 1, 2),
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.0,
                        "close": 10.5,
                        "volume": 100.0,
                        "amount": 1000.0,
                    },
                    {
                        "security_id": "SSE-600003",
                        "stock_code": "600003",
                        "date": date(2020, 1, 2),
                        "open": 10.0,
                        "high": 0.0,
                        "low": 0.0,
                        "close": 10.5,
                        "volume": 100.0,
                        "amount": 1000.0,
                    },
                ]
            )
            universe = pa.Table.from_pylist(
                [
                    {
                        "security_id": "SSE-600001",
                        "stock_code": "600001",
                        "exchange": "SSE",
                        "market_date": day,
                        "valid_from": date(2020, 1, 2),
                    }
                    for day in (date(2020, 1, 2), date(2020, 1, 3))
                ]
                + [
                    {
                        "security_id": "SSE-600002",
                        "stock_code": "600002",
                        "exchange": "SSE",
                        "market_date": date(2020, 1, 2),
                        "valid_from": date(2020, 1, 2),
                    },
                    {
                        "security_id": "SSE-600003",
                        "stock_code": "600003",
                        "exchange": "SSE",
                        "market_date": date(2020, 1, 2),
                        "valid_from": date(2020, 1, 2),
                    },
                ]
            )
            master = pa.Table.from_pylist(
                [
                    {
                        "stock_code": "600001",
                        "list_date": date(2010, 1, 1),
                        "delist_date": date(2020, 1, 3),
                    },
                    {
                        "stock_code": "600002",
                        "list_date": date(2011, 1, 1),
                        "delist_date": date(2020, 1, 2),
                    },
                    {
                        "stock_code": "600003",
                        "list_date": date(2012, 1, 1),
                        "delist_date": date(2020, 1, 2),
                    },
                ]
            )
            suspensions = pa.Table.from_pylist(
                [
                    {
                        "stock_code": "600001",
                        "suspend_start": date(2020, 1, 3),
                        "suspend_end": date(2020, 1, 3),
                        "knowledge_cutoff": date(2020, 1, 3),
                    }
                ]
            )
            boundaries = pa.Table.from_pylist(
                [
                    {
                        "stock_code": "600002",
                        "exchange": "SSE",
                        "market_date": date(2020, 1, 2),
                        "exception_type": "terminal_boundary",
                        "knowledge_cutoff": date(2020, 1, 3),
                    }
                ]
            )
            pq.write_table(bars, revision / "daily_ohlcv_none.parquet")
            pq.write_table(universe, revision / "daily_universe.parquet")
            pq.write_table(master, root / "ref_security_master.parquet")
            pq.write_table(suspensions, root / "ref_suspensions.parquet")
            pq.write_table(boundaries, root / "ref_delist_day_boundary_exceptions.parquet")

            coverage_path = root / "coverage.parquet"
            metadata_path = root / "coverage.json"
            report_path = root / "s1.json"
            result = build_identity_coverage(
                revision_root=revision,
                reference_root=root,
                knowledge_cutoff=date(2020, 1, 3),
                coverage_output=coverage_path,
                coverage_metadata_output=metadata_path,
                s1_report_output=report_path,
            )
            coverage = pq.read_table(coverage_path).to_pylist()
            report = json.loads(report_path.read_text(encoding="utf-8"))

        by_key = {(row["stock_code"], row["market_date"]): row for row in coverage}
        self.assertEqual(result.coverage_rows, 4)
        self.assertEqual(result.status_counts, {"missing": 1, "suspended": 1, "trading": 2})
        self.assertEqual(by_key[("600001", date(2020, 1, 3))]["coverage_status"], "suspended")
        self.assertFalse(by_key[("600001", date(2020, 1, 3))]["bar_present"])
        self.assertFalse(by_key[("600001", date(2020, 1, 3))]["is_executable"])
        self.assertEqual(by_key[("600003", date(2020, 1, 2))]["reason_code"], "invalid_bar")
        self.assertFalse(by_key[("600003", date(2020, 1, 2))]["data_valid"])
        self.assertEqual(report["summary"]["unexplained_missing_session_count"], 0)
        self.assertEqual(report["summary"]["terminal_boundary_session_count"], 1)
        self.assertEqual(report["summary"]["invalid_observed_session_count"], 1)
        self.assertEqual(
            next(record for record in report["records"] if record["stock_code"] == "600001")[
                "list_date"
            ],
            "2010-01-01",
        )
