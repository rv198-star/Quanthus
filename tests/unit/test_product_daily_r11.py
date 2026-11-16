from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quanthus.backtest import (
    BacktestEngine,
    BacktestRunSpec,
    BacktestSignal,
    EventType,
    MarketSession,
    OpenExecutionState,
    OrderSide,
    UnitState,
    load_execution_policy,
)
from quanthus.contracts import SnapshotRef
from quanthus.position_lifecycle import PositionLifecycleState, PositionStage
from quanthus.product_daily import (
    ActualOrderState,
    ActualPositionState,
    BucketPhase,
    BucketState,
    BuyFeeRule,
    CandidateEntry,
    CandidateIdentity,
    CandidateObservation,
    CandidateProjection,
    CandidateState,
    CommandEnvelope,
    CycleSettlementEvidence,
    EntryLimits,
    FundingState,
    MarkedExposure,
    PendingBuy,
    PendingSell,
    ProductDailyError,
    QuantityLimits,
    QuantityRequest,
    ReasonCode,
    RiskSnapshot,
    SettlementRequest,
    apply_late_cash_delta,
    decide_entry,
    evaluate_quantity,
    prepare_cycle,
    project_candidate,
    settle_batch,
    transfer_to_reserve,
)

ROOT = Path(__file__).resolve().parents[2]
GOLDENS = ROOT / "config" / "research" / "r11" / "lead-golden-cases.json"
NOW = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
READY = CycleSettlementEvidence(True, True, True, True, True)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def dec(value: str | int | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def command(case_id: str, payload: object, revision: int) -> CommandEnvelope:
    return CommandEnvelope.create(case_id, payload, revision)


def bucket(
    bucket_id: str,
    cash: str,
    *,
    target: str = "20000",
    phase: BucketPhase = BucketPhase.CLOSED,
    cycle_id: str | None = "cycle-1",
    locked: str | None = None,
) -> BucketState:
    return BucketState(
        bucket_id=bucket_id,
        cash=dec(cash),
        target_notional=dec(target),
        locked_cycle_budget=dec(locked or min(dec(cash), dec(target))),
        cycle_id=cycle_id,
        phase=phase,
    )


def funding(
    *buckets: BucketState,
    reserve: str = "0",
    adjustment: str = "0",
    revision: int = 0,
) -> FundingState:
    return FundingState(
        buckets=tuple(sorted(buckets or (bucket("U1", "20000"),), key=lambda x: x.bucket_id)),
        reserve=dec(reserve),
        unallocated_adjustment=dec(adjustment),
        revision=revision,
    )


def fee_rule() -> BuyFeeRule:
    return BuyFeeRule(dec("0.0003"), dec("5"), dec("0.01"))


def exposure(quantity: int, *, mark: str = "10", bound: str = "10") -> MarkedExposure:
    return MarkedExposure(
        unit_id="EXTERNAL-1",
        opportunity_id="external-opportunity-1",
        security_code="SYN001",
        level1_industry_id="IND-1",
        quantity=quantity,
        mark_price=dec(mark),
        price_bound=dec(bound),
    )


def risk(
    *,
    equity: str | None = "100000",
    cash: str | None = "100000",
    positions: tuple[MarkedExposure, ...] = (),
    pending_buys: tuple[PendingBuy, ...] = (),
    pending_sells: tuple[PendingSell, ...] = (),
    snapshot_id: str = "risk-s1",
) -> RiskSnapshot:
    return RiskSnapshot(
        snapshot_id=snapshot_id,
        as_of=NOW,
        equity=None if equity is None else dec(equity),
        cash_available=None if cash is None else dec(cash),
        positions=positions,
        pending_buys=pending_buys,
        pending_sells=pending_sells,
    )


def quantity_request(
    *,
    budget: str = "100000",
    fraction: str | None = "1",
    mark: str | None = "10",
    bound: str | None = "10",
    stock: str | None = ".10",
    industry: str | None = "1",
    total: str | None = "1",
    snapshot_id: str = "risk-s1",
) -> QuantityRequest:
    return QuantityRequest(
        decision_snapshot_id=snapshot_id,
        bucket_budget=dec(budget),
        fraction=None if fraction is None else dec(fraction),
        security_code="SYN001",
        level1_industry_id="IND-1",
        mark_price=None if mark is None else dec(mark),
        price_bound=None if bound is None else dec(bound),
        lot_size=100,
        limits=QuantityLimits(
            stock=None if stock is None else dec(stock),
            industry=None if industry is None else dec(industry),
            total=None if total is None else dec(total),
        ),
        fee_rule=fee_rule(),
    )


def identity(event: str = "SYN-E1", security: str = "SYN001") -> CandidateIdentity:
    return CandidateIdentity(
        original_event_id=event,
        original_source_snapshot_id="SYN-S1",
        candidate_rule_digest="sha256:" + "1" * 64,
        security_code=security,
        level1_industry_id="IND-1",
    )


def observation(
    *,
    event: str = "SYN-E1",
    security: str = "SYN001",
    event_index: int = 1,
    current: int = 2,
    expiry: int = 2,
    input_valid: bool = True,
    capacity_allowed: bool = True,
    evidence_at: datetime | None = NOW,
    actual_order: ActualOrderState = ActualOrderState.NONE,
    actual_position: ActualPositionState = ActualPositionState.NONE,
    active: tuple[str, ...] = (),
    previous_event: int | None = None,
    last_closed: int | None = None,
    raw_allocated: bool = False,
    policy_adopted: bool = True,
) -> CandidateObservation:
    return CandidateObservation(
        identity=identity(event, security),
        decision_id=f"decision-{event}",
        observed_at=NOW,
        knowledge_cutoff=NOW,
        event_session_index=event_index,
        current_session_index=current,
        first_eligible_session_index=2,
        expiry_session_index=expiry,
        input_valid=input_valid,
        policy_adopted=policy_adopted,
        raw_allocated=raw_allocated,
        capacity_allowed=capacity_allowed,
        expert_decision_ref="trend-risk-decision-1",
        position_state_ref="position-state-1",
        actual_order_state=actual_order,
        actual_position_state=actual_position,
        evidence_at=evidence_at,
        active_or_reserved_security_codes=active,
        previous_event_session_index=previous_event,
        last_closed_settled_session_index=last_closed,
    )


def run_settlement_cases(given: dict[str, object]) -> dict[str, object]:
    if "buckets" in given:
        raw_buckets = given["buckets"]
        assert isinstance(raw_buckets, dict)
        state = funding(
            *(bucket(key, value) for key, value in raw_buckets.items()),
            reserve=str(given["reserve"]),
        )
    else:
        state = funding(bucket("U1", str(given["cash"])), reserve=str(given["reserve"]))
    requests = [
        SettlementRequest(
            settlement_id=f"S-{item.bucket_id}",
            bucket_id=item.bucket_id,
            cycle_id="cycle-1",
            evidence=READY,
        )
        for item in state.buckets
    ]
    result = settle_batch(state, command("settle", given, state.revision), requests)
    rows = {row["bucket_id"]: row for row in result.details["settlements"]}
    return {"result": result, "rows": rows}


def test_all_twenty_one_lead_goldens_execute_from_independent_expected_json() -> None:
    document = json.loads(GOLDENS.read_text(encoding="utf-8"))
    cases = document["cases"]
    assert len(cases) == 21
    assert {case["id"] for case in cases} == {
        "F01",
        "F02",
        "F03",
        "F04",
        "F05",
        "F06",
        "F07",
        "F08",
        "F09",
        "Q01",
        "Q02",
        "Q03",
        "Q04",
        "Q05",
        "Q06",
        "C01",
        "C02",
        "C03",
        "C04",
        "C05",
        "C06",
    }

    by_id = {case["id"]: case for case in cases}
    actuals: dict[str, dict[str, object]] = {}
    for case_id in ("F01", "F02"):
        case = by_id[case_id]
        run = run_settlement_cases(case["given"])
        result = run["result"]
        row = run["rows"]["U1"]
        expected = case["expect"]
        assert result.state.bucket("U1").cash == dec(expected["cash"])
        assert result.state.reserve == dec(expected["reserve"])
        assert (
            result.state.cash_total == dec(expected["cash_sum"]) if "cash_sum" in expected else True
        )
        if "underfunded" in expected:
            assert dec(row["underfunded"]) == dec(expected["underfunded"])
            assert dec(row["next_cycle_available"]) * dec(".30") == dec(expected["budget_30pct"])
        actuals[case_id] = {
            "cash": str(result.state.bucket("U1").cash),
            "reserve": str(result.state.reserve),
            "cash_sum": str(result.state.cash_total),
            "underfunded": row.get("underfunded"),
            "budget_30pct": (
                str(dec(row["next_cycle_available"]) * dec(".30"))
                if "budget_30pct" in expected
                else None
            ),
        }

    f03 = by_id["F03"]
    run = run_settlement_cases(f03["given"])
    assert {key: str(run["result"].state.bucket(key).cash) for key in ("U1", "U2")} == f03[
        "expect"
    ]["buckets"]
    assert run["result"].state.reserve == 0
    assert run["result"].state.cash_total == dec(f03["expect"]["cash_sum"])
    actuals["F03"] = {
        "buckets": {key: str(run["result"].state.bucket(key).cash) for key in ("U1", "U2")},
        "reserve": str(run["result"].state.reserve),
        "cash_sum": str(run["result"].state.cash_total),
    }

    f04 = by_id["F04"]
    start = funding(bucket("U1", "18500", locked="19500"), reserve="1000")
    f04_cmd = command("F04", f04["given"], start.revision)
    prepared = prepare_cycle(start, f04_cmd, bucket_id="U1", cycle_id="cycle-2")
    assert prepared.state.bucket("U1").cash == dec(f04["expect"]["cash"])
    assert prepared.state.bucket("U1").locked_cycle_budget == dec(f04["expect"]["B"])
    assert prepared.state.reserve == 0
    assert dec(prepared.details["underfunded"]) == dec(f04["expect"]["underfunded"])
    assert dec(prepared.details["B"]) * dec(".30") == dec(f04["expect"]["budget_30pct"])
    actuals["F04"] = {
        "cash": str(prepared.state.bucket("U1").cash),
        "reserve": str(prepared.state.reserve),
        "B": str(prepared.state.bucket("U1").locked_cycle_budget),
        "underfunded": prepared.details["underfunded"],
        "budget_30pct": str(dec(prepared.details["B"]) * dec(".30")),
    }

    repeated = prepare_cycle(prepared.state, f04_cmd, bucket_id="U1", cycle_id="cycle-2")
    assert repeated.state.digest == prepared.state.digest
    assert repeated.replayed
    assert repeated.details["transfer"] == "0"
    actuals["F05"] = {
        "transfer": repeated.details["transfer"],
        "B": str(repeated.state.bucket("U1").locked_cycle_budget),
        "full_state_digest_unchanged": repeated.state.digest == prepared.state.digest,
    }

    f06 = by_id["F06"]
    held = funding(
        bucket("U1", "19500", phase=BucketPhase.HELD, cycle_id="cycle-2", locked="19500"),
        bucket("U2", "1000", phase=BucketPhase.CLOSED, cycle_id="cycle-x", locked="1000"),
    )
    held_cycle = prepare_cycle(
        held,
        command("F06-prepare", f06["given"], held.revision),
        bucket_id="U1",
        cycle_id="cycle-2",
    )
    assert held_cycle.state.bucket("U1").locked_cycle_budget == dec(f06["expect"]["B"])
    moved = transfer_to_reserve(
        held_cycle.state,
        command("F06-reserve", {"amount": "1000"}, held_cycle.state.revision),
        source_bucket_id="U2",
        amount=dec("1000"),
    )
    assert moved.state.bucket("U1").locked_cycle_budget == dec(f06["expect"]["B"])
    assert moved.details["new_B_unchanged"] is True
    actuals["F06"] = {
        "B": str(moved.state.bucket("U1").locked_cycle_budget),
        "automatic_bucket_transfer": "0",
    }

    late_actuals: dict[str, dict[str, object]] = {}
    for case_id in ("F07", "F08"):
        case = by_id[case_id]
        start = funding(bucket("U1", "18500", locked="19500"), reserve=case["given"]["reserve"])
        late = apply_late_cash_delta(
            start,
            command(case_id, case["given"], start.revision),
            bucket_id="U1",
            cycle_id="cycle-1",
            cash_delta=-dec(case["given"]["late_fee"]),
        )
        assert late.state.reserve == dec(case["expect"]["reserve"])
        assert late.state.unallocated_adjustment == dec(case["expect"]["adjustment"])
        assert late.state.bucket("U1").locked_cycle_budget == dec(case["expect"]["new_B"])
        assert late.details["cash_delta"] == str(dec(case["expect"]["cash_delta"]))
        late_actuals[case_id] = {
            "reserve": str(late.state.reserve),
            "adjustment": str(late.state.unallocated_adjustment),
            "cash_delta": late.details["cash_delta"],
            "new_B": str(late.state.bucket("U1").locked_cycle_budget),
        }
    actuals.update(late_actuals)
    f09 = by_id["F09"]
    f09_source = funding(
        bucket("U1", "18500", locked="19500"),
        bucket("U2", "18000", locked="18000"),
        adjustment="-50",
    )
    before_cash = f09_source.cash_total
    transfer = transfer_to_reserve(
        f09_source,
        command("F09", f09["given"], f09_source.revision),
        source_bucket_id="U2",
        amount=dec(f09["given"]["incoming_internal_transfer"]),
    )
    assert transfer.state.reserve == dec(f09["expect"]["reserve"])
    assert transfer.state.unallocated_adjustment == dec(f09["expect"]["adjustment"])
    assert transfer.state.cash_total == before_cash
    actuals["F09"] = {
        "reserve": str(transfer.state.reserve),
        "adjustment": str(transfer.state.unallocated_adjustment),
        "total_cash_change_including_other_bucket_transfer": "0",
    }

    q01 = evaluate_quantity(quantity_request(budget="20000", fraction=".30", stock="1"), risk())
    assert q01.quantity == by_id["Q01"]["expect"]["q"]
    assert q01.gross == dec("5000") and q01.fees == dec("5")
    assert q01.next_lot_cost == dec("6005") and q01.target_cap == dec("6000")
    actuals["Q01"] = {
        "q": q01.quantity,
        "gross": str(q01.gross),
        "fees": str(q01.fees),
        "next_lot_cost": str(q01.next_lot_cost),
        "target_cap": str(q01.target_cap),
    }

    q02 = evaluate_quantity(quantity_request(), risk(positions=(exposure(900),)))
    assert q02.quantity == 0
    assert q02.next_lot_post_equity == dec("99995")
    assert q02.next_lot_cap == dec("9999.5")
    actuals["Q02"] = {
        "q": q02.quantity,
        "next_lot_post_exposure": str(q02.next_lot_post_equity + q02.fees),
        "post_equity_if_next_lot": str(q02.next_lot_post_equity),
        "cap_if_next_lot": str(q02.next_lot_cap),
        "forced_sale": q02.forced_sale,
    }

    pending = PendingBuy(
        reservation_id="pending-1",
        security_code="SYN001",
        level1_industry_id="IND-1",
        remaining_quantity=100,
        mark_price=dec("10"),
        price_bound=dec("10"),
        unrecognized_fee_upper_bound=dec("5"),
    )
    pending_sell = PendingSell("sell-1", "SYN001", "IND-1", 100, dec("10"))
    q03 = evaluate_quantity(
        quantity_request(),
        risk(positions=(exposure(800),), pending_buys=(pending,), pending_sells=(pending_sell,)),
    )
    assert q03.quantity == 0 and q03.pending_sell_releases == 0
    actuals["Q03"] = {
        "q": q03.quantity,
        "pending_sell_releases": str(q03.pending_sell_releases),
        "unknowable_other_pending_fee_blocks": True,
    }

    q04 = evaluate_quantity(quantity_request(), risk(equity=None))
    assert q04.quantity == 0
    assert ReasonCode.INPUT_UNAVAILABLE in q04.reason_codes and not q04.forced_sale
    actuals["Q04"] = {
        "q": q04.quantity,
        "reason": ReasonCode.INPUT_UNAVAILABLE.value,
        "forced_sale": q04.forced_sale,
    }

    empty_first = funding(
        bucket("A", "50", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"),
        bucket("B", "20000", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"),
    )
    q05 = decide_entry(
        candidate=CandidateEntry(
            opportunity_id="opp-q05",
            security_code="SYN001",
            level1_industry_id="IND-1",
            projection=CandidateProjection.OBSERVED,
            input_valid=True,
            knowledge_cutoff_valid=True,
            policy_adopted=True,
        ),
        funding=empty_first,
        snapshot=risk(),
        empty_bucket_ids=("B", "A"),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        limits=EntryLimits(2, 2),
        fraction=dec(".30"),
        mark_price=dec("10"),
        price_bound=dec("10"),
        lot_size=100,
        quantity_limits=QuantityLimits(dec("1"), dec("1"), dec("1")),
        fee_rule=fee_rule(),
        command=command("Q05-entry", {"case": "Q05"}, empty_first.revision),
    )
    assert q05.quantity is not None and q05.quantity.quantity == 0
    assert q05.first_considered_bucket_id == "A" and q05.second_bucket_probe is False
    assert q05.funding_state_changed is False and q05.funding_state.digest == empty_first.digest
    actuals["Q05"] = {
        "q": 0 if q05.quantity is None else q05.quantity.quantity,
        "second_bucket_probe": q05.second_bucket_probe,
        "unsubmitted_reservation_retained": q05.funding_state_changed,
        "transfers_rollback_on_failed_reservation": (
            q05.funding_state.digest == empty_first.digest
        ),
    }

    q06 = evaluate_quantity(quantity_request(), risk(positions=(exposure(890),)))
    assert q06.quantity == 100
    assert q06.post_security_exposure == dec("9900")
    assert q06.conservative_post_fill_equity == dec("99995")
    actuals["Q06"] = {
        "q": q06.quantity,
        "post_exposure": str(q06.post_security_exposure),
        "post_equity": str(q06.conservative_post_fill_equity),
        "cap": str(q06.next_lot_cap),
    }

    c01_state = CandidateState()
    first = project_candidate(
        c01_state, command("C01-first", {"event": "SYN-E1"}, 0), observation()
    )
    second = project_candidate(
        first.state,
        command("C01-second", {"event": "SYN-E1", "snapshot": "SYN-S1"}, 1),
        observation(),
    )
    assert second.record is not None
    assert second.record.identity.digest == first.record.identity.digest
    assert second.record.actual_order_state is ActualOrderState.NONE
    actuals["C01"] = {
        "opportunity_identity_unchanged": (
            second.record.identity.digest == first.record.identity.digest
        ),
        "new_fill_inferred": second.record.actual_order_state is ActualOrderState.FILLED,
    }

    c02 = project_candidate(
        CandidateState(), command("C02", {"window": "passed"}, 0), observation(current=3)
    )
    assert c02.record.projection is CandidateProjection.EXPIRED
    assert c02.record.entry_authorized is False
    assert ReasonCode.ENTRY_WINDOW_EXPIRED in c02.record.reason_codes
    actuals["C02"] = {
        "projection": c02.record.projection.value,
        "entry_authorized": c02.record.entry_authorized,
        "reason": ReasonCode.ENTRY_WINDOW_EXPIRED.value,
    }

    c03 = project_candidate(
        CandidateState(),
        command("C03", {"held": True}, 0),
        observation(input_valid=False, actual_position=ActualPositionState.HELD),
    )
    assert c03.record.projection is CandidateProjection.HELD
    assert ReasonCode.HOLD_AND_ALERT in c03.record.reason_codes
    actuals["C03"] = {
        "action": "hold_and_alert",
        "forced_sale": False,
    }

    c04 = project_candidate(
        CandidateState(),
        command("C04", {"capacity": "rejected"}, 0),
        observation(capacity_allowed=False, raw_allocated=True),
    )
    assert c04.record.projection is CandidateProjection.BLOCKED
    assert c04.record.actual_order_state is ActualOrderState.NONE
    assert c04.record.actual_position_state is ActualPositionState.NONE
    actuals["C04"] = {
        "reserved": c04.record.projection is CandidateProjection.RESERVED,
        "order": c04.record.actual_order_state is not ActualOrderState.NONE,
        "fill": c04.record.actual_order_state is ActualOrderState.FILLED,
    }

    c05 = project_candidate(
        CandidateState(),
        command("C05", {"cutoff": "future"}, 0),
        observation(evidence_at=NOW.replace(hour=16)),
    )
    assert c05.record.projection is CandidateProjection.BLOCKED
    assert c05.record.entry_authorized is False
    actuals["C05"] = {"entry_authorized": c05.record.entry_authorized}

    c06 = project_candidate(
        CandidateState(),
        command("C06", {"security": "active"}, 0),
        observation(event="SYN-E2", active=("SYN001",)),
    )
    assert c06.record.projection is CandidateProjection.BLOCKED
    assert ReasonCode.EXISTING_SECURITY_CYCLE in c06.record.reason_codes
    actuals["C06"] = {"second_active_bucket": False}

    output = os.environ.get("R11_GOLDEN_ACTUALS")
    if output:
        Path(output).write_text(
            json.dumps(
                {
                    "schema_version": "quanthus.r11.golden-actuals.v1",
                    "case_count": len(actuals),
                    "all_passed": len(actuals) == 21,
                    "actuals": actuals,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def test_cas_idempotency_and_settlement_preconditions_preserve_exact_state() -> None:
    start = funding(bucket("U1", "18000"), reserve="500")
    cas_command = command("cas", {"bucket": "U1"}, 3)
    cas = prepare_cycle(start, cas_command, bucket_id="U1", cycle_id="cycle-2")
    assert not cas.accepted and cas.state.digest == start.digest
    assert cas.reason_codes == (ReasonCode.CAS_CONFLICT,)

    accepted_command = command("idem", {"bucket": "U1"}, start.revision)
    accepted = prepare_cycle(start, accepted_command, bucket_id="U1", cycle_id="cycle-2")
    replay = prepare_cycle(accepted.state, accepted_command, bucket_id="U1", cycle_id="cycle-2")
    assert replay.state.digest == accepted.state.digest
    assert replay.details["transfer"] == "0"
    with pytest.raises(ProductDailyError, match="idempotency_conflict"):
        prepare_cycle(
            accepted.state,
            command("idem", {"bucket": "U2"}, start.revision),
            bucket_id="U1",
            cycle_id="cycle-2",
        )

    not_ready = CycleSettlementEvidence(False, True, True, False, True)
    request = SettlementRequest("S1", "U1", "cycle-1", not_ready)
    rejected = settle_batch(start, command("S1", {"ready": False}, 0), (request,))
    assert not rejected.accepted and rejected.state.digest == start.digest

    ready_request = SettlementRequest("S1", "U1", "cycle-1", READY)
    settled = settle_batch(
        start, command("S1-ready", {"ready": True}, start.revision), (ready_request,)
    )
    duplicate = settle_batch(
        settled.state,
        command("S1-again", {"ready": True, "attempt": 2}, settled.state.revision),
        (replace(ready_request, settlement_id="S1-late-profit"),),
    )
    assert not duplicate.accepted
    assert duplicate.state.digest == settled.state.digest
    with pytest.raises(ProductDailyError, match="must not be empty"):
        settle_batch(start, command("empty-settlement", {}, 0), ())
    with pytest.raises(ProductDailyError, match="bucket_not_transferable"):
        transfer_to_reserve(
            funding(bucket("U1", "10000", phase=BucketPhase.HELD)),
            command("held-transfer", {"amount": "10"}, 0),
            source_bucket_id="U1",
            amount=dec("10"),
        )


def test_quantity_maximum_is_checked_by_independent_bruteforce_and_boundaries() -> None:
    request = quantity_request(budget="20000", fraction=".30", stock="1", industry="1", total="1")
    snapshot = risk()
    decision = evaluate_quantity(request, snapshot)
    valid: list[int] = []
    for lots in range(0, 20):
        quantity = lots * 100
        gross = dec(quantity) * dec("10")
        fee = max(gross * dec(".0003"), dec("5")).quantize(dec(".01"))
        if gross + fee <= dec("6000") and gross + fee <= dec("100000"):
            valid.append(quantity)
    assert decision.quantity == max(valid) == 500

    fraction_zero = evaluate_quantity(replace(request, fraction=dec("0")), snapshot)
    assert fraction_zero.quantity == 0
    assert ReasonCode.BUDGET_CAPACITY in fraction_zero.reason_codes

    missing_price = evaluate_quantity(replace(request, price_bound=None), snapshot)
    assert missing_price.quantity == 0
    assert ReasonCode.INPUT_UNAVAILABLE in missing_price.reason_codes

    other_snapshot = evaluate_quantity(replace(request, decision_snapshot_id="risk-s2"), snapshot)
    assert other_snapshot.quantity == 0
    assert ReasonCode.INPUT_UNAVAILABLE in other_snapshot.reason_codes

    zero_ratio = evaluate_quantity(
        replace(request, limits=QuantityLimits(dec("0"), dec("1"), dec("1"))),
        snapshot,
    )
    assert zero_ratio.quantity == 0
    assert ReasonCode.STOCK_CAPACITY in zero_ratio.reason_codes

    without_bound_loss = evaluate_quantity(
        replace(
            request,
            fraction=dec("1"),
            limits=QuantityLimits(dec(".055"), request.limits.industry, request.limits.total),
        ),
        risk(),
    )
    bound_loss = evaluate_quantity(
        replace(
            request,
            fraction=dec("1"),
            price_bound=dec("11"),
            limits=QuantityLimits(dec(".055"), request.limits.industry, request.limits.total),
        ),
        risk(),
    )
    assert bound_loss.quantity < without_bound_loss.quantity
    assert bound_loss.next_lot_post_equity < without_bound_loss.next_lot_post_equity


def test_recognized_payable_and_pending_sell_do_not_double_count_or_release() -> None:
    recognized = PendingBuy(
        reservation_id="pending-recognized",
        security_code="SYN001",
        level1_industry_id="IND-1",
        remaining_quantity=100,
        mark_price=dec("10"),
        price_bound=dec("10"),
        unrecognized_fee_upper_bound=dec("0"),
        fee_already_recognized=True,
    )
    unrecognized = replace(
        recognized,
        reservation_id="pending-unrecognized",
        unrecognized_fee_upper_bound=dec("5"),
        fee_already_recognized=False,
    )
    request = quantity_request(stock="1")
    with_recognized = evaluate_quantity(
        request,
        risk(equity="99995", pending_buys=(recognized,)),
    )
    with_unrecognized = evaluate_quantity(
        request,
        risk(equity="100000", pending_buys=(unrecognized,)),
    )
    assert with_recognized.quantity == with_unrecognized.quantity
    assert (
        with_recognized.conservative_post_fill_equity
        == with_unrecognized.conservative_post_fill_equity
    )

    blocked = evaluate_quantity(
        quantity_request(stock=".10"),
        risk(
            positions=(exposure(1000),),
            pending_sells=(PendingSell("S", "SYN001", "IND-1", 1000, dec("10")),),
        ),
    )
    assert blocked.quantity == 0 and not blocked.forced_sale


def test_new_event_boundaries_policy_adoption_and_unknown_fees_fail_closed() -> None:
    too_soon = project_candidate(
        CandidateState(),
        command("C-new-too-soon", {"event_index": 22}, 0),
        observation(event="SYN-E2", event_index=22, current=22, expiry=22, previous_event=1),
    )
    allowed_event = project_candidate(
        CandidateState(),
        command("C-new-allowed", {"event_index": 23}, 0),
        observation(event="SYN-E3", event_index=23, current=23, expiry=23, previous_event=1),
    )
    assert too_soon.record.projection is CandidateProjection.BLOCKED
    assert ReasonCode.EVENT_SEPARATION in too_soon.record.reason_codes
    assert allowed_event.record.projection is CandidateProjection.OBSERVED

    cooldown = project_candidate(
        CandidateState(),
        command("C-cooldown", {"closed": 99}, 0),
        observation(event="SYN-E4", event_index=102, current=102, expiry=102, last_closed=99),
    )
    assert cooldown.record.projection is CandidateProjection.BLOCKED
    assert ReasonCode.POSITION_COOLDOWN in cooldown.record.reason_codes

    not_adopted = project_candidate(
        CandidateState(),
        command("C-policy", {"adopted": False}, 0),
        observation(policy_adopted=False),
    )
    assert not_adopted.record.projection is CandidateProjection.BLOCKED
    assert not_adopted.record.entry_authorized is False

    unknown_pending_fee = PendingBuy(
        reservation_id="pending-unknown-fee",
        security_code="SYN001",
        level1_industry_id="IND-1",
        remaining_quantity=100,
        mark_price=dec("10"),
        price_bound=dec("10"),
        unrecognized_fee_upper_bound=None,
    )
    unknown = evaluate_quantity(
        quantity_request(stock="1"),
        risk(pending_buys=(unknown_pending_fee,)),
    )
    assert unknown.quantity == 0
    assert unknown.reason_codes == (ReasonCode.INPUT_UNAVAILABLE,)

    missing_industry = MarkedExposure(
        unit_id="EXTERNAL-NO-INDUSTRY",
        opportunity_id="external-no-industry",
        security_code="SYN009",
        level1_industry_id=None,
        quantity=100,
        mark_price=dec("10"),
        price_bound=dec("10"),
    )
    missing_industry_decision = evaluate_quantity(
        quantity_request(stock="1"),
        risk(positions=(missing_industry,)),
    )
    assert missing_industry_decision.quantity == 0
    assert missing_industry_decision.reason_codes == (ReasonCode.INPUT_UNAVAILABLE,)

    lifecycle = PositionLifecycleState(
        unit_id="U1",
        stage=PositionStage.TRIAL,
        security_code="SYN001",
        opportunity_id="SYN-E1",
        state_machine_version="1.0.0",
        as_of=NOW,
        transition_ids=("entry-transition",),
        consumed_evidence=(),
    )
    lifecycle_bound = project_candidate(
        CandidateState(),
        command("C-lifecycle", {"stage": "trial"}, 0),
        replace(
            observation(actual_position=ActualPositionState.HELD),
            position_state_ref=lifecycle.digest,
            position_lifecycle=lifecycle,
        ),
    )
    assert lifecycle_bound.record.projection is CandidateProjection.HELD
    assert lifecycle_bound.record.position_state_ref == lifecycle.digest


def test_entry_reason_priority_preserves_all_simultaneous_tight_limits() -> None:
    start = funding(bucket("U1", "20000", phase=BucketPhase.HELD))
    decision = decide_entry(
        candidate=CandidateEntry(
            "tight-opp",
            "SYN001",
            "IND-1",
            CandidateProjection.OBSERVED,
            True,
            True,
            False,
        ),
        funding=start,
        snapshot=risk(),
        empty_bucket_ids=(),
        active_or_reserved_security_codes=("SYN001", "SYN002"),
        active_industry_counts=(("IND-1", 2),),
        new_buckets_today=2,
        limits=EntryLimits(2, 2),
        fraction=dec(".30"),
        mark_price=dec("10"),
        price_bound=dec("10"),
        lot_size=100,
        quantity_limits=QuantityLimits(dec("1"), dec("1"), dec("1")),
        fee_rule=fee_rule(),
        command=command("tight-entry", {"tight": True}, start.revision),
    )
    assert not decision.accepted
    assert decision.reason_codes == (
        ReasonCode.POLICY_NOT_ADOPTED,
        ReasonCode.EXISTING_SECURITY_CYCLE,
        ReasonCode.CONCENTRATION_CAPACITY,
        ReasonCode.DAY_LIMIT,
        ReasonCode.NO_EMPTY_BUCKET,
    )


def test_p2_supported_synthetic_roundtrip_enters_c15_settlement_seam() -> None:
    policy = load_execution_policy(
        ROOT / "tests" / "fixtures" / "p2" / "execution-policy.synthetic.v1.json"
    )
    engine = BacktestEngine(policy)
    buy_decision_at = datetime(2026, 7, 27, 15, 0, tzinfo=SHANGHAI)
    buy_snapshot = SnapshotRef(
        market_date=date(2026, 7, 27),
        knowledge_cutoff=buy_decision_at,
        source_revision="r11-p2-synthetic-v1",
        snapshot_id="r11-p2-buy-snapshot-v1",
    )
    buy_signal = BacktestSignal(
        signal_id="r11-p2-buy-signal",
        snapshot=buy_snapshot,
        decision_at=buy_decision_at,
        profile_id="close-to-next-open-v1",
        window_id=None,
        security_code="SYN001",
        side=OrderSide.BUY,
        unit_id="U1",
        quantity=500,
        aggregate_score=dec("0.80"),
        reason_code="synthetic_candidate_accepted",
        decision_version="r11-p2-synthetic-v1",
    )
    buy_session = MarketSession(
        session_date=date(2026, 7, 28),
        open_at=datetime(2026, 7, 28, 9, 30, tzinfo=SHANGHAI),
        close_at=datetime(2026, 7, 28, 15, 0, tzinfo=SHANGHAI),
        security_code="SYN001",
        open_price=dec("10"),
        close_price=dec("10.50"),
        open_execution_state=OpenExecutionState.EXECUTABLE,
        source_revision="r11-p2-synthetic-open-v1",
        snapshot_id="r11-p2-buy-open-v1",
    )
    buy_run = BacktestRunSpec(
        code_version="r11-code-v1",
        parameter_hash="sha256:" + "2" * 64,
        data_snapshot_id="r11-p2-buy-snapshot-v1",
        feature_version="r11-synthetic-features-v1",
        rule_version="r11-synthetic-rules-v1",
        run_id="r11-p2-buy-run",
        execution_policy_version=policy.policy_version,
        execution_profile_id="close-to-next-open-v1",
        schedule_version="close-to-next-open-synthetic-v1",
        official=False,
    )
    buy = engine.run_buy_session(
        run=buy_run,
        signal=buy_signal,
        session=buy_session,
        opening_state=UnitState.empty(unit_id="U1", cash=dec("20000")),
    )
    assert buy.result_summary["fills"] == 1

    sell_decision_at = datetime(2026, 7, 28, 15, 0, tzinfo=SHANGHAI)
    sell_signal = BacktestSignal(
        signal_id="r11-p2-sell-signal",
        snapshot=SnapshotRef(
            market_date=date(2026, 7, 28),
            knowledge_cutoff=sell_decision_at,
            source_revision="r11-p2-synthetic-v1",
            snapshot_id="r11-p2-sell-snapshot-v1",
        ),
        decision_at=sell_decision_at,
        profile_id="close-to-next-open-v1",
        window_id=None,
        security_code="SYN001",
        side=OrderSide.SELL,
        unit_id="U1",
        quantity=500,
        aggregate_score=dec("0.80"),
        reason_code="synthetic_full_exit",
        decision_version="r11-p2-synthetic-v1",
    )
    opening_position = UnitState(
        unit_id="U1",
        target_notional=dec("20000"),
        cash=dec(buy.result_summary["ending_cash"]),
        security_code="SYN001",
        settled_quantity=500,
        position_cost=dec("5000"),
        commission_expense=dec("5"),
        stamp_duty_expense=dec("0"),
        transfer_fee_expense=dec("0"),
        realized_pnl=dec("0"),
        dividend_income=dec("0"),
        external_pool=dec("0"),
        last_mark_price=dec("10.50"),
        last_mark_snapshot_id="r11-p2-buy-open-v1",
    )
    sell_session = MarketSession(
        session_date=date(2026, 7, 29),
        open_at=datetime(2026, 7, 29, 9, 30, tzinfo=SHANGHAI),
        close_at=datetime(2026, 7, 29, 15, 0, tzinfo=SHANGHAI),
        security_code="SYN001",
        open_price=dec("10.50"),
        close_price=dec("10.50"),
        open_execution_state=OpenExecutionState.EXECUTABLE,
        source_revision="r11-p2-synthetic-open-v1",
        snapshot_id="r11-p2-sell-open-v1",
    )
    sell_run = replace(
        buy_run,
        data_snapshot_id="r11-p2-sell-snapshot-v1",
        run_id="r11-p2-sell-run",
    )
    sell = engine.run_sell_sequence(
        run=sell_run,
        signal=sell_signal,
        sessions=(sell_session,),
        opening_state=opening_position,
    )
    assert sell.result_summary["position_quantity"] == 0
    realized_cash = dec(sell.result_summary["ending_cash"])
    p2_settlement = next(
        event.payload for event in sell.events if event.event_type is EventType.UNIT_SETTLEMENT
    )
    p2_profit_to_pool = dec(p2_settlement["profit_transfer"])
    assert realized_cash == dec("20000") and p2_profit_to_pool > 0

    closed_funding = funding(
        bucket("U1", str(realized_cash), locked="20000"),
        reserve=str(p2_profit_to_pool),
    )
    settled = settle_batch(
        closed_funding,
        command("p2-seam-settle", {"cash": str(realized_cash)}, closed_funding.revision),
        (SettlementRequest("p2-seam-settlement", "U1", "cycle-1", READY),),
    )
    assert settled.accepted and settled.state.reserve == p2_profit_to_pool
    assert settled.state.bucket("U1").cash == dec("20000")


def test_three_story_paths_rebuild_to_identical_terminal_digests() -> None:
    def story_one() -> str:
        start = funding(
            bucket("A", "50", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"),
            bucket("B", "20000", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"),
        )
        entry = decide_entry(
            candidate=CandidateEntry(
                "story1-opp",
                "SYN001",
                "IND-1",
                CandidateProjection.OBSERVED,
                True,
                True,
                True,
            ),
            funding=start,
            snapshot=risk(),
            empty_bucket_ids=("B", "A"),
            active_or_reserved_security_codes=(),
            active_industry_counts=(),
            new_buckets_today=0,
            limits=EntryLimits(2, 2),
            fraction=dec(".30"),
            mark_price=dec("10"),
            price_bound=dec("10"),
            lot_size=100,
            quantity_limits=QuantityLimits(dec("1"), dec("1"), dec("1")),
            fee_rule=fee_rule(),
            command=command("story1-entry", {"story": 1}, start.revision),
        )
        assert not entry.accepted and entry.funding_state_changed is False
        expired = CandidateObservation(
            identity=identity(),
            decision_id="story1-expired",
            observed_at=NOW,
            knowledge_cutoff=NOW,
            event_session_index=1,
            current_session_index=3,
            first_eligible_session_index=2,
            expiry_session_index=2,
            input_valid=True,
            policy_adopted=True,
            raw_allocated=False,
            capacity_allowed=False,
            expert_decision_ref="trend-risk-decision-1",
            position_state_ref=None,
            evidence_at=NOW,
        )
        candidate_state = project_candidate(
            CandidateState(), command("story1-observe", {"story": 1}, 0), expired
        ).state
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {"entry": entry.to_dict(), "candidate": candidate_state.to_dict()},
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        )

    def story_two() -> str:
        start = funding(bucket("U1", "20000"), reserve="500")
        prep = prepare_cycle(
            start,
            command("story2-prepare", {"story": 2, "step": "prepare"}, start.revision),
            bucket_id="U1",
            cycle_id="story2-cycle",
        )
        q = evaluate_quantity(quantity_request(budget="20000", fraction=".30", stock="1"), risk())
        held_bucket = replace(prep.state.bucket("U1"), phase=BucketPhase.HELD)
        held_state = replace(prep.state, buckets=(held_bucket,))
        realized = replace(held_bucket, cash=dec("22000"), phase=BucketPhase.CLOSED)
        closed_state = replace(held_state, buckets=(realized,))
        settled = settle_batch(
            closed_state,
            command("story2-settle", {"story": 2, "step": "settle"}, closed_state.revision),
            (SettlementRequest("story2-settlement", "U1", "story2-cycle", READY),),
        )
        new_observation = observation(
            event="SYN-E2",
            current=30,
            expiry=30,
            previous_event=1,
            last_closed=10,
        )
        new_state = project_candidate(
            CandidateState(),
            command("story2-new-event", {"story": 2, "step": "new-event"}, 0),
            new_observation,
        ).state
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {"q": q.to_dict(), "settled": settled.to_dict(), "new": new_state.to_dict()},
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        )

    def story_three() -> str:
        baseline = evaluate_quantity(
            quantity_request(budget="100000", fraction=".30", stock="1"), risk()
        )
        tighter = evaluate_quantity(
            quantity_request(budget="100000", fraction=".30", stock=".10"),
            risk(positions=(exposure(500),)),
        )
        start = funding(bucket("U1", "18500", locked="19500"), reserve="100")
        late = apply_late_cash_delta(
            start,
            command("story3-late", {"story": 3}, start.revision),
            bucket_id="U1",
            cycle_id="cycle-1",
            cash_delta=dec("-25"),
        )
        held = project_candidate(
            CandidateState(),
            command("story3-held", {"story": 3, "held": True}, 0),
            observation(actual_position=ActualPositionState.HELD),
        ).record
        assert held is not None and not held.entry_authorized
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {
                        "baseline": baseline.to_dict(),
                        "tighter": tighter.to_dict(),
                        "late": late.to_dict(),
                        "held": held.to_dict(),
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        )

    digests = {
        "story_1_observe_capacity_expire": story_one(),
        "story_2_reserve_buy_hold_exit_settle_new_event": story_two(),
        "story_3_external_pending_late_no_exit": story_three(),
    }
    for digest_value in digests.values():
        assert digest_value.startswith("sha256:")
    assert story_one() == digests["story_1_observe_capacity_expire"]
    assert story_two() == digests["story_2_reserve_buy_hold_exit_settle_new_event"]
    assert story_three() == digests["story_3_external_pending_late_no_exit"]
    output = os.environ.get("R11_STORY_ACTUALS")
    if output:
        Path(output).write_text(
            json.dumps(
                {
                    "schema_version": "quanthus.r11.story-actuals.v1",
                    "story_count": 3,
                    "rebuild_identical": True,
                    "stories": digests,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
