"""Read-only fact history returned by the synthetic facts port."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from quanthus.capabilities.accounting import AccountProjection


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
    order_id: str | None = None


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
class ReceivedOrderObservation:
    observation_id: str
    receipt_id: str
    order_id: str
    status: str
    observed_at: str
    final_cumulative_fill_qty: int | None
    cancelled_qty: int | None
    order_revision: int
    store_status: str


@dataclass(frozen=True)
class ReceivedFeeAssessment:
    fee_id: str
    receipt_id: str
    order_id: str
    amount: Decimal
    occurred_at: str
    store_status: str


@dataclass(frozen=True)
class OrderRecord:
    """Effective per-order ledger state the desktop shows."""

    order_id: str
    security_code: str
    side: str
    price_bound: Decimal
    accepted_total_qty: int
    known_fill_qty: int
    unreported_fill_qty: int
    hold_qty: int
    hold_amount: Decimal
    status: str
    needs_reconciliation: bool
    reason_codes: tuple[str, ...]
    fee_confirmed_total: Decimal
    settlement_ready: bool


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
    orders: tuple[OrderRecord, ...]
    observations: tuple[ReceivedOrderObservation, ...]
    fees: tuple[ReceivedFeeAssessment, ...]
    outbox_pending: int


@dataclass(frozen=True)
class AccountSnapshot:
    """Projection and history committed together; one read never mixes two revisions."""

    projection: AccountProjection
    history: FactHistory


@dataclass(frozen=True)
class ConfigurationBinding:
    """Composition and decision source an account store was first published with."""

    binding_digest: str
    binding: dict[str, Any]
    recorded_at: str
