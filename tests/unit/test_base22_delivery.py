from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from quanthus.pipelines.securities_warehouse import base22_delivery as d
from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError

CONFIG = Path(__file__).resolve().parents[2] / "config/pipelines/base22-delivery.v2.json"


def assess(checks=None, limitations=None):
    return d.assess_delivery(
        contract=json.loads(CONFIG.read_bytes()),
        tables={"actions", "prices"},
        limitations=limitations
        if limitations is not None
        else [
            {
                "id": "two-missing-documents",
                "tables": ["actions"],
                "blocked_uses": ["canonical_adjusted_price"],
                "disposition": "PRESERVE_UNAVAILABLE",
                "scope_kind": "keys",
                "scope": ["key1", "key2"],
                "risk": "Unknown implementation cannot determine factor.",
                "repair_cost": "Recover two implementation notices, not full recollection.",
                "evidence_paths": ["evidence/missing.json"],
            }
        ],
        evidence={"evidence/missing.json": "sha256:" + "a" * 64},
        checks=checks if checks is not None else dict.fromkeys(d.HARD_CHECKS, "PASS"),
    )


def test_local_limitations_do_not_block_other_qualified_delivery_or_become_repairs():
    result = assess()
    assert result["build_eligible"] and result["independently_verified"]
    assert not result["limitations_are_repaired_data"]
    assert result["consumer_admission"] == "NOT_GRANTED"
    d.require_use(result, table="prices", use="canonical_adjusted_price")
    d.require_use(result, table="actions", use="governance_baseline")
    d.require_use(result, table="actions", use="canonical_adjusted_price", scope_tokens={"healthy"})
    with pytest.raises(BoundedError, match="DELIVERY_USE_UNAVAILABLE"):
        d.require_use(
            result, table="actions", use="canonical_adjusted_price", scope_tokens={"key1"}
        )
    with pytest.raises(BoundedError, match="DELIVERY_USE_UNAVAILABLE"):
        d.require_use(result, table="actions", use="canonical_adjusted_price")


@pytest.mark.parametrize("gate", d.HARD_CHECKS)
def test_quality_principle_cannot_waive_structural_failures(gate):
    checks = dict.fromkeys(d.HARD_CHECKS, "PASS")
    checks[gate] = "FAIL"
    result = assess(checks=checks)
    assert not result["build_eligible"] and not result["independently_verified"]
    with pytest.raises(BoundedError, match="NOT_STRUCTURALLY_READY"):
        d.require_use(result, table="prices", use="governance_baseline")


def test_ready_does_not_imply_cold_or_owner_acceptance():
    checks = dict.fromkeys(d.HARD_CHECKS, "PASS")
    checks["two_independent_source_denied_cold_runs"] = "NOT_RUN"
    checks["missing_and_corrupt_fail_closed"] = "NOT_RUN"
    result = assess(checks=checks)
    assert result["build_eligible"] and not result["independently_verified"]
    checks["global_keys_and_knowledge_versions"] = "NOT_RUN"
    assert not assess(checks=checks)["build_eligible"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_paths", []),
        ("scope", []),
        ("tables", ["unknown"]),
        ("blocked_uses", ["governance_baseline"]),
        ("disposition", "REPAIRED"),
        ("repair_cost", ""),
        ("risk", ""),
        ("scope_kind", "ignore_everything"),
    ],
)
def test_unbound_or_unscoped_limitation_rejected(field, value):
    limitations = copy.deepcopy(assess()["limitations"])
    limitations[0][field] = value
    with pytest.raises(BoundedError, match="UNBOUND_OR_UNSCOPED"):
        assess(limitations=limitations)


def test_unknown_use_and_changed_frozen_protection_fail_closed():
    with pytest.raises(BoundedError, match="USE_UNKNOWN"):
        d.require_use(assess(), table="actions", use="automatic_P6_acceptance")
    contract = json.loads(CONFIG.read_bytes())
    contract["permanent_protections"]["original_visibility_unavailable"] = 0
    with pytest.raises(BoundedError, match="FROZEN_PROTECTION_CHANGED"):
        d.validate_contract(contract)
