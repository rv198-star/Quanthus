"""Read-only fact history returned by the synthetic facts port."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ReceivedReport:
    report_id: str
    receipt_id: str
    status: str
    received_at: str
    security_code: str
    side: str
    quantity: int
    price: Decimal
    confirmed_fee: Decimal
    occurred_at: str
    intended_quantity: int
    intended_price_cap: Decimal
    suggestion_id: str | None
    deviations: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class CurrentFact:
    fact_id: str
    security_code: str
    quantity: int
    price: Decimal
    confirmed_fee: Decimal
    fact_revision: int


@dataclass(frozen=True)
class ReceivedCorrection:
    correction_id: str
    receipt_id: str
    fact_id: str
    expected_fact_revision: int
    quantity: int
    price: Decimal
    reason: str
    status: str
    error_code: str | None
    received_at: str


@dataclass(frozen=True)
class LedgerEvent:
    event_id: str
    event_sequence: int
    revision: int
    kind: str
    fact_id: str
    cash_delta: Decimal
    position_delta: Decimal
    quantity_delta: int
    recorded_at: str


@dataclass(frozen=True)
class PlanStatus:
    plan_id: str
    status: str
    based_on_revision: int


@dataclass(frozen=True)
class FactHistory:
    baseline_request_id: str
    baseline_cash: Decimal
    baseline_quantity: int
    baseline_security_code: str
    baseline_as_of: str
    baseline_source_id: str
    baseline_coverage_id: str
    reports: tuple[ReceivedReport, ...]
    facts: tuple[CurrentFact, ...]
    corrections: tuple[ReceivedCorrection, ...]
    events: tuple[LedgerEvent, ...]
    plans: tuple[PlanStatus, ...]
    outbox_pending: int
