"""Qualify retained weekly ST captures for exact native reuse, never PIT admission.

The original dated interval and notice audit must be present locally. A repeated
window is evidence about that interval, not a new interval or an inferred change.
Unknown baselines remain unknown. The provider's reported capture clock remains
an observation claim; it cannot recover an absent original wire acquisition.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .base22 import THROUGH, digest
from .base22_bounded import BoundedError
from .base22_outputs import instant

CONTRACT_ID = "p10-base22-weekly-st-exact-native-reuse-v1"
BODY_FIELDS = (
    "interval_id",
    "stock_code",
    "exchange",
    "status_label",
    "is_st",
    "effective_start",
    "effective_end",
    "announcement_date",
    "knowledge_cutoff",
    "source",
    "source_url_or_notice_id",
    "evidence_path",
    "event_kind",
    "confidence",
    "notes",
)
MIC = {"SSE": "XSHG", "SZSE": "XSHE", "BSE": "BJSE"}


def _day(value: Any, *, nullable: bool = False) -> date | None:
    if value is None and nullable:
        return None
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise BoundedError("ST_REUSE_DATE_UNKNOWN_OR_INVALID")


def _body(row: dict[str, Any]) -> dict[str, Any]:
    if not set(BODY_FIELDS) <= set(row):
        raise BoundedError("ST_REUSE_ORIGINAL_BODY_INCOMPLETE")
    result = {k: row[k] for k in BODY_FIELDS}
    for k in ("effective_start", "effective_end", "announcement_date", "knowledge_cutoff"):
        result[k] = _day(result[k], nullable=k in {"effective_end", "announcement_date"})
    flag = result["is_st"]
    if type(flag) is int and flag in (0, 1):
        result["is_st"] = str(flag)
    elif not isinstance(flag, str) or flag not in ("0", "1"):
        raise BoundedError("ST_REUSE_FLAG_UNKNOWN")
    if (
        result["exchange"] not in MIC
        or result["confidence"] not in {"proven", "not_proven"}
        or result["status_label"] not in {"normal", "ST", "delisting_risk", "unknown"}
        or result["effective_start"] > THROUGH
        or (result["announcement_date"] is not None and result["announcement_date"] > THROUGH)
    ):
        raise BoundedError("ST_REUSE_BODY_SCOPE_INVALID")
    if result["confidence"] == "not_proven" and (
        result["status_label"] != "unknown"
        or result["event_kind"] != "no_proven_event"
        or result["source_url_or_notice_id"] is not None
    ):
        raise BoundedError("ST_REUSE_UNKNOWN_BASELINE_MUST_REMAIN_UNKNOWN")
    return result


def qualify_capture(
    capture: dict[str, Any],
    original: dict[str, Any],
    native: dict[str, Any],
    *,
    events: list[dict[str, Any]],
    listing: dict[str, Any],
    venue: dict[str, Any],
    original_source_sha256: str,
    observed_at: datetime,
) -> dict[str, Any]:
    """Prove the selected source body/notice/dated identity already exists unchanged.

    All original clocks and native atoms are hashed, not rewritten. Full source
    files/manifests, the metadata's original gate and the entire native partition
    are independently checked by the caller. This receipt grants neither whole
    August coverage nor qualification of unselected intervals/provider states.
    """
    body, captured = _body(original), _body(capture)
    if body != captured:
        raise BoundedError("ST_REUSE_CAPTURE_ORIGINAL_BODY_CHANGED")
    clock = instant(observed_at)
    provider_claim = capture.get("provider_acquired_at")
    if instant(provider_claim) > clock:
        raise BoundedError("ST_REUSE_REPORTED_CAPTURE_AFTER_OBSERVATION")
    if not capture.get("source_revision") or capture.get("provider_dataset") != "st-status":
        raise BoundedError("ST_REUSE_CAPTURE_REVISION_OR_DATASET_UNKNOWN")
    start = datetime.combine(body["effective_start"], time(), ZoneInfo("Asia/Shanghai"))
    end = (
        datetime.combine(
            body["effective_end"] + timedelta(days=1), time(), ZoneInfo("Asia/Shanghai")
        )
        if body["effective_end"] is not None
        else None
    )
    if (
        listing["listing_code"] != body["stock_code"]
        or listing["venue_id"] != venue["venue_id"]
        or venue["mic_code"] != MIC[body["exchange"]]
        or listing["security_id"] != native["security_id"]
        or not (
            listing["effective_from"] <= start
            and (listing["effective_to"] is None or start < listing["effective_to"])
        )
    ):
        raise BoundedError("ST_REUSE_EXACT_DATED_IDENTITY_VENUE_MISMATCH")
    for parent in (listing, venue):
        if not (
            parent["knowledge_from"] <= native["knowledge_from"]
            and (
                parent["knowledge_to"] is None
                or native["knowledge_from"] < parent["knowledge_to"]
            )
        ):
            raise BoundedError("ST_REUSE_PARENT_KNOWLEDGE_MISMATCH")
    expected = {
        "record_source": "st_status_pit",
        "source_revision": original["source_revision"],
        "source_artifact_sha256": original_source_sha256,
        "status_type": "SPECIAL_TREATMENT",
        "status_value": "UNKNOWN"
        if body["status_label"] == "unknown"
        else body["status_label"].upper()
        if body["is_st"] == "1"
        else "NORMAL",
        "reason_code": "BASELINE_NOT_PROVEN"
        if body["confidence"] == "not_proven"
        else body["event_kind"],
        "effective_from": start.astimezone(timezone.utc),
        "effective_to": end.astimezone(timezone.utc) if end is not None else None,
    }
    if (
        any(native.get(k) != v for k, v in expected.items())
        or not native["source_record_id"].endswith("#interval_id=" + body["interval_id"])
        or native["knowledge_from"] > clock
    ):
        raise BoundedError("ST_REUSE_NATIVE_ATOMS_OR_LINEAGE_DIFFER")
    matched = []
    if body["confidence"] == "proven":
        matched = [
            event
            for event in events
            if event["stock_code"] == body["stock_code"]
            and event["exchange"] == body["exchange"]
            and event["event_kind"] == body["event_kind"]
            and _day(event["announcement_date"]) == body["announcement_date"]
            and _day(event["effective_start_parsed"]) == body["effective_start"]
            and body["source_url_or_notice_id"] in {str(event["ann_id"]), event["url"]}
        ]
        if len(matched) != 1:
            raise BoundedError("ST_REUSE_ORIGINAL_NOTICE_NOT_UNIQUE_OR_MISSING")
    proof = {
        "contract_id": CONTRACT_ID,
        "disposition": "EXACT_NO_CHANGE_NATIVE_ENTITY_REUSE",
        "original_row_sha256": digest(original),
        "capture_row_sha256": digest(capture),
        "original_body_sha256": digest(body),
        "native_row_sha256": digest(native),
        "listing_row_sha256": digest(listing),
        "venue_row_sha256": digest(venue),
        "notice_row_sha256": digest(matched[0]) if matched else None,
        "native_status_unknown": body["confidence"] == "not_proven",
        "reported_provider_capture_clock": provider_claim,
        "original_wire_provider_acquisition": None,
        "warehouse_verified_at": clock.isoformat(),
        "native_rows_mutated": 0,
        "new_intervals_or_backfilled_PIT_times": 0,
        "historical_PIT_admission": False,
        "consumer_admission": "NOT_GRANTED",
        "whole_August_or_Base22_acceptance": False,
    }
    proof["identity"] = digest(proof)
    return proof
