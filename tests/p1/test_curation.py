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

    from quanthus.curation import build_curated_daily
    from quanthus.universe import UniversePolicy


@unittest.skipUnless(P1_STACK_AVAILABLE, "P1 optional dependencies are not installed")
class CurationTests(unittest.TestCase):
    def test_build_filters_scope_and_preserves_invalid_sse_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bars = pa.Table.from_pylist(
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
                        "stock_code": "600001",
                        "date": date(2025, 1, 2),
                        "open": 10.0,
                        "high": 0.0,
                        "low": 0.0,
                        "close": 10.5,
                        "volume": 100.0,
                        "amount": 1000.0,
                    },
                    {
                        "stock_code": "689009",
                        "date": date(2025, 1, 2),
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.0,
                        "close": 10.5,
                        "volume": None,
                        "amount": 1000.0,
                    },
                    {
                        "stock_code": "430001",
                        "date": date(2025, 1, 2),
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.0,
                        "close": 10.5,
                        "volume": 100.0,
                        "amount": 1000.0,
                    },
                ]
            )
            master = pa.Table.from_pylist(
                [
                    {
                        "security_id": "SSE-600000",
                        "stock_code": "600000",
                        "exchange": "SSE",
                    },
                    {
                        "security_id": "SSE-600001",
                        "stock_code": "600001",
                        "exchange": "SSE",
                    },
                    {
                        "security_id": "689009",
                        "stock_code": "689009",
                        "exchange": "SSE",
                    },
                    {
                        "security_id": "BSE-430001",
                        "stock_code": "430001",
                        "exchange": "BSE",
                    },
                ]
            )
            manifest = pa.Table.from_pylist(
                [
                    {
                        "dataset": "daily_ohlcv_none",
                        "version": "none-v1",
                        "checksum": "sha256:test",
                        "adj_mode": "none",
                        "built_at": date(2025, 1, 3),
                    }
                ]
            )
            pq.write_table(bars, root / "daily_ohlcv_none.parquet")
            pq.write_table(master, root / "ref_security_master.parquet")
            pq.write_table(manifest, root / "dataset_manifest.parquet")
            policy = UniversePolicy(
                path=root / "policy.json",
                version="test-v1",
                allowed_exchanges=("SSE", "SZSE"),
                excluded_security_ids={"689009": "CDR"},
                out_of_scope_code_prefixes={},
                invalid_bar_handling="retain_with_data_valid_false",
            )

            result = build_curated_daily(root, root / "curated.parquet", policy)
            output = pq.read_table(root / "curated.parquet").to_pylist()

        self.assertEqual(result.row_count, 2)
        self.assertEqual(result.valid_row_count, 1)
        self.assertEqual([row["stock_code"] for row in output], ["600000", "600001"])
        self.assertEqual([row["data_valid"] for row in output], [True, False])


if __name__ == "__main__":
    unittest.main()
