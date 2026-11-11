"""Source-labelled nonuniform terms preserve exact native interval history."""

from copy import deepcopy
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse.base22 import load_contract, partition
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base22_nonuniform_history_merge import (
    TABLES,
    merge_nonuniform_partition,
)
from tests.unit.test_base22_nonuniform_revision import MIXED, fixture, project


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("mixed", [False, True])
def test_complete_nonuniform_groups_preserve_other_atoms_and_exact_old_values(table, mixed):
    c = fixture(MIXED, mixed=True) if mixed else fixture()
    actions, terms, _ = project(c)
    old, patch = ([c[1]], actions) if table == TABLES[0] else (c[2], terms)
    rule = load_contract(Path("config/pipelines"))[0]["tables"][table]
    unaffected = {**old[0], "corporate_action_id": "unaffected"}
    if table == TABLES[1]:
        unaffected["corporate_action_term_id"] = "other-term"
    history, frozen = old + [unaffected], deepcopy(old + [unaffected])
    expected, proof = merge_nonuniform_partition(
        table, partition(old[0], rule), rule, history, old, patch
    )
    assert history == frozen and unaffected in expected
    assert expected[0] == patch[0]
    assert proof["closed_versions"] == len(old)
    assert proof["new_versions"] == len(patch) - len(old)
    if table == TABLES[1]:
        assert proof["general_gross_cash_qualified"] is False
        assert any("TAX_UNAVAILABLE" in r["term_type"] for r in expected) or mixed


@pytest.mark.parametrize(
    "defect",
    [
        "before",
        "closure",
        "missing_population",
        "source",
        "generic_cash",
        "duplicate",
        "new_clock",
        "different_effective",
        "incomplete_before",
        "extra_event",
    ],
)
def test_unbound_or_incomplete_groups_never_become_generic_cash(defect):
    c = fixture()
    _, patch, _ = project(c)
    old, history = deepcopy(c[2]), deepcopy(c[2])
    rule = load_contract(Path("config/pipelines"))[0]["tables"][TABLES[1]]
    if defect == "before":
        history[0]["amount_per_share"] += 1
    elif defect == "closure":
        patch[0]["amount_per_share"] += 1
    elif defect == "missing_population":
        patch.pop()
    elif defect == "source":
        patch[-1]["record_source"] = "unknown"
    elif defect == "generic_cash":
        patch[-1]["term_type"] = "CASH_AMOUNT_PER_SHARE"
    elif defect == "duplicate":
        patch.append(deepcopy(patch[-1]))
    elif defect == "new_clock":
        patch[-1]["knowledge_from"] = old[0]["knowledge_from"]
    elif defect == "different_effective":
        patch[-1]["effective_at"] = None
    elif defect == "incomplete_before":
        history.append({**old[0], "corporate_action_term_id": "unselected-other-term"})
    else:
        patch[-1]["corporate_action_id"] = "unbound"
    with pytest.raises((BoundedError, RuntimeError), match="NONUNIFORM"):
        merge_nonuniform_partition(TABLES[1], partition(old[0], rule), rule, history, old, patch)
