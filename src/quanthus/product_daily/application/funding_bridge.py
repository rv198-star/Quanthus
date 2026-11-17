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
    validate_report,
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
    PendingBuy,
    QuantityLimits,
    RiskSnapshot,
    decide_entry,
)
from quanthus.shared.fact_contracts import FactError, digest

FUNDING_STATE_SCHEMA = "r11-funding-state-v1"


def _raw_object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", f"{field} must be an object")
    return value


def _raw_list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", f"{field} must be a list")
    return value


def _raw_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", f"{field} must be text")
    return value


def _raw_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", f"{field} must be an integer")
    return value


def _raw_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", f"{field} must be decimal text")
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", f"{field} is not decimal") from exc
    if not parsed.is_finite():
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", f"{field} is not finite")
    return parsed


def _raw_digest(value: object, field: str) -> str:
    text = _raw_str(value, field)
    if not text.startswith("sha256:") or len(text) != 71:
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", f"{field} must be sha256")
    return text


def _exact_fields(raw: dict[str, Any], fields: set[str], name: str) -> None:
    if set(raw) != fields:
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", f"{name} fields differ")


def _canonical_result(value: object, field: str) -> str:
    if not isinstance(value, dict):
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", f"{field} must be an object")
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def restore_funding_state(raw: dict[str, Any]) -> FundingState:
    """Restore the complete public FundingState JSON shape without partial defaults."""

    _exact_fields(
        raw,
        {
            "buckets",
            "reserve",
            "unallocated_adjustment",
            "cash_total",
            "revision",
            "processed_commands",
            "completed_settlements",
            "entry_reservations",
            "active_entry_reservation_ids",
        },
        "funding state",
    )
    try:
        bucket_rows = []
        for index, value in enumerate(_raw_list(raw["buckets"], "buckets")):
            item = _raw_object(value, f"buckets[{index}]")
            _exact_fields(
                item,
                {
                    "bucket_id",
                    "cash",
                    "target_notional",
                    "locked_cycle_budget",
                    "cycle_id",
                    "phase",
                },
                f"buckets[{index}]",
            )
            cycle = item["cycle_id"]
            if cycle is not None and not isinstance(cycle, str):
                raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", "cycle_id type differs")
            bucket_rows.append(
                BucketState(
                    _raw_str(item["bucket_id"], "bucket_id"),
                    _raw_decimal(item["cash"], "bucket.cash"),
                    _raw_decimal(item["target_notional"], "bucket.target_notional"),
                    _raw_decimal(item["locked_cycle_budget"], "bucket.locked_cycle_budget"),
                    cycle,
                    BucketPhase(_raw_str(item["phase"], "bucket.phase")),
                )
            )
        buckets = tuple(bucket_rows)
        command_rows = []
        for index, value in enumerate(_raw_list(raw["processed_commands"], "processed_commands")):
            item = _raw_object(value, f"processed_commands[{index}]")
            _exact_fields(
                item,
                {
                    "command_id",
                    "body_digest",
                    "operation",
                    "invocation_digest",
                    "result_digest",
                    "result",
                    "revision",
                },
                f"processed_commands[{index}]",
            )
            result_json = _canonical_result(item["result"], "processed result")
            command = ProcessedCommand(
                _raw_str(item["command_id"], "command_id"),
                _raw_digest(item["body_digest"], "body_digest"),
                _raw_str(item["operation"], "operation"),
                _raw_digest(item["invocation_digest"], "invocation_digest"),
                _raw_digest(item["result_digest"], "result_digest"),
                result_json,
                _raw_int(item["revision"], "command revision"),
            )
            if command.result_digest != digest(command.result):
                raise FactError("FUNDING_STATE_IDENTITY_MISMATCH", "command result digest differs")
            command_rows.append(command)
        commands = tuple(command_rows)
        settlement_rows = []
        for index, value in enumerate(
            _raw_list(raw["completed_settlements"], "completed_settlements")
        ):
            item = _raw_object(value, f"completed_settlements[{index}]")
            _exact_fields(
                item,
                {"settlement_id", "bucket_id", "cycle_id", "cash_after"},
                f"completed_settlements[{index}]",
            )
            settlement_rows.append(
                CompletedSettlement(
                    _raw_str(item["settlement_id"], "settlement_id"),
                    _raw_str(item["bucket_id"], "settlement bucket_id"),
                    _raw_str(item["cycle_id"], "settlement cycle_id"),
                    _raw_decimal(item["cash_after"], "cash_after"),
                )
            )
        settlements = tuple(settlement_rows)
        reservation_rows = []
        for index, value in enumerate(_raw_list(raw["entry_reservations"], "entry_reservations")):
            item = _raw_object(value, f"entry_reservations[{index}]")
            _exact_fields(
                item,
                {
                    "reservation_id",
                    "opportunity_id",
                    "security_code",
                    "level1_industry_id",
                    "bucket_id",
                    "cycle_id",
                    "quantity",
                    "command_id",
                    "invocation_digest",
                    "result_digest",
                    "result",
                },
                f"entry_reservations[{index}]",
            )
            result_json = _canonical_result(item["result"], "reservation result")
            reservation = EntryReservation(
                _raw_str(item["reservation_id"], "reservation_id"),
                _raw_str(item["opportunity_id"], "opportunity_id"),
                _raw_str(item["security_code"], "security_code"),
                _raw_str(item["level1_industry_id"], "level1_industry_id"),
                _raw_str(item["bucket_id"], "reservation bucket_id"),
                _raw_str(item["cycle_id"], "reservation cycle_id"),
                _raw_int(item["quantity"], "reservation quantity"),
                _raw_str(item["command_id"], "reservation command_id"),
                _raw_digest(item["invocation_digest"], "reservation invocation_digest"),
                _raw_digest(item["result_digest"], "reservation result_digest"),
                result_json,
            )
            if reservation.result_digest != digest(reservation.result):
                raise FactError(
                    "FUNDING_STATE_IDENTITY_MISMATCH", "reservation result digest differs"
                )
            reservation_rows.append(reservation)
        reservations = tuple(reservation_rows)
        state = FundingState(
            buckets,
            _raw_decimal(raw["reserve"], "reserve"),
            _raw_decimal(raw["unallocated_adjustment"], "unallocated_adjustment"),
            _raw_int(raw["revision"], "revision"),
            commands,
            settlements,
            reservations,
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, FactError):
            raise
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", str(exc)) from exc
    if _raw_decimal(raw["cash_total"], "cash_total") != state.cash_total:
        raise FactError("FUNDING_STATE_IDENTITY_MISMATCH", "cash_total differs")
    raw_active = _raw_list(raw["active_entry_reservation_ids"], "active_entry_reservation_ids")
    if any(not isinstance(item, str) for item in raw_active):
        raise FactError("FUNDING_STATE_SCHEMA_MISMATCH", "active id type differs")
    if tuple(raw_active) != tuple(
        sorted(item.reservation_id for item in state.active_entry_reservations)
    ):
        raise FactError("FUNDING_STATE_IDENTITY_MISMATCH", "active reservations differ")
    if state.to_dict() != raw:
        raise FactError("FUNDING_STATE_IDENTITY_MISMATCH", "canonical state differs")
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
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise FactError("INVALID_REPORT_SHAPE", "baseline request_id is required")
        if not isinstance(self.security_code, str) or not self.security_code.strip():
            raise FactError("INVALID_REPORT_SHAPE", "baseline security_code is required")
        if not isinstance(self.account_cash, Decimal) or not self.account_cash.is_finite():
            raise FactError("INVALID_REPORT_SHAPE", "baseline cash must be Decimal")
        if self.account_cash != self.funding.cash_total:
            raise FactError("BASELINE_IDENTITY_MISMATCH", "account and funding cash differ")

    @property
    def payload_digest(self) -> str:
        c = self.context
        return digest(
            {
                "request_id": self.request_id,
                "account_id": c.account_id,
                "mode": c.mode,
                "source_id": c.source_id,
                "as_of": c.as_of.isoformat(),
                "coverage_id": c.coverage_id,
                "security_code": self.security_code,
                "account_cash": format(self.account_cash, "f"),
                "funding": funding_state_envelope(self.funding),
            }
        )


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
        if (
            not isinstance(self.profile_version, str)
            or not self.profile_version.strip()
            or not isinstance(self.source_id, str)
            or not self.source_id.strip()
        ):
            raise FactError("INVALID_REPORT_SHAPE", "fill profile identity is required")
        values = (
            self.order_total_quantity,
            self.final_cumulative_fill_quantity,
            self.cancelled_quantity,
        )
        if any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in values):
            raise FactError("INVALID_REPORT_SHAPE", "fill quantities must be integers")
        if not isinstance(self.confirmed_fee, Decimal):
            raise FactError("INVALID_REPORT_SHAPE", "confirmed fee is invalid")
        exponent = self.confirmed_fee.as_tuple().exponent
        if (
            not self.confirmed_fee.is_finite()
            or self.confirmed_fee < 0
            or (isinstance(exponent, int) and exponent < -2)
        ):
            raise FactError("INVALID_REPORT_SHAPE", "confirmed fee is invalid")
        if not isinstance(self.terminal, bool):
            raise FactError("INVALID_REPORT_SHAPE", "terminal must be boolean")
        if self.order_total_quantity != (
            self.final_cumulative_fill_quantity + self.cancelled_quantity
        ):
            raise FactError("FINAL_FILL_GAP", "terminal fill quantities do not reconcile")
        if not self.terminal:
            raise FactError("UNSUPPORTED_TERMINAL_STATE", "only terminal facts are admitted")

    @property
    def payload_digest(self) -> str:
        return digest(
            {
                "profile_version": self.profile_version,
                "source_id": self.source_id,
                "order_total_quantity": self.order_total_quantity,
                "final_cumulative_fill_quantity": self.final_cumulative_fill_quantity,
                "cancelled_quantity": self.cancelled_quantity,
                "confirmed_fee": format(self.confirmed_fee, "f"),
                "terminal": self.terminal,
            }
        )


@dataclass(frozen=True)
class PendingObligation:
    obligation_id: str
    reservation_id: str
    security_code: str
    level1_industry_id: str
    bucket_id: str
    quantity: int
    price_bound: Decimal
    fee_upper_bound: Decimal
    source_id: str
    status: str = "OPEN"

    def pending_buy(self) -> PendingBuy:
        if self.status != "OPEN":
            raise FactError("ORDER_ALREADY_FILLED", "obligation is terminal")
        return PendingBuy(
            self.reservation_id,
            self.security_code,
            self.level1_industry_id,
            self.quantity,
            self.price_bound,
            self.price_bound,
            self.fee_upper_bound,
            False,
        )


@dataclass(frozen=True)
class BuyImpact:
    account: AccountProjection
    funding: FundingState
    postings: tuple[Posting, ...]
    cash_delta: Decimal
    receipt: ProcessedCommand


@dataclass(frozen=True)
class BridgeState:
    account: AccountProjection
    funding: FundingState
    account_revision: int


@dataclass(frozen=True)
class BridgeSnapshot:
    state: BridgeState
    obligations: tuple[PendingObligation, ...]

    @property
    def revision(self) -> int:
        return self.state.account_revision

    @property
    def digest(self) -> str:
        return digest(
            {
                "account": self.state.account.digest,
                "funding": self.state.funding.digest,
                "revision": self.revision,
                "obligations": [
                    {
                        "obligation_id": item.obligation_id,
                        "reservation_id": item.reservation_id,
                        "security_code": item.security_code,
                        "level1_industry_id": item.level1_industry_id,
                        "bucket_id": item.bucket_id,
                        "quantity": item.quantity,
                        "price_bound": format(item.price_bound, "f"),
                        "fee_upper_bound": format(item.fee_upper_bound, "f"),
                        "source_id": item.source_id,
                        "status": item.status,
                    }
                    for item in self.obligations
                ],
            }
        )


@dataclass(frozen=True)
class EntryBridgeResult:
    accepted: bool
    replayed: bool
    quantity: int
    reservation_id: str | None
    funding: FundingState
    account_revision: int
    result_digest: str
    command_id: str
    obligation: PendingObligation | None = None


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
    def read_snapshot(self, context: SyntheticContext) -> BridgeSnapshot: ...
    def obligations(self, context: SyntheticContext) -> tuple[PendingObligation, ...]: ...
    def commit_entry(
        self, context: SyntheticContext, expected: str, result: EntryBridgeResult
    ) -> BridgeState: ...
    def receive_buy(
        self, context: SyntheticContext, report: ExecutionReport, profile: CycleFillProfile
    ) -> str: ...
    def commit_buy(
        self,
        context: SyntheticContext,
        expected_account_digest: str,
        expected_funding_digest: str,
        receipt_id: str,
        report: ExecutionReport,
        profile: CycleFillProfile,
        obligation: PendingObligation,
        account: AccountProjection,
        funding: FundingState,
        receipt: ProcessedCommand,
        postings: tuple[Posting, ...],
    ) -> BuyBridgeResult: ...
    def replay_buy(
        self,
        context: SyntheticContext,
        report: ExecutionReport,
        profile: CycleFillProfile,
    ) -> BuyBridgeResult | None: ...
    def rebuild(self, context: SyntheticContext) -> BridgeState: ...


def apply_confirmed_buy_transition(
    state: BridgeState,
    report: ExecutionReport,
    profile: CycleFillProfile,
    obligation: PendingObligation,
) -> BuyImpact:
    """The one public buy calculation used by live posting and cold rebuild."""

    if (
        obligation.status != "OPEN"
        or obligation.security_code != report.security_code
        or obligation.quantity != report.quantity
        or report.quantity != profile.final_cumulative_fill_quantity
        or report.confirmed_fee != profile.confirmed_fee
        or report.source_id != profile.source_id
        or profile.cancelled_quantity != 0
    ):
        raise FactError("FINAL_FILL_GAP", "report, profile and obligation differ")
    account, postings = apply_buy_delta(
        state.account,
        security_code=report.security_code,
        quantity=report.quantity,
        price=report.price,
        fee=report.confirmed_fee,
    )
    cash_delta = account.cash - state.account.cash
    matched = state.funding.bucket(obligation.bucket_id)
    updated_bucket = BucketState(
        matched.bucket_id,
        matched.cash + cash_delta,
        matched.target_notional,
        matched.locked_cycle_budget,
        matched.cycle_id,
        matched.phase,
    )
    buckets = tuple(
        updated_bucket if item.bucket_id == updated_bucket.bucket_id else item
        for item in state.funding.buckets
    )
    result_body = {
        "accepted": True,
        "account_cash": format(account.cash, "f"),
        "account_digest": account.digest,
        "funding_cash": format(
            sum((item.cash for item in buckets), Decimal("0"))
            + state.funding.reserve
            + state.funding.unallocated_adjustment,
            "f",
        ),
        "profile_digest": profile.payload_digest,
        "source_fact_id": report.source_fact_id,
    }
    command = CommandEnvelope.create(
        f"post:{report.report_id}:{profile.payload_digest}",
        result_body,
        state.funding.revision,
    )
    receipt = make_receipt(
        command=command,
        operation="post_execution_report",
        invocation_payload=result_body,
        result=result_body,
        revision=state.funding.revision + 1,
    )
    funding = FundingState(
        buckets,
        state.funding.reserve,
        state.funding.unallocated_adjustment,
        state.funding.revision + 1,
        (*state.funding.processed_commands, receipt),
        state.funding.completed_settlements,
        state.funding.entry_reservations,
    )
    if funding.cash_total != account.cash:
        raise FactError("BASELINE_IDENTITY_MISMATCH", "buy closure cash differs")
    return BuyImpact(account, funding, postings, cash_delta, receipt)


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
        view = self._port.read_snapshot(context)
        state = view.state
        known_command = any(
            item.command_id == intent.command.command_id
            for item in state.funding.processed_commands
        )
        if not known_command and state.account_revision != intent.expected_account_revision:
            raise ProductDailyError("ACCOUNT_REVISION_CONFLICT", "account revision is stale")
        if not known_command:
            pending = tuple(
                item.pending_buy() for item in view.obligations if item.status == "OPEN"
            )
            if (
                intent.snapshot.cash_available != state.account.cash
                or intent.snapshot.pending_buys != pending
                or tuple((item.security_code, item.quantity) for item in intent.snapshot.positions)
                != tuple((item.security_code, item.quantity) for item in state.account.positions)
                or any(
                    item.mark_price != intent.mark_price or item.price_bound != intent.price_bound
                    for item in intent.snapshot.positions
                )
                or intent.snapshot.equity
                != state.account.cash
                + sum(
                    (
                        Decimal(item.quantity) * intent.mark_price
                        for item in state.account.positions
                    ),
                    Decimal("0"),
                )
            ):
                raise FactError(
                    "RISK_SNAPSHOT_STALE", "risk snapshot differs from current account closure"
                )
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
        obligation = None
        if (
            decision.accepted
            and decision.reservation_id is not None
            and decision.quantity is not None
        ):
            obligation = PendingObligation(
                decision.reservation_id,
                decision.reservation_id,
                intent.candidate.security_code,
                intent.candidate.level1_industry_id,
                decision.selected_bucket_id or "",
                decision.quantity.quantity,
                intent.price_bound,
                decision.quantity.fees or Decimal("0"),
                context.source_id,
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
            intent.command.command_id,
            obligation,
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
        validate_report(report)
        profile.__post_init__()
        existing = self._port.replay_buy(context, report, profile)
        if existing is not None:
            return existing
        receipt_id = self._port.receive_buy(context, report, profile)
        view = self._port.read_snapshot(context)
        state = view.state
        obligations = tuple(
            item
            for item in view.obligations
            if item.status == "OPEN" and item.security_code == report.security_code
        )
        if len(obligations) != 1:
            raise FactError("ORDER_ALREADY_FILLED", "no open application obligation")
        impact = apply_confirmed_buy_transition(state, report, profile, obligations[0])
        return self._port.commit_buy(
            context,
            state.account.digest,
            state.funding.digest,
            receipt_id,
            report,
            profile,
            obligations[0],
            impact.account,
            impact.funding,
            impact.receipt,
            impact.postings,
        )

    def risk_snapshot(self, context: SyntheticContext, *, mark_price: Decimal) -> RiskSnapshot:
        view = self._port.read_snapshot(self._context_for(context))
        state = view.state
        pending = tuple(item.pending_buy() for item in view.obligations if item.status == "OPEN")
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
            f"bridge-risk:{view.digest}",
            self._context.as_of,
            state.account.cash + marked,
            state.account.cash,
            positions,
            pending_buys=pending,
        )

    def rebuild(self, context: SyntheticContext) -> BridgeState:
        return self._port.rebuild(self._context_for(context))
