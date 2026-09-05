"""Bounded structure-intelligence MVP over admitted daily DWD projections.

This module is intentionally research-only.  It proves that a purpose-specific immutable
application projection can carry point-in-time daily structure facts into the existing expert and
aggregator contracts without activating the draft twelve-state vocabulary or creating execution
authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from quanthus.expert_protocols import (
    ExpertApplicability,
    ExpertContext,
    ScoringOutput,
)
from quanthus.feature_registry import (
    FeatureCutoffRule,
    FeatureLineage,
    FeatureSourceEvidence,
)
from quanthus.label_registry import FeatureFact

MVP_ADM_ID = "structure-research-adm"
MVP_ADM_VERSION = "0.1.0"
MVP_FEATURE_ID = "structure.mvp.state"
MVP_FEATURE_VERSION = "0.1.0"
MVP_EXPERT_ID = "expert.structure-mvp"
MVP_EXPERT_VERSION = "0.1.0"
MVP_LOOKBACK = 20


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


FEATURE_DEFINITION_DIGEST = _digest(
    {
        "feature_id": MVP_FEATURE_ID,
        "version": MVP_FEATURE_VERSION,
        "purpose": "technical-proof coarse structure state",
        "lookback": MVP_LOOKBACK,
        "inputs": ["close", "high", "low", "volume"],
        "authority": "research-only",
    }
)


class StructureMvpState(str, Enum):
    """Coarse research outcomes used only to prove the DWD-to-expert path."""

    UNAVAILABLE = "unavailable"
    RANGE_INTACT = "range_intact"
    BREAKOUT_ATTEMPT = "breakout_attempt"
    BREAKOUT_ACCEPTED = "breakout_accepted"
    SUPPORT_THREATENED = "support_threatened"
    SUPPORT_BREACH = "support_breach"


@dataclass(frozen=True)
class StructureSourceRow:
    """Minimal admitted daily record consumed from a DWD-derived projection."""

    market_date: date
    security_code: str
    dwd_security_id: str
    snapshot_id: str
    knowledge_cutoff: datetime
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    source_revision: str
    source_artifact_sha256: str
    history_reset: bool = False

    def __post_init__(self) -> None:
        if not self.security_code.strip() or not self.dwd_security_id.strip():
            raise ValueError("structure source identity must not be empty")
        if not self.snapshot_id.strip() or not self.source_revision.strip():
            raise ValueError("structure source lineage must not be empty")
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise ValueError("structure source knowledge_cutoff must be timezone-aware")
        if self.knowledge_cutoff.date() < self.market_date:
            raise ValueError("structure source knowledge_cutoff cannot precede market_date")
        if not self.source_artifact_sha256.startswith("sha256:"):
            raise ValueError("structure source artifact must use sha256 identity")
        if any(not value.is_finite() for value in (self.high, self.low, self.close)):
            raise ValueError("structure prices must be finite")
        if self.low > self.high or not self.low <= self.close <= self.high:
            raise ValueError("structure source OHLC relationship is invalid")
        if self.volume is not None and (not self.volume.is_finite() or self.volume < 0):
            raise ValueError("structure volume must be finite and non-negative")
        if not isinstance(self.history_reset, bool):
            raise ValueError("structure history_reset must be boolean")


@dataclass(frozen=True)
class StructureAdmRow:
    market_date: date
    security_code: str
    dwd_security_id: str
    snapshot_id: str
    knowledge_cutoff: datetime
    state: StructureMvpState
    close: Decimal
    prior_high20: Decimal | None
    prior_low20: Decimal | None
    volume_ratio20: Decimal | None
    source_revision: str
    source_artifact_sha256: str
    reason_code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_date": self.market_date.isoformat(),
            "security_code": self.security_code,
            "dwd_security_id": self.dwd_security_id,
            "snapshot_id": self.snapshot_id,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "state": self.state.value,
            "close": str(self.close),
            "prior_high20": None if self.prior_high20 is None else str(self.prior_high20),
            "prior_low20": None if self.prior_low20 is None else str(self.prior_low20),
            "volume_ratio20": (
                None if self.volume_ratio20 is None else str(self.volume_ratio20)
            ),
            "source_revision": self.source_revision,
            "source_artifact_sha256": self.source_artifact_sha256,
            "reason_code": self.reason_code,
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


def source_row_from_direct_dwd_projection(
    value: Mapping[str, object],
    *,
    snapshot_id: str,
    knowledge_cutoff: datetime,
) -> StructureSourceRow:
    """Adapt one existing Direct-DWD daily projection row into the MVP boundary.

    The existing projection intentionally keeps the compatibility ``security_id`` code alongside
    governed ``dwd_security_id``.  The adapter preserves both identities and requires the caller to
    supply the exact replay snapshot/cutoff rather than treating DWD ingestion timestamps as the
    historical trading decision time.
    """

    required = {
        "date",
        "security_id",
        "dwd_security_id",
        "high",
        "low",
        "close",
        "volume",
        "dwd_bar_source_revision",
        "dwd_bar_source_artifact_sha256",
    }
    missing = sorted(required.difference(value))
    if missing:
        raise ValueError(f"direct DWD structure projection is missing fields: {missing}")
    market_date = value["date"]
    if isinstance(market_date, datetime):
        market_date = market_date.date()
    if not isinstance(market_date, date):
        raise ValueError("direct DWD structure projection date is invalid")

    def decimal(field: str, *, nullable: bool = False) -> Decimal | None:
        raw = value[field]
        if raw is None:
            if nullable:
                return None
            raise ValueError(f"direct DWD structure projection {field} is null")
        result = Decimal(str(raw))
        if not result.is_finite():
            raise ValueError(f"direct DWD structure projection {field} is invalid")
        return result

    high = decimal("high")
    low = decimal("low")
    close = decimal("close")
    assert high is not None and low is not None and close is not None
    return StructureSourceRow(
        market_date=market_date,
        security_code=str(value["security_id"]),
        dwd_security_id=str(value["dwd_security_id"]),
        snapshot_id=snapshot_id,
        knowledge_cutoff=knowledge_cutoff,
        high=high,
        low=low,
        close=close,
        volume=decimal("volume", nullable=True),
        source_revision=str(value["dwd_bar_source_revision"]),
        source_artifact_sha256=str(value["dwd_bar_source_artifact_sha256"]),
    )


@dataclass(frozen=True)
class StructureResearchAdm:
    """Immutable semantic package for the MVP application boundary."""

    upstream_identity: str
    policy_digest: str
    rows: tuple[StructureAdmRow, ...]

    def __post_init__(self) -> None:
        if not self.upstream_identity.strip() or not self.policy_digest.startswith("sha256:"):
            raise ValueError("structure ADM identity is invalid")
        keys = [(row.security_code, row.market_date) for row in self.rows]
        if len(keys) != len(set(keys)):
            raise ValueError("structure ADM contains duplicate security-date rows")

    @property
    def content_digest(self) -> str:
        return _digest([row.to_dict() for row in self.rows])

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "0.1.0",
            "adm_id": MVP_ADM_ID,
            "adm_version": MVP_ADM_VERSION,
            "research_only": True,
            "execution_authorized": False,
            "upstream_identity": self.upstream_identity,
            "policy_digest": self.policy_digest,
            "row_count": len(self.rows),
            "content_digest": self.content_digest,
            "allowed_use": "structure_mvp_expert_input",
            "forbidden_claims": [
                "canonical_dwd_fact",
                "generic_dws",
                "operational_ads",
                "production_stage_model",
                "execution_authority",
            ],
        }

    @property
    def manifest_digest(self) -> str:
        return _digest(self.manifest)


def _decimal_mean(values: list[Decimal]) -> Decimal:
    if not values:
        raise ValueError("cannot average an empty decimal sequence")
    return sum(values, Decimal("0")) / Decimal(len(values))


def build_structure_research_adm(
    rows: Iterable[StructureSourceRow],
    *,
    upstream_identity: str,
    policy_digest: str,
) -> StructureResearchAdm:
    """Build a deterministic, PIT-only coarse structure projection.

    The evaluator uses only the current row plus the preceding 20 rows for that security.  It does
    not use future bars, current-membership backfills, model output, or position state.  The states
    are deliberately coarse and are not the draft thirteen-state vocabulary.
    """

    ordered = tuple(sorted(rows, key=lambda item: (item.security_code, item.market_date)))
    output: list[StructureAdmRow] = []
    history: dict[str, list[StructureSourceRow]] = {}
    attempts: dict[str, tuple[Decimal, int] | None] = {}

    for row in ordered:
        prior = history.setdefault(row.security_code, [])
        if row.history_reset:
            prior.clear()
            attempts[row.security_code] = None
        attempt = attempts.get(row.security_code)
        if len(prior) < MVP_LOOKBACK:
            state = StructureMvpState.UNAVAILABLE
            reason = "insufficient_history"
            prior_high = None
            prior_low = None
            volume_ratio = None
        else:
            window = prior[-MVP_LOOKBACK:]
            prior_high = max(item.high for item in window)
            prior_low = min(item.low for item in window)
            volumes = [item.volume for item in window]
            if row.volume is None or any(value is None for value in volumes):
                volume_ratio = None
            else:
                average_volume = _decimal_mean(
                    [value for value in volumes if value is not None]
                )
                volume_ratio = (
                    None if average_volume == 0 else row.volume / average_volume
                )

            if row.close < prior_low:
                state = StructureMvpState.SUPPORT_BREACH
                reason = "close_below_prior_low20"
                attempts[row.security_code] = None
            elif row.close <= prior_low * Decimal("1.02"):
                state = StructureMvpState.SUPPORT_THREATENED
                reason = "close_near_prior_low20"
            elif attempt is not None and row.close >= attempt[0]:
                level, count = attempt
                count += 1
                if count >= 2:
                    state = StructureMvpState.BREAKOUT_ACCEPTED
                    reason = "two_post_attempt_closes_hold_breakout"
                    attempts[row.security_code] = None
                else:
                    state = StructureMvpState.BREAKOUT_ATTEMPT
                    reason = "breakout_confirmation_pending"
                    attempts[row.security_code] = (level, count)
            elif (
                row.close > prior_high
                and volume_ratio is not None
                and volume_ratio >= Decimal("1.20")
            ):
                state = StructureMvpState.BREAKOUT_ATTEMPT
                reason = "close_above_prior_high20_with_volume"
                attempts[row.security_code] = (prior_high, 0)
            else:
                state = StructureMvpState.RANGE_INTACT
                reason = "no_mvp_structure_trigger"
                if attempt is not None and row.close < attempt[0]:
                    attempts[row.security_code] = None

        output.append(
            StructureAdmRow(
                market_date=row.market_date,
                security_code=row.security_code,
                dwd_security_id=row.dwd_security_id,
                snapshot_id=row.snapshot_id,
                knowledge_cutoff=row.knowledge_cutoff,
                state=state,
                close=row.close,
                prior_high20=prior_high,
                prior_low20=prior_low,
                volume_ratio20=volume_ratio,
                source_revision=row.source_revision,
                source_artifact_sha256=row.source_artifact_sha256,
                reason_code=reason,
            )
        )
        prior.append(row)

    return StructureResearchAdm(
        upstream_identity=upstream_identity,
        policy_digest=policy_digest,
        rows=tuple(output),
    )


def structure_feature_fact(adm: StructureResearchAdm, row: StructureAdmRow) -> FeatureFact:
    """Bind one ADM result into the existing expert context as research feature evidence."""

    lineage = FeatureLineage(
        feature_id=MVP_FEATURE_ID,
        feature_version=MVP_FEATURE_VERSION,
        definition_digest=FEATURE_DEFINITION_DIGEST,
        snapshot_id=row.snapshot_id,
        market_date=row.market_date,
        knowledge_cutoff=row.knowledge_cutoff,
        source_revision=adm.manifest_digest,
        universe_policy_version=MVP_ADM_VERSION,
        sources=(
            FeatureSourceEvidence(
                dataset=MVP_ADM_ID,
                column="state",
                dataset_version=MVP_ADM_VERSION,
                manifest_id=adm.manifest_digest,
                knowledge_cutoff=row.knowledge_cutoff,
            ),
        ),
        history_sessions=MVP_LOOKBACK + 1,
        cross_section_complete=None,
        cutoff_rule=FeatureCutoffRule.SNAPSHOT_OR_EARLIER,
    )
    return FeatureFact(
        security_code=row.security_code,
        feature_id=MVP_FEATURE_ID,
        feature_version=MVP_FEATURE_VERSION,
        lineage=lineage,
        value=row.state.value,
        unavailable_reason=None,
    )


class StructureMvpScoringExpert:
    """Plumbing-only expert mapping used to prove expert consumption and attribution."""

    expert_id = MVP_EXPERT_ID
    expert_version = MVP_EXPERT_VERSION

    _POLICY: Mapping[StructureMvpState, tuple[Decimal, Decimal, str]] = {
        StructureMvpState.UNAVAILABLE: (Decimal("0"), Decimal("0"), "structure_unavailable"),
        StructureMvpState.RANGE_INTACT: (Decimal("0"), Decimal("0.50"), "structure_range_intact"),
        StructureMvpState.BREAKOUT_ATTEMPT: (
            Decimal("0.40"),
            Decimal("0.75"),
            "structure_breakout_attempt",
        ),
        StructureMvpState.BREAKOUT_ACCEPTED: (
            Decimal("0.80"),
            Decimal("1.00"),
            "structure_breakout_accepted",
        ),
        StructureMvpState.SUPPORT_THREATENED: (
            Decimal("-0.40"),
            Decimal("0.75"),
            "structure_support_threatened",
        ),
        StructureMvpState.SUPPORT_BREACH: (
            Decimal("-1.00"),
            Decimal("1.00"),
            "structure_support_breach",
        ),
    }

    def evaluate(self, context: ExpertContext) -> ScoringOutput:
        matches = [
            fact
            for fact in context.feature_facts
            if (fact.feature_id, fact.feature_version)
            == (MVP_FEATURE_ID, MVP_FEATURE_VERSION)
        ]
        if len(matches) != 1:
            raise ValueError("structure MVP expert requires exactly one structure feature")
        fact = matches[0]
        evidence = tuple(
            item
            for item in context.evidence_refs
            if item.kind.value == "feature" and item.evidence_id == MVP_FEATURE_ID
        )
        if len(evidence) != 1:
            raise ValueError("structure MVP evidence is missing from expert context")
        state = StructureMvpState(str(fact.value))
        signal, confidence, reason = self._POLICY[state]
        if state is StructureMvpState.UNAVAILABLE:
            return ScoringOutput(
                expert_id=self.expert_id,
                expert_version=self.expert_version,
                applicability=ExpertApplicability.ABSTAINED,
                applicability_reason="structure_input_unavailable",
                reason_code=reason,
                evidence=evidence,
                signal=None,
                confidence=None,
            )
        return ScoringOutput(
            expert_id=self.expert_id,
            expert_version=self.expert_version,
            applicability=ExpertApplicability.APPLICABLE,
            applicability_reason="structure_mvp_research",
            reason_code=reason,
            evidence=evidence,
            signal=signal,
            confidence=confidence,
        )
