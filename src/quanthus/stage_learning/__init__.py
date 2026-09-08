"""Development-only stock-stage historical harness.

This package owns engineering candidates and capacity-safe historical replay for
P3-STAGE-001-C. It does not publish official LabelCard outputs or grant any
strategy/execution authority.
"""

from quanthus.stage_learning.core import (
    ActivityObservation,
    InferenceStatus,
    PredicateEvidence,
    PredicateState,
    StageCandidateObservation,
    StageDecision,
    build_activity_observations,
    build_stage34_decisions,
)
from quanthus.stage_learning.source import DevelopmentReadScope, read_development_admissions

__all__ = [
    "ActivityObservation",
    "DevelopmentReadScope",
    "InferenceStatus",
    "PredicateEvidence",
    "PredicateState",
    "StageCandidateObservation",
    "StageDecision",
    "build_activity_observations",
    "build_stage34_decisions",
    "read_development_admissions",
]
