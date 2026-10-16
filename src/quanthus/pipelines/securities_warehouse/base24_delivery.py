"""Purpose-scoped Base 2.4 delivery contract for the 1d/native-5m profile."""

from __future__ import annotations

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
    compatible = dict(contract)
    compatible.update(
        contract_id="p10-base23-purpose-qualified-delivery-v1",
        purpose="p10_governance_baseline_with_disclosed_coverage",
    )
    result = assess_base23(
        contract=compatible,
        tables=tables,
        limitations=limitations,
        evidence=evidence,
        checks=checks,
    )
    result.update(
        contract_id=CONTRACT_ID,
        purpose=PURPOSE,
        required_intervals=list(INTERVALS),
        optional_1m_disposition=ONE_MINUTE_DISPOSITION,
    )
    from .base22 import digest

    result["identity"] = digest({k: v for k, v in result.items() if k != "identity"})
    return result
