from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from quanthus.backtest.inputs import CoverageStatus, P1DailyExecutionEvidence
from quanthus.contracts import SnapshotRef
from quanthus.event_library.contracts import sha256_digest
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import (
    CandidateDailyInput,
    CandidateEntryEvidence,
    CandidateReason,
    EntryStatus,
    partition_executable_entries,
    scan_forward_candidates,
)
from quanthus.event_library.source import load_event_scan_source_policy
from quanthus.event_library.splits import load_split_policy
from quanthus.policy import find_project_root

ROOT = find_project_root()
CARD = load_candidate_rule_card(
    ROOT / "config" / "p4-candidate-price-volume-transition.research.v1.json"
)
SPLIT = load_split_policy(ROOT / "config" / "p4-event-split-policy.research.v1.json")
SOURCE = load_event_scan_source_policy(
    ROOT / "config" / "p4-event-scan-source.research.v1.json",
    release_path=ROOT / "config" / "releases" / "v2-build202607.json",
    provider_path=ROOT / "config" / "replay-data-provider.development.v2.json",
)
SHANGHAI = ZoneInfo("Asia/Shanghai")
SNAPSHOT_ID = "dwd-base-202607-v2.1+correction-20260813-001"
SNAPSHOT_MANIFEST = SOURCE.base_release.manifest_sha256


def _weekdays(start: date, end: date) -> tuple[date, ...]:
    values: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            values.append(cursor)
        cursor += timedelta(days=1)
    return tuple(values)


TRADING_DATES = _weekdays(date(2012, 1, 1), date(2021, 12, 31))


def _row(
    *,
    security: str,
    session_date: date,
    range_fraction: Decimal,
    turnover: Decimal,
    industry: str = "industry-01",
) -> CandidateDailyInput:
    open_price = Decimal("100.00")
    return CandidateDailyInput(
        security_id=f"security-{security}",
        listing_id=f"listing-{security}",
        listing_code=security,
        company_id=f"company-{security}",
        venue_id="SSE",
        instrument_type="common_equity",
        static_level1_industry_id=industry,
        session_date=session_date,
        close_at=datetime.combine(session_date, time(15, 0), SHANGHAI),
        knowledge_cutoff=datetime.combine(session_date, time(14, 59), SHANGHAI),
        open_price=open_price,
        high_price=open_price * (Decimal("1") + range_fraction),
        low_price=open_price,
        close_price=open_price,
        turnover=turnover,
        is_tradable=True,
        listing_sessions=200,
        adjustment_effective=False,
        source_evidence_digest=sha256_digest(
            {"security": security, "date": session_date.isoformat()}
        ),
    )


def _series(
    security: str,
    *,
    signal_indices: tuple[int, ...],
    industry: str = "industry-01",
) -> list[CandidateDailyInput]:
    start = min(signal_indices) - 21
    end = max(signal_indices)
    ranges = {index: Decimal("0.01") for index in range(start, end + 1)}
    turnovers = {index: Decimal("100.00") for index in range(start, end + 1)}
    for signal in signal_indices:
        ranges[signal - 1] = Decimal("0.005")
        turnovers[signal - 1] = Decimal("50.00")
        ranges[signal] = Decimal("0.02")
        turnovers[signal] = Decimal("200.00")
    return [
        _row(
            security=security,
            session_date=TRADING_DATES[index],
            range_fraction=ranges[index],
            turnover=turnovers[index],
            industry=industry,
        )
        for index in range(start, end + 1)
    ]


def _scan(rows: list[CandidateDailyInput]):
    return scan_forward_candidates(
        rows,
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
    )


def _entry_evidence(candidate, *, state: str) -> CandidateEntryEvidence:
    entry_date = candidate.entry_date
    open_at = datetime.combine(entry_date, time(9, 30), SHANGHAI)
    close_at = datetime.combine(entry_date, time(15, 0), SHANGHAI)
    snapshot = SnapshotRef(
        market_date=entry_date,
        knowledge_cutoff=close_at,
        source_revision="synthetic-entry-v1",
        snapshot_id=SNAPSHOT_ID,
    )
    if state == "suspended":
        evidence = P1DailyExecutionEvidence(
            snapshot=snapshot,
            security_code=candidate.listing_code,
            open_at=open_at,
            close_at=close_at,
            coverage_status=CoverageStatus.SUSPENDED,
            data_valid=False,
            is_executable=False,
            entry_policy_allows=False,
            reason_code="SUSPENDED",
            open_price=None,
            high_price=None,
            low_price=None,
            close_price=None,
            upper_limit_price=None,
            lower_limit_price=None,
        )
    else:
        open_price = Decimal("11.00") if state == "limit_up" else Decimal("10.00")
        evidence = P1DailyExecutionEvidence(
            snapshot=snapshot,
            security_code=candidate.listing_code,
            open_at=open_at,
            close_at=close_at,
            coverage_status=CoverageStatus.TRADING,
            data_valid=True,
            is_executable=True,
            entry_policy_allows=True,
            reason_code="TRADING",
            open_price=open_price,
            high_price=Decimal("11.50"),
            low_price=Decimal("9.50"),
            close_price=Decimal("10.50"),
            upper_limit_price=Decimal("11.00"),
            lower_limit_price=Decimal("9.00"),
        )
    return CandidateEntryEvidence(
        event_id=candidate.event_id,
        evidence=evidence,
        evidence_digest=sha256_digest(
            {"event_id": candidate.event_id, "state": state}
        ),
        matcher_policy_version="p2-daily-research-v1",
    )


class EventForwardScanTests(unittest.TestCase):
    def test_quiet_to_active_edge_materializes_one_deterministic_event(self) -> None:
        result = _scan(_series("600001", signal_indices=(43,)))

        self.assertEqual(result.counters.evaluated_rows, 1)
        self.assertEqual(result.counters.raw_triggers, 1)
        self.assertEqual(result.counters.candidate_events, 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate.feature_evidence.t_minus_1_range_activity_ratio, Decimal("0.5"))
        self.assertEqual(
            candidate.feature_evidence.t_minus_1_turnover_activity_ratio,
            Decimal("0.5"),
        )
        self.assertEqual(candidate.feature_evidence.t_range_activity_ratio, Decimal("2"))
        self.assertEqual(candidate.feature_evidence.t_turnover_activity_ratio, Decimal("2"))
        self.assertEqual(candidate.record.identity.snapshot_id, SNAPSHOT_ID)
        self.assertEqual(candidate.record.snapshot_manifest_digest, SNAPSHOT_MANIFEST)

        repeated = _scan(list(reversed(_series("600001", signal_indices=(43,)))))
        self.assertEqual(repeated.candidates[0].event_id, candidate.event_id)
        self.assertEqual(
            repeated.candidates[0].record.feature_window_manifest_digest,
            candidate.record.feature_window_manifest_digest,
        )

    def test_new_dwd_build_cutoff_cannot_be_used_as_historical_knowledge(self) -> None:
        rows = _series("600009", signal_indices=(43,))
        rows[-1] = replace(
            rows[-1],
            knowledge_cutoff=datetime(2026, 7, 31, 15, 0, tzinfo=SHANGHAI),
        )

        result = _scan(rows)

        self.assertEqual(result.counters.evaluated_rows, 1)
        self.assertEqual(result.counters.raw_triggers, 0)
        self.assertEqual(result.counters.candidate_events, 0)
        self.assertEqual(
            result.counters.reason_counts[CandidateReason.KNOWLEDGE_AFTER_T_CLOSE.value],
            1,
        )

    def test_cooldown_and_concentration_are_deterministic_and_reconciled(self) -> None:
        cooldown = _scan(_series("600002", signal_indices=(43, 50)))

        self.assertEqual(cooldown.counters.raw_triggers, 2)
        self.assertEqual(cooldown.counters.isolated_candidates, 1)
        self.assertEqual(
            cooldown.counters.reason_counts[CandidateReason.SAME_SECURITY_ISOLATION.value],
            1,
        )

        rows: list[CandidateDailyInput] = []
        for index in range(51):
            rows.extend(
                _series(
                    f"60{index:04d}",
                    signal_indices=(43,),
                    industry="industry-crowded",
                )
            )
        concentrated = _scan(rows)
        self.assertEqual(concentrated.counters.raw_triggers, 51)
        self.assertEqual(concentrated.counters.isolated_candidates, 51)
        self.assertEqual(concentrated.counters.concentration_retained, 50)
        self.assertEqual(concentrated.counters.candidate_events, 50)
        self.assertEqual(
            concentrated.counters.reason_counts[CandidateReason.CONCENTRATION_REJECTED.value],
            1,
        )

    def test_split_boundary_rejects_whole_event_without_unexplained_loss(self) -> None:
        result = _scan(_series("600003", signal_indices=(21,)))

        self.assertEqual(result.counters.evaluated_rows, 1)
        self.assertEqual(result.counters.raw_triggers, 1)
        self.assertEqual(result.counters.concentration_retained, 1)
        self.assertEqual(result.counters.candidate_events, 0)
        self.assertEqual(
            result.counters.reason_counts[CandidateReason.SPLIT_POLICY_REJECTED.value],
            1,
        )

    def test_t_plus_1_partition_reuses_p2_buy_open_contract(self) -> None:
        candidate = _scan(_series("600004", signal_indices=(43,))).candidates[0]

        normal = _entry_evidence(candidate, state="normal")
        executable = partition_executable_entries(
            (candidate,), evidence_by_event_id={candidate.event_id: normal}
        )
        self.assertEqual(executable.executable_events, 1)
        self.assertEqual(executable.records[0].status, EntryStatus.EXECUTABLE)
        self.assertEqual(executable.records[0].reason_code, "executable_at_t_plus_1_open")

        limit_up = _entry_evidence(candidate, state="limit_up")
        blocked = partition_executable_entries(
            (candidate,), evidence_by_event_id={candidate.event_id: limit_up}
        )
        self.assertEqual(blocked.not_executable_events, 1)
        self.assertEqual(blocked.records[0].status, EntryStatus.NOT_EXECUTABLE)
        self.assertEqual(blocked.records[0].reason_code, "buy_blocked_limit_up")
        self.assertEqual(blocked.records[0].event_id, candidate.event_id)

        suspended = _entry_evidence(candidate, state="suspended")
        suspended_result = partition_executable_entries(
            (candidate,), evidence_by_event_id={candidate.event_id: suspended}
        )
        self.assertEqual(suspended_result.records[0].reason_code, "security_suspended")

        missing = partition_executable_entries((candidate,), evidence_by_event_id={})
        self.assertEqual(missing.records[0].reason_code, "missing_execution_evidence")
        self.assertEqual(missing.records[0].event_id, candidate.event_id)


if __name__ == "__main__":
    unittest.main()
