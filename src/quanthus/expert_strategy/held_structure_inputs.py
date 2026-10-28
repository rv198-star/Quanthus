"""Adapt the already accepted historical raw lane without repairing shared data."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from quanthus.structure_v2_inputs import DailyInput, digest, exact_sha


def historical_daily_inputs(
    *,
    code: str,
    validity: dict[str, Any],
    bars: list[dict[str, Any]],
    sessions: list[date],
    through: date,
    source_cutoff: datetime,
    raw_member_sha256: str,
    calendar_member_sha256: str,
    action_closure_sha256: str,
    action_boundaries: dict[date, tuple[str, ...]],
    blocked_intervals: tuple[tuple[date, date, str], ...] = (),
) -> tuple[DailyInput, ...]:
    """Preserve raw binary64's declared Decimal(str) convention, not new precision.

    The listing reference identifies the exact native accepted validity interval;
    it does not pretend that this historical bundle contains a native DWD listing ID.
    Missing observations enumerate the real per-venue calendar and remain missing.
    """
    if (
        validity["stock_code"] != code
        or validity["security_id"] != code
        or validity["exchange"] not in {"SSE", "SZSE"}
        or not all(
            exact_sha(s) for s in (raw_member_sha256, calendar_member_sha256, action_closure_sha256)
        )
        or source_cutoff.tzinfo is None
        or source_cutoff.utcoffset() is None
        or sessions != sorted(set(sessions))
        or through > date(2021, 12, 30)
    ):
        raise ValueError("HELD_SOURCE_IDENTITY_OR_SCOPE")
    native_start = date.fromisoformat(validity["valid_from"])
    native_end = validity["valid_to"]
    # Native security-code-validity.v1 defines NULL as an open interval, without
    # requiring its separate report-window clipping flag to be true.
    if native_end is not None and not isinstance(native_end, date):
        native_end = date.fromisoformat(native_end)
    listing_ref = "accepted-validity-interval:" + digest(validity)
    by_day: dict[date, dict[str, Any]] = {}
    allowed_days = set(sessions)
    for bar in bars:
        day = bar["date"]
        if (
            bar["stock_code"] != code
            or bar["security_id"] != code
            or bar["identity_exchange"] != validity["exchange"]
            or bar["adj_mode"] != "none"
            or day not in allowed_days
            or day < native_start
            or (native_end is not None and day > native_end)
            or day > through
            or day in by_day
        ):
            raise ValueError("HELD_RAW_DATE_VENUE_LISTING_OR_DUPLICATE")
        by_day[day] = bar
    epoch = digest({"listing": listing_ref, "start": native_start})
    output = []
    for index, day in enumerate(sessions):
        if day > through or day < max(date(2011, 1, 4), native_start):
            continue
        if native_end is not None and day > native_end:
            raise ValueError("HELD_REQUEST_OUTSIDE_LISTING_INTERVAL")
        boundary = action_boundaries.get(day)
        if boundary:
            epoch = digest({"listing": listing_ref, "action_date": day, "members": boundary})
        row = by_day.get(day)
        blocked = [reason for start, end, reason in blocked_intervals if start <= day <= end]
        quality = (
            "retained_action_exception" if blocked else ("missing_raw_bar" if row is None else None)
        )
        values = {
            name: None if row is None or row[name] is None else Decimal(str(row[name]))
            for name in ("high", "low", "close", "volume")
        }
        if row is not None and values["volume"] is not None and values["volume"] <= 0:
            quality = quality or "zero_activity_not_observed_trading"
        output.append(
            DailyInput(
                code,
                day,
                index,
                source_cutoff,
                validity["security_id"],
                listing_ref,
                validity["exchange"],
                **values,
                source_record_id=digest(
                    {
                        "member": raw_member_sha256,
                        "code": code,
                        "date": day,
                        "source_built_at": None if row is None else row["built_at"],
                        "built_at_semantics": "artifact_generation_not_knowledge",
                    }
                ),
                source_revision="accepted-p3-legacy-raw-none-v1",
                source_artifact_sha256=raw_member_sha256,
                dwd_known_from="unavailable",
                coordinate_epoch=epoch,
                finality="final",
                quality_reason=quality,
                reset_reason="declared_action_boundary" if boundary else None,
                calendar_member_digest=calendar_member_sha256,
                action_member_digest=action_closure_sha256,
            )
        )
    return tuple(output)
