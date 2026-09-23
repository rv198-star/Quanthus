"""Date, source-byte and nullable-turnover gates for Tencent 1m recovery."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from quanthus.providers.minute_tdx import _tencent_daily_evidence, _tencent_one_minute_bars
from quanthus.providers.tencent_m1_normalize import expected_times


HAS_DATA_LIBRARIES = bool(importlib.util.find_spec("pandas") and importlib.util.find_spec("pyarrow"))


class _Response:
    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.data


def _payload(market_date: str) -> bytes:
    day = market_date.replace("-", "")
    stamps = [f"{day}0930", *expected_times(market_date)]
    return json.dumps(
        {
            "code": 0,
            "data": {
                "sz000001": {
                    "m1": [[stamp, "10", "10", "10", "10", "1", {}, "0"] for stamp in stamps]
                }
            },
        }
    ).encode()


@unittest.skipUnless(HAS_DATA_LIBRARIES, "pandas and pyarrow required")
class TencentMinuteFallbackTests(unittest.TestCase):
    def test_same_date_normalizes_and_preserves_raw(self):
        raw = _payload("2026-09-23")
        with tempfile.TemporaryDirectory() as temporary:
            frame, meta = _tencent_one_minute_bars(
                "000001",
                "2026-09-23",
                {"volume": 24100, "close": 10, "tradestatus": "1"},
                Path(temporary),
                opener=lambda *_args, **_kwargs: _Response(raw),
            )
            self.assertEqual(len(frame), 240)
            self.assertEqual(frame.iloc[0]["volume"], 200)
            self.assertTrue(frame["amount"].isna().all())
            self.assertEqual(str(frame["amount"].dtype), "Float64")
            self.assertEqual(meta["market_date"], "2026-09-23")
            self.assertEqual(meta["source_raw_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(gzip.decompress(Path(meta["source_raw_path"]).read_bytes()), raw)

    def test_wrong_date_never_becomes_requested_date(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "incomplete Tencent session"):
                _tencent_one_minute_bars(
                    "000001",
                    "2026-09-23",
                    {"volume": 24100, "close": 10, "tradestatus": "1"},
                    Path(temporary),
                    opener=lambda *_args, **_kwargs: _Response(_payload("2026-09-22")),
                )

    def test_daily_volume_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "volume mismatch"):
                _tencent_one_minute_bars(
                    "000001",
                    "2026-09-23",
                    {"volume": 24000, "close": 10, "tradestatus": "1"},
                    Path(temporary),
                    opener=lambda *_args, **_kwargs: _Response(_payload("2026-09-23")),
                )

    def test_sealed_market_evidence_requires_same_date_and_both_files(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            daily = root / "daily.parquet"
            status = root / "status.parquet"
            pq.write_table(
                pa.Table.from_pylist(
                    [{"stock_code": "000001", "market_date": "2026-09-23", "volume": 24100.0, "close": 10.0}]
                ),
                daily,
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [{"stock_code": "000001", "market_date": "2026-09-23", "tradestatus": "1"}]
                ),
                status,
            )
            evidence = _tencent_daily_evidence(
                daily_path=daily,
                status_path=status,
                market_date="2026-09-23",
                symbols=["000001"],
            )
            self.assertEqual(evidence["000001"]["volume"], 24100)
            with self.assertRaisesRegex(ValueError, "wrong-date"):
                _tencent_daily_evidence(
                    daily_path=daily,
                    status_path=status,
                    market_date="2026-09-22",
                    symbols=["000001"],
                )


if __name__ == "__main__":
    unittest.main()
