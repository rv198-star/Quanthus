"""Typed bridge between the accepted funding core and durable account facts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from quanthus.capabilities.accounting import AccountProjection, Posting, apply_buy_delta
from quanthus.capabilities.execution import (
    ExecutionReport as ExecutionReport,
)
from quanthus.capabilities.execution import (
    SyntheticContext as SyntheticContext,
)
from quanthus.capabilities.execution import (
    validate_context,
)
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization
from quanthus.product_daily.contracts import (
    CommandEnvelope,
    ProcessedCommand,
    ProductDailyError,
    make_receipt,
)
from quanthus.product_daily.funding import (
    BucketPhase,
    BucketState,
    CompletedSettlement,
    EntryReservation,
    FundingState,
)
from quanthus.product_daily.risk import (
    BuyFeeRule,
    CandidateEntry,
    EntryDecision,
    EntryLimits,
    MarkedExposure,
    QuantityLimits,
    RiskSnapshot,
    decide_entry,
)
from quanthus.shared.fact_contracts import FactError, digest

FUNDING_STATE_SCHEMA = "r11-funding-state-v1"


def _canonical_result(value: object, field: str) -> str:
    if not isinstance(value, dict):
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", f"{field} must be an object")
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def restore_funding_state(raw: dict[str, Any]) -> FundingState:
    """Restore the complete public FundingState JSON shape without partial defaults."""

    expected = {
        "buckets",
        "reserve",
        "unallocated_adjustment",
        "cash_total",
        "revision",
        "processed_commands",
        "completed_settlements",
        "entry_reservations",
        "active_entry_reservation_ids",
    }
    if set(raw) != expected:
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", "funding state fields differ")
    try:
        buckets = tuple(
            BucketState(
                str(item["bucket_id"]),
                Decimal(str(item["cash"])),
                Decimal(str(item["target_notional"])),
                Decimal(str(item["locked_cycle_budget"])),
                None if item["cycle_id"] is None else str(item["cycle_id"]),
                BucketPhase(str(item["phase"])),
            )
            for item in raw["buckets"]
        )
        commands = tuple(
            ProcessedCommand(
                str(item["command_id"]),
                str(item["body_digest"]),
                str(item["operation"]),
                str(item["invocation_digest"]),
                str(item["result_digest"]),
                _canonical_result(item["result"], "processed result"),
                int(item["revision"]),
            )
            for item in raw["processed_commands"]
        )
        settlements = tuple(
            CompletedSettlement(
                str(item["settlement_id"]),
                str(item["bucket_id"]),
                str(item["cycle_id"]),
                Decimal(str(item["cash_after"])),
            )
            for item in raw["completed_settlements"]
        )
        reservations = tuple(
            EntryReservation(
                str(item["reservation_id"]),
                str(item["opportunity_id"]),
                str(item["security_code"]),
                str(item["level1_industry_id"]),
                str(item["bucket_id"]),
                str(item["cycle_id"]),
                int(item["quantity"]),
                str(item["command_id"]),
                str(item["invocation_digest"]),
                str(item["result_digest"]),
                _canonical_result(item["result"], "reservation result"),
            )
            for item in raw["entry_reservations"]
        )
        state = FundingState(
            buckets,
            Decimal(str(raw["reserve"])),
            Decimal(str(raw["unallocated_adjustment"])),
            int(raw["revision"]),
            commands,
            settlements,
            reservations,
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, FactError):
            raise
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", str(exc)) from exc
    if str(raw["cash_total"]) != format(state.cash_total, "f"):
        raise FactError("FUNDING_STATE_IDENTITY_MISMATCH", "cash_total differs")
    if tuple(raw["active_entry_reservation_ids"]) != tuple(
        sorted(item.reservation_id for item in state.active_entry_reservations)
    ):
        raise FactError("FUNDING_STATE_IDENTITY_MISMATCH", "active reservations differ")
    return state


def funding_state_envelope(state: FundingState) -> dict[str, object]:
    body = state.to_dict()
    return {"schema_version": FUNDING_STATE_SCHEMA, "state": body, "digest": digest(body)}


def restore_funding_envelope(raw: dict[str, Any]) -> FundingState:
    if set(raw) != {"schema_version", "state", "digest"}:
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", "envelope fields differ")
    if raw["schema_version"] != FUNDING_STATE_SCHEMA:
        raise FactError("UNSUPPORTED_FUNDING_STATE_SCHEMA", "unsupported schema")
    state_raw = raw["state"]
    if not isinstance(state_raw, dict) or raw["digest"] != digest(state_raw):
        raise FactError("FUNDING_STATE_IDENTITY_MISMATCH", "envelope digest differs")
    return restore_funding_state(state_raw)


@dataclass(frozen=True)
class CycleBaseline:
    context: SyntheticContext
    request_id: str
    account_cash: Decimal
    funding: FundingState
    security_code: str

    def __post_init__(self) -> None:
        validate_context(self.context)
        if self.account_cash != self.funding.cash_total:
            raise FactError("BASELINE_IDENTITY_MISMATCH", "account and funding cash differ")


@dataclass(frozen=True)
class CycleEntryIntent:
    expected_account_revision: int
    command: CommandEnvelope
    candidate: CandidateEntry
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
class CycleFillProfile:
    profile_version: str
    source_id: str
    order_total_quantity: int
    final_cumulative_fill_quantity: int
    cancelled_quantity: int
    confirmed_fee: Decimal
    terminal: bool = True

    def __post_init__(self) -> None:
        if not self.profile_version or not self.source_id:
            raise FactError("INVALID_REPORT_SHAPE", "fill profile identity is required")
        values = (
            self.order_total_quantity,
            self.final_cumulative_fill_quantity,
            self.cancelled_quantity,
        )
        if any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in values):
            raise FactError("INVALID_REPORT_SHAPE", "fill quantities must be integers")
        if self.order_total_quantity != (
            self.final_cumulative_fill_quantity + self.cancelled_quantity
        ):
            raise FactError("FINAL_FILL_GAP", "terminal fill quantities do not reconcile")
        if not self.terminal:
            raise FactError("UNSUPPORTED_TERMINAL_STATE", "only terminal facts are admitted")


@dataclass(frozen=True)
class BridgeState:
    account: AccountProjection
    funding: FundingState
    account_revision: int


@dataclass(frozen=True)
class EntryBridgeResult:
    accepted: bool
    replayed: bool
    quantity: int
    reservation_id: str | None
    funding: FundingState
    account_revision: int
    result_digest: str


@dataclass(frozen=True)
class BuyBridgeResult:
    receipt_id: str
    status: str
    account: AccountProjection
    funding: FundingState
    account_revision: int
    postings: tuple[Posting, ...]


class FundingBridgePort(Protocol):
    def establish(self, baseline: CycleBaseline) -> BridgeState: ...
    def read(self, context: SyntheticContext) -> BridgeState: ...
    def commit_entry(
        self, context: SyntheticContext, expected: str, result: EntryBridgeResult
    ) -> BridgeState: ...
    def receive_buy(self, context: SyntheticContext, report: ExecutionReport) -> str: ...
    def commit_buy(
        self,
        context: SyntheticContext,
        expected_account_digest: str,
        expected_funding_digest: str,
        receipt_id: str,
        report: ExecutionReport,
        profile: CycleFillProfile,
        account: AccountProjection,
        funding: FundingState,
        receipt: ProcessedCommand,
        postings: tuple[Posting, ...],
    ) -> BuyBridgeResult: ...
    def replay_buy(
        self, context: SyntheticContext, report: ExecutionReport
    ) -> BuyBridgeResult | None: ...
    def rebuild(self, context: SyntheticContext) -> BridgeState: ...


class FundingBridgeService:
    def __init__(
        self,
        port: FundingBridgePort,
        authorized_context: SyntheticContext,
        authorization: SyntheticRuntimeAuthorization,
    ) -> None:
        authorization.require(authorized_context)
        self._port = port
        self._authorization = authorization
        self._context = validate_context(authorized_context)

    def _context_for(self, context: SyntheticContext) -> SyntheticContext:
        value = validate_context(context)
        self._authorization.require(value)
        if value != self._context:
            raise FactError("UNAUTHORIZED_CONTEXT", "bridge context differs")
        return value

    def establish_baseline(self, baseline: CycleBaseline) -> BridgeState:
        self._context_for(baseline.context)
        return self._port.establish(baseline)

    def current(self, context: SyntheticContext) -> BridgeState:
        return self._port.read(self._context_for(context))

    def enter(self, context: SyntheticContext, intent: CycleEntryIntent) -> EntryBridgeResult:
        context = self._context_for(context)
        state = self._port.read(context)
        known_command = any(
            item.command_id == intent.command.command_id
            for item in state.funding.processed_commands
        )
        if not known_command and state.account_revision != intent.expected_account_revision:
            raise ProductDailyError("ACCOUNT_REVISION_CONFLICT", "account revision is stale")
        decision: EntryDecision = decide_entry(
            candidate=intent.candidate,
            funding=state.funding,
            snapshot=intent.snapshot,
            empty_bucket_ids=intent.empty_bucket_ids,
            active_or_reserved_security_codes=intent.active_or_reserved_security_codes,
            active_industry_counts=intent.active_industry_counts,
            new_buckets_today=intent.new_buckets_today,
            limits=intent.limits,
            fraction=intent.fraction,
            mark_price=intent.mark_price,
            price_bound=intent.price_bound,
            lot_size=intent.lot_size,
            quantity_limits=intent.quantity_limits,
            fee_rule=intent.fee_rule,
            command=intent.command,
        )
        result = EntryBridgeResult(
            decision.accepted,
            decision.replayed,
            decision.quantity.quantity if decision.quantity is not None else 0,
            decision.reservation_id,
            decision.funding_state,
            state.account_revision
            + (1 if decision.funding_state_changed and not decision.replayed else 0),
            decision.digest,
        )
        expected = digest(
            {
                "account": state.account.digest,
                "funding": state.funding.digest,
                "revision": state.account_revision,
            }
        )
        self._port.commit_entry(context, expected, result)
        return result

    def submit_buy(
        self, context: SyntheticContext, report: ExecutionReport, profile: CycleFillProfile
    ) -> BuyBridgeResult:
        context = self._context_for(context)
        existing = self._port.replay_buy(context, report)
        if existing is not None:
            return existing
        receipt_id = self._port.receive_buy(context, report)
        state = self._port.read(context)
        if (
            report.quantity != profile.final_cumulative_fill_quantity
            or report.confirmed_fee != profile.confirmed_fee
            or profile.cancelled_quantity != 0
        ):
            raise FactError("FINAL_FILL_GAP", "report and terminal profile differ")
        active = tuple(
            item
            for item in state.funding.active_entry_reservations
            if item.security_code == report.security_code
        )
        if len(active) != 1 or active[0].quantity != report.quantity:
            raise FactError("ENTRY_RESERVATION_MISMATCH", "entry reservation differs from fill")
        account, postings = apply_buy_delta(
            state.account,
            security_code=report.security_code,
            quantity=report.quantity,
            price=report.price,
            fee=report.confirmed_fee,
        )
        bucket = state.funding.bucket(active[0].bucket_id)
        updated_bucket = BucketState(
            bucket.bucket_id,
            bucket.cash - report.price * report.quantity - report.confirmed_fee,
            bucket.target_notional,
            bucket.locked_cycle_budget,
            bucket.cycle_id,
            bucket.phase,
        )
        result_body = {
            "accepted": True,
            "account_cash": format(account.cash, "f"),
            "funding_cash": format(updated_bucket.cash + state.funding.reserve, "f"),
        }
        buy_command = CommandEnvelope.create(
            f"post:{report.report_id}", result_body, state.funding.revision
        )
        receipt = make_receipt(
            command=buy_command,
            operation="post_execution_report",
            invocation_payload=result_body,
            result=result_body,
            revision=state.funding.revision + 1,
        )
        updated_funding = FundingState(
            (updated_bucket,),
            state.funding.reserve,
            state.funding.unallocated_adjustment,
            state.funding.revision + 1,
            (*state.funding.processed_commands, receipt),
            state.funding.completed_settlements,
            state.funding.entry_reservations,
        )
        return self._port.commit_buy(
            context,
            state.account.digest,
            state.funding.digest,
            receipt_id,
            report,
            profile,
            account,
            updated_funding,
            receipt,
            postings,
        )

    def risk_snapshot(self, context: SyntheticContext, *, mark_price: Decimal) -> RiskSnapshot:
        state = self._port.read(self._context_for(context))
        positions = tuple(
            MarkedExposure(
                unit_id=f"position:{item.security_code}",
                opportunity_id="confirmed-fact",
                security_code=item.security_code,
                level1_industry_id="IND1",
                quantity=item.quantity,
                mark_price=mark_price,
                price_bound=mark_price,
            )
            for item in state.account.positions
        )
        marked = sum((Decimal(item.quantity) * mark_price for item in positions), Decimal("0"))
        return RiskSnapshot(
            "bridge-risk",
            self._context.as_of,
            state.account.cash + marked,
            state.account.cash,
            positions,
        )

    def rebuild(self, context: SyntheticContext) -> BridgeState:
        return self._port.rebuild(self._context_for(context))
