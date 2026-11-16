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

from quanthus.aggregator import GovernedAction
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
from quanthus.expert_protocols import EvidenceKind, ExpertEvidenceRef
from quanthus.expert_strategy import (
    ExpertStrategyRegistry,
    FormalExpertContext,
    FormalStrategyRunner,
)
from quanthus.feature_registry import FeatureCutoffRule, FeatureLineage, FeatureSourceEvidence
from quanthus.label_registry import FeatureFact
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
    ExpertEntryEligibility,
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
    candidate_invocation_payload,
    decide_entry,
    entry_invocation_payload,
    evaluate_quantity,
    late_cash_invocation_payload,
    prepare_cycle,
    prepare_cycle_invocation_payload,
    project_candidate,
    settle_batch,
    settle_invocation_payload,
    transfer_invocation_payload,
    transfer_to_reserve,
)

ROOT = Path(__file__).resolve().parents[2]
GOLDENS = ROOT / "config" / "research" / "r11" / "lead-golden-cases.json"
NOW = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
READY = CycleSettlementEvidence(True, True, True, True, True)
SHANGHAI = ZoneInfo("Asia/Shanghai")
EXPERT_EVENT_ID = "event:r11-entry-seam"
EXPERT_SNAPSHOT_ID = "snapshot:r11-entry-seam"
EXPERT_CUTOFF = datetime(2020, 1, 6, 7, tzinfo=timezone.utc)


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


def _sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _expert_context(*, volatility: Decimal = Decimal("0.20")) -> FormalExpertContext:
    evidence = (
        ExpertEvidenceRef(
            EvidenceKind.SNAPSHOT,
            EXPERT_EVENT_ID,
            "1.0.0",
            _sha(EXPERT_EVENT_ID),
        ),
        ExpertEvidenceRef(
            EvidenceKind.SNAPSHOT,
            f"trend-observation:{EXPERT_EVENT_ID}",
            "1.0.0",
            _sha(f"trend-observation:{EXPERT_EVENT_ID}"),
        ),
    )
    facts = []
    for feature_id, value in (
        ("price.rsi14", Decimal("60")),
        ("risk.realized_volatility20", volatility),
    ):
        source = FeatureSourceEvidence(
            dataset="r11-expert-test",
            column=feature_id,
            dataset_version="1.0.0",
            manifest_id="manifest:r11-expert-test",
            knowledge_cutoff=EXPERT_CUTOFF,
        )
        facts.append(
            FeatureFact(
                security_code="SYN001",
                feature_id=feature_id,
                feature_version="1.0.0",
                lineage=FeatureLineage(
                    feature_id=feature_id,
                    feature_version="1.0.0",
                    definition_digest=_sha(f"definition:{feature_id}"),
                    snapshot_id=EXPERT_SNAPSHOT_ID,
                    market_date=date(2020, 1, 6),
                    knowledge_cutoff=EXPERT_CUTOFF,
                    source_revision="source:r11-expert-test",
                    universe_policy_version="universe:r11-expert-test",
                    sources=(source,),
                    history_sessions=60,
                    cross_section_complete=None,
                    cutoff_rule=FeatureCutoffRule.SNAPSHOT_OR_EARLIER,
                ),
                value=value,
                unavailable_reason=None,
            )
        )
    return FormalExpertContext(
        security_code="SYN001",
        snapshot_id=EXPERT_SNAPSHOT_ID,
        knowledge_cutoff=EXPERT_CUTOFF,
        position_state="empty",
        feature_facts=tuple(facts),
        label_results=(),
        additional_evidence_refs=evidence,
    )


def expert_entry(*, allowed: bool) -> ExpertEntryEligibility:
    registry = ExpertStrategyRegistry(ROOT)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    decision = runner.evaluate_entry(
        event_id=EXPERT_EVENT_ID,
        context=_expert_context(volatility=Decimal("0.20" if allowed else "0.51")),
        trend_state="stable",
        entry_evidence_ready=True,
    )
    assert (decision.action.final_action is GovernedAction.TRIAL_ENTRY) is allowed
    return ExpertEntryEligibility.from_formal_strategy_decision(decision)


def run_synthetic_p2_roundtrip() -> dict[str, object]:
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
    settlement_event = next(
        event.payload for event in sell.events if event.event_type is EventType.UNIT_SETTLEMENT
    )
    return {
        "buy": buy,
        "sell": sell,
        "ending_cash": dec(sell.result_summary["ending_cash"]),
        "profit_transfer": dec(settlement_event["profit_transfer"]),
        "position_quantity": int(sell.result_summary["position_quantity"]),
    }


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
    result = settle_batch(
        state,
        command("settle", settle_invocation_payload(requests), state.revision),
        requests,
    )
    rows = {row["bucket_id"]: row for row in result.details["settlements"]}
    return {"result": result, "rows": rows}


def assert_exact_expected(expected: object, actual: object, path: str = "case") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: actual must be an object"
        assert set(actual) == set(expected), f"{path}: expected/actual keys differ"
        for key in expected:
            assert_exact_expected(expected[key], actual[key], f"{path}.{key}")
        return
    if isinstance(expected, bool):
        assert type(actual) is bool and actual is expected, f"{path}: boolean mismatch"
        return
    if isinstance(expected, int):
        assert type(actual) is int and actual == expected, f"{path}: integer mismatch"
        return
    if isinstance(expected, str):
        assert isinstance(actual, str), f"{path}: string mismatch"
        try:
            expected_decimal = Decimal(expected)
            actual_decimal = Decimal(actual)
        except Exception:
            assert actual == expected, f"{path}: string mismatch"
        else:
            assert actual_decimal == expected_decimal, f"{path}: decimal mismatch"
        return
    assert actual == expected, f"{path}: value mismatch"


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
        actual_case = {
            "cash": str(result.state.bucket("U1").cash),
            "reserve": str(result.state.reserve),
        }
        if "cash_sum" in expected:
            actual_case["cash_sum"] = str(result.state.cash_total)
        if "underfunded" in expected:
            actual_case["underfunded"] = row["underfunded"]
            actual_case["budget_30pct"] = str(dec(row["next_cycle_available"]) * dec(".30"))
        actuals[case_id] = actual_case

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
    f02_result = run_settlement_cases(by_id["F02"]["given"])["result"]
    assert hasattr(f02_result, "state")
    start = replace(f02_result.state, reserve=dec("1000"))
    f04_payload = prepare_cycle_invocation_payload(bucket_id="U1", cycle_id="cycle-2")
    f04_cmd = command("F04", f04_payload, start.revision)
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
        bucket("U2", "1000", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"),
    )
    f06_payload = prepare_cycle_invocation_payload(bucket_id="U1", cycle_id="cycle-2")
    held_cycle = prepare_cycle(
        held,
        command("F06-prepare", f06_payload, held.revision),
        bucket_id="U1",
        cycle_id="cycle-2",
    )
    assert held_cycle.state.bucket("U1").locked_cycle_budget == dec(f06["expect"]["B"])
    transfer_payload = transfer_invocation_payload(source_bucket_id="U2", amount=dec("1000"))
    moved = transfer_to_reserve(
        held_cycle.state,
        command("F06-reserve", transfer_payload, held_cycle.state.revision),
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
        old_settled = funding(
            bucket("U1", "20000", cycle_id="cycle-old", locked="20000"),
            reserve=case["given"]["reserve"],
        )
        settlement_request = SettlementRequest(
            settlement_id=f"S-{case_id}",
            bucket_id="U1",
            cycle_id="cycle-old",
            evidence=READY,
        )
        old_settled = settle_batch(
            old_settled,
            command(
                f"{case_id}-old-settle",
                settle_invocation_payload((settlement_request,)),
                old_settled.revision,
            ),
            (settlement_request,),
        ).state
        new_held = replace(
            old_settled.bucket("U1"),
            cash=dec("18500"),
            cycle_id="cycle-new",
            phase=BucketPhase.HELD,
            locked_cycle_budget=dec("19500"),
        )
        start = replace(old_settled, buckets=(new_held,))
        late_payload = late_cash_invocation_payload(
            bucket_id="U1",
            cycle_id="cycle-old",
            cash_delta=-dec(case["given"]["late_fee"]),
        )
        late = apply_late_cash_delta(
            start,
            command(case_id, late_payload, start.revision),
            bucket_id="U1",
            cycle_id="cycle-old",
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
        bucket("U1", "18500", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"),
        bucket("U2", "18000", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"),
        adjustment="-50",
    )
    before_cash = f09_source.cash_total
    f09_payload = transfer_invocation_payload(
        source_bucket_id="U2", amount=dec(f09["given"]["incoming_internal_transfer"])
    )
    transfer = transfer_to_reserve(
        f09_source,
        command("F09", f09_payload, f09_source.revision),
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
        "next_lot_post_exposure": str(q02.next_lot_post_security_exposure),
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
        "note": "otherpendingbuyfees notalreadyrecognized mustalso reduceEfloor",
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
    q05_candidate = CandidateEntry(
        opportunity_id=EXPERT_EVENT_ID,
        security_code="SYN001",
        level1_industry_id="IND-1",
        projection=CandidateProjection.OBSERVED,
        input_valid=True,
        knowledge_cutoff_valid=True,
        policy_adopted=True,
        expert_entry=expert_entry(allowed=True),
    )
    q05_snapshot = risk()
    q05_limits = EntryLimits(2, 2)
    q05_fraction = dec(".30")
    q05_mark = dec("10")
    q05_bound = dec("10")
    q05_quantity_limits = QuantityLimits(dec("1"), dec("1"), dec("1"))
    q05_fee = fee_rule()
    q05_payload = entry_invocation_payload(
        candidate=q05_candidate,
        snapshot=q05_snapshot,
        empty_bucket_ids=("B", "A"),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        limits=q05_limits,
        fraction=q05_fraction,
        mark_price=q05_mark,
        price_bound=q05_bound,
        lot_size=100,
        quantity_limits=q05_quantity_limits,
        fee_rule=q05_fee,
    )
    q05 = decide_entry(
        candidate=q05_candidate,
        funding=empty_first,
        snapshot=q05_snapshot,
        empty_bucket_ids=("B", "A"),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        limits=q05_limits,
        fraction=q05_fraction,
        mark_price=q05_mark,
        price_bound=q05_bound,
        lot_size=100,
        quantity_limits=q05_quantity_limits,
        fee_rule=q05_fee,
        command=command("Q05-entry", q05_payload, empty_first.revision),
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
    c01_observation = observation()
    c01_payload = candidate_invocation_payload(c01_observation)
    first = project_candidate(c01_state, command("C01-first", c01_payload, 0), c01_observation)
    second = project_candidate(
        first.state,
        command("C01-second", c01_payload, 1),
        c01_observation,
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

    c02_observation = observation(current=3)
    c02 = project_candidate(
        CandidateState(),
        command("C02", candidate_invocation_payload(c02_observation), 0),
        c02_observation,
    )
    assert c02.record.projection is CandidateProjection.EXPIRED
    assert c02.record.entry_authorized is False
    assert ReasonCode.ENTRY_WINDOW_EXPIRED in c02.record.reason_codes
    actuals["C02"] = {
        "projection": c02.record.projection.value,
        "entry_authorized": c02.record.entry_authorized,
        "reason": ReasonCode.ENTRY_WINDOW_EXPIRED.value,
    }

    c03_observation = observation(input_valid=False, actual_position=ActualPositionState.HELD)
    c03 = project_candidate(
        CandidateState(),
        command("C03", candidate_invocation_payload(c03_observation), 0),
        c03_observation,
    )
    assert c03.record.projection is CandidateProjection.HELD
    assert ReasonCode.HOLD_AND_ALERT in c03.record.reason_codes
    actuals["C03"] = {
        "action": "hold_and_alert",
        "forced_sale": False,
    }

    c04_observation = observation(capacity_allowed=False, raw_allocated=True)
    c04 = project_candidate(
        CandidateState(),
        command("C04", candidate_invocation_payload(c04_observation), 0),
        c04_observation,
    )
    assert c04.record.projection is CandidateProjection.BLOCKED
    assert c04.record.actual_order_state is ActualOrderState.NONE
    assert c04.record.actual_position_state is ActualPositionState.NONE
    actuals["C04"] = {
        "reserved": c04.record.projection is CandidateProjection.RESERVED,
        "order": c04.record.actual_order_state is not ActualOrderState.NONE,
        "fill": c04.record.actual_order_state is ActualOrderState.FILLED,
    }

    c05_observation = observation(evidence_at=NOW.replace(hour=16))
    c05 = project_candidate(
        CandidateState(),
        command("C05", candidate_invocation_payload(c05_observation), 0),
        c05_observation,
    )
    assert c05.record.projection is CandidateProjection.BLOCKED
    assert c05.record.entry_authorized is False
    actuals["C05"] = {"entry_authorized": c05.record.entry_authorized}

    c06_observation = observation(event="SYN-E2", active=("SYN001",))
    c06 = project_candidate(
        CandidateState(),
        command("C06", candidate_invocation_payload(c06_observation), 0),
        c06_observation,
    )
    assert c06.record.projection is CandidateProjection.BLOCKED
    assert ReasonCode.EXISTING_SECURITY_CYCLE in c06.record.reason_codes
    actuals["C06"] = {"second_active_bucket": False}

    for case in cases:
        assert case["id"] in actuals
        assert_exact_expected(case["expect"], actuals[case["id"]], case["id"])

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
    start = funding(bucket("U1", "18000", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"))
    prepare_payload = prepare_cycle_invocation_payload(bucket_id="U1", cycle_id="cycle-2")
    cas_command = command("cas", prepare_payload, 3)
    cas = prepare_cycle(start, cas_command, bucket_id="U1", cycle_id="cycle-2")
    assert not cas.accepted and cas.state.digest == start.digest
    assert cas.reason_codes == (ReasonCode.CAS_CONFLICT,)

    accepted_command = command("idem", prepare_payload, start.revision)
    accepted = prepare_cycle(start, accepted_command, bucket_id="U1", cycle_id="cycle-2")
    replay = prepare_cycle(accepted.state, accepted_command, bucket_id="U1", cycle_id="cycle-2")
    assert replay.state.digest == accepted.state.digest
    assert replay.details["transfer"] == "0"
    with pytest.raises(ProductDailyError, match="idempotency_conflict"):
        prepare_cycle(
            accepted.state,
            command(
                "idem",
                prepare_cycle_invocation_payload(bucket_id="U1", cycle_id="cycle-2"),
                start.revision,
            ),
            bucket_id="U1",
            cycle_id="cycle-3",
        )

    settlement_start = funding(bucket("S1", "18000"), reserve="500")
    not_ready = CycleSettlementEvidence(False, True, True, False, True)
    request = SettlementRequest("S1", "S1", "cycle-1", not_ready)
    rejected = settle_batch(
        settlement_start,
        command(
            "S1",
            settle_invocation_payload((request,)),
            settlement_start.revision,
        ),
        (request,),
    )
    assert not rejected.accepted and rejected.state.digest == settlement_start.digest

    ready_request = SettlementRequest("S1", "S1", "cycle-1", READY)
    settled = settle_batch(
        settlement_start,
        command(
            "S1-ready",
            settle_invocation_payload((ready_request,)),
            settlement_start.revision,
        ),
        (ready_request,),
    )
    late_request = replace(ready_request, settlement_id="S1-late-profit")
    duplicate = settle_batch(
        settled.state,
        command(
            "S1-again",
            settle_invocation_payload((late_request,)),
            settled.state.revision,
        ),
        (late_request,),
    )
    assert not duplicate.accepted
    assert duplicate.state.digest == settled.state.digest
    with pytest.raises(ProductDailyError, match="must not be empty"):
        settle_batch(
            settlement_start,
            command("empty-settlement", settle_invocation_payload(()), 0),
            (),
        )
    with pytest.raises(ProductDailyError, match="bucket_not_transferable"):
        held_start = funding(bucket("U1", "10000", phase=BucketPhase.HELD))
        transfer_to_reserve(
            held_start,
            command(
                "held-transfer",
                transfer_invocation_payload(source_bucket_id="U1", amount=dec("10")),
                held_start.revision,
            ),
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
    too_soon_observation = observation(
        event="SYN-E2", event_index=22, current=22, expiry=22, previous_event=1
    )
    too_soon = project_candidate(
        CandidateState(),
        command(
            "C-new-too-soon",
            candidate_invocation_payload(too_soon_observation),
            0,
        ),
        too_soon_observation,
    )
    allowed_observation = observation(
        event="SYN-E3", event_index=23, current=23, expiry=23, previous_event=1
    )
    allowed_event = project_candidate(
        CandidateState(),
        command(
            "C-new-allowed",
            candidate_invocation_payload(allowed_observation),
            0,
        ),
        allowed_observation,
    )
    assert too_soon.record.projection is CandidateProjection.BLOCKED
    assert ReasonCode.EVENT_SEPARATION in too_soon.record.reason_codes
    assert allowed_event.record.projection is CandidateProjection.OBSERVED

    cooldown_observation = observation(
        event="SYN-E4", event_index=102, current=102, expiry=102, last_closed=99
    )
    cooldown = project_candidate(
        CandidateState(),
        command(
            "C-cooldown",
            candidate_invocation_payload(cooldown_observation),
            0,
        ),
        cooldown_observation,
    )
    assert cooldown.record.projection is CandidateProjection.BLOCKED
    assert ReasonCode.POSITION_COOLDOWN in cooldown.record.reason_codes

    not_adopted_observation = observation(policy_adopted=False)
    not_adopted = project_candidate(
        CandidateState(),
        command(
            "C-policy",
            candidate_invocation_payload(not_adopted_observation),
            0,
        ),
        not_adopted_observation,
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
    lifecycle_observation = replace(
        observation(actual_position=ActualPositionState.HELD),
        position_state_ref=lifecycle.digest,
        position_lifecycle=lifecycle,
    )
    lifecycle_bound = project_candidate(
        CandidateState(),
        command(
            "C-lifecycle",
            candidate_invocation_payload(lifecycle_observation),
            0,
        ),
        lifecycle_observation,
    )
    assert lifecycle_bound.record.projection is CandidateProjection.HELD
    assert lifecycle_bound.record.position_state_ref == lifecycle.digest


def test_entry_reason_priority_preserves_all_simultaneous_tight_limits() -> None:
    start = funding(bucket("U1", "20000", phase=BucketPhase.HELD))
    candidate = CandidateEntry(
        EXPERT_EVENT_ID,
        "SYN001",
        "IND-1",
        CandidateProjection.OBSERVED,
        True,
        True,
        False,
        expert_entry=expert_entry(allowed=True),
    )
    snapshot = risk()
    payload = entry_invocation_payload(
        candidate=candidate,
        snapshot=snapshot,
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
    )
    decision = decide_entry(
        candidate=candidate,
        funding=start,
        snapshot=snapshot,
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
        command=command("tight-entry", payload, start.revision),
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
    seam_request = SettlementRequest("p2-seam-settlement", "U1", "cycle-1", READY)
    settled = settle_batch(
        closed_funding,
        command(
            "p2-seam-settle",
            settle_invocation_payload((seam_request,)),
            closed_funding.revision,
        ),
        (seam_request,),
    )
    assert settled.accepted and settled.state.reserve == p2_profit_to_pool
    assert settled.state.bucket("U1").cash == dec("20000")


def test_three_causal_stories_rebuild_intermediate_state_and_receipts() -> None:
    def story_one() -> dict[str, object]:
        start = funding(
            bucket("A", "50", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"),
            bucket("B", "20000", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"),
        )
        candidate = CandidateEntry(
            EXPERT_EVENT_ID,
            "SYN001",
            "IND-1",
            CandidateProjection.OBSERVED,
            True,
            True,
            True,
            expert_entry=expert_entry(allowed=True),
        )
        snapshot = risk()
        empty_ids = ("B", "A")
        limits = EntryLimits(2, 2)
        quantity_limits = QuantityLimits(dec("1"), dec("1"), dec("1"))
        fraction = dec(".30")
        payload = entry_invocation_payload(
            candidate=candidate,
            snapshot=snapshot,
            empty_bucket_ids=empty_ids,
            active_or_reserved_security_codes=(),
            active_industry_counts=(),
            new_buckets_today=0,
            limits=limits,
            fraction=fraction,
            mark_price=dec("10"),
            price_bound=dec("10"),
            lot_size=100,
            quantity_limits=quantity_limits,
            fee_rule=fee_rule(),
        )
        entry = decide_entry(
            candidate=candidate,
            funding=start,
            snapshot=snapshot,
            empty_bucket_ids=empty_ids,
            active_or_reserved_security_codes=(),
            active_industry_counts=(),
            new_buckets_today=0,
            limits=limits,
            fraction=fraction,
            mark_price=dec("10"),
            price_bound=dec("10"),
            lot_size=100,
            quantity_limits=quantity_limits,
            fee_rule=fee_rule(),
            command=command("story1-entry", payload, start.revision),
        )
        assert not entry.accepted and entry.quantity is not None and entry.quantity.quantity == 0
        assert entry.funding_state.digest == start.digest

        expired = observation(
            event=EXPERT_EVENT_ID,
            current=3,
            capacity_allowed=False,
        )
        candidate_transition = project_candidate(
            CandidateState(),
            command("story1-observe", candidate_invocation_payload(expired), 0),
            expired,
        )
        assert candidate_transition.record is not None
        assert candidate_transition.record.projection is CandidateProjection.EXPIRED
        assert not candidate_transition.record.entry_authorized
        return {
            "entry": {
                "accepted": entry.accepted,
                "quantity": entry.quantity.quantity,
                "reasons": [item.value for item in entry.reason_codes],
                "funding_digest": entry.funding_state.digest,
            },
            "candidate": {
                "identity": candidate_transition.record.identity.digest,
                "projection": candidate_transition.record.projection.value,
                "state_digest": candidate_transition.state.digest,
                "receipt": candidate_transition.result_digest,
            },
        }

    def story_two() -> dict[str, object]:
        p2 = run_synthetic_p2_roundtrip()
        ending_cash = p2["ending_cash"]
        profit = p2["profit_transfer"]
        assert isinstance(ending_cash, Decimal) and isinstance(profit, Decimal)
        assert p2["position_quantity"] == 0 and profit > 0

        old_closed = funding(
            bucket("U1", str(ending_cash), cycle_id="story2-old", locked="20000"),
            reserve=str(profit),
        )
        old_request = SettlementRequest("story2-old-settlement", "U1", "story2-old", READY)
        old_settled = settle_batch(
            old_closed,
            command(
                "story2-old-settle",
                settle_invocation_payload((old_request,)),
                old_closed.revision,
            ),
            (old_request,),
        ).state
        prepare_payload = prepare_cycle_invocation_payload(bucket_id="U1", cycle_id="story2-new")
        prepared = prepare_cycle(
            old_settled,
            command("story2-prepare", prepare_payload, old_settled.revision),
            bucket_id="U1",
            cycle_id="story2-new",
        )
        quantity = evaluate_quantity(
            quantity_request(budget="20000", fraction=".30", stock="1"), risk()
        )
        assert quantity.quantity == 500
        held_bucket = replace(prepared.state.bucket("U1"), phase=BucketPhase.HELD)
        held_state = replace(prepared.state, buckets=(held_bucket,))

        new_observation = observation(
            event="SYN-E2",
            event_index=30,
            current=30,
            expiry=30,
            previous_event=1,
            last_closed=10,
        )
        new_candidate = project_candidate(
            CandidateState(),
            command(
                "story2-new-event",
                candidate_invocation_payload(new_observation),
                0,
            ),
            new_observation,
        )
        assert new_candidate.record is not None
        assert new_candidate.record.projection is CandidateProjection.OBSERVED
        return {
            "p2": {
                "buy_fills": p2["buy"].result_summary["fills"],
                "position_quantity": p2["position_quantity"],
                "ending_cash": str(ending_cash),
                "profit_transfer": str(profit),
            },
            "funding": {
                "settled_digest": old_settled.digest,
                "prepared_digest": prepared.state.digest,
                "reserve": str(held_state.reserve),
                "B": str(held_bucket.locked_cycle_budget),
                "receipt": prepared.result_digest,
            },
            "quantity": quantity.quantity,
            "new_event": new_candidate.record.to_dict(),
            "new_event_receipt": new_candidate.result_digest,
        }

    def story_three() -> dict[str, object]:
        old_closed = funding(bucket("U1", "20000", cycle_id="story3-old", locked="20000"))
        old_request = SettlementRequest("story3-old-settle", "U1", "story3-old", READY)
        old_settled = settle_batch(
            old_closed,
            command("story3-old", settle_invocation_payload((old_request,)), old_closed.revision),
            (old_request,),
        ).state
        new_held = replace(
            old_settled.bucket("U1"),
            cash=dec("18500"),
            cycle_id="story3-new",
            phase=BucketPhase.HELD,
            locked_cycle_budget=dec("20000"),
        )
        reused = replace(old_settled, buckets=(new_held,), reserve=dec("100"))
        pending = PendingBuy(
            reservation_id="story3-pending",
            security_code="SYN001",
            level1_industry_id="IND-1",
            remaining_quantity=100,
            mark_price=dec("10"),
            price_bound=dec("10"),
            unrecognized_fee_upper_bound=dec("5"),
        )
        baseline = evaluate_quantity(
            quantity_request(budget="20000", fraction=".30", stock="1"), risk()
        )
        tightened = evaluate_quantity(
            quantity_request(budget="20000", fraction=".30", stock=".10"),
            risk(positions=(exposure(500),), pending_buys=(pending,)),
        )
        assert tightened.quantity < baseline.quantity
        late_payload = late_cash_invocation_payload(
            bucket_id="U1", cycle_id="story3-old", cash_delta=dec("-25")
        )
        late = apply_late_cash_delta(
            reused,
            command("story3-late", late_payload, reused.revision),
            bucket_id="U1",
            cycle_id="story3-old",
            cash_delta=dec("-25"),
        )
        assert late.accepted and late.state.reserve == dec("75")
        assert late.state.bucket("U1").locked_cycle_budget == dec("20000")

        held_observation = observation(
            input_valid=False,
            actual_position=ActualPositionState.HELD,
        )
        held = project_candidate(
            CandidateState(),
            command(
                "story3-held",
                candidate_invocation_payload(held_observation),
                0,
            ),
            held_observation,
        ).record
        assert held is not None and held.projection is CandidateProjection.HELD
        assert not held.entry_authorized
        return {
            "baseline_quantity": baseline.quantity,
            "tightened_quantity": tightened.quantity,
            "pending_buy": pending.reservation_id,
            "late": {
                "reserve": str(late.state.reserve),
                "B": str(late.state.bucket("U1").locked_cycle_budget),
                "state_digest": late.state.digest,
                "receipt": late.result_digest,
            },
            "held": held.to_dict(),
        }

    traces = {
        "story_1_observe_capacity_expire": story_one(),
        "story_2_p2_buy_hold_exit_reserve_new_event": story_two(),
        "story_3_pending_and_late_fee_after_reuse": story_three(),
    }
    rebuilt = {
        "story_1_observe_capacity_expire": story_one(),
        "story_2_p2_buy_hold_exit_reserve_new_event": story_two(),
        "story_3_pending_and_late_fee_after_reuse": story_three(),
    }
    assert rebuilt == traces
    digests = {
        key: "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        for key, value in traces.items()
    }
    output = os.environ.get("R11_STORY_ACTUALS")
    if output:
        Path(output).write_text(
            json.dumps(
                {
                    "schema_version": "quanthus.r11.story-actuals.v2",
                    "story_count": 3,
                    "rebuild_identical": True,
                    "stories": digests,
                    "traces": traces,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def test_r1_old_cycle_late_delta_after_new_cycle_reuse() -> None:
    old = funding(bucket("U1", "20000", cycle_id="old", locked="20000"), reserve="2000")
    old_request = SettlementRequest("S-old", "U1", "old", READY)
    settled = settle_batch(
        old,
        command("settle-old", settle_invocation_payload((old_request,)), old.revision),
        (old_request,),
    ).state
    new_held = replace(settled.bucket("U1"), cycle_id="new", phase=BucketPhase.HELD)
    reused = replace(settled, buckets=(new_held,))
    fee_payload = late_cash_invocation_payload(
        bucket_id="U1", cycle_id="old", cash_delta=dec("-100")
    )
    fee = apply_late_cash_delta(
        reused,
        command("old-fee", fee_payload, reused.revision),
        bucket_id="U1",
        cycle_id="old",
        cash_delta=dec("-100"),
    )
    assert fee.accepted and fee.state.reserve == dec("1900")
    assert fee.state.bucket("U1").cycle_id == "new"
    assert fee.state.bucket("U1").locked_cycle_budget == dec("20000")

    debt_state = replace(
        reused,
        reserve=dec("0"),
        unallocated_adjustment=dec("-50"),
    )
    refund_payload = late_cash_invocation_payload(
        bucket_id="U1", cycle_id="old", cash_delta=dec("80")
    )
    refund = apply_late_cash_delta(
        debt_state,
        command("late-refund", refund_payload, debt_state.revision),
        bucket_id="U1",
        cycle_id="old",
        cash_delta=dec("80"),
    )
    assert refund.state.unallocated_adjustment == 0
    assert refund.state.reserve == dec("30")
    assert refund.state.bucket("U1").locked_cycle_budget == dec("20000")


def test_r2_settlement_profit_offsets_debt_before_loss_topup() -> None:
    start = funding(
        bucket("U1", "22000", cycle_id="profit", locked="20000"),
        bucket("U2", "18000", cycle_id="loss", locked="18000"),
        adjustment="-50",
    )
    requests = (
        SettlementRequest("profit", "U1", "profit", READY),
        SettlementRequest("loss", "U2", "loss", READY),
    )
    result = settle_batch(
        start,
        command("settle-debt-first", settle_invocation_payload(requests), 0),
        requests,
    )
    assert result.accepted
    assert result.state.bucket("U1").cash == dec("20000")
    assert result.state.bucket("U2").cash == dec("19950")
    assert result.state.reserve == 0
    assert result.state.unallocated_adjustment == 0
    assert result.state.cash_total == dec("39950")


def test_r3_unsettled_closed_cycle_cannot_be_reused() -> None:
    start = funding(bucket("U1", "18000", cycle_id="old", locked="18000"), reserve="500")
    payload = prepare_cycle_invocation_payload(bucket_id="U1", cycle_id="new")
    rejected = prepare_cycle(
        start,
        command("premature", payload, start.revision),
        bucket_id="U1",
        cycle_id="new",
    )
    assert not rejected.accepted
    assert rejected.state.digest == start.digest
    assert rejected.reason_codes == (ReasonCode.SETTLEMENT_NOT_READY,)

    request = SettlementRequest("S-old", "U1", "old", READY)
    settled = settle_batch(
        start,
        command("settle-before-new", settle_invocation_payload((request,)), 0),
        (request,),
    ).state
    allowed = prepare_cycle(
        settled,
        command("new-after-settle", payload, settled.revision),
        bucket_id="U1",
        cycle_id="new",
    )
    assert allowed.accepted and allowed.state.bucket("U1").cycle_id == "new"


def test_r4_command_operation_and_business_body_are_bound() -> None:
    start = funding(bucket("U1", "20000", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"))
    envelope_payload = transfer_invocation_payload(source_bucket_id="U1", amount=dec("100"))
    envelope = command("transfer", envelope_payload, start.revision)
    first = transfer_to_reserve(start, envelope, source_bucket_id="U1", amount=dec("100"))
    assert first.accepted
    with pytest.raises(ProductDailyError, match="idempotency_conflict"):
        transfer_to_reserve(first.state, envelope, source_bucket_id="U1", amount=dec("200"))

    with pytest.raises(ProductDailyError, match="idempotency_conflict"):
        settle_batch(
            first.state,
            envelope,
            (SettlementRequest("S", "U1", "cycle-1", READY),),
        )

    observation_one = observation(capacity_allowed=False)
    candidate_command = command("candidate", candidate_invocation_payload(observation_one), 0)
    candidate_first = project_candidate(CandidateState(), candidate_command, observation_one)
    with pytest.raises(ProductDailyError, match="idempotency_conflict"):
        project_candidate(
            candidate_first.state,
            candidate_command,
            replace(observation_one, decision_id="changed", capacity_allowed=True),
        )


def test_r5_transient_blocks_recompute_but_expiry_and_window_are_sticky() -> None:
    blocked_observation = observation(capacity_allowed=False)
    first = project_candidate(
        CandidateState(),
        command("obs-blocked", candidate_invocation_payload(blocked_observation), 0),
        blocked_observation,
    )
    assert first.record is not None
    assert first.record.projection is CandidateProjection.BLOCKED

    eligible_observation = replace(blocked_observation, capacity_allowed=True, decision_id="D2")
    second = project_candidate(
        first.state,
        command(
            "obs-eligible",
            candidate_invocation_payload(eligible_observation),
            first.state.revision,
        ),
        eligible_observation,
    )
    assert second.record is not None
    assert second.record.projection is CandidateProjection.OBSERVED
    assert second.record.entry_authorized

    blocked_again_observation = replace(eligible_observation, input_valid=False, decision_id="D3")
    blocked_again = project_candidate(
        second.state,
        command(
            "obs-blocked-again",
            candidate_invocation_payload(blocked_again_observation),
            second.state.revision,
        ),
        blocked_again_observation,
    )
    assert blocked_again.record is not None
    assert blocked_again.record.projection is CandidateProjection.BLOCKED

    expired_observation = replace(
        eligible_observation,
        decision_id="D4",
        current_session_index=3,
    )
    expired = project_candidate(
        blocked_again.state,
        command(
            "obs-expired",
            candidate_invocation_payload(expired_observation),
            blocked_again.state.revision,
        ),
        expired_observation,
    )
    assert expired.record is not None
    assert expired.record.projection is CandidateProjection.EXPIRED

    rearmed_observation = replace(expired_observation, decision_id="D5", current_session_index=2)
    rearmed = project_candidate(
        expired.state,
        command(
            "obs-rearmed",
            candidate_invocation_payload(rearmed_observation),
            expired.state.revision,
        ),
        rearmed_observation,
    )
    assert rearmed.record is not None
    assert rearmed.record.projection is CandidateProjection.EXPIRED
    assert not rearmed.record.entry_authorized

    extended_observation = replace(rearmed_observation, decision_id="D6", expiry_session_index=3)
    with pytest.raises(ProductDailyError, match="window cannot be rewritten"):
        project_candidate(
            rearmed.state,
            command(
                "obs-window-extension",
                candidate_invocation_payload(extended_observation),
                rearmed.state.revision,
            ),
            extended_observation,
        )


def test_r6_entry_consumes_explicit_original_expert_action() -> None:
    empty = funding(bucket("U1", "20000", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"))
    snapshot = risk()
    limits = EntryLimits(2, 2)
    quantity_limits = QuantityLimits(dec("1"), dec("1"), dec("1"))
    allowed_candidate = CandidateEntry(
        EXPERT_EVENT_ID,
        "SYN001",
        "IND-1",
        CandidateProjection.OBSERVED,
        True,
        True,
        True,
        expert_entry=expert_entry(allowed=True),
    )
    allowed_payload = entry_invocation_payload(
        candidate=allowed_candidate,
        snapshot=snapshot,
        empty_bucket_ids=("U1",),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        limits=limits,
        fraction=dec(".30"),
        mark_price=dec("10"),
        price_bound=dec("10"),
        lot_size=100,
        quantity_limits=quantity_limits,
        fee_rule=fee_rule(),
    )
    allowed = decide_entry(
        candidate=allowed_candidate,
        funding=empty,
        snapshot=snapshot,
        empty_bucket_ids=("U1",),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        limits=limits,
        fraction=dec(".30"),
        mark_price=dec("10"),
        price_bound=dec("10"),
        lot_size=100,
        quantity_limits=quantity_limits,
        fee_rule=fee_rule(),
        command=command("expert-allowed", allowed_payload, 0),
    )
    assert allowed.accepted

    blocked_candidate = replace(allowed_candidate, expert_entry=expert_entry(allowed=False))
    blocked_payload = entry_invocation_payload(
        candidate=blocked_candidate,
        snapshot=snapshot,
        empty_bucket_ids=("U1",),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        limits=limits,
        fraction=dec(".30"),
        mark_price=dec("10"),
        price_bound=dec("10"),
        lot_size=100,
        quantity_limits=quantity_limits,
        fee_rule=fee_rule(),
    )
    blocked = decide_entry(
        candidate=blocked_candidate,
        funding=empty,
        snapshot=snapshot,
        empty_bucket_ids=("U1",),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        limits=limits,
        fraction=dec(".30"),
        mark_price=dec("10"),
        price_bound=dec("10"),
        lot_size=100,
        quantity_limits=quantity_limits,
        fee_rule=fee_rule(),
        command=command("expert-blocked", blocked_payload, 0),
    )
    assert not blocked.accepted and blocked.funding_state.digest == empty.digest
    assert ReasonCode.EXPERT_ENTRY_BLOCKED in blocked.reason_codes

    projection_blocked = replace(
        allowed_candidate,
        projection=CandidateProjection.BLOCKED,
    )
    projection_payload = entry_invocation_payload(
        candidate=projection_blocked,
        snapshot=snapshot,
        empty_bucket_ids=("U1",),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        limits=limits,
        fraction=dec(".30"),
        mark_price=dec("10"),
        price_bound=dec("10"),
        lot_size=100,
        quantity_limits=quantity_limits,
        fee_rule=fee_rule(),
    )
    projection_rejected = decide_entry(
        candidate=projection_blocked,
        funding=empty,
        snapshot=snapshot,
        empty_bucket_ids=("U1",),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        limits=limits,
        fraction=dec(".30"),
        mark_price=dec("10"),
        price_bound=dec("10"),
        lot_size=100,
        quantity_limits=quantity_limits,
        fee_rule=fee_rule(),
        command=command("projection-blocked", projection_payload, 0),
    )
    assert not projection_rejected.accepted
    assert ReasonCode.CANDIDATE_NOT_ELIGIBLE in projection_rejected.reason_codes


def test_r7_composite_entry_replay_is_atomic_and_immutable() -> None:
    empty = funding(bucket("U1", "20000", phase=BucketPhase.EMPTY, cycle_id=None, locked="0"))
    candidate = CandidateEntry(
        EXPERT_EVENT_ID,
        "SYN001",
        "IND-1",
        CandidateProjection.OBSERVED,
        True,
        True,
        True,
        expert_entry=expert_entry(allowed=True),
    )
    snapshot = risk()
    limits = EntryLimits(2, 2)
    quantity_limits = QuantityLimits(dec("1"), dec("1"), dec("1"))
    payload = entry_invocation_payload(
        candidate=candidate,
        snapshot=snapshot,
        empty_bucket_ids=("U1",),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        limits=limits,
        fraction=dec(".30"),
        mark_price=dec("10"),
        price_bound=dec("10"),
        lot_size=100,
        quantity_limits=quantity_limits,
        fee_rule=fee_rule(),
    )
    envelope = command("entry-once", payload, 0)
    kwargs = {
        "candidate": candidate,
        "snapshot": snapshot,
        "empty_bucket_ids": ("U1",),
        "active_or_reserved_security_codes": (),
        "active_industry_counts": (),
        "new_buckets_today": 0,
        "limits": limits,
        "fraction": dec(".30"),
        "mark_price": dec("10"),
        "price_bound": dec("10"),
        "lot_size": 100,
        "quantity_limits": quantity_limits,
        "fee_rule": fee_rule(),
    }
    first = decide_entry(funding=empty, command=envelope, **kwargs)
    assert first.accepted and first.quantity is not None and first.quantity.quantity == 500
    assert len(first.funding_state.entry_reservations) == 1
    assert [item.operation for item in first.funding_state.processed_commands] == ["decide_entry"]
    replay = decide_entry(funding=first.funding_state, command=envelope, **kwargs)
    assert replay.replayed and replay.accepted
    assert replay.quantity is not None and replay.quantity.quantity == first.quantity.quantity
    assert replay.reservation_id == first.reservation_id
    assert replay.result_digest == first.result_digest
    assert replay.funding_state.digest == first.funding_state.digest

    changed_candidate = replace(candidate, security_code="SYN002")
    with pytest.raises(ProductDailyError, match="idempotency_conflict"):
        decide_entry(
            funding=first.funding_state,
            command=envelope,
            **{**kwargs, "candidate": changed_candidate},
        )


def test_r8_nonpositive_equity_is_directly_blocked() -> None:
    for equity in (Decimal("0"), Decimal("-1")):
        decision = evaluate_quantity(
            quantity_request(budget="10000", fraction="1", stock="0"),
            risk(equity=str(equity)),
        )
        assert decision.quantity == 0
        assert decision.reason_codes == (ReasonCode.INPUT_UNAVAILABLE,)


def test_r9_strict_typed_inputs_reject_truthy_and_binary_values() -> None:
    with pytest.raises(ProductDailyError, match="must be a boolean"):
        CycleSettlementEvidence("false", True, True, True, True)  # type: ignore[arg-type]
    with pytest.raises(ProductDailyError, match="must be >= 0.01"):
        PendingBuy(
            reservation_id="zero-price",
            security_code="SYN001",
            level1_industry_id="IND-1",
            remaining_quantity=100,
            mark_price=dec("0"),
            price_bound=dec("0"),
            unrecognized_fee_upper_bound=Decimal("0"),
        )
    with pytest.raises(ProductDailyError, match="must be finite"):
        MarkedExposure(
            unit_id="float",
            opportunity_id="float",
            security_code="SYN001",
            level1_industry_id="IND-1",
            quantity=100,
            mark_price=10.0,  # type: ignore[arg-type]
            price_bound=dec("10"),
        )
    with pytest.raises(ProductDailyError, match="cash quantum"):
        bucket("U1", "100.001", phase=BucketPhase.EMPTY, cycle_id=None, locked="0")
    with pytest.raises(ProductDailyError, match="must be an integer"):
        CandidateObservation(
            identity=identity(),
            decision_id="bool-int",
            observed_at=NOW,
            knowledge_cutoff=NOW,
            event_session_index=True,  # type: ignore[arg-type]
            current_session_index=2,
            first_eligible_session_index=2,
            expiry_session_index=2,
            input_valid=True,
            policy_adopted=True,
            raw_allocated=False,
            capacity_allowed=True,
            expert_decision_ref="expert-ok",
            position_state_ref=None,
            evidence_at=NOW,
        )
    zero_fee = BuyFeeRule(Decimal("0"), Decimal("0"), Decimal("0.01"))
    assert zero_fee.minimum_commission == 0
