import unittest
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from quanthus.event_library.contracts import (
    EventContractError,
    EventIdentity,
    EventRecord,
    SplitAssignment,
    event_record_from_dict,
    validate_event_record_set,
)


class EventLibraryContractTests(unittest.TestCase):
    def _identity(self) -> EventIdentity:
        return EventIdentity(
            security_id="security-cn-000001",
            signal_date=date(2020, 6, 30),
            candidate_rule_id="p4-event.price-volume-state-transition",
            candidate_rule_version="1.0.0",
            snapshot_id="dwd-snapshot-2020-06-30-v1",
        )

    def _record(self) -> EventRecord:
        shanghai = ZoneInfo("Asia/Shanghai")
        digest = "sha256:" + "a" * 64
        return EventRecord(
            identity=self._identity(),
            candidate_rule_digest=digest,
            snapshot_manifest_digest=digest,
            knowledge_cutoff=datetime(2020, 6, 30, 15, 0, tzinfo=shanghai),
            signal_close=datetime(2020, 6, 30, 15, 0, tzinfo=shanghai),
            eligibility_evidence_digest=digest,
            feature_window_manifest_digest=digest,
            isolation_group_id="security-cn-000001:2020-06",
            split_policy_id="p4-event-date-split",
            split_policy_version="1.0.0",
            split_policy_digest=digest,
            split_assignment=SplitAssignment.DEVELOPMENT,
        )

    def test_event_id_uses_only_the_frozen_outcome_independent_namespace(self) -> None:
        identity = self._identity()
        future_payload_a = {"future_return": "0.50", "label": "positive"}
        future_payload_b = {"future_return": "-0.50", "label": "failure"}

        self.assertEqual(identity.event_id, self._identity().event_id)
        self.assertNotEqual(
            identity.event_id,
            replace(identity, candidate_rule_version="1.0.1").event_id,
        )
        self.assertNotEqual(
            identity.event_id,
            replace(identity, snapshot_id="dwd-snapshot-2020-06-30-v2").event_id,
        )
        self.assertNotEqual(future_payload_a, future_payload_b)
        self.assertEqual(identity.event_id, self._identity().event_id)

    def test_event_record_is_label_free_and_pit_bound(self) -> None:
        record = self._record()

        self.assertEqual(record.event_id, record.identity.event_id)
        self.assertNotIn("outcome", record.to_dict())
        self.assertNotIn("label", record.to_dict())
        self.assertEqual(record.to_dict()["split_assignment"], "development")

        shanghai = ZoneInfo("Asia/Shanghai")
        with self.assertRaisesRegex(EventContractError, "after T close"):
            replace(
                record,
                knowledge_cutoff=datetime(2020, 6, 30, 15, 0, 1, tzinfo=shanghai),
            )
        with self.assertRaisesRegex(EventContractError, "lowercase sha256"):
            replace(record, candidate_rule_digest="not-a-digest")

    def test_event_record_parser_rejects_unknown_fields_and_forged_identity(self) -> None:
        record = self._record()
        raw = record.to_dict()

        self.assertEqual(event_record_from_dict(raw), record)
        raw["future_return_20"] = "0.50"
        with self.assertRaisesRegex(EventContractError, "unexpected"):
            event_record_from_dict(raw)

        forged = record.to_dict()
        forged["event_id"] = "event:" + "0" * 64
        with self.assertRaisesRegex(EventContractError, "frozen identity"):
            event_record_from_dict(forged)

    def test_event_record_set_rejects_duplicates_and_multi_split_identity(self) -> None:
        record = self._record()

        with self.assertRaisesRegex(EventContractError, "duplicate event_id"):
            validate_event_record_set([record, record])
        with self.assertRaisesRegex(EventContractError, "multiple split"):
            validate_event_record_set(
                [
                    record,
                    replace(record, split_assignment=SplitAssignment.ARBITRATION),
                ]
            )


if __name__ == "__main__":
    unittest.main()
