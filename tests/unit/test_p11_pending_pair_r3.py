"""Legacy pending fields cannot be masked by account membership."""

from dataclasses import replace

import pytest

from quanthus.product_daily.contracts import ProductDailyError
from quanthus.product_daily.recommendation.daily_runner import PendingSecurityInput
from tests.unit import test_p11_daily_runner as original


@pytest.fixture
def runner():
    return original.runner.__wrapped__()


@pytest.fixture
def resources():
    return original.resources.__wrapped__()


@pytest.mark.parametrize("hint", [False, True])
@pytest.mark.parametrize("typed", [False, True])
def test_quantity_without_kind_never_borrows_pending_membership(runner, resources, hint, typed):
    _, _, account = original._producer_inputs()
    if hint:
        account = replace(account, active_or_reserved_security_codes=("600001",))
    raw = replace(original._trend_raw("600001"), pending_quantity=300)
    kwargs = (
        {"pending_securities": (PendingSecurityInput("600001", "open_buy_order", 100),)}
        if typed
        else {}
    )
    with pytest.raises(ProductDailyError, match="pending_kind_unavailable"):
        original._run(
            runner, resources, [original._trend_raw("600000"), raw], account=account, **kwargs
        )


@pytest.mark.parametrize(
    "kind,quantity", [("open_buy_order", 200), ("open_buy_order", None), ("reserved_bucket", 100)]
)
def test_legacy_must_reference_exact_typed_fact(runner, resources, kind, quantity):
    raw = replace(
        original._trend_raw("600001"), pending_kind="open_buy_order", pending_quantity=100
    )
    with pytest.raises(ProductDailyError, match="pending_metadata_conflict"):
        original._run(
            runner,
            resources,
            [original._trend_raw("600000"), raw],
            pending_securities=(PendingSecurityInput("600001", kind, quantity),),
        )


@pytest.mark.parametrize("quantity", [100, None])
def test_matching_legacy_projection_is_not_a_second_pending_order(runner, resources, quantity):
    raw = replace(
        original._trend_raw("600001"), pending_kind="open_buy_order", pending_quantity=quantity
    )
    output = original._run(
        runner,
        resources,
        [original._trend_raw("600000"), raw],
        pending_securities=(PendingSecurityInput("600001", "open_buy_order", quantity),),
    )
    member = {item["security_code"]: item for item in output.source_closure["account_membership"]}[
        "600001"
    ]
    assert member["pending_records"] == [{"kind": "open_buy_order", "quantity": quantity}]
    assert output.seam.held_decisions == ()
    assert [candidate.security_code for candidate in output.candidates] == ["600000"]
