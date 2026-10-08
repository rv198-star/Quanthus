"""Already closed historical intervals must not be extended to governance time."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_holder_column_revision import (
    qualify_column_restorations,
)
from quanthus.pipelines.securities_warehouse.base22_holder_native_repair_merge import (
    reconcile_current_pair,
)
from tests.unit.test_base22_holder_column_revision import case


def fixture():
    args = case()
    result = qualify_column_restorations(**args)
    pair = next(iter(result["revisions"].values()))["output"]
    return args["native"][0], pair


@pytest.mark.parametrize(
    "current_kind", ["original_open", "later_same_legacy_value", "already_correct"]
)
def test_only_current_open_interval_closes_and_corrected_body_is_exact(current_kind):
    original, pair = fixture()
    current = dict(original)
    if current_kind != "original_open":
        current["knowledge_from"] += timedelta(days=7)
    if current_kind == "already_correct":
        current["share_nature"] = pair[1]["share_nature"]
        current["record_source"] = "another-source-with-identical-qualified-body"
    frozen = deepcopy([pair, current])
    closed, new, proof = reconcile_current_pair(*pair, current)
    assert closed == {**current, "knowledge_to": pair[1]["knowledge_from"]}
    assert new == pair[1]
    assert [pair, current] == frozen
    assert proof["previously_closed_intervals_rewritten"] is False
    assert proof["third_value_or_different_provider_priority_granted"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "third_class",
        "third_quantity",
        "different_provider",
        "closed",
        "new_key",
        "old_clock",
        "missing_closure",
        "missing_field",
    ],
)
def test_unproved_new_current_value_or_unbound_version_remains_held(defect):
    original, pair = fixture()
    current = dict(original)
    if defect == "third_class":
        current["share_nature"] = "另一未裁定类别"
    elif defect == "third_quantity":
        current["holding_quantity"] += Decimal("1")
    elif defect == "different_provider":
        current["record_source"] = "unqualified-other-provider"
    elif defect == "closed":
        current["knowledge_to"] = pair[1]["knowledge_from"]
    elif defect == "new_key":
        pair[1]["security_id"] = "other"
    elif defect == "old_clock":
        current["knowledge_from"] = pair[1]["knowledge_from"]
    elif defect == "missing_closure":
        pair[0]["knowledge_to"] = None
    else:
        current.pop("share_nature")
    with pytest.raises(BoundedError, match="HOLDER_NATIVE_REPAIR"):
        reconcile_current_pair(*pair, current)
