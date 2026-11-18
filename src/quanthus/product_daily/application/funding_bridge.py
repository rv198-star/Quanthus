"""Typed bridge between the accepted funding core and durable account facts."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from quanthus.capabilities.accounting import (
    AccountProjection,
    Posting,
    apply_buy_delta,
    apply_sell_delta,
)
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
from quanthus.product_daily.c13_later_signal import (
    LaterSignalDecision,
    LaterSignalPolicy,
    LaterSignalStatus,
    OriginalOpportunity,
    SignalObservation,
    SyntheticCalendar,
    evaluate_later_signal,
)
from quanthus.product_daily.candidate import CandidateProjection
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
    CycleSettlementEvidence,
    EntryReservation,
    FundingDecision,
    FundingState,
    SettlementRequest,
    apply_late_cash_delta,
    late_cash_invocation_payload,
    settle_batch,
    settle_invocation_payload,
)
from quanthus.product_daily.risk import (
    BuyFeeRule,
    CandidateEntry,
    EntryLimits,
    ExpertEntryAction,
    ExpertEntryEligibility,
    MarkedExposure,
    PendingBuy,
    PendingSell,
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


def entry_intent_to_dict(intent: CycleEntryIntent) -> dict[str, Any]:
    return {
        "expected_account_revision": intent.expected_account_revision,
        "command": {
            "command_id": intent.command.command_id,
            "body_digest": intent.command.body_digest,
            "expected_revision": intent.command.expected_revision,
        },
        "candidate": intent.candidate.to_dict(),
        "snapshot": intent.snapshot.to_dict(),
        "empty_bucket_ids": list(intent.empty_bucket_ids),
        "active_or_reserved_security_codes": list(intent.active_or_reserved_security_codes),
        "active_industry_counts": [list(item) for item in intent.active_industry_counts],
        "new_buckets_today": intent.new_buckets_today,
        "limits": {
            "max_active_or_reserved_per_industry": (
                intent.limits.max_active_or_reserved_per_industry
            ),
            "max_new_buckets_per_day": intent.limits.max_new_buckets_per_day,
        },
        "fraction": format(intent.fraction, "f"),
        "mark_price": format(intent.mark_price, "f"),
        "price_bound": format(intent.price_bound, "f"),
        "lot_size": intent.lot_size,
        "quantity_limits": {
            "stock": format(intent.quantity_limits.stock or Decimal("0"), "f"),
            "industry": format(intent.quantity_limits.industry or Decimal("0"), "f"),
            "total": format(intent.quantity_limits.total or Decimal("0"), "f"),
        },
        "fee_rule": {
            "commission_rate": format(intent.fee_rule.commission_rate or Decimal("0"), "f"),
            "minimum_commission": format(intent.fee_rule.minimum_commission or Decimal("0"), "f"),
            "cash_quantum": format(intent.fee_rule.cash_quantum, "f"),
        },
    }


def restore_entry_intent(raw: dict[str, Any]) -> CycleEntryIntent:
    candidate_raw = raw["candidate"]
    expert_raw = candidate_raw.get("expert_entry")
    expert = None
    if expert_raw is not None:
        expert = ExpertEntryEligibility(
            expert_raw["event_id"],
            expert_raw["security_code"],
            ExpertEntryAction(expert_raw["final_action"]),
            expert_raw["source_digest"],
            Decimal(expert_raw["target_maximum_position"])
            if expert_raw["target_maximum_position"] is not None
            else None,
        )
    candidate = CandidateEntry(
        candidate_raw["opportunity_id"],
        candidate_raw["security_code"],
        candidate_raw["level1_industry_id"],
        CandidateProjection(candidate_raw["projection"]),
        candidate_raw["input_valid"],
        candidate_raw["knowledge_cutoff_valid"],
        candidate_raw["policy_adopted"],
        expert,
    )
    snapshot_raw = raw["snapshot"]
    positions = tuple(
        MarkedExposure(
            item["unit_id"],
            item["opportunity_id"],
            item["security_code"],
            item["level1_industry_id"],
            item["quantity"],
            Decimal(item["mark_price"]) if item["mark_price"] is not None else None,
            Decimal(item["price_bound"]) if item["price_bound"] is not None else None,
        )
        for item in snapshot_raw["positions"]
    )
    pending = tuple(
        PendingBuy(
            item["reservation_id"],
            item["security_code"],
            item["level1_industry_id"],
            item["remaining_quantity"],
            Decimal(item["mark_price"]) if item["mark_price"] is not None else None,
            Decimal(item["price_bound"]) if item["price_bound"] is not None else None,
            Decimal(item["unrecognized_fee_upper_bound"])
            if item["unrecognized_fee_upper_bound"] is not None
            else None,
            item["fee_already_recognized"],
        )
        for item in snapshot_raw["pending_buys"]
    )
    sells = tuple(
        PendingSell(
            item["sell_id"],
            item["security_code"],
            item["level1_industry_id"],
            item["quantity"],
            Decimal(item["mark_price"]) if item["mark_price"] is not None else None,
        )
        for item in snapshot_raw["pending_sells"]
    )
    snapshot = RiskSnapshot(
        snapshot_raw["snapshot_id"],
        datetime.fromisoformat(snapshot_raw["as_of"]),
        Decimal(snapshot_raw["equity"]) if snapshot_raw["equity"] is not None else None,
        Decimal(snapshot_raw["cash_available"])
        if snapshot_raw["cash_available"] is not None
        else None,
        positions,
        pending,
        sells,
        Decimal(snapshot_raw["recognized_payables"]),
    )
    limits = raw["limits"]
    quantity_limits = raw["quantity_limits"]
    fee = raw["fee_rule"]
    command = raw["command"]
    return CycleEntryIntent(
        raw["expected_account_revision"],
        CommandEnvelope(
            command["command_id"], command["body_digest"], command["expected_revision"]
        ),
        candidate,
        snapshot,
        tuple(raw["empty_bucket_ids"]),
        tuple(raw["active_or_reserved_security_codes"]),
        tuple((item[0], item[1]) for item in raw["active_industry_counts"]),
        raw["new_buckets_today"],
        EntryLimits(
            limits["max_active_or_reserved_per_industry"],
            limits["max_new_buckets_per_day"],
        ),
        Decimal(raw["fraction"]),
        Decimal(raw["mark_price"]),
        Decimal(raw["price_bound"]),
        raw["lot_size"],
        QuantityLimits(
            Decimal(quantity_limits["stock"]),
            Decimal(quantity_limits["industry"]),
            Decimal(quantity_limits["total"]),
        ),
        BuyFeeRule(
            Decimal(fee["commission_rate"]),
            Decimal(fee["minimum_commission"]),
            Decimal(fee["cash_quantum"]),
        ),
    )


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
    cycle_id: str
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
class BuyLot:
    lot_id: str
    security_code: str
    cycle_id: str
    quantity: int
    remaining_quantity: int
    cost_basis: Decimal
    remaining_cost: Decimal
    source_report_id: str
    occurred_at: datetime
    source_id: str
    source_sequence: str

    def __post_init__(self) -> None:
        if not isinstance(self.lot_id, str) or not self.lot_id.strip():
            raise FactError("INVALID_REPORT_SHAPE", "lot identity is required")
        if not isinstance(self.security_code, str) or not self.security_code.strip():
            raise FactError("INVALID_REPORT_SHAPE", "lot security is required")
        if not isinstance(self.cycle_id, str) or not self.cycle_id.strip():
            raise FactError("INVALID_REPORT_SHAPE", "lot cycle is required")
        if not isinstance(self.source_report_id, str) or not self.source_report_id.strip():
            raise FactError("INVALID_REPORT_SHAPE", "lot source report is required")
        if not isinstance(self.occurred_at, datetime) or (
            self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None
        ):
            raise FactError("INVALID_REPORT_SHAPE", "lot occurrence must be aware")
        if (
            not isinstance(self.source_id, str)
            or not self.source_id.strip()
            or not isinstance(self.source_sequence, str)
            or not self.source_sequence.strip()
        ):
            raise FactError("INVALID_REPORT_SHAPE", "lot source identity is required")
        if (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or not isinstance(self.remaining_quantity, int)
            or isinstance(self.remaining_quantity, bool)
            or self.quantity <= 0
            or self.remaining_quantity < 0
        ):
            raise FactError("INVALID_REPORT_SHAPE", "lot quantities are invalid")
        if self.remaining_quantity > self.quantity:
            raise FactError("INVALID_REPORT_SHAPE", "lot remaining quantity exceeds original")
        if (
            not isinstance(self.cost_basis, Decimal)
            or not self.cost_basis.is_finite()
            or self.cost_basis < 0
        ):
            raise FactError("INVALID_REPORT_SHAPE", "lot cost is invalid")
        if (
            not isinstance(self.remaining_cost, Decimal)
            or not self.remaining_cost.is_finite()
            or self.remaining_cost < 0
            or self.remaining_cost > self.cost_basis
        ):
            raise FactError("INVALID_REPORT_SHAPE", "lot remaining cost is invalid")
        if self.remaining_quantity == 0 and self.remaining_cost != 0:
            raise FactError("INVALID_REPORT_SHAPE", "closed lot must have zero cost")


@dataclass(frozen=True)
class SellExecutionReport:
    report_id: str
    security_code: str
    cycle_id: str
    quantity: int
    price: Decimal
    confirmed_fee: Decimal
    occurred_at: datetime
    source_id: str
    source_sequence: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(item, str) or not item.strip()
            for item in (
                self.report_id,
                self.security_code,
                self.cycle_id,
                self.source_id,
                self.source_sequence,
            )
        ):
            raise FactError("INVALID_REPORT_SHAPE", "sell identity is invalid")
        if (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity <= 0
        ):
            raise FactError("INVALID_REPORT_SHAPE", "sell quantity must be positive")
        if not isinstance(self.price, Decimal) or not self.price.is_finite() or self.price <= 0:
            raise FactError("INVALID_REPORT_SHAPE", "sell price must be positive")
        if (
            not isinstance(self.confirmed_fee, Decimal)
            or not self.confirmed_fee.is_finite()
            or self.confirmed_fee < 0
        ):
            raise FactError("INVALID_REPORT_SHAPE", "sell fee is invalid")
        if not isinstance(self.occurred_at, datetime) or (
            self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None
        ):
            raise FactError("INVALID_REPORT_SHAPE", "sell time must be aware")

    @property
    def payload_digest(self) -> str:
        return digest(self.to_dict())

    @property
    def source_fact_id(self) -> str:
        return digest([self.source_id, self.source_sequence])

    def to_dict(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "security_code": self.security_code,
            "cycle_id": self.cycle_id,
            "quantity": self.quantity,
            "price": format(self.price, "f"),
            "confirmed_fee": format(self.confirmed_fee, "f"),
            "occurred_at": self.occurred_at.isoformat(),
            "source_id": self.source_id,
            "source_sequence": self.source_sequence,
        }


@dataclass(frozen=True)
class SellProfile:
    profile_version: str
    source_id: str
    order_total_quantity: int
    final_cumulative_fill_quantity: int
    cancelled_quantity: int
    confirmed_fee: Decimal
    terminal: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.profile_version, str)
            or not self.profile_version
            or not isinstance(self.source_id, str)
            or not self.source_id
            or not isinstance(self.terminal, bool)
            or not self.terminal
        ):
            raise FactError("INVALID_REPORT_SHAPE", "sell profile identity is invalid")
        values = (
            self.order_total_quantity,
            self.final_cumulative_fill_quantity,
            self.cancelled_quantity,
        )
        if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in values):
            raise FactError("INVALID_REPORT_SHAPE", "sell profile quantities are invalid")
        if self.order_total_quantity != (
            self.final_cumulative_fill_quantity + self.cancelled_quantity
        ):
            raise FactError("FINAL_FILL_GAP", "sell terminal quantities differ")
        if (
            not isinstance(self.confirmed_fee, Decimal)
            or not self.confirmed_fee.is_finite()
            or self.confirmed_fee < 0
        ):
            raise FactError("INVALID_REPORT_SHAPE", "sell profile fee is invalid")

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

    @property
    def profile_kind(self) -> str:
        return "full_position"


@dataclass(frozen=True)
class ReductionSellProfile:
    """A complete sell order reducing, but not exhausting, one open lot."""

    profile_version: str
    source_id: str
    order_total_quantity: int
    final_cumulative_fill_quantity: int
    cancelled_quantity: int
    confirmed_fee: Decimal
    terminal: bool

    def __post_init__(self) -> None:
        if self.profile_version != "complete-order-residual-lot-v1":
            raise FactError("UNSUPPORTED_SELL_PROFILE", "reduction profile version differs")
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise FactError("INVALID_REPORT_SHAPE", "reduction source is required")
        if any(
            not isinstance(item, int) or isinstance(item, bool)
            for item in (
                self.order_total_quantity,
                self.final_cumulative_fill_quantity,
                self.cancelled_quantity,
            )
        ):
            raise FactError("INVALID_REPORT_SHAPE", "reduction quantities are invalid")
        if self.order_total_quantity <= 0 or self.final_cumulative_fill_quantity <= 0:
            raise FactError("INVALID_REPORT_SHAPE", "reduction quantities are invalid")
        if self.cancelled_quantity < 0:
            raise FactError("INVALID_REPORT_SHAPE", "reduction cancelled quantity is invalid")
        if self.cancelled_quantity != 0:
            raise FactError("FINAL_FILL_GAP", "reduction order must fill completely")
        if self.order_total_quantity != self.final_cumulative_fill_quantity:
            raise FactError("FINAL_FILL_GAP", "reduction order quantities differ")
        if not isinstance(self.terminal, bool):
            raise FactError("INVALID_REPORT_SHAPE", "reduction terminal flag is invalid")
        if self.terminal is not True:
            raise FactError("UNSUPPORTED_TERMINAL_STATE", "reduction order must be terminal")
        if (
            not isinstance(self.confirmed_fee, Decimal)
            or not self.confirmed_fee.is_finite()
            or self.confirmed_fee < 0
        ):
            raise FactError("INVALID_REPORT_SHAPE", "reduction fee is invalid")

    @property
    def payload_digest(self) -> str:
        return str(
            digest(
                {
                    "profile_kind": self.profile_kind,
                    "profile_version": self.profile_version,
                    "source_id": self.source_id,
                    "order_total_quantity": self.order_total_quantity,
                    "final_cumulative_fill_quantity": self.final_cumulative_fill_quantity,
                    "cancelled_quantity": self.cancelled_quantity,
                    "confirmed_fee": format(self.confirmed_fee, "f"),
                    "terminal": self.terminal,
                }
            )
        )

    @property
    def profile_kind(self) -> str:
        return "residual_lot_reduction"


@dataclass(frozen=True)
class SellImpact:
    account: AccountProjection
    funding: FundingState
    postings: tuple[Posting, ...]
    cash_delta: Decimal
    receipt: ProcessedCommand
    lot: BuyLot


@dataclass(frozen=True)
class SettlementBridgeResult:
    settlement_id: str
    funding: FundingState
    decision: FundingDecision
    account_revision: int
    receipt_id: str


@dataclass(frozen=True)
class LateFeeFact:
    fact_id: str
    bucket_id: str
    cycle_id: str
    cash_delta: Decimal
    source_id: str
    source_sequence: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        if any(
            not isinstance(item, str) or not item.strip()
            for item in (
                self.fact_id,
                self.bucket_id,
                self.cycle_id,
                self.source_id,
                self.source_sequence,
            )
        ):
            raise FactError("INVALID_REPORT_SHAPE", "late fee identity is invalid")
        if (
            not isinstance(self.cash_delta, Decimal)
            or not self.cash_delta.is_finite()
            or self.cash_delta == 0
        ):
            raise FactError("INVALID_REPORT_SHAPE", "late fee delta is invalid")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise FactError("INVALID_REPORT_SHAPE", "late fee time must be aware")

    @property
    def payload_digest(self) -> str:
        return digest(
            {
                "fact_id": self.fact_id,
                "bucket_id": self.bucket_id,
                "cycle_id": self.cycle_id,
                "cash_delta": format(self.cash_delta, "f"),
                "source_id": self.source_id,
                "source_sequence": self.source_sequence,
                "occurred_at": self.occurred_at.isoformat(),
            }
        )


@dataclass(frozen=True)
class LateFeeResult:
    receipt_id: str
    status: str
    account: AccountProjection
    funding: FundingState
    account_revision: int
    postings: tuple[Posting, ...]


@dataclass(frozen=True)
class BridgeState:
    account: AccountProjection
    funding: FundingState
    account_revision: int


@dataclass(frozen=True)
class BridgeSnapshot:
    state: BridgeState
    obligations: tuple[PendingObligation, ...]
    lots: tuple[BuyLot, ...] = ()
    latest_received_at: datetime | None = None

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
                        "cycle_id": item.cycle_id,
                        "quantity": item.quantity,
                        "price_bound": format(item.price_bound, "f"),
                        "fee_upper_bound": format(item.fee_upper_bound, "f"),
                        "source_id": item.source_id,
                        "status": item.status,
                    }
                    for item in self.obligations
                ],
                "lots": [
                    {
                        "lot_id": item.lot_id,
                        "security_code": item.security_code,
                        "cycle_id": item.cycle_id,
                        "quantity": item.quantity,
                        "remaining_quantity": item.remaining_quantity,
                        "cost_basis": format(item.cost_basis, "f"),
                        "remaining_cost": format(item.remaining_cost, "f"),
                        "source_report_id": item.source_report_id,
                        "occurred_at": (
                            item.occurred_at.isoformat() if item.occurred_at is not None else None
                        ),
                        "source_id": item.source_id,
                        "source_sequence": item.source_sequence,
                    }
                    for item in self.lots
                ],
                "latest_received_at": (
                    self.latest_received_at.isoformat()
                    if self.latest_received_at is not None
                    else None
                ),
            }
        )


@dataclass(frozen=True)
class SyntheticTemporalProfile:
    profile_version: str
    received_times: tuple[datetime, ...]
    roles: tuple[str, ...] = ()
    occurred_times: tuple[datetime, ...] = ()
    decision_times: tuple[datetime, ...] = ()

    def __post_init__(self) -> None:
        if not self.profile_version or not self.received_times:
            raise FactError("INVALID_REPORT_SHAPE", "temporal profile is empty")
        if any(item.tzinfo is None or item.utcoffset() is None for item in self.received_times):
            raise FactError("INVALID_REPORT_SHAPE", "temporal profile must be aware")
        if self.roles and len(self.roles) != len(self.received_times):
            raise FactError("INVALID_REPORT_SHAPE", "temporal roles must align")
        if self.occurred_times and len(self.occurred_times) != len(self.received_times):
            raise FactError("INVALID_REPORT_SHAPE", "temporal occurrences must align")
        if self.decision_times and len(self.decision_times) != len(self.received_times):
            raise FactError("INVALID_REPORT_SHAPE", "temporal decisions must align")
        if any(item.tzinfo is None or item.utcoffset() is None for item in self.occurred_times):
            raise FactError("INVALID_REPORT_SHAPE", "temporal occurrences must be aware")
        if any(item.tzinfo is None or item.utcoffset() is None for item in self.decision_times):
            raise FactError("INVALID_REPORT_SHAPE", "temporal decisions must be aware")

    def require(
        self,
        context: SyntheticContext,
        role: str,
        occurred_at: datetime | None = None,
    ) -> None:
        try:
            index = self.received_times.index(context.as_of)
        except ValueError:
            raise FactError(
                "TEMPORAL_PROFILE_REQUIRED", "context is outside synthetic profile"
            ) from None
        if self.roles and self.roles[index] != role:
            raise FactError("TEMPORAL_PROFILE_REQUIRED", "operation role is outside profile")
        if self.decision_times and self.decision_times[index] > context.as_of:
            raise FactError("TEMPORAL_PROFILE_REQUIRED", "decision window is in the future")
        if (
            occurred_at is not None
            and self.occurred_times
            and occurred_at != self.occurred_times[index]
        ):
            raise FactError("TEMPORAL_PROFILE_REQUIRED", "fact occurrence is outside profile")


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
    intent: CycleEntryIntent | None = None


@dataclass(frozen=True)
class LaterAttemptBinding:
    slot_identity: str
    parent_opportunity_id: str
    later_signal_id: str
    policy_version: str
    execution_window_id: str
    attempt_identity: str

    def to_dict(self) -> dict[str, str]:
        return {
            "slot_identity": self.slot_identity,
            "parent_opportunity_id": self.parent_opportunity_id,
            "later_signal_id": self.later_signal_id,
            "policy_version": self.policy_version,
            "execution_window_id": self.execution_window_id,
            "attempt_identity": self.attempt_identity,
        }


@dataclass(frozen=True)
class LaterEntryRequest:
    opportunity: OriginalOpportunity
    prior: SignalObservation | None
    current: SignalObservation
    policy: LaterSignalPolicy | None
    calendar: SyntheticCalendar
    intent: CycleEntryIntent


@dataclass(frozen=True)
class LaterEntryResult:
    decision: LaterSignalDecision
    entry: EntryBridgeResult | None
    binding: LaterAttemptBinding | None


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
    def replay_entry(
        self, context: SyntheticContext, intent: CycleEntryIntent
    ) -> EntryBridgeResult | None: ...
    def read(self, context: SyntheticContext) -> BridgeState: ...
    def read_snapshot(self, context: SyntheticContext) -> BridgeSnapshot: ...
    def obligations(self, context: SyntheticContext) -> tuple[PendingObligation, ...]: ...
    def lots(self, context: SyntheticContext) -> tuple[BuyLot, ...]: ...
    def latest_received_at(self, context: SyntheticContext) -> datetime | None: ...
    def receive_operation(
        self,
        context: SyntheticContext,
        kind: str,
        identity: str,
        payload_digest: str,
        payload: object,
    ) -> str: ...
    def check_source_alias(
        self,
        context: SyntheticContext,
        *,
        identity: str,
        source_id: str,
        source_sequence: str,
        new_digest: str,
        payload: object,
    ) -> None: ...
    def commit_entry(
        self,
        context: SyntheticContext,
        expected: str,
        result: EntryBridgeResult,
        later_binding: LaterAttemptBinding | None = None,
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
    def replay_sell(
        self,
        context: SyntheticContext,
        report: SellExecutionReport,
        profile: SellProfile | ReductionSellProfile,
    ) -> BuyBridgeResult | None: ...
    def replay_settlement(
        self,
        context: SyntheticContext,
        settlement_id: str,
        cycle_id: str,
    ) -> SettlementBridgeResult | None: ...
    def replay_late_fee(
        self,
        context: SyntheticContext,
        fact: LateFeeFact,
    ) -> LateFeeResult | None: ...
    def commit_sell(
        self,
        context: SyntheticContext,
        expected_account_digest: str,
        expected_funding_digest: str,
        receipt_id: str,
        report: SellExecutionReport,
        profile: SellProfile | ReductionSellProfile,
        impact: SellImpact,
    ) -> BuyBridgeResult: ...
    def settlement_facts(
        self, context: SyntheticContext, cycle_id: str
    ) -> tuple[bool, bool, bool, bool]: ...
    def commit_settlement(
        self,
        context: SyntheticContext,
        expected_account_digest: str,
        expected_funding_digest: str,
        result: SettlementBridgeResult,
        request: SettlementRequest,
    ) -> SettlementBridgeResult: ...
    def commit_late_fee(
        self,
        context: SyntheticContext,
        expected_account_digest: str,
        expected_funding_digest: str,
        fact: LateFeeFact,
        result: LateFeeResult,
    ) -> LateFeeResult: ...
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


def calculate_entry_transition(
    state: BridgeState, intent: CycleEntryIntent, source_id: str
) -> EntryBridgeResult:
    decision = decide_entry(
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
    if decision.accepted and decision.reservation_id is not None and decision.quantity is not None:
        bucket_id = decision.selected_bucket_id or ""
        obligation = PendingObligation(
            decision.reservation_id,
            decision.reservation_id,
            intent.candidate.security_code,
            intent.candidate.level1_industry_id,
            bucket_id,
            decision.funding_state.bucket(bucket_id).cycle_id or "",
            decision.quantity.quantity,
            intent.price_bound,
            decision.quantity.fees or Decimal("0"),
            source_id,
        )
    return EntryBridgeResult(
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
        intent,
    )


def apply_confirmed_sell_transition(
    state: BridgeState,
    report: SellExecutionReport,
    profile: SellProfile | ReductionSellProfile,
    lot: BuyLot,
) -> SellImpact:
    """The one full-or-reduction sell calculation used by live posting and rebuild."""

    if not isinstance(profile, (SellProfile, ReductionSellProfile)):
        raise FactError("UNSUPPORTED_SELL_PROFILE", "typed sell profile is required")
    if (
        lot.security_code != report.security_code
        or lot.cycle_id != report.cycle_id
        or report.quantity != profile.final_cumulative_fill_quantity
        or report.confirmed_fee != profile.confirmed_fee
        or report.source_id != profile.source_id
        or profile.cancelled_quantity != 0
    ):
        raise FactError("FINAL_FILL_GAP", "sell report, profile and lot differ")
    if isinstance(profile, SellProfile):
        if lot.remaining_quantity != report.quantity:
            raise FactError("FINAL_FILL_GAP", "full-position profile requires entire residual")
        sold_cost = lot.remaining_cost
        remaining_quantity = 0
        remaining_cost = Decimal("0.00")
    else:
        if report.quantity >= lot.remaining_quantity:
            raise FactError(
                "REDUCTION_PROFILE_NOT_APPLICABLE",
                "reduction profile requires a non-empty residual lot",
            )
        sold_cost = (lot.remaining_cost * report.quantity / lot.remaining_quantity).quantize(
            Decimal("0.01")
        )
        remaining_quantity = lot.remaining_quantity - report.quantity
        remaining_cost = lot.remaining_cost - sold_cost
    if report.occurred_at < lot.occurred_at:
        raise FactError("TEMPORAL_ORDER_VIOLATION", "sell occurred before the matched buy")
    if report.source_id != lot.source_id or report.source_sequence == lot.source_sequence:
        raise FactError("SOURCE_CONTEXT_MISMATCH", "sell source differs from matched buy")
    core = apply_sell_delta(
        state.account,
        security_code=report.security_code,
        quantity=report.quantity,
        price=report.price,
        fee=report.confirmed_fee,
        cost_basis=sold_cost,
    )
    bucket = state.funding.bucket(_bucket_for_cycle(state.funding, report.cycle_id))
    updated_bucket = BucketState(
        bucket.bucket_id,
        bucket.cash + core.cash_delta,
        bucket.target_notional,
        bucket.locked_cycle_budget,
        bucket.cycle_id,
        bucket.phase,
    )
    buckets = tuple(
        updated_bucket if item.bucket_id == bucket.bucket_id else item
        for item in state.funding.buckets
    )
    result_body = {
        "accepted": True,
        "account_cash": format(core.projection.cash, "f"),
        "account_digest": core.projection.digest,
        "cycle_id": report.cycle_id,
        "lot_id": lot.lot_id,
        "profile_digest": profile.payload_digest,
        "source_fact_id": report.source_fact_id,
    }
    if isinstance(profile, ReductionSellProfile):
        result_body.update(
            {
                "profile_kind": profile.profile_kind,
                "cost_basis_consumed": format(sold_cost, "f"),
                "remaining_quantity": remaining_quantity,
                "remaining_cost": format(remaining_cost, "f"),
            }
        )
    command = CommandEnvelope.create(
        f"sell:{report.report_id}:{profile.payload_digest}",
        result_body,
        state.funding.revision,
    )
    receipt = make_receipt(
        command=command,
        operation="post_confirmed_sell",
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
    if funding.cash_total != core.projection.cash:
        raise FactError("BASELINE_IDENTITY_MISMATCH", "sell closure cash differs")
    updated_lot = replace(
        lot,
        remaining_quantity=remaining_quantity,
        remaining_cost=remaining_cost,
    )
    return SellImpact(
        core.projection,
        funding,
        core.postings,
        core.cash_delta,
        receipt,
        updated_lot,
    )


def _bucket_for_cycle(funding: FundingState, cycle_id: str) -> str:
    matches = [item.bucket_id for item in funding.buckets if item.cycle_id == cycle_id]
    if len(matches) != 1:
        raise FactError("CYCLE_IDENTITY_MISMATCH", "cycle does not identify one bucket")
    return matches[0]


def apply_late_fee_transition(
    state: BridgeState,
    fact: LateFeeFact,
    command: CommandEnvelope,
) -> LateFeeResult:
    decision = apply_late_cash_delta(
        state.funding,
        command,
        bucket_id=fact.bucket_id,
        cycle_id=fact.cycle_id,
        cash_delta=fact.cash_delta,
    )
    if not decision.accepted:
        raise FactError("LATE_FEE_NOT_APPLIED", "late fee funding decision refused")
    if decision.replayed:
        cash = state.account.cash
        postings: tuple[Posting, ...] = ()
    else:
        cash = state.account.cash + fact.cash_delta
        postings = (
            Posting("ASSET_CASH", fact.cash_delta),
            Posting("EXPENSE_CONFIRMED_FEE", -fact.cash_delta),
        )
    account = AccountProjection(
        state.account.revision + (0 if decision.replayed else 1),
        state.account.event_sequence + (0 if decision.replayed else 1),
        cash,
        state.account.positions,
        state.account.fact_count + (0 if decision.replayed else 1),
    )
    if account.cash != decision.state.cash_total:
        raise FactError("BASELINE_IDENTITY_MISMATCH", "late fee cash closure differs")
    return LateFeeResult(
        command.command_id,
        "DUPLICATE" if decision.replayed else "POSTED",
        account,
        decision.state,
        account.revision,
        postings,
    )


class FundingBridgeService:
    def __init__(
        self,
        port: FundingBridgePort,
        authorized_context: SyntheticContext,
        authorization: SyntheticRuntimeAuthorization,
        temporal_profile: SyntheticTemporalProfile | None = None,
    ) -> None:
        authorization.require(authorized_context)
        self._port = port
        self._authorization = authorization
        self._context = validate_context(authorized_context)
        self._temporal_profile = temporal_profile

    def _context_for(self, context: SyntheticContext) -> SyntheticContext:
        value = validate_context(context)
        self._authorization.require(value)
        if (
            value.account_id != self._context.account_id
            or value.mode != self._context.mode
            or value.source_id != self._context.source_id
            or value.coverage_id != self._context.coverage_id
            or value.as_of < self._context.as_of
        ):
            raise FactError("UNAUTHORIZED_CONTEXT", "bridge context identity or time differs")
        return value

    def _require_not_before_latest(self, context: SyntheticContext) -> None:
        latest = self._port.latest_received_at(context)
        if latest is not None and context.as_of < latest:
            raise FactError("CONTEXT_TIME_REGRESSION", "caller as_of precedes consumed facts")

    def _require_fact_time(
        self,
        context: SyntheticContext,
        occurred_at: datetime | None,
        *,
        role: str,
        mutation: bool = True,
    ) -> None:
        self._require_not_before_latest(context)
        if occurred_at is not None and occurred_at > context.as_of:
            raise FactError("FUTURE_FACT", "fact occurred after caller as_of")
        if mutation and self._temporal_profile is not None:
            self._temporal_profile.require(context, role, occurred_at)

    def establish_baseline(self, baseline: CycleBaseline) -> BridgeState:
        self._context_for(baseline.context)
        return self._port.establish(baseline)

    def current(self, context: SyntheticContext) -> BridgeState:
        value = self._context_for(context)
        self._require_not_before_latest(value)
        return self._port.read(value)

    def enter(self, context: SyntheticContext, intent: CycleEntryIntent) -> EntryBridgeResult:
        context = self._context_for(context)
        existing = self._port.replay_entry(context, intent)
        if existing is not None:
            return existing
        result, expected = self._prepare_entry(context, intent)
        self._port.commit_entry(context, expected, result)
        return result

    def _prepare_entry(
        self, context: SyntheticContext, intent: CycleEntryIntent
    ) -> tuple[EntryBridgeResult, str]:
        view = self._port.read_snapshot(context)
        state = view.state
        if state.funding.completed_settlements and self._temporal_profile is None:
            raise FactError("TEMPORAL_PROFILE_REQUIRED", "new cycle execution lacks profile")
        known_command = any(
            item.command_id == intent.command.command_id
            for item in state.funding.processed_commands
        )
        if not known_command and state.account_revision != intent.expected_account_revision:
            raise ProductDailyError("ACCOUNT_REVISION_CONFLICT", "account revision is stale")
        if not known_command:
            if intent.snapshot.as_of != context.as_of:
                raise FactError(
                    "RISK_SNAPSHOT_STALE", "risk snapshot as_of differs from caller context"
                )
            self._require_fact_time(context, None, role="entry")
        if (
            state.funding.completed_settlements
            and self._temporal_profile is None
            and not known_command
        ):
            raise FactError("TEMPORAL_PROFILE_REQUIRED", "new cycle admission lacks profile")
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
        result = calculate_entry_transition(state, intent, context.source_id)
        expected = digest(
            {
                "account": state.account.digest,
                "funding": state.funding.digest,
                "revision": state.account_revision,
            }
        )
        return result, expected

    def enter_later(
        self, context: SyntheticContext, request: LaterEntryRequest
    ) -> LaterEntryResult:
        context = self._context_for(context)
        decision = evaluate_later_signal(
            opportunity=request.opportunity,
            prior=request.prior,
            current=request.current,
            policy=request.policy,
            calendar=request.calendar,
        )
        if decision.status is not LaterSignalStatus.ELIGIBLE_FOR_ONE_ATTEMPT:
            return LaterEntryResult(decision, None, None)
        if decision.candidate is None or decision.slot is None:
            raise FactError("INVALID_C13_DECISION", "eligible decision lacks planning identity")
        if decision.slot.account_id != context.account_id:
            raise FactError("C13_ACCOUNT_MISMATCH", "attempt slot belongs to another account")
        if (
            request.intent.candidate.opportunity_id != decision.candidate.identity
            or request.intent.candidate.security_code != request.opportunity.security_id
            or request.intent.command.command_id != decision.candidate.identity
        ):
            raise FactError(
                "C13_ATTEMPT_BINDING_MISMATCH",
                "entry does not bind the planned attempt and security",
            )
        expert = request.intent.candidate.expert_entry
        if expert is None or expert.event_id != request.intent.candidate.opportunity_id:
            raise FactError("C13_ATTEMPT_BINDING_MISMATCH", "attempt eligibility is missing")
        if expert.source_digest != decision.candidate.later_signal_id:
            raise FactError("C13_ATTEMPT_BINDING_MISMATCH", "attempt signal digest differs")
        binding = LaterAttemptBinding(
            decision.slot.identity,
            request.opportunity.opportunity_id,
            decision.candidate.later_signal_id,
            decision.candidate.policy_version,
            decision.candidate.execution_window_id,
            decision.candidate.identity,
        )
        existing = self._port.replay_entry(context, request.intent)
        if existing is not None:
            return LaterEntryResult(decision, existing, binding)
        result, expected = self._prepare_entry(context, request.intent)
        self._port.commit_entry(context, expected, result, binding)
        if not result.accepted:
            return LaterEntryResult(decision, result, None)
        return LaterEntryResult(decision, result, binding)

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
        self._require_fact_time(context, report.occurred_at, role="buy")
        view = self._port.read_snapshot(context)
        if view.state.funding.completed_settlements and self._temporal_profile is None:
            raise FactError("TEMPORAL_PROFILE_REQUIRED", "new cycle execution lacks profile")
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

    def submit_sell(
        self,
        context: SyntheticContext,
        report: SellExecutionReport,
        profile: SellProfile | ReductionSellProfile,
    ) -> BuyBridgeResult:
        context = self._context_for(context)
        report.__post_init__()
        profile.__post_init__()
        existing = self._port.replay_sell(context, report, profile)
        if existing is not None:
            return existing
        receipt_id = self._port.receive_operation(
            context,
            "sell",
            report.report_id,
            digest({"report": report.to_dict(), "profile": profile.payload_digest}),
            {"report": report.to_dict(), "profile_digest": profile.payload_digest},
        )
        self._port.check_source_alias(
            context,
            identity=report.report_id,
            source_id=report.source_id,
            source_sequence=report.source_sequence,
            new_digest=receipt_id,
            payload=report.to_dict(),
        )
        self._require_fact_time(context, report.occurred_at, role="sell")
        view = self._port.read_snapshot(context)
        if view.state.funding.completed_settlements and self._temporal_profile is None:
            raise FactError("TEMPORAL_PROFILE_REQUIRED", "new cycle sell lacks profile")
        lots = tuple(
            item
            for item in view.lots
            if item.security_code == report.security_code
            and item.cycle_id == report.cycle_id
            and item.remaining_quantity >= report.quantity
            and item.remaining_quantity > 0
        )
        if len(lots) != 1:
            raise FactError("LOT_IDENTITY_MISMATCH", "sell does not match one open lot")
        impact = apply_confirmed_sell_transition(view.state, report, profile, lots[0])
        return self._port.commit_sell(
            context,
            view.state.account.digest,
            view.state.funding.digest,
            receipt_id,
            report,
            profile,
            impact,
        )

    def settle_cycle(
        self, context: SyntheticContext, *, settlement_id: str, cycle_id: str
    ) -> SettlementBridgeResult:
        context = self._context_for(context)
        existing = self._port.replay_settlement(context, settlement_id, cycle_id)
        if existing is not None:
            return existing
        self._require_not_before_latest(context)
        if self._temporal_profile is not None:
            self._temporal_profile.require(context, "settlement")
        view = self._port.read_snapshot(context)
        if view.state.funding.completed_settlements and self._temporal_profile is None:
            raise FactError("TEMPORAL_PROFILE_REQUIRED", "later settlement lacks profile")
        bucket = view.state.funding.bucket(_bucket_for_cycle(view.state.funding, cycle_id))
        request = SettlementRequest(
            settlement_id,
            bucket.bucket_id,
            cycle_id,
            CycleSettlementEvidence(
                not view.state.account.positions,
                *self._port.settlement_facts(context, cycle_id),
            ),
        )
        payload = settle_invocation_payload((request,))
        payload_digest = digest(payload)
        receipt_id = self._port.receive_operation(
            context, "settlement", settlement_id, payload_digest, payload
        )
        if not request.evidence.settlement_ready:
            raise FactError("SETTLEMENT_NOT_READY", "actual facts do not prove completeness")
        closed_funding = FundingState(
            tuple(
                replace(item, phase=BucketPhase.CLOSED)
                if item.bucket_id == bucket.bucket_id
                else item
                for item in view.state.funding.buckets
            ),
            view.state.funding.reserve,
            view.state.funding.unallocated_adjustment,
            view.state.funding.revision,
            view.state.funding.processed_commands,
            view.state.funding.completed_settlements,
            view.state.funding.entry_reservations,
        )
        command = CommandEnvelope.create(
            f"settle:{settlement_id}", payload, closed_funding.revision
        )
        decision = settle_batch(closed_funding, command, (request,))
        if not decision.accepted:
            raise FactError("SETTLEMENT_NOT_READY", "settle_batch refused actual facts")
        account = replace(view.state.account, revision=view.state.account.revision + 1)
        result = SettlementBridgeResult(
            settlement_id, decision.state, decision, account.revision, receipt_id
        )
        return self._port.commit_settlement(
            context,
            view.state.account.digest,
            view.state.funding.digest,
            result,
            request,
        )

    def apply_late_fee(self, context: SyntheticContext, fact: LateFeeFact) -> LateFeeResult:
        context = self._context_for(context)
        fact.__post_init__()
        existing = self._port.replay_late_fee(context, fact)
        if existing is not None:
            return existing
        receipt_id = self._port.receive_operation(
            context,
            "late_fee",
            fact.fact_id,
            fact.payload_digest,
            {
                "fact_id": fact.fact_id,
                "bucket_id": fact.bucket_id,
                "cycle_id": fact.cycle_id,
                "cash_delta": format(fact.cash_delta, "f"),
                "source_id": fact.source_id,
                "source_sequence": fact.source_sequence,
                "occurred_at": fact.occurred_at.isoformat(),
            },
        )
        self._port.check_source_alias(
            context,
            identity=fact.fact_id,
            source_id=fact.source_id,
            source_sequence=fact.source_sequence,
            new_digest=receipt_id,
            payload={
                "fact_id": fact.fact_id,
                "cash_delta": format(fact.cash_delta, "f"),
            },
        )
        self._require_fact_time(context, fact.occurred_at, role="late_fee")
        view = self._port.read_snapshot(context)
        if view.state.funding.completed_settlements and self._temporal_profile is None:
            raise FactError("TEMPORAL_PROFILE_REQUIRED", "late fee lacks profile")
        payload = late_cash_invocation_payload(
            bucket_id=fact.bucket_id,
            cycle_id=fact.cycle_id,
            cash_delta=fact.cash_delta,
        )
        command = CommandEnvelope.create(fact.fact_id, payload, view.state.funding.revision)
        result = apply_late_fee_transition(view.state, fact, command)
        result = replace(result, receipt_id=receipt_id)
        return self._port.commit_late_fee(
            context,
            view.state.account.digest,
            view.state.funding.digest,
            fact,
            result,
        )

    def risk_snapshot(self, context: SyntheticContext, *, mark_price: Decimal) -> RiskSnapshot:
        value = self._context_for(context)
        self._require_not_before_latest(value)
        view = self._port.read_snapshot(value)
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
            value.as_of,
            state.account.cash + marked,
            state.account.cash,
            positions,
            pending_buys=pending,
        )

    def rebuild(self, context: SyntheticContext) -> BridgeState:
        return self._port.rebuild(self._context_for(context))
