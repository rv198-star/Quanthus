from copy import deepcopy

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError
from quanthus.pipelines.securities_warehouse.base23_required import CONTRACT_ID
from quanthus.pipelines.securities_warehouse.base23_required_image import (
    KIND,
    PREFIX,
    validate_required_binding,
)


def fixture():
    item = {
        "path": "payload/T/all/part-000000.parquet",
        "partition": "all",
        "rows": 1,
        "size_bytes": 10,
        "sha256": "sha256:" + "a" * 64,
    }
    sid = "required"
    scope = {
        "identity": "union",
        "contract_id": CONTRACT_ID,
        "complete_Base23": False,
        "consumer_admission": "NOT_GRANTED",
        "outputs": {t: [item] for t in ("dwd_corporate_action", "dwd_corporate_action_term")},
    }
    dep = {"identity": "dep", "inputs": {"native": {"dwd_listing": [item]}}}
    plan = {
        "required_acceptance": {
            "kind": KIND,
            "local_prefix": PREFIX,
            "scope_identity": "union",
            "dependency_identity": "dep",
            "replay_identity": "sha256:" + "b" * 64,
            "source_id": sid,
        },
        "sources": {
            sid: {
                "descriptor": "SCOPE.json",
                "role": "qualified_domain_output",
                "quality_gate": "QUALIFIED_FOR_GOVERNANCE_RETENTION",
            }
        },
        "tables": {
            t: {"source_ids": [sid], "files": [{**item, "table": t, "source_id": sid}]}
            for t in scope["outputs"]
        },
    }
    plan["tables"]["dwd_listing"] = {"files": [item]}
    return plan, scope, dep


def test_exact_bound_domain_and_dated_parents_succeed_without_old_path_resolution():
    args = fixture()
    before = deepcopy(args)
    assert validate_required_binding(*args) == "required"
    assert args == before


@pytest.mark.parametrize(
    "damage", ["unqualified", "source_ids", "missing_domain", "parent", "dependency"]
)
def test_required_image_scope_and_dated_parent_substitution_fail(damage):
    plan, scope, dep = fixture()
    if damage == "unqualified":
        plan["sources"]["required"]["quality_gate"] = "UNQUALIFIED"
    elif damage == "source_ids":
        plan["tables"]["dwd_corporate_action"]["source_ids"] = []
    elif damage == "missing_domain":
        scope["outputs"].pop("dwd_corporate_action_term")
    elif damage == "parent":
        plan["tables"]["dwd_listing"]["files"] = []
    else:
        dep["identity"] = "other"
    with pytest.raises(BoundedError):
        validate_required_binding(plan, scope, dep)
