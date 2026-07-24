import importlib.util
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

    from quanthus.delisted_coverage import run_delisted_coverage
    from quanthus.universe import UniversePolicy


@unittest.skipUnless(P1_STACK_AVAILABLE, "P1 optional dependencies are not installed")
class DelistedCoverageTests(unittest.TestCase):
    def test_report_separates_observed_suspended_missing_and_outside_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tables = {
                "daily_ohlcv_none.parquet": pa.Table.from_pylist(
                    [
                        {
                            "stock_code": "600000",
                            "date": date(2011, 1, 4),
                            "open": 10.0,
                            "high": 11.0,
                            "low": 9.0,
                            "close": 10.5,
                            "volume": 100.0,
                            "amount": 1000.0,
                        },
                        {
                            "stock_code": "600001",
                            "date": date(2011, 1, 4),
                            "open": 10.0,
                            "high": 11.0,
                            "low": 9.0,
                            "close": 10.5,
                            "volume": 100.0,
                            "amount": 1000.0,
                        },
                        {
                            "stock_code": "600003",
                            "date": date(2011, 1, 6),
                            "open": 10.0,
                            "high": 11.0,
                            "low": 9.0,
                            "close": 10.5,
                            "volume": 100.0,
                            "amount": 1000.0,
                        },
                    ]
                ),
                "ref_security_master.parquet": pa.Table.from_pylist(
                    [
                        {
                            "security_id": "600000",
                            "stock_code": "600000",
                            "exchange": "SSE",
                            "list_date": date(2010, 1, 1),
                            "delist_date": date(2011, 1, 4),
                        },
                        {
                            "security_id": "600001",
                            "stock_code": "600001",
                            "exchange": "SSE",
                            "list_date": date(2011, 1, 4),
                            "delist_date": date(2011, 1, 6),
                        },
                        {
                            "security_id": "600002",
                            "stock_code": "600002",
                            "exchange": "SSE",
                            "list_date": date(2009, 1, 1),
                            "delist_date": date(2010, 12, 31),
                        },
                        {
                            "security_id": "600003",
                            "stock_code": "600003",
                            "exchange": "SSE",
                            "list_date": date(2010, 1, 1),
                            "delist_date": None,
                        },
                    ]
                ),
                "ref_trading_calendar.parquet": pa.Table.from_pylist(
                    [
                        {"exchange": "SSE", "trade_date": day, "is_open": 1}
                        for day in (date(2011, 1, 4), date(2011, 1, 5), date(2011, 1, 6))
                    ]
                ),
                "ref_suspensions.parquet": pa.Table.from_pylist(
                    [
                        {
                            "suspension_id": "s1",
                            "stock_code": "600001",
                            "suspend_start": date(2011, 1, 5),
                            "suspend_end": date(2011, 1, 5),
                            "knowledge_cutoff": date(2011, 1, 5),
                        }
                    ]
                ),
                "dataset_manifest.parquet": pa.Table.from_pylist(
                    [
                        {
                            "dataset": "daily_ohlcv_none",
                            "version": "none-v1",
                            "checksum": "sha256:test",
                            "adj_mode": "none",
                            "built_at": date(2011, 1, 6),
                        }
                    ]
                ),
            }
            for name, table in tables.items():
                pq.write_table(table, root / name)
            policy = UniversePolicy(
                path=root / "policy.json",
                version="test-v1",
                allowed_exchanges=("SSE", "SZSE"),
                excluded_security_ids={},
                out_of_scope_code_prefixes={},
                invalid_bar_handling="retain_with_data_valid_false",
            )

            report = run_delisted_coverage(
                root,
                date(2011, 1, 5),
                universe_policy=policy,
            )
            report_path = root / "s1-report.json"
            report.write_json(report_path)
            report_payload = report_path.read_text(encoding="utf-8")

        by_code = {record.stock_code: record for record in report.records}
        self.assertEqual(report.history_start, date(2011, 1, 4))
        self.assertEqual(report.history_end, date(2011, 1, 6))
        self.assertEqual(by_code["600000"].coverage_state, "complete")
        self.assertEqual(by_code["600001"].observed_session_count, 1)
        self.assertEqual(by_code["600001"].suspended_session_count, 1)
        self.assertEqual(by_code["600001"].unexplained_missing_session_count, 1)
        self.assertEqual(by_code["600001"].coverage_state, "incomplete")
        self.assertEqual(by_code["600002"].coverage_state, "outside_history_window")
        self.assertEqual(report.to_dict()["summary"]["unexplained_missing_session_count"], 1)
        self.assertIn('"reference_delisted_security_count": 3', report_payload)


if __name__ == "__main__":
    unittest.main()
