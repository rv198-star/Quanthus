"""Assemble and validate a daily advice package from strictly injected views.

This module performs no real signal production, no full real FormalStrategyRunner
adaptation, and no account-fact mapping of decide_entry.  The caller injects already-
computed candidate signals (for example the output of an accepted synthetic run) and account
facts; the producer orders them, applies per-member clock admission, and emits a decision_set
whose shape matches the P12 consumer contract.  When a required member, clock, receipt, or
candidate-evidence identity is missing the result is a blocked diagnostic, never a formal
package a consumer could mistake for a real recommendation.

G1 is a *proposal* producer: it assembles and validates structure.  Real signal production,
the real expert adaptation, and account binding are explicit upstream/downstream
responsibilities and are not granted by anything here.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quanthus.event_library.contracts import require_sha256_digest
from quanthus.product_daily.contracts import (
    ProductDailyError,
    require_aware,
    require_decimal,
    require_nonnegative_int,
    require_text,
)
from quanthus.product_daily.recommendation.admission import (
    AdmissionDisposition,
    MemberAssessment,
    assess_members,
)
from quanthus.product_daily.recommendation.contracts import (
    _EXCHANGE_TZ,
    _SESSION_CLOSE,
    _SESSION_OPEN,
    ENDED,
    ENTRY_PLAN,
    ENTRY_REASON_CODES,
    EXIT_PLAN,
    EXPLANATION_CODES,
    HOLDING,
    INPUT_BLOCKED,
    INPUT_UNAVAILABLE,
    PACKAGE_VERSION,
    SCHEMA,
    STALE_MARKET_INPUT,
    USE_ACTION,
    USE_EXPLANATION,
    AuditBlock,
    DailyAdvicePackage,
    DailyFactsView,
    Decision,
    DecisionSet,
    EntryPlan,
)


def _json_digest(value: object) -> str:
    """sha256 over the canonical JSON of value (sort keys, compact, ASCII-free)."""

    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
    )


def _evidence_digest(*parts: str) -> str:
    """Bind a candidate-evidence claim to the exact identities it was issued for."""

    return _json_digest({"evidence": list(parts)})


@dataclass(frozen=True)
class CandidateSignal:
    """One injected candidate already qualified by an upstream decision.

    expert_decision_digest and risk_decision_digest are not free-form labels: the
    producer requires them to equal the canonical digest of the evidence claim bound to this
    batch, policy, candidate rule, and opportunity id.  Any other string is treated as an
    unverifiable claim and the candidate is emitted input_blocked.
    """

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
    expert_decision_digest: str | None = None
    risk_decision_digest: str | None = None

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
        for name in ("expert_decision_digest", "risk_decision_digest"):
            value = getattr(self, name)
            if value is not None:
                require_text(value, name)

    def expected_expert_digest(self, facts: DailyFactsView, policy: AdvicePolicy) -> str:
        return _evidence_digest(
            "expert",
            facts.batch_id,
            facts.intake_receipt_id,
            policy.policy_id,
            policy.version,
            policy.candidate_rule_digest,
            self.opportunity_id,
        )

    def expected_risk_digest(self, facts: DailyFactsView, policy: AdvicePolicy) -> str:
        return _evidence_digest(
            "risk",
            facts.batch_id,
            facts.intake_receipt_id,
            policy.policy_id,
            policy.version,
            policy.candidate_rule_digest,
            self.opportunity_id,
        )


@dataclass(frozen=True)
class HeldSignal:
    """One injected held-position decision already qualified by the expert path.

    Mirrors CandidateSignal but for an open position: the expert reduce/exit/hold evidence
    is bound to the batch, policy, candidate rule and security the same way, and the real
    clocks travel with the signal so the downstream decision keeps the three-layer time.
    `action` is the expert's held outcome (held | reduce | exit | hold_alert).
    """

    opportunity_id: str
    security_code: str
    security_name: str
    industry_id: str
    action: str
    component_ref_id: str
    component_version: str
    reason_codes: tuple[str, ...]
    explanations: tuple[dict[str, str], ...]
    next_observation: str
    depends_on_member_ids: tuple[str, ...]
    target_maximum_position: Decimal | None = None
    expert_decision_digest: str | None = None
    risk_decision_digest: str | None = None
    signal_close: datetime | None = None
    knowledge_cutoff: datetime | None = None
    decision_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "opportunity_id",
            "security_code",
            "industry_id",
            "action",
            "component_ref_id",
            "component_version",
        ):
            require_text(getattr(self, name), name)
        for name in (
            "expert_decision_digest",
            "risk_decision_digest",
        ):
            value = getattr(self, name)
            if value is not None:
                require_text(value, name)
        for name in ("signal_close", "knowledge_cutoff", "decision_at"):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)

    def expected_expert_digest(self, facts: DailyFactsView, policy: AdvicePolicy) -> str:
        return _evidence_digest(
            "expert-held",
            facts.batch_id,
            facts.intake_receipt_id,
            policy.policy_id,
            policy.version,
            policy.candidate_rule_digest,
            self.opportunity_id,
        )

    def expected_risk_digest(self, facts: DailyFactsView, policy: AdvicePolicy) -> str:
        return _evidence_digest(
            "risk-held",
            facts.batch_id,
            facts.intake_receipt_id,
            policy.policy_id,
            policy.version,
            policy.candidate_rule_digest,
            self.opportunity_id,
        )


@dataclass(frozen=True)
class CandidateEvaluationReceipt:
    """Positive proof that the upstream candidate producer ran to completion for this batch.

    The receipt is bound to the exact facts batch, intake receipt, policy, cutoff, and the
    injected candidate identity set through binding_digest; the producer recomputes that
    digest and refuses any mismatch.  Without it a zero-candidate input is "not run", never a
    real empty advice day, and a non-empty candidate list is unverifiable.
    """

    producer_id: str
    producer_version: str
    run_id: str
    completed_at: datetime
    coverage_digest: str
    evaluated_count: int
    qualified_count: int
    binding_digest: str
    evaluated_universe: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "producer_id",
            "producer_version",
            "run_id",
            "coverage_digest",
            "binding_digest",
        ):
            require_text(getattr(self, name), name)
        require_aware(self.completed_at, "completed_at")
        require_nonnegative_int(self.evaluated_count, "evaluated_count")
        require_nonnegative_int(self.qualified_count, "qualified_count")
        if self.qualified_count > self.evaluated_count:
            raise ProductDailyError("invalid_receipt", "qualified_count exceeds evaluated_count")
        require_sha256_digest(self.coverage_digest, "coverage_digest")
        if self.evaluated_universe:
            for code in self.evaluated_universe:
                require_text(code, "evaluated_universe[]")

    @staticmethod
    def _candidate_payload(candidates: tuple[CandidateSignal, ...]) -> list[dict[str, Any]]:
        """The full injected candidate material the receipt claims to have evaluated."""

        return [
            {
                "opportunity_id": item.opportunity_id,
                "security_code": item.security_code,
                "security_name": item.security_name,
                "industry_id": item.industry_id,
                "component_ref_id": item.component_ref_id,
                "component_version": item.component_version,
                "fraction": str(item.fraction),
                "price_bound": str(item.price_bound),
                "mark_price": str(item.mark_price),
                "reason_codes": sorted(item.reason_codes),
                "explanations": item.explanations,
                "invalidations": list(item.invalidations),
                "next_observation": item.next_observation,
                "depends_on_member_ids": sorted(item.depends_on_member_ids),
            }
            for item in sorted(candidates, key=lambda item: item.opportunity_id)
        ]

    @staticmethod
    def expected_binding_digest(
        facts: DailyFactsView,
        policy: AdvicePolicy,
        candidates: tuple[CandidateSignal, ...],
        receipt_identity: dict[str, object] | None = None,
    ) -> str:
        """Digest the receipt must carry to be valid for this produce() call.

        The digest covers the full injected candidate payload, not only ids: changing a
        candidate's component, security, marks, prices, reasons, or explanations changes the
        expected binding and makes a mismatched receipt fail closed.

        The binding also covers the exact member set (id, version, digest, available_at,
        required flag), the required-member set, the intake received/first-read/verified
        clocks, the calendar and price basis, the market and next-session dates, and the
        evaluated-universe identity the receipt claims to have covered.  Changing any member
        digest, any member's available clock, the required set, the calendar, or the price
        basis changes the expected binding and fails the receipt closed.
        """

        return _json_digest(
            {
                "batch_id": facts.batch_id,
                "intake_receipt_id": facts.intake_receipt_id,
                "source_id": facts.source_id,
                "source_version": facts.source_version,
                "calendar_version": facts.calendar_version,
                "price_basis": facts.price_basis,
                "market_date": facts.market_date.isoformat(),
                "next_session": facts.next_session.isoformat(),
                "members": [
                    {
                        "member_id": member.member_id,
                        "version": member.version,
                        "digest": member.digest,
                        "available_at": member.available_at.isoformat(),
                        "required": member.required,
                    }
                    for member in sorted(facts.members, key=lambda item: item.member_id)
                ],
                "required_member_ids": sorted(facts.required_member_ids),
                "intake_received_at": facts.intake_received_at.isoformat(),
                "intake_first_read_at": facts.intake_first_read_at.isoformat(),
                "intake_verified_at": facts.intake_verified_at.isoformat(),
                "policy_id": policy.policy_id,
                "policy_version": policy.version,
                "candidate_rule_digest": policy.candidate_rule_digest,
                "decision_cutoff": policy.decision_cutoff.isoformat(),
                "candidates": CandidateEvaluationReceipt._candidate_payload(candidates),
                # The run identity the receipt claims (producer, version, run id, evaluated
                # universe): binding it makes a self-minted receipt for a different run or
                # universe fail closed even when the field-level digests are recomputed.
                "receipt_identity": receipt_identity,
            }
        )

    def expected_coverage_digest(self) -> str:
        """The coverage digest the receipt must carry for this exact run.

        It binds the evaluated-universe identity, the producer identity, and the run id so a
        free-form 'not-a-digest-and-not-bound-to-a-universe' string can never pass.  The
        producer recomputes it and refuses any mismatch.
        """

        return _json_digest(
            {
                "producer_id": self.producer_id,
                "producer_version": self.producer_version,
                "run_id": self.run_id,
                "evaluated_universe": sorted(self.evaluated_universe),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "run_id": self.run_id,
            "completed_at": self.completed_at.isoformat(),
            "coverage_digest": self.coverage_digest,
            "evaluated_count": self.evaluated_count,
            "qualified_count": self.qualified_count,
            "binding_digest": self.binding_digest,
        }


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
    # The producer identities this policy trusts to have actually run the candidate
    # evaluation in this job.  A receipt from any other producer_id is refused, so a
    # caller cannot mint a self-consistent receipt for a run that never happened.
    trusted_producer_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "policy_id",
            "version",
            "window_ref",
            "candidate_rule_digest",
            "producer_version",
        ):
            require_text(getattr(self, name), name)
        for pid in self.trusted_producer_ids:
            require_text(pid, "trusted_producer_ids[]")
        require_aware(self.decision_cutoff, "decision_cutoff")
        require_aware(self.published_at, "published_at")
        require_aware(self.eligible_at, "eligible_at")
        require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.eligible_at:
            raise ProductDailyError("invalid_window", "entry window must be non-empty")
        eligible_local = self.eligible_at.astimezone(_EXCHANGE_TZ)
        expires_local = self.expires_at.astimezone(_EXCHANGE_TZ)
        if eligible_local.time() < _SESSION_OPEN or eligible_local.time() > _SESSION_CLOSE:
            raise ProductDailyError(
                "invalid_window", "eligible_at must fall inside the session open/close"
            )
        if expires_local.time() > _SESSION_CLOSE:
            raise ProductDailyError("invalid_window", "expires_at must not pass the session close")
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
        if self.package is None or not self.assessment.publishable:
            return False
        # A package that carries any blocked decision is a mixed/unsafe package, not a
        # publishable advice day.
        return all(
            decision.stage != INPUT_BLOCKED
            for decision_set in self.package.decision_sets
            for decision in decision_set.decisions
        )


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

    def _block(
        self,
        *,
        diagnostics: list[dict[str, Any]],
        assessment: MemberAssessment,
        disposition: AdmissionDisposition,
        reason: str,
        detail: dict[str, Any],
    ) -> ProducerResult:
        diagnostics.append({"reason": reason, **detail})
        return ProducerResult(
            package=None,
            disposition=disposition,
            diagnostics=tuple(diagnostics),
            assessment=assessment,
        )

    def _audit(
        self,
        *,
        facts: DailyFactsView,
        policy: AdvicePolicy,
        git_revision: str | None,
        receipt: CandidateEvaluationReceipt | None,
        admission_disposition: str,
        empty_reason: str | None,
        per_member_clock: tuple[tuple[str, str], ...],
    ) -> AuditBlock:
        return AuditBlock(
            producer_version=policy.producer_version,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            source_version=facts.source_version,
            candidate_rule_digest=policy.candidate_rule_digest,
            decision_cutoff=policy.decision_cutoff,
            per_member_available_at=per_member_clock,
            admission_disposition=admission_disposition,
            empty_reason=empty_reason,
            git_revision=git_revision,
            intake_receipt_id=facts.intake_receipt_id,
            evaluation_receipt=None if receipt is None else receipt.to_dict(),
        )

    def _blocked_decision(
        self,
        *,
        signal: "CandidateSignal | HeldSignal | None",
        experts: ExpertBundle,
        code: str,
        text: str,
        fallback_reason: str = INPUT_UNAVAILABLE,
    ) -> Decision:
        """One input_blocked decision carrying only a P12-legal reason code."""

        explanations: tuple[dict[str, str], ...] = (
            signal.explanations if signal is not None and signal.explanations else ()
        )
        if not any(item.get("use") == USE_EXPLANATION for item in explanations):
            explanations = (
                *explanations,
                {
                    "code": code,
                    "use": USE_EXPLANATION,
                    "component_ref": f"{experts.ref_id}@{experts.version}",
                    "text": text,
                },
            )
        return Decision(
            opportunity_id=signal.opportunity_id if signal is not None else "input-blocked",
            security_code=signal.security_code if signal is not None else "000000",
            security_name=signal.security_name if signal is not None else "input blocked",
            stage=INPUT_BLOCKED,
            component_ref_id=(signal.component_ref_id if signal is not None else experts.ref_id),
            component_version=(signal.component_version if signal is not None else experts.version),
            reason_codes=(fallback_reason,),
            explanations=explanations,
            next_observation=(
                signal.next_observation
                if signal is not None
                else "reassess after the named input reaches a qualified version"
            ),
        )

    def produce(
        self,
        *,
        facts: DailyFactsView,
        account: AccountFactsView,
        policy: AdvicePolicy,
        experts: ExpertBundle,
        candidates: tuple[CandidateSignal, ...],
        held_signals: tuple[HeldSignal, ...] = (),
        evaluation_receipt: CandidateEvaluationReceipt | None = None,
    ) -> ProducerResult:
        diagnostics: list[dict[str, Any]] = []
        assessment = assess_members(facts.members, policy.decision_cutoff)
        per_member_clock = tuple(
            (item.member_id, item.available_at.isoformat()) for item in facts.members
        )
        git_revision = _git_revision(self._repo_root) if self._repo_root else None

        try:
            return self._produce(
                facts=facts,
                account=account,
                policy=policy,
                experts=experts,
                candidates=candidates,
                held_signals=held_signals,
                evaluation_receipt=evaluation_receipt,
                diagnostics=diagnostics,
                assessment=assessment,
                per_member_clock=per_member_clock,
                git_revision=git_revision,
            )
        except ProductDailyError as exc:
            # A contract-level refusal while building a decision (bad explanation, bogus
            # reason, invalid window) is an input defect, not a runtime crash: convert it
            # to a fail-closed diagnostic so no partial or malformed package escapes.
            diagnostics.append(
                {
                    "reason": "contract_violation",
                    "detail": str(exc),
                }
            )
            return ProducerResult(
                package=None,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                diagnostics=tuple(diagnostics),
                assessment=assessment,
            )

    def _produce(
        self,
        *,
        facts: DailyFactsView,
        account: AccountFactsView,
        policy: AdvicePolicy,
        experts: ExpertBundle,
        candidates: tuple[CandidateSignal, ...],
        held_signals: tuple[HeldSignal, ...],
        evaluation_receipt: CandidateEvaluationReceipt | None,
        diagnostics: list[dict[str, Any]],
        assessment: MemberAssessment,
        per_member_clock: tuple[tuple[str, str], ...],
        git_revision: str | None,
    ) -> ProducerResult:

        # --- hard input gates, all fail closed -----------------------------------------
        if facts.intake_received_at > policy.decision_cutoff:
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="intake_received_after_cutoff",
                detail={
                    "intake_received_at": facts.intake_received_at.isoformat(),
                    "decision_cutoff": policy.decision_cutoff.isoformat(),
                },
            )
        if facts.intake_verified_at > policy.decision_cutoff:
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="intake_verified_after_cutoff",
                detail={
                    "intake_verified_at": facts.intake_verified_at.isoformat(),
                    "decision_cutoff": policy.decision_cutoff.isoformat(),
                },
            )
        if facts.intake_first_read_at > policy.decision_cutoff:
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="intake_first_read_after_cutoff",
                detail={
                    "intake_first_read_at": facts.intake_first_read_at.isoformat(),
                    "decision_cutoff": policy.decision_cutoff.isoformat(),
                },
            )

        # The candidate evaluation receipt is mandatory on every path: a non-empty
        # candidate list is unverifiable without it, and an empty one is "not run".
        if evaluation_receipt is None:
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="candidate_evaluation_receipt_missing",
                detail={"decision_cutoff": policy.decision_cutoff.isoformat()},
            )
        if evaluation_receipt.completed_at > policy.decision_cutoff:
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="candidate_evaluation_after_cutoff",
                detail={
                    "completed_at": evaluation_receipt.completed_at.isoformat(),
                    "decision_cutoff": policy.decision_cutoff.isoformat(),
                },
            )
        if evaluation_receipt.evaluated_count == 0:
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="candidate_evaluation_empty_scope",
                detail={
                    "evaluated_count": 0,
                    "note": "an evaluated scope of zero can prove neither a candidate day "
                    "nor an empty advice day",
                },
            )
        if evaluation_receipt.completed_at < facts.intake_verified_at:
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="candidate_evaluation_before_intake_verified",
                detail={
                    "completed_at": evaluation_receipt.completed_at.isoformat(),
                    "intake_verified_at": facts.intake_verified_at.isoformat(),
                    "note": "a candidate evaluation must see the verified intake, not run "
                    "before it",
                },
            )
        # The receipt must come from a producer this policy actually trusts to have run
        # the evaluation in this job; a self-consistent receipt for a run that never ran
        # is refused before any field check.
        if (
            policy.trusted_producer_ids
            and evaluation_receipt.producer_id not in policy.trusted_producer_ids
        ):
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="untrusted_evaluation_producer",
                detail={
                    "producer_id": evaluation_receipt.producer_id,
                    "trusted": sorted(policy.trusted_producer_ids),
                },
            )
        expected_binding = CandidateEvaluationReceipt.expected_binding_digest(
            facts,
            policy,
            candidates,
            receipt_identity={
                "producer_id": evaluation_receipt.producer_id,
                "producer_version": evaluation_receipt.producer_version,
                "run_id": evaluation_receipt.run_id,
                "evaluated_universe": sorted(evaluation_receipt.evaluated_universe),
            },
        )
        if evaluation_receipt.binding_digest != expected_binding:
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="candidate_evaluation_binding_mismatch",
                detail={
                    "expected": expected_binding,
                    "actual": evaluation_receipt.binding_digest,
                },
            )
        # The coverage digest must be the run-bound identity the producer recomputes: a
        # free-form or unbound coverage string can never pass as a real evaluated universe.
        expected_coverage = evaluation_receipt.expected_coverage_digest()
        if evaluation_receipt.coverage_digest != expected_coverage:
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="candidate_evaluation_coverage_mismatch",
                detail={
                    "expected": expected_coverage,
                    "actual": evaluation_receipt.coverage_digest,
                },
            )

        # --- member clocks --------------------------------------------------------------
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
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=assessment.disposition,
                reason="required_member_clock_blocked",
                detail={"decision_cutoff": policy.decision_cutoff.isoformat()},
            )

        # --- per-candidate admission -----------------------------------------------------
        admitted_ids = {item.member_id for item in assessment.admitted_members}
        decisions: list[Decision] = []
        publishable_candidates = 0
        seen_ids: set[str] = set()
        seen_security_codes: set[str] = set()
        for signal in candidates:
            if signal.opportunity_id in seen_ids:
                diagnostics.append(
                    {
                        "opportunity_id": signal.opportunity_id,
                        "reason": "duplicate_opportunity_id",
                    }
                )
                decisions.append(
                    self._blocked_decision(
                        signal=signal,
                        experts=experts,
                        code="DUPLICATE_IDENTITY",
                        text="opportunity id collides inside this candidate set",
                        fallback_reason=INPUT_UNAVAILABLE,
                    )
                )
                continue
            seen_ids.add(signal.opportunity_id)

            if signal.security_code in seen_security_codes:
                diagnostics.append(
                    {
                        "opportunity_id": signal.opportunity_id,
                        "reason": "duplicate_security_code",
                        "security_code": signal.security_code,
                    }
                )
                decisions.append(
                    self._blocked_decision(
                        signal=signal,
                        experts=experts,
                        code="DUPLICATE_SECURITY",
                        text="two live candidates claim the same security code",
                        fallback_reason=INPUT_UNAVAILABLE,
                    )
                )
                continue
            seen_security_codes.add(signal.security_code)

            unknown_reasons = [
                code for code in signal.reason_codes if code not in ENTRY_REASON_CODES
            ]
            if unknown_reasons:
                diagnostics.append(
                    {
                        "opportunity_id": signal.opportunity_id,
                        "reason": "unregistered_reason_code",
                        "unknown": unknown_reasons,
                    }
                )
                decisions.append(
                    self._blocked_decision(
                        signal=signal,
                        experts=experts,
                        code="UNREGISTERED_REASON",
                        text="candidate carries a reason code outside the registered set",
                        fallback_reason=INPUT_UNAVAILABLE,
                    )
                )
                continue

            # An injected explanation must carry a registered use, code and component before
            # it can reach a decision; anything else is an unverifiable claim that fails the
            # whole call closed rather than producing a malformed or mislabeled decision.
            bad_explanations = [
                item
                for item in signal.explanations
                if item.get("use") not in (USE_ACTION, USE_EXPLANATION)
                or not item.get("code")
                or item.get("code") not in EXPLANATION_CODES
                or not item.get("component_ref")
            ]
            if bad_explanations:
                return self._block(
                    diagnostics=diagnostics,
                    assessment=assessment,
                    disposition=AdmissionDisposition.INPUT_NOT_READY,
                    reason="invalid_explanation",
                    detail={
                        "opportunity_id": signal.opportunity_id,
                        "invalid": bad_explanations,
                    },
                )

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
                    self._blocked_decision(
                        signal=signal,
                        experts=experts,
                        code="INPUT_CLOCK",
                        text="a member this candidate depends on did not reach a "
                        "qualified consumer-visible version before the cutoff",
                        fallback_reason=STALE_MARKET_INPUT,
                    )
                )
                continue

            expected_expert = signal.expected_expert_digest(facts, policy)
            expected_risk = signal.expected_risk_digest(facts, policy)
            if (
                signal.expert_decision_digest != expected_expert
                or signal.risk_decision_digest != expected_risk
            ):
                diagnostics.append(
                    {
                        "opportunity_id": signal.opportunity_id,
                        "reason": "qualification_evidence_mismatch",
                        "expected_expert": expected_expert,
                        "expected_risk": expected_risk,
                        "expert_decision_digest": signal.expert_decision_digest,
                        "risk_decision_digest": signal.risk_decision_digest,
                    }
                )
                decisions.append(
                    self._blocked_decision(
                        signal=signal,
                        experts=experts,
                        code="QUALIFICATION_EVIDENCE",
                        text="candidate lacks the batch-bound expert and risk "
                        "decision evidence this package requires",
                        fallback_reason=INPUT_UNAVAILABLE,
                    )
                )
                continue

            if not any(item.get("use") == USE_ACTION for item in signal.explanations):
                diagnostics.append(
                    {
                        "opportunity_id": signal.opportunity_id,
                        "reason": "missing_action_explanation",
                    }
                )
                decisions.append(
                    self._blocked_decision(
                        signal=signal,
                        experts=experts,
                        code="MISSING_ACTION_EXPLANATION",
                        text="an actionable candidate needs an action-use explanation",
                        fallback_reason=INPUT_UNAVAILABLE,
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
                        invalidations=signal.invalidations,
                    ),
                )
            )
            publishable_candidates += 1

        # The receipt's qualified_count must match the candidates that actually made an
        # entry_plan; anything else means the upstream count no longer describes this batch.
        if evaluation_receipt.qualified_count != publishable_candidates:
            return self._block(
                diagnostics=diagnostics,
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="receipt_candidate_mismatch",
                detail={
                    "qualified_count": evaluation_receipt.qualified_count,
                    "publishable_candidates": publishable_candidates,
                },
            )

        # --- held positions: independent traversal, real held signals ----------------------
        # Held decisions travel the same decision set as entries.  A held signal that is
        # evaluable becomes holding / exit_plan (a reduce is still an exit-class action);
        # a held signal whose evidence cannot be verified becomes input_blocked; a held
        # position whose facts are unavailable arrives as a hold_alert HeldSignal and is
        # emitted HOLDING with an alert explanation, never a forced sale and never dropped.
        for held in held_signals:
            missing = [mid for mid in held.depends_on_member_ids if mid not in admitted_ids]
            if missing:
                diagnostics.append(
                    {
                        "opportunity_id": held.opportunity_id,
                        "missing_member_ids": missing,
                        "reason": "member_clock_blocked",
                    }
                )
                decisions.append(
                    self._blocked_decision(
                        signal=held,
                        experts=experts,
                        code="INPUT_CLOCK",
                        text="a member this held decision depends on did not reach a "
                        "qualified consumer-visible version before the cutoff",
                        fallback_reason=STALE_MARKET_INPUT,
                    )
                )
                continue
            expected_expert = held.expected_expert_digest(facts, policy)
            expected_risk = held.expected_risk_digest(facts, policy)
            if (
                held.expert_decision_digest is not None
                and held.risk_decision_digest is not None
                and (
                    held.expert_decision_digest != expected_expert
                    or held.risk_decision_digest != expected_risk
                )
            ):
                diagnostics.append(
                    {
                        "opportunity_id": held.opportunity_id,
                        "reason": "qualification_evidence_mismatch",
                        "expert_decision_digest": held.expert_decision_digest,
                        "risk_decision_digest": held.risk_decision_digest,
                    }
                )
                decisions.append(
                    self._blocked_decision(
                        signal=held,
                        experts=experts,
                        code="QUALIFICATION_EVIDENCE",
                        text="held decision lacks the batch-bound expert evidence this "
                        "package requires",
                        fallback_reason=INPUT_UNAVAILABLE,
                    )
                )
                continue
            stage = EXIT_PLAN if held.action in {"reduce", "exit"} else HOLDING
            explanations: tuple[dict[str, str], ...] = held.explanations
            if held.action == "hold_alert" and not any(
                item.get("use") == USE_EXPLANATION for item in explanations
            ):
                explanations = (
                    *explanations,
                    {
                        "code": "INPUT_CLOCK",
                        "use": USE_EXPLANATION,
                        "component_ref": f"{experts.ref_id}@{experts.version}",
                        "text": "position facts unavailable; hold and alert, never a forced sale",
                    },
                )
            decisions.append(
                Decision(
                    opportunity_id=held.opportunity_id,
                    security_code=held.security_code,
                    security_name=held.security_name,
                    stage=stage,
                    component_ref_id=held.component_ref_id,
                    component_version=held.component_version,
                    reason_codes=held.reason_codes,
                    explanations=explanations,
                    next_observation=held.next_observation,
                )
            )

        if not decisions:
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

        audit = self._audit(
            facts=facts,
            policy=policy,
            git_revision=git_revision,
            receipt=evaluation_receipt,
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
            per_member_clock=per_member_clock,
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
