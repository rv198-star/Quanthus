import unittest
from datetime import date

from quanthus.adjustments import AdjustmentFactor, AdjustmentFactorError, AdjustmentFactorIndex


class AdjustmentFactorTests(unittest.TestCase):
    def test_lookup_never_uses_a_factor_known_after_replay_cutoff(self) -> None:
        index = AdjustmentFactorIndex(
            [
                AdjustmentFactor(
                    "600000",
                    date(2024, 1, 10),
                    1.1,
                    "v1",
                    date(2024, 1, 5),
                ),
                AdjustmentFactor(
                    "600000",
                    date(2024, 2, 10),
                    1.2,
                    "v1",
                    date(2024, 2, 20),
                ),
            ]
        )

        resolved = index.at("600000", date(2024, 2, 15), date(2024, 2, 15))

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.factor, 1.1)
        self.assertEqual(index.at("600000", date(2024, 2, 15), date(2024, 2, 1)).factor, 1.1)
        self.assertIsNone(index.at("600000", date(2024, 2, 15), date(2024, 1, 4)))
        correction = index.at("600000", date(2024, 2, 20), date(2024, 2, 20))
        self.assertIsNotNone(correction)
        assert correction is not None
        self.assertEqual(correction.factor, 1.2)

    def test_lookup_selects_the_latest_eligible_effective_date(self) -> None:
        index = AdjustmentFactorIndex(
            [
                AdjustmentFactor("000001", date(2024, 1, 10), 1.1, "v1", date(2024, 1, 8)),
                AdjustmentFactor("000001", date(2024, 2, 10), 1.3, "v1", date(2024, 2, 8)),
            ]
        )

        resolved = index.at("000001", date(2024, 2, 11), date(2024, 2, 11))

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.factor, 1.3)

    def test_invalid_or_duplicate_revisions_are_rejected(self) -> None:
        with self.assertRaisesRegex(AdjustmentFactorError, "positive"):
            AdjustmentFactor("000001", date(2024, 1, 10), 0.0, "v1", date(2024, 1, 8))

        factor = AdjustmentFactor("000001", date(2024, 1, 10), 1.1, "v1", date(2024, 1, 8))
        with self.assertRaisesRegex(AdjustmentFactorError, "duplicate"):
            AdjustmentFactorIndex([factor, factor])


if __name__ == "__main__":
    unittest.main()
