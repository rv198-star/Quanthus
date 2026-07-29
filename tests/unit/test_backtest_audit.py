import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quanthus.backtest import (
    AuditEventStore,
    AuditIntegrityError,
    BacktestResult,
    BacktestRunSpec,
    EventType,
    NotificationDeliveryState,
    NotificationEvent,
)
from quanthus.backtest.contracts import EngineEvent
from quanthus.policy import find_project_root

ROOT = find_project_root()
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "golden" / "p2_audit_notification.v1.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class BacktestAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        self.inputs = self.fixture["inputs"]
        self.expected = self.fixture["expected"]

    def result(self) -> BacktestResult:
        decision_at = datetime(2026, 7, 28, 9, 50, tzinfo=SHANGHAI)
        run = BacktestRunSpec(
            code_version="test-code-v1",
            parameter_hash="sha256:synthetic-parameters",
            data_snapshot_id="p2-window-snapshot-001",
            feature_version="synthetic-feature-v1",
            rule_version="synthetic-decision-v1",
            run_id=self.inputs["run_id"],
            execution_policy_version="p2-execution-synthetic-v1",
            execution_profile_id=self.inputs["profile_id"],
            schedule_version="synthetic-two-window-schedule-v1",
            official=False,
        )
        event = EngineEvent(
            event_id="evt-signal-notification-001",
            sequence=1,
            event_type=EventType.SIGNAL_ACCEPTED,
            event_time=decision_at,
            reason_code="signal_contract_valid",
            payload={
                "signal_id": self.inputs["signal_id"],
                "snapshot_id": "p2-window-snapshot-001",
                "decision_at": decision_at.isoformat(),
                "profile_id": self.inputs["profile_id"],
                "window_id": self.inputs["window_id"],
                "security_code": "600000",
                "side": "buy",
                "unit_id": "unit-001",
                "quantity": 100,
                "aggregate_score": "0.80",
                "decision_reason_code": "synthetic_window_opportunity",
                "decision_version": "synthetic-decision-v1",
            },
        )
        return BacktestResult(
            run=run,
            events=(event,),
            result_summary={"status": "decision_persisted", "fills": 0},
        )

    def notification(self, attempt_index: int = 0) -> NotificationEvent:
        raw = self.inputs["attempts"][attempt_index]
        return NotificationEvent(
            notification_id=self.inputs["notification_id"],
            run_id=self.inputs["run_id"],
            signal_id=self.inputs["signal_id"],
            profile_id=self.inputs["profile_id"],
            window_id=self.inputs["window_id"],
            content_hash=self.inputs["content_hash"],
            provider=self.inputs["provider"],
            attempt=raw["attempt"],
            attempted_at=datetime.fromisoformat(raw["attempted_at"]),
            terminal_state=NotificationDeliveryState(raw["terminal_state"]),
            reason_code=raw["reason_code"],
        )

    def test_run_publish_is_idempotent_and_manifest_covers_the_event_stream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AuditEventStore(Path(directory))
            first = store.persist_run(self.result())
            second = store.persist_run(self.result())

            self.assertEqual(first.storage_manifest, second.storage_manifest)
            self.assertEqual(first.events, second.events)
            self.assertEqual(
                [event["event_type"] for event in first.events],
                self.expected["run_event_types"],
            )
            self.assertTrue(
                str(first.storage_manifest["event_stream_checksum"]).startswith("sha256:")
            )
            self.assertTrue((first.path / "READY").is_file())

            changed = replace(self.result(), result_summary={"status": "changed", "fills": 0})
            with self.assertRaisesRegex(FileExistsError, "different contents"):
                store.persist_run(changed)

    def test_tampered_event_stream_fails_checksum_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AuditEventStore(Path(directory))
            persisted = store.persist_run(self.result())
            events_path = persisted.path / "events.jsonl"
            events_path.write_bytes(events_path.read_bytes().replace(b"600000", b"600001"))

            with self.assertRaisesRegex(AuditIntegrityError, "event stream checksum mismatch"):
                store.load_run(self.inputs["run_id"])

    def test_notification_retries_are_append_only_and_do_not_imply_a_fill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AuditEventStore(Path(directory))
            with self.assertRaisesRegex(KeyError, "audit run not found"):
                store.persist_notification(self.notification())

            persisted_run = store.persist_run(self.result())
            first = store.persist_notification(self.notification())
            replayed = store.persist_notification(self.notification())
            completed = store.persist_notification(self.notification(1))
            reloaded_run = store.load_run(self.inputs["run_id"])

            self.assertEqual(first.attempts, replayed.attempts)
            self.assertEqual(len(completed.attempts), self.expected["notification_attempt_count"])
            self.assertEqual(
                [attempt["terminal_state"] for attempt in completed.attempts],
                self.expected["terminal_states"],
            )
            self.assertTrue(all(completed.attempt_checksums))
            self.assertEqual(persisted_run.events, reloaded_run.events)
            self.assertEqual(
                sum(event["event_type"] == EventType.FILL.value for event in reloaded_run.events),
                self.expected["fill_count"],
            )
            self.assertEqual(
                completed.attempts[-1]["event_type"],
                self.expected["notification_event_type"],
            )

            changed_attempt = replace(self.notification(1), reason_code="changed_after_delivery")
            with self.assertRaisesRegex(FileExistsError, "different contents"):
                store.persist_notification(changed_attempt)

    def test_notification_identity_and_attempt_order_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AuditEventStore(Path(directory))
            store.persist_run(self.result())
            with self.assertRaisesRegex(ValueError, "first notification attempt"):
                store.persist_notification(self.notification(1))

            store.persist_notification(self.notification())
            wrong_window = replace(self.notification(1), window_id="window_2")
            with self.assertRaisesRegex(ValueError, "must match the persisted signal"):
                store.persist_notification(wrong_window)


if __name__ == "__main__":
    unittest.main()
