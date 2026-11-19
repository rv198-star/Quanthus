"""Narrow persistence port for the P12 synthetic fact slice."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from quanthus.capabilities.accounting import AccountProjection
from quanthus.capabilities.execution import (
    Correction,
    ExecutionReport,
    FeeAssessment,
    OrderObservation,
    SyntheticContext,
)
from quanthus.product_daily.application.history import (
    AccountSnapshot,
    ConfigurationBinding,
    FactHistory,
)
from quanthus.product_daily.application.models import (
    Baseline,
    CorrectionResult,
    Receipt,
    SubmissionResult,
)
from quanthus.product_daily.funding import (
    CycleSettlementEvidence,
    FundingDecision,
    FundingState,
    SettlementRequest,
)


class SyntheticFactsPort(Protocol):
    def establish_baseline(self, baseline: Baseline) -> AccountProjection: ...

    def receive_report(self, context: SyntheticContext, report: ExecutionReport) -> Receipt: ...

    def post_report(
        self, context: SyntheticContext, receipt: Receipt, report: ExecutionReport
    ) -> SubmissionResult: ...

    def receive_order_observation(
        self, context: SyntheticContext, observation: OrderObservation
    ) -> Receipt: ...

    def post_order_observation(
        self, context: SyntheticContext, receipt: Receipt, observation: OrderObservation,
        accepted_total_qty: int, side: str, security_code: str, price_bound: Decimal,
        estimated_fee_upper: Decimal,
    ) -> SubmissionResult: ...

    def receive_fee_assessment(
        self, context: SyntheticContext, fee: FeeAssessment
    ) -> Receipt: ...

    def post_fee_assessment(
        self, context: SyntheticContext, receipt: Receipt, fee: FeeAssessment
    ) -> SubmissionResult: ...

    def correct_fact(
        self, context: SyntheticContext, correction: Correction
    ) -> CorrectionResult: ...

    def projection(self, context: SyntheticContext) -> AccountProjection: ...

    def rebuild(self, context: SyntheticContext) -> AccountProjection: ...

    def history(self, context: SyntheticContext) -> FactHistory: ...

    def snapshot(self, context: SyntheticContext) -> AccountSnapshot: ...

    def funding_state(
        self, context: SyntheticContext
    ) -> tuple[FundingState, int] | None: ...

    def funding_accountable_cash(
        self, context: SyntheticContext
    ) -> Decimal | None: ...

    def funding_pending_by_bucket(
        self, context: SyntheticContext
    ) -> dict[str, Decimal] | None: ...

    def bind_funding_state(
        self,
        context: SyntheticContext,
        state: FundingState,
        bucket_securities: dict[str, str] | None = None,
    ) -> FundingState: ...

    def funding_settlement_evidence(
        self, context: SyntheticContext, security_codes: set[str] | None = None
    ) -> CycleSettlementEvidence: ...

    def settle_funding(
        self,
        context: SyntheticContext,
        *,
        settlement_id: str,
        requests: tuple[SettlementRequest, ...],
        close_bucket_ids: tuple[str, ...] | None,
    ) -> FundingDecision: ...

    def prepare_funding_cycle(
        self, context: SyntheticContext, *, bucket_id: str, cycle_id: str
    ) -> FundingDecision: ...

    def bind_configuration(
        self, context: SyntheticContext, binding_digest: str, binding: dict[str, object]
    ) -> ConfigurationBinding: ...

    def configuration_binding(self, context: SyntheticContext) -> ConfigurationBinding | None: ...
