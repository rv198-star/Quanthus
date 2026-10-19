"""Purpose-scoped Base 2.4 delivery contract for the 1d/native-5m profile."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .base22 import EXTENSION_SHA, MODEL_SHA
from .base22_bounded import BoundedError
from .base23_delivery import HARD_CHECKS, USES
from .base23_delivery import assess_delivery as assess_base23

CONTRACT_ID = "p10-base24-purpose-qualified-1d5m-delivery-v1"
PURPOSE = "p10_governance_baseline_1d_native5m_with_disclosed_coverage"
FREQUENCY_POLICY_SHA = "sha256:12cac14c023edb2c151be28fb90ce5da57b8562eeca80dbf5c2d2ea2b001608e"
INTERVALS = ("1d", "5m")
ONE_MINUTE_DISPOSITION = "NOT_INCLUDED_RETAINED_SOURCE_NOT_DELETED"
BASE24_LIMITATION_RULES = {
    "base24-bounded-continuous-session-turnover": {
        "scope_kind": "security_days",
        "disposition": "QUALIFIED_5M_CONTINUOUS_SESSION_ONLY",
        "blocked_uses": ["whole_day_turnover_from_5m"],
    },
    "base24-exact-5m-unavailable": {
        "scope_kind": "security_days",
        "disposition": "PRESERVE_UNAVAILABLE",
        "blocked_uses": ["5m_bar_on_exact_security_day"],
    },
    "base24-optional-1m-not-included": {
        "scope_kind": "interval",
        "disposition": "NOT_INCLUDED_DIAGNOSTIC_ONLY",
        "blocked_uses": ["1m_consumption"],
    },
}
BASE24_LIMITATION_IDS = set(BASE24_LIMITATION_RULES)

SCHEMA_SUCCESSORS = {
    "dwd_shareholder_count_observation": {
        "additive_column": "average_holding_exact",
        "arrow_type": "decimal256(41,13)",
        "nullable": True,
        "legacy_column_retained": "average_holding",
    },
    "dwd_shareholding_observation": {
        "additional_nullable_business_key_fields": [
            "share_nature",
            "holding_percentage_unit",
        ],
    },
}
PERMANENT_PROTECTIONS = {
    "original_visibility_unavailable": 4173,
    "tail_conflicts": "KEEP_OLD_THREE_KEYS_PENDING_AUTHORITY",
    "consumer_admission": "NOT_GRANTED",
    "source_truth_or_PIT_inference": False,
    "frozen_policy_changes": False,
}


def validate_contract(contract: Mapping[str, Any]) -> None:
    if (
        contract.get("contract_id") != CONTRACT_ID
        or contract.get("purpose") != PURPOSE
        or contract.get("business_through") != "2026-09-30"
        or contract.get("business_timezone") != "Asia/Shanghai"
        or contract.get("predecessor_contract") != "p10-base23-purpose-qualified-delivery-v1"
        or contract.get("frequency_policy_sha256") != FREQUENCY_POLICY_SHA
        or contract.get("required_intervals") != list(INTERVALS)
        or contract.get("optional_1m_disposition") != ONE_MINUTE_DISPOSITION
        or contract.get("supplier_native_5m_required") is not True
        or contract.get("derive_5m_from_1m") is not False
        or contract.get("model_sha256") != MODEL_SHA
        or contract.get("extension_sha256") != EXTENSION_SHA
        or contract.get("required_structural_checks") != list(HARD_CHECKS)
        or contract.get("schema_successors") != SCHEMA_SUCCESSORS
        or contract.get("governance_read_use") != "governance_baseline"
        or set(contract.get("restricted_uses", [])) != USES
        or contract.get("permanent_protections") != PERMANENT_PROTECTIONS
    ):
        raise BoundedError("BASE24_DELIVERY_CONTRACT_CHANGED")


def assess_delivery(
    *,
    contract: Mapping[str, Any],
    tables: set[str],
    limitations: list[dict[str, Any]],
    evidence: Mapping[str, str],
    checks: Mapping[str, str],
) -> dict[str, Any]:
    validate_contract(contract)
    inherited = []
    frequency = []
    for item in limitations:
        limitation_id = item.get("id")
        if limitation_id in BASE24_LIMITATION_IDS:
            frequency.append(item)
        elif isinstance(limitation_id, str) and limitation_id.startswith("base24-"):
            raise BoundedError("BASE24_LIMITATION_UNBOUND_OR_UNSCOPED")
        else:
            inherited.append(item)
    if {item.get("id") for item in frequency} != BASE24_LIMITATION_IDS:
        raise BoundedError("BASE24_LIMITATION_UNBOUND_OR_UNSCOPED")
    for item in frequency:
        rule = BASE24_LIMITATION_RULES[item["id"]]
        evidence_paths = item.get("evidence_paths")
        if (
            item.get("tables") != ["dwd_security_bar"]
            or item.get("scope_kind") != rule["scope_kind"]
            or item.get("disposition") != rule["disposition"]
            or item.get("blocked_uses") != rule["blocked_uses"]
            or not isinstance(item.get("scope"), list)
            or not item["scope"]
            or any(not isinstance(value, str) or not value for value in item["scope"])
            or not item.get("risk")
            or not item.get("repair_cost")
            or not isinstance(evidence_paths, list)
            or not evidence_paths
            or any(path not in evidence for path in evidence_paths)
            or any(
                not re.fullmatch(r"sha256:[0-9a-f]{64}", evidence[path])
                for path in evidence_paths
            )
        ):
            raise BoundedError("BASE24_LIMITATION_UNBOUND_OR_UNSCOPED")
    compatible = dict(contract)
    compatible.update(
        contract_id="p10-base23-purpose-qualified-delivery-v1",
        purpose="p10_governance_baseline_with_disclosed_coverage",
    )
    result = assess_base23(
        contract=compatible,
        tables=tables,
        limitations=inherited,
        evidence=evidence,
        checks=checks,
    )
    result.update(
        contract_id=CONTRACT_ID,
        purpose=PURPOSE,
        required_intervals=list(INTERVALS),
        optional_1m_disposition=ONE_MINUTE_DISPOSITION,
        limitations=limitations,
        known_limitation_count=len(limitations),
    )
    from .base22 import digest

    result["identity"] = digest({k: v for k, v in result.items() if k != "identity"})
    return result
