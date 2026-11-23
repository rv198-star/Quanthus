"""Assemble and validate a daily advice package from strictly injected views.

This module performs no real signal production, no full real `FormalStrategyRunner`
adaptation, and no account-fact mapping of `decide_entry`.  The caller injects already-
computed candidate signals (for example the output of an accepted synthetic run) and account
facts; the producer orders them, applies per-member clock admission, and emits a decision_set
whose shape matches the P12 consumer contract.  When a required member or clock is missing
the result is a blocked diagnostic, never a formal package a consumer could mistake for a
real recommendation.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.product_daily.contracts import (
    ProductDailyError,
    require_aware,
    require_decimal,
    require_text,
)
from quanthus.product_daily.recommendation.admission import (
    AdmissionDisposition,
    MemberAssessment,
    assess_members,
)
from quanthus.product_daily.recommendation.contracts import (
    ENDED,
    ENTRY_PLAN,
    INPUT_BLOCKED,
    PACKAGE_VERSION,
    SCHEMA,
    USE_EXPLANATION,
    AuditBlock,
    DailyAdvicePackage,
    DailyFactsView,
    Decision,
    DecisionSet,
    EntryPlan,
)


@dataclass(frozen=True)
class CandidateSignal:
    """One injected candidate already qualified by an upstream decision."""

    opportunity_id: str
    security_code: str
    security_name: str
    industry_id: str
    fraction: Decimal
    price_bound: Decimal
    mark_price: Decimal
    component_ref_id: str
    component_version: str
    reason_codes: tuple[str, ...]
    explanations: tuple[dict[str, str], ...]
    invalidations: tuple[str, ...]
    next_observation: str
    depends_on_member_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "opportunity_id",
            "security_code",
            "industry_id",
            "component_ref_id",
            "component_version",
        ):
            require_text(getattr(self, name), name)
        require_decimal(self.fraction, "fraction", minimum=Decimal("0"), maximum=Decimal("1"))
        require_decimal(self.price_bound, "price_bound", minimum=Decimal("0.01"))
        require_decimal(self.mark_price, "mark_price", minimum=Decimal("0.01"))


@dataclass(frozen=True)
class AccountFactsView:
    """Injected account facts used only to annotate plans; never a sizing authority here."""

    account_id: str
    as_of: datetime
    cash_available: Decimal | None
    active_or_reserved_security_codes: tuple[str, ...]
    active_industry_counts: tuple[tuple[str, int], ...]
    new_buckets_today: int
    pending_commitment_total: Decimal | None

    def __post_init__(self) -> None:
        require_text(self.account_id, "account_id")
        require_aware(self.as_of, "as_of")


@dataclass(frozen=True)
class AdvicePolicy:
    """Caller-declared, versioned policy; cutoff and publish instant are declared, not derived."""

    policy_id: str
    version: str
    decision_cutoff: datetime
    published_at: datetime
    window_ref: str
    eligible_at: datetime
    expires_at: datetime
    candidate_rule_digest: str
    producer_version: str = PACKAGE_VERSION

    def __post_init__(self) -> None:
        for name in (
            "policy_id",
            "version",
            "window_ref",
            "candidate_rule_digest",
            "producer_version",
        ):
            require_text(getattr(self, name), name)
        require_aware(self.decision_cutoff, "decision_cutoff")
        require_aware(self.published_at, "published_at")
        require_aware(self.eligible_at, "eligible_at")
        require_aware(self.expires_at, "expires_at")
        if self.expires_at < self.eligible_at:
            raise ProductDailyError("invalid_window", "expires_at precedes eligible_at")
        if self.published_at < self.decision_cutoff:
            raise ProductDailyError(
                "invalid_publish", "published_at precedes the declared decision_cutoff"
            )


@dataclass(frozen=True)
class ExpertBundle:
    """Version identity of the upstream expert path used to qualify candidates (no scoring here)."""

    ref_id: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        for name in ("ref_id", "version", "digest"):
            require_text(getattr(self, name), name)


@dataclass(frozen=True)
class ProducerResult:
    """Either a publishable package or a blocked diagnostic; never both."""

    package: DailyAdvicePackage | None
    disposition: AdmissionDisposition
    diagnostics: tuple[dict[str, Any], ...]
    assessment: MemberAssessment

    @property
    def publishable(self) -> bool:
        return self.package is not None and self.assessment.publishable


def _git_revision(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


class DailyAdviceProducer:
    """Assemble one decision_set for one already-signed-off daily batch."""

    def __init__(self, *, repo_root: Path | str | None = None) -> None:
        self._repo_root = Path(repo_root) if repo_root is not None else None

    def produce(
        self,
        *,
        facts: DailyFactsView,
        account: AccountFactsView,
        policy: AdvicePolicy,
        experts: ExpertBundle,
        candidates: tuple[CandidateSignal, ...],
    ) -> ProducerResult:
        diagnostics: list[dict[str, Any]] = []
        assessment = assess_members(facts.members, policy.decision_cutoff)
        per_member_clock = tuple(
            (item.member_id, item.available_at.isoformat()) for item in facts.members
        )
        git_revision = _git_revision(self._repo_root) if self._repo_root else None

        if not assessment.publishable:
            for blocked in (*assessment.blocked_required, *assessment.blocked_optional):
                diagnostics.append(
                    {
                        "member_id": blocked.member_id,
                        "required": blocked.required,
                        "reason": blocked.reason,
                        "available_at": blocked.available_at,
                        "decision_cutoff": policy.decision_cutoff.isoformat(),
                    }
                )
            audit = AuditBlock(
                producer_version=policy.producer_version,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                source_version=facts.source_version,
                candidate_rule_digest=policy.candidate_rule_digest,
                decision_cutoff=policy.decision_cutoff,
                per_member_available_at=per_member_clock,
                admission_disposition=assessment.disposition.value,
                empty_reason="required member clock not qualified before cutoff",
                git_revision=git_revision,
            )
            # Fail closed: emit a diagnostic set carrying INPUT_BLOCKED decisions only, marked
            # clearly so no consumer treats it as a real daily package.
            decisions = tuple(
                Decision(
                    opportunity_id=signal.opportunity_id,
                    security_code=signal.security_code,
                    security_name=signal.security_name,
                    stage=INPUT_BLOCKED,
                    component_ref_id=signal.component_ref_id,
                    component_version=signal.component_version,
                    reason_codes=("INPUT_NOT_READY",),
                    explanations=(
                        {
                            "code": "INPUT_CLOCK",
                            "use": USE_EXPLANATION,
                            "component_ref": f"{experts.ref_id}@{experts.version}",
                            "text": (
                                "required daily input did not reach a qualified "
                                "consumer-visible version before the declared cutoff"
                            ),
                        },
                    ),
                    next_observation="reassess after the named member reaches a qualified version",
                )
                for signal in candidates
            ) or (
                Decision(
                    opportunity_id="input-blocked",
                    security_code="000000",
                    security_name="input blocked",
                    stage=INPUT_BLOCKED,
                    component_ref_id=experts.ref_id,
                    component_version=experts.version,
                    reason_codes=("INPUT_NOT_READY",),
                    explanations=(
                        {
                            "code": "INPUT_CLOCK",
                            "use": USE_EXPLANATION,
                            "component_ref": f"{experts.ref_id}@{experts.version}",
                            "text": (
                                "required daily input did not reach a qualified "
                                "consumer-visible version before the declared cutoff"
                            ),
                        },
                    ),
                    next_observation="reassess after the named member reaches a qualified version",
                ),
            )
            decision_set = DecisionSet(
                source_id=facts.source_id,
                version=PACKAGE_VERSION,
                market_date=facts.market_date,
                knowledge_cutoff=policy.decision_cutoff,
                published_at=policy.published_at,
                next_session=facts.next_session,
                decisions=decisions,
                audit=audit,
            )
            package = DailyAdvicePackage(
                schema=SCHEMA,
                package_id=f"{facts.source_id}-{facts.market_date.isoformat()}",
                version=PACKAGE_VERSION,
                decision_sets=(decision_set,),
            )
            return ProducerResult(
                package=None,
                disposition=assessment.disposition,
                diagnostics=tuple(diagnostics),
                assessment=assessment,
            )

        # Publishable: emit one decision per injected candidate; members a given candidate
        # depends on that did not make the cutoff block only that candidate.
        admitted_ids = {item.member_id for item in assessment.admitted_members}
        decisions: list[Decision] = []
        for signal in candidates:
            missing = [mid for mid in signal.depends_on_member_ids if mid not in admitted_ids]
            if missing:
                diagnostics.append(
                    {
                        "opportunity_id": signal.opportunity_id,
                        "missing_member_ids": missing,
                        "reason": "member_clock_blocked",
                    }
                )
                decisions.append(
                    Decision(
                        opportunity_id=signal.opportunity_id,
                        security_code=signal.security_code,
                        security_name=signal.security_name,
                        stage=INPUT_BLOCKED,
                        component_ref_id=signal.component_ref_id,
                        component_version=signal.component_version,
                        reason_codes=("INPUT_NOT_READY",),
                        explanations=signal.explanations,
                        next_observation=signal.next_observation,
                    )
                )
                continue
            decisions.append(
                Decision(
                    opportunity_id=signal.opportunity_id,
                    security_code=signal.security_code,
                    security_name=signal.security_name,
                    stage=ENTRY_PLAN,
                    component_ref_id=signal.component_ref_id,
                    component_version=signal.component_version,
                    reason_codes=signal.reason_codes,
                    explanations=signal.explanations,
                    next_observation=signal.next_observation,
                    entry=EntryPlan(
                        fraction=signal.fraction,
                        price_bound=signal.price_bound,
                        mark_price=signal.mark_price,
                        industry_id=signal.industry_id,
                        eligible_at=policy.eligible_at,
                        expires_at=policy.expires_at,
                        window_ref=policy.window_ref,
                    ),
                )
            )
        if not decisions:
            # Genuine empty result: admitted members exist and no candidate was injected.
            decisions.append(
                Decision(
                    opportunity_id="no-qualified-candidate",
                    security_code="000000",
                    security_name="no qualified candidate",
                    stage=ENDED,
                    component_ref_id=experts.ref_id,
                    component_version=experts.version,
                    reason_codes=(),
                    explanations=(
                        {
                            "code": "NO_QUALIFIED_ENTRY",
                            "use": USE_EXPLANATION,
                            "component_ref": f"{experts.ref_id}@{experts.version}",
                            "text": "no qualified entry candidate for this daily package",
                        },
                    ),
                    next_observation="next session after close",
                )
            )
        audit = AuditBlock(
            producer_version=policy.producer_version,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            source_version=facts.source_version,
            candidate_rule_digest=policy.candidate_rule_digest,
            decision_cutoff=policy.decision_cutoff,
            per_member_available_at=per_member_clock,
            admission_disposition=(
                AdmissionDisposition.PUBLISHABLE.value
                if any(item.stage == ENTRY_PLAN for item in decisions)
                else "empty_advice"
            ),
            empty_reason=(
                None
                if any(item.stage == ENTRY_PLAN for item in decisions)
                else "no qualified entry candidate"
            ),
            git_revision=git_revision,
        )
        decision_set = DecisionSet(
            source_id=facts.source_id,
            version=PACKAGE_VERSION,
            market_date=facts.market_date,
            knowledge_cutoff=policy.decision_cutoff,
            published_at=policy.published_at,
            next_session=facts.next_session,
            decisions=tuple(decisions),
            audit=audit,
        )
        package = DailyAdvicePackage(
            schema=SCHEMA,
            package_id=f"{facts.source_id}-{facts.market_date.isoformat()}",
            version=PACKAGE_VERSION,
            decision_sets=(decision_set,),
        )
        return ProducerResult(
            package=package,
            disposition=assessment.disposition,
            diagnostics=tuple(diagnostics),
            assessment=assessment,
        )
