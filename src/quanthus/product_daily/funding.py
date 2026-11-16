"""Fixed-bucket, finite-reserve and late-adjustment policy functions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Any

from quanthus.product_daily.contracts import (
    CommandDisposition,
    CommandEnvelope,
    ProcessedCommand,
    ProductDailyError,
    ReasonCode,
    decimal_text,
    digest,
    make_receipt,
    ordered_reasons,
    quantize,
    require_decimal,
    require_text,
    resolve_command,
)


class BucketPhase(str, Enum):
    EMPTY = "empty"
    RESERVED = "reserved"
    HELD = "held"
    CLOSED = "closed"


@dataclass(frozen=True)
class BucketState:
    bucket_id: str
    cash: Decimal
    target_notional: Decimal
    locked_cycle_budget: Decimal
    cycle_id: str | None
    phase: BucketPhase

    def __post_init__(self) -> None:
        require_text(self.bucket_id, "bucket_id")
        require_decimal(self.cash, "bucket.cash", minimum=Decimal("0"))
        require_decimal(
            self.target_notional,
            "bucket.target_notional",
            minimum=Decimal("0.0000000001"),
        )
        require_decimal(
            self.locked_cycle_budget,
            "bucket.locked_cycle_budget",
            minimum=Decimal("0"),
            maximum=self.target_notional,
        )
        if not isinstance(self.phase, BucketPhase):
            raise ProductDailyError("invalid_bucket", "phase must be a BucketPhase")
        if self.phase is BucketPhase.EMPTY:
            if self.cycle_id is not None or self.locked_cycle_budget != 0:
                raise ProductDailyError(
                    "invalid_bucket", "an empty bucket cannot carry cycle identity or budget"
                )
        else:
            require_text(self.cycle_id, "bucket.cycle_id")
            if self.locked_cycle_budget <= 0:
                raise ProductDailyError(
                    "invalid_bucket", "a non-empty cycle requires a positive locked budget"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket_id": self.bucket_id,
            "cash": decimal_text(self.cash),
            "target_notional": decimal_text(self.target_notional),
            "locked_cycle_budget": decimal_text(self.locked_cycle_budget),
            "cycle_id": self.cycle_id,
            "phase": self.phase.value,
        }


@dataclass(frozen=True)
class CompletedSettlement:
    settlement_id: str
    bucket_id: str
    cycle_id: str
    cash_after: Decimal

    def __post_init__(self) -> None:
        for field_name in ("settlement_id", "bucket_id", "cycle_id"):
            require_text(getattr(self, field_name), field_name)
        require_decimal(self.cash_after, "settlement.cash_after", minimum=Decimal("0"))

    def to_dict(self) -> dict[str, str]:
        return {
            "settlement_id": self.settlement_id,
            "bucket_id": self.bucket_id,
            "cycle_id": self.cycle_id,
            "cash_after": decimal_text(self.cash_after) or "0",
        }


@dataclass(frozen=True)
class FundingState:
    buckets: tuple[BucketState, ...]
    reserve: Decimal
    unallocated_adjustment: Decimal
    revision: int
    processed_commands: tuple[ProcessedCommand, ...] = ()
    completed_settlements: tuple[CompletedSettlement, ...] = ()

    def __post_init__(self) -> None:
        if not self.buckets:
            raise ProductDailyError("invalid_funding_state", "at least one bucket is required")
        if len({item.bucket_id for item in self.buckets}) != len(self.buckets):
            raise ProductDailyError("invalid_funding_state", "bucket ids must be unique")
        require_decimal(self.reserve, "reserve", minimum=Decimal("0"))
        require_decimal(
            self.unallocated_adjustment,
            "unallocated_adjustment",
            maximum=Decimal("0"),
        )
        if self.revision < 0:
            raise ProductDailyError("invalid_revision", "revision must not be negative")
        if len({item.command_id for item in self.processed_commands}) != len(
            self.processed_commands
        ):
            raise ProductDailyError("invalid_receipt", "processed command ids must be unique")
        if len({item.settlement_id for item in self.completed_settlements}) != len(
            self.completed_settlements
        ):
            raise ProductDailyError("invalid_settlement", "settlement ids must be unique")

    @property
    def cash_total(self) -> Decimal:
        return (
            sum((item.cash for item in self.buckets), Decimal("0"))
            + self.reserve
            + self.unallocated_adjustment
        )

    def bucket(self, bucket_id: str) -> BucketState:
        matches = [item for item in self.buckets if item.bucket_id == bucket_id]
        if len(matches) != 1:
            raise ProductDailyError("unknown_bucket", f"unknown bucket {bucket_id}")
        return matches[0]

    @property
    def digest(self) -> str:
        return digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "buckets": [item.to_dict() for item in sorted(self.buckets, key=lambda x: x.bucket_id)],
            "reserve": decimal_text(self.reserve),
            "unallocated_adjustment": decimal_text(self.unallocated_adjustment),
            "cash_total": decimal_text(self.cash_total),
            "revision": self.revision,
            "processed_commands": [item.to_dict() for item in self.processed_commands],
            "completed_settlements": [
                item.to_dict()
                for item in sorted(
                    self.completed_settlements,
                    key=lambda x: (x.settlement_id, x.bucket_id),
                )
            ],
        }


@dataclass(frozen=True)
class FundingLedgerPosting:
    account: str
    amount: Decimal

    def __post_init__(self) -> None:
        require_text(self.account, "ledger account")
        require_decimal(self.amount, "ledger amount")

    def to_dict(self) -> dict[str, str]:
        return {"account": self.account, "amount": decimal_text(self.amount) or "0"}


@dataclass(frozen=True)
class FundingLedgerEntry:
    reference_id: str
    reference_type: str
    postings: tuple[FundingLedgerPosting, ...]

    def __post_init__(self) -> None:
        require_text(self.reference_id, "ledger reference_id")
        require_text(self.reference_type, "ledger reference_type")
        if not self.postings:
            raise ProductDailyError("invalid_ledger", "ledger entry must contain postings")
        if sum((item.amount for item in self.postings), Decimal("0")) != 0:
            raise ProductDailyError("unbalanced_ledger", "funding postings must balance to zero")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "reference_type": self.reference_type,
            "postings": [item.to_dict() for item in self.postings],
        }


@dataclass(frozen=True)
class FundingDecision:
    operation: str
    accepted: bool
    replayed: bool
    state: FundingState
    reason_codes: tuple[ReasonCode, ...]
    ledger_entries: tuple[FundingLedgerEntry, ...]
    details_json: str
    result_digest: str

    def __post_init__(self) -> None:
        require_text(self.operation, "operation")
        try:
            parsed = json.loads(self.details_json)
        except json.JSONDecodeError as exc:
            raise ProductDailyError("invalid_result", "details_json must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ProductDailyError("invalid_result", "details must be an object")

    @property
    def details(self) -> dict[str, Any]:
        parsed = json.loads(self.details_json)
        if not isinstance(parsed, dict):
            raise ProductDailyError("invalid_result", "details must be an object")
        return parsed

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "accepted": self.accepted,
            "replayed": self.replayed,
            "reason_codes": [item.value for item in self.reason_codes],
            "details": self.details,
            "state_digest": self.state.digest,
            "state_revision": self.state.revision,
            "cash_total": decimal_text(self.state.cash_total),
            "reserve": decimal_text(self.state.reserve),
            "unallocated_adjustment": decimal_text(self.state.unallocated_adjustment),
            "ledger_entries": [item.to_dict() for item in self.ledger_entries],
            "result_digest": self.result_digest,
        }


@dataclass(frozen=True)
class CycleSettlementEvidence:
    position_flat: bool
    orders_terminal: bool
    fills_complete: bool
    fees_known: bool
    conflicts_clear: bool

    @property
    def settlement_ready(self) -> bool:
        return all(
            (
                self.position_flat,
                self.orders_terminal,
                self.fills_complete,
                self.fees_known,
                self.conflicts_clear,
            )
        )

    def to_dict(self) -> dict[str, bool]:
        return {
            "position_flat": self.position_flat,
            "orders_terminal": self.orders_terminal,
            "fills_complete": self.fills_complete,
            "fees_known": self.fees_known,
            "conflicts_clear": self.conflicts_clear,
        }


@dataclass(frozen=True)
class SettlementRequest:
    settlement_id: str
    bucket_id: str
    cycle_id: str
    evidence: CycleSettlementEvidence

    def __post_init__(self) -> None:
        for field_name in ("settlement_id", "bucket_id", "cycle_id"):
            require_text(getattr(self, field_name), field_name)


def _json_details(details: Mapping[str, Any]) -> str:
    def convert(value: Any) -> Any:
        if isinstance(value, Decimal):
            return decimal_text(value)
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {str(key): convert(item) for key, item in value.items()}
        return value

    return json.dumps(convert(details), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _decision(
    *,
    operation: str,
    accepted: bool,
    replayed: bool,
    state: FundingState,
    reasons: Sequence[ReasonCode],
    ledger_entries: Sequence[FundingLedgerEntry],
    details: Mapping[str, Any],
    receipt: ProcessedCommand | None = None,
) -> FundingDecision:
    ordered = ordered_reasons(list(reasons))
    payload = {
        "operation": operation,
        "accepted": accepted,
        "reason_codes": [item.value for item in ordered],
        "details": _json_details(details),
    }
    return FundingDecision(
        operation=operation,
        accepted=accepted,
        replayed=replayed,
        state=state,
        reason_codes=ordered,
        ledger_entries=tuple(ledger_entries),
        details_json=_json_details(details),
        result_digest=(receipt.result_digest if receipt is not None else digest(payload)),
    )


def _duplicate_decision(
    *,
    operation: str,
    state: FundingState,
    receipt: ProcessedCommand,
    ledger_entries: Sequence[FundingLedgerEntry] = (),
) -> FundingDecision:
    stored = receipt.result
    reasons = tuple(ReasonCode(item) for item in stored.get("reason_codes", []))
    raw_details = stored.get("details_json", "{}")
    if not isinstance(raw_details, str):
        raise ProductDailyError("invalid_receipt", "details_json in receipt must be a string")
    if operation == "prepare_cycle":
        parsed_details = json.loads(raw_details)
        if isinstance(parsed_details, dict):
            parsed_details["original_transfer"] = parsed_details.get("transfer")
            parsed_details["transfer"] = "0"
            parsed_details["state_unchanged"] = True
            raw_details = json.dumps(
                parsed_details, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            )
    return FundingDecision(
        operation=operation,
        accepted=bool(stored.get("accepted", False)),
        replayed=True,
        state=state,
        reason_codes=reasons,
        ledger_entries=tuple(ledger_entries),
        details_json=raw_details,
        result_digest=receipt.result_digest,
    )


def _cas_decision(
    *,
    operation: str,
    state: FundingState,
) -> FundingDecision:
    return _decision(
        operation=operation,
        accepted=False,
        replayed=False,
        state=state,
        reasons=(ReasonCode.CAS_CONFLICT,),
        ledger_entries=(),
        details={"expected_state_unchanged": True},
    )


def _append_receipt(
    *,
    state: FundingState,
    command: CommandEnvelope,
    operation: str,
    reasons: Sequence[ReasonCode],
    details: Mapping[str, Any],
) -> ProcessedCommand:
    payload = {
        "operation": operation,
        "accepted": True,
        "reason_codes": [item.value for item in ordered_reasons(list(reasons))],
        "details_json": _json_details(details),
    }
    return make_receipt(command=command, result=payload, revision=state.revision + 1)


def _settle_one(
    state: FundingState,
    request: SettlementRequest,
) -> tuple[FundingState, dict[str, Any], tuple[FundingLedgerEntry, ...]]:
    if not request.evidence.settlement_ready:
        return (
            state,
            {
                "settlement_id": request.settlement_id,
                "accepted": False,
                "reason": ReasonCode.SETTLEMENT_NOT_READY.value,
                "evidence": request.evidence.to_dict(),
            },
            (),
        )
    if any(
        item.settlement_id == request.settlement_id
        or (item.bucket_id == request.bucket_id and item.cycle_id == request.cycle_id)
        for item in state.completed_settlements
    ):
        return (
            state,
            {
                "settlement_id": request.settlement_id,
                "accepted": False,
                "reason": ReasonCode.SETTLEMENT_ALREADY_COMPLETED.value,
            },
            (),
        )

    bucket = state.bucket(request.bucket_id)
    if bucket.cycle_id != request.cycle_id or bucket.phase is not BucketPhase.CLOSED:
        raise ProductDailyError(
            "invalid_settlement_identity",
            "settlement must reference a closed bucket cycle",
        )

    initial_cash = bucket.cash
    reserve = state.reserve
    profit_to_reserve = Decimal("0")
    reserve_topup = Decimal("0")
    underfunded = Decimal("0")
    postings: list[FundingLedgerPosting] = []

    if initial_cash > bucket.target_notional:
        profit_to_reserve = initial_cash - bucket.target_notional
        final_cash = bucket.target_notional
        reserve += profit_to_reserve
        postings.extend(
            (
                FundingLedgerPosting(f"bucket/{bucket.bucket_id}/cash", -profit_to_reserve),
                FundingLedgerPosting("shared/reserve", profit_to_reserve),
            )
        )
    elif initial_cash < bucket.target_notional:
        shortage = bucket.target_notional - initial_cash
        reserve_topup = min(reserve, shortage)
        final_cash = initial_cash + reserve_topup
        reserve -= reserve_topup
        underfunded = shortage - reserve_topup
        if reserve_topup > 0:
            postings.extend(
                (
                    FundingLedgerPosting("shared/reserve", -reserve_topup),
                    FundingLedgerPosting(f"bucket/{bucket.bucket_id}/cash", reserve_topup),
                )
            )
    else:
        final_cash = initial_cash

    next_bucket = replace(bucket, cash=final_cash)
    buckets = tuple(
        sorted(
            (next_bucket if item.bucket_id == bucket.bucket_id else item for item in state.buckets),
            key=lambda item: item.bucket_id,
        )
    )
    completed = CompletedSettlement(
        settlement_id=request.settlement_id,
        bucket_id=request.bucket_id,
        cycle_id=request.cycle_id,
        cash_after=final_cash,
    )
    next_state = replace(
        state,
        buckets=buckets,
        reserve=reserve,
        completed_settlements=tuple(
            sorted((*state.completed_settlements, completed), key=lambda item: item.settlement_id)
        ),
    )
    ledger_entries: tuple[FundingLedgerEntry, ...] = ()
    if postings:
        ledger_entries = (
            FundingLedgerEntry(
                reference_id=request.settlement_id,
                reference_type="fixed_cycle_settlement",
                postings=tuple(postings),
            ),
        )
    details = {
        "settlement_id": request.settlement_id,
        "bucket_id": request.bucket_id,
        "cycle_id": request.cycle_id,
        "accepted": True,
        "profit_to_reserve": profit_to_reserve,
        "reserve_topup": reserve_topup,
        "underfunded": underfunded,
        "bucket_cash_after": final_cash,
        "next_cycle_available": final_cash,
        "new_cycle_budget_unchanged": True,
    }
    return next_state, details, ledger_entries


def settle_batch(
    state: FundingState,
    command: CommandEnvelope,
    requests: Sequence[SettlementRequest],
    *,
    cash_quantum: Decimal = Decimal("0.01"),
) -> FundingDecision:
    """Settle eligible buckets in confirmed bucket-id order with one shared reserve."""

    resolution = resolve_command(
        revision=state.revision,
        processed=state.processed_commands,
        command=command,
    )
    if resolution.disposition is CommandDisposition.DUPLICATE:
        if resolution.receipt is None:
            raise ProductDailyError("invalid_receipt", "duplicate command lacks receipt")
        return _duplicate_decision(
            operation="settle_batch", state=state, receipt=resolution.receipt
        )
    if resolution.disposition is CommandDisposition.CAS_CONFLICT:
        return _cas_decision(operation="settle_batch", state=state)

    ordered = tuple(sorted(requests, key=lambda item: (item.bucket_id, item.settlement_id)))
    if not ordered:
        raise ProductDailyError("invalid_settlement", "settlement batch must not be empty")
    if len({item.settlement_id for item in ordered}) != len(ordered):
        raise ProductDailyError("duplicate_settlement", "settlement ids must be unique")
    if len({item.bucket_id for item in ordered}) != len(ordered):
        raise ProductDailyError("duplicate_settlement", "one batch may settle each bucket once")

    working = state
    rows: list[dict[str, Any]] = []
    ledgers: list[FundingLedgerEntry] = []
    for request in ordered:
        working, row, entries = _settle_one(working, request)
        rows.append(row)
        ledgers.extend(entries)
    if not all(bool(row["accepted"]) for row in rows):
        return _decision(
            operation="settle_batch",
            accepted=False,
            replayed=False,
            state=state,
            reasons=(ReasonCode.SETTLEMENT_NOT_READY,),
            ledger_entries=(),
            details={"settlements": rows, "state_unchanged": True},
        )

    reasons = (ReasonCode.FIXED_CYCLE_SETTLED,)
    details = {"settlements": rows}
    receipt = _append_receipt(
        state=working,
        command=command,
        operation="settle_batch",
        reasons=reasons,
        details=details,
    )
    committed = replace(
        working,
        revision=working.revision + 1,
        processed_commands=(*working.processed_commands, receipt),
    )
    normalized = tuple(
        FundingLedgerEntry(
            reference_id=item.reference_id,
            reference_type=item.reference_type,
            postings=tuple(
                FundingLedgerPosting(
                    item.account,
                    quantize(item.amount, cash_quantum),
                )
                for item in item.postings
            ),
        )
        for item in ledgers
    )
    return _decision(
        operation="settle_batch",
        accepted=True,
        replayed=False,
        state=committed,
        reasons=reasons,
        ledger_entries=normalized,
        details=details,
        receipt=receipt,
    )


def prepare_cycle(
    state: FundingState,
    command: CommandEnvelope,
    *,
    bucket_id: str,
    cycle_id: str,
    cash_quantum: Decimal = Decimal("0.01"),
) -> FundingDecision:
    """Top up a new cycle from available reserve and lock its actual B once."""

    require_text(cycle_id, "cycle_id")
    resolution = resolve_command(
        revision=state.revision,
        processed=state.processed_commands,
        command=command,
    )
    if resolution.disposition is CommandDisposition.DUPLICATE:
        if resolution.receipt is None:
            raise ProductDailyError("invalid_receipt", "duplicate command lacks receipt")
        return _duplicate_decision(
            operation="prepare_cycle", state=state, receipt=resolution.receipt
        )
    if resolution.disposition is CommandDisposition.CAS_CONFLICT:
        return _cas_decision(operation="prepare_cycle", state=state)

    bucket = state.bucket(bucket_id)
    if bucket.phase in {BucketPhase.RESERVED, BucketPhase.HELD}:
        if bucket.cycle_id == cycle_id:
            return _decision(
                operation="prepare_cycle",
                accepted=True,
                replayed=False,
                state=state,
                reasons=(ReasonCode.CYCLE_ALREADY_ACTIVE,),
                ledger_entries=(),
                details={
                    "bucket_id": bucket_id,
                    "cycle_id": cycle_id,
                    "transfer": Decimal("0"),
                    "B": bucket.locked_cycle_budget,
                    "state_unchanged": True,
                },
            )
        raise ProductDailyError(
            "cycle_already_active",
            "a bucket cannot replace its active cycle budget",
        )

    reserve = state.reserve
    adjustment = state.unallocated_adjustment
    adjustment_offset = Decimal("0")
    pre_postings: list[FundingLedgerPosting] = []
    if adjustment < 0 and reserve > 0:
        adjustment_offset = min(reserve, -adjustment)
        reserve -= adjustment_offset
        adjustment += adjustment_offset
        pre_postings.extend(
            (
                FundingLedgerPosting("shared/reserve", -adjustment_offset),
                FundingLedgerPosting("shared/unallocated_adjustment", adjustment_offset),
            )
        )

    shortage = max(bucket.target_notional - bucket.cash, Decimal("0"))
    transfer = min(reserve, shortage)
    bucket_cash = bucket.cash + transfer
    reserve -= transfer
    locked = min(bucket.target_notional, bucket_cash)
    if locked <= 0:
        return _decision(
            operation="prepare_cycle",
            accepted=False,
            replayed=False,
            state=state,
            reasons=(ReasonCode.BUDGET_CAPACITY,),
            ledger_entries=(),
            details={"bucket_id": bucket_id, "state_unchanged": True},
        )

    next_bucket = replace(
        bucket,
        cash=quantize(bucket_cash, cash_quantum),
        locked_cycle_budget=quantize(locked, cash_quantum),
        cycle_id=cycle_id,
        phase=BucketPhase.RESERVED,
    )
    working = replace(
        state,
        buckets=tuple(
            sorted(
                (next_bucket if item.bucket_id == bucket_id else item for item in state.buckets),
                key=lambda item: item.bucket_id,
            )
        ),
        reserve=quantize(reserve, cash_quantum),
        unallocated_adjustment=quantize(adjustment, cash_quantum),
    )
    postings = list(pre_postings)
    if transfer > 0:
        postings.extend(
            (
                FundingLedgerPosting("shared/reserve", -transfer),
                FundingLedgerPosting(f"bucket/{bucket_id}/cash", transfer),
            )
        )
    ledger_entries: tuple[FundingLedgerEntry, ...] = ()
    if postings:
        ledger_entries = (
            FundingLedgerEntry(
                reference_id=command.command_id,
                reference_type="cycle_pretopup",
                postings=tuple(postings),
            ),
        )
    reasons = (ReasonCode.CYCLE_PREPARED,)
    details = {
        "bucket_id": bucket_id,
        "cycle_id": cycle_id,
        "adjustment_offset": adjustment_offset,
        "transfer": transfer,
        "B": locked,
        "underfunded": bucket.target_notional - locked,
    }
    receipt = _append_receipt(
        state=working,
        command=command,
        operation="prepare_cycle",
        reasons=reasons,
        details=details,
    )
    committed = replace(
        working,
        revision=working.revision + 1,
        processed_commands=(*working.processed_commands, receipt),
    )
    return _decision(
        operation="prepare_cycle",
        accepted=True,
        replayed=False,
        state=committed,
        reasons=reasons,
        ledger_entries=ledger_entries,
        details=details,
        receipt=receipt,
    )


def apply_late_cash_delta(
    state: FundingState,
    command: CommandEnvelope,
    *,
    bucket_id: str,
    cycle_id: str,
    cash_delta: Decimal,
    cash_quantum: Decimal = Decimal("0.01"),
) -> FundingDecision:
    """Apply a closed-cycle late fee without rewriting that cycle or next-cycle B."""

    resolution = resolve_command(
        revision=state.revision,
        processed=state.processed_commands,
        command=command,
    )
    if resolution.disposition is CommandDisposition.DUPLICATE:
        if resolution.receipt is None:
            raise ProductDailyError("invalid_receipt", "duplicate command lacks receipt")
        return _duplicate_decision(
            operation="apply_late_cash_delta", state=state, receipt=resolution.receipt
        )
    if resolution.disposition is CommandDisposition.CAS_CONFLICT:
        return _cas_decision(operation="apply_late_cash_delta", state=state)

    bucket = state.bucket(bucket_id)
    require_decimal(cash_delta, "cash_delta", maximum=Decimal("0"))
    amount = quantize(-cash_delta, cash_quantum)
    if amount <= 0:
        raise ProductDailyError("invalid_late_delta", "late cash delta must reduce cash")
    if bucket.phase is not BucketPhase.CLOSED or bucket.cycle_id != cycle_id:
        raise ProductDailyError(
            "invalid_late_cycle", "late cash delta requires the named closed cycle"
        )

    reserve_used = min(state.reserve, amount)
    remaining = amount - reserve_used
    reserve = state.reserve - reserve_used
    adjustment = state.unallocated_adjustment - remaining
    next_state = replace(
        state,
        reserve=reserve,
        unallocated_adjustment=adjustment,
    )
    postings: list[FundingLedgerPosting] = []
    if reserve_used > 0:
        postings.append(FundingLedgerPosting("shared/reserve", -reserve_used))
    if remaining > 0:
        postings.append(FundingLedgerPosting("shared/unallocated_adjustment", -remaining))
    postings.append(FundingLedgerPosting("fees/late_adjustment", amount))
    ledger = (
        FundingLedgerEntry(
            reference_id=command.command_id,
            reference_type="late_cash_adjustment",
            postings=tuple(postings),
        ),
    )
    reasons = (ReasonCode.LATE_DELTA_APPLIED,)
    details = {
        "bucket_id": bucket_id,
        "cycle_id": cycle_id,
        "cash_delta": cash_delta,
        "reserve_used": reserve_used,
        "adjustment_delta": -remaining,
        "new_B": bucket.locked_cycle_budget,
    }
    receipt = _append_receipt(
        state=next_state,
        command=command,
        operation="apply_late_cash_delta",
        reasons=reasons,
        details=details,
    )
    committed = replace(
        next_state,
        revision=next_state.revision + 1,
        processed_commands=(*next_state.processed_commands, receipt),
    )
    return _decision(
        operation="apply_late_cash_delta",
        accepted=True,
        replayed=False,
        state=committed,
        reasons=reasons,
        ledger_entries=ledger,
        details=details,
        receipt=receipt,
    )


def transfer_to_reserve(
    state: FundingState,
    command: CommandEnvelope,
    *,
    source_bucket_id: str,
    amount: Decimal,
    cash_quantum: Decimal = Decimal("0.01"),
) -> FundingDecision:
    """Move internal cash to reserve, first extinguishing negative adjustment."""

    resolution = resolve_command(
        revision=state.revision,
        processed=state.processed_commands,
        command=command,
    )
    if resolution.disposition is CommandDisposition.DUPLICATE:
        if resolution.receipt is None:
            raise ProductDailyError("invalid_receipt", "duplicate command lacks receipt")
        return _duplicate_decision(
            operation="transfer_to_reserve", state=state, receipt=resolution.receipt
        )
    if resolution.disposition is CommandDisposition.CAS_CONFLICT:
        return _cas_decision(operation="transfer_to_reserve", state=state)

    movement = quantize(
        require_decimal(amount, "amount", minimum=Decimal("0.0000000001")), cash_quantum
    )
    bucket = state.bucket(source_bucket_id)
    if bucket.phase not in {BucketPhase.EMPTY, BucketPhase.CLOSED}:
        raise ProductDailyError(
            "bucket_not_transferable",
            "internal reserve transfer requires an empty or closed source bucket",
        )
    if movement > bucket.cash:
        raise ProductDailyError("insufficient_bucket_cash", "reserve transfer exceeds bucket cash")
    adjustment_offset = min(movement, -state.unallocated_adjustment)
    reserve_increment = movement - adjustment_offset
    next_bucket = replace(bucket, cash=bucket.cash - movement)
    next_state = replace(
        state,
        buckets=tuple(
            sorted(
                (
                    next_bucket if item.bucket_id == source_bucket_id else item
                    for item in state.buckets
                ),
                key=lambda item: item.bucket_id,
            )
        ),
        reserve=state.reserve + reserve_increment,
        unallocated_adjustment=state.unallocated_adjustment + adjustment_offset,
    )
    postings = [FundingLedgerPosting(f"bucket/{source_bucket_id}/cash", -movement)]
    if adjustment_offset > 0:
        postings.append(FundingLedgerPosting("shared/unallocated_adjustment", adjustment_offset))
    if reserve_increment > 0:
        postings.append(FundingLedgerPosting("shared/reserve", reserve_increment))
    ledger = (
        FundingLedgerEntry(
            reference_id=command.command_id,
            reference_type="internal_transfer_to_reserve",
            postings=tuple(postings),
        ),
    )
    reasons = (ReasonCode.RESERVE_TRANSFER_APPLIED,)
    details = {
        "source_bucket_id": source_bucket_id,
        "transfer": movement,
        "adjustment_offset": adjustment_offset,
        "reserve_increment": reserve_increment,
        "new_B_unchanged": True,
    }
    receipt = _append_receipt(
        state=next_state,
        command=command,
        operation="transfer_to_reserve",
        reasons=reasons,
        details=details,
    )
    committed = replace(
        next_state,
        revision=next_state.revision + 1,
        processed_commands=(*next_state.processed_commands, receipt),
    )
    return _decision(
        operation="transfer_to_reserve",
        accepted=True,
        replayed=False,
        state=committed,
        reasons=reasons,
        ledger_entries=ledger,
        details=details,
        receipt=receipt,
    )
