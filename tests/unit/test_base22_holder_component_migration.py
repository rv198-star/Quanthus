"""Selected repairs share source projection and never discard unproved old atoms."""

from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse import base22_holder_component_migration as m
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from tests.unit.test_base22_holder_components import group_case


def setup():
    args = group_case()
    later = args.pop("later_raw")
    args["native"] = args.pop("before")
    args["retained_components"] = m.prepare_retained_components(
        later, resolver=args["resolver"], contract=args["contract"]
    )
    args["source_scope_identity"] = "sha256:" + "d" * 64
    return args


def test_migration_consumes_original_components_once_and_preserves_exact_old_row(monkeypatch):
    args = setup()
    calls = []
    real = m._native_origin

    def origin(*a):
        calls.append(1)
        return real(*a)

    monkeypatch.setattr(m, "_native_origin", origin)
    result = m.qualify_component_migrations(**args)
    item = next(iter(result["revisions"].values()))
    assert item["output"][0] == {**args["native"][0], "knowledge_to": args["observed_at"]}
    assert len(item["output"]) == 3
    assert result["shared_weekly_projection"] == "REUSED"
    assert len(calls) == 1
    assert result["held"] == {}


@pytest.mark.parametrize("field", ["raw", "rows", "proof", "member_sha256"])
def test_shared_source_binding_cannot_be_changed_without_reproduction(field):
    args = setup()
    item = args["retained_components"][0]
    if field == "raw":
        item["raw"]["content"] += "extra"
    elif field == "rows":
        item["rows"][0]["raw_holding_quantity"] = "0"
    elif field == "proof":
        item["proof"]["frames"] = []
    else:
        item[field] = "sha256:" + "e" * 64
    with pytest.raises(BoundedError, match="SHARED_RAW_PROOF"):
        m.qualify_component_migrations(**args)


def test_native_value_conflict_is_held_not_silently_replaced():
    args = setup()
    args["native"][0]["raw_holding_quantity"] = "0"
    result = m.qualify_component_migrations(**args)
    assert result["revisions"] == {}
    assert set(result["held"].values()) == {
        "HOLDER_COMPONENT_MIGRATION_EXACT_NATIVE_BINDING_FAILED"
    }


def test_duplicate_native_group_is_not_given_another_invented_component_identity():
    args = setup()
    args["native"].append(deepcopy(args["native"][0]))
    with pytest.raises(BoundedError, match="DUPLICATE_NATIVE"):
        m.qualify_component_migrations(**args)
