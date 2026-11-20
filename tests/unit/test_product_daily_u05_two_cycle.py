"""Original U05 public synthetic two-cycle acceptance story."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.execution import ExecutionReport, SyntheticContext
from quanthus.platform.adapters.product_daily_cycle_sqlite import build_funding_bridge_service
from quanthus.product_daily.application.funding_bridge import (
    CycleBaseline,
    CycleEntryIntent,
    CycleFillProfile,
    LateFeeFact,
    SellExecutionReport,
    SellProfile,
    SyntheticTemporalProfile,
    funding_state_envelope,
)
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization
from quanthus.product_daily.candidate import CandidateProjection
from quanthus.product_daily.contracts import CommandEnvelope
from quanthus.product_daily.funding import BucketPhase, BucketState, FundingState
from quanthus.product_daily.risk import (
    BuyFeeRule,
    CandidateEntry,
    EntryLimits,
    ExpertEntryAction,
    ExpertEntryEligibility,
    QuantityLimits,
    entry_invocation_payload,
    pending_cash_commitment,
)
from quanthus.shared.fact_contracts import FactError, digest

NOW = datetime(2026, 11, 17, 16, tzinfo=timezone.utc)
AUTH = SyntheticRuntimeAuthorization("two-cycle-runtime", frozenset({"SYN-TWOCYCLE"}))
ROOT = Path(__file__).resolve().parents[2]
TEMPORAL_CONFIG = json.loads(
    (ROOT / "config/research/r11/continuation-004/two-cycle-temporal-profile.v2.json").read_text(
        encoding="utf-8"
    )
)
TEMPORAL = SyntheticTemporalProfile(
    TEMPORAL_CONFIG["version"],
    tuple(datetime.fromisoformat(event["received_at"]) for event in TEMPORAL_CONFIG["events"]),
    tuple(event["role"] for event in TEMPORAL_CONFIG["events"]),
    tuple(datetime.fromisoformat(event["occurred_at"]) for event in TEMPORAL_CONFIG["events"]),
    tuple(datetime.fromisoformat(event["decision_at"]) for event in TEMPORAL_CONFIG["events"]),
)
S01_AT = datetime(2026, 11, 17, 16, tzinfo=timezone.utc)
S02_AT = datetime(2026, 11, 18, 1, tzinfo=timezone.utc)
S03_AT = datetime(2026, 11, 19, 1, tzinfo=timezone.utc)
S04_AT = datetime(2026, 11, 19, 2, tzinfo=timezone.utc)
S05_AT = datetime(2026, 11, 19, 17, tzinfo=timezone.utc)
S06_AT = datetime(2026, 11, 20, tzinfo=timezone.utc)
S07_AT = datetime(2026, 11, 20, 1, tzinfo=timezone.utc)
S08_AT = datetime(2026, 11, 23, 1, tzinfo=timezone.utc)
S09_AT = datetime(2026, 11, 23, 2, tzinfo=timezone.utc)


def context(at: datetime = NOW) -> SyntheticContext:
    return SyntheticContext("SYN-TWOCYCLE", "synthetic", "fixture:two-cycle", at, "complete")


def baseline() -> CycleBaseline:
    funding = FundingState(
        (
            BucketState(
                "U1",
                Decimal("20000.00"),
                Decimal("20000.00"),
                Decimal("0.00"),
                None,
                BucketPhase.EMPTY,
            ),
        ),
        Decimal("1000.00"),
        Decimal("0.00"),
        0,
    )
    return CycleBaseline(context(), "two-cycle-baseline", Decimal("21000.00"), funding, "SYN001")


def candidate(opportunity: str, security: str) -> CandidateEntry:
    return CandidateEntry(
        opportunity,
        security,
        "IND1",
        CandidateProjection.OBSERVED,
        True,
        True,
        True,
        ExpertEntryEligibility(
            opportunity,
            security,
            ExpertEntryAction.TRIAL_ENTRY,
            "sha256:" + ("1" if security == "SYN001" else "2") * 64,
            Decimal(".30"),
        ),
    )


def entry_intent(
    service,
    *,
    candidate_entry: CandidateEntry,
    command_id: str,
    expected_revision: int,
    at: datetime,
) -> CycleEntryIntent:
    service_context = context(at)
    snapshot = service.risk_snapshot(service_context, mark_price=Decimal("10"))
    args = {
        "candidate": candidate_entry,
        "snapshot": snapshot,
        "empty_bucket_ids": ("U1",),
        "active_or_reserved_security_codes": (),
        "active_industry_counts": (),
        "new_buckets_today": 0,
        "limits": EntryLimits(2, 2),
        "fraction": Decimal(".30"),
        "mark_price": Decimal("10"),
        "price_bound": Decimal("10"),
        "lot_size": 100,
        "quantity_limits": QuantityLimits(Decimal("1"), Decimal("1"), Decimal("1")),
        "fee_rule": BuyFeeRule(Decimal(".0003"), Decimal("5"), Decimal(".01")),
    }
    command = CommandEnvelope.create(
        command_id, entry_invocation_payload(**args), expected_revision
    )
    return CycleEntryIntent(
        expected_revision,
        command,
        candidate_entry,
        snapshot,
        ("U1",),
        (),
        (),
        0,
        EntryLimits(2, 2),
        Decimal(".30"),
        Decimal("10"),
        Decimal("10"),
        100,
        QuantityLimits(Decimal("1"), Decimal("1"), Decimal("1")),
        BuyFeeRule(Decimal(".0003"), Decimal("5"), Decimal(".01")),
    )


def buy_report(
    report_id: str, security: str, *, at: datetime, source_sequence: str
) -> ExecutionReport:
    return ExecutionReport(
        report_id,
        security,
        "buy",
        500,
        Decimal("10"),
        Decimal("5"),
        at,
        "fixture:two-cycle:broker",
        source_sequence,
        500,
        Decimal("10"),
    )


def buy_profile(source_sequence: str) -> CycleFillProfile:
    return CycleFillProfile(
        "single-fill-terminal-v1", "fixture:two-cycle:broker", 500, 500, 0, Decimal("5"), True
    )


def sell_report(
    report_id: str, security: str, cycle_id: str, price: str, *, at: datetime, source_sequence: str
) -> SellExecutionReport:
    return SellExecutionReport(
        report_id,
        security,
        cycle_id,
        500,
        Decimal(price),
        Decimal("5"),
        at,
        "fixture:two-cycle:broker",
        source_sequence,
    )


def sell_profile(source_sequence: str) -> SellProfile:
    del source_sequence
    return SellProfile(
        "single-fill-terminal-v1", "fixture:two-cycle:broker", 500, 500, 0, Decimal("5"), True
    )


def assert_closure(state) -> None:
    assert state.account.cash == state.funding.cash_total
    assert state.account.revision == state.account_revision


def drive_to_first_flat(service):
    service.establish_baseline(baseline())
    intent = entry_intent(
        service,
        candidate_entry=candidate("OP-FAULT", "SYN001"),
        command_id="fault-entry",
        expected_revision=0,
        at=NOW,
    )
    entry = service.enter(context(), intent)
    assert entry.accepted and entry.quantity == 500
    cycle_id = service.current(context()).funding.bucket("U1").cycle_id
    assert cycle_id is not None
    service.submit_buy(
        context(S02_AT),
        buy_report(
            "fault-buy",
            "SYN001",
            at=datetime(2026, 11, 18, 1, tzinfo=timezone.utc),
            source_sequence="fault-buy",
        ),
        buy_profile("fault-buy"),
    )
    service.submit_sell(
        context(S03_AT),
        sell_report(
            "fault-sell",
            "SYN001",
            cycle_id,
            "12",
            at=datetime(2026, 11, 19, 1, tzinfo=timezone.utc),
            source_sequence="fault-sell",
        ),
        sell_profile("fault-sell"),
    )
    return cycle_id


def test_public_two_cycle_buy_sell_settle_late_fee_and_cold_rebuild(tmp_path: Path) -> None:
    path = tmp_path / "two-cycle.sqlite"
    service = build_funding_bridge_service(
        path, context=context(), authorization=AUTH, temporal_profile=TEMPORAL
    )
    service.establish_baseline(baseline())

    first_intent = entry_intent(
        service,
        candidate_entry=candidate("OP-A", "SYN001"),
        command_id="entry-1",
        expected_revision=0,
        at=datetime(2026, 11, 17, 16, tzinfo=timezone.utc),
    )
    first_entry = service.enter(context(), first_intent)
    assert first_entry.accepted and first_entry.quantity == 500
    first_cycle = service.current(context()).funding.bucket("U1").cycle_id
    assert first_cycle is not None
    actuals = [
        {
            "id": "S01",
            "quantity": first_entry.quantity,
            "cash": str(service.current(context()).account.cash),
            "bucket": str(service.current(context()).funding.bucket("U1").cash),
            "reserve": str(service.current(context()).funding.reserve),
            "B": str(service.current(context()).funding.bucket("U1").locked_cycle_budget),
            "pending": str(
                pending_cash_commitment(
                    service.risk_snapshot(context(), mark_price=Decimal("10"))
                ).total
            ),
        }
    ]

    first_buy = service.submit_buy(
        context(datetime(2026, 11, 18, 1, tzinfo=timezone.utc)),
        buy_report(
            "buy-1",
            "SYN001",
            at=datetime(2026, 11, 18, 1, tzinfo=timezone.utc),
            source_sequence="buy-1",
        ),
        buy_profile("buy-1"),
    )
    assert (first_buy.account.cash, first_buy.account.quantity("SYN001")) == (
        Decimal("15995.00"),
        500,
    )
    actuals.append(
        {
            "id": "S02",
            "cash": str(first_buy.account.cash),
            "bucket": str(first_buy.funding.bucket("U1").cash),
            "reserve": str(first_buy.funding.reserve),
            "quantity": first_buy.account.quantity("SYN001"),
            "pending": "0.00",
            "equity": str(service.risk_snapshot(context(S02_AT), mark_price=Decimal("10")).equity),
        }
    )
    first_sell = service.submit_sell(
        context(datetime(2026, 11, 19, 1, tzinfo=timezone.utc)),
        sell_report(
            "sell-1",
            "SYN001",
            first_cycle,
            "12",
            at=datetime(2026, 11, 19, 1, tzinfo=timezone.utc),
            source_sequence="sell-1",
        ),
        sell_profile("sell-1"),
    )
    assert (first_sell.account.cash, first_sell.account.quantity("SYN001")) == (
        Decimal("21990.00"),
        0,
    )
    assert {item.account: item.amount for item in first_sell.postings} == {
        "ASSET_CASH": Decimal("5995.00"),
        "ASSET_POSITION": Decimal("-5000"),
        "REALIZED_PNL": Decimal("-1000"),
        "EXPENSE_CONFIRMED_FEE": Decimal("5.00"),
    }
    actuals.append(
        {
            "id": "S03",
            "cash": str(first_sell.account.cash),
            "bucket": str(first_sell.funding.bucket("U1").cash),
            "reserve": str(first_sell.funding.reserve),
            "position_quantity": 0,
            "fill_quantity": 500,
        }
    )
    before_sell_alias = service.current(context(S03_AT))
    with pytest.raises(FactError, match="SOURCE_FACT_DUPLICATE"):
        service.submit_sell(
            context(datetime(2026, 11, 19, 2, tzinfo=timezone.utc)),
            replace(
                sell_report(
                    "sell-1",
                    "SYN001",
                    first_cycle,
                    "12",
                    at=datetime(2026, 11, 19, 1, tzinfo=timezone.utc),
                    source_sequence="sell-1",
                ),
                report_id="sell-1-alias",
            ),
            sell_profile("sell-1"),
        )
    assert service.current(context(S04_AT)) == before_sell_alias
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT status FROM bridge_operations WHERE kind='sell' AND identity='sell-1-alias'"
            ).fetchone()[0]
            == "RECEIVED"
        )
        assert (
            db.execute(
                "SELECT state FROM bridge_conflict_register "
                "WHERE scope_key='source:fixture:two-cycle:broker:sell-1'"
            ).fetchone()[0]
            == "RESOLVED_ALIAS"
        )
    first_settlement = service.settle_cycle(
        context(datetime(2026, 11, 19, 2, tzinfo=timezone.utc)),
        settlement_id="settle-1",
        cycle_id=first_cycle,
    )
    assert first_settlement.funding.bucket("U1").cash == Decimal("20000.00")
    assert first_settlement.funding.reserve == Decimal("1990.00")
    assert first_settlement.funding.active_entry_reservations == ()
    actuals.append(
        {
            "id": "S04",
            "cash": str(first_settlement.funding.cash_total),
            "bucket": str(first_settlement.funding.bucket("U1").cash),
            "reserve": str(first_settlement.funding.reserve),
            "active": 0,
        }
    )

    second_intent = entry_intent(
        service,
        candidate_entry=candidate("OP-B", "SYN002"),
        command_id="entry-2",
        expected_revision=service.current(context(S04_AT)).account_revision,
        at=datetime(2026, 11, 19, 17, tzinfo=timezone.utc),
    )
    second_entry = service.enter(
        context(datetime(2026, 11, 19, 17, tzinfo=timezone.utc)), second_intent
    )
    assert second_entry.accepted and second_entry.quantity == 500
    actuals.append(
        {
            "id": "S05",
            "cash": str(service.current(context(S05_AT)).account.cash),
            "bucket": str(service.current(context(S05_AT)).funding.bucket("U1").cash),
            "reserve": str(service.current(context(S05_AT)).funding.reserve),
            "B": str(service.current(context(S05_AT)).funding.bucket("U1").locked_cycle_budget),
            "pending": "5005.00",
            "quantity": 500,
        }
    )
    second_cycle = service.current(context(S05_AT)).funding.bucket("U1").cycle_id
    assert second_cycle is not None and second_cycle != first_cycle
    occupied_before = service.current(context(S05_AT))
    occupied = entry_intent(
        service,
        candidate_entry=candidate("OP-OCCUPIED", "SYN003"),
        command_id="occupied-entry",
        expected_revision=occupied_before.account_revision,
        at=S05_AT,
    )
    occupied_result = service.enter(context(S05_AT), occupied)
    assert not occupied_result.accepted
    assert occupied_result.quantity == 0
    assert service.current(context(S05_AT)) == occupied_before

    late_fact = LateFeeFact(
        "late-fee-1",
        "U1",
        first_cycle,
        Decimal("-100"),
        "fixture:two-cycle:late",
        "late-fee-1",
        datetime(2026, 11, 18, 16, tzinfo=timezone.utc),
    )
    late = service.apply_late_fee(
        context(S06_AT),
        late_fact,
    )
    assert late.account.cash == Decimal("21890.00")
    assert late.funding.reserve == Decimal("1890.00")
    assert late.funding.bucket("U1").locked_cycle_budget == Decimal("20000.00")
    assert len(service.risk_snapshot(context(S06_AT), mark_price=Decimal("10")).pending_buys) == 1
    before_late_alias = service.current(context(S06_AT))
    with pytest.raises(FactError, match="SOURCE_FACT_DUPLICATE"):
        service.apply_late_fee(context(S06_AT), replace(late_fact, fact_id="late-fee-alias"))
    assert service.current(context(S06_AT)) == before_late_alias
    actuals.append(
        {
            "id": "S06",
            "cash": str(late.account.cash),
            "bucket": str(late.funding.bucket("U1").cash),
            "reserve": str(late.funding.reserve),
            "B": str(late.funding.bucket("U1").locked_cycle_budget),
            "pending": "5005.00",
        }
    )

    second_buy = service.submit_buy(
        context(S07_AT),
        buy_report(
            "buy-2",
            "SYN002",
            at=datetime(2026, 11, 20, 1, tzinfo=timezone.utc),
            source_sequence="buy-2",
        ),
        buy_profile("buy-2"),
    )
    assert second_buy.account.cash == Decimal("16885.00")
    assert second_buy.funding.bucket("U1").cash == Decimal("14995.00")
    actuals.append(
        {
            "id": "S07",
            "cash": str(second_buy.account.cash),
            "bucket": str(second_buy.funding.bucket("U1").cash),
            "reserve": str(second_buy.funding.reserve),
            "quantity": 500,
            "pending": "0.00",
            "equity": str(service.risk_snapshot(context(S07_AT), mark_price=Decimal("10")).equity),
        }
    )
    second_sell = service.submit_sell(
        context(S08_AT),
        sell_report(
            "sell-2",
            "SYN002",
            second_cycle,
            "9",
            at=datetime(2026, 11, 23, 1, tzinfo=timezone.utc),
            source_sequence="sell-2",
        ),
        sell_profile("sell-2"),
    )
    assert second_sell.account.cash == Decimal("21380.00")
    assert second_sell.funding.bucket("U1").cash == Decimal("19490.00")
    assert {item.account: item.amount for item in second_sell.postings} == {
        "ASSET_CASH": Decimal("4495.00"),
        "ASSET_POSITION": Decimal("-5000"),
        "REALIZED_PNL": Decimal("500"),
        "EXPENSE_CONFIRMED_FEE": Decimal("5.00"),
    }
    actuals.append(
        {
            "id": "S08",
            "cash": str(second_sell.account.cash),
            "bucket": str(second_sell.funding.bucket("U1").cash),
            "reserve": str(second_sell.funding.reserve),
            "position_quantity": 0,
            "fill_quantity": 500,
        }
    )
    second_settlement = service.settle_cycle(
        context(datetime(2026, 11, 23, 2, tzinfo=timezone.utc)),
        settlement_id="settle-2",
        cycle_id=second_cycle,
    )
    final_at = context(S09_AT)
    final = service.current(final_at)
    assert final.account.cash == final.funding.cash_total == Decimal("21380.00")
    assert final.funding.bucket("U1").cash == Decimal("20000.00")
    assert final.funding.reserve == Decimal("1380.00")
    assert len(final.funding.entry_reservations) == 2
    assert len(final.funding.completed_settlements) == 2
    assert final.funding.active_entry_reservations == ()
    assert final.account.cash - Decimal("21000.00") == Decimal("380.00")
    actuals.append(
        {
            "id": "S09",
            "cash": str(final.account.cash),
            "bucket": str(final.funding.bucket("U1").cash),
            "reserve": str(final.funding.reserve),
            "active": 0,
            "entries": len(final.funding.entry_reservations),
            "settlements": len(final.funding.completed_settlements),
        }
    )
    assert_closure(final)
    staged = funding_state_envelope(final.funding)
    with sqlite3.connect(path) as db:
        assert tuple(
            row[0]
            for row in db.execute("SELECT transition_id FROM bridge_journal ORDER BY sequence")
        ) == (
            "entry:entry-1",
            "buy:buy-1",
            "sell:sell-1",
            "settlement:settle-1",
            "entry:entry-2",
            "late_fee:late-fee-1",
            "buy:buy-2",
            "sell:sell-2",
            "settlement:settle-2",
        )
    reopened = build_funding_bridge_service(
        path, context=context(S09_AT), authorization=AUTH, temporal_profile=TEMPORAL
    )
    rebuilt = reopened.rebuild(context(S09_AT))
    current = reopened.current(context(S09_AT))
    assert rebuilt.account == current.account
    assert rebuilt.account_revision == current.account_revision
    assert rebuilt.funding.to_dict() == current.funding.to_dict()
    assert rebuilt.funding.digest == current.funding.digest
    assert funding_state_envelope(rebuilt.funding) == staged
    assert second_settlement.account_revision == final.account_revision
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM bridge_obligations")
        db.execute("DELETE FROM bridge_lots")
    cacheless = build_funding_bridge_service(
        path, context=context(S09_AT), authorization=AUTH, temporal_profile=TEMPORAL
    )
    cacheless_rebuild = cacheless.rebuild(context(S09_AT))
    assert cacheless_rebuild.account == final.account
    assert cacheless_rebuild.funding.to_dict() == final.funding.to_dict()
    output = os.environ.get("R11_TWO_CYCLE_ACTUALS")
    if output:
        Path(output).write_text(
            json.dumps(
                {
                    "steps": actuals,
                    "final_account_cash": str(final.account.cash),
                    "journal_count": 9,
                },
                indent=2,
                sort_keys=True,
            )
            + chr(10)
        )
    journals = 9
    with sqlite3.connect(path) as db:
        journals = db.execute("SELECT count(*) FROM bridge_journal").fetchone()[0]
    replay_entry = service.enter(context(), first_intent)
    replay_settle = service.settle_cycle(context(), settlement_id="settle-1", cycle_id=first_cycle)
    replay_late = service.apply_late_fee(context(), late_fact)
    assert replay_entry.quantity == 500
    assert replay_settle.funding == first_settlement.funding
    assert replay_settle.decision.ledger_entries == first_settlement.decision.ledger_entries
    assert replay_settle.decision.details_json == first_settlement.decision.details_json
    assert replay_settle.decision.result_digest == first_settlement.decision.result_digest
    assert replay_late.funding == late.funding
    assert replay_late.postings == late.postings
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM bridge_journal").fetchone()[0] == journals


def test_settlement_unknown_and_fault_rollback_retry(tmp_path: Path) -> None:
    unknown_path = tmp_path / "unknown.sqlite"
    unknown = build_funding_bridge_service(
        unknown_path,
        context=context(),
        authorization=AUTH,
        clock=lambda: S09_AT + timedelta(days=30),
    )
    unknown.establish_baseline(baseline())
    intent = entry_intent(
        unknown,
        candidate_entry=candidate("OP-UNKNOWN", "SYN001"),
        command_id="unknown-entry",
        expected_revision=0,
        at=NOW,
    )
    assert unknown.enter(context(), intent).accepted
    cycle_id = unknown.current(context()).funding.bucket("U1").cycle_id
    assert cycle_id is not None
    with pytest.raises(FactError, match="SETTLEMENT_NOT_READY"):
        unknown.settle_cycle(context(), settlement_id="unknown-settle", cycle_id=cycle_id)
    assert unknown.current(context()).funding.completed_settlements == ()
    with sqlite3.connect(unknown_path) as db:
        assert (
            db.execute(
                "SELECT status FROM bridge_operations WHERE kind='settlement' "
                "AND identity='unknown-settle'"
            ).fetchone()[0]
            == "RECEIVED"
        )
    unknown.submit_buy(
        context(S02_AT),
        buy_report(
            "unknown-buy",
            "SYN001",
            at=datetime(2026, 11, 18, 1, tzinfo=timezone.utc),
            source_sequence="unknown-buy",
        ),
        buy_profile("unknown-buy"),
    )
    unknown.submit_sell(
        context(S03_AT),
        sell_report(
            "unknown-sell",
            "SYN001",
            cycle_id,
            "12",
            at=datetime(2026, 11, 19, 1, tzinfo=timezone.utc),
            source_sequence="unknown-sell",
        ),
        sell_profile("unknown-sell"),
    )
    completed = unknown.settle_cycle(
        context(S04_AT), settlement_id="unknown-settle", cycle_id=cycle_id
    )
    assert completed.funding.reserve == Decimal("1990.00")
    with sqlite3.connect(unknown_path) as db:
        assert (
            db.execute(
                "SELECT received_at FROM bridge_operations WHERE kind='settlement' AND "
                "identity='unknown-settle'"
            ).fetchone()[0]
            == S04_AT.isoformat()
        )

    fired: list[str] = []

    def fault(stage: str) -> None:
        if "settlement" in stage and not fired:
            fired.append(stage)
            raise RuntimeError("settlement fault")

    path = tmp_path / "settlement-fault.sqlite"
    service = build_funding_bridge_service(
        path,
        context=context(),
        authorization=AUTH,
        fault_hook=fault,
        temporal_profile=TEMPORAL,
    )
    cycle_id = drive_to_first_flat(service)
    before = service.current(context(S03_AT))
    with pytest.raises(RuntimeError, match="settlement fault"):
        service.settle_cycle(context(S04_AT), settlement_id="fault-settle", cycle_id=cycle_id)
    assert service.current(context(S04_AT)) == before
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT status FROM bridge_operations WHERE kind='settlement' "
                "AND identity='fault-settle'"
            ).fetchone()[0]
            == "RECEIVED"
        )
    settled = service.settle_cycle(context(S04_AT), settlement_id="fault-settle", cycle_id=cycle_id)
    assert settled.funding.reserve == Decimal("1990.00")
    assert service.rebuild(context(S04_AT)) == service.current(context(S04_AT))


def test_late_fee_fault_rollback_retry_and_conflict(tmp_path: Path) -> None:
    fired: list[str] = []

    def fault(stage: str) -> None:
        if "late_fee" in stage and not fired:
            fired.append(stage)
            raise RuntimeError("late fee fault")

    path = tmp_path / "late-fault.sqlite"
    service = build_funding_bridge_service(
        path,
        context=context(),
        authorization=AUTH,
        fault_hook=fault,
        temporal_profile=TEMPORAL,
    )
    first_cycle = drive_to_first_flat(service)
    service.settle_cycle(context(S04_AT), settlement_id="late-base-settle", cycle_id=first_cycle)
    second = entry_intent(
        service,
        candidate_entry=candidate("OP-LATE", "SYN002"),
        command_id="late-entry",
        expected_revision=service.current(context(S04_AT)).account_revision,
        at=S05_AT,
    )
    assert service.enter(context(S05_AT), second).accepted
    before = service.current(context(S05_AT))
    fact = LateFeeFact(
        "late-fault",
        "U1",
        first_cycle,
        Decimal("-100"),
        "fixture:late-fault",
        "late-fault",
        datetime(2026, 11, 18, 16, tzinfo=timezone.utc),
    )
    with pytest.raises(RuntimeError, match="late fee fault"):
        service.apply_late_fee(context(S06_AT), fact)
    assert service.current(context(S06_AT)) == before
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT status FROM bridge_operations WHERE kind='late_fee' "
                "AND identity='late-fault'"
            ).fetchone()[0]
            == "RECEIVED"
        )
        original_payload = db.execute(
            "SELECT payload_json FROM bridge_operations WHERE kind='late_fee' "
            "AND identity='late-fault'"
        ).fetchone()[0]
    with pytest.raises(FactError, match="IDEMPOTENCY_CONFLICT"):
        service.apply_late_fee(context(S06_AT), replace(fact, cash_delta=Decimal("-99")))
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT payload_json FROM bridge_operations WHERE kind='late_fee' "
                "AND identity='late-fault'"
            ).fetchone()[0]
            == original_payload
        )
    applied = service.apply_late_fee(context(S06_AT), fact)
    assert applied.account.cash == Decimal("21890.00")
    assert applied.funding.reserve == Decimal("1890.00")
    with pytest.raises(FactError, match="IDEMPOTENCY_CONFLICT"):
        service.apply_late_fee(context(S06_AT), replace(fact, cash_delta=Decimal("-99")))
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM bridge_conflict_register WHERE state='UNRESOLVED'"
            ).fetchone()[0]
            >= 1
        )
    assert service.apply_late_fee(context(S06_AT), fact) == applied
    reopened = build_funding_bridge_service(
        path, context=context(S06_AT), authorization=AUTH, temporal_profile=TEMPORAL
    )
    rebuilt = reopened.rebuild(context(S06_AT))
    current = reopened.current(context(S06_AT))
    assert rebuilt.account == current.account
    assert rebuilt.funding.to_dict() == current.funding.to_dict()
    with pytest.raises(FactError, match="IDEMPOTENCY_CONFLICT"):
        service.apply_late_fee(context(S06_AT), replace(fact, cash_delta=Decimal("-99")))


def test_unknown_sell_fee_is_rejected_before_fact_admission() -> None:
    with pytest.raises(FactError, match="INVALID_REPORT_SHAPE"):
        SellProfile(
            "single-fill-terminal-v1",
            "fixture:two-cycle:broker",
            500,
            500,
            0,
            None,  # type: ignore[arg-type]
            True,
        )


def test_temporal_profile_rejects_old_context_and_future_facts(tmp_path: Path) -> None:
    path = tmp_path / "temporal.sqlite"
    service = build_funding_bridge_service(
        path, context=context(), authorization=AUTH, temporal_profile=TEMPORAL
    )
    service.establish_baseline(baseline())
    intent = entry_intent(
        service,
        candidate_entry=candidate("OP-TIME", "SYN001"),
        command_id="time-entry",
        expected_revision=0,
        at=S01_AT,
    )
    assert service.enter(context(S01_AT), intent).accepted
    service.submit_buy(
        context(S02_AT),
        buy_report("time-buy", "SYN001", at=S02_AT, source_sequence="time-buy"),
        buy_profile("time-buy"),
    )
    cycle_id = service.current(context(S02_AT)).funding.bucket("U1").cycle_id
    assert cycle_id is not None
    assert service.risk_snapshot(context(S02_AT), mark_price=Decimal("10")).as_of == S02_AT
    with pytest.raises(FactError, match="CONTEXT_TIME_REGRESSION"):
        service.current(context(S01_AT))
    with pytest.raises(FactError, match="CONTEXT_TIME_REGRESSION"):
        service.settle_cycle(context(S01_AT), settlement_id="old-settle", cycle_id=cycle_id)
    with pytest.raises(FactError, match="FUTURE_FACT"):
        service.submit_sell(
            context(S02_AT),
            sell_report(
                "future-sell", "SYN001", cycle_id, "12", at=S08_AT, source_sequence="future-sell"
            ),
            sell_profile("future-sell"),
        )


def test_conflict_barrier_immutable_raw_fact_and_cached_receipt_integrity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "conflict.sqlite"
    service = build_funding_bridge_service(
        path, context=context(), authorization=AUTH, temporal_profile=TEMPORAL
    )
    cycle_id = drive_to_first_flat(service)
    with sqlite3.connect(path) as db:
        before = db.execute(
            "SELECT payload_json FROM bridge_operations WHERE kind='sell' AND identity='fault-sell'"
        ).fetchone()[0]
    with pytest.raises(FactError, match="IDEMPOTENCY_CONFLICT"):
        service.submit_sell(
            context(S04_AT),
            sell_report(
                "fault-sell", "SYN001", cycle_id, "11", at=S03_AT, source_sequence="fault-sell"
            ),
            sell_profile("fault-sell"),
        )
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT payload_json FROM bridge_operations WHERE kind='sell' "
                "AND identity='fault-sell'"
            ).fetchone()[0]
            == before
        )
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM bridge_conflict_register WHERE state='UNRESOLVED'"
            ).fetchone()[0]
            == 1
        )
    with pytest.raises(FactError, match="SETTLEMENT_NOT_READY"):
        service.settle_cycle(context(S04_AT), settlement_id="same-id-blocked", cycle_id=cycle_id)
    assert (
        service.submit_sell(
            context(S04_AT),
            sell_report(
                "fault-sell", "SYN001", cycle_id, "12", at=S03_AT, source_sequence="fault-sell"
            ),
            sell_profile("fault-sell"),
        ).account.quantity("SYN001")
        == 0
    )
    barrier_path = tmp_path / "barrier.sqlite"
    barrier = build_funding_bridge_service(
        barrier_path, context=context(), authorization=AUTH, temporal_profile=TEMPORAL
    )
    barrier_cycle = drive_to_first_flat(barrier)
    original_receive = barrier._port.receive_operation  # type: ignore[attr-defined]

    def inject_conflict(*args, **kwargs):
        receipt = original_receive(*args, **kwargs)
        with sqlite3.connect(barrier_path) as db:
            db.execute(
                "INSERT INTO bridge_conflict_register VALUES(?,?,?,?,?,?,?)",
                (
                    "barrier",
                    "SYN-TWOCYCLE",
                    "synthetic",
                    "account:SYN-TWOCYCLE",
                    "UNRESOLVED",
                    "barrier",
                    "raw conflict",
                ),
            )
        return receipt

    barrier._port.receive_operation = inject_conflict  # type: ignore[attr-defined,method-assign]
    with pytest.raises(FactError, match="SETTLEMENT_NOT_READY"):
        barrier.settle_cycle(context(S04_AT), settlement_id="barrier", cycle_id=barrier_cycle)

    receipt_path = tmp_path / "receipt.sqlite"
    receipt = build_funding_bridge_service(
        receipt_path, context=context(), authorization=AUTH, temporal_profile=TEMPORAL
    )
    receipt_cycle = drive_to_first_flat(receipt)
    settled = receipt.settle_cycle(
        context(S04_AT), settlement_id="receipt-settle", cycle_id=receipt_cycle
    )
    clean_path = tmp_path / "receipt-clean.sqlite"
    with sqlite3.connect(receipt_path) as source, sqlite3.connect(clean_path) as target:
        source.backup(target)
    with sqlite3.connect(receipt_path) as db:
        raw = json.loads(
            db.execute(
                "SELECT result_json FROM bridge_operations WHERE kind='settlement' "
                "AND identity='receipt-settle'"
            ).fetchone()[0]
        )
        raw["decision"]["result_digest"] = "sha256:" + "0" * 64
        db.execute(
            "UPDATE bridge_operations SET result_json=? WHERE kind='settlement' "
            "AND identity='receipt-settle'",
            (json.dumps(raw, sort_keys=True),),
        )
    corrupt = build_funding_bridge_service(
        receipt_path, context=context(S04_AT), authorization=AUTH, temporal_profile=TEMPORAL
    )
    with pytest.raises(FactError, match="STORE_PROJECTION_MISMATCH"):
        corrupt.settle_cycle(
            context(S04_AT), settlement_id="receipt-settle", cycle_id=receipt_cycle
        )
    with pytest.raises(FactError, match="STORE_PROJECTION_MISMATCH"):
        corrupt.rebuild(context(S04_AT))
    clean = build_funding_bridge_service(
        clean_path, context=context(S04_AT), authorization=AUTH, temporal_profile=TEMPORAL
    )
    clean.rebuild(context(S04_AT))
    assert (
        clean.settle_cycle(
            context(S04_AT), settlement_id="receipt-settle", cycle_id=receipt_cycle
        ).decision.result_digest
        == settled.decision.result_digest
    )

    result_path = tmp_path / "journal-result.sqlite"
    with sqlite3.connect(clean_path) as source, sqlite3.connect(result_path) as target:
        source.backup(target)
    with sqlite3.connect(result_path) as db:
        event = json.loads(
            db.execute("SELECT payload_json FROM bridge_journal WHERE kind='buy'").fetchone()[0]
        )
        event["result"]["account"]["cash"] = "0.00"
        changed = json.dumps(event, sort_keys=True)
        db.execute(
            "UPDATE bridge_journal SET payload_json=?,digest=? WHERE kind='buy'",
            (changed, digest(event)),
        )
        cached = json.loads(
            db.execute("SELECT result_json FROM bridge_inbox WHERE status='POSTED'").fetchone()[0]
        )
        cached["account"]["cash"] = "0.00"
        db.execute(
            "UPDATE bridge_inbox SET result_json=? WHERE status='POSTED'",
            (json.dumps(cached, sort_keys=True),),
        )
    result_corrupt = build_funding_bridge_service(
        result_path, context=context(S04_AT), authorization=AUTH, temporal_profile=TEMPORAL
    )
    with pytest.raises(FactError, match="buy result identity differs"):
        result_corrupt.rebuild(context(S04_AT))

    missing_path = tmp_path / "missing-profile.sqlite"
    missing = build_funding_bridge_service(missing_path, context=context(), authorization=AUTH)
    missing_cycle = drive_to_first_flat(missing)
    missing.settle_cycle(context(S04_AT), settlement_id="first", cycle_id=missing_cycle)
    intent = entry_intent(
        missing,
        candidate_entry=candidate("OP-NO-PROFILE", "SYN002"),
        command_id="no-profile",
        expected_revision=missing.current(context(S04_AT)).account_revision,
        at=S05_AT,
    )
    with pytest.raises(FactError, match="TEMPORAL_PROFILE_REQUIRED"):
        missing.enter(context(S05_AT), intent)

    order_path = tmp_path / "source-order.sqlite"
    order = build_funding_bridge_service(
        order_path, context=context(), authorization=AUTH, temporal_profile=TEMPORAL
    )
    order.establish_baseline(baseline())
    order_entry = entry_intent(
        order,
        candidate_entry=candidate("OP-ORDER", "SYN001"),
        command_id="order-entry",
        expected_revision=0,
        at=NOW,
    )
    order.enter(context(), order_entry)
    order_cycle = order.current(context()).funding.bucket("U1").cycle_id
    assert order_cycle is not None
    order.submit_buy(
        context(S02_AT),
        buy_report("order-buy", "SYN001", at=S02_AT, source_sequence="order-buy"),
        buy_profile("order-buy"),
    )
    before_rejected_sell = order.current(context(S02_AT))
    with pytest.raises(FactError, match="TEMPORAL_PROFILE_REQUIRED"):
        order.submit_sell(
            context(S03_AT),
            sell_report(
                "order-sell", "SYN001", order_cycle, "12", at=NOW, source_sequence="order-sell"
            ),
            sell_profile("order-sell"),
        )
    assert order.current(context(S03_AT)) == before_rejected_sell
    with sqlite3.connect(order_path) as db:
        assert (
            db.execute(
                "SELECT status FROM bridge_operations WHERE kind='sell' AND identity='order-sell'"
            ).fetchone()[0]
            == "RECEIVED"
        )

    plain_order_path = tmp_path / "plain-source-order.sqlite"
    plain_order = build_funding_bridge_service(
        plain_order_path, context=context(), authorization=AUTH
    )
    plain_order.establish_baseline(baseline())
    plain_entry = entry_intent(
        plain_order,
        candidate_entry=candidate("OP-PLAIN-ORDER", "SYN001"),
        command_id="plain-order-entry",
        expected_revision=0,
        at=NOW,
    )
    plain_order.enter(context(), plain_entry)
    plain_cycle = plain_order.current(context()).funding.bucket("U1").cycle_id
    assert plain_cycle is not None
    plain_order.submit_buy(
        context(S02_AT),
        buy_report("plain-order-buy", "SYN001", at=S02_AT, source_sequence="plain-order-buy"),
        buy_profile("plain-order-buy"),
    )
    with pytest.raises(FactError, match="TEMPORAL_ORDER_VIOLATION"):
        plain_order.submit_sell(
            context(S03_AT),
            sell_report(
                "plain-order-sell",
                "SYN001",
                plain_cycle,
                "12",
                at=NOW,
                source_sequence="plain-order-sell",
            ),
            sell_profile("plain-order-sell"),
        )


@pytest.mark.parametrize("offset_days", [-1, 7])
def test_fresh_entry_requires_same_asof_risk_and_preserves_original_replay(
    tmp_path: Path, offset_days: int
) -> None:
    service = build_funding_bridge_service(
        tmp_path / "entry-risk-clock.sqlite",
        context=context(),
        authorization=AUTH,
        temporal_profile=TEMPORAL,
    )
    service.establish_baseline(baseline())
    intent = entry_intent(
        service,
        candidate_entry=candidate("OP-RISK-CLOCK", "SYN001"),
        command_id="risk-clock-entry",
        expected_revision=0,
        at=NOW,
    )
    wrong_snapshot = replace(intent.snapshot, as_of=NOW + timedelta(days=offset_days))
    args = {
        "candidate": intent.candidate,
        "snapshot": wrong_snapshot,
        "empty_bucket_ids": intent.empty_bucket_ids,
        "active_or_reserved_security_codes": intent.active_or_reserved_security_codes,
        "active_industry_counts": intent.active_industry_counts,
        "new_buckets_today": intent.new_buckets_today,
        "limits": intent.limits,
        "fraction": intent.fraction,
        "mark_price": intent.mark_price,
        "price_bound": intent.price_bound,
        "lot_size": intent.lot_size,
        "quantity_limits": intent.quantity_limits,
        "fee_rule": intent.fee_rule,
    }
    wrong_intent = replace(
        intent,
        snapshot=wrong_snapshot,
        command=CommandEnvelope.create(
            intent.command.command_id, entry_invocation_payload(**args), 0
        ),
    )
    before = service.current(context())
    with pytest.raises(FactError, match="RISK_SNAPSHOT_STALE"):
        service.enter(context(), wrong_intent)
    assert service.current(context()) == before

    accepted = service.enter(context(), intent)
    assert accepted.accepted and accepted.quantity == 500
    service.submit_buy(
        context(S02_AT),
        buy_report("risk-clock-buy", "SYN001", at=S02_AT, source_sequence="risk-clock-buy"),
        buy_profile("risk-clock-buy"),
    )
    after_fill = service.current(context(S02_AT))
    replay = service.enter(context(), intent)
    assert replay.quantity == accepted.quantity
    assert replay.reservation_id == accepted.reservation_id
    assert replay.result_digest == accepted.result_digest
    assert service.current(context(S02_AT)) == after_fill
