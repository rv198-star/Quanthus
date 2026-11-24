"""Rejected market input and reservation hints cannot establish shared facts."""

from dataclasses import replace
from datetime import date

import pytest

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation import daily_runner
from tests.unit import test_p11_daily_runner as original
from tests.unit.test_product_daily_rule_seam import _p12_loader


@pytest.fixture
def runner():
    return original.runner.__wrapped__()


@pytest.fixture
def resources():
    return original.resources.__wrapped__()


@pytest.mark.parametrize("failure", ["identity", "missing_close"])
@pytest.mark.parametrize("bad_first", [True, False])
def test_rejected_calendar_cannot_change_valid_entry(runner, resources, failure, bad_first):
    valid, bad = original._trend_raw("600000"), original._trend_raw("600001")
    bad = replace(
        bad, universe_membership=tuple(sorted((*bad.universe_membership, date(2026, 11, 21))))
    )
    if failure == "identity":
        bad = replace(bad, identity=replace(bad.identity, security_id="security-OTHER"))
    else:
        bad = replace(bad, close_at_by_date={})
    output = original._run(runner, resources, [bad, valid] if bad_first else [valid, bad])
    assert [candidate.security_code for candidate in output.candidates] == ["600000"]
    assert any(item["security_code"] == "600001" for item in output.source_closure["exclusions"])
    assert output.result.publishable
    _p12_loader()(output.result.package.decision_sets[0].to_dict())


@pytest.mark.parametrize("disposition", ["ok", "FRESH_NO_BASELINE"])
@pytest.mark.parametrize("zero_quantity", [False, True])
def test_combined_hint_is_not_fill(runner, resources, disposition, zero_quantity):
    _, _, account = original._producer_inputs()
    account = replace(account, active_or_reserved_security_codes=("600001",))
    raw = [original._trend_raw("600000")]
    if zero_quantity:
        raw.append(replace(original._trend_raw("600001"), held_quantity=0))
    output = original._run(runner, resources, raw, account=account, account_disposition=disposition)
    assert output.seam.held_decisions == ()
    assert [candidate.security_code for candidate in output.candidates] == ["600000"]
    member = {item["security_code"]: item for item in output.source_closure["account_membership"]}[
        "600001"
    ]
    assert member["held"] is False and member["pending"] is True
    assert member["membership_source"] == "combined_account_hint"


def test_pending_descriptor_without_market_rows_binds_details(runner, resources):
    pending_type = daily_runner.PendingSecurityInput
    first = original._run(
        runner,
        resources,
        [original._trend_raw("600000")],
        pending_securities=(pending_type("600001", "open_buy_order", 200),),
    )
    second = original._run(
        runner,
        resources,
        [original._trend_raw("600000")],
        pending_securities=(pending_type("600001", "open_buy_order", 300),),
    )
    assert first.seam.held_decisions == ()
    member = {item["security_code"]: item for item in first.source_closure["account_membership"]}[
        "600001"
    ]
    assert member["pending_records"] == [{"kind": "open_buy_order", "quantity": 200}]
    assert first.receipt.source_closure_digest != second.receipt.source_closure_digest


@pytest.mark.parametrize("with_raw", [False, True])
def test_legacy_pending_code_requires_explicit_details(runner, resources, with_raw):
    raw = [original._trend_raw("600000")]
    if with_raw:
        raw.append(original._trend_raw("600001"))
    with pytest.raises(ProductDailyError, match="pending_metadata_missing"):
        original._run(runner, resources, raw, pending_security_codes=("600001",))


def test_unknown_pending_quantity_is_explicit_not_zero(runner, resources):
    pending_type = daily_runner.PendingSecurityInput
    output = original._run(
        runner,
        resources,
        [original._trend_raw("600000")],
        pending_securities=(pending_type("600001", "reserved_bucket", None),),
    )
    member = {item["security_code"]: item for item in output.source_closure["account_membership"]}[
        "600001"
    ]
    assert member["pending_records"] == [{"kind": "reserved_bucket", "quantity": None}]
    assert member["pending_quantity_state"] == "unavailable"
    assert member["held"] is False


def test_multiple_pending_kinds_are_preserved_without_quantity_sum(runner, resources):
    pending_type = daily_runner.PendingSecurityInput
    output = original._run(
        runner,
        resources,
        [original._trend_raw("600000")],
        pending_securities=(
            pending_type("600001", "reserved_bucket", None),
            pending_type("600001", "open_buy_order", 100),
        ),
    )
    member = {item["security_code"]: item for item in output.source_closure["account_membership"]}[
        "600001"
    ]
    assert member["pending_records"] == [
        {"kind": "open_buy_order", "quantity": 100},
        {"kind": "reserved_bucket", "quantity": None},
    ]
    assert member["pending_quantity"] is None
    assert output.seam.held_decisions == ()


def test_actual_held_with_separate_pending_fact_keeps_real_exit(runner, resources):
    pending_type = daily_runner.PendingSecurityInput
    held = replace(
        original._quiet_held("600001"),
        held_membership=True,
        held_quantity=200,
        position_facts=original._held_facts(),
    )
    output = original._run(
        runner,
        resources,
        [held],
        pending_securities=(pending_type("600001", "open_buy_order", 100),),
    )
    assert [decision.action for decision in output.seam.held_decisions] == ["exit"]
    validated = _p12_loader()(output.result.package.decision_sets[0].to_dict())
    assert any("EXIT_PLAN" in str(decision.stage) for decision in validated.decisions)
