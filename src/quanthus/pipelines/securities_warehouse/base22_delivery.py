"""Purpose-scoped Base 2.2 delivery; source limitations never become repairs.

This successor leaves the synthetic v1 and frozen consumer policies unchanged.
The image contains all physical tables, qualified observations and a mandatory
local limitations registry. Governance reads can proceed; affected business uses
remain unavailable. Structural corruption cannot be waived as a limitation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .base22 import EXTENSION_SHA, MODEL_SHA, digest
from .base22_bounded import BoundedError

CONTRACT_ID = "p10-base22-purpose-qualified-delivery-v2"
PURPOSE = "p10_governance_baseline_with_disclosed_coverage"
HARD_CHECKS = (
    "exact_physical_tables_partitions_files",
    "all_file_hashes",
    "physical_schema",
    "global_keys_and_knowledge_versions",
    "business_cutoff_and_original_times",
    "calendar_date_and_venue",
    "foreign_keys_and_table_source_closure",
    "recomputed_typed_partition_summaries",
    "registered_capacity",
    "two_independent_source_denied_cold_runs",
    "missing_and_corrupt_fail_closed",
)
USES = {
    "trading_entry",
    "historical_pit",
    "canonical_adjusted_price",
    "cross_provider_exact_price",
    "complete_holder_matrix",
    "complete_market_coverage",
    "original_revision_reproduction",
}


def validate_contract(contract: Mapping[str, Any]) -> None:
    if (
        contract.get("contract_id") != CONTRACT_ID
        or contract.get("purpose") != PURPOSE
        or contract.get("business_through") != "2026-08-31"
        or contract.get("business_timezone") != "Asia/Shanghai"
        or contract.get("model_sha256") != MODEL_SHA
        or contract.get("extension_sha256") != EXTENSION_SHA
        or contract.get("required_structural_checks") != list(HARD_CHECKS)
        or set(contract.get("restricted_uses", [])) != USES
        or contract.get("governance_read_use") != "governance_baseline"
        or contract.get("schema_successors")
        != {
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
        or contract.get("permanent_protections")
        != {
            "original_visibility_unavailable": 4173,
            "tail_conflicts": "KEEP_OLD_THREE_KEYS_PENDING_AUTHORITY",
            "consumer_admission": "NOT_GRANTED",
            "source_truth_or_PIT_inference": False,
            "frozen_policy_changes": False,
        }
    ):
        raise BoundedError("DELIVERY_CONTRACT_OR_FROZEN_PROTECTION_CHANGED")


def assess_delivery(
    *,
    contract: Mapping[str, Any],
    tables: set[str],
    limitations: list[dict[str, Any]],
    evidence: Mapping[str, str],
    checks: Mapping[str, str],
) -> dict[str, Any]:
    """Only evidence-bound scope/uses can be limited; hard failures stay failures.

    A missing structural receipt is NOT_RUN. Build eligibility and independent
    acceptance are separate states, so READY never implies cold acceptance.
    """
    validate_contract(contract)
    if set(checks) != set(HARD_CHECKS) or any(
        v not in {"PASS", "FAIL", "NOT_RUN"} for v in checks.values()
    ):
        raise BoundedError("DELIVERY_STRUCTURAL_RECEIPTS_INVALID")
    ids: set[str] = set()
    for item in limitations:
        if (
            not isinstance(item.get("id"), str)
            or not item["id"]
            or item["id"] in ids
            or not item.get("tables")
            or not set(item["tables"]) <= tables
            or not item.get("blocked_uses")
            or not set(item["blocked_uses"]) <= USES
            or item.get("disposition") != "PRESERVE_UNAVAILABLE"
            or item.get("scope_kind") not in {"keys", "table_window", "fields"}
            or not item.get("scope")
            or not item.get("risk")
            or not item.get("repair_cost")
            or not item.get("evidence_paths")
            or any(p not in evidence for p in item["evidence_paths"])
            or any(
                not re.fullmatch(r"sha256:[0-9a-f]{64}", evidence[p])
                for p in item["evidence_paths"]
            )
        ):
            raise BoundedError("DELIVERY_LIMITATION_UNBOUND_OR_UNSCOPED")
        ids.add(item["id"])
    failed = [k for k in HARD_CHECKS if checks[k] == "FAIL"]
    pending = [k for k in HARD_CHECKS if checks[k] == "NOT_RUN"]
    independent = {"two_independent_source_denied_cold_runs", "missing_and_corrupt_fail_closed"}
    result = {
        "contract_id": CONTRACT_ID,
        "purpose": PURPOSE,
        "tables": sorted(tables),
        "limitations": limitations,
        "structural_checks": dict(checks),
        "build_eligible": not failed and not (set(pending) - independent),
        "independently_verified": not failed and not pending,
        "known_limitation_count": len(ids),
        "failed_checks": failed,
        "not_run_checks": pending,
        "consumer_admission": "NOT_GRANTED",
        "limitations_are_repaired_data": False,
    }
    result["identity"] = digest(result)
    return result


def require_use(
    assessment: Mapping[str, Any],
    *,
    table: str,
    use: str,
    scope_tokens: set[str] | None = None,
) -> None:
    """Fail closed for an affected use, even when a caller omits its selector.

    Tokens are exact keys/fields/window identities from the limitations registry.
    An absent selector asks for the whole table. This never grants frozen consumer
    admission; that remains the consumer's own versioned contract.
    """
    if assessment.get("purpose") != PURPOSE or not assessment.get("build_eligible"):
        raise BoundedError("DELIVERY_NOT_STRUCTURALLY_READY")
    if table not in assessment.get("tables", []):
        raise BoundedError("DELIVERY_TABLE_UNKNOWN")
    if use == "governance_baseline":
        return
    if use not in USES:
        raise BoundedError("DELIVERY_USE_UNKNOWN")
    for item in assessment["limitations"]:
        if (
            table in item["tables"]
            and use in item["blocked_uses"]
            and (
                not scope_tokens
                or item["scope_kind"] == "table_window"
                or bool(scope_tokens & set(item["scope"]))
            )
        ):
            raise BoundedError("DELIVERY_USE_UNAVAILABLE:" + item["id"])
