import unittest

from quanthus.policy import load_policy


class FrozenP0DecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_policy()

    def test_time_contract(self) -> None:
        self.assertEqual(self.policy.at("clock", "signal_time"), "T_after_close")
        self.assertEqual(self.policy.at("clock", "feature_cutoff"), "T_close")
        self.assertEqual(self.policy.at("clock", "first_execution_time"), "T_plus_1_open")

    def test_data_contract(self) -> None:
        self.assertEqual(
            self.policy.at("data", "price_storage"),
            "unadjusted_with_effective_date_adjustment_factors",
        )
        self.assertEqual(
            self.policy.at("data", "sector_fallback"),
            "disable_sector_expert_and_filter",
        )

    def test_order_contract(self) -> None:
        self.assertEqual(
            self.policy.at("orders", "unfilled_buy"),
            "cancel_and_reassess_next_day",
        )
        self.assertEqual(
            self.policy.at("orders", "unfilled_sell"),
            "carry_until_tradable",
        )

    def test_risk_contract(self) -> None:
        self.assertEqual(self.policy.at("risk", "max_units_per_level1_industry"), 2)
        self.assertEqual(self.policy.at("risk", "max_new_units_per_day"), 2)
        self.assertEqual(
            self.policy.at("risk", "veto_priority"),
            ["force_liquidate", "reduce_only", "block_new"],
        )
        self.assertEqual(len(self.policy.at("risk", "exit_triggers")), 4)

    def test_event_contract(self) -> None:
        self.assertTrue(self.policy.at("events", "feature_window_includes_signal_day"))
        self.assertEqual(
            self.policy.at("events", "unexecutable_outcome"),
            "not_executable",
        )

    def test_validation_partitions(self) -> None:
        self.assertEqual(
            self.policy.at("validation", "development_years"),
            {"start": 2012, "end": 2021},
        )
        self.assertEqual(
            self.policy.at("validation", "arbitration_years"),
            {"start": 2022, "end": 2024},
        )
        self.assertEqual(
            self.policy.at("validation", "permanent_blind_years"),
            {"start": 2025, "end": 2025},
        )

    def test_counterfactual_contract(self) -> None:
        self.assertEqual(
            self.policy.at("counterfactual", "official_metric"),
            "capital_constrained_strategy_rerun_delta",
        )
        self.assertEqual(
            self.policy.at("counterfactual", "single_signal_metric"),
            "diagnostic_only",
        )


if __name__ == "__main__":
    unittest.main()
