"""Outcome-blind identity and record contracts for the P4 event library."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quanthus.forward_validation.base24_runtime import AfterCloseDecisionClock


class EventContractError(ValueError):
    """Raised when an event-library contract is incomplete or unsafe."""


_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def canonical_json_bytes(value: object) -> bytes:
    """Return the repository canonical JSON representation."""

    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_digest(value: object) -> str:
    """Digest a canonical JSON value with an explicit algorithm prefix."""

    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def require_sha256_digest(value: str, path: str) -> str:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise EventContractError(f"{path} must be a lowercase sha256 digest")
    return value


def _require_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventContractError(f"{path} must be a non-empty string")
    return value


def _require_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise EventContractError(f"{path} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    path: str,
) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise EventContractError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


def _require_date(value: object, path: str) -> date:
    try:
        return date.fromisoformat(_require_text(value, path))
    except ValueError as exc:
        raise EventContractError(f"{path} must be an ISO date") from exc


def _require_datetime(value: object, path: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_require_text(value, path))
    except ValueError as exc:
        raise EventContractError(f"{path} must be an ISO timestamp") from exc
    return _require_aware(parsed, path)


def _require_aware(value: datetime, path: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EventContractError(f"{path} must be timezone-aware")
    return value


class SplitAssignment(str, Enum):
    DEVELOPMENT = "development"
    ARBITRATION = "arbitration"
    PERMANENT_BLIND = "permanent_blind"
    PROSPECTIVE = "prospective"
    SNAPSHOT_REPLAY = "snapshot_mechanism_replay"


@dataclass(frozen=True)
class EventIdentity:
    """The complete outcome-independent namespace for one candidate event."""

    security_id: str
    signal_date: date
    candidate_rule_id: str
    candidate_rule_version: str
    snapshot_id: str

    def __post_init__(self) -> None:
        for path, value in (
            ("security_id", self.security_id),
            ("candidate_rule_id", self.candidate_rule_id),
            ("candidate_rule_version", self.candidate_rule_version),
            ("snapshot_id", self.snapshot_id),
        ):
            _require_text(value, path)

    def to_dict(self) -> dict[str, str]:
        return {
            "security_id": self.security_id,
            "signal_date": self.signal_date.isoformat(),
            "candidate_rule_id": self.candidate_rule_id,
            "candidate_rule_version": self.candidate_rule_version,
            "snapshot_id": self.snapshot_id,
        }

    @property
    def event_id(self) -> str:
        payload = {
            "namespace": "p4-event-id-v1",
            **self.to_dict(),
        }
        return f"event:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"


@dataclass(frozen=True)
class EventRecord:
    """A label-free event record bound to exact data, policy, and split evidence."""

    identity: EventIdentity
    candidate_rule_digest: str
    snapshot_manifest_digest: str
    knowledge_cutoff: datetime
    signal_close: datetime
    eligibility_evidence_digest: str
    feature_window_manifest_digest: str
    isolation_group_id: str
    split_policy_id: str
    split_policy_version: str
    split_policy_digest: str
    split_assignment: SplitAssignment
    prospective_clock_json: str | None = None
    snapshot_replay_clock_json: str | None = None

    def __post_init__(self) -> None:
        for path, value in (
            ("candidate_rule_digest", self.candidate_rule_digest),
            ("snapshot_manifest_digest", self.snapshot_manifest_digest),
            ("eligibility_evidence_digest", self.eligibility_evidence_digest),
            ("feature_window_manifest_digest", self.feature_window_manifest_digest),
            ("split_policy_digest", self.split_policy_digest),
        ):
            require_sha256_digest(value, path)
        for path, value in (
            ("isolation_group_id", self.isolation_group_id),
            ("split_policy_id", self.split_policy_id),
            ("split_policy_version", self.split_policy_version),
        ):
            _require_text(value, path)
        _require_aware(self.knowledge_cutoff, "knowledge_cutoff")
        _require_aware(self.signal_close, "signal_close")
        if self.signal_close.date() != self.identity.signal_date:
            raise EventContractError("signal_close date must match identity.signal_date")
        if self.snapshot_replay_clock_json is not None:
            from quanthus.backtest.snapshot_clock import parse_replay_clock

            replay_clock = parse_replay_clock(self.snapshot_replay_clock_json)
            if json.loads(self.snapshot_replay_clock_json)["schema_version"] not in {
                "quanthus.p6-fixed-snapshot-clock.v1",
                "quanthus.r10-controlled-clock.v1",
            }:
                raise EventContractError("event replay clock version is not admitted")
            if (
                self.prospective_clock_json is not None
                or self.split_assignment != SplitAssignment.SNAPSHOT_REPLAY
                or replay_clock.market_date != self.identity.signal_date
                or self.signal_close != replay_clock.simulated_close
                or self.knowledge_cutoff != replay_clock.knowledge_cutoff
            ):
                raise EventContractError("snapshot mechanism clock does not bind event")
            object.__setattr__(self, "snapshot_replay_clock_json", replay_clock.to_json())
        elif self.split_assignment == SplitAssignment.SNAPSHOT_REPLAY:
            raise EventContractError("snapshot replay requires its explicit two-clock contract")
        elif self.prospective_clock_json is not None:
            clock = prospective_clock(self.prospective_clock_json)
            if (
                self.split_assignment != SplitAssignment.PROSPECTIVE
                or clock.market_date != self.identity.signal_date
                or clock.observation_end != self.signal_close
                or not clock.source_available_at <= self.knowledge_cutoff <= clock.decision_at
            ):
                raise EventContractError("prospective decision clock does not bind event")
            object.__setattr__(
                self,
                "prospective_clock_json",
                canonical_json_bytes(clock.to_dict()).decode("utf-8"),
            )
        elif self.knowledge_cutoff > self.signal_close:
            raise EventContractError("knowledge_cutoff must not be after T close")

    @property
    def decision_at(self) -> datetime:
        if self.snapshot_replay_clock_json is not None:
            from quanthus.backtest.snapshot_clock import parse_replay_clock

            return parse_replay_clock(self.snapshot_replay_clock_json).knowledge_cutoff
        if self.prospective_clock_json is None:
            return self.signal_close
        return prospective_clock(self.prospective_clock_json).decision_at

    @property
    def event_id(self) -> str:
        return self.identity.event_id

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "event_id": self.event_id,
            **self.identity.to_dict(),
            "candidate_rule_digest": self.candidate_rule_digest,
            "snapshot_manifest_digest": self.snapshot_manifest_digest,
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "signal_close": self.signal_close.isoformat(),
            "eligibility_evidence_digest": self.eligibility_evidence_digest,
            "feature_window_manifest_digest": self.feature_window_manifest_digest,
            "isolation_group_id": self.isolation_group_id,
            "split_policy_id": self.split_policy_id,
            "split_policy_version": self.split_policy_version,
            "split_policy_digest": self.split_policy_digest,
            "split_assignment": self.split_assignment.value,
        }
        if self.prospective_clock_json is not None:
            payload["prospective_clock"] = json.loads(self.prospective_clock_json)
        if self.snapshot_replay_clock_json is not None:
            payload["snapshot_replay_clock"] = json.loads(self.snapshot_replay_clock_json)
        return payload


def prospective_clock(value: str) -> AfterCloseDecisionClock:
    """Strict opt-in clock; the legacy T-close event contract stays unchanged."""
    from quanthus.forward_validation.base24_runtime import AfterCloseDecisionClock

    try:
        raw = json.loads(value)
        clock = AfterCloseDecisionClock(
            market_date=date.fromisoformat(raw["market_date"]),
            source_available_at=datetime.fromisoformat(raw["source_available_at"]),
            decision_at=datetime.fromisoformat(raw["decision_at"]),
            next_session_open=(
                None
                if raw["next_session_open"] is None
                else datetime.fromisoformat(raw["next_session_open"])
            ),
        )
        if raw != clock.to_dict():
            raise EventContractError("prospective clock version or fields differ")
        return clock
    except (ValueError, KeyError, TypeError) as exc:
        raise EventContractError("invalid prospective decision clock") from exc


def event_record_from_dict(value: object) -> EventRecord:
    """Parse one event record without defaults, unknown fields, or outcome extensions."""

    raw = _require_mapping(value, "event_record")
    _require_exact_keys(
        raw,
        {
            "event_id",
            "security_id",
            "signal_date",
            "candidate_rule_id",
            "candidate_rule_version",
            "snapshot_id",
            "candidate_rule_digest",
            "snapshot_manifest_digest",
            "knowledge_cutoff",
            "signal_close",
            "eligibility_evidence_digest",
            "feature_window_manifest_digest",
            "isolation_group_id",
            "split_policy_id",
            "split_policy_version",
            "split_policy_digest",
            "split_assignment",
        }
        | ({"prospective_clock"} if "prospective_clock" in raw else set())
        | ({"snapshot_replay_clock"} if "snapshot_replay_clock" in raw else set()),
        "event_record",
    )
    identity = EventIdentity(
        security_id=_require_text(raw["security_id"], "security_id"),
        signal_date=_require_date(raw["signal_date"], "signal_date"),
        candidate_rule_id=_require_text(raw["candidate_rule_id"], "candidate_rule_id"),
        candidate_rule_version=_require_text(
            raw["candidate_rule_version"], "candidate_rule_version"
        ),
        snapshot_id=_require_text(raw["snapshot_id"], "snapshot_id"),
    )
    declared_event_id = _require_text(raw["event_id"], "event_id")
    if declared_event_id != identity.event_id:
        raise EventContractError("event_id does not match the frozen identity namespace")
    try:
        split_assignment = SplitAssignment(
            _require_text(raw["split_assignment"], "split_assignment")
        )
    except ValueError as exc:
        raise EventContractError("split_assignment is unsupported") from exc
    return EventRecord(
        identity=identity,
        candidate_rule_digest=require_sha256_digest(
            _require_text(raw["candidate_rule_digest"], "candidate_rule_digest"),
            "candidate_rule_digest",
        ),
        snapshot_manifest_digest=require_sha256_digest(
            _require_text(raw["snapshot_manifest_digest"], "snapshot_manifest_digest"),
            "snapshot_manifest_digest",
        ),
        knowledge_cutoff=_require_datetime(raw["knowledge_cutoff"], "knowledge_cutoff"),
        signal_close=_require_datetime(raw["signal_close"], "signal_close"),
        eligibility_evidence_digest=require_sha256_digest(
            _require_text(
                raw["eligibility_evidence_digest"],
                "eligibility_evidence_digest",
            ),
            "eligibility_evidence_digest",
        ),
        feature_window_manifest_digest=require_sha256_digest(
            _require_text(
                raw["feature_window_manifest_digest"],
                "feature_window_manifest_digest",
            ),
            "feature_window_manifest_digest",
        ),
        isolation_group_id=_require_text(raw["isolation_group_id"], "isolation_group_id"),
        split_policy_id=_require_text(raw["split_policy_id"], "split_policy_id"),
        split_policy_version=_require_text(raw["split_policy_version"], "split_policy_version"),
        split_policy_digest=require_sha256_digest(
            _require_text(raw["split_policy_digest"], "split_policy_digest"),
            "split_policy_digest",
        ),
        split_assignment=split_assignment,
        prospective_clock_json=(
            None
            if "prospective_clock" not in raw
            else canonical_json_bytes(raw["prospective_clock"]).decode("utf-8")
        ),
        snapshot_replay_clock_json=(
            None
            if "snapshot_replay_clock" not in raw
            else canonical_json_bytes(raw["snapshot_replay_clock"]).decode("utf-8")
        ),
    )


def validate_event_record_set(records: Iterable[EventRecord]) -> None:
    """Reject duplicate identities and any attempt to assign one event to multiple splits."""

    seen: dict[str, EventRecord] = {}
    for record in records:
        previous = seen.get(record.event_id)
        if previous is None:
            seen[record.event_id] = record
            continue
        if previous.split_assignment != record.split_assignment:
            raise EventContractError("one event cannot have multiple split assignments")
        raise EventContractError(f"duplicate event_id: {record.event_id}")
