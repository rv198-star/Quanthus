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
from datetime import date, datetime, time, timedelta, timezone
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

# Session bounds of the active daily profile (close-to-next-open-v1): the next-session
# window may only open at/after 09:30 and close at/before 15:00 exchange time
# (Asia/Shanghai, matching P12 decision_access._EXCHANGE_TZ).
EXCHANGE_TZ = timezone(timedelta(hours=8))
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(15, 0)
# Backwards-compatible private aliases used by EntryPlan/AdvicePolicy checks.
_EXCHANGE_TZ = EXCHANGE_TZ
_SESSION_OPEN = SESSION_OPEN
_SESSION_CLOSE = SESSION_CLOSE

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

# The only reason codes the P12 consumer accepts on an input_blocked decision
# (capabilities.decision_access.validate_decision_set).
STALE_MARKET_INPUT = "STALE_MARKET_INPUT"
INPUT_UNAVAILABLE = "INPUT_UNAVAILABLE"
BLOCKED_REASON_CODES = frozenset({STALE_MARKET_INPUT, INPUT_UNAVAILABLE})

# Reason codes a produced entry_plan decision may carry on the wire.  The P12 gate
# (validate_decision_set) does not police entry reason codes, so the producer keeps its own
# registered set: an injected candidate carrying anything else is an input defect and is
# emitted input_blocked, never passed through.
ENTRY_REASON_CODES = frozenset({NEW_ENTRY_SIGNAL})
# Reason codes a non-blocked, non-entry decision may carry (none today; ended/waiting carry
# no business reason codes in this profile).
NON_LIVE_REASON_CODES: frozenset[str] = frozenset()
REGISTERED_REASON_CODES = ENTRY_REASON_CODES | BLOCKED_REASON_CODES | NON_LIVE_REASON_CODES

# Explanation codes registered for this producer.  The action-use explanation on an entry
# must be one of these; anything else is an unverifiable claim.
EXPLANATION_CODES = frozenset(
    {
        "REPLAY_ENTRY",
        "REPLAY_SIZING",
        "NO_QUALIFIED_ENTRY",
        "INPUT_CLOCK",
        "QUALIFICATION_EVIDENCE",
        "UNREGISTERED_REASON",
        "MISSING_ACTION_EXPLANATION",
        "DUPLICATE_IDENTITY",
    }
)

# Explanation `use` values match P12's ExplanationUse enum.
USE_ACTION = "action"
USE_EXPLANATION = "explanation"
EXPLANATION_USES = frozenset({USE_ACTION, USE_EXPLANATION})


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
        if self.expires_at <= self.eligible_at:
            raise ProductDailyError("invalid_window", "entry window must be non-empty")
        # P12 execution profile bounds (close-to-next-open-v1 session hours): convert to
        # exchange time before comparing, matching P12's execution_window_issue exactly.
        eligible_local = self.eligible_at.astimezone(EXCHANGE_TZ)
        expires_local = self.expires_at.astimezone(EXCHANGE_TZ)
        if eligible_local.time() < _SESSION_OPEN or eligible_local.time() > _SESSION_CLOSE:
            raise ProductDailyError(
                "invalid_window", "eligible_at must fall inside the session open/close"
            )
        if expires_local.time() > _SESSION_CLOSE:
            raise ProductDailyError("invalid_window", "expires_at must not pass the session close")
        if expires_local.date() != eligible_local.date():
            raise ProductDailyError(
                "invalid_window", "entry window must open and close on the same session"
            )
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
        for code in self.reason_codes:
            if code not in REGISTERED_REASON_CODES:
                raise ProductDailyError("invalid_reason", f"unregistered reason code: {code}")
        if self.stage == ENTRY_PLAN and not self.reason_codes:
            raise ProductDailyError(
                "invalid_reason", "an entry_plan must carry a registered reason"
            )
        if self.stage == ENTRY_PLAN and not all(
            code in ENTRY_REASON_CODES for code in self.reason_codes
        ):
            raise ProductDailyError(
                "invalid_reason", "entry_plan may only carry entry reason codes"
            )
        if self.stage == INPUT_BLOCKED:
            if not any(code in BLOCKED_REASON_CODES for code in self.reason_codes):
                raise ProductDailyError(
                    "invalid_reason",
                    "input_blocked requires STALE_MARKET_INPUT or INPUT_UNAVAILABLE",
                )
            if any(code in ENTRY_REASON_CODES for code in self.reason_codes):
                raise ProductDailyError(
                    "invalid_reason",
                    "an input_blocked decision may not carry an entry reason",
                )
        for item in self.explanations:
            use = item.get("use")
            if use not in EXPLANATION_USES:
                raise ProductDailyError(
                    "invalid_explanation",
                    "explanation use must be one of " + str(sorted(EXPLANATION_USES)),
                )
            explanation_code = item.get("code")
            if not explanation_code:
                raise ProductDailyError("invalid_explanation", "explanation code must not be empty")
            if explanation_code not in EXPLANATION_CODES:
                raise ProductDailyError(
                    "invalid_explanation",
                    f"unregistered explanation code: {explanation_code}",
                )
            explanation_component = item.get("component_ref")
            if not explanation_component:
                raise ProductDailyError(
                    "invalid_explanation", "explanation component_ref must not be empty"
                )

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
    intake_receipt_id: str | None = None
    evaluation_receipt: dict[str, Any] | None = None

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
            "intake_receipt_id": self.intake_receipt_id,
            "evaluation_receipt": self.evaluation_receipt,
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
        if not isinstance(self.market_date, date) or not isinstance(self.next_session, date):
            raise ProductDailyError("invalid_date", "market_date and next_session must be dates")
        if self.knowledge_cutoff.date() != self.market_date:
            raise ProductDailyError(
                "invalid_cutoff", "knowledge_cutoff must fall on the market date"
            )
        if self.published_at < self.knowledge_cutoff:
            raise ProductDailyError("invalid_publish", "published_at precedes the knowledge cutoff")
        if self.next_session <= self.market_date:
            raise ProductDailyError("invalid_date", "next_session must follow market_date")
        if not self.decisions:
            raise ProductDailyError("invalid_package", "a decision set must not be empty")
        opportunity_ids = [item.opportunity_id for item in self.decisions]
        if len(opportunity_ids) != len(set(opportunity_ids)):
            raise ProductDailyError("invalid_decision", "opportunity ids must be unique")
        live_codes = [item.security_code for item in self.decisions if item.stage != ENDED]
        if len(live_codes) != len(set(live_codes)):
            raise ProductDailyError("invalid_decision", "one live opportunity per security")
        for item in self.decisions:
            if item.entry is not None:
                if item.entry.eligible_at <= self.knowledge_cutoff:
                    raise ProductDailyError(
                        "invalid_window", "entry may first execute after the cutoff"
                    )
                eligible_local = item.entry.eligible_at.astimezone(EXCHANGE_TZ)
                expires_local = item.entry.expires_at.astimezone(EXCHANGE_TZ)
                if eligible_local.date() != self.next_session:
                    raise ProductDailyError(
                        "invalid_window", "entry window must open on next_session"
                    )
                if expires_local.date() != self.next_session:
                    raise ProductDailyError(
                        "invalid_window", "entry window must close on next_session"
                    )

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
        if not self.decision_sets:
            raise ProductDailyError("invalid_package", "a package must carry a decision set")
        dates = [item.market_date for item in self.decision_sets]
        if len(dates) != len(set(dates)):
            raise ProductDailyError("invalid_package", "duplicate market_date inside one package")

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
    file_sha256: str
    decision_set_digests: tuple[tuple[str, str], ...]
    p12_package_digests: tuple[tuple[str, str], ...] | None
    decision_count: int
    entry_plan_count: int
    admission_disposition: str
    published_at: datetime
    elapsed_ms: int

    def __post_init__(self) -> None:
        require_text(self.package_id, "package_id")
        require_text(self.version, "version")
        require_text(self.path, "path")
        require_text(self.file_sha256, "file_sha256")
        require_aware(self.published_at, "published_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "version": self.version,
            "path": self.path,
            "file_sha256": self.file_sha256,
            "decision_set_digests": dict(self.decision_set_digests),
            "p12_package_digests": (
                None if self.p12_package_digests is None else dict(self.p12_package_digests)
            ),
            "decision_count": self.decision_count,
            "entry_plan_count": self.entry_plan_count,
            "admission_disposition": self.admission_disposition,
            "published_at": self.published_at.isoformat(),
            "elapsed_ms": self.elapsed_ms,
        }
