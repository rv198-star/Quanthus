"""Three clocks for the bounded history reader (issue #749).

The reader keeps three clocks separate and never conflates them:

* source knowledge - the row's own knowledge_from / publish /
  receive times, when the member carries them.
* actual read time - the wall-clock instant the reader opened the
  member bytes (always recorded, on the receipt).
* logical decision_cutoff - the predeclared consumer deadline passed
  in by the caller; the reader never derives it from max(visible_at).

When a member cannot prove a historical as-of visibility time, the
reader labels that usage ENGINEERING_REPLAY_AS_OBSERVED and stamps
visible_at from the qualified index observation, never back-filling the
source's own knowledge clock.
"""

from __future__ import annotations

from datetime import datetime, timezone

REPLAY_AS_OBSERVED = "ENGINEERING_REPLAY_AS_OBSERVED"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value) -> datetime | None:
    """Parse an ISO timestamp into an aware UTC datetime, or None.

    A naive ISO string is assumed UTC (the warehouse members write
    Z-suffixed instants); a value that cannot be parsed returns None
    and the caller marks the field UNKNOWN instead of guessing.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """ISO-8601 with a Z suffix, or None for a missing clock."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
