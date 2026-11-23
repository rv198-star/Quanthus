"""Public use cases; depends only on typed facts and a narrow port."""

from __future__ import annotations

from decimal import Decimal

from quanthus.capabilities.accounting import AccountProjection
from quanthus.capabilities.execution import (
    Correction,
    ExecutionReport,
    FeeAssessment,
    OrderObservation,
    SyntheticContext,
    validate_context,
    validate_correction,
    validate_fee_assessment,
    validate_observation,
    validate_report,
)
from quanthus.product_daily.application.history import (
    AccountSnapshot,
    ConfigurationBinding,
    FactHistory,
)
from quanthus.product_daily.application.models import (
    Baseline,
    CorrectionResult,
    SubmissionResult,
    SyntheticRuntimeAuthorization,
)
from quanthus.product_daily.application.ports import SyntheticFactsPort
from quanthus.product_daily.funding import (
    CycleSettlementEvidence,
    FundingDecision,
    FundingState,
    SettlementRequest,
)
from quanthus.shared.fact_contracts import FactError


class SyntheticFactService:
    def __init__(
        self,
        port: SyntheticFactsPort,
        authorized_context: SyntheticContext,
        authorization: SyntheticRuntimeAuthorization,
    ) -> None:
        self._port = port
        authorization.require(authorized_context)
        self._authorization = authorization
        self._authorized_context = validate_context(authorized_context)

    def _authorize(self, context: SyntheticContext) -> SyntheticContext:
        validated = validate_context(context)
        self._authorization.require(validated)
        if validated != self._authorized_context:
            raise FactError("UNAUTHORIZED_CONTEXT", "account or trusted baseline context differs")
        return validated

    def establish_baseline(self, baseline: Baseline) -> AccountProjection:
        self._authorize(baseline.context)
        baseline.__post_init__()
        return self._port.establish_baseline(baseline)

    def submit_report(self, context: SyntheticContext, report: ExecutionReport) -> SubmissionResult:
        validated = self._authorize(context)
        validate_report(report)
        receipt = self._port.receive_report(validated, report)
        if receipt.status == "IDEMPOTENCY_CONFLICT":
            return SubmissionResult(
                receipt.receipt_id,
                receipt.status,
                self._port.projection(validated),
                conflicts=receipt.conflicts,
            )
        return self._port.post_report(validated, receipt, report)

    def correct_fact(self, context: SyntheticContext, correction: Correction) -> CorrectionResult:
        return self._port.correct_fact(self._authorize(context), validate_correction(correction))

    def submit_order_observation(
        self,
        context: SyntheticContext,
        observation: OrderObservation,
        *,
        accepted_total_qty: int,
        side: str,
        security_code: str,
        price_bound: Decimal,
        estimated_fee_upper: Decimal,
    ) -> SubmissionResult:
        validated = self._authorize(context)
        validate_observation(observation)
        receipt = self._port.receive_order_observation(validated, observation)
        if receipt.status == "IDEMPOTENCY_CONFLICT":
            return SubmissionResult(
                receipt.receipt_id,
                receipt.status,
                self._port.projection(validated),
                conflicts=receipt.conflicts,
            )
        return self._port.post_order_observation(
            validated, receipt, observation,
            accepted_total_qty, side, security_code, price_bound, estimated_fee_upper,
        )

    def submit_fee_assessment(
        self, context: SyntheticContext, fee: FeeAssessment
    ) -> SubmissionResult:
        validated = self._authorize(context)
        validate_fee_assessment(fee)
        receipt = self._port.receive_fee_assessment(validated, fee)
        if receipt.status == "IDEMPOTENCY_CONFLICT":
            return SubmissionResult(
                receipt.receipt_id,
                receipt.status,
                self._port.projection(validated),
                conflicts=receipt.conflicts,
            )
        return self._port.post_fee_assessment(validated, receipt, fee)

    def projection(self, context: SyntheticContext) -> AccountProjection:
        return self._port.projection(self._authorize(context))

    def rebuild(self, context: SyntheticContext) -> AccountProjection:
        return self._port.rebuild(self._authorize(context))

    def history(self, context: SyntheticContext) -> FactHistory:
        """Original reports, current facts, corrections and ledger events for one account."""
        return self._port.history(self._authorize(context))

    def snapshot(self, context: SyntheticContext) -> AccountSnapshot:
        """Projection and history from one committed state."""
        return self._port.snapshot(self._authorize(context))

    def funding_state(
        self, context: SyntheticContext
    ) -> tuple[FundingState, int] | None:
        """Persisted fixed-bucket partition, or None when not yet bound."""
        return self._port.funding_state(self._authorize(context))

    def funding_accountable_cash(
        self, context: SyntheticContext
    ) -> Decimal | None:
        """Partition cash plus proceeds awaiting attribution, or None unbound."""
        return self._port.funding_accountable_cash(self._authorize(context))

    def funding_pending_by_bucket(
        self, context: SyntheticContext
    ) -> dict[str, Decimal] | None:
        """Per-bucket proceeds already attributable but not yet settled."""
        return self._port.funding_pending_by_bucket(self._authorize(context))

    def funding_held_by_bucket(
        self, context: SyntheticContext
    ) -> dict[str, bool] | None:
        """Whether each bucket's attributed security is still held."""
        return self._port.funding_held_by_bucket(self._authorize(context))

    def bind_funding_state(
        self,
        context: SyntheticContext,
        state: FundingState,
        bucket_securities: dict[str, str] | None = None,
    ) -> FundingState:
        return self._port.bind_funding_state(
            self._authorize(context), state, bucket_securities=bucket_securities
        )

    def funding_binding(
        self, context: SyntheticContext
    ) -> dict | None:
        """Persisted bucket-security bindings for attribution decisions."""
        return self._port.funding_binding(self._authorize(context))

    def funding_realized_cash_by_security(
        self,
        context: SyntheticContext,
        security_codes: set[str] | None = None,
        since_sequence: int = 0,
    ) -> dict[str, Decimal] | None:
        """Per-security cash moved by admitted fills since a sequence floor."""
        return self._port.funding_realized_cash_by_security(
            self._authorize(context),
            security_codes=security_codes,
            since_sequence=since_sequence,
        )

    def mark_cycle_floor(
        self,
        context: SyntheticContext,
        *,
        bucket_id: str,
        event_sequence: int,
    ) -> None:
        """Lower an open cycle's accountable floor to an earlier sequence."""
        return self._port.mark_cycle_floor(
            self._authorize(context), bucket_id=bucket_id, event_sequence=event_sequence
        )

    def funding_unattributed_fills(
        self, context: SyntheticContext
    ) -> tuple[dict, ...] | None:
        """Fills whose cash no open bucket's attributed security explains."""
        return self._port.funding_unattributed_fills(self._authorize(context))

    def attribute_prebinding_cycle(
        self,
        context: SyntheticContext,
        *,
        bucket_id: str,
        cycle_id: str,
        security_code: str,
    ) -> FundingDecision:
        """Open a cycle on an empty bucket owning a pre-binding fill."""
        return self._port.attribute_prebinding_cycle(
            self._authorize(context),
            bucket_id=bucket_id,
            cycle_id=cycle_id,
            security_code=security_code,
        )

    def funding_settlement_evidence(
        self, context: SyntheticContext, security_codes: set[str] | None = None
    ) -> CycleSettlementEvidence:
        return self._port.funding_settlement_evidence(
            self._authorize(context), security_codes=security_codes
        )

    def settle_funding(
        self,
        context: SyntheticContext,
        *,
        settlement_id: str,
        requests: tuple[SettlementRequest, ...],
        close_bucket_ids: tuple[str, ...] | None,
    ) -> FundingDecision:
        return self._port.settle_funding(
            self._authorize(context),
            settlement_id=settlement_id,
            requests=requests,
            close_bucket_ids=close_bucket_ids,
        )

    def prepare_funding_cycle(
        self, context: SyntheticContext, *, bucket_id: str, cycle_id: str
    ) -> FundingDecision:
        return self._port.prepare_funding_cycle(
            self._authorize(context), bucket_id=bucket_id, cycle_id=cycle_id
        )

    def bind_configuration(
        self, context: SyntheticContext, binding_digest: str, binding: dict[str, object]
    ) -> ConfigurationBinding:
        return self._port.bind_configuration(self._authorize(context), binding_digest, binding)

    def configuration_binding(self, context: SyntheticContext) -> ConfigurationBinding | None:
        return self._port.configuration_binding(self._authorize(context))

    def migrate_configuration(
        self,
        context: SyntheticContext,
        binding_digest: str,
        binding: dict[str, object],
        migration_record: dict[str, object],
    ) -> ConfigurationBinding:
        migrate = getattr(self._port, "migrate_configuration", None)
        if not callable(migrate):
            raise FactError(
                "CONFIGURATION_BINDING_MISMATCH",
                "this fact store cannot migrate its bound decision source",
            )
        return migrate(self._authorize(context), binding_digest, binding, migration_record)
