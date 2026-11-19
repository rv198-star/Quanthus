"""Transport-free read models and commands of the P12 desktop use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from quanthus.capabilities.decision_access import ChartEvidence, Explanation
from quanthus.capabilities.funding import FundingView
from quanthus.capabilities.risk import AccountRiskPolicy
from quanthus.product_daily.application.desktop.policy import AdoptionBinding
from quanthus.product_daily.application.history import (
    CurrentFact,
    FactHistory,
    LedgerEvent,
    PlanStatus,
    ReceivedCorrection,
    ReceivedReport,
)
from quanthus.shared.values import AccountScope, MaybeDecimal, VersionRef


@dataclass(frozen=True)
class PublishedPlan:
    """Immutable plan inside a published package (sized at the package account revision)."""

    plan_id: str
    opportunity_id: str
    security_code: str
    side: str
    current_qty: int
    target_qty: int
    delta_qty: int
    price_cap: Decimal
    cash_bound: Decimal | None
    estimated_fee: Decimal | None
    eligible_at: datetime
    expires_at: datetime
    window_ref: str
    invalidations: tuple[str, ...]
    reason_codes: tuple[str, ...]
    primary_constraint: str | None
    risk_check_ref: str


@dataclass(frozen=True)
class PublishedPackage:
    package_id: str
    schema_version: str
    mode: str
    synthetic: bool
    market_date: date
    knowledge_cutoff: datetime
    published_at: datetime
    account_revision: int
    composition_ref: VersionRef
    source_ref: VersionRef
    plans: tuple[PublishedPlan, ...]
    digest: str


@dataclass(frozen=True)
class PublicationStatus:
    status: str
    market_date: date
    published_at: datetime


@dataclass(frozen=True)
class PlanApplicability:
    plan_id: str
    status: str
    current_qty: int
    delta_qty: int
    account_revision: int
    reason_codes: tuple[str, ...]
    next_step: str


@dataclass(frozen=True)
class OpportunityView:
    opportunity_id: str
    security_code: str
    security_name: str
    stage: str
    queue_group: str
    action_code: str
    status_code: str
    reason_codes: tuple[str, ...]
    primary_constraint: str | None
    changes: tuple[str, ...]
    explanations: tuple[Explanation, ...]
    next_observation: str | None
    chart: ChartEvidence | None
    cost_price: MaybeDecimal
    plan: PublishedPlan | None
    applicability: PlanApplicability | None


@dataclass(frozen=True)
class PositionLine:
    security_code: str
    security_name: str | None
    quantity: int
    mark_price: MaybeDecimal
    market_value: MaybeDecimal
    average_cost: MaybeDecimal
    weight: MaybeDecimal
    opportunity_id: str | None


@dataclass(frozen=True)
class AccountSummary:
    scope: AccountScope
    account_revision: int
    event_sequence: int
    as_of: datetime
    valuation_ref: str | None
    economic_equity: MaybeDecimal
    cash_total: Decimal
    cash_available_for_new: MaybeDecimal
    cash_occupied: Decimal
    positions_value: MaybeDecimal
    reserve: MaybeDecimal
    top_concentration: MaybeDecimal
    top_concentration_security: str | None
    reconciliation_status: str
    pending_reconciliations: int
    ledger_digest: str
    positions: tuple[PositionLine, ...]


@dataclass(frozen=True)
class WorkbenchView:
    scope: AccountScope
    package: PublishedPackage | None
    publication: PublicationStatus
    clock: datetime
    clock_kind: str
    account: AccountSummary
    opportunities: tuple[OpportunityView, ...]
    action_blockers: tuple[str, ...]


@dataclass(frozen=True)
class ReportRecord:
    report: ReceivedReport
    plan_id: str | None
    deviations: tuple[str, ...]


@dataclass(frozen=True)
class FactRecord:
    fact: CurrentFact
    plan_id: str | None
    corrections: tuple[ReceivedCorrection, ...]


@dataclass(frozen=True)
class RecordsView:
    scope: AccountScope
    package: PublishedPackage | None
    publication: PublicationStatus
    account: AccountSummary
    history: FactHistory
    reports: tuple[ReportRecord, ...]
    facts: tuple[FactRecord, ...]
    events: tuple[LedgerEvent, ...]
    plans: tuple[PlanStatus, ...]


@dataclass(frozen=True)
class FundingRulesView:
    scope: AccountScope
    composition_ref: VersionRef
    execution_profile: str
    funding: FundingView
    risk: AccountRiskPolicy
    adoption: tuple[AdoptionBinding, ...]
    action_blockers: tuple[str, ...]
    account_revision: int


@dataclass(frozen=True)
class SubmitFill:
    """User-confirmed buy fill against a published plan; never an order instruction."""

    report_id: str
    plan_id: str
    quantity: int
    price: Decimal
    confirmed_fee: Decimal
    occurred_at: datetime


@dataclass(frozen=True)
class CorrectFill:
    correction_id: str
    fact_id: str
    expected_fact_revision: int
    quantity: int
    price: Decimal
    reason: str


@dataclass(frozen=True)
class FactOutcome:
    kind: str
    receipt_id: str
    status: str
    account: AccountSummary
    deviations: tuple[str, ...]
    conflicts: tuple[str, ...]
    affected_plans: tuple[str, ...]
    next_action: str
    cash_delta: Decimal | None
    quantity_delta: int | None
    current_fact_revision: int | None
