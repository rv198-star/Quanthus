"""Continuous Structure Facts v1 over admitted daily DWD-derived observations.

This module publishes research facts, not trading states or execution authority.  It keeps
rolling support/resistance, signed margins, and one causal breakout episode explicit so consumers
can own their scoring/policy mappings without recovering hidden state from a coarse label.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from quanthus.expert_protocols import ExpertApplicability, ExpertContext, ScoringOutput
from quanthus.feature_registry import FeatureCutoffRule, FeatureLineage, FeatureSourceEvidence
from quanthus.label_registry import FeatureFact
from quanthus.structure_intelligence import StructureMvpState, StructureSourceRow

FACTS_ADM_ID = "structure-continuous-facts"
FACTS_ADM_VERSION = "1.0.0"
FACTS_LOOKBACK = 20
COMPATIBILITY_EXPERT_ID = "expert.structure-continuous-compatibility"
COMPATIBILITY_EXPERT_VERSION = "1.0.0"

FEATURES: Mapping[str, tuple[str, str]] = {
    "resistance_margin": ("structure.resistance-margin", "1.0.0"),
    "support_margin": ("structure.support-margin", "1.0.0"),
    "volume_ratio20": ("structure.volume-ratio20", "1.0.0"),
    "breakout_margin": ("structure.breakout-margin", "1.0.0"),
    "episode_support_margin": ("structure.episode-support-margin", "1.0.0"),
    "post_attempt_hold_count": ("structure.post-attempt-hold-count", "1.0.0"),
    "episode_age_sessions": ("structure.episode-age-sessions", "1.0.0"),
    "episode_status": ("structure.episode-status", "1.0.0"),
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _mean(values: list[Decimal]) -> Decimal:
    if not values:
        raise ValueError("cannot average empty structure-fact values")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _margin(value: Decimal, anchor: Decimal) -> Decimal:
    if anchor <= 0:
        raise ValueError("structure anchor must be positive")
    return value / anchor - Decimal("1")


def _episode_id(
    row: StructureSourceRow,
    *,
    breakout_level: Decimal,
    support_level: Decimal,
    window_start: date,
    window_end: date,
) -> str:
    semantic = {
        "security_code": row.security_code,
        "dwd_security_id": row.dwd_security_id,
        "attempt_at": row.market_date.isoformat(),
        "attempt_snapshot_id": row.snapshot_id,
        "breakout_level": str(breakout_level),
        "support_level": str(support_level),
        "anchor_window_start": window_start.isoformat(),
        "anchor_window_end": window_end.isoformat(),
    }
    return f"episode_{hashlib.sha256(_canonical_bytes(semantic)).hexdigest()[:24]}"


@dataclass(frozen=True)
class ContinuousStructureFactRow:
    market_date: date
    security_code: str
    dwd_security_id: str
    snapshot_id: str
    knowledge_cutoff: datetime
    source_revision: str
    source_artifact_sha256: str
    source_close: Decimal
    available: bool
    availability_reason: str | None
    history_reset: bool
    source_window_start: date | None
    source_window_end: date | None
    rolling_resistance20: Decimal | None
    rolling_support20: Decimal | None
    resistance_margin: Decimal | None
    support_margin: Decimal | None
    volume_ratio20: Decimal | None
    episode_id: str | None
    episode_status: str | None
    episode_termination_reason: str | None
    attempt_at: date | None
    attempt_snapshot_id: str | None
    breakout_level: Decimal | None
    episode_support_level: Decimal | None
    attempt_volume_ratio20: Decimal | None
    episode_anchor_window_start: date | None
    episode_anchor_window_end: date | None
    breakout_margin: Decimal | None
    episode_support_margin: Decimal | None
    post_attempt_hold_count: int | None
    episode_age_sessions: int | None

    def __post_init__(self) -> None:
        if not self.security_code.strip() or not self.dwd_security_id.strip():
            raise ValueError("continuous structure fact identity must not be empty")
        if not self.snapshot_id.strip() or not self.source_revision.strip():
            raise ValueError("continuous structure fact lineage must not be empty")
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise ValueError("continuous structure cutoff must be timezone-aware")
        if not self.source_artifact_sha256.startswith("sha256:"):
            raise ValueError("continuous structure source artifact must use sha256")
        if not self.source_close.is_finite() or self.source_close <= 0:
            raise ValueError("continuous structure source close must be positive")
        if self.available == (self.availability_reason is not None):
            raise ValueError("continuous structure availability fields disagree")
        anchor_fields = (
            self.source_window_start,
            self.source_window_end,
            self.rolling_resistance20,
            self.rolling_support20,
            self.resistance_margin,
            self.support_margin,
        )
        if self.available and any(value is None for value in anchor_fields):
            raise ValueError("available continuous structure row lacks rolling anchors")
        if not self.available and any(value is not None for value in anchor_fields):
            raise ValueError("unavailable continuous structure row cannot publish rolling anchors")
        episode_fields = (
            self.episode_status,
            self.attempt_at,
            self.attempt_snapshot_id,
            self.breakout_level,
            self.episode_support_level,
            self.episode_anchor_window_start,
            self.episode_anchor_window_end,
            self.breakout_margin,
            self.episode_support_margin,
            self.post_attempt_hold_count,
            self.episode_age_sessions,
        )
        if self.episode_id is None and any(value is not None for value in episode_fields):
            raise ValueError("continuous structure row has episode fields without episode identity")
        if self.episode_id is None and self.attempt_volume_ratio20 is not None:
            raise ValueError("continuous structure row has attempt volume without episode identity")
        if self.episode_id is not None and any(value is None for value in episode_fields):
            raise ValueError("continuous structure episode is not row-locally explainable")
        if self.episode_status not in {None, "active", "ended"}:
            raise ValueError("continuous structure episode status is invalid")
        if self.episode_status == "ended" and not self.episode_termination_reason:
            raise ValueError("ended continuous structure episode requires termination reason")
        if self.episode_status != "ended" and self.episode_termination_reason is not None:
            raise ValueError("active/non-episode row cannot carry termination reason")
        if self.post_attempt_hold_count is not None and self.post_attempt_hold_count < 0:
            raise ValueError("continuous structure hold count cannot be negative")
        if self.episode_age_sessions is not None and self.episode_age_sessions < 0:
            raise ValueError("continuous structure episode age cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        def decimal(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        def day(value: date | None) -> str | None:
            return None if value is None else value.isoformat()

        return {
            "market_date": self.market_date.isoformat(),
            "security_code": self.security_code,
            "dwd_security_id": self.dwd_security_id,
            "snapshot_id": self.snapshot_id,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "source_revision": self.source_revision,
            "source_artifact_sha256": self.source_artifact_sha256,
            "source_close": str(self.source_close),
            "available": self.available,
            "availability_reason": self.availability_reason,
            "history_reset": self.history_reset,
            "source_window_start": day(self.source_window_start),
            "source_window_end": day(self.source_window_end),
            "rolling_resistance20": decimal(self.rolling_resistance20),
            "rolling_support20": decimal(self.rolling_support20),
            "resistance_margin": decimal(self.resistance_margin),
            "support_margin": decimal(self.support_margin),
            "volume_ratio20": decimal(self.volume_ratio20),
            "episode_id": self.episode_id,
            "episode_status": self.episode_status,
            "episode_termination_reason": self.episode_termination_reason,
            "attempt_at": day(self.attempt_at),
            "attempt_snapshot_id": self.attempt_snapshot_id,
            "breakout_level": decimal(self.breakout_level),
            "episode_support_level": decimal(self.episode_support_level),
            "attempt_volume_ratio20": decimal(self.attempt_volume_ratio20),
            "episode_anchor_window_start": day(self.episode_anchor_window_start),
            "episode_anchor_window_end": day(self.episode_anchor_window_end),
            "breakout_margin": decimal(self.breakout_margin),
            "episode_support_margin": decimal(self.episode_support_margin),
            "post_attempt_hold_count": self.post_attempt_hold_count,
            "episode_age_sessions": self.episode_age_sessions,
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True)
class _Episode:
    episode_id: str
    attempt_at: date
    attempt_snapshot_id: str
    breakout_level: Decimal
    support_level: Decimal
    attempt_volume_ratio20: Decimal | None
    anchor_window_start: date
    anchor_window_end: date
    hold_count: int = 0
    age_sessions: int = 0


@dataclass(frozen=True)
class ContinuousStructureFactsAdm:
    upstream_identity: str
    policy_digest: str
    rows: tuple[ContinuousStructureFactRow, ...]

    def __post_init__(self) -> None:
        if not self.upstream_identity.strip() or not self.policy_digest.startswith("sha256:"):
            raise ValueError("continuous structure ADM identity is invalid")
        keys = [(row.security_code, row.market_date) for row in self.rows]
        if len(keys) != len(set(keys)):
            raise ValueError("continuous structure ADM contains duplicate security-date rows")

    @property
    def content_digest(self) -> str:
        return _digest([row.to_dict() for row in self.rows])

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": FACTS_ADM_VERSION,
            "adm_id": FACTS_ADM_ID,
            "adm_version": FACTS_ADM_VERSION,
            "research_only": True,
            "execution_authorized": False,
            "upstream_identity": self.upstream_identity,
            "policy_digest": self.policy_digest,
            "row_count": len(self.rows),
            "content_digest": self.content_digest,
            "allowed_use": "continuous_structure_fact_consumption",
            "forbidden_claims": [
                "canonical_dwd_fact",
                "generic_dws",
                "operational_ads",
                "authoritative_market_state",
                "execution_authority",
                "economic_value_proven",
            ],
        }

    @property
    def manifest_digest(self) -> str:
        return _digest(self.manifest)


def build_continuous_structure_facts(
    rows: Iterable[StructureSourceRow],
    *,
    upstream_identity: str,
    policy_digest: str,
) -> ContinuousStructureFactsAdm:
    """Build deterministic PIT continuous facts with one frozen breakout episode per security."""

    ordered = tuple(sorted(rows, key=lambda item: (item.security_code, item.market_date)))
    output: list[ContinuousStructureFactRow] = []
    history: dict[str, list[StructureSourceRow]] = {}
    active: dict[str, _Episode | None] = {}

    for row in ordered:
        prior = history.setdefault(row.security_code, [])
        if row.history_reset:
            prior.clear()
            active[row.security_code] = None
        episode = active.get(row.security_code)
        if len(prior) < FACTS_LOOKBACK:
            output.append(
                ContinuousStructureFactRow(
                    market_date=row.market_date,
                    security_code=row.security_code,
                    dwd_security_id=row.dwd_security_id,
                    snapshot_id=row.snapshot_id,
                    knowledge_cutoff=row.knowledge_cutoff,
                    source_revision=row.source_revision,
                    source_artifact_sha256=row.source_artifact_sha256,
                    source_close=row.close,
                    available=False,
                    availability_reason=(
                        "insufficient_history_after_reset"
                        if row.history_reset
                        else "insufficient_history"
                    ),
                    history_reset=row.history_reset,
                    source_window_start=None,
                    source_window_end=None,
                    rolling_resistance20=None,
                    rolling_support20=None,
                    resistance_margin=None,
                    support_margin=None,
                    volume_ratio20=None,
                    episode_id=None,
                    episode_status=None,
                    episode_termination_reason=None,
                    attempt_at=None,
                    attempt_snapshot_id=None,
                    breakout_level=None,
                    episode_support_level=None,
                    attempt_volume_ratio20=None,
                    episode_anchor_window_start=None,
                    episode_anchor_window_end=None,
                    breakout_margin=None,
                    episode_support_margin=None,
                    post_attempt_hold_count=None,
                    episode_age_sessions=None,
                )
            )
            prior.append(row)
            continue

        window = prior[-FACTS_LOOKBACK:]
        resistance = max(item.high for item in window)
        support = min(item.low for item in window)
        window_start = window[0].market_date
        window_end = window[-1].market_date
        resistance_margin = _margin(row.close, resistance)
        support_margin = _margin(row.close, support)
        volumes = [item.volume for item in window]
        if row.volume is None or any(value is None for value in volumes):
            volume_ratio = None
        else:
            average_volume = _mean([value for value in volumes if value is not None])
            volume_ratio = None if average_volume == 0 else row.volume / average_volume

        status: str | None = None
        termination: str | None = None
        if episode is not None:
            age = episode.age_sessions + 1
            if row.close < episode.breakout_level:
                row_episode = _Episode(
                    episode_id=episode.episode_id,
                    attempt_at=episode.attempt_at,
                    attempt_snapshot_id=episode.attempt_snapshot_id,
                    breakout_level=episode.breakout_level,
                    support_level=episode.support_level,
                    attempt_volume_ratio20=episode.attempt_volume_ratio20,
                    anchor_window_start=episode.anchor_window_start,
                    anchor_window_end=episode.anchor_window_end,
                    hold_count=episode.hold_count,
                    age_sessions=age,
                )
                status = "ended"
                termination = "close_below_breakout_level"
                active[row.security_code] = None
            else:
                row_episode = _Episode(
                    episode_id=episode.episode_id,
                    attempt_at=episode.attempt_at,
                    attempt_snapshot_id=episode.attempt_snapshot_id,
                    breakout_level=episode.breakout_level,
                    support_level=episode.support_level,
                    attempt_volume_ratio20=episode.attempt_volume_ratio20,
                    anchor_window_start=episode.anchor_window_start,
                    anchor_window_end=episode.anchor_window_end,
                    hold_count=episode.hold_count + 1,
                    age_sessions=age,
                )
                status = "active"
                active[row.security_code] = row_episode
        elif row.close > resistance:
            row_episode = _Episode(
                episode_id=_episode_id(
                    row,
                    breakout_level=resistance,
                    support_level=support,
                    window_start=window_start,
                    window_end=window_end,
                ),
                attempt_at=row.market_date,
                attempt_snapshot_id=row.snapshot_id,
                breakout_level=resistance,
                support_level=support,
                attempt_volume_ratio20=volume_ratio,
                anchor_window_start=window_start,
                anchor_window_end=window_end,
            )
            status = "active"
            active[row.security_code] = row_episode
        else:
            row_episode = None

        output.append(
            ContinuousStructureFactRow(
                market_date=row.market_date,
                security_code=row.security_code,
                dwd_security_id=row.dwd_security_id,
                snapshot_id=row.snapshot_id,
                knowledge_cutoff=row.knowledge_cutoff,
                source_revision=row.source_revision,
                source_artifact_sha256=row.source_artifact_sha256,
                source_close=row.close,
                available=True,
                availability_reason=None,
                history_reset=row.history_reset,
                source_window_start=window_start,
                source_window_end=window_end,
                rolling_resistance20=resistance,
                rolling_support20=support,
                resistance_margin=resistance_margin,
                support_margin=support_margin,
                volume_ratio20=volume_ratio,
                episode_id=None if row_episode is None else row_episode.episode_id,
                episode_status=status,
                episode_termination_reason=termination,
                attempt_at=None if row_episode is None else row_episode.attempt_at,
                attempt_snapshot_id=(
                    None if row_episode is None else row_episode.attempt_snapshot_id
                ),
                breakout_level=None if row_episode is None else row_episode.breakout_level,
                episode_support_level=None if row_episode is None else row_episode.support_level,
                attempt_volume_ratio20=(
                    None if row_episode is None else row_episode.attempt_volume_ratio20
                ),
                episode_anchor_window_start=(
                    None if row_episode is None else row_episode.anchor_window_start
                ),
                episode_anchor_window_end=(
                    None if row_episode is None else row_episode.anchor_window_end
                ),
                breakout_margin=(
                    None
                    if row_episode is None
                    else _margin(row.close, row_episode.breakout_level)
                ),
                episode_support_margin=(
                    None if row_episode is None else _margin(row.close, row_episode.support_level)
                ),
                post_attempt_hold_count=(
                    None if row_episode is None else row_episode.hold_count
                ),
                episode_age_sessions=(
                    None if row_episode is None else row_episode.age_sessions
                ),
            )
        )
        prior.append(row)

    return ContinuousStructureFactsAdm(
        upstream_identity=upstream_identity,
        policy_digest=policy_digest,
        rows=tuple(output),
    )


def render_coarse_structure_state(row: ContinuousStructureFactRow) -> StructureMvpState:
    """Render the legacy coarse vocabulary for explanation only; it does not own fact identity."""

    if not row.available:
        return StructureMvpState.UNAVAILABLE
    if row.episode_status == "active":
        if (row.post_attempt_hold_count or 0) >= 2:
            return StructureMvpState.BREAKOUT_ACCEPTED
        return StructureMvpState.BREAKOUT_ATTEMPT
    if row.support_margin is not None and row.support_margin < 0:
        return StructureMvpState.SUPPORT_BREACH
    if row.support_margin is not None and row.support_margin <= Decimal("0.02"):
        return StructureMvpState.SUPPORT_THREATENED
    return StructureMvpState.RANGE_INTACT


def _feature_definition_digest(feature_id: str, feature_version: str) -> str:
    return _digest(
        {
            "feature_id": feature_id,
            "feature_version": feature_version,
            "source": FACTS_ADM_ID,
            "source_version": FACTS_ADM_VERSION,
            "authority": "research-only-continuous-structure-fact",
        }
    )


def continuous_structure_feature_facts(
    adm: ContinuousStructureFactsAdm,
    row: ContinuousStructureFactRow,
) -> tuple[FeatureFact, ...]:
    """Project one fact row into the existing expert protocol without coarse-state mediation."""

    values: Mapping[str, object | None] = {
        "resistance_margin": row.resistance_margin,
        "support_margin": row.support_margin,
        "volume_ratio20": row.volume_ratio20,
        "breakout_margin": row.breakout_margin,
        "episode_support_margin": row.episode_support_margin,
        "post_attempt_hold_count": row.post_attempt_hold_count,
        "episode_age_sessions": row.episode_age_sessions,
        "episode_status": row.episode_status,
    }
    output: list[FeatureFact] = []
    for key in sorted(FEATURES):
        feature_id, feature_version = FEATURES[key]
        value = values[key]
        lineage = FeatureLineage(
            feature_id=feature_id,
            feature_version=feature_version,
            definition_digest=_feature_definition_digest(feature_id, feature_version),
            snapshot_id=row.snapshot_id,
            market_date=row.market_date,
            knowledge_cutoff=row.knowledge_cutoff,
            source_revision=adm.manifest_digest,
            universe_policy_version=FACTS_ADM_VERSION,
            sources=(
                FeatureSourceEvidence(
                    dataset=FACTS_ADM_ID,
                    column=key,
                    dataset_version=FACTS_ADM_VERSION,
                    manifest_id=adm.manifest_digest,
                    knowledge_cutoff=row.knowledge_cutoff,
                ),
            ),
            history_sessions=FACTS_LOOKBACK + 1,
            cross_section_complete=None,
            cutoff_rule=FeatureCutoffRule.SNAPSHOT_OR_EARLIER,
        )
        output.append(
            FeatureFact(
                security_code=row.security_code,
                feature_id=feature_id,
                feature_version=feature_version,
                lineage=lineage,
                value=value,
                unavailable_reason=(
                    None
                    if value is not None
                    else (
                        row.availability_reason
                        if not row.available
                        else "not_applicable_without_active_episode"
                    )
                ),
            )
        )
    return tuple(output)


class ContinuousStructureFactsCompatibilityExpert:
    """Research-only consumer proving direct fact ingestion through the expert protocol.

    The score is the bounded signed breakout margin only while an active episode exists. Rows
    without an active episode abstain. It is deliberately not an accepted investment mapping and
    carries no economic or execution claim.
    """

    expert_id = COMPATIBILITY_EXPERT_ID
    expert_version = COMPATIBILITY_EXPERT_VERSION

    @staticmethod
    def _fact(context: ExpertContext, name: str) -> FeatureFact:
        feature_id, feature_version = FEATURES[name]
        matches = [
            fact
            for fact in context.feature_facts
            if fact.key == (feature_id, feature_version)
        ]
        if len(matches) != 1:
            raise ValueError(f"continuous structure context lacks exactly one {name} fact")
        return matches[0]

    def evaluate(self, context: ExpertContext) -> ScoringOutput:
        resistance = self._fact(context, "resistance_margin")
        breakout = self._fact(context, "breakout_margin")
        status = self._fact(context, "episode_status")
        evidence_ids = {
            FEATURES["resistance_margin"][0],
            FEATURES["breakout_margin"][0],
            FEATURES["episode_status"][0],
        }
        evidence = tuple(
            item
            for item in context.evidence_refs
            if item.kind.value == "feature" and item.evidence_id in evidence_ids
        )
        if len(evidence) != 3:
            raise ValueError("continuous structure expert evidence is incomplete")
        if resistance.value is None:
            return ScoringOutput(
                expert_id=self.expert_id,
                expert_version=self.expert_version,
                applicability=ExpertApplicability.ABSTAINED,
                applicability_reason="continuous_structure_unavailable",
                reason_code="continuous_structure_unavailable",
                evidence=evidence,
                signal=None,
                confidence=None,
            )
        if status.value != "active" or breakout.value is None:
            return ScoringOutput(
                expert_id=self.expert_id,
                expert_version=self.expert_version,
                applicability=ExpertApplicability.ABSTAINED,
                applicability_reason="no_active_breakout_episode",
                reason_code="no_active_breakout_episode",
                evidence=evidence,
                signal=None,
                confidence=None,
            )
        if not isinstance(breakout.value, Decimal):
            raise ValueError("continuous breakout margin must be Decimal")
        signal = max(Decimal("-1"), min(Decimal("1"), breakout.value))
        return ScoringOutput(
            expert_id=self.expert_id,
            expert_version=self.expert_version,
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="active_breakout_episode_available",
            reason_code="continuous_breakout_margin_direct",
            evidence=evidence,
            signal=signal,
            confidence=Decimal("1"),
        )
