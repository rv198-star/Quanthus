"""Formal F9 continuous facts for same-support break, persistence, reclaim and rebound.

This module is additive to the accepted Structure Facts v2 B1-B5 slice. It reuses the exact
breakout-start rolling-low anchor as a predeclared support *candidate* and never claims that the
reference is universally strong support. Stage 10/11 thresholds live in a separate research
projection and are not part of fact identity.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Context, Decimal, localcontext
from typing import Any

from quanthus.structure_v2 import Interaction, Measurement, StructureV2Result
from quanthus.structure_v2_inputs import (
    Admission,
    DailyInput,
    calculation_spec,
    digest,
    signed_distance,
)
from quanthus.temporal_contracts import BusinessTime, ObservationRevision

SUPPORT_ROLE = "breakout-start-rolling-low-candidate"
PROCESS_CONCEPT = "structure.episode.support-failure-sequence"
REBOUND_CONCEPT = "structure.geometry.post-break-rebound"
DISTANCE_CONCEPT = "structure.geometry.signed-distance"


def support_fact_spec(request: dict[str, Any]) -> dict[str, Any]:
    """Canonical F9 identity; report scope and projection thresholds stay outside facts."""
    return {
        "schema_version": "quanthus.structure-f9-definition.v1",
        "base_structure_spec_digest": digest(calculation_spec(request)),
        "support_role": request["support_role"],
        "support_horizon_sessions": request["support_horizon_sessions"],
        "break_rule": "prior_close>=support>close",
        "reclaim_rule": "prior_close<=support<close",
        "break_day_unreclaimed_age": 0,
        "post_break_rebound": "close/post_break_running_low-1-while-unreclaimed",
        "reclaim_session_excluded_from_rebound": True,
        "gap_policy": "censor-without-failure",
        "coordinate_policy": request["coordinate_policy"],
    }


def _ratio(current: Decimal, prior: Decimal) -> Decimal:
    if prior <= 0:
        raise ValueError("support fact denominator must be positive")
    with localcontext(Context(prec=28)):
        return current / prior - Decimal(1)


@dataclass(frozen=True)
class SupportBinding:
    process_id: str
    episode_id: str
    security_code: str
    security_id: str
    listing_id: str
    scale: int
    started_at: date
    support_role: str
    support_anchor_key: str
    support_anchor_revision: str
    support_level: Decimal
    breakout_anchor_key: str
    breakout_anchor_revision: str
    coordinate_ref: str
    spec_digest: str
    source_episode_revision: str

    def to_dict(self) -> dict[str, Any]:
        return {"concept_id": PROCESS_CONCEPT, **asdict(self)}


@dataclass(frozen=True)
class ReboundMeasurement:
    security_code: str
    security_id: str
    listing_id: str
    market_date: date
    scale: int
    process_id: str
    support_anchor_key: str
    support_anchor_revision: str
    coordinate_ref: str
    close: Decimal
    post_break_running_low: Decimal
    value: Decimal
    spec_digest: str
    source_member_digest: str
    business_time: BusinessTime

    @property
    def identity(self) -> ObservationRevision:
        subject = digest(
            {
                "security_id": self.security_id,
                "listing_id": self.listing_id,
                "process_id": self.process_id,
                "support_anchor_key": self.support_anchor_key,
            }
        )
        return ObservationRevision(
            subject,
            self.listing_id,
            REBOUND_CONCEPT,
            "1.0.0",
            self.spec_digest,
            self.coordinate_ref,
            self.business_time,
            digest(
                {
                    "support_anchor_revision": self.support_anchor_revision,
                    "current_member": self.source_member_digest,
                    "post_break_running_low": self.post_break_running_low,
                }
            ),
            digest(self.value),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_key": self.identity.observation_key,
            "revision_id": self.identity.revision_id,
            "concept_id": REBOUND_CONCEPT,
            **asdict(self),
        }


@dataclass(frozen=True)
class SupportFailureObservation:
    process_id: str
    episode_id: str
    security_code: str
    market_date: date
    scale: int
    phase: str
    break_at: date | None
    reclaim_at: date | None
    unreclaimed_age_sessions: int | None
    support_distance_revision_ref: str | None
    post_break_rebound_revision_ref: str | None
    break_interaction_ref: str | None
    reclaim_interaction_ref: str | None
    close: Decimal | None
    source_member_digest: str
    censor_reason: str | None = None

    @property
    def key(self) -> str:
        return digest({"process_id": self.process_id, "market_date": self.market_date})

    @property
    def revision_id(self) -> str:
        return digest(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_key": self.key,
            "revision_id": self.revision_id,
            "concept_id": PROCESS_CONCEPT,
            **asdict(self),
        }


@dataclass(frozen=True)
class SupportFactsResult:
    bindings: tuple[SupportBinding, ...]
    distances: tuple[Measurement, ...]
    interactions: tuple[Interaction, ...]
    rebounds: tuple[ReboundMeasurement, ...]
    observations: tuple[SupportFailureObservation, ...]

    def semantic(self) -> dict[str, Any]:
        return {
            "bindings": [item.to_dict() for item in self.bindings],
            "distances": [item.to_dict() for item in self.distances],
            "interactions": [item.to_dict() for item in self.interactions],
            "rebounds": [item.to_dict() for item in self.rebounds],
            "observations": [item.to_dict() for item in self.observations],
        }

    @property
    def digest(self) -> str:
        return digest(self.semantic())


def _support_binding(
    structure: StructureV2Result, started: Any, spec_digest: str
) -> SupportBinding:
    breakout = next(
        (item for item in structure.anchors if item.revision_id == started.anchor_revision), None
    )
    if breakout is None:
        raise ValueError("support binding lacks breakout anchor")
    supports = [
        item
        for item in structure.anchors
        if item.security_code == started.security_code
        and item.scale == started.scale
        and item.side == "low"
        and item.window_start == breakout.window_start
        and item.window_end == breakout.window_end
        and item.coordinate_ref == breakout.coordinate_ref
    ]
    if len(supports) != 1:
        raise ValueError("support binding requires one exact sibling rolling-low anchor")
    support = supports[0]
    if support.level != started.frozen_low:
        raise ValueError("support binding level differs from immutable episode formation evidence")
    process_id = digest(
        {
            "episode_id": started.episode_id,
            "support_role": SUPPORT_ROLE,
            "support_anchor_key": support.key,
        }
    )
    return SupportBinding(
        process_id,
        started.episode_id,
        started.security_code,
        breakout.security_id,
        breakout.listing_id,
        started.scale,
        started.started_at,
        SUPPORT_ROLE,
        support.key,
        support.revision_id,
        support.level,
        breakout.key,
        breakout.revision_id,
        breakout.coordinate_ref,
        digest(
            {
                "root_spec": spec_digest,
                "concept": PROCESS_CONCEPT,
                "support_role": SUPPORT_ROLE,
                "break_rule": "prior_close>=support>close",
                "reclaim_rule": "prior_close<=support<close",
                "break_day_unreclaimed_age": 0,
            }
        ),
        started.revision_id,
    )


def _distance(row: DailyInput, binding: SupportBinding) -> Measurement:
    assert isinstance(row.close, Decimal)
    if row.coordinate_ref != binding.coordinate_ref:
        raise ValueError("support distance crosses coordinate boundary")
    # Identity binds the actual frozen support target from the B3 sibling rolling-low anchor.
    return Measurement(
        row.security_code,
        binding.security_id,
        binding.listing_id,
        row.market_date,
        binding.scale,
        binding.support_anchor_key,
        binding.support_anchor_revision,
        binding.support_level,
        binding.coordinate_ref,
        row.close,
        signed_distance(row.close, binding.support_level),
        digest(
            {
                "root_spec": binding.spec_digest,
                "concept": DISTANCE_CONCEPT,
                "target_role": SUPPORT_ROLE,
                "formula": "close/support-1",
            }
        ),
        row.member_digest,
        BusinessTime("measurement", row.result_as_of),
    )


def _interaction(
    kind: str,
    prior: DailyInput,
    row: DailyInput,
    binding: SupportBinding,
    previous_loss_ref: str | None = None,
) -> Interaction:
    assert isinstance(prior.close, Decimal) and isinstance(row.close, Decimal)
    if (
        prior.coordinate_ref != binding.coordinate_ref
        or row.coordinate_ref != binding.coordinate_ref
    ):
        raise ValueError("support interaction crosses coordinate boundary")
    return Interaction(
        kind,
        row.security_code,
        row.market_date,
        binding.scale,
        binding.support_anchor_key,
        binding.support_anchor_revision,
        prior.close,
        row.close,
        binding.support_level,
        prior.member_digest,
        row.member_digest,
        previous_loss_ref,
    )


def _rebound(
    row: DailyInput, binding: SupportBinding, post_break_low: Decimal
) -> ReboundMeasurement:
    assert isinstance(row.close, Decimal)
    return ReboundMeasurement(
        row.security_code,
        binding.security_id,
        binding.listing_id,
        row.market_date,
        binding.scale,
        binding.process_id,
        binding.support_anchor_key,
        binding.support_anchor_revision,
        binding.coordinate_ref,
        row.close,
        post_break_low,
        _ratio(row.close, post_break_low),
        digest(
            {
                "root_spec": binding.spec_digest,
                "concept": REBOUND_CONCEPT,
                "formula": "close/post_break_running_low-1",
                "reclaim_session_excluded": True,
            }
        ),
        row.member_digest,
        BusinessTime("measurement", row.result_as_of),
    )


def build_support_failure_facts(
    admissions: tuple[Admission, ...],
    structure: StructureV2Result,
    *,
    spec_digest: str,
    maximum_sessions: int = 60,
) -> SupportFactsResult:
    """Build one first-break/reclaim sequence for each breakout-start support candidate.

    `unreclaimed_age_sessions` is zero on the break session and counts subsequent uninterrupted
    admitted exchange sessions. Reclaim is terminal for this bounded first sequence. Missing input,
    identity or coordinate discontinuity censors the process and never fabricates failure.
    """
    if type(maximum_sessions) is not int or maximum_sessions < 2:
        raise ValueError("support fact horizon must be an integer >=2")
    starts: dict[str, Any] = {}
    for item in structure.episodes:
        if item.observation == "started":
            if item.episode_id in starts:
                raise ValueError("duplicate support-process start episode")
            starts[item.episode_id] = item
    rows_by_security: dict[str, list[Admission]] = {}
    for item in sorted(
        admissions, key=lambda value: (value.row.security_code, value.row.market_date)
    ):
        rows_by_security.setdefault(item.row.security_code, []).append(item)

    bindings: list[SupportBinding] = []
    distances: list[Measurement] = []
    interactions: list[Interaction] = []
    rebounds: list[ReboundMeasurement] = []
    observations: list[SupportFailureObservation] = []

    for episode_id in sorted(starts):
        started = starts[episode_id]
        binding = _support_binding(structure, started, spec_digest)
        bindings.append(binding)
        series = rows_by_security.get(binding.security_code, [])
        start_index = next(
            (
                index
                for index, item in enumerate(series)
                if item.row.market_date == binding.started_at
            ),
            None,
        )
        if start_index is None:
            raise ValueError("support-process start row is missing")
        start_attempt = series[start_index]
        if not start_attempt.admitted or start_attempt.row.close is None:
            raise ValueError("support-process start row is not admitted")
        previous = start_attempt.row
        break_at: date | None = None
        reclaim_at: date | None = None
        break_ref: str | None = None
        reclaim_ref: str | None = None
        break_session_index: int | None = None
        post_break_low: Decimal | None = None

        for offset, attempt in enumerate(series[start_index : start_index + maximum_sessions]):
            row = attempt.row
            discontinuity = row.reset_reason
            if offset > 0 and (
                row.listing_id != binding.listing_id
                or row.coordinate_ref != binding.coordinate_ref
                or row.session_index != previous.session_index + 1
            ):
                discontinuity = "identity_coordinate_or_calendar_discontinuity"
            if not attempt.admitted:
                discontinuity = attempt.reason
            if discontinuity:
                observations.append(
                    SupportFailureObservation(
                        binding.process_id,
                        binding.episode_id,
                        binding.security_code,
                        row.market_date,
                        binding.scale,
                        "censored",
                        break_at,
                        reclaim_at,
                        None,
                        None,
                        None,
                        break_ref,
                        reclaim_ref,
                        row.close,
                        row.member_digest,
                        discontinuity,
                    )
                )
                break
            if row.close is None:
                raise ValueError("admitted support-process row lacks close")
            distance = _distance(row, binding)
            distances.append(distance)
            phase = "intact" if break_at is None else "unreclaimed"
            age: int | None = None
            rebound_ref: str | None = None

            if offset > 0 and break_at is None and previous.close is not None and (
                previous.close >= binding.support_level > row.close
            ):
                event = _interaction("close-cross-below", previous, row, binding)
                interactions.append(event)
                break_at = row.market_date
                break_ref = event.revision_id
                break_session_index = row.session_index
                post_break_low = row.close
                phase = "unreclaimed"
                age = 0
            elif break_at is not None:
                if (
                    previous.close is not None
                    and previous.close <= binding.support_level < row.close
                ):
                    event = _interaction("close-reclaim", previous, row, binding, break_ref)
                    interactions.append(event)
                    reclaim_at = row.market_date
                    reclaim_ref = event.revision_id
                    phase = "reclaimed"
                else:
                    assert break_session_index is not None and post_break_low is not None
                    post_break_low = min(post_break_low, row.close)
                    age = row.session_index - break_session_index

            if phase == "unreclaimed":
                assert post_break_low is not None
                rebound = _rebound(row, binding, post_break_low)
                rebounds.append(rebound)
                rebound_ref = rebound.identity.revision_id

            observations.append(
                SupportFailureObservation(
                    binding.process_id,
                    binding.episode_id,
                    binding.security_code,
                    row.market_date,
                    binding.scale,
                    phase,
                    break_at,
                    reclaim_at,
                    age,
                    distance.identity.revision_id,
                    rebound_ref,
                    break_ref,
                    reclaim_ref,
                    row.close,
                    row.member_digest,
                )
            )
            previous = row
            if phase == "reclaimed":
                break

    return SupportFactsResult(
        tuple(bindings),
        tuple(
            sorted(
                distances,
                key=lambda item: (
                    item.security_code,
                    item.market_date,
                    item.scale,
                    item.anchor_key,
                ),
            )
        ),
        tuple(interactions),
        tuple(
            sorted(
                rebounds,
                key=lambda item: (
                    item.security_code,
                    item.market_date,
                    item.scale,
                    item.process_id,
                ),
            )
        ),
        tuple(
            sorted(
                observations,
                key=lambda item: (
                    item.security_code,
                    item.market_date,
                    item.scale,
                    item.process_id,
                ),
            )
        ),
    )


@dataclass(frozen=True)
class Stage1011Projection:
    process_id: str
    market_date: date
    prior_stage_10_confirmation_ref: str | None
    stage_10_active: bool
    stage_11_active: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_stage_10_11(
    facts: SupportFactsResult,
    *,
    persistence_sessions: int = 5,
    failure_depth_abs_min: Decimal = Decimal("0.02"),
    rebound_min: Decimal = Decimal("0.05"),
) -> tuple[Stage1011Projection, ...]:
    """Research-only projection proving Stage 11 consumes a *prior* immutable Stage-10 ref."""
    if persistence_sessions < 1 or failure_depth_abs_min <= 0 or rebound_min <= 0:
        raise ValueError("Stage 10/11 projection thresholds must be positive")
    distance_by_revision = {item.identity.revision_id: item for item in facts.distances}
    rebound_by_revision = {item.identity.revision_id: item for item in facts.rebounds}
    by_process: dict[str, list[SupportFailureObservation]] = {}
    for item in facts.observations:
        by_process.setdefault(item.process_id, []).append(item)
    output: list[Stage1011Projection] = []
    for process_id in sorted(by_process):
        confirmation_ref: str | None = None
        for item in sorted(by_process[process_id], key=lambda value: value.market_date):
            if item.phase in {"censored", "reclaimed", "intact"}:
                output.append(
                    Stage1011Projection(
                        process_id,
                        item.market_date,
                        confirmation_ref,
                        False,
                        False,
                        item.phase,
                    )
                )
                continue
            if item.support_distance_revision_ref is None:
                raise ValueError("unreclaimed support observation lacks distance fact")
            distance = distance_by_revision[item.support_distance_revision_ref].value
            if (
                confirmation_ref is None
                and item.unreclaimed_age_sessions is not None
                and item.unreclaimed_age_sessions >= persistence_sessions
                and distance <= -failure_depth_abs_min
            ):
                confirmation_ref = digest(
                    {
                        "projection": "stage-10-development-v1",
                        "process_id": process_id,
                        "confirmed_at": item.market_date,
                        "persistence_sessions": persistence_sessions,
                        "failure_depth_abs_min": failure_depth_abs_min,
                    }
                )
            stage_10_active = confirmation_ref is not None
            stage_11_active = False
            if stage_10_active and item.post_break_rebound_revision_ref is not None:
                rebound = rebound_by_revision[item.post_break_rebound_revision_ref].value
                stage_11_active = rebound >= rebound_min
            output.append(
                Stage1011Projection(
                    process_id,
                    item.market_date,
                    confirmation_ref,
                    stage_10_active,
                    stage_11_active,
                    (
                        "prior_stage_10_unreclaimed_rebound"
                        if stage_11_active
                        else (
                            "stage_10_confirmed_unreclaimed"
                            if stage_10_active
                            else "unreclaimed_not_confirmed"
                        )
                    ),
                )
            )
    return tuple(output)


def support_report_counts(
    facts: SupportFactsResult, projection: tuple[Stage1011Projection, ...]
) -> dict[str, Any]:
    phase_counts: dict[str, int] = {}
    for item in facts.observations:
        phase_counts[item.phase] = phase_counts.get(item.phase, 0) + 1
    confirmations = {
        item.prior_stage_10_confirmation_ref
        for item in projection
        if item.prior_stage_10_confirmation_ref is not None
    }
    return {
        "support_binding_count": len(facts.bindings),
        "support_distance_count": len(facts.distances),
        "support_break_count": sum(item.kind == "close-cross-below" for item in facts.interactions),
        "support_reclaim_count": sum(item.kind == "close-reclaim" for item in facts.interactions),
        "post_break_rebound_count": len(facts.rebounds),
        "support_process_observation_count": len(facts.observations),
        "phase_counts": dict(sorted(phase_counts.items())),
        "stage_10_confirmation_count": len(confirmations),
        "stage_10_active_observation_count": sum(item.stage_10_active for item in projection),
        "stage_11_active_observation_count": sum(item.stage_11_active for item in projection),
    }


def support_review_sample(
    facts: SupportFactsResult,
    projection: tuple[Stage1011Projection, ...],
    *,
    limit: int,
    seed: str,
) -> list[dict[str, Any]]:
    if type(limit) is not int or limit < 1:
        raise ValueError("support review sample limit must be positive integer")
    distances = {item.identity.revision_id: item for item in facts.distances}
    rebounds = {item.identity.revision_id: item for item in facts.rebounds}
    projected = {(item.process_id, item.market_date): item for item in projection}
    rows: list[dict[str, Any]] = []
    for item in facts.observations:
        if item.phase == "intact" and item.break_interaction_ref is None:
            continue
        distance = (
            None
            if item.support_distance_revision_ref is None
            else distances[item.support_distance_revision_ref].value
        )
        rebound = (
            None
            if item.post_break_rebound_revision_ref is None
            else rebounds[item.post_break_rebound_revision_ref].value
        )
        p = projected[(item.process_id, item.market_date)]
        rows.append(
            {
                "security_code": item.security_code,
                "market_date": item.market_date,
                "scale": item.scale,
                "process_id": item.process_id,
                "phase": item.phase,
                "unreclaimed_age_sessions": item.unreclaimed_age_sessions,
                "support_distance": distance,
                "post_break_rebound": rebound,
                "stage_10_active": p.stage_10_active,
                "stage_11_active": p.stage_11_active,
                "prior_stage_10_confirmation_ref": p.prior_stage_10_confirmation_ref,
                "censor_reason": item.censor_reason,
            }
        )
    ranked = sorted(
        rows,
        key=lambda item: (
            hashlib.sha256(
                f"{seed}|{item['process_id']}|{item['market_date']}".encode()
            ).hexdigest(),
            item["process_id"],
            item["market_date"],
        ),
    )
    return ranked[:limit]
