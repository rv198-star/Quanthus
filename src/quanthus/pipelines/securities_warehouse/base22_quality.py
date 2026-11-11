"""New negative governance observations; never positive industry or PIT admission."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, canonical, digest
from .base22_bounded import BoundedError
from .base22_financial import _clock

CONTRACT_ID = "p10-base22-unavailable-governance-v1"
TABLE = "dwd_quality_exception"


def _exception(
    *,
    key: dict[str, Any],
    effective: date,
    detected_at: Any,
    rule: str,
    domain: str,
    affected_table: str,
    source_sha256: str,
    evidence: Any,
) -> dict[str, Any]:
    if not isinstance(effective, date) or isinstance(effective, datetime) or effective > THROUGH:
        raise BoundedError("QUALITY_BUSINESS_DATE_INVALID")
    clock = _clock(detected_at)
    if datetime.combine(effective, time(), ZoneInfo("Asia/Shanghai")) > clock:
        raise BoundedError("QUALITY_DETECTION_PRECEDES_EFFECTIVE_DATE")
    key_json = canonical(key).decode("utf-8")
    return {
        "record_source": "base22_quality/negative_source_governance",
        "source_record_id": digest([rule, key]),
        "source_revision": CONTRACT_ID,
        "source_artifact_sha256": source_sha256,
        "knowledge_from": clock,
        "knowledge_to": None,
        "affected_effective_at": datetime.combine(effective, time(), ZoneInfo("Asia/Shanghai")),
        "detected_at": clock,
        "quality_exception_id": "quality_exception_" + digest([CONTRACT_ID, rule, key])[7:],
        "affected_table": affected_table,
        "affected_business_key_json": key_json,
        "rule_id": rule,
        "classification": "SOURCE_INFORMATION_UNAVAILABLE",
        "severity": "ERROR",
        "authority": "P10_SOURCE_GOVERNANCE_ONLY",
        "evidence_digest": digest(evidence),
        "resolution_state": "UNRESOLVED",
        "rule_version": CONTRACT_ID,
        "affected_domain": domain,
        "disposition": "PRESERVE_UNAVAILABLE_NO_CONSUMER_ADMISSION",
    }


def visibility_exception(
    raw: dict[str, Any],
    *,
    detected_at: Any,
    source_sha256: str,
) -> dict[str, Any]:
    """Keep an exact existing fail-closed identity without supplying a cutoff.

    The new observation time belongs to this governance assessment. The original
    visibility row (including recorded_at and numeric evidence) stays in the local
    source archive, unchanged; a raw exception key is not an invented factor ID.
    """
    if (
        raw.get("exception_type") != "factor_pit_visibility"
        or raw.get("reason_code") != "no_proveable_announce_after_em_exact_link"
        or raw.get("visibility_origin") != "harvest_day_exception|no_proveable_announce"
        or raw.get("policy")
        != "factor_unavailable_before_knowledge_cutoff; no forge; degrade/block adj features"
    ):
        raise BoundedError("QUALITY_ORIGINAL_VISIBILITY_NOT_UNAVAILABLE")
    key = {n: raw[n] for n in ("stock_code", "exchange", "effective_date", "source_revision")}
    key["original_declared_cutoff_not_proven_availability"] = raw.get("knowledge_cutoff")
    key["scope"] = "original_visibility_exception_key_not_inferred_native_factor_id"
    effective = raw["effective_date"]
    if isinstance(effective, str):
        try:
            effective = date.fromisoformat(effective)
        except ValueError:
            raise BoundedError("QUALITY_ORIGINAL_EFFECTIVE_DATE_INVALID") from None
    return _exception(
        key=key,
        effective=effective,
        detected_at=detected_at,
        rule="P10-474-KEEP-VISIBILITY-UNAVAILABLE",
        domain="disclosure_company_event",
        affected_table="dwd_adjustment_factor",
        source_sha256=source_sha256,
        evidence=raw,
    )


def industry_exception(
    metadata: dict[str, Any],
    *,
    listing: dict[str, Any],
    detected_at: Any,
    source_sha256: str,
    snapshot_at: datetime,
) -> dict[str, Any]:
    """Represent unqualified historical provenance, without writing its industry.

    Only exact native identities whose listing ended before the actual industry
    snapshot qualify for this negative observation. The supplied research mapping
    is evidence of an unresolved source claim, never a historical industry fact.
    """
    identity = metadata["identity_provenance"]
    if (
        identity["security_id"] != listing["security_id"]
        or identity["listing_code"] != listing["listing_code"]
        or listing["effective_to"] is None
        or listing["effective_to"] >= snapshot_at
        or metadata.get("native_industry_present") is not False
        or not any(b["listing_id"] == listing["listing_id"] for b in identity["listing_bindings"])
    ):
        raise BoundedError("QUALITY_INDUSTRY_EXACT_HISTORICAL_IDENTITY_FAILED")
    key = {
        "listing_id": listing["listing_id"],
        "security_id": listing["security_id"],
        "scope": "historical_industry_source_claim_not_an_accepted_membership",
    }
    return _exception(
        key=key,
        effective=listing["effective_from"].astimezone(ZoneInfo("Asia/Shanghai")).date(),
        detected_at=detected_at,
        rule="P10-470-HISTORICAL-INDUSTRY-SOURCE-UNQUALIFIED",
        domain="listed_company",
        affected_table="dwd_company_industry_membership",
        source_sha256=source_sha256,
        evidence={
            "metadata": metadata,
            "native_listing": listing,
            "snapshot_at": snapshot_at,
            "positive_qualification": False,
        },
    )
