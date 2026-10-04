"""Safe exact-key repair and rolling-event contracts; no image/consumer admission.

A new retained-source revision describes what CN02 can prove now. It never claims
an original accepted child, provider acquisition time, normalization scope or PIT
availability that the retained bytes do not establish.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .base22 import business_date, digest
from .base22_sources import SourceAssessmentError


def tail_repair_evidence(
    *,
    raw: Any,
    raw_sha256: str,
    listing_id: str,
    existing: list[dict[str, Any]],
    states: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    observed_at: datetime,
    version_key: list[str],
    input_closure: dict[str, str],
) -> dict[str, Any]:
    """Freeze actual missing/conflicting keys, versions and unchanged status differences.

    Existing row documents are hashed only. Reports never include numeric values.
    The source can qualify for retrospective sealed repair processing, while missing
    provider acquisition remains unknown and downstream historical PIT stays blocked.
    """
    if observed_at.tzinfo is None:
        raise SourceAssessmentError("QUALIFICATION_CLOCK_UNKNOWN")
    required = {
        "original_ods_manifest",
        "original_dwd_manifest",
        "listing_members",
        "bar_members",
        "state_members",
        "gap_members",
        "contract",
        "price_basis_policy",
        "evaluator",
    }
    if set(input_closure) != required or any(
        re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None for value in input_closure.values()
    ):
        raise SourceAssessmentError("QUALIFICATION_INPUT_CLOSURE_INVALID")
    prior: dict[Any, list[dict[str, Any]]] = {}
    state_by_date: dict[Any, list[dict[str, Any]]] = {}
    gap_by_date: dict[Any, list[dict[str, Any]]] = {}
    for row in existing:
        if row["listing_id"] != listing_id or row["interval_size"] != "1d":
            raise SourceAssessmentError("REPAIR_IDENTITY_CONFLICT")
        prior.setdefault(business_date(row["observation_start"]), []).append(row)
    for row in states:
        if row.get("listing_id") != listing_id:
            raise SourceAssessmentError("REPAIR_STATE_IDENTITY_CONFLICT")
        state_by_date.setdefault(business_date(row["observation_start"]), []).append(row)
    for row in gaps:
        if row.get("listing_id", listing_id) != listing_id:
            raise SourceAssessmentError("REPAIR_GAP_IDENTITY_CONFLICT")
        gap_by_date.setdefault(business_date(row["affected_effective_at"]), []).append(row)
    rows = raw.to_pylist()
    allowed: list[dict[str, Any]] = []
    nontrade: list[dict[str, Any]] = []
    overlap = 0
    conflicts = 0
    unchanged = 0
    seen = set()
    values = {
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
        "amount": "turnover",
    }
    for row in rows:
        day = row["trade_date"]
        if day in seen:
            raise SourceAssessmentError("REPAIR_RAW_DUPLICATE_KEY")
        seen.add(day)
        nontrading = (
            row["tradestatus"] != "1" or row["volume"] == Decimal(0) or row["amount"] == Decimal(0)
        )
        if nontrading:
            nontrade.append(
                {
                    "trade_date": str(day),
                    "raw_nontrading": row["tradestatus"] != "1",
                    "raw_zero_volume": row["volume"] == Decimal(0),
                    "raw_zero_turnover": row["amount"] == Decimal(0),
                    "retained_states": [
                        {
                            "is_tradable": s["is_tradable"],
                            "reason_code": s["reason_code"],
                            "key_sha256": digest(
                                [s["listing_id"], s["calendar_session_id"], s["knowledge_from"]]
                            ),
                            "state_control_projection_sha256": digest(s),
                        }
                        for s in state_by_date.get(day, [])
                    ],
                    "retained_gaps": [
                        {"gap_reason": g["gap_reason"], "row_sha256": digest(g)}
                        for g in gap_by_date.get(day, [])
                    ],
                    "disposition": "PRESERVE_STATE_DIFFERENCE_REQUIRE_RECONCILIATION_NO_FLAG_FLIP",
                }
            )
        candidates = prior.get(day, [])
        incoming = digest([row["stock_code"], day, {k: row[k] for k in values}, raw_sha256])
        if not candidates:
            allowed.append(
                {
                    "trade_date": str(day),
                    "listing_id": listing_id,
                    "operation": "INSERT_NEW_RETAINED_SOURCE_KNOWLEDGE_VERSION",
                    "raw_key_value_sha256": incoming,
                    "original_provider_acquisition": None,
                    "new_warehouse_observed_at": observed_at.isoformat(),
                    "is_tradable_override_allowed": False,
                }
            )
            continue
        overlap += 1
        for old in candidates:
            different = [k for k, target in values.items() if row[k] != old[target]]
            if not different:
                unchanged += 1
                continue
            conflicts += 1
            allowed.append(
                {
                    "trade_date": str(day),
                    "listing_id": listing_id,
                    "operation": "RETAIN_OLD_VALUE_VERSION_APPEND_NEW_VALUE_VERSION",
                    "changed_fields": different,
                    "old_version_key_sha256": digest([old[k] for k in version_key]),
                    "before_row_sha256": digest(old),
                    "incoming_raw_key_value_sha256": incoming,
                    "new_warehouse_observed_at": observed_at.isoformat(),
                    "original_provider_acquisition": None,
                    "old_source": {
                        k: old[k]
                        for k in ["record_source", "source_revision", "source_artifact_sha256"]
                    },
                    "priority": (
                        "exact retained unadjusted Baostock raw on listed keys only; "
                        "prior version retained"
                    ),
                    "historical_knowledge_time_replacement_allowed": False,
                }
            )
    body = {
        "schema_version": "p10-base22-retained-tail-qualification.v1",
        "purpose": "sealed_retrospective_data_quality_repair_not_historical_PIT",
        "source_raw_sha256": raw_sha256,
        "input_closure": input_closure,
        "raw_schema_sha256": digest(str(raw.schema.remove_metadata())),
        "listing_id": listing_id,
        "date_scope": {"start": str(min(seen)), "end": str(max(seen))} if seen else None,
        "qualified_at": observed_at.isoformat(),
        "source_provider": "baostock",
        "adjustflag": "3",
        "price_basis": "UNADJUSTED",
        "raw_rows": len(rows),
        "overlap_dates": overlap,
        "missing_dates": len(seen - set(prior)),
        "conflicting_existing_versions": conflicts,
        "unchanged_existing_versions": unchanged,
        "provider_acquisition": "UNAVAILABLE",
        "historical_accepted_child": "NOT_ASSERTED",
        "source_priority": (
            "raw only at exact frozen missing/conflicting dated keys; all other history retained"
        ),
        "allowed_operations": allowed,
        "nontrading_or_zero_activity": nontrade,
        "state_mutations": 0,
        "quality_gate_472": "BLOCKED_PENDING_CALENDAR_STATE_REPAIR_OUTPUT_CLOSURE",
        "consumer_admission": "NOT_GRANTED",
        "legacy_promoted_metadata_rewritten": False,
        "historical_measurement": {
            "through_2021_12_31": 675,
            "three_security_total": 2025,
            "status": "previously measured; original temporary files unavailable for recheck",
            "not_the_current_full_tail_window": True,
        },
    }
    return {**body, "new_revision_identity": digest(body)}


def rolling_revision_contract(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Key/body versions retain every genuine source acquisition observation.

    Identical snapshots share a body version, not an invented earlier cutoff. A
    different body is a revision. Equal-clock conflicting bodies fail closed. No
    row body, title or market value leaves this function.
    """
    groups: dict[tuple[str, str], dict[str, list[dict[str, str]]]] = {}
    clock_versions: dict[tuple[str, str, str], str] = {}
    for row in observations:
        key = (row["stock_code"], row["ann_id"])
        acquired = row.get("acquired_at")
        if not acquired:
            raise SourceAssessmentError("EVENT_ACQUISITION_UNKNOWN")
        try:
            clock = datetime.fromisoformat(acquired)
            if clock.tzinfo is None:
                raise ValueError
        except ValueError:
            raise SourceAssessmentError("EVENT_ACQUISITION_INVALID") from None
        body = {
            k: v
            for k, v in row.items()
            if k not in {"acquired_at", "artifact_sha256", "release_id"}
        }
        body_sha = digest(body)
        coordinate = (*key, clock.astimezone(timezone.utc).isoformat())
        if coordinate in clock_versions and clock_versions[coordinate] != body_sha:
            raise SourceAssessmentError("EVENT_EQUAL_CLOCK_CONFLICT")
        clock_versions[coordinate] = body_sha
        observation = {
            "acquired_at": acquired,
            "artifact_sha256": row["artifact_sha256"],
            "release_id": row["release_id"],
        }
        versions = groups.setdefault(key, {})
        retained = versions.setdefault(body_sha, [])
        if observation not in retained:
            retained.append(observation)
    return {
        "schema_version": "p10-base22-rolling-event-revision.v1",
        "business_key": ["stock_code", "ann_id"],
        "body_excludes": ["acquired_at", "artifact_sha256", "release_id"],
        "same_body_rule": "deduplicate content; retain every acquisition/source observation",
        "different_body_rule": "retain true knowledge revisions; equal-clock conflict blocks",
        "source_priority": (
            "ordered genuine acquisition observations; never earliest clock for a later body"
        ),
        "key_count": len(groups),
        "body_version_count": sum(len(v) for v in groups.values()),
        "version_count_census": dict(Counter(len(v) for v in groups.values())),
        "keys": [
            {
                "stock_code": key[0],
                "ann_id": key[1],
                "versions": [
                    {
                        "body_sha256": sha,
                        "observations": sorted(
                            obs,
                            key=lambda x: (
                                datetime.fromisoformat(x["acquired_at"]).astimezone(timezone.utc),
                                x["artifact_sha256"],
                            ),
                        ),
                    }
                    for sha, obs in sorted(versions.items())
                ],
            }
            for key, versions in sorted(groups.items())
        ],
        "projection_admission": "NOT_GRANTED_BY_REVISION_CONTRACT",
    }
