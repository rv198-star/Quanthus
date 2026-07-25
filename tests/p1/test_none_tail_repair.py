import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from quanthus.none_backfill import NoneBackfillError
from quanthus.none_tail_repair import build_none_tail_repair_revision


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NoneTailRepairTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        parent = root / "parent"
        raw = root / "raw"
        parent.mkdir()
        (raw / "raw_shards").mkdir(parents=True)
        built_at = datetime(2026, 7, 25, tzinfo=timezone.utc)
        parent_rows = []
        codes = ("000919", "600805", "600810")
        for code in codes:
            exchange = "SZSE" if code.startswith("0") else "SSE"
            parent_rows.append(
                {
                    "security_id": code,
                    "stock_code": code,
                    "identity_exchange": exchange,
                    "date": date(2019, 3, 26),
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "turnover_rate": 1.0,
                    "pct_change": 0.5,
                    "adj_mode": "none",
                    "adj_version": "parent",
                    "source": "parent-source",
                    "built_at": built_at,
                }
            )
        pq.write_table(pa.Table.from_pylist(parent_rows), parent / "daily_ohlcv_none.parquet")
        linked = (
            "daily_universe.parquet",
            "daily_isST.parquet",
            "st_status_pit.parquet",
            "ref_security_code_validity.parquet",
        )
        for name in linked:
            pq.write_table(pa.Table.from_pylist([{"value": name}]), parent / name)
        (parent / "META.json").write_text('{"revision":"parent"}\n', encoding="utf-8")
        parent_files = ("daily_ohlcv_none.parquet", *linked)
        (parent / "CHECKSUMS.sha256").write_text(
            "".join(f"{_sha256(parent / name)}  {name}\n" for name in parent_files),
            encoding="utf-8",
        )

        fields = [
            "date",
            "code",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "adjustflag",
            "turn",
            "pctChg",
            "tradestatus",
            "isST",
        ]
        raw_files = []
        for code in codes:
            exchange = "SZSE" if code.startswith("0") else "SSE"
            provider_code = f"sz.{code}" if exchange == "SZSE" else f"sh.{code}"
            prefix_row = [
                "2019-03-26",
                provider_code,
                "10",
                "11",
                "9",
                "10.5",
                "100",
                "1000",
                "3",
                "1",
                "0.5",
                "1",
                "0",
            ]
            tail_row = [
                "2026-07-24",
                provider_code,
                "10.5",
                "11.5",
                "10",
                "11",
                "120",
                "1300",
                "3",
                "1.1",
                "4.8",
                "1",
                "0",
            ]
            variants = {
                "window_2011_to_20190326": ([prefix_row], "2011-01-01", "2019-03-26"),
                "tail_20190327_to_20260724": ([tail_row], "2019-03-27", "2026-07-24"),
                "full_2011_to_20260724": ([prefix_row, tail_row], "2011-01-01", "2026-07-24"),
            }
            for label, (rows, start, end) in variants.items():
                payload = {
                    "security_id": code,
                    "bs_code": provider_code,
                    "exchange": exchange,
                    "label": label,
                    "fields": fields,
                    "meta": {
                        "request": {
                            "api": "query_history_k_data_plus",
                            "code": provider_code,
                            "fields": ",".join(fields),
                            "start_date": start,
                            "end_date": end,
                            "frequency": "d",
                            "adjustflag": "3",
                            "fetched_at_utc": "2026-07-25T00:00:00+00:00",
                        },
                        "error_code": "0",
                        "error_msg": "success",
                    },
                    "n_rows": len(rows),
                    "rows": rows,
                }
                relative = f"raw_shards/{code}_{label}.json"
                (raw / relative).write_text(json.dumps(payload) + "\n", encoding="utf-8")
                raw_files.append(relative)
        meta = {
            "revision": "raw-v1",
            "kind": "raw_none_tail_repair",
            "issue": 40,
            "promoted": False,
            "codes": list(codes),
            "tail_window": {"start": "2019-03-27", "end": "2026-07-24"},
            "source_contract": {
                "provider": "baostock",
                "api": "query_history_k_data_plus",
                "frequency": "d",
                "adjustflag": "3",
                "fields": ",".join(fields),
            },
            "counts": {"tail_rows_total": 3},
        }
        (raw / "META.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")
        (raw / "manifest.json").write_text("{}\n", encoding="utf-8")
        pq.write_table(
            pa.Table.from_pylist([{"security_id": code} for code in codes]),
            raw / "per_code_summary.parquet",
        )
        raw_files.extend(("META.json", "manifest.json", "per_code_summary.parquet"))
        (raw / "CHECKSUMS.sha256").write_text(
            "".join(f"{_sha256(raw / name)}  {name}\n" for name in raw_files),
            encoding="utf-8",
        )
        return parent, raw

    def test_appends_only_absent_tail_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent, raw = self._fixture(root)
            result = build_none_tail_repair_revision(
                parent=parent,
                raw_revision=raw,
                output=root / "child",
                revision="child",
            )
            rows = pq.read_table(result.output / "daily_ohlcv_none.parquet").to_pylist()

        self.assertEqual(result.raw_rows, 3)
        self.assertEqual(result.overlap_rows, 0)
        self.assertEqual(result.inserted_rows, 3)
        self.assertEqual(result.output_rows, 6)
        old = next(row for row in rows if row["date"] == date(2019, 3, 26))
        self.assertEqual(old["source"], "parent-source")

    def test_rejects_non_reconciling_full_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent, raw = self._fixture(root)
            full = raw / "raw_shards" / "000919_full_2011_to_20260724.json"
            payload = json.loads(full.read_text(encoding="utf-8"))
            payload["rows"] = payload["rows"][:1]
            payload["n_rows"] = 1
            full.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            ledger = raw / "CHECKSUMS.sha256"
            ledger.write_text(
                ledger.read_text(encoding="utf-8").replace(
                    next(line.split()[0] for line in ledger.read_text().splitlines() if full.name in line),
                    _sha256(full),
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(NoneBackfillError, "does not reconcile"):
                build_none_tail_repair_revision(
                    parent=parent,
                    raw_revision=raw,
                    output=root / "child",
                    revision="child",
                )


if __name__ == "__main__":
    unittest.main()
