"""Typed contract for the R11 daily advice package (schema quanthus.r11.daily-advice.v1).

The decision-set shape intentionally mirrors the P12 `decision_sets` consumer contract
(`MarketDecisionSet` / `MarketDecision` / `EntryIntent` / `Explanation`) so a produced
package can later be handed to an existing P12 binding without a translation layer.  Nothing
here authorizes real market reads, account bindings, or publication.  The producer's
`knowledge_cutoff` is declared in advance by the caller, never inferred from
`max(available_at)`; `published_at` is the injected real publish instant and is never
backfilled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from quanthus.product_daily.contracts import (
    ProductDailyError,
    decimal_text,
    require_aware,
    require_decimal,
    require_text,
)

SCHEMA = "quanthus.r11.daily-advice.v1"
PACKAGE_VERSION = "1.0.0"

ENTRY_PLAN = "entry_plan"
WAITING = "waiting"
HOLDING = "holding"
EXIT_PLAN = "exit_plan"
INPUT_BLOCKED = "input_blocked"
ENDED = "ended"

STAGES = frozenset({ENTRY_PLAN, WAITING, HOLDING, EXIT_PLAN, INPUT_BLOCKED, ENDED})

# Reason codes already registered in the shared decision-access contract.  The producer must
# not invent new codes; anything outside this set is a contract violation that the caller must
# resolve before a package is treated as loadable.
NEW_ENTRY_SIGNAL = "NEW_ENTRY_SIGNAL"
INPUT_NOT_READY = "INPUT_NOT_READY"

# Explanation `use` values match P12's ExplanationUse enum.
USE_ACTION = "action"
USE_EXPLANATION = "explanation"


@dataclass(frozen=True)
class MemberEvidence:
    """One consumed daily member with its own visibility clock (057 per-member rule)."""

    member_id: str
    version: str
    digest: str
    available_at: datetime
    required: bool

    def __post_init__(self) -> None:
        for name in ("member_id", "version", "digest"):
            require_text(getattr(self, name), name)
        require_aware(self.available_at, "available_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "version": self.version,
            "digest": self.digest,
            "available_at": self.available_at.isoformat(),
            "required": self.required,
        }


@dataclass(frozen=True)
class DailyFactsView:
    """Injected view of one signed-off daily batch; never reads market rows itself."""

    batch_id: str
    market_date: date
    next_session: date
    source_id: str
    source_version: str
    calendar_version: str
    price_basis: str
    members: tuple[MemberEvidence, ...]
    required_member_ids: tuple[str, ...]
    intake_receipt_id: str
    intake_received_at: datetime
    intake_verified_at: datetime

    def __post_init__(self) -> None:
        for name in ("batch_id", "source_id", "source_version", "calendar_version", "price_basis"):
            require_text(getattr(self, name), name)
        if not isinstance(self.market_date, date) or not isinstance(self.next_session, date):
            raise ProductDailyError("invalid_date", "market_date and next_session must be dates")
        require_text(self.intake_receipt_id, "intake_receipt_id")
        require_aware(self.intake_received_at, "intake_received_at")
        require_aware(self.intake_verified_at, "intake_verified_at")
        if self.intake_verified_at < self.intake_received_at:
            raise ProductDailyError(
                "invalid_intake", "intake_verified_at precedes intake_received_at"
            )
        if not self.required_member_ids:
            raise ProductDailyError(
                "invalid_members", "a signed-off daily batch must name required members"
            )
        for mid in self.required_member_ids:
            require_text(mid, "required_member_ids[]")
        member_ids = {item.member_id for item in self.members}
        missing = [mid for mid in self.required_member_ids if mid not in member_ids]
        if missing:
            raise ProductDailyError(
                "invalid_members",
                f"required members absent from the signed-off batch: {missing}",
            )
        for member in self.members:
            if member.member_id in self.required_member_ids and not member.required:
                raise ProductDailyError(
                    "invalid_members",
                    f"member {member.member_id} is declared required but marked optional",
                )

    @property
    def latest_available_at(self) -> datetime:
        return max(item.available_at for item in self.members)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "market_date": self.market_date.isoformat(),
            "next_session": self.next_session.isoformat(),
            "source_id": self.source_id,
            "source_version": self.source_version,
            "calendar_version": self.calendar_version,
            "price_basis": self.price_basis,
            "members": [item.to_dict() for item in self.members],
            "required_member_ids": list(self.required_member_ids),
            "intake_receipt_id": self.intake_receipt_id,
            "intake_received_at": self.intake_received_at.isoformat(),
            "intake_verified_at": self.intake_verified_at.isoformat(),
        }


@dataclass(frozen=True)
class EntryPlan:
    fraction: Decimal
    price_bound: Decimal
    mark_price: Decimal
    industry_id: str
    eligible_at: datetime
    expires_at: datetime
    window_ref: str
    invalidations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_decimal(self.fraction, "fraction", minimum=Decimal("0"), maximum=Decimal("1"))
        require_decimal(self.price_bound, "price_bound", minimum=Decimal("0.01"))
        require_decimal(self.mark_price, "mark_price", minimum=Decimal("0.01"))
        require_text(self.industry_id, "industry_id")
        require_aware(self.eligible_at, "eligible_at")
        require_aware(self.expires_at, "expires_at")
        require_text(self.window_ref, "window_ref")
        if self.expires_at < self.eligible_at:
            raise ProductDailyError("invalid_window", "expires_at precedes eligible_at")
        for item in self.invalidations:
            require_text(item, "invalidations[]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fraction": decimal_text(self.fraction),
            "price_bound": decimal_text(self.price_bound),
            "mark_price": decimal_text(self.mark_price),
            "industry_id": self.industry_id,
            "eligible_at": self.eligible_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "window_ref": self.window_ref,
            "invalidations": list(self.invalidations),
        }


@dataclass(frozen=True)
class Decision:
    opportunity_id: str
    security_code: str
    security_name: str
    stage: str
    component_ref_id: str
    component_version: str
    reason_codes: tuple[str, ...]
    explanations: tuple[dict[str, str], ...]
    next_observation: str
    entry: EntryPlan | None = None

    def __post_init__(self) -> None:
        for name in ("opportunity_id", "security_code", "component_ref_id", "component_version"):
            require_text(getattr(self, name), name)
        if self.stage not in STAGES:
            raise ProductDailyError("invalid_stage", f"stage must be one of {sorted(STAGES)}")
        if self.stage == ENTRY_PLAN and self.entry is None:
            raise ProductDailyError("invalid_stage", "entry_plan requires an entry block")
        if self.stage != ENTRY_PLAN and self.entry is not None:
            raise ProductDailyError("invalid_stage", "only entry_plan carries an entry block")

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "security_code": self.security_code,
            "security_name": self.security_name,
            "stage": self.stage,
            "component": {"ref_id": self.component_ref_id, "version": self.component_version},
            "reason_codes": list(self.reason_codes),
            "explanations": list(self.explanations),
            "next_observation": self.next_observation,
            "entry": None if self.entry is None else self.entry.to_dict(),
        }


@dataclass(frozen=True)
class AuditBlock:
    producer_version: str
    policy_id: str
    policy_version: str
    source_version: str
    candidate_rule_digest: str
    decision_cutoff: datetime
    per_member_available_at: tuple[tuple[str, str], ...]
    admission_disposition: str
    empty_reason: str | None
    git_revision: str | None

    def __post_init__(self) -> None:
        for name in (
            "producer_version",
            "policy_id",
            "policy_version",
            "source_version",
            "candidate_rule_digest",
            "admission_disposition",
        ):
            require_text(getattr(self, name), name)
        require_aware(self.decision_cutoff, "decision_cutoff")

    def to_dict(self) -> dict[str, Any]:
        return {
            "producer_version": self.producer_version,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "source_version": self.source_version,
            "candidate_rule_digest": self.candidate_rule_digest,
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "per_member_available_at": dict(self.per_member_available_at),
            "admission_disposition": self.admission_disposition,
            "empty_reason": self.empty_reason,
            "git_revision": self.git_revision,
        }


@dataclass(frozen=True)
class DecisionSet:
    source_id: str
    version: str
    market_date: date
    knowledge_cutoff: datetime
    published_at: datetime
    next_session: date
    decisions: tuple[Decision, ...]
    audit: AuditBlock

    def __post_init__(self) -> None:
        require_text(self.source_id, "source_id")
        require_text(self.version, "version")
        require_aware(self.knowledge_cutoff, "knowledge_cutoff")
        require_aware(self.published_at, "published_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "version": self.version,
            "market_date": self.market_date.isoformat(),
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "published_at": self.published_at.isoformat(),
            "next_session": self.next_session.isoformat(),
            "decisions": [item.to_dict() for item in self.decisions],
            "audit": self.audit.to_dict(),
        }


@dataclass(frozen=True)
class DailyAdvicePackage:
    schema: str
    package_id: str
    version: str
    decision_sets: tuple[DecisionSet, ...]

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ProductDailyError("invalid_schema", f"schema must be {SCHEMA}")
        require_text(self.package_id, "package_id")
        require_text(self.version, "version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema,
            "package_id": self.package_id,
            "version": self.version,
            "decision_sets": [item.to_dict() for item in self.decision_sets],
        }


@dataclass(frozen=True)
class PublishReceipt:
    package_id: str
    version: str
    path: str
    package_digests: tuple[tuple[str, str], ...]
    decision_count: int
    entry_plan_count: int
    admission_disposition: str
    published_at: datetime
    elapsed_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "version": self.version,
            "path": self.path,
            "package_digests": dict(self.package_digests),
            "decision_count": self.decision_count,
            "entry_plan_count": self.entry_plan_count,
            "admission_disposition": self.admission_disposition,
            "published_at": self.published_at.isoformat(),
            "elapsed_ms": self.elapsed_ms,
        }
