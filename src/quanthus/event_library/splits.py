"""Versioned date-partition, purge, embargo, and access contracts for P4 events."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from quanthus.event_library.contracts import (
    SplitAssignment,
    require_sha256_digest,
    sha256_digest,
)


class SplitPolicyError(ValueError):
    """Raised when a split policy or event assignment is unsafe."""


class PartitionAccessError(SplitPolicyError):
    """Raised when a run attempts to read a forbidden validation partition."""


_SEMVER_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SplitPolicyError(f"{path} must be an object")
    return value


def _sequence(value: object, path: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise SplitPolicyError(f"{path} must be an array")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SplitPolicyError(f"{path} must be a non-empty string")
    return value


def _positive_int(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SplitPolicyError(f"{path} must be a positive integer")
    return value


def _date(value: object, path: str) -> date:
    try:
        return date.fromisoformat(_text(value, path))
    except ValueError as exc:
        raise SplitPolicyError(f"{path} must be an ISO date") from exc


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected.difference(value))
    unexpected = sorted(set(value).difference(expected))
    if missing or unexpected:
        raise SplitPolicyError(
            f"{path} keys do not match contract; missing={missing}, unexpected={unexpected}"
        )


@dataclass(frozen=True)
class PartitionWindow:
    assignment: SplitAssignment
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise SplitPolicyError(f"{self.assignment.value} partition start must not follow end")

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.assignment.value,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }


@dataclass(frozen=True)
class SplitGovernance:
    approval_state: str
    approval_authority: str
    decision_reference: str

    def __post_init__(self) -> None:
        if self.approval_state not in {"pending_human_approval", "approved", "deprecated"}:
            raise SplitPolicyError("unsupported split-policy approval state")
        if self.approval_authority != "human":
            raise SplitPolicyError("split policy requires human approval")

    def to_dict(self) -> dict[str, str]:
        return {
            "approval_state": self.approval_state,
            "approval_authority": self.approval_authority,
            "decision_reference": self.decision_reference,
        }


@dataclass(frozen=True)
class SplitPolicy:
    path: Path
    schema_version: str
    policy_id: str
    policy_version: str
    lifecycle: str
    owner: str
    accepted_profile: str
    partitions: tuple[PartitionWindow, ...]
    purge_sessions: int
    embargo_sessions: int
    maximum_overlap_sessions: int
    allowed_read_partitions: tuple[SplitAssignment, ...]
    manifest_binding: str
    governance: SplitGovernance
    semantic_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != "1.0.0":
            raise SplitPolicyError("split policy schema_version must be 1.0.0")
        if _SEMVER_PATTERN.fullmatch(self.policy_version) is None:
            raise SplitPolicyError("split policy version must be semantic versioning")
        if self.lifecycle not in {"draft", "approved", "deprecated"}:
            raise SplitPolicyError("unsupported split-policy lifecycle")
        expected_names = (
            SplitAssignment.DEVELOPMENT,
            SplitAssignment.ARBITRATION,
            SplitAssignment.PERMANENT_BLIND,
        )
        if tuple(item.assignment for item in self.partitions) != expected_names:
            raise SplitPolicyError("split partitions must be development, arbitration, blind")
        for left, right in zip(self.partitions, self.partitions[1:], strict=False):
            if left.end >= right.start:
                raise SplitPolicyError("split partitions must not overlap")
            if left.end + timedelta(days=1) != right.start:
                raise SplitPolicyError("split partitions must be calendar-contiguous")
        if self.purge_sessions < self.maximum_overlap_sessions:
            raise SplitPolicyError("purge must cover the declared maximum overlap")
        if self.embargo_sessions < self.maximum_overlap_sessions:
            raise SplitPolicyError("embargo must cover the declared maximum overlap")
        if not self.allowed_read_partitions:
            raise SplitPolicyError("allowed_read_partitions must not be empty")
        if len(self.allowed_read_partitions) != len(set(self.allowed_read_partitions)):
            raise SplitPolicyError("allowed_read_partitions must be unique")
        if self.accepted_profile != "close-to-next-open-v1":
            raise SplitPolicyError("P4 split policy must use close-to-next-open-v1")
        if self.manifest_binding != "exact_dwd_snapshot_and_split_policy_digest_required":
            raise SplitPolicyError("split assignment must be manifest-bound")
        if self.lifecycle == "approved" and self.governance.approval_state != "approved":
            raise SplitPolicyError("approved split policy requires approved governance")
        if self.lifecycle == "draft" and self.governance.approval_state != "pending_human_approval":
            raise SplitPolicyError("draft split policy requires pending human approval")
        if self.lifecycle == "deprecated" and self.governance.approval_state != "deprecated":
            raise SplitPolicyError("deprecated split policy requires deprecated governance")
        require_sha256_digest(self.semantic_digest, "semantic_digest")

    @property
    def computed_semantic_digest(self) -> str:
        return sha256_digest(self.semantic_payload())

    def require_stage_b_authority(self) -> None:
        if self.lifecycle != "approved" or self.governance.approval_state != "approved":
            raise SplitPolicyError(
                "Stage B is blocked until the split-policy digest receives human approval"
            )

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "accepted_profile": self.accepted_profile,
            "partitions": [item.to_dict() for item in self.partitions],
            "purge_sessions": self.purge_sessions,
            "embargo_sessions": self.embargo_sessions,
            "maximum_overlap_sessions": self.maximum_overlap_sessions,
            "allowed_read_partitions": [item.value for item in self.allowed_read_partitions],
            "manifest_binding": self.manifest_binding,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.semantic_payload(),
            "lifecycle": self.lifecycle,
            "owner": self.owner,
            "governance": self.governance.to_dict(),
            "semantic_digest": self.semantic_digest,
        }

    def partition_for(self, value: date) -> PartitionWindow:
        for partition in self.partitions:
            if partition.contains(value):
                return partition
        raise SplitPolicyError(f"date {value.isoformat()} is outside the governed partitions")


def assign_event_split(
    policy: SplitPolicy,
    *,
    signal_date: date,
    feature_start: date,
    outcome_end: date,
    trading_dates: Sequence[date],
) -> SplitAssignment:
    """Assign one whole event or reject boundary leakage and forbidden access."""

    if feature_start > signal_date or signal_date > outcome_end:
        raise SplitPolicyError("event dates must satisfy feature_start <= T <= outcome_end")
    ordered = tuple(trading_dates)
    if not ordered or tuple(sorted(set(ordered))) != ordered:
        raise SplitPolicyError("trading_dates must be sorted and unique")
    date_to_index = {value: index for index, value in enumerate(ordered)}
    for name, value in (
        ("feature_start", feature_start),
        ("signal_date", signal_date),
        ("outcome_end", outcome_end),
    ):
        if value not in date_to_index:
            raise SplitPolicyError(f"{name} must be an open trading date")

    partition = policy.partition_for(signal_date)
    if partition.assignment not in policy.allowed_read_partitions:
        raise PartitionAccessError(
            f"partition {partition.assignment.value} is not authorized for this policy"
        )
    if not partition.contains(feature_start) or not partition.contains(outcome_end):
        raise SplitPolicyError("all slices of one event must remain in one split")

    partition_dates = tuple(value for value in ordered if partition.contains(value))
    if len(partition_dates) <= policy.embargo_sessions + policy.purge_sessions:
        raise SplitPolicyError("partition has insufficient sessions for purge and embargo")
    first_allowed = partition_dates[policy.embargo_sessions]
    last_allowed = partition_dates[-policy.purge_sessions - 1]
    if feature_start < first_allowed:
        raise SplitPolicyError("event enters the split-boundary embargo interval")
    if outcome_end > last_allowed:
        raise SplitPolicyError("event enters the split-boundary purge interval")
    return partition.assignment


def _parse_split_policy(path: Path) -> SplitPolicy:
    try:
        raw_value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SplitPolicyError(f"cannot read split policy: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SplitPolicyError(f"invalid split-policy JSON: {exc}") from exc
    raw = _mapping(raw_value, "split_policy")
    _exact_keys(
        raw,
        {
            "schema_version",
            "policy_id",
            "policy_version",
            "lifecycle",
            "owner",
            "accepted_profile",
            "partitions",
            "purge_sessions",
            "embargo_sessions",
            "maximum_overlap_sessions",
            "allowed_read_partitions",
            "manifest_binding",
            "governance",
            "semantic_digest",
        },
        "split_policy",
    )

    partitions: list[PartitionWindow] = []
    for index, partition_value in enumerate(_sequence(raw["partitions"], "partitions")):
        partition = _mapping(partition_value, f"partitions[{index}]")
        _exact_keys(partition, {"name", "start", "end"}, f"partitions[{index}]")
        try:
            assignment = SplitAssignment(
                _text(partition["name"], f"partitions[{index}].name")
            )
        except ValueError as exc:
            raise SplitPolicyError(f"partitions[{index}].name is unsupported") from exc
        partitions.append(
            PartitionWindow(
                assignment=assignment,
                start=_date(partition["start"], f"partitions[{index}].start"),
                end=_date(partition["end"], f"partitions[{index}].end"),
            )
        )

    allowed: list[SplitAssignment] = []
    for index, value in enumerate(
        _sequence(raw["allowed_read_partitions"], "allowed_read_partitions")
    ):
        try:
            allowed.append(SplitAssignment(_text(value, f"allowed_read_partitions[{index}]")))
        except ValueError as exc:
            raise SplitPolicyError(
                f"allowed_read_partitions[{index}] is unsupported"
            ) from exc

    governance = _mapping(raw["governance"], "governance")
    _exact_keys(
        governance,
        {"approval_state", "approval_authority", "decision_reference"},
        "governance",
    )
    return SplitPolicy(
        path=path.resolve(),
        schema_version=_text(raw["schema_version"], "schema_version"),
        policy_id=_text(raw["policy_id"], "policy_id"),
        policy_version=_text(raw["policy_version"], "policy_version"),
        lifecycle=_text(raw["lifecycle"], "lifecycle"),
        owner=_text(raw["owner"], "owner"),
        accepted_profile=_text(raw["accepted_profile"], "accepted_profile"),
        partitions=tuple(partitions),
        purge_sessions=_positive_int(raw["purge_sessions"], "purge_sessions"),
        embargo_sessions=_positive_int(raw["embargo_sessions"], "embargo_sessions"),
        maximum_overlap_sessions=_positive_int(
            raw["maximum_overlap_sessions"], "maximum_overlap_sessions"
        ),
        allowed_read_partitions=tuple(allowed),
        manifest_binding=_text(raw["manifest_binding"], "manifest_binding"),
        governance=SplitGovernance(
            approval_state=_text(
                governance["approval_state"], "governance.approval_state"
            ),
            approval_authority=_text(
                governance["approval_authority"], "governance.approval_authority"
            ),
            decision_reference=_text(
                governance["decision_reference"], "governance.decision_reference"
            ),
        ),
        semantic_digest=require_sha256_digest(
            _text(raw["semantic_digest"], "semantic_digest"), "semantic_digest"
        ),
    )


def calculate_split_policy_digest(path: Path) -> str:
    """Calculate the split-policy semantic digest without approving it."""

    return _parse_split_policy(path).computed_semantic_digest


def load_split_policy(path: Path) -> SplitPolicy:
    """Load a strict versioned split policy and verify its semantic digest."""

    policy = _parse_split_policy(path)
    if policy.semantic_digest != policy.computed_semantic_digest:
        raise SplitPolicyError("split-policy semantic_digest does not match content")
    return policy
