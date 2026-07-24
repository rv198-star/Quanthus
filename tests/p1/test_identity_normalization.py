import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

P1_STACK_AVAILABLE = all(
    importlib.util.find_spec(module) is not None for module in ("duckdb", "pyarrow")
)

if P1_STACK_AVAILABLE:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quanthus.code_validity import build_code_validity_table, load_code_validity_mapping
    from quanthus.identity_normalization import build_identity_normalized_revision


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


@unittest.skipUnless(P1_STACK_AVAILABLE, "P1 optional dependencies are not installed")
class IdentityNormalizationTests(unittest.TestCase):
    def test_default_mapping_has_the_three_evidenced_non_overlapping_aliases(self) -> None:
        mapping = load_code_validity_mapping()

        actual = {
            (interval.security_id, interval.stock_code, interval.valid_from, interval.valid_to)
            for interval in mapping.intervals
        }

        self.assertEqual(
            actual,
            {
                ("SZSE-000022", "000022", date(1993, 5, 5), date(2018, 12, 26)),
                ("SZSE-000022", "001872", date(2018, 12, 27), None),
                ("SZSE-000043", "000043", date(1994, 9, 28), date(2019, 12, 16)),
                ("SZSE-000043", "001914", date(2019, 12, 17), None),
                ("SZSE-300114", "300114", date(2010, 8, 27), date(2025, 2, 17)),
                ("SZSE-300114", "302132", date(2025, 2, 18), None),
            },
        )

    def _write_mapping(self, root: Path) -> Path:
        path = root / "mapping.json"
        evidence = _digest("test-evidence")
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "mapping_version": "test-code-validity-v1",
                    "status": "frozen",
                    "source_revision": "test-evidence-v1",
                    "intervals": [
                        {
                            "security_id": "SSE-600001",
                            "stock_code": "600001",
                            "exchange": "SSE",
                            "valid_from": "2019-01-01",
                            "valid_to": "2020-01-02",
                            "source": "test",
                            "evidence_locator": "evidence/old.pdf",
                            "evidence_sha256": evidence,
                            "source_revision": "test-evidence-v1",
                            "knowledge_cutoff": "2019-01-01T00:00:00+00:00",
                        },
                        {
                            "security_id": "SSE-600001",
                            "stock_code": "600101",
                            "exchange": "SSE",
                            "valid_from": "2020-01-03",
                            "valid_to": None,
                            "source": "test",
                            "evidence_locator": "evidence/new.pdf",
                            "evidence_sha256": evidence,
                            "source_revision": "test-evidence-v1",
                            "knowledge_cutoff": "2020-01-02T23:59:59+00:00",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_crops_alias_history_and_rekeys_the_daily_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            mapping_path = self._write_mapping(root)
            master = pa.Table.from_pylist(
                [
                    {
                        "security_id": "SSE-600001",
                        "stock_code": "600001",
                        "exchange": "SSE",
                        "list_date": date(2019, 1, 1),
                        "delist_date": date(2020, 1, 2),
                        "built_at": datetime(2020, 1, 4, tzinfo=timezone.utc),
                    },
                    {
                        "security_id": "SSE-600101",
                        "stock_code": "600101",
                        "exchange": "SSE",
                        "list_date": date(2019, 1, 1),
                        "delist_date": None,
                        "built_at": datetime(2020, 1, 4, tzinfo=timezone.utc),
                    },
                ]
            )
            bars = pa.Table.from_pylist(
                [
                    {
                        "stock_code": code,
                        "date": day,
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.0,
                        "close": 10.5,
                        "volume": 100.0,
                        "amount": 1000.0,
                    }
                    for code in ("600001", "600101")
                    for day in (date(2020, 1, 2), date(2020, 1, 3))
                ]
            )
            calendar = pa.Table.from_pylist(
                [
                    {"exchange": "SSE", "trade_date": day, "is_open": 1}
                    for day in (date(2020, 1, 2), date(2020, 1, 3))
                ]
            )
            pq.write_table(master, root / "ref_security_master.parquet")
            pq.write_table(bars, root / "daily_ohlcv_none.parquet")
            pq.write_table(calendar, root / "ref_trading_calendar.parquet")
            validity = root / "ref_security_code_validity.parquet"
            build_code_validity_table(
                root=root,
                mapping=load_code_validity_mapping(mapping_path),
                output=validity,
            )

            candidate = root / "candidate"
            candidate.mkdir()
            daily_s2 = pa.Table.from_pylist(
                [
                    {
                        "date": day,
                        "code": f"sh.{code}",
                        "isST": "0",
                        "tradestatus": "1",
                        "stock_code": code,
                        "exchange": "SSE",
                        "raw_shard": f"raw_shards/{code}.json",
                    }
                    for code in ("600001", "600101")
                    for day in (date(2020, 1, 2), date(2020, 1, 3))
                ]
            )
            pq.write_table(daily_s2, candidate / "daily_isST.parquet")
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "stock_code": code,
                            "exchange": "SSE",
                            "status_label": "non_st_class",
                            "is_st_class": False,
                            "effective_start": date(2020, 1, 2),
                            "effective_end": date(2020, 1, 3),
                            "knowledge_cutoff": date(2020, 1, 3),
                            "source": "baostock.isST",
                            "source_revision": "parent",
                            "raw_shard": f"raw_shards/{code}.json",
                            "source_market_date_start": date(2020, 1, 2),
                        }
                        for code in ("600001", "600101")
                    ]
                ),
                candidate / "st_status_pit_candidate.parquet",
            )
            (candidate / "META.json").write_text("{}\n", encoding="utf-8")
            (candidate / "CHECKSUMS.sha256").write_text("", encoding="utf-8")

            output = root / "child"
            result = build_identity_normalized_revision(
                root=root,
                code_validity=validity,
                s2_candidate=candidate,
                output=output,
                revision="test-child-v1",
            )
            normalized_bars = pq.read_table(output / "daily_ohlcv_none.parquet").to_pylist()
            normalized_universe = pq.read_table(output / "daily_universe.parquet").to_pylist()
            normalized_s2 = pq.read_table(output / "daily_isST.parquet").to_pylist()

        self.assertEqual(result.none_row_count, 2)
        self.assertEqual(result.universe_row_count, 2)
        self.assertEqual(result.s2_daily_row_count, 2)
        self.assertEqual(result.s2_interval_row_count, 2)
        self.assertEqual(
            [(row["stock_code"], row["date"]) for row in normalized_bars],
            [("600001", date(2020, 1, 2)), ("600101", date(2020, 1, 3))],
        )
        self.assertEqual(
            {(row["security_id"], row["market_date"]) for row in normalized_universe},
            {("SSE-600001", date(2020, 1, 2)), ("SSE-600001", date(2020, 1, 3))},
        )
        self.assertEqual(
            {(row["security_id"], row["date"]) for row in normalized_s2},
            {("SSE-600001", date(2020, 1, 2)), ("SSE-600001", date(2020, 1, 3))},
        )


if __name__ == "__main__":
    unittest.main()
