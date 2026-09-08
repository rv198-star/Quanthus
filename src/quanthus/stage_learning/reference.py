"""Deterministic T-only reference sampling and independent-review contracts for P3-STAGE-001-D."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from quanthus.stage_learning.core import ActivityObservation
from quanthus.structure_v2 import EpisodeObservation
from quanthus.structure_v2_inputs import Admission, digest


class ReferencePilotError(ValueError):
    """Raised when the bounded reference-pilot contract is violated."""


class ReferenceKind(str, Enum):
    DEFINITE = "definite"
    COEXISTENCE = "coexistence"
    AMBIGUOUS_SET = "ambiguous_set"
    UNJUDGEABLE = "unjudgeable"


@dataclass(frozen=True)
class ReferenceContext:
    context_id: str
    security_code: str
    market_date: date
    anchor_scale: int
    episode_id: str
    anchor_key: str
    anchor_revision: str
    sampling_stratum: str
    boundary_tags: tuple[str, ...]
    evidence_digest: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewerJudgement:
    context_id: str
    reviewer_id: str
    reviewer_role: str
    reference_kind: ReferenceKind
    supported_stage_codes: tuple[int, ...]
    adjacent_contexts: tuple[str, ...]
    reason_codes: tuple[str, ...]
    evidence_digest: str

    def __post_init__(self) -> None:
        if self.reviewer_role not in {"primary", "adjudicator"}:
            raise ReferencePilotError("reviewer_role must be primary or adjudicator")
        if not self.reviewer_id.strip():
            raise ReferencePilotError("reviewer_id is required")
        if any(code not in {3, 4} for code in self.supported_stage_codes):
            raise ReferencePilotError("first-group reference supports only stage 3/4 codes")
        if len(set(self.supported_stage_codes)) != len(self.supported_stage_codes):
            raise ReferencePilotError("supported_stage_codes contains duplicates")
        if not self.evidence_digest.startswith("sha256:"):
            raise ReferencePilotError("review evidence digest is invalid")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResolvedReference:
    context_id: str
    reference_kind: ReferenceKind
    supported_stage_codes: tuple[int, ...]
    adjacent_contexts: tuple[str, ...]
    resolution: str
    primary_reviewer_ids: tuple[str, str]
    adjudicator_id: str | None
    evidence_digest: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rank(seed: str, key: str) -> str:
    return hashlib.sha256(f"{seed}|{key}".encode()).hexdigest()


def _decimal_distance(close: Decimal | None, level: Decimal) -> Decimal | None:
    if close is None or level <= 0:
        return None
    return close / level - Decimal(1)


def boundary_tags(
    episode: EpisodeObservation,
    activity: ActivityObservation | None,
) -> tuple[str, ...]:
    """Return T-only boundary/rare tags without using any future outcome."""
    tags: list[str] = []
    if episode.observation == "started":
        tags.append("first_probe")
    if episode.observation in {"lost", "below"}:
        tags.append("same_anchor_loss")
    if episode.observation == "reclaimed":
        tags.append("same_anchor_reclaim")
    if episode.censor_reason:
        tags.append("censored_process")
    if episode.hold_count in {1, 2, 3}:
        tags.append("acceptance_hold_boundary")
    distance = _decimal_distance(episode.close, episode.frozen_level)
    if distance is not None and abs(distance) <= Decimal("0.005"):
        tags.append("near_frozen_anchor")
    if activity is None or not activity.available or activity.volume_ratio is None:
        tags.append("activity_unavailable")
    elif abs(activity.volume_ratio - Decimal("1.25")) <= Decimal("0.15"):
        tags.append("activity_threshold_neighborhood")
    return tuple(sorted(set(tags)))


def _context_key(episode: EpisodeObservation) -> str:
    return digest(
        {
            "security_code": episode.security_code,
            "market_date": episode.market_date,
            "anchor_scale": episode.scale,
            "episode_id": episode.episode_id,
            "anchor_revision": episode.anchor_revision,
        }
    )


def context_identity(episode: EpisodeObservation) -> str:
    """Stable T-only identity shared by sampling and downstream learning."""
    return _context_key(episode)


def select_reference_contexts(
    episodes: Sequence[EpisodeObservation],
    activity: Sequence[ActivityObservation],
    *,
    report_start: date,
    report_end: date,
    seed: str,
    random_count: int,
    boundary_count: int,
) -> tuple[ReferenceContext, ...]:
    """Freeze 80 representative + 40 boundary/rare contexts deterministically.

    Sampling uses only T-or-earlier Structure/Activity facts.  No candidate verdict, reviewer label,
    settlement or future price is admitted to the ranking function.
    """
    if not seed or random_count < 1 or boundary_count < 1:
        raise ReferencePilotError("reference sampling parameters are invalid")
    activity_by_key = {item.key: item for item in activity}
    if len(activity_by_key) != len(activity):
        raise ReferencePilotError("duplicate activity observation")
    eligible: list[tuple[EpisodeObservation, tuple[str, ...]]] = []
    seen: set[str] = set()
    for episode in sorted(
        episodes,
        key=lambda item: (item.security_code, item.market_date, item.scale, item.episode_id),
    ):
        if not report_start <= episode.market_date <= report_end:
            continue
        context_id = _context_key(episode)
        if context_id in seen:
            continue
        seen.add(context_id)
        eligible.append(
            (
                episode,
                boundary_tags(
                    episode,
                    activity_by_key.get((episode.security_code, episode.market_date)),
                ),
            )
        )
    need = random_count + boundary_count
    if len(eligible) < need:
        raise ReferencePilotError(f"reference population has {len(eligible)} contexts; need {need}")

    boundary_pool = [item for item in eligible if item[1]]
    if len(boundary_pool) < boundary_count:
        raise ReferencePilotError("reference population has insufficient boundary/rare contexts")
    boundary_selected = sorted(
        boundary_pool,
        key=lambda item: (_rank(seed + "|boundary", _context_key(item[0])), _context_key(item[0])),
    )[:boundary_count]
    boundary_ids = {_context_key(item[0]) for item in boundary_selected}
    random_pool = [item for item in eligible if _context_key(item[0]) not in boundary_ids]
    random_selected = sorted(
        random_pool,
        key=lambda item: (_rank(seed + "|random", _context_key(item[0])), _context_key(item[0])),
    )[:random_count]
    if len(random_selected) != random_count:
        raise ReferencePilotError(
            "reference population cannot satisfy random sample after boundary draw"
        )

    output: list[ReferenceContext] = []
    for stratum, rows in (("random", random_selected), ("boundary_or_rare", boundary_selected)):
        for episode, tags in rows:
            context_id = _context_key(episode)
            evidence_digest = digest(
                {
                    "episode_revision": episode.revision_id,
                    "activity_revision": (
                        activity_by_key[(episode.security_code, episode.market_date)].revision_id
                        if (episode.security_code, episode.market_date) in activity_by_key
                        else None
                    ),
                }
            )
            output.append(
                ReferenceContext(
                    context_id=context_id,
                    security_code=episode.security_code,
                    market_date=episode.market_date,
                    anchor_scale=episode.scale,
                    episode_id=episode.episode_id,
                    anchor_key=episode.anchor_key,
                    anchor_revision=episode.anchor_revision,
                    sampling_stratum=stratum,
                    boundary_tags=tags,
                    evidence_digest=evidence_digest,
                )
            )
    return tuple(sorted(output, key=lambda item: (item.sampling_stratum, item.context_id)))


def build_reviewer_packet(
    contexts: Sequence[ReferenceContext],
    episodes: Sequence[EpisodeObservation],
    activity: Sequence[ActivityObservation],
    admissions: Sequence[Admission],
    *,
    history_sessions: int,
) -> tuple[dict[str, Any], ...]:
    """Build reviewer-visible T-only evidence without candidate verdicts or future data."""
    if history_sessions < 20:
        raise ReferencePilotError("review packet history must be at least 20 sessions")
    episode_by_context = {_context_key(item): item for item in episodes}
    activity_by_key = {item.key: item for item in activity}
    rows_by_security: dict[str, list[Admission]] = {}
    for admission in sorted(
        admissions,
        key=lambda item: (item.row.security_code, item.row.market_date),
    ):
        rows_by_security.setdefault(admission.row.security_code, []).append(admission)
    output: list[dict[str, Any]] = []
    for context in contexts:
        episode = episode_by_context.get(context.context_id)
        if episode is None:
            raise ReferencePilotError("sampled context cannot be resolved to its episode")
        prior = [
            item
            for item in rows_by_security.get(context.security_code, ())
            if item.row.market_date <= context.market_date
        ][-(history_sessions + 1) :]
        if not prior or prior[-1].row.market_date != context.market_date:
            raise ReferencePilotError("review context has no T business attempt")
        activity_item = activity_by_key.get((context.security_code, context.market_date))
        packet = {
            "schema_version": "quanthus.stage-reference-review-packet.v1",
            "context_id": context.context_id,
            "sampling_stratum": context.sampling_stratum,
            "boundary_tags": context.boundary_tags,
            "security_code": context.security_code,
            "market_date": context.market_date,
            "anchor_scale": context.anchor_scale,
            "frozen_reference": {
                "anchor_key": episode.anchor_key,
                "anchor_revision": episode.anchor_revision,
                "frozen_high": episode.frozen_level,
                "frozen_low": episode.frozen_low,
                "episode_started_at": episode.started_at,
            },
            "t_only_process_facts": {
                "observation": episode.observation,
                "phase": episode.phase,
                "exchange_session_age": episode.exchange_session_age,
                "observed_bar_count": episode.observed_bar_count,
                "hold_count": episode.hold_count,
                "close": episode.close,
                "interaction_ref": episode.interaction_ref,
                "previous_loss_ref": episode.previous_loss_ref,
                "censor_reason": episode.censor_reason,
            },
            "activity": None if activity_item is None else activity_item.to_dict(),
            "history": [
                {
                    "market_date": item.row.market_date,
                    "admitted": item.admitted,
                    "reason": item.reason,
                    "high": item.row.high,
                    "low": item.row.low,
                    "close": item.row.close,
                    "volume": item.row.volume,
                    "coordinate_epoch": item.row.coordinate_epoch,
                    "reset_reason": item.row.reset_reason,
                    "source_member_digest": item.row.member_digest,
                }
                for item in prior
            ],
            "review_contract": {
                "reference_kind": [item.value for item in ReferenceKind],
                "supported_stage_codes": [3, 4],
                "adjacent_context_tokens": [
                    "consolidation_like",
                    "trend_background",
                    "breakout_failure",
                    "other",
                ],
                "primary_reviewers_required": 2,
                "adjudicator_required_on_disagreement": True,
            },
            "forbidden_fields": [
                "engineering_candidate_state",
                "engineering_primary_stage",
                "future_prices",
                "future_settlement",
                "strategy_result",
            ],
        }
        if digest(
            {
                "episode_revision": episode.revision_id,
                "activity_revision": None if activity_item is None else activity_item.revision_id,
            }
        ) != context.evidence_digest:
            raise ReferencePilotError("review evidence digest differs from sampled context")
        output.append(packet)
    return tuple(output)


def _judgement_signature(value: ReviewerJudgement) -> tuple[Any, ...]:
    return (
        value.reference_kind,
        value.supported_stage_codes,
        value.adjacent_contexts,
    )


def resolve_reference_reviews(
    contexts: Sequence[ReferenceContext],
    judgements: Iterable[ReviewerJudgement],
) -> tuple[ResolvedReference, ...]:
    """Resolve only truly independent primary reviews; disagreement needs a third adjudicator."""
    by_context: dict[str, list[ReviewerJudgement]] = {}
    for judgement in judgements:
        by_context.setdefault(judgement.context_id, []).append(judgement)
    context_by_id = {item.context_id: item for item in contexts}
    if set(by_context) - set(context_by_id):
        raise ReferencePilotError("review ledger contains unknown contexts")
    output: list[ResolvedReference] = []
    for context in contexts:
        rows = by_context.get(context.context_id, [])
        primary = [item for item in rows if item.reviewer_role == "primary"]
        adjudicators = [item for item in rows if item.reviewer_role == "adjudicator"]
        if len(primary) != 2 or primary[0].reviewer_id == primary[1].reviewer_id:
            raise ReferencePilotError(
                "each resolved context requires two distinct primary reviewers"
            )
        if any(item.evidence_digest != context.evidence_digest for item in rows):
            raise ReferencePilotError("review judgement evidence digest differs")
        if _judgement_signature(primary[0]) == _judgement_signature(primary[1]):
            if adjudicators:
                raise ReferencePilotError("adjudication is forbidden when primary reviewers agree")
            chosen = primary[0]
            resolution = "primary_agreement"
            adjudicator_id = None
        else:
            if len(adjudicators) != 1:
                raise ReferencePilotError("primary disagreement requires exactly one adjudicator")
            if adjudicators[0].reviewer_id in {item.reviewer_id for item in primary}:
                raise ReferencePilotError("adjudicator must be independent from primary reviewers")
            chosen = adjudicators[0]
            resolution = "third_party_adjudication"
            adjudicator_id = chosen.reviewer_id
        output.append(
            ResolvedReference(
                context_id=context.context_id,
                reference_kind=chosen.reference_kind,
                supported_stage_codes=chosen.supported_stage_codes,
                adjacent_contexts=chosen.adjacent_contexts,
                resolution=resolution,
                primary_reviewer_ids=(primary[0].reviewer_id, primary[1].reviewer_id),
                adjudicator_id=adjudicator_id,
                evidence_digest=context.evidence_digest,
            )
        )
    return tuple(output)
