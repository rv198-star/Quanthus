"""F01-F07 public core funding/account bridge acceptance."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
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
    pending_cash_commitment,
)
from quanthus.shared.fact_contracts import FactError, digest
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


def rebind_intent(
    intent: CycleEntryIntent,
    snapshot: RiskSnapshot,
    *,
    command_id: str,
    expected_account_revision: int,
    candidate: CandidateEntry | None = None,
) -> CycleEntryIntent:
    candidate = candidate or intent.candidate
    payload = entry_invocation_payload(
        candidate=candidate,
        snapshot=snapshot,
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
    )
    return replace(
        intent,
        expected_account_revision=expected_account_revision,
        command=CommandEnvelope.create(command_id, payload, expected_account_revision),
        candidate=candidate,
        snapshot=snapshot,
    )


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
    bool_revision = deepcopy(raw["final_state"])
    bool_revision["revision"] = False
    bool_envelope = {
        "schema_version": envelope["schema_version"],
        "state": bool_revision,
        "digest": digest(bool_revision),
    }
    with pytest.raises(ValueError, match="FUNDING_STATE_SCHEMA_MISMATCH"):
        restore_funding_envelope(bool_envelope)
    nested_extra = deepcopy(raw["final_state"])
    nested_extra["buckets"][0]["unexpected"] = "drop-me"
    nested_envelope = {
        "schema_version": envelope["schema_version"],
        "state": nested_extra,
        "digest": digest(nested_extra),
    }
    with pytest.raises(ValueError, match="FUNDING_STATE_SCHEMA_MISMATCH"):
        restore_funding_envelope(nested_envelope)


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
    before_fill_risk = service.risk_snapshot(context(), mark_price=Decimal("10"))
    assert before_fill_risk.equity == Decimal("21000.00")
    assert before_fill_risk.cash_before_internal_holds == Decimal("21000.00")
    assert len(before_fill_risk.pending_buys) == 1
    assert before_fill_risk.pending_buys[0].remaining_quantity == 500
    assert pending_cash_commitment(before_fill_risk).total == Decimal("5005.00")

    result = service.submit_buy(context(), report(), profile())
    assert result.account.cash == Decimal("15995.00")
    assert result.account.quantity("SYN001") == 500
    assert result.funding.bucket("U1").cash == Decimal("14995.00")
    assert result.funding.reserve == Decimal("1000.00")
    assert result.funding.cash_total == Decimal("15995.00")
    assert result.funding.bucket("U1").locked_cycle_budget == Decimal("20000.00")
    assert result.funding.active_entry_reservations[0].reservation_id == entry.reservation_id
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
        assert db.execute("SELECT status FROM bridge_obligations").fetchone()[0] == "FILLED"
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
    bad_snapshot = replace(intent.snapshot, snapshot_id="stale", cash_available=Decimal("0"))
    bad_intent = replace(
        intent,
        expected_account_revision=before_entry_replay.account_revision,
        command=CommandEnvelope.create(
            "stale-risk",
            entry_invocation_payload(
                candidate=intent.candidate,
                snapshot=bad_snapshot,
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
            ),
            before_entry_replay.account_revision,
        ),
        snapshot=bad_snapshot,
    )
    with pytest.raises(FactError, match="RISK_SNAPSHOT_STALE"):
        service.enter(context(), bad_intent)
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
    assert len(service.risk_snapshot(context(), mark_price=Decimal("10")).pending_buys) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM bridge_inbox").fetchone()[0] == "RECEIVED"
        assert db.execute("SELECT count(*) FROM bridge_events").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM bridge_outbox").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM bridge_source_facts").fetchone()[0] == 0
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


def test_baseline_pending_source_profile_and_other_bucket_identity(tmp_path: Path) -> None:
    path = tmp_path / "identity.sqlite"
    service = build_funding_bridge_service(path, context=context(), authorization=AUTH)
    start = FundingState(
        (
            BucketState(
                "U1", Decimal("20000"), Decimal("20000"), Decimal("0"), None, BucketPhase.EMPTY
            ),
            BucketState(
                "U2", Decimal("5000"), Decimal("5000"), Decimal("0"), None, BucketPhase.EMPTY
            ),
        ),
        Decimal("1000"),
        Decimal("0"),
        0,
    )
    service.establish_baseline(
        CycleBaseline(context(), "identity-base", Decimal("26000"), start, "SYN001")
    )
    changed = FundingState(
        (
            BucketState(
                "U1", Decimal("20001"), Decimal("20001"), Decimal("0"), None, BucketPhase.EMPTY
            ),
        ),
        Decimal("1000"),
        Decimal("0"),
        0,
    )
    with pytest.raises(FactError, match="IDEMPOTENCY_CONFLICT"):
        service.establish_baseline(
            CycleBaseline(context(), "changed-base", Decimal("21001"), changed, "SYN001")
        )
    assert service.current(context()).funding == start
    intent, _ = inputs()
    intent = rebind_intent(
        intent,
        service.risk_snapshot(context(), mark_price=Decimal("10")),
        command_id="two-bucket-entry",
        expected_account_revision=0,
    )
    service.enter(context(), intent)
    service.submit_buy(context(), report(), profile())
    assert service.current(context()).funding.bucket("U2").cash == Decimal("5000")
    before_alias = service.current(context())
    with pytest.raises(FactError, match="SOURCE_FACT_DUPLICATE"):
        service.submit_buy(context(), replace(report(), report_id="alias-report"), profile())
    assert service.current(context()) == before_alias
    with pytest.raises(FactError, match="IDEMPOTENCY_CONFLICT"):
        service.submit_buy(
            context(),
            report(),
            replace(
                profile(), profile_version="changed", source_id="other", confirmed_fee=Decimal("99")
            ),
        )
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM bridge_conflicts").fetchone()[0] >= 2
        alias = db.execute(
            "SELECT status FROM bridge_inbox WHERE report_id='alias-report'"
        ).fetchone()
        assert alias == ("RECEIVED",)


def test_rejected_entries_do_not_advance_rebuild_revision(tmp_path: Path) -> None:
    path = tmp_path / "rejected.sqlite"
    service = build_funding_bridge_service(path, context=context(), authorization=AUTH)
    service.establish_baseline(baseline())
    intent, _ = inputs()
    refused = replace(
        intent,
        candidate=replace(intent.candidate, input_valid=False),
        command=CommandEnvelope.create(
            "first-refusal",
            entry_invocation_payload(
                candidate=replace(intent.candidate, input_valid=False),
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
            ),
            0,
        ),
    )
    first = service.enter(context(), refused)
    assert not first.accepted and service.current(context()).account_revision == 0
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT accepted FROM bridge_entry_receipts WHERE command_id='first-refusal'"
            ).fetchone()[0]
            == 0
        )
    assert service.rebuild(context()) == service.current(context())

    accepted = service.enter(context(), intent)
    assert accepted.accepted
    current_risk = service.risk_snapshot(context(), mark_price=Decimal("10"))
    second_refused = replace(
        refused,
        expected_account_revision=service.current(context()).account_revision,
        command=CommandEnvelope.create(
            "second-refusal",
            entry_invocation_payload(
                candidate=refused.candidate,
                snapshot=current_risk,
                empty_bucket_ids=("U1",),
                active_or_reserved_security_codes=(),
                active_industry_counts=(),
                new_buckets_today=0,
                limits=refused.limits,
                fraction=refused.fraction,
                mark_price=refused.mark_price,
                price_bound=refused.price_bound,
                lot_size=refused.lot_size,
                quantity_limits=refused.quantity_limits,
                fee_rule=refused.fee_rule,
            ),
            service.current(context()).account_revision,
        ),
        snapshot=current_risk,
    )
    second = service.enter(context(), second_refused)
    assert not second.accepted
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT accepted FROM bridge_entry_receipts WHERE command_id='second-refusal'"
            ).fetchone()[0]
            == 0
        )
    assert service.current(context()).account_revision == 1
    assert service.rebuild(context()) == service.current(context())


def test_incompatible_cycle_store_is_not_reinterpreted(tmp_path: Path) -> None:
    path = tmp_path / "candidate-v1.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE candidate_state(value TEXT)")
        db.execute("PRAGMA user_version=1")
    with pytest.raises(FactError, match="STORE_SCHEMA_MISMATCH"):
        build_funding_bridge_service(path, context=context(), authorization=AUTH)
