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

    from quanthus.none_backfill import (
        NoneBackfillError,
        build_none_backfill_revision,
        write_none_current,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(P1_STACK_AVAILABLE, "P1 optional dependencies are not installed")
class NoneBackfillTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        parent = root / "parent"
        raw = root / "raw"
        parent.mkdir()
        (raw / "raw_shards").mkdir(parents=True)
        built_at = datetime(2026, 7, 25, tzinfo=timezone.utc)
        parent_rows = [
            {
                "security_id": "000001",
                "stock_code": "000001",
                "identity_exchange": "SZSE",
                "date": date(2020, 1, 2),
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
        ]
        pq.write_table(pa.Table.from_pylist(parent_rows), parent / "daily_ohlcv_none.parquet")
        for name in (
            "daily_universe.parquet",
            "daily_isST.parquet",
            "st_status_pit.parquet",
            "ref_security_code_validity.parquet",
        ):
            pq.write_table(pa.Table.from_pylist([{"value": name}]), parent / name)
        (parent / "META.json").write_text('{"revision":"parent"}\n', encoding="utf-8")
        parent_names = (
            "daily_ohlcv_none.parquet",
            "daily_universe.parquet",
            "daily_isST.parquet",
            "st_status_pit.parquet",
            "ref_security_code_validity.parquet",
        )
        (parent / "CHECKSUMS.sha256").write_text(
            "".join(f"{_sha256(parent / name)}  {name}\n" for name in parent_names),
            encoding="utf-8",
        )

        detection = root / "detection.parquet"
        detected_rows = [
            {
                "security_id": f"{value:06d}",
                "reason_code": "prior_close_missing_fail_closed",
            }
            for value in range(1, 277)
            for _ in range(2315 + (1 if value <= 69 else 0))
        ]
        self.assertEqual(len(detected_rows), 639009)
        pq.write_table(pa.Table.from_pylist(detected_rows), detection)
        scope_rows = [
            {
                "security_id": f"{value:06d}",
                "terminal": "fetched",
                "provider_error_code": "0",
            }
            for value in range(1, 277)
        ]
        pq.write_table(pa.Table.from_pylist(scope_rows), raw / "scope_ledger_276.parquet")
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
        for value in range(1, 277):
            code = f"{value:06d}"
            rows = (
                [
                    [
                        "2020-01-02",
                        f"sz.{code}",
                        "99",
                        "99",
                        "99",
                        "99",
                        "1",
                        "99",
                        "3",
                        "1",
                        "0",
                        "1",
                        "0",
                    ],
                    [
                        "2020-01-03",
                        f"sz.{code}",
                        "10",
                        "10",
                        "10",
                        "10",
                        "",
                        "",
                        "3",
                        "",
                        "0",
                        "0",
                        "0",
                    ],
                ]
                if value == 1
                else [
                    [
                        "2020-01-03",
                        f"sz.{code}",
                        "10",
                        "10",
                        "10",
                        "10",
                        "1",
                        "10",
                        "3",
                        "1",
                        "0",
                        "1",
                        "0",
                    ]
                ]
            )
            payload = {
                "security_id": code,
                "stock_code": code,
                "exchange": "SZSE",
                "bs_code": f"sz.{code}",
                "request_window": {"start": "2020-01-01", "end": "2020-01-03"},
                "provider": "baostock",
                "frequency": "d",
                "adjustflag": "3",
                "fields": fields,
                "meta": {
                    "request": {
                        "code": f"sz.{code}",
                        "adjustflag": "3",
                        "fetched_at_utc": "2026-07-25T00:00:00+00:00",
                    },
                    "error_code": "0",
                    "error_msg": "success",
                },
                "n_rows": len(rows),
                "rows": rows,
            }
            (raw / "raw_shards" / f"{code}.json").write_text(
                json.dumps(payload) + "\n", encoding="utf-8"
            )
        meta = {
            "revision": "raw-v1",
            "kind": "raw_none_acquisition_candidate",
            "promoted": False,
            "detection_revision": "detection-v1",
            "detection_parquet_sha256": _sha256(detection),
        }
        (raw / "META.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")
        (raw / "manifest.json").write_text("{}\n", encoding="utf-8")
        raw_names = ["scope_ledger_276.parquet", "META.json", "manifest.json"]
        raw_names.extend(f"raw_shards/{value:06d}.json" for value in range(1, 277))
        (raw / "CHECKSUMS.sha256").write_text(
            "".join(f"{_sha256(raw / name)}  {name}\n" for name in raw_names),
            encoding="utf-8",
        )
        return parent, raw, detection

    def test_fills_missing_keys_without_replacing_parent_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent, raw, detection = self._fixture(root)
            output = root / "child-v1"
            result = build_none_backfill_revision(
                parent=parent,
                raw_revision=raw,
                detection_file=detection,
                output=output,
                revision="child-v1",
            )
            rows = pq.read_table(output / "daily_ohlcv_none.parquet").to_pylist()
            pointer = root / "CURRENT.json"
            write_none_current(revision_dir=output, pointer=pointer)
            promoted = json.loads(pointer.read_text(encoding="utf-8"))

        self.assertEqual(result.raw_rows, 277)
        self.assertEqual(result.overlap_rows, 1)
        self.assertEqual(result.inserted_rows, 276)
        self.assertEqual(result.output_rows, 277)
        existing = next(row for row in rows if row["date"] == date(2020, 1, 2))
        suspended = next(
            row
            for row in rows
            if row["security_id"] == "000001" and row["date"] == date(2020, 1, 3)
        )
        self.assertEqual(existing["close"], 10.5)
        self.assertEqual(existing["source"], "parent-source")
        self.assertEqual(suspended["volume"], 0.0)
        self.assertEqual(suspended["amount"], 0.0)
        self.assertEqual(promoted["revision"], "child-v1")
        self.assertEqual(promoted["daily_sha256"], result.checksums["daily_ohlcv_none.parquet"])

    def test_rejects_a_changed_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent, raw, detection = self._fixture(root)
            shard = raw / "raw_shards" / "000001.json"
            shard.write_text(shard.read_text(encoding="utf-8") + " ", encoding="utf-8")

            with self.assertRaisesRegex(NoneBackfillError, "checksum mismatch"):
                build_none_backfill_revision(
                    parent=parent,
                    raw_revision=raw,
                    detection_file=detection,
                    output=root / "child",
                    revision="child-v1",
                )


if __name__ == "__main__":
    unittest.main()
