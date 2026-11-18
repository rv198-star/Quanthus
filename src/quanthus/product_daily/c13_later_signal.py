"""Pure synthetic C13 later-signal decision before any reservation or execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from zoneinfo import ZoneInfo

from quanthus.shared.fact_contracts import FactError, digest

NEXT_SYNTHETIC_OPEN_WINDOW = "next-adopted-open-v1"


class LaterSignalStatus(str, Enum):
    ORIGINAL_T1_UNCHANGED = "ORIGINAL_T1_UNCHANGED"
    OBSERVATION_ONLY = "OBSERVATION_ONLY"
    BLOCK_LATER_ATTEMPT = "BLOCK_LATER_ATTEMPT"
    ELIGIBLE_FOR_ONE_ATTEMPT = "ELIGIBLE_FOR_ONE_ATTEMPT"
    TERMINAL_NO_REARM = "TERMINAL_NO_REARM"


class Eligibility(str, Enum):
    UNKNOWN = "UNKNOWN"
    EXPLICIT_FALSE = "EXPLICIT_FALSE"
    EXPLICIT_TRUE = "EXPLICIT_TRUE"


class SignalCause(str, Enum):
    QUALIFYING_RULE_TRANSITION = "QUALIFYING_RULE_TRANSITION"
    FUNDING_OR_PRICE_REFRESH = "FUNDING_OR_PRICE_REFRESH"
    SAME_SESSION_CORRECTION = "SAME_SESSION_CORRECTION"


class OriginalAttemptState(str, Enum):
    NEVER_SUBMITTED = "NEVER_SUBMITTED"
    TERMINAL_ZERO_FILL_RECONCILED = "TERMINAL_ZERO_FILL_RECONCILED"
    PENDING = "PENDING"
    ANY_FILL = "ANY_FILL"
    HELD = "HELD"
    EXITING = "EXITING"
    OPEN_FEE = "OPEN_FEE"
    UNRESOLVED_CORRECTION = "UNRESOLVED_CORRECTION"
    UNSETTLED = "UNSETTLED"

    @property
    def permits_later_attempt(self) -> bool:
        return self in {
            OriginalAttemptState.NEVER_SUBMITTED,
            OriginalAttemptState.TERMINAL_ZERO_FILL_RECONCILED,
        }


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FactError("INVALID_C13_INPUT", f"{name} must be non-empty text")
    return value


def _require_aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FactError("INVALID_C13_INPUT", f"{name} must be timezone-aware")
    return value


def _require_exact_int(value: object, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise FactError("INVALID_C13_INPUT", f"{name} must be an integer")
    return value


def parent_opportunity_identity(
    event_id: str, source_snapshot_id: str, candidate_rule_digest: str
) -> str:
    return str(
        digest(
            {
                "event_id": _require_text(event_id, "event_id"),
                "source_snapshot_id": _require_text(source_snapshot_id, "source_snapshot_id"),
                "candidate_rule_digest": _require_text(
                    candidate_rule_digest, "candidate_rule_digest"
                ),
            }
        )
    )


@dataclass(frozen=True)
class OriginalOpportunity:
    opportunity_id: str
    event_id: str
    source_snapshot_id: str
    candidate_rule_digest: str
    account_id: str
    security_id: str
    original_window_end: datetime
    original_window_closed: bool
    original_attempt_state: OriginalAttemptState

    def __post_init__(self) -> None:
        for name in (
            "opportunity_id",
            "event_id",
            "source_snapshot_id",
            "candidate_rule_digest",
            "account_id",
            "security_id",
        ):
            _require_text(getattr(self, name), name)
        _require_aware(self.original_window_end, "original_window_end")
        if not isinstance(self.original_window_closed, bool):
            raise FactError("INVALID_C13_INPUT", "original_window_closed must be boolean")
        if not isinstance(self.original_attempt_state, OriginalAttemptState):
            raise FactError("INVALID_C13_INPUT", "original_attempt_state must be typed")
        if self.opportunity_id != self.identity:
            raise FactError("INVALID_C13_INPUT", "original opportunity identity does not match")

    @property
    def identity(self) -> str:
        return parent_opportunity_identity(
            self.event_id, self.source_snapshot_id, self.candidate_rule_digest
        )


@dataclass(frozen=True)
class SignalObservation:
    parent_opportunity_id: str
    security_id: str
    rule_version: str
    producer_version: str
    source_version: str
    evidence_id: str
    signal_session: date
    feature_session: date
    evidence_at: datetime
    available_at: datetime
    decision_cutoff: datetime
    eligibility: Eligibility
    cause: SignalCause = SignalCause.QUALIFYING_RULE_TRANSITION

    def __post_init__(self) -> None:
        for name in (
            "parent_opportunity_id",
            "security_id",
            "rule_version",
            "producer_version",
            "source_version",
            "evidence_id",
        ):
            _require_text(getattr(self, name), name)
        if not isinstance(self.signal_session, date) or isinstance(self.signal_session, datetime):
            raise FactError("INVALID_C13_INPUT", "signal_session must be a date")
        if not isinstance(self.feature_session, date) or isinstance(self.feature_session, datetime):
            raise FactError("INVALID_C13_INPUT", "feature_session must be a date")
        _require_aware(self.evidence_at, "evidence_at")
        _require_aware(self.available_at, "available_at")
        _require_aware(self.decision_cutoff, "decision_cutoff")
        if not isinstance(self.eligibility, Eligibility):
            raise FactError("INVALID_C13_INPUT", "eligibility must be typed")
        if not isinstance(self.cause, SignalCause):
            raise FactError("INVALID_C13_INPUT", "cause must be typed")


@dataclass(frozen=True)
class LaterSignalPolicy:
    policy_version: str
    producer_version: str
    predicate_version: str
    calendar_version: str
    timezone_name: str
    observation_lifetime_sessions: int
    execution_window_id: str
    zero_fill_retry_policy_version: str
    expiry: datetime
    attempt_limit: int = 1

    def __post_init__(self) -> None:
        for name in (
            "policy_version",
            "producer_version",
            "predicate_version",
            "calendar_version",
            "timezone_name",
            "execution_window_id",
            "zero_fill_retry_policy_version",
        ):
            _require_text(getattr(self, name), name)
        ZoneInfo(self.timezone_name)
        _require_exact_int(
            self.observation_lifetime_sessions, "observation_lifetime_sessions", minimum=1
        )
        if _require_exact_int(self.attempt_limit, "attempt_limit", minimum=1) != 1:
            raise FactError("INVALID_C13_POLICY", "later-attempt limit must be one")
        _require_aware(self.expiry, "expiry")

    @property
    def digest(self) -> str:
        return str(
            digest(
                {
                    "policy_version": self.policy_version,
                    "producer_version": self.producer_version,
                    "predicate_version": self.predicate_version,
                    "calendar_version": self.calendar_version,
                    "timezone_name": self.timezone_name,
                    "observation_lifetime_sessions": self.observation_lifetime_sessions,
                    "execution_window_id": self.execution_window_id,
                    "zero_fill_retry_policy_version": self.zero_fill_retry_policy_version,
                    "expiry": self.expiry.isoformat(),
                    "attempt_limit": self.attempt_limit,
                }
            )
        )


@dataclass(frozen=True)
class SyntheticCalendar:
    version: str
    timezone_name: str
    sessions: tuple[date, ...]
    opens: tuple[datetime, ...]
    closes: tuple[datetime, ...]
    execution_window_id: str

    def __post_init__(self) -> None:
        _require_text(self.version, "calendar version")
        _require_text(self.execution_window_id, "calendar execution window")
        zone = ZoneInfo(_require_text(self.timezone_name, "calendar timezone"))
        if not self.sessions or not len(self.sessions) == len(self.opens) == len(self.closes):
            raise FactError("INVALID_C13_CALENDAR", "calendar rows must align")
        if any(type(session) is not date for session in self.sessions):
            raise FactError("INVALID_C13_CALENDAR", "calendar sessions must be dates")
        if tuple(sorted(self.sessions)) != self.sessions or len(set(self.sessions)) != len(
            self.sessions
        ):
            raise FactError("INVALID_C13_CALENDAR", "calendar sessions must be unique and ordered")
        for session, opening, closing in zip(self.sessions, self.opens, self.closes, strict=True):
            _require_aware(opening, "calendar opening")
            _require_aware(closing, "calendar closing")
            if (
                opening.astimezone(zone).date() != session
                or closing.astimezone(zone).date() != session
                or opening >= closing
            ):
                raise FactError("INVALID_C13_CALENDAR", "session clock or open/close order invalid")

    def preceding_session(self, session: date) -> date | None:
        try:
            index = self.sessions.index(session)
        except ValueError:
            return None
        return self.sessions[index - 1] if index > 0 else None

    def following_open(self, session: date) -> datetime | None:
        index = self.sessions.index(session) + 1
        return self.opens[index] if index < len(self.opens) else None

    def closing_time(self, session: date) -> datetime:
        return self.closes[self.sessions.index(session)]

    def session_distance(self, start: date, end: date) -> int | None:
        try:
            return self.sessions.index(end) - self.sessions.index(start)
        except ValueError:
            return None


@dataclass(frozen=True)
class AttemptSlot:
    account_id: str
    parent_opportunity_id: str

    def __post_init__(self) -> None:
        _require_text(self.account_id, "account_id")
        _require_text(self.parent_opportunity_id, "parent_opportunity_id")

    @property
    def identity(self) -> str:
        return str(digest([self.account_id, self.parent_opportunity_id]))


@dataclass(frozen=True)
class PlanningAttempt:
    slot: AttemptSlot
    later_signal_id: str
    policy_version: str
    execution_window_id: str
    execution_at: datetime

    @property
    def identity(self) -> str:
        return str(
            digest(
                [
                    self.slot.identity,
                    self.later_signal_id,
                    self.policy_version,
                    self.execution_window_id,
                    self.execution_at.astimezone(timezone.utc).isoformat(),
                ]
            )
        )


@dataclass(frozen=True)
class LaterSignalDecision:
    status: LaterSignalStatus
    reason: str
    slot: AttemptSlot | None = None
    candidate: PlanningAttempt | None = None


def _observation_key(signal: SignalObservation) -> dict[str, object]:
    return {
        "parent_opportunity_id": signal.parent_opportunity_id,
        "security_id": signal.security_id,
        "rule_version": signal.rule_version,
        "producer_version": signal.producer_version,
        "source_version": signal.source_version,
        "evidence_id": signal.evidence_id,
        "signal_session": signal.signal_session.isoformat(),
        "feature_session": signal.feature_session.isoformat(),
        "evidence_at": signal.evidence_at.astimezone(timezone.utc).isoformat(),
        "available_at": signal.available_at.astimezone(timezone.utc).isoformat(),
        "decision_cutoff": signal.decision_cutoff.astimezone(timezone.utc).isoformat(),
        "eligibility": signal.eligibility.value,
        "cause": signal.cause.value,
    }


def evaluate_later_signal(
    *,
    opportunity: OriginalOpportunity,
    prior: SignalObservation | None,
    current: SignalObservation,
    policy: LaterSignalPolicy | None,
    calendar: SyntheticCalendar,
) -> LaterSignalDecision:
    """Return a pure later-signal decision; never reserve, persist, or authorize execution."""

    if not isinstance(opportunity, OriginalOpportunity) or not isinstance(
        current, SignalObservation
    ):
        raise FactError("INVALID_C13_INPUT", "typed opportunity and current observation required")
    if prior is not None and not isinstance(prior, SignalObservation):
        raise FactError("INVALID_C13_INPUT", "prior observation must be typed")
    if not isinstance(calendar, SyntheticCalendar):
        raise FactError("INVALID_C13_INPUT", "typed synthetic calendar required")

    if (
        not opportunity.original_window_closed
        or current.decision_cutoff <= opportunity.original_window_end
    ):
        return LaterSignalDecision(LaterSignalStatus.ORIGINAL_T1_UNCHANGED, "ORIGINAL_WINDOW_OPEN")
    if policy is None:
        return LaterSignalDecision(LaterSignalStatus.OBSERVATION_ONLY, "POLICY_NOT_ADOPTED")
    if not isinstance(policy, LaterSignalPolicy):
        raise FactError("INVALID_C13_INPUT", "policy must be typed or null")
    if policy.execution_window_id != NEXT_SYNTHETIC_OPEN_WINDOW:
        return LaterSignalDecision(
            LaterSignalStatus.BLOCK_LATER_ATTEMPT, "UNREGISTERED_EXECUTION_WINDOW"
        )
    if (
        policy.calendar_version != calendar.version
        or policy.timezone_name != calendar.timezone_name
        or policy.execution_window_id != calendar.execution_window_id
    ):
        return LaterSignalDecision(
            LaterSignalStatus.BLOCK_LATER_ATTEMPT, "CALENDAR_BINDING_MISMATCH"
        )
    if not opportunity.original_attempt_state.permits_later_attempt:
        return LaterSignalDecision(
            LaterSignalStatus.BLOCK_LATER_ATTEMPT, "ORIGINAL_ATTEMPT_NOT_TERMINAL_ZERO"
        )
    zone = ZoneInfo(policy.timezone_name)
    observation_age = calendar.session_distance(
        opportunity.original_window_end.astimezone(zone).date(), current.signal_session
    )
    if observation_age is None:
        return LaterSignalDecision(
            LaterSignalStatus.BLOCK_LATER_ATTEMPT, "LIFETIME_CALENDAR_UNBOUND"
        )
    if (
        observation_age > policy.observation_lifetime_sessions
        or current.decision_cutoff > policy.expiry
    ):
        return LaterSignalDecision(LaterSignalStatus.TERMINAL_NO_REARM, "EXPIRY_PASSED")
    if prior is None:
        return LaterSignalDecision(LaterSignalStatus.OBSERVATION_ONLY, "PRIOR_EVIDENCE_MISSING")
    if (
        prior.parent_opportunity_id != opportunity.opportunity_id
        or current.parent_opportunity_id != opportunity.opportunity_id
        or prior.security_id != current.security_id
        or prior.security_id != opportunity.security_id
        or prior.rule_version != current.rule_version
        or prior.rule_version != policy.predicate_version
        or prior.producer_version != current.producer_version
        or prior.producer_version != policy.producer_version
        or prior.source_version != current.source_version
    ):
        return LaterSignalDecision(
            LaterSignalStatus.BLOCK_LATER_ATTEMPT, "SIGNAL_IDENTITY_MISMATCH"
        )
    if current.evidence_at < prior.evidence_at:
        return LaterSignalDecision(LaterSignalStatus.TERMINAL_NO_REARM, "BACKDATED_RECOVERY")
    if prior.signal_session != calendar.preceding_session(current.signal_session):
        return LaterSignalDecision(LaterSignalStatus.BLOCK_LATER_ATTEMPT, "SIGNAL_SESSION_GAP")
    if (
        prior.feature_session != prior.signal_session
        or current.feature_session != current.signal_session
    ):
        return LaterSignalDecision(
            LaterSignalStatus.BLOCK_LATER_ATTEMPT, "FEATURE_SESSION_MISMATCH"
        )
    if (
        prior.evidence_at.astimezone(zone).date() != prior.signal_session
        or prior.available_at.astimezone(zone).date() != prior.signal_session
        or prior.decision_cutoff.astimezone(zone).date() != prior.signal_session
        or current.evidence_at.astimezone(zone).date() != current.signal_session
        or current.available_at.astimezone(zone).date() != current.signal_session
        or current.decision_cutoff.astimezone(zone).date() != current.signal_session
        or prior.decision_cutoff >= current.decision_cutoff
    ):
        return LaterSignalDecision(
            LaterSignalStatus.BLOCK_LATER_ATTEMPT, "EVIDENCE_SESSION_MISMATCH"
        )
    if prior.evidence_id == current.evidence_id:
        return LaterSignalDecision(LaterSignalStatus.OBSERVATION_ONLY, "DUPLICATE_EVIDENCE")
    if (
        not calendar.closing_time(prior.signal_session)
        <= prior.evidence_at
        <= prior.available_at
        <= prior.decision_cutoff
        or not calendar.closing_time(current.signal_session)
        <= current.evidence_at
        <= current.available_at
        <= current.decision_cutoff
    ):
        return LaterSignalDecision(LaterSignalStatus.BLOCK_LATER_ATTEMPT, "EVIDENCE_NOT_AVAILABLE")
    if current.cause is not SignalCause.QUALIFYING_RULE_TRANSITION:
        return LaterSignalDecision(LaterSignalStatus.OBSERVATION_ONLY, "NON_QUALIFYING_CHANGE")
    if prior.eligibility is Eligibility.UNKNOWN or current.eligibility is Eligibility.UNKNOWN:
        return LaterSignalDecision(LaterSignalStatus.OBSERVATION_ONLY, "UNKNOWN_IS_NOT_FALSE")
    if not (
        prior.eligibility is Eligibility.EXPLICIT_FALSE
        and current.eligibility is Eligibility.EXPLICIT_TRUE
    ):
        return LaterSignalDecision(
            LaterSignalStatus.OBSERVATION_ONLY, "NO_FALSE_TO_TRUE_TRANSITION"
        )
    execution_at = calendar.following_open(current.signal_session)
    if execution_at is None or not current.decision_cutoff < execution_at <= policy.expiry:
        return LaterSignalDecision(LaterSignalStatus.TERMINAL_NO_REARM, "NO_OPEN_IN_WINDOW")

    slot = AttemptSlot(opportunity.account_id, opportunity.identity)
    signal_id = str(
        digest({"prior": _observation_key(prior), "current": _observation_key(current)})
    )
    candidate = PlanningAttempt(
        slot,
        signal_id,
        policy.policy_version,
        policy.execution_window_id,
        execution_at,
    )
    return LaterSignalDecision(
        LaterSignalStatus.ELIGIBLE_FOR_ONE_ATTEMPT,
        "FIRST_EXPLICIT_FALSE_TO_TRUE",
        slot,
        candidate,
    )
