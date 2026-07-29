import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

from quanthus.policy import find_project_root
from quanthus.strategy_policy import StrategyPolicyError, load_governed_strategy_policy

ROOT = find_project_root()
POLICY_PATH = ROOT / "config" / "p3-governed-strategy.research.v1.json"


class GovernedStrategyPolicyTests(unittest.TestCase):
    def test_repository_policy_freezes_candidate_before_development_replay(self) -> None:
        policy = load_governed_strategy_policy(POLICY_PATH)

        self.assertEqual(policy.strategy_version, "1.0.0")
        self.assertEqual(policy.portfolio.unit_count, 5)
        self.assertEqual(policy.bindings.execution_policy_version, "p2-daily-research-v1")
        self.assertEqual(str(policy.portfolio.trial_position_fraction), "0.30")
        self.assertFalse(policy.portfolio.adds_enabled)
        self.assertFalse(policy.portfolio.reductions_enabled)
        self.assertEqual(policy.exit.maximum_holding_sessions, 40)
        self.assertTrue(policy.digest.startswith("sha256:"))
        policy.validation.require_development_scope(date(2012, 1, 4), date(2021, 12, 30))
        with self.assertRaisesRegex(StrategyPolicyError, "restricted to development"):
            policy.validation.require_development_scope(date(2022, 1, 4), date(2022, 1, 5))

    def test_unknown_fields_and_unsupported_full_entry_fail_closed(self) -> None:
        raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        raw["portfolio"]["unexpected"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(StrategyPolicyError, "unexpected"):
                load_governed_strategy_policy(path)

        policy = load_governed_strategy_policy(POLICY_PATH)
        with self.assertRaisesRegex(StrategyPolicyError, "does not support add or reduce"):
            replace(policy.portfolio, adds_enabled=True)
        with self.assertRaisesRegex(StrategyPolicyError, "unsupported structural_break_rule"):
            replace(policy.exit, structural_break_rule="unbound_rule_name")
        with self.assertRaisesRegex(StrategyPolicyError, "required_breakdowns"):
            replace(policy.evaluation, required_breakdowns=("calendar_year",))


if __name__ == "__main__":
    unittest.main()
