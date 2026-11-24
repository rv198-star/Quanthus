"""Per-member clock admission for the daily advice producer (057 intake rule).

The caller declares the decision cutoff in advance.  Each member must have a concrete
consumer-visible `available_at` at or before that cutoff; a member whose visibility clock is
missing or straddles the cutoff is never inferred.  A blocked *required* member closes the
whole use: the caller must treat the result as not publishable rather than emit a formal
ended/empty package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from quanthus.product_daily.recommendation.contracts import MemberEvidence


class AdmissionDisposition(str, Enum):
    PUBLISHABLE = "publishable"
    INPUT_NOT_READY = "input_not_ready"


@dataclass(frozen=True)
class MemberClock:
    member_id: str
    admitted: bool
    required: bool
    reason: str | None
    available_at: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "member_id": self.member_id,
            "admitted": self.admitted,
            "required": self.required,
            "reason": self.reason,
            "available_at": self.available_at,
        }


@dataclass(frozen=True)
class MemberAssessment:
    disposition: AdmissionDisposition
    admitted_members: tuple[MemberEvidence, ...]
    blocked_required: tuple[MemberClock, ...]
    blocked_optional: tuple[MemberClock, ...]
    latest_admitted_available_at: datetime | None

    @property
    def publishable(self) -> bool:
        return self.disposition is AdmissionDisposition.PUBLISHABLE


def assess_members(
    members: tuple[MemberEvidence, ...],
    decision_cutoff: datetime,
) -> MemberAssessment:
    """Admit each member iff its own available_at is at or before the cutoff.

    A late-arriving member blocks only decisions that depend on it; a blocked *required*
    member makes the whole use INPUT_NOT_READY.  A member whose evidence window straddles the
    cutoff is treated as not qualified (the caller passes the member's concrete visible
    available_at, never an inferred one).
    """

    admitted: list[MemberEvidence] = []
    blocked_required: list[MemberClock] = []
    blocked_optional: list[MemberClock] = []
    for member in members:
        if member.available_at <= decision_cutoff:
            admitted.append(member)
            continue
        clock = MemberClock(
            member_id=member.member_id,
            admitted=False,
            required=member.required,
            reason="member_clock_blocked",
            available_at=member.available_at.isoformat(),
        )
        if member.required:
            blocked_required.append(clock)
        else:
            blocked_optional.append(clock)
    latest = max((m.available_at for m in admitted), default=None)
    if blocked_required:
        return MemberAssessment(
            AdmissionDisposition.INPUT_NOT_READY,
            tuple(admitted),
            tuple(blocked_required),
            tuple(blocked_optional),
            latest,
        )
    return MemberAssessment(
        AdmissionDisposition.PUBLISHABLE,
        tuple(admitted),
        (),
        tuple(blocked_optional),
        latest,
    )
