"""Dated September static snapshots, with native history left unchanged."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import digest
from .base22_bounded import BoundedError
from .base22_financial import _clock
from .base23_sources import START, THROUGH

CONTRACT_ID = "p10-base23-retained-dated-static-industry-v1"
QUALIFICATION = "RETAINED_PROVIDER_STATIC_SNAPSHOTS_NOT_HISTORICAL_PIT"
TABLE = "dwd_company_industry_membership"
FIELDS = {
    "security_id", "stock_code", "stock_name", "industry_code", "industry_name",
    "classification", "provider_update_date", "market_date", "source", "source_revision",
    "acquired_at",
}


def snapshot_observation(
    raw: dict[str, Any], *, day: date, parent: Any, node: dict[str, Any],
    observed_at: datetime, source_sha256: str, revision: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Only the declared as-of date, exact node and dated company qualify.

    Capture and provider-update fields are retained in local evidence. A provider
    update date is never a historical membership start or a PIT cutoff.
    """
    if set(raw) != FIELDS:
        raise BoundedError("SEPTEMBER_INDUSTRY_EXACT_SOURCE_SCHEMA_REQUIRED")
    if (
        type(day) is not date or not START <= day <= THROUGH
        or raw["market_date"] != str(day)
        or not isinstance(raw["stock_code"], str)
        or re.fullmatch(r"\d{6}", raw["stock_code"]) is None
        or raw["security_id"] != raw["stock_code"]
        or raw["classification"] != "证监会行业分类"
        or raw["source"] != "baostock.query_stock_industry"
        or not raw["source_revision"] or not revision
        or re.fullmatch(r"sha256:[0-9a-f]{64}", source_sha256) is None
    ):
        raise BoundedError("SEPTEMBER_INDUSTRY_DATE_IDENTITY_SOURCE_UNQUALIFIED")
    if (
        parent is None or raw["industry_code"] != node.get("node_code")
        or raw["industry_name"] != node.get("node_name")
    ):
        raise BoundedError("SEPTEMBER_INDUSTRY_DATED_PARENT_OR_EXACT_NODE_UNAVAILABLE")
    acquired, observed = _clock(raw["acquired_at"]), _clock(observed_at)
    start = datetime.combine(day, time(), ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
    if (
        acquired > observed or parent.knowledge_from > observed
        or node["knowledge_from"] > observed
        or node["effective_from"] > start
        or node["effective_to"] is not None and start >= node["effective_to"]
        or node["knowledge_to"] is not None and observed >= node["knowledge_to"]
    ):
        raise BoundedError("SEPTEMBER_INDUSTRY_PARENT_OR_NODE_TIME_UNAVAILABLE")
    row = {
        "record_source": "base23_weekly/dated_static_industry",
        "source_record_id": digest([raw["stock_code"], day, raw["industry_code"]]),
        "source_revision": revision, "source_artifact_sha256": source_sha256,
        "knowledge_from": observed, "knowledge_to": None,
        "effective_from": start, "effective_to": None,
        "company_id": parent.company_id, "industry_node_id": node["industry_node_id"],
        "membership_type": "PRIMARY_CLASSIFICATION", "confidence": None,
        "source_declared_as_of": day,
    }
    proof = {
        "contract_id": CONTRACT_ID, "raw_row_sha256": digest(raw),
        "typed_row_sha256": digest(row), "original_acquired_at": raw["acquired_at"],
        "actual_acquisition_instant": acquired,
        "original_provider_update_date": raw["provider_update_date"],
        "original_source_revision": raw["source_revision"],
        "actual_governance_observation": observed,
        "historical_PIT_qualified": False, "native_history_rewritten": False,
    }
    return row, proof
