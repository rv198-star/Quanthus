"""Date, source-byte and nullable-turnover gates for Tencent 1m recovery."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quanthus.providers.minute_tdx import (
    _tencent_daily_evidence,
    _tencent_one_minute_bars,
    _tencent_qualification_symbols,
    collect,
)
from quanthus.providers.tencent_m1_normalize import expected_times

HAS_DATA_LIBRARIES = bool(
    importlib.util.find_spec("pandas") and importlib.util.find_spec("pyarrow")
)


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
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(RuntimeError, "incomplete Tencent session"),
        ):
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
            self.assertEqual(
                gzip.decompress((Path(temporary) / "000001.json.gz").read_bytes()),
                _payload("2026-09-23"),
            )

    def test_qualification_prefers_non_round_lot_volume_per_market(self):
        daily = {
            "000001": {"volume": 20000, "tradestatus": "1"},
            "000002": {"volume": 20035, "tradestatus": "1"},
            "600000": {"volume": 30000, "tradestatus": "1"},
            "600001": {"volume": 30017, "tradestatus": "1"},
            "688001": {"volume": 73, "tradestatus": "1"},
        }
        self.assertEqual(
            _tencent_qualification_symbols(list(daily), daily),
            ("000002", "600001", "688001"),
        )

    def test_source_qualification_blocks_full_scan_before_partial_bars(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            daily = root / "daily.parquet"
            status = root / "status.parquet"
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "stock_code": "000001",
                            "market_date": "2026-09-23",
                            "volume": 24135.0,
                            "close": 10.0,
                        },
                        {
                            "stock_code": "600000",
                            "market_date": "2026-09-23",
                            "volume": 24117.0,
                            "close": 10.0,
                        },
                    ]
                ),
                daily,
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {"stock_code": code, "market_date": "2026-09-23", "tradestatus": "1"}
                        for code in ("000001", "600000")
                    ]
                ),
                status,
            )
            home = root / "minute"
            with (
                patch(
                    "quanthus.providers.minute_tdx._tencent_one_minute_bars",
                    side_effect=RuntimeError("Tencent/daily volume mismatch"),
                ) as fetch,
                self.assertRaisesRegex(RuntimeError, "source qualification failed"),
            ):
                collect(
                    home=home,
                    layer="research",
                    frequency="1m",
                    mode="incr",
                    symbols=["000001", "600000"],
                    max_pages=1,
                    sleep_seconds=0,
                    market_date="2026-09-23",
                    tencent_daily_path=daily,
                    tencent_status_path=status,
                )
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(
                list(json.loads((home / "checkpoint.json").read_text())["symbols"]), []
            )
            self.assertEqual(
                json.loads((home / "source_qualification.json").read_text())["failed_symbol"],
                "000001",
            )
            self.assertEqual(json.loads((home / "status.json").read_text())["state"], "failed")

    def test_sealed_market_evidence_requires_same_date_and_both_files(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            daily = root / "daily.parquet"
            status = root / "status.parquet"
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "stock_code": "000001",
                            "market_date": "2026-09-23",
                            "volume": 24100.0,
                            "close": 10.0,
                        }
                    ]
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
