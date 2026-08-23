"""Outcome-blind forward candidate scan and T+1 executable-entry partition."""

from __future__ import annotations

import hashlib
import time
from bisect import bisect_left, insort
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from typing import Any

from quanthus.backtest.contracts import OpenExecutionState
from quanthus.backtest.inputs import (
    P1DailyExecutionEvidence,
    buy_open_rejection_reason,
    market_session_from_p1,
)
from quanthus.event_library.contracts import (
    EventIdentity,
    EventRecord,
    require_sha256_digest,
    sha256_digest,
)
from quanthus.event_library.rule_card import CandidateRuleCard
from quanthus.event_library.source import EventScanSourcePolicy
from quanthus.event_library.splits import SplitPolicy, SplitPolicyError, assign_event_split


class CandidateScanError(ValueError):
    """Raised when candidate scan inputs violate PIT, identity, or reconciliation contracts."""


class CandidateReason(str, Enum):
    OBSERVATION_GAP = "observation_gap"
    VENUE_NOT_ALLOWED = "venue_not_allowed"
    INSTRUMENT_NOT_ALLOWED = "instrument_not_allowed"
    INSUFFICIENT_LISTING_HISTORY = "insufficient_listing_history"
    STATIC_INDUSTRY_MISSING = "static_industry_missing"
    MISSING_INPUT = "missing_input"
    INVALID_OHLC = "invalid_ohlc"
    NON_POSITIVE_TURNOVER = "non_positive_turnover"
    KNOWLEDGE_AFTER_T_CLOSE = "knowledge_after_t_close"
    T_MINUS_1_NOT_TRADABLE = "t_minus_1_not_tradable"
    T_NOT_TRADABLE = "t_not_tradable"
    CORPORATE_ACTION_BOUNDARY = "corporate_action_boundary"
    ZERO_ACTIVITY_BASELINE = "zero_activity_baseline"
    MISSING_T_PLUS_1_SESSION = "missing_t_plus_1_session"
    NOT_TRIGGERED = "not_triggered"
    SAME_SECURITY_ISOLATION = "same_security_isolation"
    CONCENTRATION_REJECTED = "concentration_rejected"
    SPLIT_POLICY_REJECTED = "split_policy_rejected"


class EntryStatus(str, Enum):
    EXECUTABLE = "executable"
    NOT_EXECUTABLE = "not_executable"


def _require_text(value: str | None, field_name: str) -> str:
    if value is None or not value.strip():
        raise CandidateScanError(f"{field_name} must not be empty")
    return value


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CandidateScanError(f"{field_name} must be timezone-aware")


def _fraction(value: Decimal) -> Fraction:
    if not value.is_finite():
        raise CandidateScanError("scan decimals must be finite")
    return Fraction(value)


def _fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _median(values: Sequence[Fraction]) -> Fraction:
    if not values:
        raise CandidateScanError("median requires at least one value")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _rank_digest(
    *,
    rule_digest: str,
    snapshot_id: str,
    signal_date: date,
    security_id: str,
) -> str:
    value = "|".join((rule_digest, snapshot_id, signal_date.isoformat(), security_id))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateDailyInput:
    security_id: str
    listing_id: str
    listing_code: str
    company_id: str
    venue_id: str
    instrument_type: str
    static_level1_industry_id: str | None
    session_date: date
    close_at: datetime
    knowledge_cutoff: datetime
    open_price: Decimal | None
    high_price: Decimal | None
    low_price: Decimal | None
    close_price: Decimal | None
    turnover: Decimal | None
    is_tradable: bool | None
    listing_sessions: int
    adjustment_effective: bool | None
    source_evidence_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "security_id",
            "listing_id",
            "listing_code",
            "company_id",
            "venue_id",
            "instrument_type",
        ):
            _require_text(getattr(self, field_name), field_name)
        _require_aware(self.close_at, "close_at")
        _require_aware(self.knowledge_cutoff, "knowledge_cutoff")
        if self.close_at.date() != self.session_date:
            raise CandidateScanError("close_at date must match session_date")
        if not isinstance(self.listing_sessions, int) or isinstance(self.listing_sessions, bool):
            raise CandidateScanError("listing_sessions must be an integer")
        if self.listing_sessions < 0:
            raise CandidateScanError("listing_sessions must not be negative")
        require_sha256_digest(self.source_evidence_digest, "source_evidence_digest")


@dataclass(frozen=True)
class CandidateFeatureEvidence:
    feature_start: date
    signal_date: date
    t_minus_1_range_activity_ratio: Fraction
    t_minus_1_turnover_activity_ratio: Fraction
    t_range_activity_ratio: Fraction
    t_turnover_activity_ratio: Fraction
    source_evidence_digests: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_start": self.feature_start.isoformat(),
            "signal_date": self.signal_date.isoformat(),
            "t_minus_1_range_activity_ratio": _fraction_text(
                self.t_minus_1_range_activity_ratio
            ),
            "t_minus_1_turnover_activity_ratio": _fraction_text(
                self.t_minus_1_turnover_activity_ratio
            ),
            "t_range_activity_ratio": _fraction_text(self.t_range_activity_ratio),
            "t_turnover_activity_ratio": _fraction_text(self.t_turnover_activity_ratio),
            "source_evidence_digests": list(self.source_evidence_digests),
        }


@dataclass(frozen=True)
class ScannedCandidate:
    record: EventRecord
    listing_id: str
    listing_code: str
    company_id: str
    static_level1_industry_id: str
    feature_start: date
    entry_date: date
    feature_evidence: CandidateFeatureEvidence

    def __post_init__(self) -> None:
        for field_name in (
            "listing_id",
            "listing_code",
            "company_id",
            "static_level1_industry_id",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.feature_start > self.record.identity.signal_date:
            raise CandidateScanError("feature_start must not follow signal date")
        if self.entry_date <= self.record.identity.signal_date:
            raise CandidateScanError("entry_date must follow signal date")

    @property
    def event_id(self) -> str:
        return self.record.event_id


@dataclass(frozen=True)
class CandidateScanCounters:
    evaluated_rows: int
    raw_triggers: int
    isolated_candidates: int
    concentration_retained: int
    candidate_events: int
    reason_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        values = (
            self.evaluated_rows,
            self.raw_triggers,
            self.isolated_candidates,
            self.concentration_retained,
            self.candidate_events,
            *self.reason_counts.values(),
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in values
        ):
            raise CandidateScanError("scan counters must be non-negative integers")
        cooldown = self.reason_counts.get(CandidateReason.SAME_SECURITY_ISOLATION.value, 0)
        concentration = self.reason_counts.get(CandidateReason.CONCENTRATION_REJECTED.value, 0)
        missing_industry = self.reason_counts.get(CandidateReason.STATIC_INDUSTRY_MISSING.value, 0)
        split = self.reason_counts.get(CandidateReason.SPLIT_POLICY_REJECTED.value, 0)
        post_trigger = cooldown + concentration + missing_industry + split
        pre_trigger = sum(self.reason_counts.values()) - post_trigger
        if self.evaluated_rows != self.raw_triggers + pre_trigger:
            raise CandidateScanError("evaluated-row reconciliation differs")
        if self.raw_triggers != self.isolated_candidates + cooldown:
            raise CandidateScanError("raw-trigger reconciliation differs")
        if self.isolated_candidates != (
            self.concentration_retained + concentration + missing_industry
        ):
            raise CandidateScanError("isolation reconciliation differs")
        if self.concentration_retained != self.candidate_events + split:
            raise CandidateScanError("split reconciliation differs")

    def to_dict(self) -> dict[str, object]:
        return {
            "evaluated_rows": self.evaluated_rows,
            "raw_triggers": self.raw_triggers,
            "isolated_candidates": self.isolated_candidates,
            "concentration_retained": self.concentration_retained,
            "candidate_events": self.candidate_events,
            "reason_counts": dict(sorted(self.reason_counts.items())),
        }


@dataclass(frozen=True)
class CandidateIndustryGap:
    security_id: str
    listing_id: str
    listing_code: str
    company_id: str
    isolated_event_count: int

    def __post_init__(self) -> None:
        for field_name in ("security_id", "listing_id", "listing_code", "company_id"):
            _require_text(getattr(self, field_name), field_name)
        if (
            not isinstance(self.isolated_event_count, int)
            or isinstance(self.isolated_event_count, bool)
            or self.isolated_event_count <= 0
        ):
            raise CandidateScanError("industry-gap event count must be a positive integer")


@dataclass(frozen=True)
class CandidateScanResult:
    candidates: tuple[ScannedCandidate, ...]
    counters: CandidateScanCounters
    industry_gaps: tuple[CandidateIndustryGap, ...] = ()

    def __post_init__(self) -> None:
        event_ids = [candidate.event_id for candidate in self.candidates]
        if len(event_ids) != len(set(event_ids)):
            raise CandidateScanError("candidate scan contains duplicate event ids")
        if len(self.candidates) != self.counters.candidate_events:
            raise CandidateScanError("candidate count differs from reconciliation counters")
        gap_keys = [
            (item.security_id, item.listing_id, item.listing_code, item.company_id)
            for item in self.industry_gaps
        ]
        if len(gap_keys) != len(set(gap_keys)):
            raise CandidateScanError("industry-gap identities must be unique")
        gap_event_count = sum(item.isolated_event_count for item in self.industry_gaps)
        expected_gap_events = self.counters.reason_counts.get(
            CandidateReason.STATIC_INDUSTRY_MISSING.value, 0
        )
        if gap_event_count != expected_gap_events:
            raise CandidateScanError("industry-gap diagnostics do not reconcile")


@dataclass(frozen=True)
class CandidateEntryEvidence:
    event_id: str
    candidate_snapshot_id: str
    evidence: P1DailyExecutionEvidence
    evidence_digest: str
    matcher_policy_version: str

    def __post_init__(self) -> None:
        _require_text(self.event_id, "event_id")
        _require_text(self.candidate_snapshot_id, "candidate_snapshot_id")
        require_sha256_digest(self.evidence_digest, "entry evidence digest")
        _require_text(self.matcher_policy_version, "matcher_policy_version")


@dataclass(frozen=True)
class ExecutableCandidate:
    candidate: ScannedCandidate
    status: EntryStatus
    reason_code: str
    open_execution_state: OpenExecutionState
    entry_open_price: Decimal | None
    entry_evidence_digest: str
    entry_source_snapshot_id: str | None
    matcher_policy_version: str

    def __post_init__(self) -> None:
        _require_text(self.reason_code, "reason_code")
        require_sha256_digest(self.entry_evidence_digest, "entry_evidence_digest")
        if self.entry_source_snapshot_id is not None:
            _require_text(self.entry_source_snapshot_id, "entry_source_snapshot_id")
        _require_text(self.matcher_policy_version, "matcher_policy_version")
        if self.status is EntryStatus.EXECUTABLE:
            if self.entry_open_price is None:
                raise CandidateScanError("executable candidate requires an entry open price")
        elif self.entry_open_price is not None:
            raise CandidateScanError("not-executable candidate must not retain an entry price")

    @property
    def event_id(self) -> str:
        return self.candidate.event_id


@dataclass(frozen=True)
class EntryPartitionResult:
    records: tuple[ExecutableCandidate, ...]
    executable_events: int
    not_executable_events: int

    def __post_init__(self) -> None:
        if self.executable_events + self.not_executable_events != len(self.records):
            raise CandidateScanError("entry partition counters do not reconcile")
        event_ids = [record.event_id for record in self.records]
        if len(event_ids) != len(set(event_ids)):
            raise CandidateScanError("entry partition contains duplicate event ids")


@dataclass(frozen=True)
class _RawCandidate:
    security_id: str
    listing_id: str
    listing_code: str
    company_id: str
    industry_id: str | None
    signal_date: date
    signal_close: datetime
    knowledge_cutoff: datetime
    feature_start: date
    entry_date: date
    feature_evidence: CandidateFeatureEvidence
    eligibility_evidence_digest: str
    rank_digest: str


def _window_reason(
    window: Sequence[CandidateDailyInput],
    *,
    expected_dates: Sequence[date],
    card: CandidateRuleCard,
) -> CandidateReason | None:
    if tuple(item.session_date for item in window) != tuple(expected_dates):
        return CandidateReason.OBSERVATION_GAP
    current = window[-1]
    prior = window[-2]
    if current.venue_id not in card.eligibility.allowed_venues:
        return CandidateReason.VENUE_NOT_ALLOWED
    if current.instrument_type not in card.eligibility.instrument_types:
        return CandidateReason.INSTRUMENT_NOT_ALLOWED
    if current.listing_sessions < card.eligibility.minimum_listing_sessions:
        return CandidateReason.INSUFFICIENT_LISTING_HISTORY
    if prior.is_tradable is not True:
        return CandidateReason.T_MINUS_1_NOT_TRADABLE
    if current.is_tradable is not True:
        return CandidateReason.T_NOT_TRADABLE
    if prior.adjustment_effective is not False or current.adjustment_effective is not False:
        return CandidateReason.CORPORATE_ACTION_BOUNDARY
    for item in window:
        if item.knowledge_cutoff > current.close_at:
            return CandidateReason.KNOWLEDGE_AFTER_T_CLOSE
        prices = (item.open_price, item.high_price, item.low_price, item.close_price)
        if any(value is None for value in prices) or item.turnover is None:
            return CandidateReason.MISSING_INPUT
        assert item.open_price is not None
        assert item.high_price is not None
        assert item.low_price is not None
        assert item.close_price is not None
        assert item.turnover is not None
        valid_prices = (
            item.open_price,
            item.high_price,
            item.low_price,
            item.close_price,
        )
        if any(not value.is_finite() or value <= 0 for value in valid_prices):
            return CandidateReason.INVALID_OHLC
        if not (
            item.low_price <= item.open_price <= item.high_price
            and item.low_price <= item.close_price <= item.high_price
        ):
            return CandidateReason.INVALID_OHLC
        if not item.turnover.is_finite() or item.turnover <= 0:
            return CandidateReason.NON_POSITIVE_TURNOVER
    return None


def _feature_evidence(window: Sequence[CandidateDailyInput]) -> CandidateFeatureEvidence | None:
    ranges: list[Fraction] = []
    turnovers: list[Fraction] = []
    for item in window:
        assert item.open_price is not None
        assert item.high_price is not None
        assert item.low_price is not None
        assert item.turnover is not None
        ranges.append(_fraction(item.high_price - item.low_price) / _fraction(item.open_price))
        turnovers.append(_fraction(item.turnover))
    prior_range_median = _median(ranges[:20])
    current_range_median = _median(ranges[1:21])
    prior_turnover_median = _median(turnovers[:20])
    current_turnover_median = _median(turnovers[1:21])
    if 0 in {
        prior_range_median,
        current_range_median,
        prior_turnover_median,
        current_turnover_median,
    }:
        return None
    return CandidateFeatureEvidence(
        feature_start=window[0].session_date,
        signal_date=window[-1].session_date,
        t_minus_1_range_activity_ratio=ranges[-2] / prior_range_median,
        t_minus_1_turnover_activity_ratio=turnovers[-2] / prior_turnover_median,
        t_range_activity_ratio=ranges[-1] / current_range_median,
        t_turnover_activity_ratio=turnovers[-1] / current_turnover_median,
        source_evidence_digests=tuple(item.source_evidence_digest for item in window),
    )


def _static_row_reason(item: CandidateDailyInput) -> CandidateReason | None:
    prices = (item.open_price, item.high_price, item.low_price, item.close_price)
    if any(value is None for value in prices) or item.turnover is None:
        return CandidateReason.MISSING_INPUT
    assert item.open_price is not None
    assert item.high_price is not None
    assert item.low_price is not None
    assert item.close_price is not None
    assert item.turnover is not None
    valid_prices = (
        item.open_price,
        item.high_price,
        item.low_price,
        item.close_price,
    )
    if any(not value.is_finite() or value <= 0 for value in valid_prices):
        return CandidateReason.INVALID_OHLC
    if not (
        item.low_price <= item.open_price <= item.high_price
        and item.low_price <= item.close_price <= item.high_price
    ):
        return CandidateReason.INVALID_OHLC
    if not item.turnover.is_finite() or item.turnover <= 0:
        return CandidateReason.NON_POSITIVE_TURNOVER
    return None


def _rolling_medians(
    values: Sequence[Fraction | None], *, window_size: int
) -> tuple[Fraction | None, ...]:
    """Return exact trailing medians while retaining invalid-window boundaries."""

    if window_size <= 0:
        raise ValueError("rolling median window must be positive")
    ordered: list[Fraction] = []
    invalid = 0
    medians: list[Fraction | None] = []
    for index, value in enumerate(values):
        if value is None:
            invalid += 1
        else:
            insort(ordered, value)
        if index >= window_size:
            expired = values[index - window_size]
            if expired is None:
                invalid -= 1
            else:
                position = bisect_left(ordered, expired)
                if position >= len(ordered) or ordered[position] != expired:
                    raise CandidateScanError("rolling median state differs")
                ordered.pop(position)
        if index + 1 < window_size or invalid:
            medians.append(None)
            continue
        middle = window_size // 2
        median = (
            ordered[middle]
            if window_size % 2
            else (ordered[middle - 1] + ordered[middle]) / 2
        )
        medians.append(median)
    return tuple(medians)


def _exact_series_values(
    rows: Sequence[CandidateDailyInput],
) -> tuple[
    tuple[CandidateReason | None, ...],
    tuple[Fraction | None, ...],
    tuple[Fraction | None, ...],
    tuple[Fraction | None, ...],
    tuple[Fraction | None, ...],
]:
    row_reasons: list[CandidateReason | None] = []
    ranges: list[Fraction | None] = []
    turnovers: list[Fraction | None] = []
    for item in rows:
        reason = _static_row_reason(item)
        row_reasons.append(reason)
        if reason is not None:
            ranges.append(None)
            turnovers.append(None)
            continue
        assert item.open_price is not None
        assert item.high_price is not None
        assert item.low_price is not None
        assert item.turnover is not None
        ranges.append(_fraction(item.high_price - item.low_price) / _fraction(item.open_price))
        turnovers.append(_fraction(item.turnover))
    return (
        tuple(row_reasons),
        tuple(ranges),
        tuple(turnovers),
        _rolling_medians(ranges, window_size=20),
        _rolling_medians(turnovers, window_size=20),
    )


def _precomputed_window_reason(
    rows: Sequence[CandidateDailyInput],
    row_reasons: Sequence[CandidateReason | None],
    *,
    first_index: int,
    current_index: int,
    expected_dates: Sequence[date],
    card: CandidateRuleCard,
) -> CandidateReason | None:
    window = rows[first_index : current_index + 1]
    if tuple(item.session_date for item in window) != tuple(expected_dates):
        return CandidateReason.OBSERVATION_GAP
    current = rows[current_index]
    prior = rows[current_index - 1]
    if current.venue_id not in card.eligibility.allowed_venues:
        return CandidateReason.VENUE_NOT_ALLOWED
    if current.instrument_type not in card.eligibility.instrument_types:
        return CandidateReason.INSTRUMENT_NOT_ALLOWED
    if current.listing_sessions < card.eligibility.minimum_listing_sessions:
        return CandidateReason.INSUFFICIENT_LISTING_HISTORY
    if prior.is_tradable is not True:
        return CandidateReason.T_MINUS_1_NOT_TRADABLE
    if current.is_tradable is not True:
        return CandidateReason.T_NOT_TRADABLE
    if prior.adjustment_effective is not False or current.adjustment_effective is not False:
        return CandidateReason.CORPORATE_ACTION_BOUNDARY
    for index in range(first_index, current_index + 1):
        if rows[index].knowledge_cutoff > current.close_at:
            return CandidateReason.KNOWLEDGE_AFTER_T_CLOSE
        reason = row_reasons[index]
        if reason is not None:
            return reason
    return None


def _triggered(features: CandidateFeatureEvidence, card: CandidateRuleCard) -> bool:
    thresholds = {item.threshold_id: Fraction(item.value) for item in card.thresholds}
    return (
        features.t_minus_1_range_activity_ratio <= thresholds["quiet_range_ratio_max"]
        and features.t_minus_1_turnover_activity_ratio
        <= thresholds["quiet_turnover_ratio_max"]
        and features.t_range_activity_ratio >= thresholds["active_range_ratio_min"]
        and features.t_turnover_activity_ratio >= thresholds["active_turnover_ratio_min"]
    )


def _scan_context(
    *,
    trading_dates: Sequence[date],
    snapshot_id: str,
    snapshot_manifest_digest: str,
    card: CandidateRuleCard,
    split_policy: SplitPolicy,
    source_policy: EventScanSourcePolicy,
) -> tuple[tuple[date, ...], dict[date, int], int]:
    card.require_stage_b_authority()
    split_policy.require_stage_b_authority()
    _require_text(snapshot_id, "snapshot_id")
    require_sha256_digest(snapshot_manifest_digest, "snapshot_manifest_digest")
    ordered_dates = tuple(trading_dates)
    if not ordered_dates or tuple(sorted(set(ordered_dates))) != ordered_dates:
        raise CandidateScanError("trading_dates must be sorted and unique")
    for value in ordered_dates:
        source_policy.require_development_date(value)
    history_sessions = card.eligibility.minimum_history_sessions
    if history_sessions != 22:
        raise CandidateScanError("approved candidate implementation requires 22-session history")
    return (
        ordered_dates,
        {value: index for index, value in enumerate(ordered_dates)},
        history_sessions,
    )


def _scan_security_series(
    security_rows: Sequence[CandidateDailyInput],
    *,
    ordered_dates: Sequence[date],
    date_index: Mapping[date, int],
    history_sessions: int,
    snapshot_id: str,
    snapshot_manifest_digest: str,
    card: CandidateRuleCard,
    source_policy: EventScanSourcePolicy,
) -> tuple[list[_RawCandidate], Counter[str], int]:
    if not security_rows:
        return [], Counter(), 0
    rows = tuple(security_rows)
    if tuple(sorted(rows, key=lambda item: item.session_date)) != rows:
        raise CandidateScanError("one security scan series must be sorted by session_date")
    security_ids = {item.security_id for item in rows}
    if len(security_ids) != 1:
        raise CandidateScanError("one scan series must contain exactly one security_id")
    session_dates = [item.session_date for item in rows]
    if len(session_dates) != len(set(session_dates)):
        raise CandidateScanError("candidate input contains duplicate security-date rows")
    for row in rows:
        source_policy.require_development_date(row.session_date)
    series_identities = {(item.listing_id, item.listing_code, item.company_id) for item in rows}
    if len(series_identities) != 1:
        raise CandidateScanError("one scan series must retain one listing/company identity")

    security_id = rows[0].security_id
    reasons: Counter[str] = Counter()
    raw: list[_RawCandidate] = []
    evaluated = 0
    (
        row_reasons,
        ranges,
        turnovers,
        range_medians,
        turnover_medians,
    ) = _exact_series_values(rows)
    thresholds = {item.threshold_id: Fraction(item.value) for item in card.thresholds}
    for local_index in range(history_sessions - 1, len(rows)):
        current = rows[local_index]
        evaluated += 1
        trading_index = date_index.get(current.session_date)
        if trading_index is None or trading_index < history_sessions - 1:
            reasons[CandidateReason.OBSERVATION_GAP.value] += 1
            continue
        if trading_index + 1 >= len(ordered_dates):
            reasons[CandidateReason.MISSING_T_PLUS_1_SESSION.value] += 1
            continue
        first_index = local_index - history_sessions + 1
        window = rows[first_index : local_index + 1]
        expected_dates = ordered_dates[trading_index - history_sessions + 1 : trading_index + 1]
        reason = _precomputed_window_reason(
            rows,
            row_reasons,
            first_index=first_index,
            current_index=local_index,
            expected_dates=expected_dates,
            card=card,
        )
        if reason is not None:
            reasons[reason.value] += 1
            continue
        prior_range = ranges[local_index - 1]
        current_range = ranges[local_index]
        prior_turnover = turnovers[local_index - 1]
        current_turnover = turnovers[local_index]
        prior_range_median = range_medians[local_index - 2]
        current_range_median = range_medians[local_index - 1]
        prior_turnover_median = turnover_medians[local_index - 2]
        current_turnover_median = turnover_medians[local_index - 1]
        if any(
            value is None
            for value in (
                prior_range,
                current_range,
                prior_turnover,
                current_turnover,
                prior_range_median,
                current_range_median,
                prior_turnover_median,
                current_turnover_median,
            )
        ):
            raise CandidateScanError("precomputed exact feature window differs")
        assert prior_range is not None
        assert current_range is not None
        assert prior_turnover is not None
        assert current_turnover is not None
        assert prior_range_median is not None
        assert current_range_median is not None
        assert prior_turnover_median is not None
        assert current_turnover_median is not None
        if 0 in {
            prior_range_median,
            current_range_median,
            prior_turnover_median,
            current_turnover_median,
        }:
            reasons[CandidateReason.ZERO_ACTIVITY_BASELINE.value] += 1
            continue
        prior_range_ratio = prior_range / prior_range_median
        prior_turnover_ratio = prior_turnover / prior_turnover_median
        current_range_ratio = current_range / current_range_median
        current_turnover_ratio = current_turnover / current_turnover_median
        if not (
            prior_range_ratio <= thresholds["quiet_range_ratio_max"]
            and prior_turnover_ratio <= thresholds["quiet_turnover_ratio_max"]
            and current_range_ratio >= thresholds["active_range_ratio_min"]
            and current_turnover_ratio >= thresholds["active_turnover_ratio_min"]
        ):
            reasons[CandidateReason.NOT_TRIGGERED.value] += 1
            continue
        features = CandidateFeatureEvidence(
            feature_start=window[0].session_date,
            signal_date=current.session_date,
            t_minus_1_range_activity_ratio=prior_range_ratio,
            t_minus_1_turnover_activity_ratio=prior_turnover_ratio,
            t_range_activity_ratio=current_range_ratio,
            t_turnover_activity_ratio=current_turnover_ratio,
            source_evidence_digests=tuple(item.source_evidence_digest for item in window),
        )
        eligibility_payload: dict[str, Any] = {
            "security_id": security_id,
            "listing_id": current.listing_id,
            "signal_date": current.session_date.isoformat(),
            "source_evidence_digests": list(features.source_evidence_digests),
            "candidate_rule_digest": card.semantic_digest,
            "snapshot_manifest_digest": snapshot_manifest_digest,
        }
        raw.append(
            _RawCandidate(
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
                rank_digest=_rank_digest(
                    rule_digest=card.semantic_digest,
                    snapshot_id=snapshot_id,
                    signal_date=current.session_date,
                    security_id=security_id,
                ),
            )
        )
    return raw, reasons, evaluated


def _finalize_raw_candidates(
    raw: Sequence[_RawCandidate],
    *,
    reasons: Counter[str],
    evaluated: int,
    ordered_dates: Sequence[date],
    date_index: Mapping[date, int],
    snapshot_id: str,
    snapshot_manifest_digest: str,
    card: CandidateRuleCard,
    split_policy: SplitPolicy,
) -> CandidateScanResult:
    ordered_raw = sorted(raw, key=lambda item: (item.signal_date, item.security_id))
    isolated: list[_RawCandidate] = []
    last_accepted_index: dict[str, int] = {}
    separation = max(
        card.isolation.cooldown_sessions,
        card.isolation.same_security_minimum_separation_sessions,
    )
    for candidate in ordered_raw:
        index = date_index[candidate.signal_date]
        previous = last_accepted_index.get(candidate.security_id)
        if previous is not None and index - previous < separation:
            reasons[CandidateReason.SAME_SECURITY_ISOLATION.value] += 1
            continue
        last_accepted_index[candidate.security_id] = index
        isolated.append(candidate)

    concentration_pool: list[_RawCandidate] = []
    industry_gap_counts: Counter[tuple[str, str, str, str]] = Counter()
    for candidate in isolated:
        if candidate.industry_id is None or not candidate.industry_id.strip():
            reasons[CandidateReason.STATIC_INDUSTRY_MISSING.value] += 1
            industry_gap_counts[
                (
                    candidate.security_id,
                    candidate.listing_id,
                    candidate.listing_code,
                    candidate.company_id,
                )
            ] += 1
            continue
        concentration_pool.append(candidate)

    daily_groups: dict[date, list[_RawCandidate]] = defaultdict(list)
    wave_groups: dict[tuple[int, str], list[_RawCandidate]] = defaultdict(list)
    for candidate in concentration_pool:
        assert candidate.industry_id is not None
        daily_groups[candidate.signal_date].append(candidate)
        wave = date_index[candidate.signal_date] // card.concentration.wave_sessions
        wave_groups[(wave, candidate.industry_id)].append(candidate)
    daily_allowed: set[tuple[str, date]] = set()
    for candidates in daily_groups.values():
        ranked = sorted(candidates, key=lambda item: (item.rank_digest, item.security_id))
        for candidate in ranked[: card.concentration.maximum_events_per_signal_date]:
            daily_allowed.add((candidate.security_id, candidate.signal_date))
    wave_allowed: set[tuple[str, date]] = set()
    for candidates in wave_groups.values():
        ranked = sorted(candidates, key=lambda item: (item.rank_digest, item.security_id))
        for candidate in ranked[: card.concentration.maximum_events_per_group_per_wave]:
            wave_allowed.add((candidate.security_id, candidate.signal_date))
    retained: list[_RawCandidate] = []
    for candidate in concentration_pool:
        key = (candidate.security_id, candidate.signal_date)
        if key not in daily_allowed or key not in wave_allowed:
            reasons[CandidateReason.CONCENTRATION_REJECTED.value] += 1
            continue
        retained.append(candidate)

    events: list[ScannedCandidate] = []
    for candidate in retained:
        try:
            assignment = assign_event_split(
                split_policy,
                signal_date=candidate.signal_date,
                feature_start=candidate.feature_start,
                outcome_end=candidate.entry_date,
                trading_dates=ordered_dates,
            )
        except SplitPolicyError:
            reasons[CandidateReason.SPLIT_POLICY_REJECTED.value] += 1
            continue
        event_identity = EventIdentity(
            security_id=candidate.security_id,
            signal_date=candidate.signal_date,
            candidate_rule_id=card.rule_id,
            candidate_rule_version=card.rule_version,
            snapshot_id=snapshot_id,
        )
        feature_digest = sha256_digest(candidate.feature_evidence.to_dict())
        isolation_digest = sha256_digest(
            {
                "security_id": candidate.security_id,
                "feature_start": candidate.feature_start.isoformat(),
                "signal_date": candidate.signal_date.isoformat(),
                "separation_sessions": separation,
            }
        )
        record = EventRecord(
            identity=event_identity,
            candidate_rule_digest=card.semantic_digest,
            snapshot_manifest_digest=snapshot_manifest_digest,
            knowledge_cutoff=candidate.knowledge_cutoff,
            signal_close=candidate.signal_close,
            eligibility_evidence_digest=candidate.eligibility_evidence_digest,
            feature_window_manifest_digest=feature_digest,
            isolation_group_id=f"isolation:{isolation_digest.removeprefix('sha256:')}",
            split_policy_id=split_policy.policy_id,
            split_policy_version=split_policy.policy_version,
            split_policy_digest=split_policy.semantic_digest,
            split_assignment=assignment,
        )
        assert candidate.industry_id is not None
        events.append(
            ScannedCandidate(
                record=record,
                listing_id=candidate.listing_id,
                listing_code=candidate.listing_code,
                company_id=candidate.company_id,
                static_level1_industry_id=candidate.industry_id,
                feature_start=candidate.feature_start,
                entry_date=candidate.entry_date,
                feature_evidence=candidate.feature_evidence,
            )
        )

    counters = CandidateScanCounters(
        evaluated_rows=evaluated,
        raw_triggers=len(ordered_raw),
        isolated_candidates=len(isolated),
        concentration_retained=len(retained),
        candidate_events=len(events),
        reason_counts=dict(reasons),
    )
    return CandidateScanResult(
        candidates=tuple(
            sorted(
                events,
                key=lambda item: (
                    item.record.identity.signal_date,
                    item.record.identity.security_id,
                ),
            )
        ),
        counters=counters,
        industry_gaps=tuple(
            CandidateIndustryGap(
                security_id=security_id,
                listing_id=listing_id,
                listing_code=listing_code,
                company_id=company_id,
                isolated_event_count=count,
            )
            for (security_id, listing_id, listing_code, company_id), count in sorted(
                industry_gap_counts.items()
            )
        ),
    )


def scan_forward_candidate_series(
    series: Iterable[Sequence[CandidateDailyInput]],
    *,
    trading_dates: Sequence[date],
    snapshot_id: str,
    snapshot_manifest_digest: str,
    card: CandidateRuleCard,
    split_policy: SplitPolicy,
    source_policy: EventScanSourcePolicy,
    progress: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> CandidateScanResult:
    """Scan sorted one-security series while keeping only triggers globally in memory."""

    ordered_dates, date_index, history_sessions = _scan_context(
        trading_dates=trading_dates,
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        card=card,
        split_policy=split_policy,
        source_policy=source_policy,
    )
    reasons: Counter[str] = Counter()
    raw: list[_RawCandidate] = []
    evaluated = 0
    last_series_end: dict[str, date] = {}
    series_count = 0
    decoded_rows = 0
    decode_elapsed = 0.0
    scan_elapsed = 0.0
    series_iterator = iter(series)
    while True:
        decode_started = time.perf_counter()
        try:
            security_rows = next(series_iterator)
        except StopIteration:
            break
        rows = tuple(security_rows)
        decode_elapsed += time.perf_counter() - decode_started
        if not rows:
            continue
        decoded_rows += len(rows)
        security_id = rows[0].security_id
        previous_end = last_series_end.get(security_id)
        if previous_end is not None and rows[0].session_date <= previous_end:
            raise CandidateScanError(
                "multiple scan series for one security_id must be ordered and non-overlapping"
            )
        last_series_end[security_id] = rows[-1].session_date
        scan_started = time.perf_counter()
        child_raw, child_reasons, child_evaluated = _scan_security_series(
            rows,
            ordered_dates=ordered_dates,
            date_index=date_index,
            history_sessions=history_sessions,
            snapshot_id=snapshot_id,
            snapshot_manifest_digest=snapshot_manifest_digest,
            card=card,
            source_policy=source_policy,
        )
        scan_elapsed += time.perf_counter() - scan_started
        raw.extend(child_raw)
        reasons.update(child_reasons)
        evaluated += child_evaluated
        series_count += 1
        if progress is not None and series_count % 100 == 0:
            progress(
                "candidate-scan",
                {
                    "terminal": False,
                    "series_completed": series_count,
                    "rows_decoded": decoded_rows,
                    "evaluated_rows": evaluated,
                    "raw_triggers": len(raw),
                    "worker_count": 1,
                    "row_decode_elapsed_seconds": round(decode_elapsed, 3),
                    "security_scan_elapsed_seconds": round(scan_elapsed, 3),
                },
            )
    if progress is not None:
        progress(
            "candidate-scan",
            {
                "terminal": True,
                "series_completed": series_count,
                "rows_decoded": decoded_rows,
                "evaluated_rows": evaluated,
                "raw_triggers": len(raw),
                "worker_count": 1,
                "row_decode_elapsed_seconds": round(decode_elapsed, 3),
                "security_scan_elapsed_seconds": round(scan_elapsed, 3),
            },
        )
    finalization_started = time.perf_counter()
    result = _finalize_raw_candidates(
        raw,
        reasons=reasons,
        evaluated=evaluated,
        ordered_dates=ordered_dates,
        date_index=date_index,
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        card=card,
        split_policy=split_policy,
    )
    if progress is not None:
        progress(
            "candidate-finalization",
            {
                "terminal": True,
                "elapsed_seconds": round(time.perf_counter() - finalization_started, 3),
                "raw_triggers": result.counters.raw_triggers,
                "isolated_candidates": result.counters.isolated_candidates,
                "concentration_retained": result.counters.concentration_retained,
                "candidate_events": result.counters.candidate_events,
            },
        )
    return result


def scan_forward_candidates(
    rows: Sequence[CandidateDailyInput],
    *,
    trading_dates: Sequence[date],
    snapshot_id: str,
    snapshot_manifest_digest: str,
    card: CandidateRuleCard,
    split_policy: SplitPolicy,
    source_policy: EventScanSourcePolicy,
) -> CandidateScanResult:
    """Scan an in-memory corpus; production callers should prefer the series-streaming API."""

    grouped: dict[tuple[str, str, str, str], list[CandidateDailyInput]] = defaultdict(list)
    for row in rows:
        key = (row.security_id, row.listing_id, row.listing_code, row.company_id)
        grouped[key].append(row)
    return scan_forward_candidate_series(
        (
            tuple(sorted(grouped[key], key=lambda item: item.session_date))
            for key in sorted(grouped)
        ),
        trading_dates=trading_dates,
        snapshot_id=snapshot_id,
        snapshot_manifest_digest=snapshot_manifest_digest,
        card=card,
        split_policy=split_policy,
        source_policy=source_policy,
    )


def partition_executable_entries(
    candidates: Sequence[ScannedCandidate],
    *,
    evidence_by_event_id: Mapping[str, CandidateEntryEvidence],
) -> EntryPartitionResult:
    """Partition candidates through the accepted P1-to-P2 buy-open contract."""

    candidate_ids = {candidate.event_id for candidate in candidates}
    unexpected = sorted(set(evidence_by_event_id).difference(candidate_ids))
    if unexpected:
        raise CandidateScanError(f"entry evidence contains unknown event ids: {unexpected}")
    records: list[ExecutableCandidate] = []
    executable = 0
    not_executable = 0
    for candidate in sorted(candidates, key=lambda item: item.event_id):
        binding = evidence_by_event_id.get(candidate.event_id)
        if binding is None:
            records.append(
                ExecutableCandidate(
                    candidate=candidate,
                    status=EntryStatus.NOT_EXECUTABLE,
                    reason_code="missing_execution_evidence",
                    open_execution_state=OpenExecutionState.MISSING_EVIDENCE,
                    entry_open_price=None,
                    entry_evidence_digest=sha256_digest(
                        {
                            "event_id": candidate.event_id,
                            "entry_date": candidate.entry_date.isoformat(),
                            "reason_code": "missing_execution_evidence",
                        }
                    ),
                    entry_source_snapshot_id=None,
                    matcher_policy_version="p2-daily-research-v1",
                )
            )
            not_executable += 1
            continue
        if binding.event_id != candidate.event_id:
            raise CandidateScanError("entry evidence key and event_id differ")
        evidence = binding.evidence
        if evidence.snapshot.market_date != candidate.entry_date:
            raise CandidateScanError("entry evidence does not belong to the candidate T+1 date")
        if binding.candidate_snapshot_id != candidate.record.identity.snapshot_id:
            raise CandidateScanError("entry evidence candidate snapshot binding differs")
        if evidence.security_code != candidate.listing_code:
            raise CandidateScanError("entry evidence security code differs from candidate listing")
        session = market_session_from_p1(evidence)
        rejection = buy_open_rejection_reason(session.open_execution_state)
        if rejection is None:
            if session.open_price is None:
                raise CandidateScanError("P2-executable entry is missing an open price")
            status = EntryStatus.EXECUTABLE
            reason_code = "executable_at_t_plus_1_open"
            entry_price = session.open_price
            executable += 1
        else:
            status = EntryStatus.NOT_EXECUTABLE
            reason_code = rejection
            entry_price = None
            not_executable += 1
        records.append(
            ExecutableCandidate(
                candidate=candidate,
                status=status,
                reason_code=reason_code,
                open_execution_state=session.open_execution_state,
                entry_open_price=entry_price,
                entry_evidence_digest=binding.evidence_digest,
                entry_source_snapshot_id=evidence.snapshot.snapshot_id,
                matcher_policy_version=binding.matcher_policy_version,
            )
        )
    return EntryPartitionResult(
        records=tuple(records),
        executable_events=executable,
        not_executable_events=not_executable,
    )
