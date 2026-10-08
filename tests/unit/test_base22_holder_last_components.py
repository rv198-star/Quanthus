"""A prequalified earlier group never overrides a different current observation."""

from datetime import datetime, timezone

import pytest

from quanthus.pipelines.securities_warehouse.base22_holder_component_migration import MIGRATION_ID
from quanthus.pipelines.securities_warehouse.base22_holder_current_columns import (
    qualify_current_columns,
)
from quanthus.pipelines.securities_warehouse.base22_holder_last_components import (
    merge_last_component_partition,
    qualify_last_components,
)
from tests.unit.test_base22_holder_current_columns import case


def inputs():
    args = case()
    current = args["current"]
    pair = next(iter(qualify_current_columns(**args)["revisions"].values()))["output"]
    pair[1]["source_revision"] = MIGRATION_ID + ":" + "a" * 64
    return args, current, pair


def test_exact_prequalified_original_uses_existing_real_governance_version():
    args, current, pair = inputs()
    result = qualify_last_components(
        current,
        pair,
        raw_closure=[],
        resolver=args["resolver"],
        component_contract=args["component_contract"],
        rule=args["rule"],
        observed_at=args["observed_at"],
        source_identity="sha256:" + "a" * 64,
    )
    assert result["counts"] == {"exact_prequalified_original": 1}
    assert result["output"] == pair
    merged, proof = merge_last_component_partition(current, result["output"])
    assert merged == [pair[0]] and proof["closed_current_versions"] == 1


def test_changed_current_requires_its_own_exact_raw_instead_of_old_pair():
    args, current, pair = inputs()
    # The earlier qualified source cannot set a later observed ratio token back.
    pair[0]["raw_holding_percentage"] = "1.250%"
    pair[1]["raw_holding_percentage"] = "1.250%"
    later = datetime(2026, 10, 9, tzinfo=timezone.utc)
    result = qualify_last_components(
        current,
        pair,
        raw_closure=[("sha256:" + "a" * 64, args["original_raw"])],
        resolver=args["resolver"],
        component_contract=args["component_contract"],
        rule=args["rule"],
        observed_at=later,
        source_identity="sha256:" + "b" * 64,
    )
    assert result["counts"] == {"exact_current_raw_columns": 1}
    assert result["output"][1]["raw_holding_percentage"] == "1.25%"
    assert result["output"][1]["knowledge_from"] == later


@pytest.mark.parametrize(
    "defect", ["missing-current", "duplicate-current", "plural-new", "missing-raw", "raw-hash"]
)
def test_incomplete_or_unproved_single_group_never_grants_a_union(defect):
    args, current, pair = inputs()
    raws = [("sha256:" + "a" * 64, args["original_raw"])]
    if defect == "missing-current":
        current = []
    elif defect == "duplicate-current":
        current.append(dict(current[0]))
    elif defect == "plural-new":
        pair.append(dict(pair[1]))
    else:
        pair[0]["raw_holding_percentage"] = "1.250%"
        if defect == "missing-raw":
            raws = []
        else:
            raws[0][1]["content_sha256"] = "sha256:" + "c" * 64
    with pytest.raises((RuntimeError, ValueError)):
        qualify_last_components(
            current,
            pair,
            raw_closure=raws,
            resolver=args["resolver"],
            component_contract=args["component_contract"],
            rule=args["rule"],
            observed_at=args["observed_at"],
            source_identity="sha256:" + "d" * 64,
        )
