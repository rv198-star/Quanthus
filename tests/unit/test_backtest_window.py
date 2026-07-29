import json
import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

from quanthus.backtest import (
    BacktestSignal,
    OrderSide,
    build_window_order_intent,
    load_execution_policy,
)
from quanthus.contracts import SnapshotRef
from quanthus.policy import find_project_root

ROOT = find_project_root()
POLICY_PATH = ROOT / "tests" / "fixtures" / "p2" / "execution-policy.synthetic.v1.json"
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p2_window_cutoff_expiry.v1.json"


class BacktestWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        self.inputs = self.fixture["inputs"]
        self.expected = self.fixture["expected"]
        self.profile = load_execution_policy(POLICY_PATH).profile(self.inputs["profile_id"])

    def signal(
        self,
        *,
        window_id: str | None = None,
        knowledge_cutoff: str | None = None,
        decision_at: str | None = None,
    ) -> BacktestSignal:
        market_date = date.fromisoformat(self.inputs["market_date"])
        return BacktestSignal(
            signal_id="signal-window-001",
            snapshot=SnapshotRef(
                market_date=market_date,
                knowledge_cutoff=datetime.fromisoformat(
                    knowledge_cutoff or self.inputs["knowledge_cutoff"]
                ),
                source_revision="p2-synthetic-window-input-v1",
                snapshot_id="p2-synthetic-window-snapshot-001",
            ),
            decision_at=datetime.fromisoformat(decision_at or self.inputs["decision_at"]),
            profile_id=self.inputs["profile_id"],
            window_id=window_id or self.inputs["window_id"],
            security_code="600000",
            side=OrderSide.BUY,
            unit_id="unit-001",
            quantity=100,
            aggregate_score=Decimal("0.80"),
            reason_code="synthetic_window_opportunity",
            decision_version="synthetic-decision-v1",
        )

    def test_window_intent_uses_cutoff_and_half_open_expiry(self) -> None:
        intent = build_window_order_intent(self.signal(), self.profile)

        self.assertEqual(intent.decision_cutoff.isoformat(), self.expected["decision_cutoff"])
        self.assertEqual(intent.eligible_at.isoformat(), self.expected["eligible_at"])
        self.assertEqual(intent.expires_at.isoformat(), self.expected["expires_at"])
        self.assertEqual(
            [
                intent.state_at(datetime.fromisoformat(observed_at)).value
                for observed_at in self.inputs["observations"]
            ],
            self.expected["states"],
        )
        self.assertNotIn("fill", intent.to_dict())

    def test_second_window_expires_at_session_close(self) -> None:
        intent = build_window_order_intent(
            self.signal(
                window_id="window_2",
                knowledge_cutoff="2026-07-28T14:30:00+08:00",
                decision_at="2026-07-28T14:31:00+08:00",
            ),
            self.profile,
        )

        self.assertEqual(intent.expires_at.isoformat(), self.expected["second_window_expires_at"])
        self.assertEqual(intent.state_at(intent.expires_at).value, "expired")

    def test_future_knowledge_and_late_creation_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds its decision cutoff"):
            build_window_order_intent(
                self.signal(knowledge_cutoff="2026-07-28T09:55:01+08:00"), self.profile
            )
        with self.assertRaisesRegex(ValueError, "before first eligible execution"):
            build_window_order_intent(
                self.signal(decision_at="2026-07-28T10:00:00+08:00"), self.profile
            )

    def test_profile_and_window_identity_must_match(self) -> None:
        with self.assertRaisesRegex(ValueError, "profile identities must match"):
            build_window_order_intent(
                replace(self.signal(), profile_id="operator-assisted-two-window-v1"),
                self.profile,
            )
        with self.assertRaisesRegex(ValueError, "unknown window_id"):
            build_window_order_intent(self.signal(window_id="window_3"), self.profile)


if __name__ == "__main__":
    unittest.main()
