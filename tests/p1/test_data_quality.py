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

    from quanthus.data_quality import CheckStatus, run_p1_data_audit


@unittest.skipUnless(P1_STACK_AVAILABLE, "P1 optional dependencies are not installed")
class DataQualityTests(unittest.TestCase):
    def _write_fixture(self, root: Path, *, invalid_bar: bool) -> None:
        high = 0.0 if invalid_bar else 10.5
        tables = {
            "daily_ohlcv.parquet": pa.Table.from_pylist(
                [
                    {
                        "stock_code": "600000",
                        "date": date(2025, 1, 2),
                        "open": 10.0,
                        "high": high,
                        "low": 9.5,
                        "close": 10.2,
                        "volume": 100.0,
                        "amount": 1000.0,
                    }
                ]
            ),
            "ref_security_master.parquet": pa.Table.from_pylist(
                [
                    {
                        "security_id": "SSE-600000",
                        "stock_code": "600000",
                        "exchange": "SSE",
                        "list_date": date(1999, 11, 10),
                        "delist_date": None,
                    }
                ]
            ),
            "ref_trading_calendar.parquet": pa.Table.from_pylist(
                [{"exchange": "SSE", "trade_date": date(2025, 1, 2), "is_open": 1}]
            ),
            "ref_corporate_actions.parquet": pa.Table.from_pylist([{"action_id": "a1"}]),
            "dataset_manifest.parquet": pa.Table.from_pylist(
                [
                    {
                        "dataset": "daily_ohlcv",
                        "row_count": 1,
                        "built_at": date(2025, 1, 3),
                    }
                ]
            ),
        }
        gap_schema = pa.schema(
            [
                ("dataset", pa.string()),
                ("stock_code", pa.string()),
                ("trade_date", pa.date32()),
                ("gap_type", pa.string()),
            ]
        )
        gaps = []
        if invalid_bar:
            gaps.append(
                {
                    "dataset": "daily_ohlcv",
                    "stock_code": "600000",
                    "trade_date": date(2025, 1, 2),
                    "gap_type": "invalid_ohlc",
                }
            )
        tables["qa_data_gaps.parquet"] = pa.Table.from_pylist(gaps, schema=gap_schema)
        for name, table in tables.items():
            pq.write_table(table, root / name)

    def test_valid_fixture_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_fixture(root, invalid_bar=False)
            report = run_p1_data_audit(root)

        self.assertFalse(report.has_failures)

    def test_invalid_ohlc_is_reported_and_reconciles_to_gap_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_fixture(root, invalid_bar=True)
            report = run_p1_data_audit(root)

        by_id = {check.check_id: check for check in report.checks}
        self.assertEqual(by_id["bars.ohlc_invariant"].status, CheckStatus.FAIL)
        self.assertEqual(by_id["bars.ohlc_invariant"].count, 1)
        self.assertEqual(by_id["data_gaps.invalid_ohlc_reconciled"].status, CheckStatus.PASS)

    def test_c1_c2_gate_requires_the_new_reference_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_fixture(root, invalid_bar=False)
            report = run_p1_data_audit(root, require_c1_c2=True)

        by_id = {check.check_id: check for check in report.checks}
        self.assertEqual(by_id["adjustment_factors.present"].status, CheckStatus.FAIL)
        self.assertEqual(by_id["suspensions.present"].status, CheckStatus.FAIL)

    def test_c1_rejects_a_factor_not_visible_by_its_effective_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_fixture(root, invalid_bar=False)
            factors = pa.Table.from_pylist(
                [
                    {
                        "stock_code": "600000",
                        "effective_date": date(2024, 1, 10),
                        "fore_adjust_factor": 1.0,
                        "back_adjust_factor": 1.0,
                        "adjust_factor": 1.0,
                        "adj_version": "v1",
                        "knowledge_cutoff": date(2024, 1, 8),
                    },
                    {
                        "stock_code": "600000",
                        "effective_date": date(2024, 2, 10),
                        "fore_adjust_factor": 1.1,
                        "back_adjust_factor": 1.1,
                        "adjust_factor": 1.1,
                        "adj_version": "v1",
                        "knowledge_cutoff": date(2024, 2, 20),
                    },
                ]
            )
            pq.write_table(factors, root / "ref_adjustment_factors.parquet")
            report = run_p1_data_audit(root, require_c1_c2=True)

        by_id = {check.check_id: check for check in report.checks}
        visibility = by_id["adjustment_factors.visible_by_effective_date"]
        self.assertEqual(visibility.status, CheckStatus.FAIL)
        self.assertEqual(visibility.count, 1)


if __name__ == "__main__":
    unittest.main()
