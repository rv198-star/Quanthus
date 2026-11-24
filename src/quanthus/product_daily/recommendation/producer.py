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
from quanthus.expert_strategy.registry import ExpertStrategyRegistry
from quanthus.expert_strategy.runner import FormalStrategyDecision, FormalStrategyRunner
from quanthus.policy import find_project_root
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
    EXIT_KIND_FULL,
    EXIT_KIND_REDUCE,
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

RULE_DAY_PRODUCER_VERSION = "1.1.0"


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


def _signal_evidence_payload(item: CandidateSignal | HeldSignal) -> dict[str, Any]:
    formal = (
        item.formal_decision.to_dict()
        if type(item.formal_decision) is FormalStrategyDecision
        else None
    )
    return {
        "expert_decision_digest": item.expert_decision_digest,
        "risk_decision_digest": item.risk_decision_digest,
        "formal_decision": formal,
        **{
            name: None if getattr(item, name) is None else getattr(item, name).isoformat()
            for name in ("signal_close", "knowledge_cutoff", "decision_at")
        },
    }


def _formal_signal_is_valid(
    signal: CandidateSignal | HeldSignal, experts: ExpertBundle, policy: AdvicePolicy
) -> bool:
    """Check the actual unified expert/guardian/constraint result, never a label hash.

    This validates in-process domain output. The public JSON digest helpers are content
    identities, not authentication of an external process. The daily entry point runs the
    fixed runner itself; callers do not issue a rule receipt on its behalf.
    """
    decision = signal.formal_decision
    if type(decision) is not FormalStrategyDecision:
        return False
    if (
        (signal.component_ref_id, signal.component_version) != (experts.ref_id, experts.version)
        or decision.topology_digest != experts.digest
        or signal.expert_decision_digest != decision.digest
        or signal.risk_decision_digest != decision.digest
        or signal.opportunity_id != decision.event_id
        or decision.action.governance_digest != decision.governance.digest
        or signal.knowledge_cutoff != decision.governance.knowledge_cutoff
        or signal.decision_at is None
        or signal.signal_close is None
        or signal.knowledge_cutoff is None
        or signal.knowledge_cutoff > signal.decision_at
        or signal.decision_at > policy.decision_cutoff
        or signal.signal_close > signal.decision_at
    ):
        return False
    if isinstance(signal, CandidateSignal):
        return (
            decision.action.final_action.value == "trial_entry"
            and decision.target_maximum_position == signal.fraction
        )
    expected_action = {
        "full_exit": "exit",
        "reduce": "reduce",
        "no_action": "held",
    }.get(decision.action.final_action.value)
    return (
        signal.action == expected_action
        or (signal.action == "hold_alert" and expected_action == "held")
    ) and (signal.target_maximum_position == decision.target_maximum_position)


@dataclass(frozen=True)
class CandidateSignal:
    """One injected candidate already qualified by an upstream decision.

    The normal path carries the actual FormalStrategyDecision and its digest, with the
    source and decision clocks. The original public expected_* helpers describe only G1
    structural fixture labels; the fixed-rule path never treats them as expert evidence.
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
    formal_decision: FormalStrategyDecision | None = None
    signal_close: datetime | None = None
    knowledge_cutoff: datetime | None = None
    decision_at: datetime | None = None

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
        for name in ("signal_close", "knowledge_cutoff", "decision_at"):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)

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
    formal_decision: FormalStrategyDecision | None = None

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
    """Content identity and coverage of one evaluation.

    The receipt is bound to the exact facts batch, intake receipt, policy, cutoff, and the
    injected candidate identity set through binding_digest. Public digest helpers verify
    content integrity; they do not attest that a process ran. run_daily_advice executes the
    fixed runner and issues the normal receipt inside that invocation.
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
    evaluation_started_at: datetime | None = None
    source_closure_digest: str | None = None

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
        if self.evaluation_started_at is not None:
            require_aware(self.evaluation_started_at, "evaluation_started_at")
        if self.source_closure_digest is not None:
            require_sha256_digest(self.source_closure_digest, "source_closure_digest")
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
                **_signal_evidence_payload(item),
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
        held_signals: tuple[HeldSignal, ...] = (),
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
                "policy_digest": policy.digest,
                "decision_cutoff": policy.decision_cutoff.isoformat(),
                "candidates": CandidateEvaluationReceipt._candidate_payload(candidates),
                "held_signals": [
                    _signal_evidence_payload(item)
                    | {
                        "opportunity_id": item.opportunity_id,
                        "security_code": item.security_code,
                        "security_name": item.security_name,
                        "industry_id": item.industry_id,
                        "action": item.action,
                        "target_maximum_position": str(item.target_maximum_position),
                        "component_ref_id": item.component_ref_id,
                        "component_version": item.component_version,
                        "reason_codes": list(item.reason_codes),
                        "explanations": item.explanations,
                        "next_observation": item.next_observation,
                        "depends_on_member_ids": sorted(item.depends_on_member_ids),
                    }
                    for item in sorted(held_signals, key=lambda item: item.opportunity_id)
                ],
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
            "evaluated_universe": list(self.evaluated_universe),
            "evaluation_started_at": (
                None
                if self.evaluation_started_at is None
                else self.evaluation_started_at.isoformat()
            ),
            "source_closure_digest": self.source_closure_digest,
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
    producer_version: str = RULE_DAY_PRODUCER_VERSION
    # Runtime configuration must name accepted producers. An empty set admits none;
    # allowlisting is separate from execution, which run_daily_advice owns.
    trusted_producer_ids: tuple[str, ...] = ()
    # Public label hashes are retained only for the original synthetic G1 fixture tests.
    # The normal path requires the actual fixed-runner object and its canonical digest.
    evidence_mode: str = "formal_runner"

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
        if self.evidence_mode not in {"formal_runner", "structural_fixture"}:
            raise ProductDailyError("invalid_policy", "unknown evidence mode")
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

    @property
    def binding_payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "producer_version": self.producer_version,
            "evidence_mode": self.evidence_mode,
            "candidate_rule_digest": self.candidate_rule_digest,
            "trusted_producer_ids": sorted(self.trusted_producer_ids),
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "published_at": self.published_at.isoformat(),
            "window_ref": self.window_ref,
            "eligible_at": self.eligible_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    @property
    def digest(self) -> str:
        return _json_digest(self.binding_payload)


@dataclass(frozen=True)
class ExpertBundle:
    """Version identity of the upstream expert path used to qualify candidates (no scoring here)."""

    ref_id: str
    version: str
    digest: str

    def __post_init__(self) -> None:
        for name in ("ref_id", "version", "digest"):
            require_text(getattr(self, name), name)

    @classmethod
    def from_runner(cls, runner: FormalStrategyRunner) -> ExpertBundle:
        return cls(runner.card.strategy_id, runner.card.strategy_version, runner.topology_digest)


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
        signals: tuple[CandidateSignal | HeldSignal, ...] = (),
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
            evidence_grade=(
                "TEST_ONLY_STRUCTURAL"
                if policy.evidence_mode == "structural_fixture"
                else "SYNTHETIC_FIXED_RUNNER"
            ),
            decision_evidence=tuple(
                {"opportunity_id": item.opportunity_id, **_signal_evidence_payload(item)}
                for item in signals
            ),
        )

    def _blocked_decision(
        self,
        *,
        signal: CandidateSignal | HeldSignal | None,
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
        """Injected G1 fixture API; the fixed-runner API is run_daily_advice.

        A producer name or a self-consistent content hash is not a completed run.
        The daily API accepts source inputs and executes the domain path itself.
        """
        if policy.evidence_mode != "structural_fixture":
            assessment = assess_members(facts.members, policy.decision_cutoff)
            return self._block(
                diagnostics=[],
                assessment=assessment,
                disposition=AdmissionDisposition.INPUT_NOT_READY,
                reason="same_job_rule_entry_required",
                detail={},
            )
        return self._assemble(
            facts=facts,
            account=account,
            policy=policy,
            experts=experts,
            candidates=candidates,
            held_signals=held_signals,
            evaluation_receipt=evaluation_receipt,
        )

    def _assemble(
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
        if evaluation_receipt.producer_id not in policy.trusted_producer_ids:
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
                **(
                    {
                        "source_closure_digest": evaluation_receipt.source_closure_digest,
                        "evaluation_started_at": (
                            None
                            if evaluation_receipt.evaluation_started_at is None
                            else evaluation_receipt.evaluation_started_at.isoformat()
                        ),
                        "completed_at": evaluation_receipt.completed_at.isoformat(),
                        "evaluated_count": evaluation_receipt.evaluated_count,
                        "qualified_count": evaluation_receipt.qualified_count,
                    }
                    if policy.evidence_mode == "formal_runner"
                    else {}
                ),
            },
            held_signals=held_signals,
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

        if policy.evidence_mode == "formal_runner":
            run_signals: tuple[CandidateSignal | HeldSignal, ...] = (*candidates, *held_signals)
            if policy.producer_version != evaluation_receipt.producer_version:
                return self._block(
                    diagnostics=diagnostics,
                    assessment=assessment,
                    disposition=AdmissionDisposition.INPUT_NOT_READY,
                    reason="producer_version_mismatch",
                    detail={},
                )
            if (
                evaluation_receipt.evaluation_started_at is None
                or evaluation_receipt.source_closure_digest is None
                or evaluation_receipt.evaluation_started_at < facts.intake_verified_at
                or evaluation_receipt.completed_at < evaluation_receipt.evaluation_started_at
                or any(
                    m.required and m.available_at > evaluation_receipt.evaluation_started_at
                    for m in facts.members
                )
            ):
                return self._block(
                    diagnostics=diagnostics,
                    assessment=assessment,
                    disposition=AdmissionDisposition.INPUT_NOT_READY,
                    reason="evaluation_run_clock_mismatch",
                    detail={},
                )
            registry = ExpertStrategyRegistry(self._repo_root or find_project_root())
            if experts != ExpertBundle(
                registry.card.strategy_id,
                registry.card.strategy_version,
                registry.topology_manifest.digest,
            ):
                return self._block(
                    diagnostics=diagnostics,
                    assessment=assessment,
                    disposition=AdmissionDisposition.INPUT_NOT_READY,
                    reason="unregistered_expert_bundle",
                    detail={},
                )
            if len(
                set(evaluation_receipt.evaluated_universe)
            ) != evaluation_receipt.evaluated_count or not {
                s.security_code for s in run_signals
            } <= set(evaluation_receipt.evaluated_universe):
                return self._block(
                    diagnostics=diagnostics,
                    assessment=assessment,
                    disposition=AdmissionDisposition.INPUT_NOT_READY,
                    reason="evaluation_scope_mismatch",
                    detail={},
                )
            for signal in run_signals:
                # Missing held facts are an explicit non-action, never a synthetic sale.
                if (
                    isinstance(signal, HeldSignal)
                    and signal.action == "hold_alert"
                    and signal.formal_decision is None
                    and signal.expert_decision_digest is None
                    and signal.risk_decision_digest is None
                    and signal.target_maximum_position is None
                ):
                    continue
                if not _formal_signal_is_valid(signal, experts, policy):
                    return self._block(
                        diagnostics=diagnostics,
                        assessment=assessment,
                        disposition=AdmissionDisposition.INPUT_NOT_READY,
                        reason="qualification_evidence_mismatch",
                        detail={"opportunity_id": signal.opportunity_id},
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
            if policy.evidence_mode == "structural_fixture" and (
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
            if policy.evidence_mode == "structural_fixture" and (
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
            # Held-reduction wire v2: a reduce must publish the expert's own target
            # remaining-position fraction; it is copied from the actual
            # FormalStrategyDecision.target_maximum_position the runner produced for this
            # held unit, never inferred from an explanation or supplied by P12.  A reduce
            # without that real decision (or with a missing/non-positive target) cannot be
            # serialized as an executable reduce and falls back to display-only hold+alert
            # rather than a synthetic full exit.
            exit_kind: str | None = None
            target_fraction = None
            if stage == EXIT_PLAN:
                if held.action == "exit":
                    exit_kind = EXIT_KIND_FULL
                else:
                    # An executable reduce takes its target ONLY from the actual
                    # FormalStrategyDecision the fixed runner produced for this held
                    # unit.  held.target_maximum_position is a caller-supplied claim;
                    # it is never a substitute for the typed formal decision, in any
                    # evidence mode.  No formal decision / invalid target -> the reduce
                    # degrades to display-only hold+alert below, never a caller-
                    # supplied executable reduce and never a synthetic full exit.
                    formal_target = (
                        held.formal_decision.target_maximum_position
                        if type(held.formal_decision) is FormalStrategyDecision
                        else None
                    )
                    if (
                        formal_target is None
                        or not formal_target.is_finite()
                        or formal_target <= 0
                        or formal_target > 1
                    ):
                        stage = HOLDING
                    else:
                        target_fraction = formal_target
                        exit_kind = EXIT_KIND_REDUCE
            explanations: tuple[dict[str, str], ...] = held.explanations
            if stage == HOLDING and held.action == "reduce" and exit_kind is None:
                explanations = (
                    *explanations,
                    {
                        "code": "QUALIFICATION_EVIDENCE",
                        "use": USE_EXPLANATION,
                        "component_ref": f"{experts.ref_id}@{experts.version}",
                        "text": "reduce intent missing a valid expert target fraction; "
                        "held with alert, never a full sale",
                    },
                )
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
                    exit_kind=exit_kind,
                    target_position_fraction=target_fraction,
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

        blocked_content = any(item.stage == INPUT_BLOCKED for item in decisions)
        ended_only = all(item.stage == ENDED for item in decisions)
        audit = self._audit(
            facts=facts,
            policy=policy,
            git_revision=git_revision,
            receipt=evaluation_receipt,
            admission_disposition=(
                AdmissionDisposition.INPUT_NOT_READY.value
                if blocked_content
                else ("empty_advice" if ended_only else AdmissionDisposition.PUBLISHABLE.value)
            ),
            empty_reason=(None if not ended_only else "no qualified entry candidate"),
            per_member_clock=per_member_clock,
            signals=(*candidates, *held_signals),
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
            disposition=(
                AdmissionDisposition.INPUT_NOT_READY if blocked_content else assessment.disposition
            ),
            diagnostics=tuple(diagnostics),
            assessment=assessment,
        )
