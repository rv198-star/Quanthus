"""Public use cases; depends only on typed facts and a narrow port."""

from __future__ import annotations

from quanthus.capabilities.accounting import AccountProjection
from quanthus.capabilities.execution import (
    Correction,
    ExecutionReport,
    SyntheticContext,
    validate_context,
    validate_correction,
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

    def bind_configuration(
        self, context: SyntheticContext, binding_digest: str, binding: dict[str, object]
    ) -> ConfigurationBinding:
        return self._port.bind_configuration(self._authorize(context), binding_digest, binding)

    def configuration_binding(self, context: SyntheticContext) -> ConfigurationBinding | None:
        return self._port.configuration_binding(self._authorize(context))
