"""F01-F07 public core funding/account bridge acceptance."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.execution import ExecutionReport, SyntheticContext
from quanthus.platform.adapters.product_daily_cycle_sqlite import build_funding_bridge_service
from quanthus.product_daily.application.funding_bridge import (
    CycleBaseline,
    CycleEntryIntent,
    CycleFillProfile,
    funding_state_envelope,
    restore_funding_envelope,
    restore_funding_state,
)
from quanthus.product_daily.application.models import SyntheticRuntimeAuthorization
from quanthus.product_daily.candidate import CandidateProjection
from quanthus.product_daily.contracts import CommandEnvelope, ProductDailyError
from quanthus.product_daily.funding import BucketPhase, BucketState, FundingState
from quanthus.product_daily.risk import (
    BuyFeeRule,
    CandidateEntry,
    EntryLimits,
    ExpertEntryAction,
    ExpertEntryEligibility,
    QuantityLimits,
    RiskSnapshot,
    decide_entry,
    entry_invocation_payload,
)
from tests.unit.test_product_daily_r11 import EXPERT_EVENT_ID
from tests.unit.test_product_daily_r11_review import _entry_inputs

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT
    / "config/research/r11/continuation-003"
    / "inherited-two-cycle-serialization-fixture.v1.json"
)
NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)
AUTH = SyntheticRuntimeAuthorization("bridge-runtime", frozenset({"SYN-BRIDGE"}))


def context(mode: str = "synthetic") -> SyntheticContext:
    return SyntheticContext("SYN-BRIDGE", mode, "fixture:bridge", NOW, "complete")


def baseline() -> CycleBaseline:
    start = FundingState(
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
    return CycleBaseline(context(), "bridge-baseline", Decimal("21000.00"), start, "SYN001")


def inputs() -> tuple[CycleEntryIntent, dict[str, object]]:
    expert = ExpertEntryEligibility(
        EXPERT_EVENT_ID,
        "SYN001",
        ExpertEntryAction.TRIAL_ENTRY,
        "sha256:" + "1" * 64,
        Decimal(".30"),
    )
    candidate = CandidateEntry(
        EXPERT_EVENT_ID,
        "SYN001",
        "IND1",
        CandidateProjection.OBSERVED,
        True,
        True,
        True,
        expert,
    )
    snapshot = RiskSnapshot("bridge-entry-risk", NOW, Decimal("21000"), Decimal("21000"))
    payload: dict[str, object] = {
        "candidate": candidate,
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
    command_payload = entry_invocation_payload(**payload)  # type: ignore[arg-type]
    intent = CycleEntryIntent(
        0,
        CommandEnvelope.create("bridge-entry", command_payload, 0),
        candidate,
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
    return intent, payload


def report() -> ExecutionReport:
    return ExecutionReport(
        "bridge-fill-1",
        "SYN001",
        "buy",
        500,
        Decimal("10"),
        Decimal("5"),
        NOW,
        "broker:synthetic",
        "single-fill-1",
        500,
        Decimal("10"),
    )


def profile() -> CycleFillProfile:
    return CycleFillProfile(
        "single-fill-terminal-v1", "broker:synthetic", 500, 500, 0, Decimal("5"), True
    )


def test_f01_complete_funding_state_roundtrip_and_fail_closed() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    start, args, command = _entry_inputs(reserve="1000", expert_cap=".30")
    first = decide_entry(funding=start, command=command, **args)
    assert first.funding_state.digest == raw["first_entry"]["funding_state_digest"]
    assert first.funding_state.revision == raw["first_entry"]["funding_state_revision"]
    state = restore_funding_state(raw["final_state"])
    assert state.to_dict() == raw["final_state"]
    envelope = funding_state_envelope(state)
    assert restore_funding_envelope(envelope) == state
    broken = dict(raw["final_state"])
    broken.pop("entry_reservations")
    with pytest.raises(ValueError, match="FUNDING_STATE_SCHEMA_MISMATCH"):
        restore_funding_state(broken)
    with pytest.raises(ValueError, match="UNSUPPORTED_FUNDING_STATE_SCHEMA"):
        restore_funding_envelope({**envelope, "schema_version": "future"})


def test_f02_f03_f04_f05_entry_buy_risk_replay_and_stale_cas(tmp_path: Path) -> None:
    path = tmp_path / "bridge.sqlite"
    service = build_funding_bridge_service(path, context=context(), authorization=AUTH)
    service.establish_baseline(baseline())
    intent, _ = inputs()
    entry = service.enter(context(), intent)
    assert entry.accepted and entry.quantity == 500 and entry.reservation_id
    state = service.current(context())
    assert state.account.cash == Decimal("21000.00")
    assert state.funding.bucket("U1").locked_cycle_budget == Decimal("20000.00")

    result = service.submit_buy(context(), report(), profile())
    assert result.account.cash == Decimal("15995.00")
    assert result.account.quantity("SYN001") == 500
    assert result.funding.bucket("U1").cash == Decimal("14995.00")
    assert result.funding.reserve == Decimal("1000.00")
    assert result.funding.cash_total == Decimal("15995.00")
    assert result.funding.bucket("U1").locked_cycle_budget == Decimal("20000.00")
    assert sum((posting.amount for posting in result.postings), Decimal("0")) == 0
    assert service.risk_snapshot(context(), mark_price=Decimal("10")).equity == Decimal("20995.00")
    risk = service.risk_snapshot(context(), mark_price=Decimal("10"))
    assert risk.cash_before_internal_holds == Decimal("15995.00")
    assert risk.pending_buys == ()

    replay = service.submit_buy(context(), report(), profile())
    assert replay.account == result.account and replay.funding == result.funding
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM bridge_events").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM bridge_outbox").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM bridge_postings").fetchone()[0] == 3
    before_entry_replay = service.current(context())
    replay_entry = service.enter(context(), intent)
    assert replay_entry.quantity == entry.quantity
    assert replay_entry.reservation_id == entry.reservation_id
    assert replay_entry.result_digest == entry.result_digest
    assert service.current(context()) == before_entry_replay
    reopened = build_funding_bridge_service(path, context=context(), authorization=AUTH)
    assert reopened.rebuild(context()) == reopened.current(context())
    with pytest.raises(ProductDailyError, match="ACCOUNT_REVISION_CONFLICT"):
        service.enter(
            context(),
            replace(
                intent,
                expected_account_revision=0,
                command=replace(intent.command, command_id="stale-entry"),
            ),
        )
    with pytest.raises(ValueError, match="UNAUTHORIZED_CONTEXT"):
        service.current(context("actual"))


def test_f06_fault_is_raw_inbox_only_then_retry_cold_reopen_and_rebuild(tmp_path: Path) -> None:
    path = tmp_path / "bridge.sqlite"
    fired: list[str] = []

    def fail(stage: str) -> None:
        if not fired:
            fired.append(stage)
            raise RuntimeError("bridge fault")

    service = build_funding_bridge_service(
        path, context=context(), authorization=AUTH, fault_hook=fail
    )
    service.establish_baseline(baseline())
    intent, _ = inputs()
    service.enter(context(), intent)
    before_fault = service.current(context())
    with pytest.raises(RuntimeError, match="bridge fault"):
        service.submit_buy(context(), report(), profile())
    assert service.current(context()) == before_fault
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM bridge_inbox").fetchone()[0] == "RECEIVED"
        assert db.execute("SELECT count(*) FROM bridge_events").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM bridge_outbox").fetchone()[0] == 0
        assert db.execute("SELECT cash FROM bridge_account").fetchone()[0] == "21000.00"
    result = service.submit_buy(context(), report(), profile())
    reopened = build_funding_bridge_service(path, context=context(), authorization=AUTH)
    assert reopened.current(context()).account == result.account
    assert reopened.rebuild(context()) == reopened.current(context())
    assert reopened.submit_buy(context(), report(), profile()) == result


def test_f07_public_capabilities_and_old_core_boundary() -> None:
    accounting = (ROOT / "src/quanthus/capabilities/accounting/facts.py").read_text()
    execution = (ROOT / "src/quanthus/capabilities/execution/facts.py").read_text()
    assert "sqlite3" not in accounting and "sqlite3" not in execution
    assert "quanthus.product_daily.application" not in accounting
    assert "quanthus.product_daily.application" not in execution
