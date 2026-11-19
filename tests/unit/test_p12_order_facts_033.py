"""Public P12-W1W2-ORDER-FACT-v1 acceptance, including disk restart and cold ledger replay."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.capabilities.execution.order_evidence import (
    OrderWindow,
    SourceExecution,
    SourceOrder,
    SyntheticOrderSource,
    WindowStatement,
)
from quanthus.platform.adapters.product_daily_cycle_sqlite import build_funding_bridge_service
from quanthus.product_daily.application.funding_bridge import (
    LaterEntryRequest,
    SyntheticTemporalProfile,
)
from quanthus.product_daily.c13_later_signal import OriginalAttemptState, evaluate_later_signal
from quanthus.product_daily.risk import (
    ExpertEntryAction,
    ExpertEntryEligibility,
    pending_cash_commitment,
)
from quanthus.shared.fact_contracts import FactError
from tests.unit.test_product_daily_c13_later_signal import (
    Eligibility,
    calendar,
    current,
    opportunity,
    policy,
    prior,
)
from tests.unit.test_product_daily_u04_c13_transaction import NOW, later_request
from tests.unit.test_product_daily_u05_two_cycle import (
    AUTH,
    baseline,
    buy_profile,
    buy_report,
    candidate,
    context,
    entry_intent,
)

START = datetime.fromisoformat("2026-11-17T09:30:00+08:00")
END = datetime.fromisoformat("2026-11-17T15:00:00+08:00")
BASE = START - timedelta(days=1)
RESERVE = START - timedelta(hours=1)
CLOSE = END + timedelta(minutes=2)
LATER_OPEN = datetime.fromisoformat("2026-11-19T09:30:00+08:00")
LATE = LATER_OPEN + timedelta(minutes=2)
PARENT = opportunity().identity
SEC = "SYN001"
WINDOW = OrderWindow(PARENT, SEC, START, END)
BROKER = "fixture:two-cycle:broker"


def attempt():
    decision = evaluate_later_signal(
        opportunity=opportunity(account_id=context().account_id, security_id=SEC),
        prior=prior(security_id=SEC),
        current=current(security_id=SEC),
        policy=policy(),
        calendar=calendar(),
    )
    assert decision.candidate is not None
    return decision.candidate.identity


def external_order(later=False, *, published=None, status="filled"):
    at = LATER_OPEN if later else START
    price_time = at + timedelta(seconds=1)
    return SourceOrder(
        "obs-L" if later else "obs-O",
        "O-LATER" if later else "O-ORIG",
        1,
        PARENT,
        attempt() if later else PARENT,
        attempt() if later else "original-entry",
        SEC,
        at,
        price_time,
        published or price_time,
        status,
        500,
        500 if status == "filled" else 0,
        0 if status == "filled" else 500,
        Decimal("5.00") if status == "filled" else Decimal("0.00"),
        True,
    )


def execution(order, sequence):
    return SourceExecution(
        sequence,
        order.order_id,
        order.revision,
        SEC,
        order.effective_at,
        order.available_at,
        500,
        Decimal("10"),
        Decimal("5"),
    )


def ledger(*, orders=(), fills=(), complete=True, listed=(), coverage_at=CLOSE, watermark=END):
    statement = WindowStatement(
        "source-window-scan", WINDOW, coverage_at, watermark, complete, listed
    )
    return SyntheticOrderSource(
        context().account_id,
        BROKER,
        "orders-fixture-v1",
        (WINDOW,),
        (statement,),
        tuple(orders),
        tuple(fills),
    )


def bridge_at(path, source, fault=None):
    profile = SyntheticTemporalProfile(
        "orders-synthetic-clocks", (RESERVE, NOW, START + timedelta(seconds=1), LATE)
    )
    bridge = build_funding_bridge_service(
        path,
        context=context(BASE),
        authorization=AUTH,
        order_source=source,
        temporal_profile=profile,
        fault_hook=fault,
    )
    bridge.establish_baseline(replace(baseline(), context=context(BASE)))
    return bridge


def reserve_original(bridge):
    intent = entry_intent(
        bridge,
        candidate_entry=candidate(PARENT, SEC),
        command_id="original-entry",
        expected_revision=0,
        at=RESERVE,
    )
    result = bridge.enter(context(RESERVE), intent)
    assert result.accepted and result.quantity == 500
    return result


def actual_buy(bridge, order, seq, at):
    report = buy_report("report-" + seq, SEC, at=order.effective_at, source_sequence=seq)
    return bridge.submit_buy(context(at), report, buy_profile(seq))


def snapshot(bridge, at):
    state = bridge.current(context(at))
    risk = bridge.risk_snapshot(context(at), mark_price=Decimal("10"))
    return {
        "cash": str(state.account.cash),
        "quantity": state.account.quantity(SEC),
        "revision": state.account_revision,
        "account_digest": state.account.digest,
        "funding_digest": state.funding.digest,
        "pending": [x.reservation_id for x in risk.pending_buys],
        "held_cash": format(pending_cash_commitment(risk).total, ".2f"),
    }


def witness(case, actual):
    directory = os.getenv("P12_033_ACTUALS")
    if directory:
        (Path(directory) / (case + ".json")).write_text(
            json.dumps(actual, sort_keys=True, indent=2) + "\n"
        )


def verify_rebuild(path, source, at, expected):
    reopened = bridge_at(path, source)
    rebuilt = reopened.rebuild(context(at))
    assert rebuilt.account.digest == expected["account_digest"]
    assert rebuilt.funding.digest == expected["funding_digest"]
    assert snapshot(reopened, at) == expected
    # A separate SQLite store has only source metadata, the baseline and immutable journal rows.
    # No economic projections, receipts, lots, slots, holds or outbox can supply expected answers.
    cold_path = path.with_name("cold-" + path.name)
    with sqlite3.connect(path) as src, sqlite3.connect(cold_path) as dest:
        src.backup(dest)
        names = [r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in names:
            if table.startswith("bridge_") and table not in {
                "bridge_order_source",
                "bridge_baseline",
                "bridge_journal",
            }:
                dest.execute(f'DELETE FROM "{table}"')
    cold = build_funding_bridge_service(
        cold_path, context=context(BASE), authorization=AUTH, order_source=source
    )
    restored = cold.reconstruct_order_account(context(at))
    assert restored.state.account.digest == expected["account_digest"]
    assert restored.state.funding.digest == expected["funding_digest"]
    assert [ob.reservation_id for ob in restored.obligations if ob.status == "OPEN"] == expected[
        "pending"
    ]
    for report_id, lineage in restored.reports.items():
        assert reopened.order_report(context(at), report_id) == lineage
    for request_id, receipt in restored.windows.items():
        assert reopened.observe_original(context(at), WINDOW, request_id=request_id) == receipt
    return reopened


def test_01_original_fill_before_later_entry(tmp_path):
    original = external_order()
    source = ledger(orders=[original], fills=[execution(original, "E-O")], listed=("O-ORIG",))
    path = tmp_path / "facts.sqlite"
    bridge = bridge_at(path, source)
    reservation = reserve_original(bridge)
    at = original.available_at
    fact = actual_buy(bridge, original, "E-O", at)
    assert fact.status == "POSTED"
    assert actual_buy(bridge, original, "E-O", at) == fact
    provenance = bridge.order_report(context(at), "report-E-O")
    assert provenance["lineage"]["reservation_id"] == reservation.reservation_id
    assert provenance["lineage"]["attempt_id"] == PARENT
    before = snapshot(bridge, NOW)
    assert before["cash"] == "15995.00" and before["quantity"] == 500 and not before["pending"]
    with pytest.raises(FactError, match="C13_ORIGINAL_FILL_PRESENT"):
        bridge.enter_later(context(NOW), later_request(bridge, security_id=SEC))
    assert snapshot(bridge, NOW) == before
    reopened = verify_rebuild(path, source, NOW, before)
    assert reopened.order_report(context(NOW), "report-E-O") == provenance
    witness(
        "01-original-before",
        {"order": provenance, "state": before, "later": "blocked", "rebuild": "equal"},
    )


def test_02_original_arrives_after_later_hold(tmp_path):
    # Source later reports a previously unseen original. Both economics match the valid later fill.
    original = external_order(published=LATE)
    later = external_order(True)
    source = ledger(
        orders=[original, later], fills=[execution(original, "E-O"), execution(later, "E-L")]
    )
    path = tmp_path / "facts.sqlite"
    bridge = bridge_at(path, source)
    result = bridge.enter_later(context(NOW), later_request(bridge, security_id=SEC))
    hold = result.entry.reservation_id
    initial = snapshot(bridge, NOW)
    for _ in range(2):
        with pytest.raises(FactError, match="C13_ORIGINAL_FILL_AFTER_LATER_RESERVATION"):
            actual_buy(bridge, original, "E-O", LATE)
    provenance = bridge.order_report(context(LATE), "report-E-O")
    assert provenance["status"] == "NEEDS_RECONCILIATION"
    assert (
        provenance["lineage"]["order_id"] == "O-ORIG"
        and provenance["lineage"]["attempt_id"] == PARENT
    )
    after = snapshot(bridge, LATE)
    assert after == initial and after["pending"] == [hold] and after["held_cash"] == "5005.00"
    assert (
        verify_rebuild(path, source, LATE, after).order_report(context(LATE), "report-E-O")
        == provenance
    )
    witness("02-original-late", {"order": provenance, "state": after, "rebuild": "equal"})


def test_03_matching_later_fill_consumes_only_own_hold(tmp_path):
    later = external_order(True)
    original = external_order(published=LATE)
    source = ledger(
        orders=[original, later], fills=[execution(original, "E-O"), execution(later, "E-L")]
    )
    path = tmp_path / "facts.sqlite"
    bridge = bridge_at(path, source)
    reservation = bridge.enter_later(context(NOW), later_request(bridge, security_id=SEC)).entry
    with pytest.raises(FactError, match="C13_ORIGINAL_FILL_AFTER_LATER_RESERVATION"):
        actual_buy(bridge, original, "E-O", LATE)
    original_fact = bridge.order_report(context(LATE), "report-E-O")
    posted = actual_buy(bridge, later, "E-L", LATE)
    assert posted.status == "POSTED"
    assert actual_buy(bridge, later, "E-L", LATE) == posted
    later_fact = bridge.order_report(context(LATE), "report-E-L")
    assert later_fact["lineage"]["order_id"] == "O-LATER"
    assert later_fact["lineage"]["reservation_id"] == reservation.reservation_id
    assert later_fact["lineage"]["attempt_id"] == attempt()
    assert bridge.order_report(context(LATE), "report-E-O") == original_fact
    after = snapshot(bridge, LATE)
    assert after["cash"] == "15995.00" and after["quantity"] == 500 and not after["pending"]
    reopened = verify_rebuild(path, source, LATE, after)
    assert reopened.order_report(context(LATE), "report-E-L") == later_fact
    witness(
        "03-later-fill",
        {"order": later_fact, "state": after, "original": original_fact, "rebuild": "equal"},
    )


def test_04_complete_window_no_submission(tmp_path):
    source = ledger()
    path = tmp_path / "facts.sqlite"
    bridge = bridge_at(path, source)
    evidence = bridge.observe_original(context(CLOSE), WINDOW, request_id="scan")
    assert (evidence["status"], evidence["order_id"], evidence["source_watermark"]) == (
        "NEVER_SUBMITTED",
        None,
        END.isoformat(),
    )
    assert evidence["coverage_receipt"] == "source-window-scan"
    assert bridge.observe_original(context(CLOSE), WINDOW, request_id="scan") == evidence
    before = snapshot(bridge, CLOSE)
    assert before["revision"] == 0 and not before["pending"]
    assert bridge.enter_later(context(NOW), later_request(bridge, security_id=SEC)).entry.accepted
    after = snapshot(bridge, NOW)
    assert after["quantity"] == 0 and len(after["pending"]) == 1
    verify_rebuild(path, source, NOW, after)
    witness("04-no-submission", {"coverage": evidence, "state": after, "rebuild": "equal"})


def test_05_terminal_zero_reconciles_original_before_later(tmp_path):
    order = replace(external_order(status="cancelled"), fee=Decimal("1.25"))
    source = ledger(orders=[order], listed=(order.order_id,))
    path = tmp_path / "facts.sqlite"
    bridge = bridge_at(path, source)
    original = reserve_original(bridge)
    evidence = bridge.observe_original(context(CLOSE), WINDOW, request_id="terminal")
    assert (
        evidence["status"],
        evidence["reservation_id"],
        evidence["released"],
        evidence["fee_posted"],
    ) == ("TERMINAL_ZERO_FILL_RECONCILED", original.reservation_id, True, "1.25")
    assert bridge.observe_original(context(CLOSE), WINDOW, request_id="terminal") == evidence
    after = snapshot(bridge, CLOSE)
    assert after["cash"] == "20998.75" and after["quantity"] == 0 and not after["pending"]
    request = later_request(bridge, security_id=SEC)
    request = replace(
        request,
        opportunity=replace(
            request.opportunity,
            original_attempt_state=OriginalAttemptState.TERMINAL_ZERO_FILL_RECONCILED,
        ),
    )
    assert bridge.enter_later(context(NOW), request).entry.accepted
    final = snapshot(bridge, NOW)
    verify_rebuild(path, source, NOW, final)
    witness(
        "05-terminal-zero",
        {"observation": evidence, "after_zero": after, "after_later": final, "rebuild": "equal"},
    )


def test_terminal_zero_cannot_close_a_reservation_created_after_submission(tmp_path):
    order = replace(external_order(status="cancelled"), fee=Decimal("1.25"))
    source = ledger(orders=(order,), listed=(order.order_id,))
    path = tmp_path / "zero-before-reservation.sqlite"
    bridge = bridge_at(path, source)
    reserved_at = START + timedelta(seconds=1)
    reservation = bridge.enter(
        context(reserved_at),
        entry_intent(
            bridge,
            candidate_entry=candidate(PARENT, SEC),
            command_id="original-entry",
            expected_revision=0,
            at=reserved_at,
        ),
    )
    assert reservation.accepted and order.submitted_at < reserved_at
    before = snapshot(bridge, CLOSE)
    assert before["cash"] == "21000.00" and before["pending"] == [reservation.reservation_id]
    receipt = bridge.observe_original(context(CLOSE), WINDOW, request_id="late-reservation")
    assert (receipt["status"], receipt["reason"], receipt["released"], receipt["fee_posted"]) == (
        "NEEDS_RECONCILIATION",
        "ORDER_RESERVATION_TIME_MISMATCH",
        False,
        "0.00",
    )
    assert snapshot(bridge, CLOSE) == before
    verify_rebuild(path, source, CLOSE, before)


@pytest.mark.parametrize(
    "problem",
    ["partial-coverage", "short-watermark", "bad-total", "open-fee", "correction", "wrong-window"],
)
def test_06_unknown_or_conflicting_keeps_hold(tmp_path, problem):
    original = external_order(status="cancelled")
    if problem == "bad-total":
        original = replace(original, cancelled=499)
    if problem == "open-fee":
        original = replace(original, fee=None)
    if problem == "correction":
        original = replace(original, corrections_clear=False)
    source = ledger(
        orders=[original],
        listed=(original.order_id,),
        complete=problem != "partial-coverage",
        watermark=END - timedelta(seconds=1) if problem == "short-watermark" else END,
    )
    if problem == "wrong-window":
        source = replace(
            source,
            statements=(
                replace(
                    source.statements[0], window=replace(WINDOW, start=START + timedelta(seconds=1))
                ),
            ),
        )
    path = tmp_path / "facts.sqlite"
    bridge = bridge_at(path, source)
    reserve_original(bridge)
    before = snapshot(bridge, CLOSE)
    observed = bridge.observe_original(context(CLOSE), WINDOW, request_id="scan")
    assert observed["status"] == "NEEDS_RECONCILIATION"
    with pytest.raises(FactError, match="C13_ORIGINAL_ORDER_FACT_REQUIRED"):
        bridge.enter_later(context(NOW), later_request(bridge, security_id=SEC))
    assert snapshot(bridge, NOW) == before and len(before["pending"]) == 1
    verify_rebuild(path, source, NOW, before)
    witness(
        "06-" + problem,
        {"receipt": observed, "state": before, "later": "blocked", "rebuild": "equal"},
    )


def test_fault_between_zero_fee_and_account_reuses_received_request(tmp_path):
    def fault(stage):
        if stage == "after_zero_funding_before_account":
            raise RuntimeError("simulated crash")

    order = replace(external_order(status="cancelled"), fee=Decimal("1.25"))
    source = ledger(orders=[order], listed=(order.order_id,))
    path = tmp_path / "crash.sqlite"
    bridge = bridge_at(path, source, fault)
    reserve_original(bridge)
    before = snapshot(bridge, CLOSE)
    with pytest.raises(RuntimeError, match="simulated crash"):
        bridge.observe_original(context(CLOSE), WINDOW, request_id="terminal")
    assert snapshot(bridge, CLOSE) == before
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT status FROM bridge_operations WHERE kind='order_window'").fetchone()[
                0
            ]
            == "RECEIVED"
        )
    reopened = bridge_at(path, source)
    receipt = reopened.observe_original(context(CLOSE), WINDOW, request_id="terminal")
    assert receipt["released"] and snapshot(reopened, CLOSE)["cash"] == "20998.75"
    assert reopened.rebuild(context(CLOSE)) == reopened.current(context(CLOSE))


def test_bound_source_cannot_be_downgraded_by_reopen_or_old_handle(tmp_path):
    path = tmp_path / "pin.sqlite"
    stale = build_funding_bridge_service(path, context=context(BASE), authorization=AUTH)
    source = ledger()
    bridge = bridge_at(path, source)
    with pytest.raises(FactError, match="ORDER_SOURCE_MISMATCH"):
        build_funding_bridge_service(path, context=context(BASE), authorization=AUTH)
    with pytest.raises(FactError, match="ORDER_SOURCE_MISMATCH"):
        bridge_at(path, replace(source, version="other-generation"))
    with pytest.raises(FactError, match="ORDER_SOURCE_MISMATCH"):
        stale.current(context(NOW))
    with pytest.raises(FactError, match="UNAUTHORIZED_CONTEXT"):
        bridge.order_report(replace(context(NOW), account_id="SYN-OTHER"), "r")
    empty = build_funding_bridge_service(
        tmp_path / "legacy", context=context(BASE), authorization=AUTH
    )
    empty.establish_baseline(replace(baseline(), context=context(BASE)))
    with pytest.raises(FactError, match="C13_ORIGINAL_ORDER_FACT_REQUIRED"):
        empty.enter_later(context(NOW), later_request(empty, security_id=SEC))


def test_correction_visible_after_decision_blocks_changed_terminal_and_fee(tmp_path):
    order = replace(external_order(status="cancelled"), fee=Decimal("1"))
    corrected = replace(
        order,
        observation_id="corrected-fee",
        supersedes=order.observation_id,
        available_at=NOW,
        fee=Decimal("2"),
    )
    source = ledger(orders=[order, corrected], listed=(order.order_id,))
    path = tmp_path / "corrected"
    bridge = bridge_at(path, source)
    reserve_original(bridge)
    first = bridge.observe_original(context(CLOSE), WINDOW, request_id="v1")
    assert first["fee_posted"] == "1.00"
    updated = bridge.observe_original(context(NOW), WINDOW, request_id="v2")
    assert updated["status"] == "NEEDS_RECONCILIATION"
    request = later_request(bridge, security_id=SEC)
    request = replace(
        request,
        opportunity=replace(
            request.opportunity,
            original_attempt_state=OriginalAttemptState.TERMINAL_ZERO_FILL_RECONCILED,
        ),
    )
    with pytest.raises(FactError, match="C13_ORIGINAL_ORDER_FACT_REQUIRED"):
        bridge.enter_later(context(NOW), request)
    assert snapshot(bridge, NOW)["cash"] == "20999.00"


def test_execution_visible_only_after_decision_and_unseen_identity_hold(tmp_path):
    original = external_order(published=LATE)
    event = replace(execution(original, "old"), available_at=NOW)
    source = ledger(orders=[original], fills=[event])
    bridge = bridge_at(tmp_path / "orphan", source)
    missing = bridge.observe_original(context(NOW), WINDOW, request_id="o")
    assert missing["status"] == "NEEDS_RECONCILIATION"
    with pytest.raises(FactError, match="C13_ORIGINAL_ORDER_FACT_REQUIRED"):
        bridge.enter_later(context(NOW), later_request(bridge, security_id=SEC))
    late_statement = ledger(coverage_at=NOW + timedelta(seconds=1))
    bridge = bridge_at(tmp_path / "future", late_statement)
    with pytest.raises(FactError, match="C13_ORIGINAL_ORDER_FACT_REQUIRED"):
        bridge.enter_later(
            context(NOW + timedelta(seconds=1)), later_request(bridge, security_id=SEC)
        )


def test_posting_failure_keeps_receipt_then_retry_posts_one_order(tmp_path):
    order = external_order(True)
    source = ledger(orders=[order], fills=[execution(order, "E-L")])
    path = tmp_path / "buy-crash"

    def fail(stage):
        if stage == "after_funding_before_account":
            raise RuntimeError("buy crash")

    bridge = bridge_at(path, source, fail)
    result = bridge.enter_later(context(NOW), later_request(bridge, security_id=SEC))
    with pytest.raises(RuntimeError, match="buy crash"):
        actual_buy(bridge, order, "E-L", LATE)
    assert snapshot(bridge, LATE)["pending"] == [result.entry.reservation_id]
    assert bridge.order_report(context(LATE), "report-E-L") is None
    reopened = bridge_at(path, source)
    assert actual_buy(reopened, order, "E-L", LATE).status == "POSTED"
    assert reopened.rebuild(context(LATE)) == reopened.current(context(LATE))


def test_replay_after_new_evidence_retains_result_and_original_window(tmp_path):
    original = external_order(published=LATE)
    bridge = bridge_at(
        tmp_path / "retry", ledger(orders=[original], fills=[execution(original, "E-O")])
    )
    req = later_request(bridge, security_id=SEC)
    first = bridge.enter_later(context(NOW), req)
    retried = bridge.enter_later(context(LATE), req)
    assert retried.entry.replayed and retried.binding == first.binding
    changed = replace(
        req, opportunity=replace(req.opportunity, original_window_end=END - timedelta(hours=1))
    )
    with pytest.raises(FactError, match="IDEMPOTENCY_CONFLICT"):
        bridge.enter_later(context(LATE), changed)


def test_incomplete_raw_report_holds_zero_settlement(tmp_path):
    order = external_order(status="cancelled")
    source = ledger(orders=[order], listed=(order.order_id,))
    path = tmp_path / "unresolved"
    bridge = bridge_at(path, source)
    original = reserve_original(bridge)
    unknown = buy_report("unknown", SEC, at=START + timedelta(seconds=1), source_sequence="absent")
    with pytest.raises(FactError, match="C13_ORDER_ATTRIBUTION_REQUIRED"):
        bridge.submit_buy(context(START + timedelta(seconds=1)), unknown, buy_profile("absent"))
    observation = bridge.observe_original(context(CLOSE), WINDOW, request_id="scan")
    assert observation["status"] == "NEEDS_RECONCILIATION"
    assert snapshot(bridge, CLOSE)["pending"] == [original.reservation_id]
    assert bridge.rebuild(context(CLOSE)) == bridge.current(context(CLOSE))


def test_correction_between_receive_and_business_retry_preserves_original_hold(tmp_path):
    order = external_order(status="cancelled")
    correction = replace(
        order,
        observation_id="corrected",
        supersedes=order.observation_id,
        available_at=NOW,
        corrections_clear=False,
    )
    feed = ledger(orders=[order, correction], listed=(order.order_id,))
    path = tmp_path / "fault-correction"

    def fail(stage):
        if stage == "after_zero_funding_before_account":
            raise RuntimeError("fault")

    bridge = bridge_at(path, feed, fail)
    original = reserve_original(bridge)
    with pytest.raises(RuntimeError):
        bridge.observe_original(context(CLOSE), WINDOW, request_id="zero")
    reopened = bridge_at(path, feed)
    with pytest.raises(FactError, match="ORDER_WINDOW_CHANGED"):
        reopened.observe_original(context(NOW), WINDOW, request_id="zero")
    result = reopened.observe_original(context(NOW), WINDOW, request_id="corrected")
    assert result["status"] == "NEEDS_RECONCILIATION"
    assert snapshot(reopened, NOW)["pending"] == [original.reservation_id]


def test_two_visible_fills_for_one_complete_fill_order_need_reconciliation(tmp_path):
    order = external_order()
    feed = ledger(
        orders=[order],
        fills=[execution(order, "fill-a"), execution(order, "fill-b")],
        listed=(order.order_id,),
    )
    bridge = bridge_at(tmp_path / "extra-fill", feed)
    original = reserve_original(bridge)
    with pytest.raises(FactError, match="ORDER_SOURCE_CONFLICT"):
        actual_buy(bridge, order, "fill-a", order.available_at)
    state = snapshot(bridge, order.available_at)
    assert state["pending"] == [original.reservation_id] and state["quantity"] == 0


def test_correction_fill_after_confirmed_zero_retains_both_reservation_identities(tmp_path):
    cancelled = external_order(status="cancelled")
    corrected = replace(
        external_order(published=LATE),
        observation_id="corrected-to-filled",
        supersedes=cancelled.observation_id,
    )
    source = ledger(
        orders=[cancelled, corrected],
        fills=[execution(corrected, "old")],
        listed=(cancelled.order_id,),
    )
    path = tmp_path / "two-known-reservations"
    bridge = bridge_at(path, source)
    old_hold = reserve_original(bridge).reservation_id
    zero = bridge.observe_original(context(CLOSE), WINDOW, request_id="zero")
    assert zero["reservation_id"] == old_hold and zero["released"]
    req = later_request(bridge, security_id=SEC)
    req = replace(
        req,
        opportunity=replace(
            req.opportunity,
            original_attempt_state=OriginalAttemptState.TERMINAL_ZERO_FILL_RECONCILED,
        ),
    )
    later_hold = bridge.enter_later(context(NOW), req).entry.reservation_id
    assert later_hold != old_hold
    with pytest.raises(FactError, match="C13_ORIGINAL_FILL_AFTER_LATER_RESERVATION"):
        actual_buy(bridge, corrected, "old", LATE)
    lineage = bridge.order_report(context(LATE), "report-old")["lineage"]
    assert lineage["order_id"] == cancelled.order_id and lineage["reservation_id"] == old_hold
    state = snapshot(bridge, LATE)
    assert state["pending"] == [later_hold] and state["cash"] == "21000.00"
    verify_rebuild(path, source, LATE, state)
    witness(
        "02b-original-corrected",
        {
            "old_reservation": old_hold,
            "later_reservation": later_hold,
            "source_lineage": lineage,
            "after": state,
            "cold_replay": "equal",
        },
    )


def test_parent_label_cannot_consume_another_parent_slot(tmp_path):
    proper = external_order(True)
    cross = replace(proper, parent_id=proper.attempt_id)
    feed = ledger(orders=[cross], fills=[execution(cross, "cross")])
    path = tmp_path / "wrong-parent"
    bridge = bridge_at(path, feed)
    reserved = bridge.enter_later(context(NOW), later_request(bridge, security_id=SEC)).entry
    with pytest.raises(FactError, match="C13_ATTEMPT_BINDING_MISMATCH"):
        actual_buy(bridge, cross, "cross", LATE)
    final = snapshot(bridge, LATE)
    assert final["pending"] == [reserved.reservation_id] and final["quantity"] == 0
    verify_rebuild(path, feed, LATE, final)


def _later_source(order_status="cancelled", *, fee=Decimal("2.50"), extra=()):
    original_zero = replace(
        external_order(status="cancelled"), fee=Decimal("1.00"), corrections_clear=True
    )
    cancelled = replace(
        external_order(later=True, published=LATE),
        status=order_status,
        filled=0,
        cancelled=500,
        fee=fee,
    )
    listed = (original_zero.order_id, cancelled.order_id)
    source = ledger(orders=[original_zero, cancelled, *extra], listed=listed)
    source = replace(
        source,
        statements=(
            replace(
                source.statements[0],
                order_ids=(original_zero.order_id,),
            ),
        ),
    )
    return source, cancelled


def _admit_later(bridge):
    original = reserve_original(bridge)
    evidence = bridge.observe_original(context(CLOSE), WINDOW, request_id="terminal")
    assert evidence["released"] and evidence["reservation_id"] == original.reservation_id
    request = later_request(bridge, security_id=SEC)
    request = replace(
        request,
        opportunity=replace(
            request.opportunity,
            original_attempt_state=OriginalAttemptState.TERMINAL_ZERO_FILL_RECONCILED,
        ),
    )
    entry = bridge.enter_later(context(NOW), request)
    assert entry.entry is not None and entry.entry.accepted
    return entry


def test_a11_later_terminal_zero_releases_only_its_attempt_and_slot_stays_consumed(tmp_path):
    source, order = _later_source()
    path = tmp_path / "a11.sqlite"
    bridge = bridge_at(path, source)
    entry = _admit_later(bridge)
    hold = entry.entry.reservation_id
    before = snapshot(bridge, LATE)
    assert before["pending"] == [hold] and before["held_cash"] == "5005.00"
    # A new eligible candidate under a newer policy cannot take the consumed slot.
    v2 = replace(
        policy(),
        policy_version="c13-synthetic-qualified-v2",
        expiry=datetime.fromisoformat("2026-11-20T10:00:00+08:00"),
    )
    second = later_request(bridge, selected_policy=v2, security_id=SEC)
    second = replace(
        second,
        opportunity=replace(
            second.opportunity,
            original_attempt_state=OriginalAttemptState.TERMINAL_ZERO_FILL_RECONCILED,
        ),
    )
    with pytest.raises(FactError, match="LATER_ATTEMPT_ALREADY_CONSUMED"):
        bridge.enter_later(context(NOW), second)
    assert snapshot(bridge, NOW)["pending"] == [hold]
    receipt = bridge.observe_later(context(LATE), attempt(), request_id="later-terminal")
    assert (
        receipt["status"],
        receipt["reservation_id"],
        receipt["released"],
        receipt["fee_posted"],
        receipt["order_id"],
    ) == ("TERMINAL_ZERO_FILL_RECONCILED", hold, True, "2.50", order.order_id)
    assert bridge.observe_later(context(LATE), attempt(), request_id="later-terminal") == receipt
    after = snapshot(bridge, LATE)
    assert after["cash"] == "20996.50" and after["quantity"] == 0 and not after["pending"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM bridge_later_attempts").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT status FROM bridge_obligations WHERE obligation_id=?",
                (receipt["reservation_id"],),
            ).fetchone()[0]
            == "CANCELLED"
        )
    with sqlite3.connect(path) as db:
        occupied = db.execute(
            "SELECT slot_identity,attempt_identity FROM bridge_later_attempts"
        ).fetchall()
    assert len(occupied) == 1 and occupied[0][1] == attempt()
    # Journal rebuild restores the occupied slot after the store table is wiped.
    with sqlite3.connect(path) as db:
        stored_slots = db.execute("SELECT * FROM bridge_later_attempts").fetchall()
        db.execute("DELETE FROM bridge_later_attempts")
    reopened = verify_rebuild(path, source, LATE, after)
    assert reopened.observe_later(context(LATE), attempt(), request_id="later-terminal") == receipt
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM bridge_later_attempts").fetchall() == stored_slots
    cold_path = path.with_name("cold-" + path.name)
    cold = build_funding_bridge_service(
        cold_path, context=context(BASE), authorization=AUTH, order_source=source
    )
    restored = cold.reconstruct_order_account(context(LATE))
    assert restored.terminals["later-terminal"] == receipt
    witness("a11-later-terminal", {"receipt": receipt, "state": after, "rebuild": "equal"})


def test_a11_later_terminal_keeps_slot_for_a_new_eligible_session_after_rebuild(tmp_path):
    source, _ = _later_source()
    path = tmp_path / "a11-next-session.sqlite"
    decision_at = LATER_OPEN.replace(hour=15, minute=10)
    temporal_profile = SyntheticTemporalProfile(
        "a11-two-session-witness", (RESERVE, NOW, LATE, decision_at)
    )

    def build():
        return build_funding_bridge_service(
            path,
            context=context(BASE),
            authorization=AUTH,
            order_source=source,
            temporal_profile=temporal_profile,
        )

    bridge = build()
    bridge.establish_baseline(replace(baseline(), context=context(BASE)))
    first = _admit_later(bridge)
    terminal = bridge.observe_later(context(LATE), attempt(), request_id="terminal-first")
    assert terminal["released"] and terminal["reservation_id"] == first.entry.reservation_id
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM bridge_later_attempts")
    bridge = build()
    bridge.rebuild(context(decision_at))
    second_policy = replace(
        policy(),
        policy_version="test-two-session-v2",
        observation_lifetime_sessions=2,
        expiry=(LATER_OPEN + timedelta(days=1)).replace(hour=10),
    )
    parent = opportunity(
        account_id=context().account_id,
        security_id=SEC,
        original_attempt_state=OriginalAttemptState.TERMINAL_ZERO_FILL_RECONCILED,
    )
    preceding = current(security_id=SEC, eligibility=Eligibility.EXPLICIT_FALSE)
    latest = current(
        security_id=SEC,
        signal_session=decision_at.date(),
        feature_session=decision_at.date(),
        evidence_at=decision_at - timedelta(minutes=10),
        available_at=decision_at - timedelta(minutes=5),
        decision_cutoff=decision_at,
        evidence_id="a11-next-session",
    )
    planned = evaluate_later_signal(
        opportunity=parent,
        prior=preceding,
        current=latest,
        policy=second_policy,
        calendar=calendar(),
    )
    assert planned.candidate is not None and planned.candidate.identity != attempt()
    next_id = planned.candidate.identity
    expert = ExpertEntryEligibility(
        next_id,
        SEC,
        ExpertEntryAction.TRIAL_ENTRY,
        planned.candidate.later_signal_id,
        Decimal("0.30"),
    )
    before = snapshot(bridge, decision_at)
    request = LaterEntryRequest(
        parent,
        preceding,
        latest,
        second_policy,
        calendar(),
        intent=entry_intent(
            bridge,
            candidate_entry=replace(candidate(next_id, SEC), expert_entry=expert),
            command_id=next_id,
            expected_revision=before["revision"],
            at=decision_at,
        ),
    )
    with pytest.raises(FactError, match="LATER_ATTEMPT_ALREADY_CONSUMED"):
        bridge.enter_later(context(decision_at), request)
    assert snapshot(bridge, decision_at) == before


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("wrong-attempt", "C13_LATER_ORDER_AMBIGUOUS"),
        ("ambiguous", "C13_LATER_ORDER_AMBIGUOUS"),
        ("open", "LATER_ORDER_UNRECONCILED"),
        ("filled", "LATER_FILL_RECORDED"),
        ("wrong-command", "C13_ATTEMPT_BINDING_MISMATCH"),
    ],
)
def test_a11_wrong_malformed_or_ambiguous_terminal_has_no_effects(tmp_path, mutation, reason):
    original_zero = replace(
        external_order(status="cancelled"), fee=Decimal("1.00"), corrections_clear=True
    )
    if mutation == "ambiguous":
        base = replace(
            external_order(later=True, published=LATE),
            status="cancelled",
            filled=0,
            cancelled=500,
            fee=Decimal("2.50"),
        )
        other = replace(
            base, observation_id="obs-other", order_id="O-LATER-2", command_id="other-command"
        )
        source = ledger(
            orders=[original_zero, base, other],
            listed=(original_zero.order_id,),
        )
    elif mutation == "open":
        source, _ = _later_source("open", fee=None)
    elif mutation == "filled":
        filled = external_order(later=True, published=LATE)
        source = ledger(
            orders=[original_zero, filled],
            listed=(original_zero.order_id,),
            fills=[execution(filled, "E-L")],
        )
    elif mutation == "wrong-command":
        order = replace(
            external_order(later=True, published=LATE),
            status="cancelled",
            filled=0,
            cancelled=500,
            fee=Decimal("2.50"),
            command_id="another-command",
        )
        source = ledger(orders=[original_zero, order], listed=(original_zero.order_id,))
    else:
        source, _ = _later_source()
    path = tmp_path / f"a11-{mutation}.sqlite"
    bridge = bridge_at(path, source)
    entry = _admit_later(bridge)
    before = snapshot(bridge, LATE)
    target = "unknown-attempt" if mutation == "wrong-attempt" else attempt()
    receipt = bridge.observe_later(context(LATE), target, request_id=f"later-{mutation}")
    expected_status = "ANY_FILL" if mutation == "filled" else "NEEDS_RECONCILIATION"
    assert (receipt["status"], receipt["reason"], receipt["released"], receipt["fee_posted"]) == (
        expected_status,
        reason,
        False,
        "0.00",
    )
    assert snapshot(bridge, LATE) == before and before["pending"] == [entry.entry.reservation_id]
    assert bridge.observe_later(context(LATE), target, request_id=f"later-{mutation}") == receipt
    verify_rebuild(path, source, LATE, before)
    witness(f"a11-{mutation}", {"receipt": receipt, "state": before, "rebuild": "equal"})


def test_a11_terminal_validates_the_persisted_slot_against_the_journal(tmp_path):
    source, _ = _later_source()
    path = tmp_path / "a11-different-slot-clock.sqlite"
    bridge = bridge_at(path, source)
    _admit_later(bridge)
    before = snapshot(bridge, LATE)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE bridge_later_attempts SET created_at=?",
            ((NOW - timedelta(minutes=1)).isoformat(),),
        )
    with pytest.raises(FactError, match="STORE_PROJECTION_MISMATCH"):
        bridge.observe_later(context(LATE), attempt(), request_id="clock-corruption")
    assert snapshot(bridge, LATE) == before
