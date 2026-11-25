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
# Formal daily update (issue #749 second segment): the production T
# close read.  The call value is "production_daily" (the label P11 and
# the read entry already use); the constant only names it in code.  The
# decision_cutoff is the formal deadline (e.g. 14:30Z); a package is
# admissible only when its QUALIFIED channel visibility is at or before
# the cutoff, and the version selected is the last revision known by
# the cutoff.  Production-complete, channel publication/withdrawal and
# this run's read/verify instants are recorded separately - never
# conflated.
FORMAL_DAILY_UPDATE = "production_daily"


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


class VerificationClock:
    """Per-member verification timestamp source (P11 intake item 1).

    The job-level ``read_at`` names when the RUN started; ``verify_at``
    names when THIS member's sha256 finished re-hashing.  Every call to
    ``SourceMap.verify_member`` stamps the wall-clock instant the hash
    completed - never the shared run-start clock.  A member that was
    registered but never hash-checked carries ``verify_at=None`` on the
    consumption closure, distinct from the run-start clock recorded
    separately on the receipt."""

    @staticmethod
    def now() -> datetime:
        return utcnow()
