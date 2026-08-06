import json
import tempfile
import unittest
from pathlib import Path

from quanthus.event_library.rule_card import (
    CandidateRuleCardError,
    calculate_candidate_rule_card_digest,
    load_candidate_rule_card,
)
from quanthus.policy import find_project_root

ROOT = find_project_root()
CARD_PATH = ROOT / "config" / "p4-candidate-price-volume-transition.research.v1.json"


class CandidateRuleCardTests(unittest.TestCase):
    def _write(self, raw: object, directory: str) -> Path:
        path = Path(directory) / "candidate.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    def test_repository_card_is_complete_digest_bound_and_stage_b_approved(self) -> None:
        card = load_candidate_rule_card(CARD_PATH)

        self.assertEqual(card.rule_id, "p4-event.price-volume-state-transition")
        self.assertEqual(card.rule_version, "1.0.0")
        self.assertEqual(card.lifecycle, "approved")
        self.assertEqual(card.governance.approval_state, "approved")
        self.assertEqual(
            card.governance.decision_reference,
            "https://github.com/rv198-star/Quanthus/issues/73#issuecomment-5196483760",
        )
        self.assertEqual(card.semantic_digest, card.computed_semantic_digest)
        self.assertEqual(card.eligibility.minimum_history_sessions, 22)
        self.assertEqual(card.isolation.cooldown_sessions, 22)
        self.assertEqual(
            card.bindings.industry_mapping_usage,
            "static_non_pit_concentration_only",
        )
        self.assertEqual(card.inputs[-1].table, "static_industry_mapping")
        self.assertEqual(card.inputs[-1].usage, "static_non_pit_concentration_only")
        self.assertIn("quiet_state(T-1)", card.states.transition_edge_definition)
        self.assertEqual(
            [threshold.threshold_id for threshold in card.thresholds],
            [
                "quiet_range_ratio_max",
                "quiet_turnover_ratio_max",
                "active_range_ratio_min",
                "active_turnover_ratio_min",
            ],
        )
        card.require_stage_b_authority()

    def test_unknown_and_outcome_bearing_fields_fail_closed(self) -> None:
        raw = json.loads(CARD_PATH.read_text(encoding="utf-8"))
        raw["unexpected"] = True
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(CandidateRuleCardError, "unexpected"),
        ):
            load_candidate_rule_card(self._write(raw, directory))

        raw = json.loads(CARD_PATH.read_text(encoding="utf-8"))
        raw["inputs"][0]["fields"].append("future_return_20")
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(CandidateRuleCardError, "outcome-bearing"),
        ):
            load_candidate_rule_card(self._write(raw, directory))

    def test_semantic_mutation_requires_a_new_digest(self) -> None:
        raw = json.loads(CARD_PATH.read_text(encoding="utf-8"))
        raw["thresholds"][0]["value"] = "0.81"
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(raw, directory)
            self.assertNotEqual(
                calculate_candidate_rule_card_digest(path),
                raw["semantic_digest"],
            )
            with self.assertRaisesRegex(CandidateRuleCardError, "does not match"):
                load_candidate_rule_card(path)

    def test_calibration_cannot_be_hidden_behind_a_non_calibration_rationale(self) -> None:
        raw = json.loads(CARD_PATH.read_text(encoding="utf-8"))
        raw["thresholds"][0]["calibration_procedure"] = "select best future separation"
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(CandidateRuleCardError, "allowed only"),
        ):
            load_candidate_rule_card(self._write(raw, directory))


if __name__ == "__main__":
    unittest.main()
