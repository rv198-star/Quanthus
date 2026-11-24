"""Account membership survives per-security market failures in the raw entry point."""

from dataclasses import replace
from datetime import datetime, time

import pytest

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.daily_runner import RawSecurityInput
from tests.unit import test_p11_daily_runner as original
from tests.unit.test_product_daily_rule_seam import _p12_loader


@pytest.fixture
def runner():
    return original.runner.__wrapped__()


@pytest.fixture
def resources():
    return original.resources.__wrapped__()


def _entry_codes(output):
    return {signal.security_code for signal in output.candidates}


def _assert_hold_alert(output, code):
    held = [d for d in output.seam.held_decisions if d.security_code == code]
    assert len(held) == 1 and held[0].action == "hold_alert"
    assert code not in _entry_codes(output)
    assert output.result.package is not None
    package_decisions = [
        d
        for ds in output.result.package.decision_sets
        for d in ds.decisions
        if d.security_code == code
    ]
    assert len(package_decisions) == 1 and package_decisions[0].stage == "holding"
    assert output.result.package.decision_sets[0].audit.admission_disposition != "empty_advice"
    for ds in output.result.package.decision_sets:
        _p12_loader()(ds.to_dict())
    return package_decisions[0]


def test_scan_domain_error_isolated_before_neighbor_is_published(runner, resources):
    bad = original._trend_raw("600001")
    clocks = dict(bad.close_at_by_date)
    clocks[original.TARGET] = datetime.combine(
        original.NEXT_OPEN.date(), time(15), original.SHANGHAI
    )
    bad = replace(bad, close_at_by_date=clocks)
    output = original._run(runner, resources, [original._trend_raw("600000"), bad])
    assert _entry_codes(output) == {"600000"}
    assert any(e["security_code"] == "600001" for e in output.source_closure["exclusions"])


@pytest.mark.parametrize("with_neighbor", [True, False])
def test_known_held_bad_market_keeps_alert(runner, resources, with_neighbor):
    held = replace(
        original._quiet_held("600001"),
        held_membership=True,
        close_at_by_date={},
        held_unavailable_reason="ledger: holding sessions unavailable",
    )
    raw = [original._trend_raw("600000"), held] if with_neighbor else [held]
    output = original._run(runner, resources, raw)
    decision = _assert_hold_alert(output, "600001")
    assert "holding sessions unavailable" in str(decision.explanations)
    assert _entry_codes(output) == ({"600000"} if with_neighbor else set())


@pytest.mark.parametrize("account_hint", [False, True])
@pytest.mark.parametrize("kind", ["open_buy_order", "reserved_bucket", "unconfirmed_fill"])
def test_pending_membership_is_not_a_position(runner, resources, account_hint, kind):
    _, _, account = original._producer_inputs()
    if account_hint:
        account = replace(account, active_or_reserved_security_codes=("600001",))
    pending = replace(original._trend_raw("600001"), pending_kind=kind)
    output = original._run(
        runner, resources, [original._trend_raw("600000"), pending], account=account
    )
    assert _entry_codes(output) == {"600000"}
    assert output.seam.held_decisions == ()
    assert output.source_closure["pending_security_codes"] == ["600001"]


def test_not_held_with_position_facts_is_rejected(runner, resources):
    raw = replace(
        original._quiet_held("600001"),
        held_membership=False,
        position_facts=original._held_facts(),
    )
    with pytest.raises(ProductDailyError, match="contradictory_account_membership"):
        original._run(runner, resources, [raw])


def test_fresh_account_cannot_have_a_known_position(runner, resources):
    raw = replace(original._quiet_held("600001"), held_membership=True)
    with pytest.raises(ProductDailyError, match="fresh_account_has_positions"):
        original._run(runner, resources, [raw], account_disposition="FRESH_NO_BASELINE")


def test_held_pending_overlap_still_reviews_the_position(runner, resources):
    raw = replace(
        original._quiet_held("600001"),
        held_membership=True,
        pending_kind="open_buy_order",
        held_unavailable_reason="position return unavailable",
    )
    output = original._run(runner, resources, [raw])
    decision = _assert_hold_alert(output, "600001")
    assert "position return unavailable" in str(decision.explanations)


def test_account_hint_without_market_rows_still_raises_alert(runner, resources):
    _, _, account = original._producer_inputs()
    account = replace(account, active_or_reserved_security_codes=("600001",))
    output = original._run(runner, resources, [original._trend_raw("600000")], account=account)
    _assert_hold_alert(output, "600001")
    assert _entry_codes(output) == {"600000"}


def test_quantities_and_reasons_are_readable_and_receipt_bound(runner, resources):
    assert {"held_quantity", "pending_quantity"} <= RawSecurityInput.__dataclass_fields__.keys()
    held = replace(
        original._quiet_held("600001"),
        held_membership=True,
        held_quantity=200,
        pending_kind="reserved_bucket",
        pending_quantity=100,
        held_unavailable_reason="holding duration missing",
    )
    first = original._run(runner, resources, [held])
    second = original._run(runner, resources, [replace(held, held_quantity=300)])
    third = original._run(runner, resources, [replace(held, pending_quantity=200)])
    assert first.receipt.source_closure_digest != second.receipt.source_closure_digest
    assert first.receipt.source_closure_digest != third.receipt.source_closure_digest
    membership = first.source_closure["account_membership"][0]
    assert membership["held_quantity"] == 200 and membership["pending_quantity"] == 100
    assert membership["unavailable_reason"] == "holding duration missing"
    decision = _assert_hold_alert(first, "600001")
    assert "holding duration missing" in str(decision.explanations)


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5])
def test_explicit_held_quantity_must_be_a_positive_integer(runner, resources, quantity):
    raw = replace(original._quiet_held("600001"), held_membership=True, held_quantity=quantity)
    with pytest.raises(ProductDailyError):
        original._run(runner, resources, [raw])


def test_qualified_held_facts_still_reach_real_exit(runner, resources):
    raw = replace(
        original._quiet_held("600001"),
        held_membership=True,
        held_quantity=200,
        position_facts=original._held_facts(),
    )
    output = original._run(runner, resources, [raw])
    assert [d.action for d in output.seam.held_decisions] == ["exit"]
    for ds in output.result.package.decision_sets:
        validated = _p12_loader()(ds.to_dict())
        assert any("EXIT_PLAN" in str(d.stage) for d in validated.decisions)
