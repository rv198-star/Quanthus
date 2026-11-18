"""Compose original formal experts with the trusted synthetic entry bridge."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from quanthus.capabilities.execution import SyntheticContext, validate_context
from quanthus.expert_strategy import FormalExpertContext, FormalStrategyRunner
from quanthus.expert_strategy.runner import FormalStrategyDecision
from quanthus.product_daily.application.funding_bridge import (
    CycleEntryIntent,
    EntryBridgeResult,
    FundingBridgeService,
)
from quanthus.product_daily.contracts import CommandEnvelope, require_bool
from quanthus.product_daily.risk import (
    BuyFeeRule,
    CandidateEntry,
    EntryLimits,
    ExpertEntryEligibility,
    QuantityLimits,
    RiskSnapshot,
    entry_invocation_payload,
)
from quanthus.shared.fact_contracts import FactError


@dataclass(frozen=True)
class ExpertEntryRequest:
    """Original expert inputs and entry economics, before expert eligibility exists."""

    command_id: str
    expected_account_revision: int
    expected_funding_revision: int
    candidate: CandidateEntry
    expert_context: FormalExpertContext
    trend_state: str | None
    entry_evidence_ready: bool
    snapshot: RiskSnapshot
    empty_bucket_ids: tuple[str, ...]
    active_or_reserved_security_codes: tuple[str, ...]
    active_industry_counts: tuple[tuple[str, int], ...]
    new_buckets_today: int
    limits: EntryLimits
    fraction: Decimal
    mark_price: Decimal
    price_bound: Decimal
    lot_size: int
    quantity_limits: QuantityLimits
    fee_rule: BuyFeeRule


@dataclass(frozen=True)
class ExpertEntryResult:
    decision: FormalStrategyDecision
    entry: EntryBridgeResult


class ExpertEntryService:
    """Use one original expert decision and the existing atomic entry operation."""

    def __init__(self, runner: FormalStrategyRunner, bridge: FundingBridgeService) -> None:
        self._runner = runner
        self._bridge = bridge

    def enter(self, context: SyntheticContext, request: ExpertEntryRequest) -> ExpertEntryResult:
        context = validate_context(context)
        require_bool(request.entry_evidence_ready, "entry_evidence_ready")
        request.expert_context.__post_init__()
        candidate = request.candidate
        if candidate.expert_entry is not None:
            raise FactError("EXPERT_ENTRY_OVERRIDE", "eligibility must come from original experts")
        if candidate.security_code != request.expert_context.security_code:
            raise FactError("EXPERT_SECURITY_MISMATCH", "expert and candidate security differ")
        if request.expert_context.knowledge_cutoff > context.as_of:
            raise FactError("FUTURE_EXPERT_CONTEXT", "expert inputs follow caller as_of")
        decision = self._runner.evaluate_entry(
            event_id=candidate.opportunity_id,
            context=request.expert_context,
            trend_state=request.trend_state,
            entry_evidence_ready=request.entry_evidence_ready,
        )
        candidate = replace(
            candidate,
            expert_entry=ExpertEntryEligibility.from_formal_strategy_decision(decision),
        )
        payload = entry_invocation_payload(
            candidate=candidate,
            snapshot=request.snapshot,
            empty_bucket_ids=request.empty_bucket_ids,
            active_or_reserved_security_codes=request.active_or_reserved_security_codes,
            active_industry_counts=request.active_industry_counts,
            new_buckets_today=request.new_buckets_today,
            limits=request.limits,
            fraction=request.fraction,
            mark_price=request.mark_price,
            price_bound=request.price_bound,
            lot_size=request.lot_size,
            quantity_limits=request.quantity_limits,
            fee_rule=request.fee_rule,
        )
        intent = CycleEntryIntent(
            expected_account_revision=request.expected_account_revision,
            command=CommandEnvelope.create(
                request.command_id, payload, request.expected_funding_revision
            ),
            candidate=candidate,
            snapshot=request.snapshot,
            empty_bucket_ids=request.empty_bucket_ids,
            active_or_reserved_security_codes=request.active_or_reserved_security_codes,
            active_industry_counts=request.active_industry_counts,
            new_buckets_today=request.new_buckets_today,
            limits=request.limits,
            fraction=request.fraction,
            mark_price=request.mark_price,
            price_bound=request.price_bound,
            lot_size=request.lot_size,
            quantity_limits=request.quantity_limits,
            fee_rule=request.fee_rule,
        )
        # The bridge checks new snapshots after looking up original command replay.
        # Replaying original inputs later therefore keeps the original quantity/result.
        return ExpertEntryResult(decision, self._bridge.enter(context, intent))
