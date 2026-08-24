from __future__ import annotations

import unittest
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from random import Random
from zoneinfo import ZoneInfo

from quanthus.backtest.inputs import CoverageStatus, P1DailyExecutionEvidence
from quanthus.contracts import SnapshotRef
from quanthus.event_library import scan as scan_module
from quanthus.event_library.contracts import sha256_digest
from quanthus.event_library.rule_card import load_candidate_rule_card
from quanthus.event_library.scan import (
    CandidateDailyInput,
    CandidateEntryEvidence,
    CandidateReason,
    CandidateScanError,
    CandidateScanShard,
    EntryStatus,
    finalize_candidate_scan_shards,
    partition_executable_entries,
    scan_candidate_series_shard,
    scan_forward_candidate_series,
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


def _reference_security_scan(rows: list[CandidateDailyInput]):
    ordered_dates, date_index, history_sessions = scan_module._scan_context(
        trading_dates=TRADING_DATES,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        split_policy=SPLIT,
        source_policy=SOURCE,
    )
    ordered_rows = tuple(rows)
    reasons: Counter[str] = Counter()
    raw: list[scan_module._RawCandidate] = []
    evaluated = 0
    security_id = ordered_rows[0].security_id
    for local_index in range(history_sessions - 1, len(ordered_rows)):
        current = ordered_rows[local_index]
        evaluated += 1
        trading_index = date_index.get(current.session_date)
        if trading_index is None or trading_index < history_sessions - 1:
            reasons[CandidateReason.OBSERVATION_GAP.value] += 1
            continue
        if trading_index + 1 >= len(ordered_dates):
            reasons[CandidateReason.MISSING_T_PLUS_1_SESSION.value] += 1
            continue
        window = ordered_rows[
            local_index - history_sessions + 1 : local_index + 1
        ]
        expected_dates = ordered_dates[
            trading_index - history_sessions + 1 : trading_index + 1
        ]
        reason = scan_module._window_reason(window, expected_dates=expected_dates, card=CARD)
        if reason is not None:
            reasons[reason.value] += 1
            continue
        features = scan_module._feature_evidence(window)
        if features is None:
            reasons[CandidateReason.ZERO_ACTIVITY_BASELINE.value] += 1
            continue
        if not scan_module._triggered(features, CARD):
            reasons[CandidateReason.NOT_TRIGGERED.value] += 1
            continue
        eligibility_payload = {
            "security_id": security_id,
            "listing_id": current.listing_id,
            "signal_date": current.session_date.isoformat(),
            "source_evidence_digests": list(features.source_evidence_digests),
            "candidate_rule_digest": CARD.semantic_digest,
            "snapshot_manifest_digest": SNAPSHOT_MANIFEST,
        }
        raw.append(
            scan_module._RawCandidate(
                security_id=security_id,
                listing_id=current.listing_id,
                listing_code=current.listing_code,
                company_id=current.company_id,
                industry_id=current.static_level1_industry_id,
                signal_date=current.session_date,
                signal_close=current.close_at,
                knowledge_cutoff=max(item.knowledge_cutoff for item in window),
                feature_start=features.feature_start,
                entry_date=ordered_dates[trading_index + 1],
                feature_evidence=features,
                eligibility_evidence_digest=sha256_digest(eligibility_payload),
                rank_digest=scan_module._rank_digest(
                    rule_digest=CARD.semantic_digest,
                    snapshot_id=SNAPSHOT_ID,
                    signal_date=current.session_date,
                    security_id=security_id,
                ),
            )
        )
    optimized = scan_module._scan_security_series(
        ordered_rows,
        ordered_dates=ordered_dates,
        date_index=date_index,
        history_sessions=history_sessions,
        snapshot_id=SNAPSHOT_ID,
        snapshot_manifest_digest=SNAPSHOT_MANIFEST,
        card=CARD,
        source_policy=SOURCE,
    )
    return (raw, reasons, evaluated), optimized


def _entry_evidence(
    candidate,
    *,
    state: str,
    source_snapshot_id: str | None = None,
    candidate_snapshot_id: str | None = None,
) -> CandidateEntryEvidence:
    entry_date = candidate.entry_date
    open_at = datetime.combine(entry_date, time(9, 30), SHANGHAI)
    close_at = datetime.combine(entry_date, time(15, 0), SHANGHAI)
    snapshot = SnapshotRef(
        market_date=entry_date,
        knowledge_cutoff=close_at,
        source_revision="synthetic-entry-v1",
        snapshot_id=(
            source_snapshot_id
            or f"synthetic-source.execution.{entry_date.strftime('%Y%m%d')}"
        ),
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
        candidate_snapshot_id=(candidate_snapshot_id or candidate.record.identity.snapshot_id),
        evidence=evidence,
        evidence_digest=sha256_digest(
            {"event_id": candidate.event_id, "state": state}
        ),
        matcher_policy_version="p2-daily-research-v1",
    )


class EventForwardScanTests(unittest.TestCase):
    def test_exact_rolling_scan_matches_reference_for_deterministic_random_series(
        self,
    ) -> None:
        random = Random(195)
        for case in range(6):
            rows = [
                _row(
                    security=f"61{case:04d}",
                    session_date=TRADING_DATES[index],
                    range_fraction=random.choice(
                        (
                            Decimal("0"),
                            Decimal("0.0037"),
                            Decimal("0.008"),
                            Decimal("0.0113"),
                            Decimal("0.026"),
                        )
                    ),
                    turnover=random.choice(
                        (
                            Decimal("17.5"),
                            Decimal("83.01"),
                            Decimal("100"),
                            Decimal("241.9"),
                        )
                    ),
                )
                for index in range(20, 260)
            ]
            for index in random.sample(range(22, len(rows) - 1), 8):
                mutation = random.randrange(5)
                if mutation == 0:
                    rows[index] = replace(rows[index], is_tradable=False)
                elif mutation == 1:
                    rows[index] = replace(rows[index], adjustment_effective=True)
                elif mutation == 2:
                    rows[index] = replace(rows[index], turnover=None)
                elif mutation == 3:
                    rows[index] = replace(rows[index], high_price=Decimal("99"))
                else:
                    rows[index] = replace(
                        rows[index],
                        knowledge_cutoff=datetime(2026, 1, 1, 15, 0, tzinfo=SHANGHAI),
                    )
            rows.pop(random.randrange(60, 180))

            reference, optimized = _reference_security_scan(rows)

            self.assertEqual(optimized, reference)

    def test_exact_rolling_scan_matches_reference_across_reason_boundaries(self) -> None:
        rows = [
            _row(
                security="600099",
                session_date=TRADING_DATES[index],
                range_fraction=(
                    Decimal("0.005")
                    if index % 29 == 27
                    else Decimal("0.02")
                    if index % 29 == 28
                    else Decimal("0.01")
                ),
                turnover=(
                    Decimal("50")
                    if index % 29 == 27
                    else Decimal("200")
                    if index % 29 == 28
                    else Decimal("100")
                ),
            )
            for index in range(20, 240)
        ]
        rows[37] = replace(rows[37], is_tradable=False)
        rows[61] = replace(rows[61], adjustment_effective=True)
        rows[89] = replace(rows[89], open_price=None)
        rows[117] = replace(
            rows[117],
            knowledge_cutoff=datetime(2026, 1, 1, 15, 0, tzinfo=SHANGHAI),
        )
        rows.pop(145)

        reference, optimized = _reference_security_scan(rows)

        self.assertEqual(optimized, reference)

    def test_streaming_series_matches_in_memory_scan_exactly(self) -> None:
        first = _series("600001", signal_indices=(43, 80), industry="industry-01")
        second = _series("000001", signal_indices=(50,), industry="industry-02")
        in_memory = _scan(first + second)
        streamed = scan_forward_candidate_series(
            (tuple(first), tuple(second)),
            trading_dates=TRADING_DATES,
            snapshot_id=SNAPSHOT_ID,
            snapshot_manifest_digest=SNAPSHOT_MANIFEST,
            card=CARD,
            split_policy=SPLIT,
            source_policy=SOURCE,
        )

        self.assertEqual(streamed, in_memory)

    def test_disjoint_scan_shards_finalize_exactly_like_one_stream(self) -> None:
        first = tuple(_series("600001", signal_indices=(43, 80), industry="industry-01"))
        second = tuple(_series("000001", signal_indices=(50,), industry="industry-02"))
        expected = scan_forward_candidate_series(
            (first, second),
            trading_dates=TRADING_DATES,
            snapshot_id=SNAPSHOT_ID,
            snapshot_manifest_digest=SNAPSHOT_MANIFEST,
            card=CARD,
            split_policy=SPLIT,
            source_policy=SOURCE,
        )
        shards = tuple(
            scan_candidate_series_shard(
                (series,),
                trading_dates=TRADING_DATES,
                snapshot_id=SNAPSHOT_ID,
                snapshot_manifest_digest=SNAPSHOT_MANIFEST,
                card=CARD,
                split_policy=SPLIT,
                source_policy=SOURCE,
            )
            for series in (first, second)
        )

        observed = finalize_candidate_scan_shards(
            tuple(reversed(shards)),
            trading_dates=TRADING_DATES,
            snapshot_id=SNAPSHOT_ID,
            snapshot_manifest_digest=SNAPSHOT_MANIFEST,
            card=CARD,
            split_policy=SPLIT,
            source_policy=SOURCE,
        )

        self.assertEqual(observed, expected)

        restored = tuple(CandidateScanShard.from_dict(item.to_dict()) for item in shards)
        self.assertEqual(restored, shards)

        overlapping = (
            shards[0],
            replace(
                shards[1],
                security_ids=tuple(
                    sorted(set(shards[0].security_ids + shards[1].security_ids))
                ),
            ),
        )
        with self.assertRaisesRegex(CandidateScanError, "overlap security ownership"):
            finalize_candidate_scan_shards(
                overlapping,
                trading_dates=TRADING_DATES,
                snapshot_id=SNAPSHOT_ID,
                snapshot_manifest_digest=SNAPSHOT_MANIFEST,
                card=CARD,
                split_policy=SPLIT,
                source_policy=SOURCE,
            )

    def test_streaming_scan_reports_decode_scan_and_finalization_progress(self) -> None:
        rows = tuple(_series("600011", signal_indices=(43, 80)))
        observed: list[tuple[str, dict[str, object]]] = []

        result = scan_forward_candidate_series(
            (rows,),
            trading_dates=TRADING_DATES,
            snapshot_id=SNAPSHOT_ID,
            snapshot_manifest_digest=SNAPSHOT_MANIFEST,
            card=CARD,
            split_policy=SPLIT,
            source_policy=SOURCE,
            progress=lambda stage, details: observed.append((stage, dict(details))),
        )

        terminal = {stage: details for stage, details in observed if details["terminal"]}
        self.assertEqual(set(terminal), {"candidate-scan", "candidate-finalization"})
        self.assertEqual(terminal["candidate-scan"]["rows_decoded"], len(rows))
        self.assertEqual(terminal["candidate-scan"]["worker_count"], 1)
        self.assertEqual(
            terminal["candidate-scan"]["evaluated_rows"], result.counters.evaluated_rows
        )
        self.assertEqual(
            terminal["candidate-finalization"]["candidate_events"],
            result.counters.candidate_events,
        )

    def test_static_industry_is_applied_only_after_raw_trigger(self) -> None:
        rows = [
            replace(row, static_level1_industry_id=None)
            for row in _series("600001", signal_indices=(43,))
        ]

        result = _scan(rows)

        self.assertEqual(result.counters.raw_triggers, 1)
        self.assertEqual(result.counters.isolated_candidates, 1)
        self.assertEqual(result.counters.concentration_retained, 0)
        self.assertEqual(result.counters.candidate_events, 0)
        self.assertEqual(
            result.counters.reason_counts[CandidateReason.STATIC_INDUSTRY_MISSING.value],
            1,
        )

    def test_streaming_series_allows_non_overlapping_relisting_intervals(self) -> None:
        first = tuple(_series("600001", signal_indices=(43,)))
        second = tuple(
            replace(row, listing_id="listing-600001-r2")
            for row in _series("600001", signal_indices=(80,))
        )

        result = scan_forward_candidate_series(
            (first, second),
            trading_dates=TRADING_DATES,
            snapshot_id=SNAPSHOT_ID,
            snapshot_manifest_digest=SNAPSHOT_MANIFEST,
            card=CARD,
            split_policy=SPLIT,
            source_policy=SOURCE,
        )

        self.assertEqual(result.counters.raw_triggers, 2)
        self.assertEqual(result.counters.candidate_events, 2)

    def test_streaming_series_rejects_duplicate_security_chunks(self) -> None:
        series = tuple(_series("600001", signal_indices=(43,)))
        with self.assertRaisesRegex(ValueError, "multiple scan series"):
            scan_forward_candidate_series(
                (series, series),
                trading_dates=TRADING_DATES,
                snapshot_id=SNAPSHOT_ID,
                snapshot_manifest_digest=SNAPSHOT_MANIFEST,
                card=CARD,
                split_policy=SPLIT,
                source_policy=SOURCE,
            )

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
        self.assertNotEqual(
            executable.records[0].entry_source_snapshot_id,
            candidate.record.identity.snapshot_id,
        )

        mismatched_binding = _entry_evidence(
            candidate,
            state="normal",
            candidate_snapshot_id="different-adm-snapshot",
        )
        with self.assertRaisesRegex(
            CandidateScanError,
            "entry evidence candidate snapshot binding differs",
        ):
            partition_executable_entries(
                (candidate,),
                evidence_by_event_id={candidate.event_id: mismatched_binding},
            )

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
