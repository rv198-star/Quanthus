"""Synthetic A10-A13 public transaction witnesses for C13."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest

from quanthus.platform.adapters.product_daily_cycle_sqlite import build_funding_bridge_service
from quanthus.product_daily.application.funding_bridge import LaterEntryRequest
from quanthus.product_daily.c13_later_signal import (
    LaterSignalStatus,
    OriginalAttemptState,
    evaluate_later_signal,
)
from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.risk import ExpertEntryAction, ExpertEntryEligibility
from quanthus.shared.fact_contracts import FactError
from tests.unit.test_product_daily_c13_later_signal import (
    calendar,
    current,
    opportunity,
    policy,
    prior,
)
from tests.unit.test_product_daily_u05_two_cycle import (
    AUTH,
    baseline,
    buy_profile,
    buy_report,
    candidate,
    context,
    entry_intent,
)

NOW = datetime.fromisoformat("2026-11-18T07:10:00+00:00")


def later_request(
    service, *, selected_policy=None, security_id="security-cn-000001"
) -> LaterEntryRequest:
    parent = opportunity(
        account_id=context().account_id,
        security_id=security_id,
        original_attempt_state=OriginalAttemptState.NEVER_SUBMITTED,
    )
    preceding = prior(security_id=security_id)
    latest = current(security_id=security_id)
    selected_policy = policy() if selected_policy is None else selected_policy
    decision = evaluate_later_signal(
        opportunity=parent,
        prior=preceding,
        current=latest,
        policy=selected_policy,
        calendar=calendar(),
    )
    assert decision.status is LaterSignalStatus.ELIGIBLE_FOR_ONE_ATTEMPT
    assert decision.candidate is not None and decision.slot is not None
    attempt_id = decision.candidate.identity
    expert = ExpertEntryEligibility(
        attempt_id,
        parent.security_id,
        ExpertEntryAction.TRIAL_ENTRY,
        decision.candidate.later_signal_id,
        Decimal("0.30"),
    )
    contestant = replace(candidate(attempt_id, parent.security_id), expert_entry=expert)
    intent = entry_intent(
        service,
        candidate_entry=contestant,
        command_id=attempt_id,
        expected_revision=service.current(context(NOW)).account_revision,
        at=NOW,
    )
    return LaterEntryRequest(parent, preceding, latest, selected_policy, calendar(), intent)


def service(path: Path, *, fault_hook=None):
    bridge = build_funding_bridge_service(
        path, context=context(), authorization=AUTH, fault_hook=fault_hook
    )
    bridge.establish_baseline(baseline())
    return bridge


def test_a10_a13_later_command_replay_slot_and_atomic_failure(tmp_path: Path) -> None:
    path = tmp_path / "a10.sqlite"
    bridge = service(path)
    request = later_request(bridge, security_id="SYN001")
    first = bridge.enter_later(context(NOW), request)
    assert first.entry is not None and first.entry.accepted
    assert first.binding is not None
    duplicate = bridge.enter_later(context(NOW), request)
    assert duplicate.entry is not None and duplicate.entry.replayed
    assert duplicate.binding == first.binding
    reopened = service(path)
    recovered_receipt = reopened.enter_later(context(NOW), request)
    assert recovered_receipt.entry is not None and recovered_receipt.entry.replayed
    assert recovered_receipt.entry.result_digest == first.entry.result_digest
    assert recovered_receipt.binding == first.binding
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM bridge_later_attempts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM bridge_journal").fetchone()[0] == 1

    fault_path = tmp_path / "fault.sqlite"
    calls: list[str] = []

    def fail(stage: str) -> None:
        if stage == "after_later_slot_before_entry" and not calls:
            calls.append(stage)
            raise RuntimeError("later entry fault")

    fault_bridge = service(fault_path, fault_hook=fail)
    fault_request = later_request(fault_bridge)
    before = fault_bridge.current(context(NOW))
    with pytest.raises(RuntimeError, match="later entry fault"):
        fault_bridge.enter_later(context(NOW), fault_request)
    assert fault_bridge.current(context(NOW)) == before
    with sqlite3.connect(fault_path) as db:
        assert db.execute("SELECT count(*) FROM bridge_later_attempts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM bridge_journal").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM bridge_obligations").fetchone()[0] == 0
    recovered = service(fault_path)
    assert recovered.enter_later(context(NOW), fault_request).entry is not None


def test_a11_new_policy_cannot_consume_a_second_slot(tmp_path: Path) -> None:
    path = tmp_path / "a11.sqlite"
    bridge = service(path)
    first_request = later_request(bridge)
    assert bridge.enter_later(context(NOW), first_request).entry is not None
    current_state = bridge.current(context(NOW))
    v2 = replace(policy(), policy_version="c13-synthetic-qualified-v2")
    second = later_request(bridge, selected_policy=v2)
    with pytest.raises(FactError, match="LATER_ATTEMPT_ALREADY_CONSUMED"):
        bridge.enter_later(context(NOW), second)
    assert bridge.current(context(NOW)) == current_state
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM bridge_later_attempts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM bridge_obligations").fetchone()[0] == 1


def test_a13_two_public_callers_leave_one_accepted_reservation(tmp_path: Path) -> None:
    path = tmp_path / "a13-race.sqlite"
    first = service(path)
    second = service(path)
    request_a = later_request(first)
    request_b = later_request(
        second, selected_policy=replace(policy(), policy_version="c13-synthetic-qualified-v2")
    )
    assert request_a.intent.expected_account_revision == request_b.intent.expected_account_revision
    ready = Barrier(2)

    def compete(bridge, request):
        ready.wait(timeout=5)
        try:
            result = bridge.enter_later(context(NOW), request)
        except (FactError, ProductDailyError) as exc:
            return str(exc).split(":", 1)[0]
        assert result.entry is not None and result.entry.accepted
        return "ACCEPTED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(compete, first, request_a)
        b = pool.submit(compete, second, request_b)
        outcomes = [a.result(timeout=10), b.result(timeout=10)]

    assert outcomes.count("ACCEPTED") == 1
    loser = next(value for value in outcomes if value != "ACCEPTED")
    assert loser in {
        "ACCOUNT_REVISION_CONFLICT",
        "LATER_ATTEMPT_ALREADY_CONSUMED",
        "RISK_SNAPSHOT_STALE",
    }
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM bridge_later_attempts").fetchone()[0] == 1
        assert (
            db.execute("SELECT count(*) FROM bridge_journal WHERE kind='entry'").fetchone()[0] == 1
        )
        assert db.execute("SELECT count(*) FROM bridge_obligations").fetchone()[0] == 1
        assert (
            db.execute("SELECT count(*) FROM bridge_entry_receipts WHERE accepted=1").fetchone()[0]
            == 1
        )


def test_a12_unresolved_original_fill_is_preserved_and_blocks_without_slot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "a12-before.sqlite"
    bridge = service(path)
    old_report = buy_report("old-late-fill", "SYN001", at=NOW, source_sequence="old-late-fill")
    with pytest.raises(FactError):
        bridge.submit_buy(context(NOW), old_report, buy_profile("old-late-fill"))
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT status FROM bridge_inbox WHERE report_id='old-late-fill'"
            ).fetchone()[0]
            == "RECEIVED"
        )
    with pytest.raises(FactError, match="C13_ORIGINAL_FACT_UNRESOLVED"):
        bridge.enter_later(context(NOW), later_request(bridge))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM bridge_later_attempts").fetchone()[0] == 0


def test_a12_later_reservation_retains_hold_on_unmatched_old_fact(tmp_path: Path) -> None:
    path = tmp_path / "a12-after.sqlite"
    bridge = service(path)
    request = later_request(bridge, security_id="SYN001")
    result = bridge.enter_later(context(NOW), request)
    assert result.entry is not None and result.entry.accepted
    before = bridge.current(context(NOW))
    quantity = result.entry.quantity
    with sqlite3.connect(path) as db:
        before_journals = db.execute("SELECT count(*) FROM bridge_journal").fetchone()[0]
    old_report = replace(
        buy_report("old-after-fill", "SYN001", at=NOW, source_sequence="old-after-fill"),
        quantity=quantity,
        intended_quantity=quantity,
    )
    profile = replace(
        buy_profile("old-after-fill"),
        order_total_quantity=quantity,
        final_cumulative_fill_quantity=quantity,
    )
    for _ in range(2):
        with pytest.raises(FactError, match="C13_ORDER_ATTRIBUTION_REQUIRED"):
            bridge.submit_buy(context(NOW), old_report, profile)
    assert bridge.current(context(NOW)) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM bridge_journal").fetchone()[0] == before_journals
        assert db.execute("SELECT count(*) FROM bridge_later_attempts").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM bridge_obligations").fetchone()[0] == 1
        assert db.execute("SELECT status FROM bridge_obligations").fetchone()[0] == "OPEN"
        assert db.execute("SELECT status FROM bridge_inbox").fetchone()[0] == "RECEIVED"
        assert (
            db.execute("SELECT state FROM bridge_conflict_register").fetchone()[0] == "UNRESOLVED"
        )
        assert (
            db.execute(
                "SELECT count(*) FROM bridge_outbox WHERE report_id=?",
                ("c13-unattributed-buy:old-after-fill",),
            ).fetchone()[0]
            == 1
        )
