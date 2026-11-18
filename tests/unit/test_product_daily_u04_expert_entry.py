"""Actual original expert aggregation through the synthetic durable entry boundary."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from quanthus.aggregator import GovernedAction
from quanthus.capabilities.execution import ExecutionReport, SyntheticContext
from quanthus.expert_strategy import ExpertStrategyRegistry, FormalStrategyRunner
from quanthus.platform.adapters.product_daily_cycle_sqlite import build_funding_bridge_service
from quanthus.product_daily.application.expert_entry import ExpertEntryRequest, ExpertEntryService
from quanthus.product_daily.application.funding_bridge import (
    CycleBaseline,
    CycleFillProfile,
    FundingBridgeService,
)
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization
from quanthus.product_daily.candidate import CandidateProjection
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.funding import BucketPhase, BucketState, FundingState
from quanthus.product_daily.risk import (
    BuyFeeRule,
    CandidateEntry,
    EntryLimits,
    ExpertEntryAction,
    ExpertEntryEligibility,
    QuantityLimits,
    RiskSnapshot,
    pending_cash_commitment,
)
from quanthus.shared.fact_contracts import FactError
from tests.unit.test_formal_expert_strategy import CUTOFF, EVENT_ID, _context

ROOT = Path(__file__).resolve().parents[2]
NOW = CUTOFF + timedelta(hours=1)
CONTEXT = SyntheticContext("SYN-EXPERT-ENTRY", "synthetic", "fixture:expert", NOW, "complete")
AUTH = SyntheticRuntimeAuthorization("original-expert-runtime", frozenset({CONTEXT.account_id}))


def request() -> ExpertEntryRequest:
    return ExpertEntryRequest(
        command_id="original-expert-entry",
        expected_account_revision=0,
        expected_funding_revision=0,
        candidate=CandidateEntry(
            EVENT_ID, "security-cn-000001", "IND1", CandidateProjection.OBSERVED, True, True, True
        ),
        expert_context=_context(),
        trend_state="stable",
        entry_evidence_ready=True,
        snapshot=RiskSnapshot("risk:expert-entry", NOW, Decimal("500000"), Decimal("500000")),
        empty_bucket_ids=tuple(f"U{i}" for i in range(1, 6)),
        active_or_reserved_security_codes=(),
        active_industry_counts=(),
        new_buckets_today=0,
        limits=EntryLimits(2, 5),
        fraction=Decimal("0.30"),
        mark_price=Decimal("10"),
        price_bound=Decimal("10"),
        lot_size=100,
        quantity_limits=QuantityLimits(Decimal("0.04"), Decimal("0.08"), Decimal("0.20")),
        fee_rule=BuyFeeRule(Decimal("0.0003"), Decimal("5"), Decimal("0.01")),
    )


def services(path: Path) -> tuple[ExpertEntryService, FundingBridgeService]:
    registry = ExpertStrategyRegistry(ROOT)
    runner = FormalStrategyRunner(registry.card, registry.topology_manifest.digest)
    bridge = build_funding_bridge_service(path, context=CONTEXT, authorization=AUTH)
    funding = FundingState(
        tuple(
            BucketState(
                f"U{i}",
                Decimal("100000"),
                Decimal("100000"),
                Decimal("0"),
                None,
                BucketPhase.EMPTY,
            )
            for i in range(1, 6)
        ),
        Decimal("0"),
        Decimal("0"),
        0,
    )
    bridge.establish_baseline(
        CycleBaseline(CONTEXT, "expert-baseline", Decimal("500000"), funding, "security-cn-000001")
    )
    return ExpertEntryService(runner, bridge), bridge


def test_actual_experts_drive_quantity_reservation_and_persisted_digest(tmp_path: Path) -> None:
    path = tmp_path / "expert.sqlite"
    app, bridge = services(path)
    result = app.enter(CONTEXT, request())
    assert result.decision.action.final_action is GovernedAction.TRIAL_ENTRY
    assert result.decision.target_maximum_position == Decimal("0.20")
    assert result.entry.accepted and result.entry.quantity == 1900
    assert result.entry.intent is not None
    expert = result.entry.intent.candidate.expert_entry
    assert expert is not None and expert.source_digest == result.decision.digest
    assert expert.target_maximum_position == Decimal("0.20")
    state = bridge.current(CONTEXT)
    assert state.account.cash == state.funding.cash_total == Decimal("500000")
    assert state.funding.bucket("U1").locked_cycle_budget == Decimal("100000")
    assert len(state.funding.active_entry_reservations) == 1
    risk = bridge.risk_snapshot(CONTEXT, mark_price=Decimal("10"))
    assert pending_cash_commitment(risk).total == Decimal("19005.70")
    assert result.entry.obligation is not None
    assert result.entry.obligation.fee_upper_bound == Decimal("5.70")
    with sqlite3.connect(path) as db:
        payload = db.execute("SELECT payload_json FROM bridge_journal").fetchone()[0]
        assert result.decision.digest in payload
    assert bridge.rebuild(CONTEXT) == state


@pytest.mark.parametrize("case", ["fragile", "risk_veto", "missing_risk", "unqualified"])
def test_original_expert_or_candidate_refusal_preserves_economic_state(
    tmp_path: Path, case: str
) -> None:
    path = tmp_path / "blocked.sqlite"
    app, bridge = services(path)
    original = request()
    if case == "fragile":
        original = replace(original, trend_state="fragile")
    elif case == "risk_veto":
        original = replace(original, expert_context=_context(volatility=Decimal("0.51")))
    elif case == "missing_risk":
        original = replace(original, expert_context=_context(rsi=None))
    else:
        original = replace(original, candidate=replace(original.candidate, policy_adopted=False))
    before = bridge.current(CONTEXT)
    result = app.enter(CONTEXT, original)
    assert not result.entry.accepted and result.entry.quantity == 0
    if case != "unqualified":
        assert result.decision.action.final_action is GovernedAction.NO_ACTION
    assert bridge.current(CONTEXT) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM bridge_journal").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM bridge_obligations").fetchone()[0] == 0


@pytest.mark.parametrize(
    "case", ["security", "future_cutoff", "source", "coverage", "mode", "override", "snapshot"]
)
def test_identity_time_and_override_refuse_without_writes(tmp_path: Path, case: str) -> None:
    app, bridge = services(tmp_path / "identity.sqlite")
    original = request()
    caller = CONTEXT
    if case == "security":
        original = replace(original, candidate=replace(original.candidate, security_code="OTHER"))
    elif case == "future_cutoff":
        caller = replace(CONTEXT, as_of=CUTOFF - timedelta(seconds=1))
    elif case == "source":
        caller = replace(CONTEXT, source_id="fixture:other")
    elif case == "coverage":
        caller = replace(CONTEXT, coverage_id="other")
    elif case == "mode":
        caller = replace(CONTEXT, mode="actual")
    elif case == "override":
        forged = ExpertEntryEligibility(
            EVENT_ID, "security-cn-000001", ExpertEntryAction.TRIAL_ENTRY, "fake", Decimal("1")
        )
        original = replace(original, candidate=replace(original.candidate, expert_entry=forged))
    else:
        original = replace(
            original, snapshot=replace(original.snapshot, as_of=NOW + timedelta(days=1))
        )
    before = bridge.current(CONTEXT)
    with pytest.raises(FactError):
        app.enter(caller, original)
    assert bridge.current(CONTEXT) == before


def test_original_command_replays_after_actual_buy_and_cold_reopen(tmp_path: Path) -> None:
    path = tmp_path / "replay.sqlite"
    app, bridge = services(path)
    original = request()
    entry = app.enter(CONTEXT, original)
    later = replace(CONTEXT, as_of=CUTOFF + timedelta(days=1))
    report = ExecutionReport(
        "original-expert-buy",
        "security-cn-000001",
        "buy",
        1900,
        Decimal("10"),
        Decimal("5.70"),
        later.as_of,
        CONTEXT.source_id,
        "1",
        1900,
        Decimal("10"),
    )
    bridge.submit_buy(
        later,
        report,
        CycleFillProfile("expert-full-fill-v1", CONTEXT.source_id, 1900, 1900, 0, Decimal("5.70")),
    )
    before = bridge.current(later)
    assert before.account.cash == Decimal("480994.30")
    assert before.account.quantity("security-cn-000001") == 1900
    registry = ExpertStrategyRegistry(ROOT)
    reopened = build_funding_bridge_service(path, context=CONTEXT, authorization=AUTH)
    app = ExpertEntryService(
        FormalStrategyRunner(registry.card, registry.topology_manifest.digest), reopened
    )
    replay = app.enter(later, original)
    assert replay.entry.replayed and replay.entry.quantity == 1900
    assert replay.entry.result_digest == entry.entry.result_digest
    assert replay.decision.digest == entry.decision.digest
    assert reopened.current(later) == before
    assert reopened.rebuild(later) == before
    with pytest.raises((FactError, ProductDailyError)):
        app.enter(later, replace(original, trend_state="fragile"))
    assert reopened.current(later) == before


@pytest.mark.parametrize("flag", ["false", 1, 0, None, 0.0])
def test_invalid_entry_evidence_flag_refuses_before_economic_writes(
    tmp_path: Path, flag: object
) -> None:
    app, bridge = services(tmp_path / "flag.sqlite")
    before = bridge.current(CONTEXT)
    with pytest.raises(ProductDailyError, match="invalid_boolean"):
        # cast keeps the malformed runtime value for the public-boundary test.
        app.enter(CONTEXT, replace(request(), entry_evidence_ready=cast(bool, flag)))
    assert bridge.current(CONTEXT) == before


def test_actual_false_evidence_remains_no_action_without_economic_change(tmp_path: Path) -> None:
    app, bridge = services(tmp_path / "false.sqlite")
    before = bridge.current(CONTEXT)
    result = app.enter(CONTEXT, replace(request(), entry_evidence_ready=False))
    assert not result.entry.accepted and result.entry.quantity == 0
    assert result.decision.action.final_action is GovernedAction.NO_ACTION
    assert bridge.current(CONTEXT) == before
