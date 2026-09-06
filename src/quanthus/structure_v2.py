"""Causal B3-B5 references, same-target interactions and bounded process observations.

No technical state renderer, consumer clock, package identity or trading authority enters facts.
Only one breakout/loss/reclaim process per security/window is monitored at once.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from quanthus.structure_v2_inputs import Admission, DailyInput, digest, signed_distance
from quanthus.temporal_contracts import BusinessTime, ObservationRevision


@dataclass(frozen=True)
class Anchor:
    security_code: str
    security_id: str
    listing_id: str
    scale: int
    side: str
    window_start: date
    window_end: date
    level: Decimal
    coordinate_ref: str
    calendar_ref: str
    spec_digest: str
    source_closure_digest: str

    @property
    def key(self) -> str:
        return digest({key: value for key, value in asdict(self).items()
                       if key not in {"level", "source_closure_digest"}})

    @property
    def revision_id(self) -> str:
        return digest(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return {"anchor_key": self.key, "anchor_revision": self.revision_id,
                "concept_id": f"structure.anchor.rolling-{self.side}", **asdict(self)}


@dataclass(frozen=True)
class Measurement:
    security_code: str
    security_id: str
    listing_id: str
    market_date: date
    scale: int
    anchor_key: str
    anchor_revision: str
    anchor_level: Decimal
    coordinate_ref: str
    close: Decimal
    value: Decimal
    spec_digest: str
    source_member_digest: str
    business_time: BusinessTime

    @property
    def identity(self) -> ObservationRevision:
        subject = digest({"security_id": self.security_id, "listing_id": self.listing_id,
                          "anchor_key": self.anchor_key})
        return ObservationRevision(
            subject, self.listing_id, "structure.geometry.signed-distance", "1.0.0",
            self.spec_digest, self.coordinate_ref, self.business_time,
            digest({"anchor_revision": self.anchor_revision,
                    "current_member": self.source_member_digest}), digest(self.value),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"observation_key": self.identity.observation_key,
                "revision_id": self.identity.revision_id,
                "concept_id": "structure.geometry.signed-distance", **asdict(self)}


@dataclass(frozen=True)
class Interaction:
    kind: str
    security_code: str
    market_date: date
    scale: int
    anchor_key: str
    anchor_revision: str
    prior_close: Decimal
    close: Decimal
    level: Decimal
    prior_member_digest: str
    current_member_digest: str
    previous_loss_ref: str | None = None

    @property
    def key(self) -> str:
        return digest({"kind": self.kind, "security_code": self.security_code,
                       "market_date": self.market_date, "scale": self.scale,
                       "anchor_key": self.anchor_key})

    @property
    def revision_id(self) -> str:
        return digest(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return {"interaction_key": self.key, "interaction_revision": self.revision_id,
                "concept_id": f"structure.interaction.{self.kind}", **asdict(self)}


@dataclass(frozen=True)
class EpisodeObservation:
    episode_id: str
    security_code: str
    market_date: date
    scale: int
    started_at: date
    anchor_key: str
    anchor_revision: str
    frozen_level: Decimal
    frozen_low: Decimal
    phase: str
    observation: str
    exchange_session_age: int
    observed_bar_count: int
    hold_count: int
    close: Decimal | None
    source_member_digest: str
    interaction_ref: str | None
    previous_loss_ref: str | None
    censor_reason: str | None = None

    @property
    def key(self) -> str:
        return digest({"episode_id": self.episode_id, "market_date": self.market_date})

    @property
    def revision_id(self) -> str:
        return digest(asdict(self))

    def to_dict(self) -> dict[str, Any]:
        return {"observation_key": self.key, "revision_id": self.revision_id,
                "concept_id": "structure.episode.reclaim-sequence", **asdict(self)}


@dataclass(frozen=True)
class StructureV2Result:
    anchors: tuple[Anchor, ...]
    measurements: tuple[Measurement, ...]
    interactions: tuple[Interaction, ...]
    episodes: tuple[EpisodeObservation, ...]
    availability: tuple[dict[str, Any], ...]

    def semantic(self) -> dict[str, Any]:
        return {"anchors": [item.to_dict() for item in self.anchors],
                "measurements": [item.to_dict() for item in self.measurements],
                "interactions": [item.to_dict() for item in self.interactions],
                "episodes": [item.to_dict() for item in self.episodes],
                "availability": self.availability}

    @property
    def digest(self) -> str:
        return digest(self.semantic())


@dataclass
class _Active:
    anchor: Anchor
    support: Anchor
    started: DailyInput
    episode_id: str
    phase: str = "active"
    holds: int = 0
    bars: int = 1
    loss_ref: str | None = None

    def observe(
        self, row: DailyInput, observation: str, interaction: Interaction | None = None,
        *, censor: str | None = None,
    ) -> EpisodeObservation:
        return EpisodeObservation(
            self.episode_id, row.security_code, row.market_date, self.anchor.scale,
            self.started.market_date, self.anchor.key, self.anchor.revision_id,
            self.anchor.level, self.support.level, "censored" if censor else self.phase,
            observation, row.session_index - self.started.session_index, self.bars,
            self.holds, row.close, row.member_digest,
            None if interaction is None else interaction.revision_id, self.loss_ref, censor,
        )


def _anchor(window: list[DailyInput], scale: int, side: str, spec: str, calendar: str) -> Anchor:
    values = [row.high if side == "high" else row.low for row in window]
    level = max(values) if side == "high" else min(values)
    assert isinstance(level, Decimal)
    first, last = window[0], window[-1]
    return Anchor(last.security_code, last.security_id, last.listing_id, scale, side,
                  first.market_date, last.market_date, level, last.coordinate_ref, calendar,
                  digest({"root_spec": spec, "scale": scale, "side": side, "exclude_t": True}),
                  digest([row.member_digest for row in window]))


def _measurement(row: DailyInput, anchor: Anchor, spec: str) -> Measurement:
    assert isinstance(row.close, Decimal)
    if row.coordinate_ref != anchor.coordinate_ref:
        raise ValueError("cannot compare prices and anchors in different coordinates")
    return Measurement(row.security_code, row.security_id, row.listing_id, row.market_date,
                       anchor.scale, anchor.key, anchor.revision_id, anchor.level,
                       row.coordinate_ref, row.close, signed_distance(row.close, anchor.level),
                       digest({"root_spec": spec, "formula": "close/anchor-1"}),
                       row.member_digest, BusinessTime("measurement", row.result_as_of))


def _interaction(
    kind: str, prior: DailyInput, row: DailyInput, anchor: Anchor, loss: str | None = None,
) -> Interaction:
    assert isinstance(prior.close, Decimal) and isinstance(row.close, Decimal)
    if prior.coordinate_ref != row.coordinate_ref or row.coordinate_ref != anchor.coordinate_ref:
        raise ValueError("interaction crosses a coordinate boundary")
    return Interaction(kind, row.security_code, row.market_date, anchor.scale,
                       anchor.key, anchor.revision_id, prior.close, row.close, anchor.level,
                       prior.member_digest, row.member_digest, loss)


def build_structure_v2(
    admissions: tuple[Admission, ...], *, spec_digest: str, calendar_ref: str,
    lookbacks: tuple[int, ...] = (20, 60),
) -> StructureV2Result:
    """One deterministic ordered pass; all previous output objects remain immutable.

    Inputs must enumerate the authoritative session calendar even when a security has no bar.
    A missing session index is independently detected, never concatenated across.
    """
    if not lookbacks or tuple(sorted(set(lookbacks))) != lookbacks or any(
        type(value) is not int or value < 2 for value in lookbacks
    ):
        raise ValueError("lookbacks must be sorted unique integer windows >=2")
    ordered = sorted(admissions, key=lambda item: (item.row.security_code, item.row.market_date))
    if len({item.key for item in ordered}) != len(ordered):
        raise ValueError("duplicate admitted business slot")
    references: dict[str, Anchor] = {}
    measures: dict[str, Measurement] = {}
    interactions: list[Interaction] = []
    episodes: list[EpisodeObservation] = []
    availability: list[dict[str, Any]] = []
    history: dict[str, list[DailyInput]] = {}
    active: dict[str, dict[int, _Active]] = {}

    for attempt in ordered:
        row = attempt.row
        prior = history.setdefault(row.security_code, [])
        slots = active.setdefault(row.security_code, {})
        discontinuity = row.reset_reason
        if prior and (prior[-1].coordinate_ref != row.coordinate_ref
                      or prior[-1].listing_id != row.listing_id):
            discontinuity = "identity_or_action_coordinate_change"
        if prior and row.session_index != prior[-1].session_index + 1:
            discontinuity = "unobserved_calendar_gap"
        if not attempt.admitted:
            discontinuity = attempt.reason
        if discontinuity:
            for scale in sorted(slots):
                episodes.append(slots[scale].observe(row, "censored", censor=discontinuity))
            slots.clear()
            prior.clear()
        if not attempt.admitted:
            for scale in lookbacks:
                availability.append({"security_code": row.security_code,
                                     "market_date": row.market_date, "scale": scale,
                                     "available": False, "reason": attempt.reason})
            continue
        for scale in lookbacks:
            available = len(prior) >= scale
            availability.append({"security_code": row.security_code,
                                 "market_date": row.market_date, "scale": scale,
                                 "available": available,
                                 "reason": "available" if available else "insufficient_history"})
            if not available:
                continue
            window = prior[-scale:]
            high = _anchor(window, scale, "high", spec_digest, calendar_ref)
            low = _anchor(window, scale, "low", spec_digest, calendar_ref)
            for reference in (high, low):
                references[reference.revision_id] = reference
                measurement = _measurement(row, reference, spec_digest)
                measures[measurement.identity.revision_id] = measurement
            previous = prior[-1]
            assert isinstance(previous.close, Decimal) and isinstance(row.close, Decimal)
            episode = slots.get(scale)
            if episode is None:
                if previous.close <= high.level < row.close:
                    event = _interaction("close-cross-above", previous, row, high)
                    interactions.append(event)
                    episode = _Active(high, low, row, digest({
                        "anchor_key": high.key, "start_interaction": event.key,
                    }))
                    slots[scale] = episode
                    episodes.append(episode.observe(row, "started", event))
                continue

            target = episode.anchor
            measurement = _measurement(row, target, spec_digest)
            measures[measurement.identity.revision_id] = measurement
            episode.bars += 1
            event = None
            observation = "held" if episode.phase == "active" else "below"
            if previous.close >= target.level > row.close:
                event = _interaction("close-cross-below", previous, row, target)
                if episode.loss_ref is None:
                    episode.loss_ref = event.revision_id
                episode.phase = "below"
                observation = "lost"
            elif episode.phase == "below" and previous.close <= target.level < row.close:
                if episode.loss_ref is None:
                    raise ValueError("reclaim without prior same-target loss evidence")
                event = _interaction("close-reclaim", previous, row, target, episode.loss_ref)
                episode.phase = "reclaimed"
                observation = "reclaimed"
            elif episode.phase == "active" and row.close >= target.level:
                episode.holds += 1
            if event is not None:
                interactions.append(event)
            episodes.append(episode.observe(row, observation, event))
            if episode.phase == "reclaimed":
                del slots[scale]
        prior.append(row)
        if len(prior) > max(lookbacks):
            del prior[:-max(lookbacks)]
    return StructureV2Result(
        tuple(references[key] for key in sorted(references)),
        tuple(sorted(measures.values(), key=lambda item: (
            item.security_code, item.market_date, item.scale, item.anchor_key,
        ))), tuple(interactions), tuple(episodes), tuple(availability),
    )
