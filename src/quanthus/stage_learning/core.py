"""Causal F7 activity facts and engineering-only stage-3/4 candidate resolver."""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Context, Decimal, localcontext
from enum import Enum
from typing import Any

from quanthus.structure_v2 import EpisodeObservation, StructureV2Result
from quanthus.structure_v2_inputs import Admission, digest


class PredicateState(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class InferenceStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    ABSTAIN = "ABSTAIN"
    RULE_CONFLICT = "RULE_CONFLICT"
    NOT_EVALUATED = "NOT_EVALUATED"


@dataclass(frozen=True)
class ActivityObservation:
    security_code: str
    market_date: date
    window: int
    available: bool
    volume_ratio: Decimal | None
    reason: str
    source_member_digest: str

    @property
    def key(self) -> tuple[str, date]:
        return self.security_code, self.market_date

    @property
    def revision_id(self) -> str:
        return digest(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return {"revision_id": self.revision_id, **asdict(self)}


@dataclass(frozen=True)
class PredicateEvidence:
    name: str
    state: PredicateState
    observed_value: str | int | None
    threshold: str | int | None
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StageCandidateObservation:
    security_code: str
    market_date: date
    anchor_scale: int
    stage_code: int
    episode_id: str
    anchor_key: str
    anchor_revision: str
    reference_role: str
    predicate_evidence: tuple[PredicateEvidence, ...]
    candidate_state: PredicateState
    authority: str = "engineering_candidate_only"
    official_label: bool = False

    @property
    def key(self) -> tuple[str, date, int, int]:
        return self.security_code, self.market_date, self.anchor_scale, self.stage_code

    @property
    def revision_id(self) -> str:
        return digest(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return {"revision_id": self.revision_id, **asdict(self)}


@dataclass(frozen=True)
class StageDecision:
    security_code: str
    market_date: date
    anchor_scale: int
    inference_status: InferenceStatus
    primary_stage: int | None
    supported_candidates: tuple[int, ...]
    evaluated_candidates: tuple[int, ...]
    reason_codes: tuple[str, ...]
    candidate_revisions: tuple[str, ...]
    authority: str = "engineering_candidate_only"
    official_label: bool = False

    @property
    def key(self) -> tuple[str, date, int]:
        return self.security_code, self.market_date, self.anchor_scale

    @property
    def revision_id(self) -> str:
        return digest(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return {"revision_id": self.revision_id, **asdict(self)}


def _ratio(current: Decimal, history: Iterable[Decimal]) -> Decimal:
    values = tuple(history)
    if not values:
        raise ValueError("activity history is empty")
    with localcontext(Context(prec=28)):
        baseline = sum(values, Decimal(0)) / Decimal(len(values))
        if baseline <= 0:
            raise ValueError("activity baseline must be positive")
        return current / baseline


class ActivityEngine:
    """Stateful activity builder; contiguous chunks produce the same facts as one batch."""

    def __init__(self, *, window: int = 20) -> None:
        if type(window) is not int or window < 2:
            raise ValueError("activity window must be an integer >=2")
        self.window = window
        self._histories: dict[str, deque[Decimal | None]] = {}
        self._last_rows: dict[str, Any] = {}
        self._seen: set[tuple[str, date]] = set()

    def consume(self, admissions: Iterable[Admission]) -> tuple[ActivityObservation, ...]:
        output: list[ActivityObservation] = []
        for admission in admissions:
            if admission.key in self._seen:
                raise ValueError("duplicate activity business slot")
            self._seen.add(admission.key)
            row = admission.row
            history = self._histories.setdefault(
                row.security_code, deque(maxlen=self.window)
            )
            previous = self._last_rows.get(row.security_code)
            discontinuity = bool(row.reset_reason)
            if previous is not None and (
                previous.coordinate_ref != row.coordinate_ref
                or previous.listing_id != row.listing_id
                or row.session_index != previous.session_index + 1
            ):
                discontinuity = True
            if discontinuity or not admission.admitted:
                history.clear()
            if not admission.admitted:
                output.append(
                    ActivityObservation(
                        row.security_code,
                        row.market_date,
                        self.window,
                        False,
                        None,
                        f"source_not_admitted:{admission.reason}",
                        row.member_digest,
                    )
                )
                self._last_rows[row.security_code] = row
                continue
            if len(history) < self.window:
                reason = "insufficient_history"
                value = None
            elif admission.missing_volume or row.volume is None:
                reason = "current_volume_unavailable"
                value = None
            elif any(item is None for item in history):
                reason = "prior_volume_unavailable"
                value = None
            else:
                try:
                    value = _ratio(row.volume, (item for item in history if item is not None))
                    reason = "available"
                except ValueError:
                    value = None
                    reason = "nonpositive_prior_volume_baseline"
            output.append(
                ActivityObservation(
                    row.security_code,
                    row.market_date,
                    self.window,
                    value is not None,
                    value,
                    reason,
                    row.member_digest,
                )
            )
            history.append(None if admission.missing_volume else row.volume)
            self._last_rows[row.security_code] = row
        return tuple(output)


def build_activity_observations(
    admissions: tuple[Admission, ...], *, window: int = 20
) -> tuple[ActivityObservation, ...]:
    """Build causal V_t / mean(V[t-window:t-1]) without filling missing volume."""
    ordered = sorted(admissions, key=lambda item: (item.row.security_code, item.row.market_date))
    return ActivityEngine(window=window).consume(ordered)


def _predicate(
    name: str,
    state: PredicateState,
    observed: str | int | Decimal | None,
    threshold: str | int | Decimal | None,
    *refs: str,
) -> PredicateEvidence:
    def text(value: str | int | Decimal | None) -> str | int | None:
        if isinstance(value, Decimal):
            return format(value, "f")
        return value

    return PredicateEvidence(name, state, text(observed), text(threshold), tuple(refs))


def _candidate_state(predicates: tuple[PredicateEvidence, ...]) -> PredicateState:
    states = {item.state for item in predicates}
    if PredicateState.FALSE in states:
        return PredicateState.FALSE
    if PredicateState.UNKNOWN in states:
        return PredicateState.UNKNOWN
    return PredicateState.TRUE


def _stage3_candidate(
    episode: EpisodeObservation,
    activity: ActivityObservation | None,
    *,
    probe_volume_ratio_min: Decimal,
) -> StageCandidateObservation:
    activity_ref = () if activity is None else (activity.revision_id,)
    activity_available = (
        activity is not None and activity.available and activity.volume_ratio is not None
    )
    predicates = (
        _predicate(
            "same_anchor_close_cross_above",
            PredicateState.TRUE if episode.observation == "started" else PredicateState.FALSE,
            episode.observation,
            "started",
            episode.interaction_ref or episode.revision_id,
        ),
        _predicate(
            "episode_started_on_current_session",
            PredicateState.TRUE if episode.exchange_session_age == 0 else PredicateState.FALSE,
            episode.exchange_session_age,
            0,
            episode.revision_id,
        ),
        _predicate(
            "anchor_formed_from_pre_t_observations",
            PredicateState.TRUE,
            "structure_v2_prior_window",
            "required",
            episode.anchor_revision,
        ),
        _predicate(
            "activity_input_available",
            PredicateState.TRUE if activity_available else PredicateState.UNKNOWN,
            None if activity is None else activity.reason,
            "available",
            *activity_ref,
        ),
        _predicate(
            "probe_volume_ratio20_min",
            PredicateState.UNKNOWN
            if not activity_available
            else PredicateState.TRUE
            if activity.volume_ratio >= probe_volume_ratio_min
            else PredicateState.FALSE,
            None if not activity_available else activity.volume_ratio,
            probe_volume_ratio_min,
            *activity_ref,
        ),
    )
    return StageCandidateObservation(
        episode.security_code,
        episode.market_date,
        episode.scale,
        3,
        episode.episode_id,
        episode.anchor_key,
        episode.anchor_revision,
        "pre_t_frozen_high_breakout_target",
        predicates,
        _candidate_state(predicates),
    )


def _stage4_candidate(
    episode: EpisodeObservation,
    activities: tuple[ActivityObservation, ...],
    *,
    hold_sessions: int,
    signed_distance_min: Decimal,
    activity_mean_min: Decimal,
) -> StageCandidateObservation:
    available = len(activities) == episode.observed_bar_count and all(
        item.available and item.volume_ratio is not None for item in activities
    )
    activity_refs = tuple(item.revision_id for item in activities)
    if available:
        with localcontext(Context(prec=28)):
            activity_mean = sum(
                (item.volume_ratio for item in activities if item.volume_ratio is not None),
                Decimal(0),
            ) / Decimal(len(activities))
    else:
        activity_mean = None
    if episode.close is None or episode.frozen_level <= 0:
        signed = None
    else:
        with localcontext(Context(prec=28)):
            signed = episode.close / episode.frozen_level - Decimal(1)
    predicates = (
        _predicate(
            "prior_same_anchor_breakout_episode_required",
            PredicateState.TRUE,
            episode.started_at.isoformat(),
            "prior_session",
            episode.revision_id,
        ),
        _predicate(
            "not_same_session_as_initial_stage_3_confirmation",
            PredicateState.TRUE if episode.exchange_session_age > 0 else PredicateState.FALSE,
            episode.exchange_session_age,
            1,
            episode.revision_id,
        ),
        _predicate(
            "no_same_anchor_close_cross_below_during_required_hold",
            PredicateState.TRUE
            if episode.phase == "active" and episode.previous_loss_ref is None
            else PredicateState.FALSE,
            episode.phase,
            "active_without_loss",
            episode.revision_id,
        ),
        _predicate(
            "acceptance_hold_sessions",
            PredicateState.TRUE if episode.hold_count >= hold_sessions else PredicateState.FALSE,
            episode.hold_count,
            hold_sessions,
            episode.revision_id,
        ),
        _predicate(
            "signed_distance_meets_candidate_margin",
            PredicateState.UNKNOWN
            if signed is None
            else PredicateState.TRUE
            if signed >= signed_distance_min
            else PredicateState.FALSE,
            signed,
            signed_distance_min,
            episode.revision_id,
        ),
        _predicate(
            "activity_window_available",
            PredicateState.TRUE if available else PredicateState.UNKNOWN,
            len(activities) if activities else None,
            episode.observed_bar_count,
            *activity_refs,
        ),
        _predicate(
            "acceptance_window_volume_ratio20_min",
            PredicateState.UNKNOWN
            if activity_mean is None
            else PredicateState.TRUE
            if activity_mean >= activity_mean_min
            else PredicateState.FALSE,
            activity_mean,
            activity_mean_min,
            *activity_refs,
        ),
    )
    return StageCandidateObservation(
        episode.security_code,
        episode.market_date,
        episode.scale,
        4,
        episode.episode_id,
        episode.anchor_key,
        episode.anchor_revision,
        "same_frozen_high_as_acceptance_support",
        predicates,
        _candidate_state(predicates),
    )


class Stage34DecisionEngine:
    """Stateful resolver that can consume episode observations in arbitrary contiguous chunks."""

    def __init__(
        self,
        activity_by_key: Mapping[tuple[str, date], ActivityObservation],
        *,
        probe_volume_ratio_min: Decimal,
        acceptance_hold_sessions: int,
        acceptance_signed_distance_min: Decimal,
        acceptance_window_volume_ratio_min: Decimal,
    ) -> None:
        self._activity = activity_by_key
        self._probe_volume_ratio_min = probe_volume_ratio_min
        self._hold_sessions = acceptance_hold_sessions
        self._signed_min = acceptance_signed_distance_min
        self._activity_mean_min = acceptance_window_volume_ratio_min
        self._episode_activities: dict[tuple[str, int, str], list[ActivityObservation]] = {}

    def consume(
        self, episodes: Iterable[EpisodeObservation]
    ) -> tuple[tuple[StageCandidateObservation, ...], tuple[StageDecision, ...]]:
        candidates: list[StageCandidateObservation] = []
        decisions: list[StageDecision] = []
        for episode in episodes:
            key = (episode.security_code, episode.scale, episode.episode_id)
            activity = self._activity.get((episode.security_code, episode.market_date))
            history = self._episode_activities.setdefault(key, [])
            if activity is not None:
                history.append(activity)
            slot_candidates: list[StageCandidateObservation] = []
            if episode.observation == "started":
                slot_candidates.append(
                    _stage3_candidate(
                        episode,
                        activity,
                        probe_volume_ratio_min=self._probe_volume_ratio_min,
                    )
                )
            elif episode.phase == "active" and episode.observation == "held":
                slot_candidates.append(
                    _stage4_candidate(
                        episode,
                        tuple(history),
                        hold_sessions=self._hold_sessions,
                        signed_distance_min=self._signed_min,
                        activity_mean_min=self._activity_mean_min,
                    )
                )
            if not slot_candidates:
                if episode.phase in {"below", "reclaimed", "censored"}:
                    self._episode_activities.pop(key, None)
                continue
            candidates.extend(slot_candidates)
            supported = tuple(
                item.stage_code
                for item in slot_candidates
                if item.candidate_state is PredicateState.TRUE
            )
            unknown = any(
                item.candidate_state is PredicateState.UNKNOWN for item in slot_candidates
            )
            if len(supported) == 1:
                status = InferenceStatus.CONFIRMED
                primary = supported[0]
                reasons = ("engineering_rule_satisfied",)
            elif len(supported) > 1:
                status = InferenceStatus.RULE_CONFLICT
                primary = None
                reasons = ("mutually_exclusive_stage_rules_both_satisfied",)
            elif unknown:
                status = InferenceStatus.ABSTAIN
                primary = None
                reasons = ("required_evidence_unknown",)
            else:
                status = InferenceStatus.NOT_EVALUATED
                primary = None
                reasons = ("first_group_rule_not_satisfied",)
            decisions.append(
                StageDecision(
                    episode.security_code,
                    episode.market_date,
                    episode.scale,
                    status,
                    primary,
                    supported,
                    tuple(item.stage_code for item in slot_candidates),
                    reasons,
                    tuple(item.revision_id for item in slot_candidates),
                )
            )
            if episode.phase in {"below", "reclaimed", "censored"}:
                self._episode_activities.pop(key, None)
        return tuple(candidates), tuple(decisions)


def build_stage34_decisions(
    structure: StructureV2Result,
    activity: tuple[ActivityObservation, ...],
    *,
    probe_volume_ratio_min: Decimal,
    acceptance_hold_sessions: int,
    acceptance_signed_distance_min: Decimal,
    acceptance_window_volume_ratio_min: Decimal,
) -> tuple[tuple[StageCandidateObservation, ...], tuple[StageDecision, ...]]:
    if acceptance_hold_sessions < 1:
        raise ValueError("acceptance hold must be >=1")
    activity_by_key = {item.key: item for item in activity}
    if len(activity_by_key) != len(activity):
        raise ValueError("duplicate activity observation")
    engine = Stage34DecisionEngine(
        activity_by_key,
        probe_volume_ratio_min=probe_volume_ratio_min,
        acceptance_hold_sessions=acceptance_hold_sessions,
        acceptance_signed_distance_min=acceptance_signed_distance_min,
        acceptance_window_volume_ratio_min=acceptance_window_volume_ratio_min,
    )
    ordered = sorted(
        structure.episodes,
        key=lambda item: (item.security_code, item.market_date, item.scale, item.episode_id),
    )
    return engine.consume(ordered)
