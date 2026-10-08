"""Only an exact raw-qualified current pair can modify a native partition."""

import pytest

from quanthus.pipelines.securities_warehouse.base22_holder_current_columns import (
    qualify_current_columns,
)
from quanthus.pipelines.securities_warehouse.base22_holder_current_native_merge import (
    merge_current_columns_partition,
)
from tests.unit.test_base22_holder_current_columns import case


def inputs():
    args = case()
    qualified = qualify_current_columns(**args)
    pair = next(iter(qualified["revisions"].values()))["output"]
    return args["current"], pair


def test_exact_pair_preserves_native_history_and_adds_one_real_governance_version():
    old, pair = inputs()
    closed_history = {**old[0], "knowledge_to": old[0]["knowledge_from"]}
    # A different already-closed version's atom is outside the target SHA.
    closed_history["source_revision"] = "older-closed-source"
    actual, proof = merge_current_columns_partition([closed_history, *old], pair)
    assert actual == [closed_history, pair[0]]
    assert proof["closed_current_versions"] == 1
    assert proof["new_governance_versions"] == 1
    assert proof["already_closed_intervals_rewritten"] is False


@pytest.mark.parametrize(
    "defect", ["missing", "duplicate", "quantity", "clock", "closed", "numeric-pair"]
)
def test_unqualified_pair_or_native_selection_fails_closed(defect):
    old, pair = inputs()
    if defect == "missing":
        old = []
    elif defect == "duplicate":
        old.append(dict(old[0]))
    elif defect == "quantity":
        old[0]["holding_quantity"] += 1
    elif defect == "clock":
        pair[0]["knowledge_to"] = old[0]["knowledge_from"]
    elif defect == "closed":
        old[0]["knowledge_to"] = pair[0]["knowledge_to"]
    else:
        pair[1]["holding_quantity"] += 1
    with pytest.raises(RuntimeError, match="CURRENT_COLUMNS_NATIVE"):
        merge_current_columns_partition(old, pair)


def test_unaffected_partition_is_byte_semantically_unchanged():
    old, pair = inputs()
    actual, proof = merge_current_columns_partition(old, [])
    assert actual == old and proof["closed_current_versions"] == 0
