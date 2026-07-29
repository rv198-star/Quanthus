import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quanthus.backtest import (
    BenchmarkCapitalWeight,
    BenchmarkCurve,
    BenchmarkCurveError,
    BenchmarkObservation,
    load_benchmark_curve,
)


class BenchmarkCurveTests(unittest.TestCase):
    def curve(self) -> BenchmarkCurve:
        return BenchmarkCurve(
            benchmark_id="csi300",
            instrument_id="sh000300",
            display_name="CSI 300",
            currency="CNY",
            price_field="close",
            source="synthetic",
            source_revision="synthetic-csi300-v1",
            acquired_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
            observations=(
                BenchmarkObservation(date(2020, 1, 2), Decimal("4000")),
                BenchmarkObservation(date(2020, 1, 3), Decimal("4040")),
                BenchmarkObservation(date(2020, 1, 6), Decimal("4400")),
            ),
        )

    def test_round_trip_digest_and_comparison(self) -> None:
        curve = self.curve()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "csi300.json"
            path.write_text(json.dumps(curve.to_dict()), encoding="utf-8")
            loaded = load_benchmark_curve(path)

        self.assertEqual(loaded, curve)
        comparison = loaded.compare((date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)))
        self.assertEqual(comparison.total_return, Decimal("0.10000000"))
        self.assertEqual(comparison.observation_count, 3)

        capital_matched = loaded.compare_capital_path(
            (
                BenchmarkCapitalWeight(date(2020, 1, 2), Decimal("0")),
                BenchmarkCapitalWeight(date(2020, 1, 3), Decimal("0.5")),
                BenchmarkCapitalWeight(date(2020, 1, 6), Decimal("0.5")),
            )
        )
        self.assertEqual(capital_matched.total_return, Decimal("0.04455446"))
        self.assertEqual(capital_matched.average_invested_ratio, Decimal("0.50000000"))
        self.assertTrue(capital_matched.capital_path_digest.startswith("sha256:"))

    def test_tamper_and_missing_replay_date_fail_closed(self) -> None:
        payload = self.curve().to_dict()
        payload["observations"][0]["close"] = "3999"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tampered.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(BenchmarkCurveError, "digest mismatch"):
                load_benchmark_curve(path)

        with self.assertRaisesRegex(BenchmarkCurveError, "missing 1 replay dates"):
            self.curve().compare((date(2020, 1, 2), date(2020, 1, 7)))

        with self.assertRaisesRegex(BenchmarkCurveError, "capital weight must be in"):
            BenchmarkCapitalWeight(date(2020, 1, 2), Decimal("1.01"))

    def test_ambiguous_or_wrongly_ordered_identity_data_is_rejected(self) -> None:
        with self.assertRaisesRegex(BenchmarkCurveError, "date-ascending"):
            BenchmarkCurve(
                benchmark_id="csi300",
                instrument_id="sh000300",
                display_name="CSI 300",
                currency="CNY",
                price_field="close",
                source="synthetic",
                source_revision="synthetic-csi300-v1",
                acquired_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
                observations=tuple(reversed(self.curve().observations)),
            )


if __name__ == "__main__":
    unittest.main()
