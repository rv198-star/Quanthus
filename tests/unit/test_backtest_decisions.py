import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from quanthus.backtest import (
    AuditEventStore,
    BacktestResult,
    BacktestRunSpec,
    DecisionDisposition,
    DecisionEvent,
    EventType,
)
from quanthus.backtest.contracts import EngineEvent
from quanthus.contracts import SnapshotRef
from quanthus.policy import find_project_root

ROOT = find_project_root()
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p2_decision_log.v1.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class BacktestDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        self.inputs = self.fixture["inputs"]
        self.expected = self.fixture["expected"]
        self.decided_at = datetime(2026, 7, 27, 15, 5, tzinfo=SHANGHAI)
        self.snapshot = SnapshotRef(
            market_date=date(2026, 7, 27),
            knowledge_cutoff=datetime(2026, 7, 27, 15, 0, tzinfo=SHANGHAI),
            source_revision="p2-synthetic-decision-evidence-v1",
            snapshot_id="p2-decision-snapshot-v1",
        )

    def run_result(self) -> BacktestResult:
        order_id = "order-decision-001"
        signal_payload = {
            "signal_id": self.inputs["accepted_signal_id"],
            "profile_id": self.inputs["profile_id"],
            "window_id": None,
            "security_code": self.inputs["security_code"],
            "unit_id": self.inputs["unit_id"],
        }
        order_payload = {
            "order_id": order_id,
            "signal_id": self.inputs["accepted_signal_id"],
        }
        events = (
            EngineEvent(
                event_id="event-decision-signal-001",
                sequence=1,
                event_type=EventType.SIGNAL_ACCEPTED,
                event_time=self.decided_at,
                reason_code="signal_contract_valid",
                payload=signal_payload,
            ),
            EngineEvent(
                event_id="event-decision-order-001",
                sequence=2,
                event_type=EventType.ORDER_CREATED,
                event_time=self.decided_at,
                reason_code="next_session_order_created",
                payload=order_payload,
            ),
        )
        return BacktestResult(
            run=BacktestRunSpec(
                code_version="test-code-v1",
                parameter_hash="sha256:synthetic-decision-log",
                data_snapshot_id=self.snapshot.snapshot_id,
                feature_version="synthetic-feature-v1",
                rule_version="synthetic-decision-v1",
                run_id=self.inputs["run_id"],
                execution_policy_version="p2-execution-synthetic-v1",
                execution_profile_id=self.inputs["profile_id"],
                schedule_version="close-to-next-open-synthetic-v1",
                official=False,
            ),
            events=events,
            result_summary={"status": "order_created", "fills": 0},
        )

    def decision(self, disposition: DecisionDisposition) -> DecisionEvent:
        accepted = disposition is DecisionDisposition.ACCEPTED
        return DecisionEvent(
            decision_id=f"decision-{disposition.value}-001",
            run_id=self.inputs["run_id"],
            signal_id=self.inputs["accepted_signal_id"] if accepted else None,
            snapshot=self.snapshot,
            decided_at=self.decided_at,
            profile_id=self.inputs["profile_id"],
            window_id=None,
            security_code=self.inputs["security_code"],
            unit_id=self.inputs["unit_id"],
            lifecycle_stage="empty",
            aggregate_score=Decimal("0.80"),
            expert_outputs={
                "synthetic_expert": {
                    "score": "0.80",
                    "reason_code": "fixture_evidence",
                }
            },
            vetoes=() if accepted else ("synthetic_veto",),
            constraints={"max_quantity": "100"},
            proposed_action="buy",
            final_action="buy" if accepted else "none",
            reason_codes=(f"{disposition.value}_fixture",),
            policy_versions={"decision": "synthetic-decision-v1"},
            linked_order_ids=("order-decision-001",) if accepted else (),
            disposition=disposition,
        )

    def test_decision_dispositions_persist_without_shadow_execution_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AuditEventStore(Path(directory))
            store.persist_run(self.run_result())
            persisted = [
                store.persist_decision(self.decision(disposition))
                for disposition in DecisionDisposition
            ]

            self.assertEqual(
                [item.event["disposition"] for item in persisted],
                self.expected["dispositions"],
            )
            self.assertEqual(
                persisted[0].event["final_action"], self.expected["accepted_final_action"]
            )
            for item in persisted[1:]:
                self.assertEqual(
                    len(item.event["linked_order_ids"]),
                    self.expected["non_executing_linked_order_count"],
                )
            self.assertEqual(
                store.persist_decision(self.decision(DecisionDisposition.ACCEPTED)).event,
                persisted[0].event,
            )

    def test_accepted_decision_must_match_persisted_signal_and_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AuditEventStore(Path(directory))
            store.persist_run(self.run_result())
            with self.assertRaisesRegex(ValueError, "linked_order_ids"):
                store.persist_decision(
                    replace(
                        self.decision(DecisionDisposition.ACCEPTED),
                        linked_order_ids=("wrong-order",),
                    )
                )

    def test_changed_decision_cannot_replace_immutable_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AuditEventStore(Path(directory))
            store.persist_run(self.run_result())
            accepted = self.decision(DecisionDisposition.ACCEPTED)
            store.persist_decision(accepted)
            with self.assertRaisesRegex(FileExistsError, "different contents"):
                store.persist_decision(replace(accepted, reason_codes=("changed",)))


if __name__ == "__main__":
    unittest.main()
