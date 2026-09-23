import unittest

from quanthus.providers.tencent_m1_normalize import expected_times
from quanthus.providers.tencent_m1_seed import validate_symbol_rows


class TencentM1SeedTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {
                "stock_code": "000001",
                "ts": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[8:10]}:{stamp[10:]}:00",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 100.0 if index == 0 else 0.0,
                "amount": None,
            }
            for index, stamp in enumerate(expected_times())
        ]

    def validate(self):
        validate_symbol_rows("000001", self.rows, daily_volume=100, daily_close=10)

    def test_exact_session_nullable_amount(self):
        self.validate()

    def test_missing_minute_fails(self):
        self.rows.pop(10)
        with self.assertRaisesRegex(ValueError, "240-bar"):
            self.validate()

    def test_wrong_date_fails(self):
        self.rows[10]["ts"] = "2026-09-23 09:41:00"
        with self.assertRaisesRegex(ValueError, "240-bar"):
            self.validate()

    def test_estimated_amount_fails(self):
        self.rows[0]["amount"] = 1000.0
        with self.assertRaisesRegex(ValueError, "nullable amount"):
            self.validate()

    def test_daily_volume_mismatch_fails(self):
        self.rows[0]["volume"] = 99.0
        with self.assertRaisesRegex(ValueError, "daily volume"):
            self.validate()

    def test_invalid_ohlc_fails(self):
        self.rows[0]["high"] = 9.0
        with self.assertRaisesRegex(ValueError, "invalid OHLCV"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
