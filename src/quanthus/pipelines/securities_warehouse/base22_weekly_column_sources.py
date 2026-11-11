"""Whole retained weekly selection; literal holder ratios remain explicitly based."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_f10_ownership import CATEGORIES as CATEGORIES
from .base22_f10_ownership import TABLES as TABLES
from .base22_f10_ownership import ownership_observation as capital_observation
from .base22_holder_column_continuation import holder_observation
from .financial_reporting import FinancialCompanyResolver

CONTRACT_ID = "p10-base22-weekly-column-ownership-source-v3"


def ownership_observation(
    raw: dict[str, Any], *, source_sha256: str, resolver: FinancialCompanyResolver
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Project once; never turn a typed source scope into whole-domain admission."""
    if raw.get("category") not in CATEGORIES:
        raise BoundedError("WEEKLY_LITERAL_EXACT_CATEGORY_REQUIRED")
    if raw["category"] == "股本结构":
        rows, predecessor = capital_observation(raw, source_sha256=source_sha256, resolver=resolver)
        scalar_classes = predecessor["scalar_classes"]
    else:
        rows, predecessor = holder_observation(raw, source_sha256=source_sha256, resolver=resolver)
        scalar_classes = dict(
            Counter(
                "EXPLICIT_NULL_PERCENTAGE"
                if r["holding_percentage"] is None
                else "EXACT_ZERO_PERCENTAGE"
                if r["holding_percentage"] == 0
                else "EXACT_FINITE_LITERAL_PERCENTAGE"
                for r in rows[TABLES[1]]
            )
        )
    proof = {
        **predecessor,
        "contract_id": CONTRACT_ID,
        "predecessor_projector_contract": predecessor["contract_id"],
        "scalar_classes": scalar_classes,
        "separate_nonempty_domains": predecessor.get("separate_nonempty_domains", []),
        "whole_domain_admission": "NOT_GRANTED",
        "native_history_union": "SEPARATE_GATE",
    }
    proof.pop("identity", None)
    proof["identity"] = digest(proof)
    return rows, proof
