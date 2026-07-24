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

    from quanthus.tradability import build_tradability_coverage
    from quanthus.universe import UniversePolicy


@unittest.skipUnless(P1_STACK_AVAILABLE, "P1 optional dependencies are not installed")
class TradabilityCoverageTests(unittest.TestCase):
    def test_coverage_classifies_lifecycle_suspension_missing_and_invalid_bar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tables = {
                "daily_ohlcv_none.parquet": pa.Table.from_pylist(
                    [
                        {
                            "stock_code": "600000",
                            "date": date(2025, 1, 2),
                            "open": 10.0,
                            "high": 11.0,
                            "low": 9.0,
                            "close": 10.5,
                            "volume": 100.0,
                            "amount": 1000.0,
                        },
                        {
                            "stock_code": "000001",
                            "date": date(2025, 1, 3),
                            "open": 10.0,
                            "high": 0.0,
                            "low": 0.0,
                            "close": 10.5,
                            "volume": 100.0,
                            "amount": 1000.0,
                        },
                    ]
                ),
                "ref_security_master.parquet": pa.Table.from_pylist(
                    [
                        {
                            "security_id": "SSE-600000",
                            "stock_code": "600000",
                            "exchange": "SSE",
                            "list_date": date(2025, 1, 2),
                            "delist_date": date(2025, 1, 3),
                        },
                        {
                            "security_id": "SZSE-000001",
                            "stock_code": "000001",
                            "exchange": "SZSE",
                            "list_date": date(2025, 1, 3),
                            "delist_date": None,
                        },
                        {
                            "security_id": "689009",
                            "stock_code": "689009",
                            "exchange": "SSE",
                            "list_date": date(2025, 1, 2),
                            "delist_date": None,
                        },
                    ]
                ),
                "ref_trading_calendar.parquet": pa.Table.from_pylist(
                    [
                        {"exchange": exchange, "trade_date": day, "is_open": 1}
                        for exchange in ("SSE", "SZSE")
                        for day in (date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 4))
                    ]
                ),
                "ref_suspensions.parquet": pa.Table.from_pylist(
                    [
                        {
                            "suspension_id": "s1",
                            "stock_code": "600000",
                            "suspend_start": date(2025, 1, 3),
                            "suspend_end": date(2025, 1, 3),
                            "knowledge_cutoff": date(2025, 1, 3),
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
                            "built_at": date(2025, 1, 4),
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
                excluded_security_ids={"689009": "CDR"},
                out_of_scope_code_prefixes={},
                invalid_bar_handling="retain_with_data_valid_false",
            )

            result = build_tradability_coverage(
                root,
                root / "coverage.parquet",
                policy,
                knowledge_cutoff=date(2025, 1, 3),
            )
            rows = pq.read_table(root / "coverage.parquet").to_pylist()

        by_key = {(row["stock_code"], row["market_date"]): row for row in rows}
        self.assertEqual(result.row_count, 6)
        self.assertEqual(
            result.status_counts,
            {
                "delisted": 1,
                "missing": 1,
                "not_listed": 1,
                "suspended": 1,
                "trading": 2,
            },
        )
        self.assertTrue(by_key[("600000", date(2025, 1, 2))]["data_valid"])
        self.assertEqual(by_key[("600000", date(2025, 1, 3))]["coverage_status"], "suspended")
        self.assertEqual(by_key[("600000", date(2025, 1, 4))]["coverage_status"], "delisted")
        self.assertEqual(by_key[("600000", date(2025, 1, 4))]["list_days"], 2)
        self.assertEqual(by_key[("000001", date(2025, 1, 2))]["coverage_status"], "not_listed")
        self.assertFalse(by_key[("000001", date(2025, 1, 3))]["data_valid"])
        self.assertEqual(by_key[("000001", date(2025, 1, 4))]["coverage_status"], "missing")

    def test_coverage_does_not_use_a_future_suspension_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bars = pa.Table.from_pylist(
                [],
                schema=pa.schema(
                    [
                        ("stock_code", pa.string()),
                        ("date", pa.date32()),
                        ("open", pa.float64()),
                        ("high", pa.float64()),
                        ("low", pa.float64()),
                        ("close", pa.float64()),
                        ("volume", pa.float64()),
                        ("amount", pa.float64()),
                    ]
                ),
            )
            pq.write_table(bars, root / "daily_ohlcv_none.parquet")
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "security_id": "SSE-600000",
                            "stock_code": "600000",
                            "exchange": "SSE",
                            "list_date": date(2025, 1, 2),
                            "delist_date": None,
                        }
                    ]
                ),
                root / "ref_security_master.parquet",
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [{"exchange": "SSE", "trade_date": date(2025, 1, 3), "is_open": 1}]
                ),
                root / "ref_trading_calendar.parquet",
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "suspension_id": "s1",
                            "stock_code": "600000",
                            "suspend_start": date(2025, 1, 3),
                            "suspend_end": date(2025, 1, 3),
                            "knowledge_cutoff": date(2025, 1, 4),
                        }
                    ]
                ),
                root / "ref_suspensions.parquet",
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "dataset": "daily_ohlcv_none",
                            "version": "none-v1",
                            "checksum": "sha256:test",
                            "adj_mode": "none",
                            "built_at": date(2025, 1, 4),
                        }
                    ]
                ),
                root / "dataset_manifest.parquet",
            )
            policy = UniversePolicy(
                path=root / "policy.json",
                version="test-v1",
                allowed_exchanges=("SSE", "SZSE"),
                excluded_security_ids={},
                out_of_scope_code_prefixes={},
                invalid_bar_handling="retain_with_data_valid_false",
            )

            build_tradability_coverage(
                root,
                root / "coverage.parquet",
                policy,
                knowledge_cutoff=date(2025, 1, 3),
            )
            row = pq.read_table(root / "coverage.parquet").to_pylist()[0]

        self.assertEqual(row["coverage_status"], "missing")
        self.assertEqual(row["reason_code"], "bar_missing_without_suspension")


if __name__ == "__main__":
    unittest.main()
