"""Candidate-only Tencent m1 normalization invariants."""

from __future__ import annotations

import json
import unittest

from quanthus.providers.tencent_m1_normalize import expected_times, normalize_symbol


def payload(stamps: list[str], *, code: str = "000001") -> bytes:
    rows = [[stamp, "10", "10", "10", "10", "1", {}, "0"] for stamp in stamps]
    return json.dumps({"code": 0, "data": {"sz" + code: {"m1": rows}}}).encode()


class TencentM1NormalizeTests(unittest.TestCase):
    def test_auction_merged_into_first_minute_without_volume_loss(self) -> None:
        stamps = ["202609220930", *expected_times()]
        rows, detail = normalize_symbol(
            "000001", payload(stamps), daily_volume=24100, daily_close=10
        )
        self.assertEqual(len(rows), 240)
        self.assertEqual(rows[0]["ts"], "2026-09-22 09:31:00")
        self.assertEqual(rows[0]["volume"], 200)
        self.assertEqual(sum(row["volume"] for row in rows), 24100)
        self.assertTrue(all(row["amount"] is None for row in rows))
        self.assertEqual(detail["source_rows"], 241)
        self.assertTrue(detail["opening_auction_merged"])

    def test_missing_provider_minute_is_explicit_zero_volume_candidate(self) -> None:
        absent = "202609221120"
        stamps = ["202609220930", *(stamp for stamp in expected_times() if stamp != absent)]
        rows, detail = normalize_symbol(
            "000001", payload(stamps), daily_volume=24000, daily_close=10
        )
        restored = [row for row in rows if row["ts"] == "2026-09-22 11:20:00"]
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["volume"], 0)
        self.assertEqual(detail["inferred_zero_volume_minutes"], [absent])
        self.assertEqual(sum(row["volume"] for row in rows), 24000)

    def test_volume_mismatch_blocks_candidate(self) -> None:
        with self.assertRaisesRegex(ValueError, "volume mismatch"):
            normalize_symbol(
                "000001",
                payload(["202609220930", *expected_times()]),
                daily_volume=24000,
                daily_close=10,
            )

    def test_duplicate_timestamp_blocks_candidate(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            normalize_symbol(
                "000001",
                payload(["202609220930", *expected_times(), "202609221500"]),
                daily_volume=24200,
                daily_close=10,
            )

    def test_star_market_volume_unit_is_one_share(self) -> None:
        rows = [[stamp, "10", "10", "10", "10", "1", {}, "0"] for stamp in ["202609220930", *expected_times()]]
        raw = json.dumps({"code": 0, "data": {"sh688981": {"m1": rows}}}).encode()
        normalized, detail = normalize_symbol(
            "688981", raw, daily_volume=241, daily_close=10
        )
        self.assertEqual(sum(row["volume"] for row in normalized), 241)
        self.assertEqual(detail["volume_multiplier"], 1)


if __name__ == "__main__":
    unittest.main()
