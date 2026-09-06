"""B1-R temporal/value contracts, not a DWD adapter or production admission gate.

Business observations have no consumer or wall-clock creation fields. Source-version clocks are
metadata. Generation, delivery and use belong to evidence_consumption receipts/events. These
pure checks cannot authenticate source receipts or establish a complete dependency universe.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from quanthus.structure_semantics import ClockView


def require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")


def require_instant(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported temporal-contract value: {type(value).__name__}")


def contract_digest(value: object) -> str:
    payload = json.dumps(
        value, default=_json_value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True)
class TimeEvidence:
    """Closed conservative bounds for one availability event, with truthful source precision.

    A date-only source uses bounds, never an invented midnight arrival. Equal timestamps do not
    prove happens-before; B2 may add verified sequence/causal receipt support. A reference here is
    not authentication of its contents.
    """

    not_before: datetime
    not_after: datetime
    precision: str
    evidence_ref: str

    def __post_init__(self) -> None:
        require_instant(self.not_before, "not_before")
        require_instant(self.not_after, "not_after")
        require_text(self.evidence_ref, "evidence_ref")
        if self.not_after < self.not_before:
            raise ValueError("time evidence bounds are reversed")
        if self.precision not in {"instant", "bounded", "date"}:
            raise ValueError("unrecognized time precision")
        if (self.precision == "instant") != (self.not_before == self.not_after):
            raise ValueError("precision and time bounds disagree")

    def definitely_before(self, cutoff: datetime) -> bool:
        require_instant(cutoff, "cutoff")
        return self.not_after < cutoff


@dataclass(frozen=True)
class ObservationWindow:
    start: datetime
    end: datetime
    include_start: bool
    include_end: bool
    calendar_ref: str
    exchange_sessions: int
    observed_bars: int

    def __post_init__(self) -> None:
        require_instant(self.start, "window.start")
        require_instant(self.end, "window.end")
        require_text(self.calendar_ref, "calendar_ref")
        if self.end <= self.start:
            raise ValueError("observation window must be nonempty")
        if type(self.include_start) is not bool or type(self.include_end) is not bool:
            raise ValueError("window boundaries must be explicit Booleans")
        for name in ("exchange_sessions", "observed_bars"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        # Do not equate counts: one session may contain many intraday bars or no valid bar.


@dataclass(frozen=True)
class BusinessTime:
    """Time belonging to the meaning of a result; never a consumption or ingestion record."""

    profile: str
    result_as_of: datetime
    window: ObservationWindow | None = None
    event_at: datetime | None = None
    confirmation_event_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    finality: str = "final"

    def __post_init__(self) -> None:
        if self.profile not in {"measurement", "reference", "process_observation"}:
            raise ValueError("unknown temporal profile")
        if self.finality not in {"partial", "final", "unknown"}:
            raise ValueError("unknown observation finality")
        require_instant(self.result_as_of, "result_as_of")
        for name in ("event_at", "confirmation_event_at", "valid_from", "valid_to"):
            value = getattr(self, name)
            if value is not None:
                require_instant(value, name)
        if self.window is not None:
            if not isinstance(self.window, ObservationWindow):
                raise ValueError("window must be an ObservationWindow")
            if self.window.end > self.result_as_of:
                raise ValueError("observation window extends beyond result_as_of")
        if self.event_at is not None and self.event_at > self.result_as_of:
            raise ValueError("observed event cannot follow result_as_of")
        if self.confirmation_event_at is not None and (
            self.event_at is None or self.confirmation_event_at < self.event_at
        ):
            raise ValueError("confirmation must refer to an earlier or equal event")
        if self.profile == "process_observation" and self.event_at is None:
            raise ValueError("process observation requires event_at")
        if self.valid_to is not None and (
            self.valid_from is None or self.valid_to <= self.valid_from
        ):
            raise ValueError("validity interval must be nonempty and half-open")
        # Future-effective references and delayed pivots are legal. Their visibility is separate.

    def confirmed_by(self, cutoff: datetime) -> bool:
        require_instant(cutoff, "cutoff")
        required = max(self.result_as_of, self.confirmation_event_at or self.result_as_of)
        return required <= cutoff


@dataclass(frozen=True)
class ObservationRevision:
    """Small semantic identity contract; not a replacement for the existing v1 Fact rows.

    Value/closure digests are supplied by the producer. Computation/package/publication/use times
    never participate. B2 must verify the content behind every reference and digest.
    """

    subject_id: str
    listing_context: str
    concept_id: str
    concept_version: str
    spec_digest: str
    coordinate_ref: str
    business_time: BusinessTime
    source_closure_digest: str
    value_digest: str

    def __post_init__(self) -> None:
        for name in (
            "subject_id", "listing_context", "concept_id", "concept_version",
            "spec_digest", "coordinate_ref", "source_closure_digest", "value_digest",
        ):
            require_text(getattr(self, name), name)
        if not isinstance(self.business_time, BusinessTime):
            raise ValueError("business_time must be typed")
        for name in ("spec_digest", "source_closure_digest", "value_digest"):
            value = getattr(self, name)
            if len(value) != 71 or not value.startswith("sha256:") or any(
                char not in "0123456789abcdef" for char in value[7:]
            ):
                raise ValueError(f"{name} must be an exact SHA-256 digest")

    @property
    def observation_key(self) -> str:
        return contract_digest({
            "subject_id": self.subject_id,
            "listing_context": self.listing_context,
            "concept_id": self.concept_id,
            "concept_version": self.concept_version,
            "spec_digest": self.spec_digest,
            "coordinate_ref": self.coordinate_ref,
            "result_as_of": self.business_time.result_as_of,
        })

    @property
    def revision_id(self) -> str:
        return contract_digest(asdict(self))


class QueryPurpose(str, Enum):
    RECOMPUTATION = "counterfactual_recomputation"
    ACTUAL_CONSUMPTION_AUDIT = "historical_actual_consumption_audit"


@dataclass(frozen=True)
class TemporalQuery:
    """Consumer query context, not a field embedded in each market observation."""

    knowledge_cutoff: datetime
    view: ClockView
    purpose: QueryPurpose
    selection_policy_ref: str

    def __post_init__(self) -> None:
        require_instant(self.knowledge_cutoff, "knowledge_cutoff")
        ClockView(self.view)
        QueryPurpose(self.purpose)
        require_text(self.selection_policy_ref, "selection_policy_ref")
        if (
            self.purpose == QueryPurpose.ACTUAL_CONSUMPTION_AUDIT
            and self.view != ClockView.PLATFORM_AS_KNOWN
        ):
            raise ValueError("actual historical use requires platform knowledge evidence")


@dataclass(frozen=True)
class SourceVersionClock:
    """Metadata for one source version; selection intervals are supplied, never inferred.

    A complete, authorized revision family and trustworthy interval/receipt evidence must be
    established by B2. Passing this object's local checks does not prove either condition.
    """

    source_key: str
    revision_id: str
    public_from: TimeEvidence | None = None
    public_to: TimeEvidence | None = None
    platform_from: TimeEvidence | None = None
    platform_to: TimeEvidence | None = None

    def __post_init__(self) -> None:
        require_text(self.source_key, "source_key")
        require_text(self.revision_id, "revision_id")
        for prefix in ("public", "platform"):
            start, end = getattr(self, f"{prefix}_from"), getattr(self, f"{prefix}_to")
            for mark in (start, end):
                if mark is not None and not isinstance(mark, TimeEvidence):
                    raise ValueError("version clocks must reference TimeEvidence")
            if end is not None and (start is None or end.not_before <= start.not_after):
                raise ValueError("version interval ordering is unproven or empty")

    def interval(self, view: ClockView) -> tuple[TimeEvidence | None, TimeEvidence | None]:
        if view == ClockView.PUBLIC_RECONSTRUCTION:
            return self.public_from, self.public_to
        if view == ClockView.PLATFORM_AS_KNOWN:
            return self.platform_from, self.platform_to
        raise ValueError("retrospective selection requires an explicit version pin")


def select_source_version(
    versions: tuple[SourceVersionClock, ...],
    query: TemporalQuery,
    *,
    retrospective_pin: str | None = None,
) -> SourceVersionClock:
    """Pure fail-closed selector over a caller-verified, complete single-key revision family.

    No unqualified latest-file selection, arrival-order priority, or unknown-to-old fallback.
    Equal/overlapping coarse timestamps require B2 ordering evidence; this slice refuses them.
    """
    if not isinstance(query, TemporalQuery):
        raise ValueError("selection requires a typed temporal query")
    if type(versions) is not tuple or any(
        not isinstance(item, SourceVersionClock) for item in versions
    ):
        raise ValueError("source revision family must be an immutable typed tuple")
    if not versions or len({item.source_key for item in versions}) != 1:
        raise ValueError("selection requires one nonempty source revision family")
    if len({item.revision_id for item in versions}) != len(versions):
        raise ValueError("duplicate source revision")
    if query.view == ClockView.RETROSPECTIVE:
        matches = [item for item in versions if item.revision_id == retrospective_pin]
        if len(matches) != 1:
            raise ValueError("retrospective version must be explicitly pinned")
        return matches[0]
    if retrospective_pin is not None:
        raise ValueError("retrospective pin is forbidden in historical knowledge selection")
    cutoff = query.knowledge_cutoff
    matches = []
    for item in versions:
        start, end = item.interval(query.view)
        if start is None:
            raise ValueError("revision availability is unproven")
        if start.not_before > cutoff:
            continue
        if not start.definitely_before(cutoff):
            raise ValueError("availability order at cutoff is unproven")
        if end is not None:
            if cutoff >= end.not_after:
                continue
            if cutoff >= end.not_before:
                raise ValueError("revision boundary order at cutoff is unproven")
        matches.append(item)
    if len(matches) != 1:
        raise ValueError("no unique admissible revision")
    return matches[0]


def dependency_ready_at(
    required: tuple[TimeEvidence, ...],
    *,
    declared_complete: bool,
    confirmation_ready: TimeEvidence | None = None,
) -> datetime:
    """Latest conservative dependency readiness bound, not proof of output generation/delivery."""
    if declared_complete is not True or type(required) is not tuple or not required:
        raise ValueError("nonempty complete dependency declaration required")
    marks = required + (() if confirmation_ready is None else (confirmation_ready,))
    if any(not isinstance(item, TimeEvidence) for item in marks):
        raise ValueError("all required availability evidence must be supplied")
    return max(item.not_after for item in marks)
