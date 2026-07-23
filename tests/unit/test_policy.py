import copy
import unittest

from quanthus.policy import ConfigurationError, load_policy, validate_policy


class PolicyTests(unittest.TestCase):
    def test_default_policy_loads_and_has_stable_digest(self) -> None:
        policy = load_policy()

        self.assertEqual(policy.version, "p0-2026-07-23")
        self.assertEqual(len(policy.digest), 64)
        self.assertEqual(policy.digest, load_policy().digest)

    def test_missing_data_cannot_be_structural_break(self) -> None:
        raw = copy.deepcopy(load_policy().raw)
        raw["data"]["missing_data_policy"]["structural_break"] = True

        with self.assertRaisesRegex(ConfigurationError, "structural break"):
            validate_policy(raw)

    def test_validation_ranges_cannot_overlap(self) -> None:
        raw = copy.deepcopy(load_policy().raw)
        raw["validation"]["arbitration_years"]["start"] = 2021

        with self.assertRaisesRegex(ConfigurationError, "non-overlapping"):
            validate_policy(raw)


if __name__ == "__main__":
    unittest.main()
