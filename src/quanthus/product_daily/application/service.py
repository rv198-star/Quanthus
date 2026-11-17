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
from quanthus.product_daily.application.models import (
    Baseline,
    CorrectionResult,
    SubmissionResult,
)
from quanthus.product_daily.application.ports import SyntheticFactsPort


class SyntheticFactService:
    def __init__(self, port: SyntheticFactsPort) -> None:
        self._port = port

    def establish_baseline(self, baseline: Baseline) -> AccountProjection:
        context = validate_context(baseline.context)
        if context.as_of != baseline.context.as_of:
            raise AssertionError("unreachable context drift")
        return self._port.establish_baseline(baseline)

    def submit_report(self, context: SyntheticContext, report: ExecutionReport) -> SubmissionResult:
        validated = validate_context(context)
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
        return self._port.correct_fact(validate_context(context), validate_correction(correction))

    def projection(self, context: SyntheticContext) -> AccountProjection:
        return self._port.projection(validate_context(context))

    def rebuild(self, context: SyntheticContext) -> AccountProjection:
        return self._port.rebuild(validate_context(context))
