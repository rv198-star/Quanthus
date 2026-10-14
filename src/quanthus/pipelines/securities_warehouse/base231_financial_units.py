"""Exact dimensionless financial ratios; monetary currency and old PIT stay held."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Any

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_outputs import instant
from .base22_precision import decimal_profile
from .base231_financial_summary import NAMESPACE
from .listed_company import _stable_id

CONTRACT_ID = "p10-base231-explicit-percent-financial-ratio-v1"


def qualify_ratio(
    fact: dict[str, Any], concept: dict[str, Any], *, observed_at: datetime,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Create separate current observations; never edit literal source facts."""
    clock = instant(observed_at)
    if (concept["financial_concept_id"] != fact["financial_concept_id"]
            or concept["taxonomy_namespace"] != NAMESPACE
            or concept["statement_type"] != "SOURCE_LITERAL_FINANCIAL_SUMMARY"
            or fact["source_artifact_sha256"] != concept["source_artifact_sha256"]
            or instant(fact["knowledge_from"]) > clock
            or instant(concept["knowledge_from"]) > clock
            or fact["period_end"] > date(2026, 9, 30)):
        raise BoundedError("FINANCIAL_UNIT_LITERAL_SOURCE_OR_CLOCK_CHANGED")
    dimensions = json.loads(fact["dimensions_json"])
    label = dimensions.get("source_literal_label", "")
    if (fact["unit_code"] != "PERCENT" or not label.endswith("(%)")
            or dimensions.get("unit_authority") != "EXPLICIT_LITERAL_COLUMN_LABEL"
            or label != concept["concept_name"]):
        raise BoundedError("FINANCIAL_UNIT_CURRENCY_OR_SCALE_UNPROVEN")
    value = fact["value_decimal"]
    if value is not None:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise BoundedError("FINANCIAL_UNIT_NATIVE_NUMBER_INVALID")
        with localcontext() as context:
            context.prec = 96
            value = value / Decimal(100)
        if not decimal_profile(value, precision=38, scale=10)["fits"]:
            raise BoundedError("FINANCIAL_UNIT_EXACT_NATIVE_RANGE_HELD")
    source_fact_sha = digest(fact)
    dims = {**dimensions, "unit_authority": CONTRACT_ID,
            "source_literal_fact_sha256": source_fact_sha,
            "source_literal_unit": "PERCENT", "exact_scale_multiplier": "0.01",
            "currency": "NOT_APPLICABLE_DIMENSIONLESS"}
    concept_id = _stable_id("financial_concept", concept["financial_concept_id"], CONTRACT_ID)
    common = dict(knowledge_from=clock, knowledge_to=None, source_revision=CONTRACT_ID)
    new_concept = {**concept, **common, "financial_concept_id": concept_id,
                   "taxonomy_namespace": "quanthus.f10.explicit_percent.ratio.v1",
                   "taxonomy_version": CONTRACT_ID,
                   "concept_code": concept["concept_code"] + "_ratio",
                   "concept_name": concept["concept_name"] + " [dimensionless ratio]",
                   "source_record_id": concept["source_record_id"] + ":ratio-v1"}
    new_fact = {**fact, **common,
                "financial_fact_id": _stable_id("financial_fact", fact["financial_fact_id"],
                                                CONTRACT_ID),
                "financial_concept_id": concept_id, "unit_code": "RATIO",
                "value_decimal": value, "dimensions_json": json.dumps(
                    dims, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "source_record_id": fact["source_record_id"] + ":ratio-v1"}
    proof = dict(contract_id=CONTRACT_ID, source_fact_sha256=source_fact_sha,
                 source_concept_sha256=digest(concept), output_fact_sha256=digest(new_fact),
                 output_concept_sha256=digest(new_concept), exact_scale_multiplier="0.01",
                 source_values_and_times_unchanged=True, currency_CNY_inferred=False,
                 original_publication=fact["published_at"],
                 historical_PIT_admission="NOT_GRANTED", consumer_admission="NOT_GRANTED")
    return {"dwd_financial_concept": [new_concept], "dwd_financial_fact": [new_fact]}, proof
