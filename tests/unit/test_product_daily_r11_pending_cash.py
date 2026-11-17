"""Independent pending-cash commitments and public entry regressions for R11."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from quanthus.product_daily import (
    BucketPhase,
    BucketState,
    BuyFeeRule,
    CandidateEntry,
    CandidateProjection,
    CommandEnvelope,
    CycleSettlementEvidence,
    EntryLimits,
    ExpertEntryAction,
    ExpertEntryEligibility,
    FundingState,
    PendingBuy,
    ProductDailyError,
    QuantityLimits,
    QuantityRequest,
    ReasonCode,
    RiskSnapshot,
    SettlementRequest,
    decide_entry,
    entry_invocation_payload,
    evaluate_quantity,
    pending_cash_commitment,
    settle_batch,
    settle_invocation_payload,
)

ROOT = Path(__file__).resolve().parents[2]
CASH_GOLDENS = ROOT / "config" / "research" / "r11" / "continuation-001" / "cash-goldens.v1.json"
NOW = datetime(2026, 11, 17, 8, tzinfo=timezone.utc)


def _dec(value: str | int | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _pending(raw: dict[str, Any]) -> PendingBuy:
    def optional(name: str) -> Decimal | None:
        value = raw[name]
        return None if value is None else _dec(value)

    return PendingBuy(
        reservation_id=raw["reservation_id"],
        security_code=raw["security_code"],
        level1_industry_id=raw["industry"],
        remaining_quantity=raw["remaining_quantity"],
        mark_price=optional("mark"),
        price_bound=optional("price_bound"),
        unrecognized_fee_upper_bound=optional("unrecognized_fee_upper_bound"),
        fee_already_recognized=raw["fee_already_recognized"],
    )


def _case_snapshot(raw: dict[str, Any]) -> RiskSnapshot:
    return RiskSnapshot(
        snapshot_id=raw["id"],
        as_of=NOW,
        equity=_dec(raw["economic_equity"]),
        cash_available=_dec(raw["cash_before_internal_holds"]),
        pending_buys=tuple(_pending(item) for item in raw["pending_buys"]),
        recognized_payables=_dec(raw.get("recognized_payables", "0")),
    )


def _case_request(raw: dict[str, Any], snapshot_id: str) -> QuantityRequest:
    stock, industry, total = raw["limits"]
    return QuantityRequest(
        decision_snapshot_id=snapshot_id,
        bucket_budget=_dec(raw["budget"]),
        fraction=_dec(raw["fraction"]),
        security_code="SYN-CANDIDATE",
        level1_industry_id="IND1",
        mark_price=_dec(raw["mark"]),
        price_bound=_dec(raw["price_bound"]),
        lot_size=raw["lot"],
        limits=QuantityLimits(_dec(stock), _dec(industry), _dec(total)),
        fee_rule=BuyFeeRule(_dec(raw["fee_rate"]), _dec(raw["minimum_fee"]), _dec("0.01")),
    )


def test_independent_pending_cash_goldens_from_public_state() -> None:
    raw = json.loads(CASH_GOLDENS.read_text(encoding="utf-8"))
    cases = raw["cases"]
    assert [case["id"] for case in cases] == [f"PC{index:02d}" for index in range(1, 12)]

    for case in cases:
        snapshot = _case_snapshot(case["snapshot"])
        decision = evaluate_quantity(_case_request(case["request"], snapshot.snapshot_id), snapshot)
        expected = case["expected"]
        expected_residual = expected["residual_cash"]
        actual_residual = decision.to_dict()["residual_cash"]

        assert decision.quantity == expected["q"], case["id"]
        assert actual_residual == expected_residual, case["id"]
        assert expected["reason_includes"] in {reason.value for reason in decision.reason_codes}
        assert decision.execution_authorized is False
        assert decision.forced_sale is False

        if expected_residual is None:
            assert decision.pending_principal is None
            assert decision.pending_unrecognized_fee is None
            assert decision.pending_cash_commitment is None
            continue

        principal = sum(
            (
                _dec(item.remaining_quantity) * item.price_bound
                for item in snapshot.pending_buys
                if item.price_bound is not None
            ),
            Decimal("0"),
        )
        unrecognized_fee = sum(
            (
                item.unrecognized_fee_upper_bound or Decimal("0")
                for item in snapshot.pending_buys
                if not item.fee_already_recognized
            ),
            Decimal("0"),
        )
        commitment = pending_cash_commitment(snapshot)
        assert decision.cash_before_internal_holds == snapshot.cash_before_internal_holds
        assert decision.pending_principal == principal == commitment.principal
        assert decision.pending_unrecognized_fee == unrecognized_fee == commitment.unrecognized_fee
        assert decision.pending_cash_commitment == principal + unrecognized_fee == commitment.total
        assert decision.residual_cash == max(
            Decimal("0"), snapshot.cash_before_internal_holds - commitment.total
        )


def _entry_inputs(
    *,
    cash: str = "20000",
    expert_cap: str = ".30",
    snapshot_id: str = "RS-CLEAN",
    pending_buys: tuple[PendingBuy, ...] = (),
) -> tuple[FundingState, dict[str, Any], CommandEnvelope]:
    cash_decimal = _dec(cash)
    state = FundingState(
        buckets=(
            BucketState(
                bucket_id="U1",
                cash=cash_decimal,
                target_notional=_dec("20000"),
                locked_cycle_budget=Decimal("0"),
                cycle_id=None,
                phase=BucketPhase.EMPTY,
            ),
        ),
        reserve=Decimal("0"),
        unallocated_adjustment=Decimal("0"),
        revision=0,
    )
    args: dict[str, Any] = {
        "candidate": CandidateEntry(
            opportunity_id="OP1",
            security_code="SYN001",
            level1_industry_id="IND1",
            projection=CandidateProjection.OBSERVED,
            input_valid=True,
            knowledge_cutoff_valid=True,
            policy_adopted=True,
            expert_entry=ExpertEntryEligibility(
                event_id="OP1",
                security_code="SYN001",
                final_action=ExpertEntryAction.TRIAL_ENTRY,
                source_digest="sha256:" + "1" * 64,
                target_maximum_position=_dec(expert_cap),
            ),
        ),
        "snapshot": RiskSnapshot(
            snapshot_id=snapshot_id,
            as_of=NOW,
            equity=cash_decimal,
            cash_available=cash_decimal,
            pending_buys=pending_buys,
        ),
        "empty_bucket_ids": ("U1",),
        "active_or_reserved_security_codes": (),
        "active_industry_counts": (),
        "new_buckets_today": 0,
        "limits": EntryLimits(2, 2),
        "fraction": _dec(".30"),
        "mark_price": _dec("10"),
        "price_bound": _dec("10"),
        "lot_size": 100,
        "quantity_limits": QuantityLimits(_dec("1"), _dec("1"), _dec("1")),
        "fee_rule": BuyFeeRule(_dec(".0003"), _dec("5"), _dec(".01")),
    }
    command = CommandEnvelope.create("entry-clean", entry_invocation_payload(**args), 0)
    return state, args, command


def test_pc12_cash_failure_preserves_exact_funding_state_and_expert_intersection() -> None:
    pending = PendingBuy(
        reservation_id="OTHER-HOLD",
        security_code="SYN-OTHER",
        level1_industry_id="IND2",
        remaining_quantity=900,
        mark_price=_dec("10"),
        price_bound=_dec("10"),
        unrecognized_fee_upper_bound=_dec("5"),
    )
    state, args, command = _entry_inputs(
        cash="10000",
        expert_cap=".15",
        snapshot_id="RS-BLOCKED",
        pending_buys=(pending,),
    )
    out = decide_entry(funding=state, command=command, **args)

    assert not out.accepted
    assert out.quantity is not None and out.quantity.quantity == 0
    assert out.quantity.target_cap == _dec("1500")
    assert out.quantity.residual_cash == _dec("995")
    assert ReasonCode.CASH_CAPACITY in out.reason_codes
    assert out.funding_state == state
    assert out.funding_state_changed is False
    assert out.reservation_id is None
    assert out.funding_ledger_entries == ()
    assert out.funding_state.processed_commands == ()
    assert out.funding_state.entry_reservations == ()


def _settle(current: FundingState, command_id: str) -> FundingState:
    closed = replace(
        current,
        buckets=(replace(current.bucket("U1"), phase=BucketPhase.CLOSED),),
        revision=current.revision + 1,
    )
    cycle_id = closed.bucket("U1").cycle_id
    assert cycle_id is not None
    requests = (
        SettlementRequest(
            command_id,
            "U1",
            cycle_id,
            CycleSettlementEvidence(True, True, True, True, True),
        ),
    )
    command = CommandEnvelope.create(
        command_id, settle_invocation_payload(requests), closed.revision
    )
    result = settle_batch(closed, command, requests)
    assert result.accepted
    return result.state


def test_pc13_replay_stays_immutable_and_new_command_uses_released_cash() -> None:
    state, first_args, first_command = _entry_inputs(cash="20000")
    first = decide_entry(funding=state, command=first_command, **first_args)
    assert first.accepted and first.quantity is not None and first.quantity.quantity == 500
    stored = first.funding_state.entry_reservations[0].result

    settled = _settle(first.funding_state, "settle-clean")
    replay = decide_entry(funding=settled, command=first_command, **first_args)
    assert replay.replayed and replay.accepted
    assert replay.quantity == first.quantity
    assert replay.reservation_id == first.reservation_id
    assert replay.result_digest == first.result_digest
    assert replay.funding_state == settled
    assert replay.funding_ledger_entries == ()
    assert settled.entry_reservations[0].result == stored

    blocked_pending = PendingBuy(
        reservation_id="NEW-PENDING",
        security_code="SYN-OTHER",
        level1_industry_id="IND2",
        remaining_quantity=900,
        mark_price=_dec("10"),
        price_bound=_dec("10"),
        unrecognized_fee_upper_bound=_dec("5"),
    )
    blocked_args = {
        **first_args,
        "candidate": replace(
            first_args["candidate"],
            opportunity_id="OP-HOLD",
            security_code="SYN003",
            expert_entry=replace(
                first_args["candidate"].expert_entry,
                event_id="OP-HOLD",
                security_code="SYN003",
                source_digest="sha256:" + "3" * 64,
            ),
        ),
        "snapshot": RiskSnapshot(
            snapshot_id="RS-NEW-HOLD",
            as_of=NOW,
            equity=_dec("10000"),
            cash_available=_dec("10000"),
            pending_buys=(blocked_pending,),
        ),
    }
    blocked_command = CommandEnvelope.create(
        "entry-while-held", entry_invocation_payload(**blocked_args), settled.revision
    )
    blocked = decide_entry(funding=settled, command=blocked_command, **blocked_args)
    assert not blocked.accepted
    assert blocked.quantity is not None and blocked.quantity.quantity == 0
    assert ReasonCode.CASH_CAPACITY in blocked.reason_codes
    assert blocked.funding_state == settled
    assert blocked.funding_ledger_entries == ()

    second_args = {
        **first_args,
        "candidate": replace(
            first_args["candidate"],
            opportunity_id="OP2",
            security_code="SYN002",
            expert_entry=replace(
                first_args["candidate"].expert_entry,
                event_id="OP2",
                security_code="SYN002",
                source_digest="sha256:" + "2" * 64,
            ),
        ),
        "snapshot": RiskSnapshot(
            snapshot_id="RS-RELEASED",
            as_of=NOW,
            equity=_dec("10000"),
            cash_available=_dec("10000"),
        ),
    }
    second_command = CommandEnvelope.create(
        "entry-after-release", entry_invocation_payload(**second_args), settled.revision
    )
    second = decide_entry(funding=settled, command=second_command, **second_args)
    assert second.accepted and second.quantity is not None
    assert second.quantity.quantity == 500
    assert second.quantity.residual_cash == _dec("10000")
    assert second.funding_state.entry_reservations[0] == settled.entry_reservations[0]


def test_pc14_duplicate_pending_reservation_identity_is_rejected() -> None:
    pending = PendingBuy(
        reservation_id="DUPLICATE",
        security_code="SYN-OTHER",
        level1_industry_id="IND2",
        remaining_quantity=100,
        mark_price=_dec("10"),
        price_bound=_dec("10"),
        unrecognized_fee_upper_bound=_dec("5"),
    )
    with pytest.raises(ProductDailyError, match="pending buy ids must be unique"):
        RiskSnapshot(
            snapshot_id="RS-DUP",
            as_of=NOW,
            equity=_dec("10000"),
            cash_available=_dec("10000"),
            pending_buys=(pending, pending),
        )


def test_pc15_legacy_snapshot_shape_keeps_command_identity_and_replay() -> None:
    state, args, _ = _entry_inputs()
    snapshot = args["snapshot"]
    legacy_snapshot = {
        "snapshot_id": snapshot.snapshot_id,
        "as_of": snapshot.as_of.isoformat(),
        "equity": "20000",
        "cash_available": "20000",
        "positions": [],
        "pending_buys": [],
        "pending_sells": [],
        "recognized_payables": "0",
    }
    assert snapshot.to_dict() == legacy_snapshot
    assert snapshot.cash_before_internal_holds == snapshot.cash_available
    legacy_payload = {**entry_invocation_payload(**args), "snapshot": legacy_snapshot}
    legacy_command = CommandEnvelope.create("legacy-issued-entry", legacy_payload, state.revision)
    first = decide_entry(funding=state, command=legacy_command, **args)
    assert first.accepted and first.quantity is not None
    assert first.quantity.quantity == 500
    original_result = first.funding_state.entry_reservations[0].result
    replay = decide_entry(funding=first.funding_state, command=legacy_command, **args)
    assert replay.replayed and replay.quantity is not None
    assert replay.quantity.quantity == first.quantity.quantity
    assert replay.digest == first.digest
    assert replay.funding_ledger_entries == ()
    assert replay.funding_state.entry_reservations[0].result == original_result
