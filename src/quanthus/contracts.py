from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum, IntEnum
from typing import Any


class EventOutcome(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    FAILURE = "failure"
    NOT_EXECUTABLE = "not_executable"


class VetoAction(IntEnum):
    BLOCK_NEW = 1
    REDUCE_ONLY = 2
    FORCE_LIQUIDATE = 3


def strictest_veto(actions: Iterable[VetoAction]) -> VetoAction | None:
    return max(actions, default=None)


@dataclass(frozen=True)
class SnapshotRef:
    market_date: date
    knowledge_cutoff: datetime
    source_revision: str
    snapshot_id: str

    def __post_init__(self) -> None:
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise ValueError("knowledge_cutoff must be timezone-aware")
        if self.knowledge_cutoff.date() < self.market_date:
            raise ValueError("knowledge_cutoff cannot precede market_date")
        if not self.source_revision.strip():
            raise ValueError("source_revision must not be empty")
        if not self.snapshot_id.strip():
            raise ValueError("snapshot_id must not be empty")


@dataclass(frozen=True)
class CandidateSignal:
    signal_id: str
    security_code: str
    aggregate_score: float

    def __post_init__(self) -> None:
        if not self.signal_id.strip():
            raise ValueError("signal_id must not be empty")
        if not self.security_code.strip():
            raise ValueError("security_code must not be empty")
        if not math.isfinite(self.aggregate_score):
            raise ValueError("aggregate_score must be finite")


def rank_candidates(candidates: Iterable[CandidateSignal]) -> list[CandidateSignal]:
    return sorted(candidates, key=lambda item: (-item.aggregate_score, item.security_code))


@dataclass(frozen=True)
class RunManifest:
    code_version: str
    parameter_hash: str
    data_snapshot_id: str
    feature_version: str
    rule_version: str
    run_id: str
    result_summary: Mapping[str, Any]

    def __post_init__(self) -> None:
        required_values = {
            "code_version": self.code_version,
            "parameter_hash": self.parameter_hash,
            "data_snapshot_id": self.data_snapshot_id,
            "feature_version": self.feature_version,
            "rule_version": self.rule_version,
            "run_id": self.run_id,
        }
        blank = [name for name, value in required_values.items() if not value.strip()]
        if blank:
            raise ValueError(f"run manifest fields must not be empty: {', '.join(blank)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code_version": self.code_version,
            "parameter_hash": self.parameter_hash,
            "data_snapshot_id": self.data_snapshot_id,
            "feature_version": self.feature_version,
            "rule_version": self.rule_version,
            "run_id": self.run_id,
            "result_summary": dict(self.result_summary),
        }
