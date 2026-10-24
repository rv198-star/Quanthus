"""Split-fill aliases retain full financial values and source graph references."""

from copy import deepcopy

import pytest

from quanthus.expert_strategy import p3_dated_share_validation as v
from quanthus.expert_strategy import p3_risk_entry_veto as old


def bundle(suffix):
    order, first, second, alias = [x + suffix for x in ("order-", "fill1-", "fill2-", "slice-")]
    records = [
        (
            "order_created",
            "2019-06-03T09:30:00+08:00",
            "ok",
            {
                "order_id": order,
                "unit_id": "unit-1",
                "security_code": "603517",
                "side": "sell",
                "quantity": 140,
                "eligible_session": "2019-06-03",
                "execution_slice_order_id": order,
            },
        ),
        (
            "fill",
            "2019-06-03T09:30:00+08:00",
            "ok",
            {
                "order_id": order,
                "fill_id": first,
                "side": "sell",
                "quantity": 100,
                "total_fees": "5.00",
                "execution_slice_order_id": order,
            },
        ),
        (
            "order_status",
            "2019-06-03T09:30:00.000001+08:00",
            "dated_share_residual_order_carried",
            {
                "order_id": order,
                "partial_fill_ids": [first],
                "remaining_quantity": 40,
                "quantity_filled": 100,
            },
        ),
        (
            "fill",
            "2019-06-04T09:30:00+08:00",
            "ok",
            {
                "order_id": order,
                "fill_id": second,
                "side": "sell",
                "quantity": 40,
                "total_fees": "5.00",
                "execution_slice_order_id": alias,
            },
        ),
    ]
    events = [
        {
            "event_id": f"evt-{suffix}-{i}",
            "event_type": kind,
            "event_time": at,
            "sequence": i,
            "reason_code": reason,
            "payload": payload,
        }
        for i, (kind, at, reason, payload) in enumerate(records, 1)
    ]
    return {
        "decisions.json": [],
        "engine-events.json": events,
        "equity.json": [],
        "daily-returns.json": [],
        "final-ledgers.json": [],
        "reconciliation.json": {"reconciled": True},
    }


def digest(data, method=v.business_bundle):
    return method(data, {}, snapshot="synthetic", anchor_snapshot="anchor", snapshot_suffixes=[])


def test_legacy_normalizer_does_not_understand_new_reference_fields():
    assert digest(bundle("a"), old.business_bundle) != digest(bundle("b"), old.business_bundle)


def test_two_independent_id_graphs_match_without_mutating_inputs():
    a, b = bundle("a"), bundle("b")
    before = deepcopy(a)
    assert digest(a) == digest(b)
    assert a == before


@pytest.mark.parametrize("field,value", [("total_fees", "5.01"), ("quantity", 99)])
def test_real_fill_values_remain_part_of_the_digest(field, value):
    a, b = bundle("a"), bundle("b")
    b["engine-events.json"][1]["payload"][field] = value
    if field == "quantity":
        b["engine-events.json"][2]["payload"]["quantity_filled"] = value
    assert digest(a) != digest(b)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown-fill",
        "other-day-fill",
        "missing-fill",
        "duplicate-fill",
        "slice-reused",
        "two-slices-one-day",
        "unknown-order",
        "wrong-reason",
        "missing-field",
    ],
)
def test_unknown_or_rewired_reference_is_rejected(mutation):
    data = bundle("a")
    order, fill, carry, later = data["engine-events.json"]
    if mutation == "unknown-fill":
        carry["payload"]["partial_fill_ids"] = ["unknown"]
    elif mutation == "other-day-fill":
        carry["payload"]["partial_fill_ids"] = [later["payload"]["fill_id"]]
    elif mutation == "missing-fill":
        carry["payload"]["partial_fill_ids"] = []
    elif mutation == "duplicate-fill":
        carry["payload"]["partial_fill_ids"] *= 2
    elif mutation == "slice-reused":
        later["payload"]["execution_slice_order_id"] = order["payload"]["order_id"]
    elif mutation == "two-slices-one-day":
        fill["payload"]["execution_slice_order_id"] = "other-slice"
    elif mutation == "unknown-order":
        fill["payload"]["order_id"] = "unknown"
    elif mutation == "wrong-reason":
        carry["reason_code"] = "ordinary"
    else:
        del carry["payload"]["partial_fill_ids"]
    with pytest.raises(ValueError, match="DATED_GRAPH"):
        digest(data)


def test_ordinary_graph_uses_exact_legacy_digest():
    data = bundle("a")
    data["engine-events.json"].pop(2)
    for event in data["engine-events.json"]:
        event["payload"].pop("execution_slice_order_id", None)
    assert digest(data) == digest(data, old.business_bundle)


def test_all_locked_carry_has_no_invented_fill_reference():
    data = bundle("a")
    data["engine-events.json"].pop(1)
    carry = data["engine-events.json"][1]
    carry["payload"].update(partial_fill_ids=[], quantity_filled=0)
    assert digest(data) == digest(deepcopy(data))


def test_real_p2_split_fill_graph_matches_after_only_generated_id_renaming():
    from tests.unit.test_p3_dated_share_engine import execute
    from tests.unit.test_p3_risk_components import row

    _, result, _, _, _ = execute()
    data = bundle("unused")
    data["engine-events.json"] = [e.to_dict() for e in result.events]
    # Add the formal strategy context which the real integration attaches to
    # ordinary P2 signals; synthetic P2 alone accepts an arbitrary version string.
    data["decisions.json"] = [
        {
            **row(),
            "surface": "held",
            "formal_decision_digest": "decision",
            "market_date": "2019-04-01",
        }
    ]
    for event in data["engine-events.json"]:
        if "decision_version" in event["payload"]:
            event["payload"]["decision_version"] = "synthetic-card|decision"
    fields = {
        "event_id",
        "signal_id",
        "intent_id",
        "order_id",
        "execution_slice_order_id",
        "fill_id",
        "entry_id",
        "settlement_id",
    }
    identifiers = set()

    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in fields:
                    identifiers.add(child)
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    def rename(value):
        if isinstance(value, dict):
            return {key: rename(child) for key, child in value.items()}
        if isinstance(value, list):
            return [rename(child) for child in value]
        return "independent-" + value if isinstance(value, str) and value in identifiers else value

    collect(data)
    assert digest(data) == digest(rename(data))
