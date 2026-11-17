"""Transport-free P12 synthetic facts DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quanthus.capabilities.accounting import AccountProjection
from quanthus.capabilities.execution import SyntheticContext
from quanthus.product_daily.contracts import ProductDailyError

POLICY_REFERENCE = "p12-synthetic-facts-0.1"


@dataclass(frozen=True)
class Baseline:
    context: SyntheticContext
    request_id: str
    cash: Decimal
    quantity: int
    security_code: str
    unsettled_quantity: int

    def __post_init__(self) -> None:
        if not self.request_id or not self.security_code:
            raise ProductDailyError("INVALID_REPORT_SHAPE", "baseline identity is required")
        if not self.cash.is_finite() or self.cash < 0:
            raise ProductDailyError("INVALID_REPORT_SHAPE", "baseline cash is invalid")
        for name in ("quantity", "unsettled_quantity"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ProductDailyError("INVALID_REPORT_SHAPE", f"{name} is invalid")


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    status: str
    receipt_digest: str | None = None
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubmissionResult:
    receipt_id: str
    status: str
    projection: AccountProjection
    deviations: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class CorrectionResult:
    receipt_id: str
    status: str
    projection: AccountProjection
    cash_delta: Decimal
    quantity_delta: int
    conflicts: tuple[str, ...] = ()
