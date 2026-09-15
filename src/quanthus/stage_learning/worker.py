"""Node-local governed worker for RX6800 stage-learning jobs.

The worker owns no production database credentials.  It accepts a tiny file-backed
control contract intended to be invoked through a dedicated authenticated SSH/MCP
adapter.  Each job executes an exact commit in an isolated Git worktree while the
control checkout remains unchanged.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOB_SCHEMA = "quanthus.stage-worker-job.v1"
STATUS_SCHEMA = "quanthus.stage-worker-status.v1"
STAGE_HARNESS_JOB = "quanthus.stage-harness.v1"
STAGE_REFERENCE_PILOT_JOB = "quanthus.stage-reference-pilot.v1"
STAGE_QUALIFICATION_JOB = "quanthus.stage-qualification.v1"
STAGE_CAPACITY_CENSUS_JOB = "quanthus.stage-capacity-census.v1"
STAGE_SECOND_GROUP_PROFILE_JOB = "quanthus.stage-second-group-profile.v1"
STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB = "quanthus.stage-second-group-repair-profile.v1"
STAGE_SECOND_GROUP_DEVELOPMENT_JOB = "quanthus.stage-second-group-development.v1"
STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB = (
    "quanthus.stage-second-group-development-supplement.v1"
)
STAGE_THIRD_GROUP_PROFILE_JOB = "quanthus.stage-third-group-profile.v1"
STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB = "quanthus.stage-order-proposition-development.v1"
STAGE_ORDER_RULE_PREPARE_JOB = "quanthus.stage-order-rule-prepare.v1"
STAGE_ORDER_RULE_RESOLVE_SCORE_JOB = "quanthus.stage-order-rule-resolve-score.v1"
STAGE_DIRECTIONAL_BOUNDARY_D1_JOB = "quanthus.stage-directional-boundary-d1-prepare.v1"
STAGE_DIRECTIONAL_BOUNDARY_D3_JOB = "quanthus.stage-directional-boundary-d3-prepare.v1"
STAGE_POSITIVE_INSTABILITY_D1_JOB = "quanthus.stage-positive-instability-d1-prepare.v1"
STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB = (
    "quanthus.stage-positive-instability-quantitative-d1-prepare.v1"
)
STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB = (
    "quanthus.stage-orderly-consolidation-joint-d1-prepare.v1"
)
STAGE_ATOMIC_PROPOSITION_G1_JOB = "quanthus.stage-atomic-proposition-g1-prepare.v1"
STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB = (
    "quanthus.stage-development-resource-capacity-census.v1"
)
STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB = "quanthus.stage-development-resource-plan.v1"
STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB = (
    "quanthus.stage-development-resource-admission-screen.v1"
)
STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB = (
    "quanthus.stage-development-resource-qualified-plan.v1"
)
STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB = (
    "quanthus.stage-atomic-proposition-successor-d1-prepare.v1"
)
STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB = (
    "quanthus.stage-atomic-proposition-admission-qualified-d1-prepare.v1"
)
STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB = (
    "quanthus.stage-complete-reserve-review-readiness.v1"
)
STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB = (
    "quanthus.stage-complete-reserve-additional-review-set.v1"
)
STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB = (
    "quanthus.stage-complete-reserve-additional-review-prepare.v1"
)
STAGE_COMBINED_ATOMIC_SCORING_JOB = "quanthus.stage-combined-atomic-scoring.v1"
STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB = (
    "quanthus.stage-stratum-normalized-instability.v1"
)
STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB = (
    "quanthus.stage-instability-channel-decomposition.v1"
)
STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB = (
    "quanthus.stage-downside-lifecycle-resource-plan.v1"
)
STAGE_QUALIFICATION_ID = "stock-stage-first-group-3-4-arbitration-shadow-v1"
QUALIFICATION_BUDGET_SCHEMA = "quanthus.stage-qualification-budget.v1"
ORDER_RULE_PREPARE_BUDGET_SCHEMA = "quanthus.stage-order-rule-prepare-budget.v1"
ORDER_RULE_PREPARE_RETRY_BUDGET_SCHEMA = "quanthus.stage-order-rule-prepare-retry-budget.v1"
ORDER_RULE_RESOLVE_SCORE_BUDGET_SCHEMA = "quanthus.stage-order-rule-resolve-score-budget.v1"
DIRECTIONAL_BOUNDARY_D1_BUDGET_SCHEMA = "quanthus.stage-directional-boundary-d1-budget.v1"
DIRECTIONAL_BOUNDARY_D3_BUDGET_SCHEMA = "quanthus.stage-directional-boundary-d3-budget.v1"
POSITIVE_INSTABILITY_D1_BUDGET_SCHEMA = "quanthus.stage-positive-instability-d1-budget.v1"
POSITIVE_INSTABILITY_QUANTITATIVE_D1_BUDGET_SCHEMA = (
    "quanthus.stage-positive-instability-quantitative-d1-budget.v1"
)
POSITIVE_INSTABILITY_QUANTITATIVE_D1_RETRY_BUDGET_SCHEMA = (
    "quanthus.stage-positive-instability-quantitative-d1-retry-budget.v1"
)
POSITIVE_INSTABILITY_QUANTITATIVE_D1_RECOVERY_BUDGET_SCHEMA = (
    "quanthus.stage-positive-instability-quantitative-d1-transport-recovery-budget.v1"
)
ORDERLY_CONSOLIDATION_JOINT_D1_BUDGET_SCHEMA = (
    "quanthus.stage-orderly-consolidation-joint-d1-budget.v1"
)
ORDERLY_CONSOLIDATION_JOINT_D1_RECOVERY_BUDGET_SCHEMA = (
    "quanthus.stage-orderly-consolidation-joint-d1-transport-recovery-budget.v1"
)
ATOMIC_PROPOSITION_G1_BUDGET_SCHEMA = "quanthus.stage-atomic-proposition-g1-budget.v1"
DEVELOPMENT_RESOURCE_CENSUS_BUDGET_SCHEMA = (
    "quanthus.stage-development-resource-capacity-census-budget.v1"
)
DEVELOPMENT_RESOURCE_PLAN_BUDGET_SCHEMA = "quanthus.stage-development-resource-plan-budget.v1"
DEVELOPMENT_RESOURCE_PLAN_RECOVERY_BUDGET_SCHEMA = (
    "quanthus.stage-development-resource-plan-recovery-budget.v1"
)
ATOMIC_PROPOSITION_SUCCESSOR_D1_BUDGET_SCHEMA = (
    "quanthus.stage-atomic-proposition-successor-d1-budget.v1"
)
ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_BUDGET_SCHEMA = (
    "quanthus.stage-atomic-proposition-admission-qualified-d1-budget.v1"
)
DEVELOPMENT_RESOURCE_ADMISSION_BUDGET_SCHEMA = (
    "quanthus.stage-development-resource-admission-screen-budget.v1"
)
DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_BUDGET_SCHEMA = (
    "quanthus.stage-development-resource-qualified-plan-budget.v1"
)
COMPLETE_RESERVE_REVIEW_READINESS_BUDGET_SCHEMA = (
    "quanthus.stage-complete-reserve-review-readiness-budget.v1"
)
COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_BUDGET_SCHEMA = (
    "quanthus.stage-complete-reserve-additional-review-set-budget.v1"
)
COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_RECOVERY_BUDGET_SCHEMA = (
    "quanthus.stage-complete-reserve-additional-review-set-recovery-budget.v1"
)
COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_BUDGET_SCHEMA = (
    "quanthus.stage-complete-reserve-additional-review-prepare-budget.v1"
)
COMBINED_ATOMIC_SCORING_BUDGET_SCHEMA = "quanthus.stage-combined-atomic-scoring-budget.v1"
STRATUM_NORMALIZED_INSTABILITY_BUDGET_SCHEMA = (
    "quanthus.stage-stratum-normalized-instability-budget.v1"
)
STRATUM_NORMALIZED_INSTABILITY_RECOVERY_BUDGET_SCHEMA = (
    "quanthus.stage-stratum-normalized-instability-recovery-budget.v1"
)
INSTABILITY_CHANNEL_DECOMPOSITION_BUDGET_SCHEMA = (
    "quanthus.stage-instability-channel-decomposition-budget.v1"
)
DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_BUDGET_SCHEMA = (
    "quanthus.stage-downside-lifecycle-resource-plan-budget.v1"
)
STRATUM_NORMALIZED_INSTABILITY_RECOVERY_AUTH = (
    "config/stage-learning/stage-stratum-normalized-instability-recovery.v1.json"
)
DEVELOPMENT_RESOURCE_PLAN_RECOVERY_AUTH = (
    "config/stage-learning/stage-development-resource-plan-recovery.v1.json"
)
COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_RECOVERY_AUTH = (
    "config/stage-learning/stage-complete-reserve-additional-review-set-recovery.v1.json"
)
ORDER_RULE_PREPARE_RETRY_AUTH = "config/stage-learning/order-rule-prepare-retry.v1.json"
POSITIVE_INSTABILITY_QUANTITATIVE_D1_RETRY_AUTH = (
    "config/stage-learning/positive-instability-quantitative-d1-retry.v1.json"
)
POSITIVE_INSTABILITY_QUANTITATIVE_D1_RECOVERY_AUTH = (
    "config/stage-learning/positive-instability-quantitative-d1-transport-recovery.v1.json"
)
ORDERLY_CONSOLIDATION_JOINT_D1_RECOVERY_AUTH = (
    "config/stage-learning/orderly-consolidation-joint-d1-transport-recovery.v1.json"
)
JOB_KIND = STAGE_HARNESS_JOB  # compatibility alias for the first admitted kind
GITHUB_REPOSITORY = "git@github.com:rv198-star/Quanthus.git"
MAIN_REF = "refs/remotes/stage-control/main"
_JOB_ID = re.compile(r"^stage_[0-9a-f]{32}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_JOB_SPECS: dict[str, dict[str, Any]] = {
    STAGE_HARNESS_JOB: {
        "config": "config/stage-learning/harness.v1.json",
        "module": "quanthus.stage_learning.harness",
        "artifact_glob": "stage-harness-*",
        "process_error": "stage_harness_process_failed",
    },
    STAGE_REFERENCE_PILOT_JOB: {
        "config": "config/stage-learning/reference-pilot.v1.json",
        "module": "quanthus.stage_learning.reference_pilot",
        "artifact_glob": "stage-reference-pilot-*",
        "process_error": "stage_reference_pilot_process_failed",
    },
    STAGE_QUALIFICATION_JOB: {
        "config": "config/stage-learning/qualification.v1.json",
        "module": "quanthus.stage_learning.qualification",
        "artifact_glob": "stage-qualification-*",
        "process_error": "stage_qualification_process_failed",
    },
    STAGE_CAPACITY_CENSUS_JOB: {
        "config": "config/stage-learning/sample-capacity-census.v1.json",
        "module": "quanthus.stage_learning.capacity_census",
        "artifact_glob": "stage-capacity-census-*",
        "process_error": "stage_capacity_census_process_failed",
        "wall_seconds": 1800,
    },
    STAGE_SECOND_GROUP_PROFILE_JOB: {
        "config": "config/stage-learning/second-group-profile.v1.json",
        "module": "quanthus.stage_learning.second_group_profile",
        "artifact_glob": "stage-second-group-profile-*",
        "process_error": "stage_second_group_profile_process_failed",
        "wall_seconds": 1800,
    },
    STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB: {
        "config": "config/stage-learning/second-group-repair-profile.v1.json",
        "module": "quanthus.stage_learning.second_group_repair_profile",
        "artifact_glob": "stage-second-group-repair-profile-*",
        "process_error": "stage_second_group_repair_profile_process_failed",
        "wall_seconds": 1800,
    },
    STAGE_SECOND_GROUP_DEVELOPMENT_JOB: {
        "config": "config/stage-learning/second-group-development.v1.json",
        "module": "quanthus.stage_learning.second_group_development",
        "artifact_glob": "stage-second-group-development-*",
        "process_error": "stage_second_group_development_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB: {
        "config": "config/stage-learning/second-group-development-supplement.v1.json",
        "module": "quanthus.stage_learning.second_group_development_supplement",
        "artifact_glob": "stage-second-group-development-supplement-*",
        "process_error": "stage_second_group_development_supplement_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_THIRD_GROUP_PROFILE_JOB: {
        "config": "config/stage-learning/third-group-profile.v1.json",
        "module": "quanthus.stage_learning.third_group_profile",
        "artifact_glob": "stage-third-group-profile-*",
        "process_error": "stage_third_group_profile_process_failed",
        "wall_seconds": 1800,
    },
    STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB: {
        "config": "config/stage-learning/order-proposition-development.v1.json",
        "module": "quanthus.stage_learning.order_proposition_development",
        "artifact_glob": "stage-order-proposition-development-*",
        "process_error": "stage_order_proposition_development_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_ORDER_RULE_PREPARE_JOB: {
        "config": "config/stage-learning/order-rule-prepare.v1.json",
        "module": "quanthus.stage_learning.order_rule_prepare",
        "artifact_glob": "stage-order-rule-prepare-*",
        "process_error": "stage_order_rule_prepare_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_ORDER_RULE_RESOLVE_SCORE_JOB: {
        "config": "config/stage-learning/order-rule-resolve-score.v1.json",
        "module": "quanthus.stage_learning.order_rule_resolve_score",
        "artifact_glob": "stage-order-rule-resolve-score-*",
        "process_error": "stage_order_rule_resolve_score_process_failed",
        "wall_seconds": 600,
    },
    STAGE_DIRECTIONAL_BOUNDARY_D1_JOB: {
        "config": "config/stage-learning/directional-boundary-development-screen.v1.json",
        "module": "quanthus.stage_learning.directional_boundary_calibration_prepare",
        "artifact_glob": "stage-directional-boundary-d1-*",
        "process_error": "stage_directional_boundary_d1_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_DIRECTIONAL_BOUNDARY_D3_JOB: {
        "config": "config/stage-learning/directional-boundary-evaluation-prepare.v1.json",
        "module": "quanthus.stage_learning.directional_boundary_evaluation_prepare",
        "artifact_glob": "stage-directional-boundary-d3-*",
        "process_error": "stage_directional_boundary_d3_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_POSITIVE_INSTABILITY_D1_JOB: {
        "config": "config/stage-learning/positive-instability-development-screen.v1.json",
        "module": "quanthus.stage_learning.positive_instability_development_prepare",
        "artifact_glob": "stage-positive-instability-d1-*",
        "process_error": "stage_positive_instability_d1_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB: {
        "config": (
            "config/stage-learning/positive-instability-quantitative-development-screen.v1.json"
        ),
        "module": "quanthus.stage_learning.positive_instability_quantitative_development_prepare",
        "artifact_glob": "stage-positive-instability-quantitative-d1-*",
        "process_error": "stage_positive_instability_quantitative_d1_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB: {
        "config": "config/stage-learning/orderly-consolidation-joint-development-screen.v1.json",
        "module": "quanthus.stage_learning.orderly_consolidation_joint_development_prepare",
        "artifact_glob": "stage-orderly-consolidation-joint-d1-*",
        "process_error": "stage_orderly_consolidation_joint_d1_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_ATOMIC_PROPOSITION_G1_JOB: {
        "config": "config/stage-learning/stage-atomic-proposition-development-screen.v1.json",
        "module": "quanthus.stage_learning.atomic_proposition_development_prepare",
        "artifact_glob": "stage-atomic-proposition-g1-*",
        "process_error": "stage_atomic_proposition_g1_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB: {
        "config": "config/stage-learning/stage-development-resource-capacity-census.v1.json",
        "module": "quanthus.stage_learning.development_resource_capacity_census",
        "artifact_glob": "stage-development-resource-census-*",
        "process_error": "stage_development_resource_capacity_census_process_failed",
        "wall_seconds": 900,
    },
    STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB: {
        "config": "config/stage-learning/stage-development-resource-plan.v1.json",
        "module": "quanthus.stage_learning.development_resource_plan",
        "artifact_glob": "stage-development-resource-plan-*",
        "process_error": "stage_development_resource_plan_process_failed",
        "wall_seconds": 600,
    },
    STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB: {
        "config": (
            "config/stage-learning/stage-atomic-proposition-successor-development-screen.v1.json"
        ),
        "module": "quanthus.stage_learning.atomic_proposition_successor_development_prepare",
        "artifact_glob": "stage-atomic-proposition-successor-d1-*",
        "process_error": "stage_atomic_proposition_successor_d1_process_failed",
        "wall_seconds": 3600,
    },
    STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB: {
        "config": (
            "config/stage-learning/"
            "stage-atomic-proposition-admission-qualified-development-screen.v1.json"
        ),
        "module": "quanthus.stage_learning.atomic_proposition_admission_qualified_prepare",
        "artifact_glob": "stage-atomic-proposition-admission-qualified-d1-*",
        "process_error": "stage_atomic_proposition_admission_qualified_d1_process_failed",
        "wall_seconds": 3600,
    },
    STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB: {
        "config": "config/stage-learning/stage-development-resource-admission-screen.v1.json",
        "module": "quanthus.stage_learning.development_resource_admission_screen",
        "artifact_glob": "stage-development-resource-admission-*",
        "process_error": "stage_development_resource_admission_screen_process_failed",
        "wall_seconds": 2700,
    },
    STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB: {
        "config": "config/stage-learning/stage-development-resource-qualified-plan.v1.json",
        "module": "quanthus.stage_learning.development_resource_qualified_plan",
        "artifact_glob": "stage-development-resource-qualified-plan-*",
        "process_error": "stage_development_resource_qualified_plan_process_failed",
        "wall_seconds": 600,
    },
    STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB: {
        "config": "config/stage-learning/stage-complete-reserve-review-readiness.v1.json",
        "module": "quanthus.stage_learning.complete_reserve_review_readiness",
        "artifact_glob": "stage-complete-reserve-review-readiness-*",
        "process_error": "stage_complete_reserve_review_readiness_process_failed",
        "wall_seconds": 3600,
    },
    STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB: {
        "config": "config/stage-learning/stage-complete-reserve-additional-review-set.v1.json",
        "module": "quanthus.stage_learning.complete_reserve_additional_review_set",
        "artifact_glob": "stage-complete-reserve-additional-review-set-*",
        "process_error": "stage_complete_reserve_additional_review_set_process_failed",
        "wall_seconds": 600,
    },
    STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB: {
        "config": "config/stage-learning/stage-complete-reserve-additional-review-prepare.v1.json",
        "module": "quanthus.stage_learning.complete_reserve_additional_review_prepare",
        "artifact_glob": "stage-complete-reserve-additional-review-prepare-*",
        "process_error": "stage_complete_reserve_additional_review_prepare_process_failed",
        "wall_seconds": 3600,
    },
    STAGE_COMBINED_ATOMIC_SCORING_JOB: {
        "config": "config/stage-learning/stage-combined-atomic-scoring.v1.json",
        "module": "quanthus.stage_learning.combined_atomic_scoring",
        "artifact_glob": "stage-combined-atomic-scoring-*",
        "process_error": "stage_combined_atomic_scoring_process_failed",
        "wall_seconds": 900,
    },
    STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB: {
        "config": "config/stage-learning/stage-stratum-normalized-instability.v1.json",
        "module": "quanthus.stage_learning.stratum_normalized_instability",
        "artifact_glob": "stage-stratum-normalized-instability-*",
        "process_error": "stage_stratum_normalized_instability_process_failed",
        "wall_seconds": 900,
    },
    STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB: {
        "config": "config/stage-learning/stage-instability-channel-decomposition.v1.json",
        "module": "quanthus.stage_learning.instability_channel_decomposition",
        "artifact_glob": "stage-instability-channel-decomposition-*",
        "process_error": "stage_instability_channel_decomposition_process_failed",
        "wall_seconds": 900,
    },
    STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB: {
        "config": "config/stage-learning/stage-downside-lifecycle-resource-plan.v1.json",
        "module": "quanthus.stage_learning.downside_lifecycle_resource_plan",
        "artifact_glob": "stage-downside-lifecycle-resource-plan-*",
        "process_error": "stage_downside_lifecycle_resource_plan_process_failed",
        "wall_seconds": 3600,
    },
}


class StageWorkerError(RuntimeError):
    """Raised when the node-local stage worker contract cannot be satisfied."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageWorkerError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise StageWorkerError(f"{label} must be an object")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise StageWorkerError(
            "qualification access budget is already reserved or consumed"
        ) from exc


@dataclass(frozen=True)
class StageJob:
    job_id: str
    kind: str
    code_revision: str
    config: str
    submitted_at: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StageJob:
        expected = {"schema_version", "job_id", "kind", "code_revision", "config", "submitted_at"}
        if set(value) != expected or value.get("schema_version") != JOB_SCHEMA:
            raise StageWorkerError("stage job fields or schema differ")
        job = cls(
            job_id=str(value["job_id"]),
            kind=str(value["kind"]),
            code_revision=str(value["code_revision"]),
            config=str(value["config"]),
            submitted_at=str(value["submitted_at"]),
        )
        if not _JOB_ID.fullmatch(job.job_id):
            raise StageWorkerError("stage job id is invalid")
        spec = _JOB_SPECS.get(job.kind)
        if spec is None:
            raise StageWorkerError("stage job kind is not admitted")
        if not _REVISION.fullmatch(job.code_revision):
            raise StageWorkerError("stage job requires an exact Git revision")
        if job.config != spec["config"]:
            raise StageWorkerError("stage job config is not admitted")
        try:
            parsed = datetime.fromisoformat(job.submitted_at)
        except ValueError as exc:
            raise StageWorkerError("stage job submitted_at is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise StageWorkerError("stage job submitted_at must be timezone-aware")
        return job

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": JOB_SCHEMA,
            "job_id": self.job_id,
            "kind": self.kind,
            "code_revision": self.code_revision,
            "config": self.config,
            "submitted_at": self.submitted_at,
        }

    @property
    def input_digest(self) -> str:
        return _digest(self.to_dict())


@dataclass(frozen=True)
class StageWorkerPaths:
    training_root: Path
    control_repo: Path
    git_key: Path

    @property
    def control_root(self) -> Path:
        return self.training_root / "control" / "stage-worker"

    @property
    def queue(self) -> Path:
        return self.control_root / "queue"

    @property
    def claimed(self) -> Path:
        return self.control_root / "claimed"

    @property
    def status(self) -> Path:
        return self.control_root / "status"

    @property
    def cancel(self) -> Path:
        return self.control_root / "cancel"

    @property
    def worktrees(self) -> Path:
        return self.control_root / "worktrees"

    @property
    def runtime_identity(self) -> Path:
        return self.control_root / "worker-runtime.json"

    @property
    def qualification_budget(self) -> Path:
        return self.control_root / "qualification-budget.json"

    @property
    def order_rule_prepare_budget(self) -> Path:
        return self.control_root / "order-rule-prepare-budget.json"

    @property
    def order_rule_prepare_retry_budget(self) -> Path:
        return self.control_root / "order-rule-prepare-retry-budget.json"

    @property
    def order_rule_resolve_score_budget(self) -> Path:
        return self.control_root / "order-rule-resolve-score-budget.json"

    @property
    def directional_boundary_d1_budget(self) -> Path:
        return self.control_root / "directional-boundary-d1-budget.json"

    @property
    def directional_boundary_d3_budget(self) -> Path:
        return self.control_root / "directional-boundary-d3-budget.json"

    @property
    def positive_instability_d1_budget(self) -> Path:
        return self.control_root / "positive-instability-d1-budget.json"

    @property
    def positive_instability_quantitative_d1_budget(self) -> Path:
        return self.control_root / "positive-instability-quantitative-d1-budget.json"

    @property
    def positive_instability_quantitative_d1_retry_budget(self) -> Path:
        return self.control_root / "positive-instability-quantitative-d1-retry-budget.json"

    @property
    def positive_instability_quantitative_d1_recovery_budget(self) -> Path:
        return (
            self.control_root
            / "positive-instability-quantitative-d1-transport-recovery-budget.json"
        )

    @property
    def orderly_consolidation_joint_d1_budget(self) -> Path:
        return self.control_root / "orderly-consolidation-joint-d1-budget.json"

    @property
    def orderly_consolidation_joint_d1_recovery_budget(self) -> Path:
        return self.control_root / "orderly-consolidation-joint-d1-transport-recovery-budget.json"

    @property
    def atomic_proposition_g1_budget(self) -> Path:
        return self.control_root / "atomic-proposition-g1-budget.json"

    @property
    def development_resource_census_budget(self) -> Path:
        return self.control_root / "development-resource-capacity-census-budget.json"

    @property
    def development_resource_plan_budget(self) -> Path:
        return self.control_root / "development-resource-plan-budget.json"

    @property
    def development_resource_plan_recovery_budget(self) -> Path:
        return self.control_root / "development-resource-plan-recovery-budget.json"

    @property
    def atomic_proposition_successor_d1_budget(self) -> Path:
        return self.control_root / "atomic-proposition-successor-d1-budget.json"

    @property
    def atomic_proposition_admission_qualified_d1_budget(self) -> Path:
        return self.control_root / "atomic-proposition-admission-qualified-d1-budget.json"

    def atomic_proposition_admission_qualified_review_ledger(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(job_id):
            raise StageWorkerError("P014 review ledger requires a valid parent job id")
        return self.control_root / "atomic-review-ledgers" / f"{job_id}.json"

    @property
    def development_resource_admission_budget(self) -> Path:
        return self.control_root / "development-resource-admission-screen-budget.json"

    @property
    def development_resource_qualified_plan_budget(self) -> Path:
        return self.control_root / "development-resource-qualified-plan-budget.json"

    @property
    def complete_reserve_review_readiness_budget(self) -> Path:
        return self.control_root / "complete-reserve-review-readiness-budget.json"

    @property
    def complete_reserve_additional_review_set_budget(self) -> Path:
        return self.control_root / "complete-reserve-additional-review-set-budget.json"

    @property
    def complete_reserve_additional_review_set_recovery_budget(self) -> Path:
        return self.control_root / "complete-reserve-additional-review-set-recovery-budget.json"

    @property
    def complete_reserve_additional_review_prepare_budget(self) -> Path:
        return self.control_root / "complete-reserve-additional-review-prepare-budget.json"

    @property
    def combined_atomic_scoring_budget(self) -> Path:
        return self.control_root / "combined-atomic-scoring-budget.json"

    @property
    def stratum_normalized_instability_budget(self) -> Path:
        return self.control_root / "stratum-normalized-instability-budget.json"

    @property
    def stratum_normalized_instability_recovery_budget(self) -> Path:
        return self.control_root / "stratum-normalized-instability-recovery-budget.json"

    @property
    def instability_channel_decomposition_budget(self) -> Path:
        return self.control_root / "instability-channel-decomposition-budget.json"

    @property
    def downside_lifecycle_resource_plan_budget(self) -> Path:
        return self.control_root / "downside-lifecycle-resource-plan-budget.json"

    def complete_reserve_additional_review_ledger(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(job_id):
            raise StageWorkerError("P015 review ledger requires a valid parent job id")
        return self.control_root / "atomic-review-ledgers" / f"p015-{job_id}.json"

    @property
    def logs(self) -> Path:
        return self.training_root / "logs" / "stage-worker"

    @property
    def runs(self) -> Path:
        return self.training_root / "runs" / "stage-worker"

    @property
    def python(self) -> Path:
        return self.control_repo / ".venv-stage-training" / "bin" / "python"

    def ensure(self) -> None:
        for path in (
            self.queue,
            self.claimed,
            self.status,
            self.cancel,
            self.worktrees,
            self.logs,
            self.runs,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _order_rule_prepare_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": ORDER_RULE_PREPARE_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-007-B",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _order_rule_prepare_retry_budget_value(
    failed_job: StageJob, retry_job: StageJob,
) -> dict[str, Any]:
    return {
        "schema_version": ORDER_RULE_PREPARE_RETRY_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-007-B",
        "technical_retry_of": failed_job.job_id,
        "failed_input_digest": failed_job.input_digest,
        "job": retry_job.to_dict(),
        "input_digest": retry_job.input_digest,
    }


def _file_sha256(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise StageWorkerError("order-rule prepare retry binding unreadable") from exc
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _order_rule_prepare_retry_authorization(
    paths: StageWorkerPaths, failed_job: StageJob,
) -> dict[str, Any]:
    authorization = _object(
        paths.control_repo / ORDER_RULE_PREPARE_RETRY_AUTH,
        "order-rule prepare retry authorization",
    )
    if (
        authorization.get("schema_version") != "quanthus.stage-order-rule-prepare-retry.v1"
        or authorization.get("work_item") != "P3-STAGE-007-B"
    ):
        raise StageWorkerError("order-rule prepare retry authorization differs")
    failure = authorization.get("failure")
    policy = authorization.get("authorization")
    bindings = authorization.get("bindings")
    if not all(isinstance(value, Mapping) for value in (failure, policy, bindings)):
        raise StageWorkerError("order-rule prepare retry authorization incomplete")
    if (
        failure.get("job_id") != failed_job.job_id
        or failure.get("code_revision") != failed_job.code_revision
        or failure.get("input_digest") != failed_job.input_digest
        or failure.get("terminal_state") != "failed"
        or failure.get("worker_error") != "stage_order_rule_prepare_process_failed"
        or failure.get("root_cause") != "post_selection_sampling_coordinate_canonicalization"
        or failure.get("failed_check") != "selected_observation_differs_from_retained_evidence"
    ):
        raise StageWorkerError("order-rule prepare retry failure binding differs")
    if (
        policy.get("maximum_technical_retries") != 1
        or policy.get("counts_against_result_bearing_job_budget") is not False
    ):
        raise StageWorkerError("order-rule prepare retry budget differs")
    for key in (
        "same_prepare_config_required", "same_rule_digest_required",
        "same_validation_digest_required", "same_source_scope_required",
        "same_sampling_algorithm_required", "same_sampling_seed_required",
    ):
        if policy.get(key) is not True:
            raise StageWorkerError("order-rule prepare retry invariant missing")
    for key in (
        "opinion_generation", "candidate_scoring", "parameter_search", "future_outcome_access",
        "arbitration_access", "permanent_blind_access", "official_labels",
        "daily_or_strategy", "trading",
    ):
        if policy.get(key) is not False:
            raise StageWorkerError("order-rule prepare retry authority expanded")
    prepare_path = paths.control_repo / "config/stage-learning/order-rule-prepare.v1.json"
    prepare = _object(prepare_path, "order-rule prepare config")
    if _digest(prepare) != bindings.get("prepare_config_digest"):
        raise StageWorkerError("order-rule prepare retry config drift")
    prepare_bindings = prepare.get("bindings")
    if not isinstance(prepare_bindings, Mapping):
        raise StageWorkerError("order-rule prepare retry config bindings missing")
    if (
        prepare_bindings.get("rule_digest") != bindings.get("rule_digest")
        or prepare_bindings.get("validation_digest") != bindings.get("validation_digest")
    ):
        raise StageWorkerError("order-rule prepare retry semantic binding drift")
    fix_relative = bindings.get("fix_path")
    if not isinstance(fix_relative, str) or not fix_relative:
        raise StageWorkerError("order-rule prepare retry fix path invalid")
    fix_path = (paths.control_repo / fix_relative).resolve()
    try:
        fix_path.relative_to(paths.control_repo.resolve())
    except ValueError as exc:
        raise StageWorkerError("order-rule prepare retry fix outside repository") from exc
    if _file_sha256(fix_path) != bindings.get("fix_file_sha256"):
        raise StageWorkerError("order-rule prepare retry fix digest differs")
    return authorization


def _parse_order_rule_prepare_marker(paths: StageWorkerPaths) -> StageJob | None:
    marker_path = paths.order_rule_prepare_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "order-rule prepare budget")
    if (
        marker.get("schema_version") != ORDER_RULE_PREPARE_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-007-B"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("order-rule prepare budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_ORDER_RULE_PREPARE_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("order-rule prepare budget marker differs")
    return job


def _parse_order_rule_prepare_retry_marker(
    paths: StageWorkerPaths, failed_job: StageJob,
) -> StageJob | None:
    marker_path = paths.order_rule_prepare_retry_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "order-rule prepare retry budget")
    if (
        marker.get("schema_version") != ORDER_RULE_PREPARE_RETRY_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-007-B"
        or marker.get("technical_retry_of") != failed_job.job_id
        or marker.get("failed_input_digest") != failed_job.input_digest
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("order-rule prepare retry budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_ORDER_RULE_PREPARE_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("order-rule prepare retry budget marker differs")
    return job


def _idempotent_stage_submission(paths: StageWorkerPaths, job: StageJob) -> dict[str, Any]:
    observed = read_status(paths, job.job_id)
    return {
        **job.to_dict(),
        "input_digest": job.input_digest,
        "state": observed["state"],
        "idempotent_reuse": True,
    }


def _existing_order_rule_prepare_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> tuple[dict[str, Any] | None, StageJob | None]:
    failed_job = _parse_order_rule_prepare_marker(paths)
    if failed_job is None:
        return None, None
    if failed_job.code_revision == code_revision:
        return _idempotent_stage_submission(paths, failed_job), None
    retry_job = _parse_order_rule_prepare_retry_marker(paths, failed_job)
    if retry_job is not None:
        if retry_job.code_revision == code_revision:
            result = _idempotent_stage_submission(paths, retry_job)
            result["technical_retry_of"] = failed_job.job_id
            return result, None
        raise StageWorkerError("order-rule prepare technical retry budget is already consumed")
    observed = read_status(paths, failed_job.job_id)
    if (
        observed.get("state") != "failed"
        or observed.get("error") != "stage_order_rule_prepare_process_failed"
        or observed.get("input_digest") != failed_job.input_digest
        or observed.get("code_revision") != failed_job.code_revision
    ):
        raise StageWorkerError("order-rule prepare is already bound to another immutable input")
    _order_rule_prepare_retry_authorization(paths, failed_job)
    return None, failed_job


def _order_rule_resolve_score_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": ORDER_RULE_RESOLVE_SCORE_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-007-C",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _require_order_rule_resolve_score_parent(paths: StageWorkerPaths) -> None:
    config = _object(
        paths.control_repo / "config/stage-learning/order-rule-resolve-score.v1.json",
        "order-rule resolve-score config",
    )
    prepare = config.get("prepare")
    if not isinstance(prepare, Mapping):
        raise StageWorkerError("order-rule resolve-score prepare binding missing")
    job_id = str(prepare.get("job_id") or "")
    if not _JOB_ID.fullmatch(job_id):
        raise StageWorkerError("order-rule resolve-score prepare job id differs")
    status_path = paths.status / f"{job_id}.json"
    if not status_path.is_file():
        raise StageWorkerError("order-rule resolve-score prepare status missing")
    observed = _object(status_path, "order-rule resolve-score prepare status")
    result_ref = observed.get("result_ref")
    prepared = result_ref.get("order_rule_prepare") if isinstance(result_ref, Mapping) else None
    if (
        observed.get("state") != "succeeded"
        or not isinstance(prepared, Mapping)
        or result_ref.get("manifest_sha256") != prepare.get("manifest_sha256")
        or prepared.get("selection_digest") != prepare.get("selection_digest")
        or prepared.get("evidence_digest") != prepare.get("evidence_digest")
        or prepared.get("selected_case_count") != prepare.get("case_count")
        or prepared.get("source_isolation_verified") is not True
        or prepared.get("review_ready") is not True
    ):
        raise StageWorkerError("order-rule resolve-score prepare receipt differs")


def _existing_order_rule_resolve_score_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.order_rule_resolve_score_budget
    if not marker_path.exists():
        _require_order_rule_resolve_score_parent(paths)
        return None
    marker = _object(marker_path, "order-rule resolve-score budget")
    if (
        marker.get("schema_version") != ORDER_RULE_RESOLVE_SCORE_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-007-C"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("order-rule resolve-score budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_ORDER_RULE_RESOLVE_SCORE_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("order-rule resolve-score budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError(
            "order-rule resolve-score has no retry or input-replacement authority"
        )
    return _idempotent_stage_submission(paths, job)


def _directional_boundary_d1_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": DIRECTIONAL_BOUNDARY_D1_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-008-D",
        "phase": "D1_PREPARE_CALIBRATION_AND_FREEZE_EVALUATION_IDENTITY_PLAN",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_directional_boundary_d1_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.directional_boundary_d1_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "directional boundary D1 budget")
    if (
        marker.get("schema_version") != DIRECTIONAL_BOUNDARY_D1_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-008-D"
        or marker.get("phase") != "D1_PREPARE_CALIBRATION_AND_FREEZE_EVALUATION_IDENTITY_PLAN"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("directional boundary D1 budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_DIRECTIONAL_BOUNDARY_D1_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("directional boundary D1 budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError("directional boundary D1 has no retry or replacement authority")
    return _idempotent_stage_submission(paths, job)


def _directional_boundary_d3_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": DIRECTIONAL_BOUNDARY_D3_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-008-D",
        "phase": "D3_PREPARE_EVALUATION_AFTER_CANDIDATE_FREEZE",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_directional_boundary_d3_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.directional_boundary_d3_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "directional boundary D3 budget")
    if (
        marker.get("schema_version") != DIRECTIONAL_BOUNDARY_D3_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-008-D"
        or marker.get("phase") != "D3_PREPARE_EVALUATION_AFTER_CANDIDATE_FREEZE"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("directional boundary D3 budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_DIRECTIONAL_BOUNDARY_D3_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("directional boundary D3 budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError("directional boundary D3 has no retry or replacement authority")
    return _idempotent_stage_submission(paths, job)


def _positive_instability_d1_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": POSITIVE_INSTABILITY_D1_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-009-D",
        "phase": "D1_PREPARE_2020_DEVELOPMENT_AND_FREEZE_2021_EVALUATION_IDENTITY_PLAN",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_positive_instability_d1_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.positive_instability_d1_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "positive instability D1 budget")
    if (
        marker.get("schema_version") != POSITIVE_INSTABILITY_D1_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-009-D"
        or marker.get("phase")
        != "D1_PREPARE_2020_DEVELOPMENT_AND_FREEZE_2021_EVALUATION_IDENTITY_PLAN"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("positive instability D1 budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_POSITIVE_INSTABILITY_D1_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("positive instability D1 budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError("positive instability D1 has no retry or replacement authority")
    return _idempotent_stage_submission(paths, job)


def _orderly_consolidation_joint_d1_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": ORDERLY_CONSOLIDATION_JOINT_D1_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-011-D",
        "phase": "D1_PREPARE_FRESH_2018_JOINT_DEVELOPMENT_AND_BIND_PRESERVED_2021_PLAN",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _orderly_consolidation_joint_d1_recovery_budget_value(
    failed_job: StageJob, recovery_job: StageJob,
) -> dict[str, Any]:
    return {
        "schema_version": ORDERLY_CONSOLIDATION_JOINT_D1_RECOVERY_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-011-D1-TRANSPORT-RECOVERY",
        "transport_recovery_of": failed_job.job_id,
        "failed_input_digest": failed_job.input_digest,
        "job": recovery_job.to_dict(),
        "input_digest": recovery_job.input_digest,
    }


def _parse_orderly_consolidation_joint_d1_marker(
    paths: StageWorkerPaths,
) -> StageJob | None:
    marker_path = paths.orderly_consolidation_joint_d1_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "orderly consolidation joint D1 budget")
    if (
        marker.get("schema_version") != ORDERLY_CONSOLIDATION_JOINT_D1_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-011-D"
        or marker.get("phase")
        != "D1_PREPARE_FRESH_2018_JOINT_DEVELOPMENT_AND_BIND_PRESERVED_2021_PLAN"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("orderly consolidation joint D1 budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("orderly consolidation joint D1 budget marker differs")
    return job


def _parse_orderly_consolidation_joint_d1_recovery_marker(
    paths: StageWorkerPaths, failed_job: StageJob,
) -> StageJob | None:
    marker_path = paths.orderly_consolidation_joint_d1_recovery_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "orderly consolidation joint D1 recovery budget")
    if (
        marker.get("schema_version") != ORDERLY_CONSOLIDATION_JOINT_D1_RECOVERY_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-011-D1-TRANSPORT-RECOVERY"
        or marker.get("transport_recovery_of") != failed_job.job_id
        or marker.get("failed_input_digest") != failed_job.input_digest
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("orderly consolidation joint D1 recovery budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("orderly consolidation joint D1 recovery budget marker differs")
    return job


def _orderly_consolidation_joint_d1_recovery_authorization(
    paths: StageWorkerPaths, failed_job: StageJob,
) -> None:
    authorization = _object(
        paths.control_repo / ORDERLY_CONSOLIDATION_JOINT_D1_RECOVERY_AUTH,
        "orderly consolidation joint D1 transport recovery authorization",
    )
    if (
        authorization.get("schema_version")
        != "quanthus.stage-orderly-consolidation-joint-d1-transport-recovery.v1"
        or authorization.get("work_item") != "P3-STAGE-011-D1-TRANSPORT-RECOVERY"
        or authorization.get("recovery_kind") != "protocol_budget_conformance_only"
    ):
        raise StageWorkerError("orderly consolidation joint D1 recovery authorization differs")
    failed = authorization.get("failed_job")
    defect = authorization.get("implementation_conformance_defect")
    frozen = authorization.get("frozen_parent_contract")
    exposure = authorization.get("observed_result_exposure_before_recovery")
    authority = authorization.get("authority")
    if not all(
        isinstance(value, Mapping)
        for value in (failed, defect, frozen, exposure, authority)
    ):
        raise StageWorkerError("orderly consolidation joint D1 recovery authorization incomplete")
    if (
        failed.get("job_id") != failed_job.job_id
        or failed.get("code_revision") != failed_job.code_revision
        or failed.get("input_digest") != failed_job.input_digest
        or failed.get("terminal_state") != "failed"
        or failed.get("error") != "stage_orderly_consolidation_joint_d1_process_failed"
        or failed.get("artifact_published") is not False
    ):
        raise StageWorkerError("orderly consolidation joint D1 recovery parent differs")

    prereg = _object(
        paths.control_repo
        / "config/stage-learning/orderly-consolidation-joint-development-screen.v1.json",
        "orderly consolidation joint development screen",
    )
    execution = _object(
        paths.control_repo
        / "config/stage-learning/orderly-consolidation-joint-development-execution.v1.json",
        "orderly consolidation joint development execution",
    )
    scope_path = str(defect.get("protocol_path") or "")
    if not scope_path:
        raise StageWorkerError("orderly consolidation joint D1 recovery scope missing")
    scope = _object(paths.control_repo / scope_path, "2018 evidence scope")
    bounds = scope.get("bounds")
    if not isinstance(bounds, Mapping):
        raise StageWorkerError("orderly consolidation joint D1 recovery bounds missing")
    if (
        defect.get("protocol_digest") != _digest(scope)
        or defect.get("declared_max_cases") != bounds.get("max_cases")
        or defect.get("declared_max_source_rows") != bounds.get("max_source_rows")
        or defect.get("declared_max_artifact_bytes") != bounds.get("max_artifact_bytes")
        or defect.get("builder_consumed_signed_bounds") is not True
        or defect.get("validator_hardcoded_case_ceiling") != 160
        or defect.get("validator_hardcoded_source_row_ceiling") != 20000
        or frozen.get("preregistration_digest") != _digest(prereg)
        or frozen.get("execution_digest") != _digest(execution)
        or frozen.get("evidence_scope_digest") != _digest(scope)
        or frozen.get("development_year") != 2018
        or frozen.get("fresh_pool_size") != 512
        or frozen.get("development_identity_capacity") != 256
        or frozen.get("development_case_target") != 192
        or frozen.get("stage0_candidate_count") != 9
        or frozen.get("stage2_candidate_count") != 3
        or frozen.get("candidate_pair_count") != 27
        or frozen.get("preserved_evaluation_identity_count") != 160
        or frozen.get("preserved_evaluation_plan_digest")
        != "sha256:2800777b5941892a73cf022ddd9375780f2a4f63985f1ce0c1eae9b3668169a7"
    ):
        raise StageWorkerError("orderly consolidation joint D1 recovery binding drift")

    if exposure.get("failed_job_market_data_read") is not True:
        raise StageWorkerError("orderly consolidation joint D1 recovery exposure differs")
    for key in (
        "governed_artifact_published",
        "candidate_pair_selected",
        "threshold_values_exposed_to_reviewer",
        "preserved_2021_evaluation_read",
        "2019_p010_development_read",
        "2020_p009_development_read",
        "2022_2024_read",
        "2025_read",
    ):
        if exposure.get(key) is not False:
            raise StageWorkerError("orderly consolidation joint D1 recovery exposure expanded")
    for key in ("development_reference_opinions", "candidate_pair_scores"):
        if exposure.get(key) != 0:
            raise StageWorkerError("orderly consolidation joint D1 recovery exposure expanded")

    for key in (
        "parameter_search",
        "semantic_retry",
        "candidate_replacement",
        "automatic_supplement",
        "preserved_2021_evaluation_read",
        "2019_p010_development_access",
        "2020_p009_development_access",
        "2022_2024_access",
        "2025_access",
        "official_labels",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise StageWorkerError("orderly consolidation joint D1 recovery authority expanded")
    if (
        authority.get("maximum_transport_recovery_jobs") != 1
        or authority.get("counts_against_result_bearing_market_data_job_budget") is not False
        or authority.get("same_preregistration_digest_required") is not True
        or authority.get("same_execution_digest_required") is not True
        or authority.get("same_evidence_scope_required") is not True
        or authority.get("same_identity_exclusion_required") is not True
        or authority.get("same_sampling_seed_required") is not True
        or authority.get("same_candidate_pair_surface_required") is not True
        or authority.get("same_gates_required") is not True
        or authority.get("same_preserved_evaluation_plan_required") is not True
    ):
        raise StageWorkerError("orderly consolidation joint D1 recovery budget differs")


def _existing_orderly_consolidation_joint_d1_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> tuple[dict[str, Any] | None, StageJob | None]:
    failed_job = _parse_orderly_consolidation_joint_d1_marker(paths)
    if failed_job is None:
        return None, None
    if failed_job.code_revision == code_revision:
        return _idempotent_stage_submission(paths, failed_job), None

    recovery_job = _parse_orderly_consolidation_joint_d1_recovery_marker(paths, failed_job)
    if recovery_job is not None:
        if recovery_job.code_revision == code_revision:
            result = _idempotent_stage_submission(paths, recovery_job)
            result["transport_recovery_of"] = failed_job.job_id
            return result, None
        raise StageWorkerError(
            "orderly consolidation joint D1 transport recovery budget is already consumed"
        )

    observed = read_status(paths, failed_job.job_id)
    if (
        observed.get("state") != "failed"
        or observed.get("error") != "stage_orderly_consolidation_joint_d1_process_failed"
        or observed.get("input_digest") != failed_job.input_digest
        or observed.get("code_revision") != failed_job.code_revision
        or observed.get("result_ref") is not None
    ):
        raise StageWorkerError(
            "orderly consolidation joint D1 is already bound to another immutable input"
        )
    _orderly_consolidation_joint_d1_recovery_authorization(paths, failed_job)
    return None, failed_job


def _atomic_proposition_g1_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": ATOMIC_PROPOSITION_G1_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-012-G1",
        "phase": "G1_PREPARE_FRESH_2016_ATOMIC_DEVELOPMENT",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_atomic_proposition_g1_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.atomic_proposition_g1_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "atomic proposition G1 budget")
    if (
        marker.get("schema_version") != ATOMIC_PROPOSITION_G1_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-012-G1"
        or marker.get("phase") != "G1_PREPARE_FRESH_2016_ATOMIC_DEVELOPMENT"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("atomic proposition G1 budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_ATOMIC_PROPOSITION_G1_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("atomic proposition G1 budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError("atomic proposition G1 has no retry or replacement authority")
    return _idempotent_stage_submission(paths, job)


def _development_resource_census_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": DEVELOPMENT_RESOURCE_CENSUS_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-013-A",
        "phase": "METADATA_ONLY_ERA_BOARD_EXPOSURE_CAPACITY_CENSUS",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_development_resource_census_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.development_resource_census_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "development resource census budget")
    if (
        marker.get("schema_version") != DEVELOPMENT_RESOURCE_CENSUS_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-013-A"
        or marker.get("phase") != "METADATA_ONLY_ERA_BOARD_EXPOSURE_CAPACITY_CENSUS"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("development resource census budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("development resource census budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError("development resource census has no retry or replacement authority")
    return _idempotent_stage_submission(paths, job)


def _development_resource_plan_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": DEVELOPMENT_RESOURCE_PLAN_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-013-C",
        "phase": "FREEZE_EXACT_METADATA_IDENTITY_WINDOW_PLAN",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _development_resource_plan_recovery_budget_value(
    failed_job: StageJob, recovery_job: StageJob,
) -> dict[str, Any]:
    return {
        "schema_version": DEVELOPMENT_RESOURCE_PLAN_RECOVERY_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-013-C-R1",
        "metadata_recovery_of": failed_job.job_id,
        "failed_input_digest": failed_job.input_digest,
        "job": recovery_job.to_dict(),
        "input_digest": recovery_job.input_digest,
    }


def _development_resource_plan_recovery_authorization(
    paths: StageWorkerPaths, failed_job: StageJob,
) -> dict[str, Any]:
    auth = _object(
        paths.control_repo / DEVELOPMENT_RESOURCE_PLAN_RECOVERY_AUTH,
        "development resource plan recovery authorization",
    )
    if (
        auth.get("schema_version") != "quanthus.stage-development-resource-plan-recovery.v1"
        or auth.get("work_item") != "P3-STAGE-013-C-R1"
        or auth.get("disposition") != "AUTHORIZE_ONE_METADATA_PLAN_R1_RECOVERY"
    ):
        raise StageWorkerError("development resource plan recovery authorization differs")
    failure = auth.get("failed_job")
    authority = auth.get("authority")
    bindings = auth.get("bindings")
    if not all(isinstance(value, Mapping) for value in (failure, authority, bindings)):
        raise StageWorkerError("development resource plan recovery authorization incomplete")
    if (
        failure.get("job_id") != failed_job.job_id
        or failure.get("code_revision") != failed_job.code_revision
        or failure.get("input_digest") != failed_job.input_digest
        or failure.get("terminal_state") != "failed"
        or failure.get("error") != "stage_development_resource_plan_process_failed"
        or failure.get("artifact_published") is not False
    ):
        raise StageWorkerError("development resource plan recovery failure binding differs")
    required_true = (
        "same_eight_cell_quotas_required",
        "same_total_reserve_required",
        "same_primary_fallback_counts_required",
        "same_global_identity_uniqueness_required",
        "same_identity_seed_required",
        "same_window_seed_required",
        "same_census_artifact_required",
        "same_protected_surfaces_required",
    )
    required_false = (
        "cross_cell_fallback",
        "post_result_replacement",
        "parameter_search",
        "semantic_retry",
        "market_price_read",
        "morphology_read",
        "label_or_reference_read",
        "candidate_output_read",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    )
    if authority.get("maximum_metadata_recovery_jobs") != 1:
        raise StageWorkerError("development resource plan recovery job budget differs")
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise StageWorkerError("development resource plan recovery authority differs")
    for key in ("original_plan_config", "parent_quota_freeze", "quota_revision_r1"):
        binding = bindings.get(key)
        if not isinstance(binding, Mapping):
            raise StageWorkerError("development resource plan recovery binding differs")
        path = paths.control_repo / str(binding.get("path") or "")
        expected = str(binding.get("digest") or "")
        try:
            observed = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise StageWorkerError("development resource plan recovery binding unreadable") from exc
        if expected != observed:
            raise StageWorkerError("development resource plan recovery binding differs")
    return dict(auth)


def _existing_development_resource_plan_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> tuple[dict[str, Any] | None, StageJob | None]:
    marker_path = paths.development_resource_plan_budget
    if not marker_path.exists():
        return None, None
    marker = _object(marker_path, "development resource plan budget")
    if (
        marker.get("schema_version") != DEVELOPMENT_RESOURCE_PLAN_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-013-C"
        or marker.get("phase") != "FREEZE_EXACT_METADATA_IDENTITY_WINDOW_PLAN"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("development resource plan budget marker differs")
    failed_job = StageJob.from_dict(marker["job"])
    if (
        failed_job.kind != STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB
        or marker.get("input_digest") != failed_job.input_digest
    ):
        raise StageWorkerError("development resource plan budget marker differs")
    if failed_job.code_revision == code_revision:
        return _idempotent_stage_submission(paths, failed_job), None
    observed = read_status(paths, failed_job.job_id)
    if (
        observed.get("state") != "failed"
        or observed.get("error") != "stage_development_resource_plan_process_failed"
        or observed.get("input_digest") != failed_job.input_digest
        or observed.get("code_revision") != failed_job.code_revision
        or observed.get("result_ref") is not None
    ):
        raise StageWorkerError("development resource plan recovery requires exact failed parent")
    _development_resource_plan_recovery_authorization(paths, failed_job)
    recovery_path = paths.development_resource_plan_recovery_budget
    if not recovery_path.exists():
        return None, failed_job
    recovery = _object(recovery_path, "development resource plan recovery budget")
    if (
        recovery.get("schema_version") != DEVELOPMENT_RESOURCE_PLAN_RECOVERY_BUDGET_SCHEMA
        or recovery.get("work_item") != "P3-STAGE-013-C-R1"
        or recovery.get("metadata_recovery_of") != failed_job.job_id
        or recovery.get("failed_input_digest") != failed_job.input_digest
        or not isinstance(recovery.get("job"), Mapping)
    ):
        raise StageWorkerError("development resource plan recovery budget marker differs")
    recovery_job = StageJob.from_dict(recovery["job"])
    if (
        recovery_job.kind != STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB
        or recovery.get("input_digest") != recovery_job.input_digest
    ):
        raise StageWorkerError("development resource plan recovery budget marker differs")
    if recovery_job.code_revision != code_revision:
        raise StageWorkerError("development resource plan recovery has no further authority")
    return _idempotent_stage_submission(paths, recovery_job), failed_job


def _atomic_proposition_successor_d1_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": ATOMIC_PROPOSITION_SUCCESSOR_D1_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-013-D1",
        "phase": "EXACT_PLAN_ATOMIC_FIRST_DEVELOPMENT_PREPARE",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_atomic_proposition_successor_d1_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.atomic_proposition_successor_d1_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "atomic proposition successor D1 budget")
    if (
        marker.get("schema_version") != ATOMIC_PROPOSITION_SUCCESSOR_D1_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-013-D1"
        or marker.get("phase") != "EXACT_PLAN_ATOMIC_FIRST_DEVELOPMENT_PREPARE"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("atomic proposition successor D1 budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("atomic proposition successor D1 budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError(
            "atomic proposition successor D1 has no retry or replacement authority"
        )
    return _idempotent_stage_submission(paths, job)


def _atomic_proposition_admission_qualified_d1_budget_value(
    job: StageJob,
) -> dict[str, Any]:
    return {
        "schema_version": ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-014-C1",
        "phase": "ADMISSION_QUALIFIED_ATOMIC_FIRST_DEVELOPMENT_PREPARE",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_atomic_proposition_admission_qualified_d1_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.atomic_proposition_admission_qualified_d1_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "atomic proposition admission-qualified D1 budget")
    if (
        marker.get("schema_version")
        != ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-014-C1"
        or marker.get("phase")
        != "ADMISSION_QUALIFIED_ATOMIC_FIRST_DEVELOPMENT_PREPARE"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError(
            "atomic proposition admission-qualified D1 budget marker differs"
        )
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError(
            "atomic proposition admission-qualified D1 budget marker differs"
        )
    if job.code_revision != code_revision:
        raise StageWorkerError(
            "atomic proposition admission-qualified D1 has no retry or replacement authority"
        )
    return _idempotent_stage_submission(paths, job)


def _development_resource_admission_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": DEVELOPMENT_RESOURCE_ADMISSION_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-014-A",
        "phase": "ADMISSION_ONLY_ERA_BOARD_RESOURCE_SCREEN",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_development_resource_admission_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.development_resource_admission_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "development resource admission budget")
    if (
        marker.get("schema_version") != DEVELOPMENT_RESOURCE_ADMISSION_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-014-A"
        or marker.get("phase") != "ADMISSION_ONLY_ERA_BOARD_RESOURCE_SCREEN"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("development resource admission budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("development resource admission budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError(
            "development resource admission has no retry or replacement authority"
        )
    return _idempotent_stage_submission(paths, job)


def _development_resource_qualified_plan_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-014-B",
        "phase": "FREEZE_ADMISSION_QUALIFIED_ERA_BOARD_PLAN",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_development_resource_qualified_plan_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.development_resource_qualified_plan_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "development resource qualified plan budget")
    if (
        marker.get("schema_version") != DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-014-B"
        or marker.get("phase") != "FREEZE_ADMISSION_QUALIFIED_ERA_BOARD_PLAN"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("development resource qualified plan budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("development resource qualified plan budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError(
            "development resource qualified plan has no retry or replacement authority"
        )
    return _idempotent_stage_submission(paths, job)


def _complete_reserve_review_readiness_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": COMPLETE_RESERVE_REVIEW_READINESS_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-015-A",
        "phase": "COMPLETE_RESERVE_REVIEW_READINESS_CENSUS",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_complete_reserve_review_readiness_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.complete_reserve_review_readiness_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "complete reserve review readiness budget")
    if (
        marker.get("schema_version") != COMPLETE_RESERVE_REVIEW_READINESS_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-015-A"
        or marker.get("phase") != "COMPLETE_RESERVE_REVIEW_READINESS_CENSUS"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("complete reserve review readiness budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("complete reserve review readiness budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError(
            "complete reserve review readiness has no retry or replacement authority"
        )
    return _idempotent_stage_submission(paths, job)


def _complete_reserve_additional_review_set_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-015-B",
        "phase": "FREEZE_COMPLETE_ADDITIONAL_REVIEW_SET",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _complete_reserve_additional_review_set_recovery_budget_value(
    failed_job: StageJob, recovery_job: StageJob,
) -> dict[str, Any]:
    return {
        "schema_version": COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_RECOVERY_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-015-B-R1",
        "technical_recovery_of": failed_job.job_id,
        "failed_input_digest": failed_job.input_digest,
        "job": recovery_job.to_dict(),
        "input_digest": recovery_job.input_digest,
    }


def _complete_reserve_additional_review_set_recovery_authorization(
    paths: StageWorkerPaths, failed_job: StageJob,
) -> dict[str, Any]:
    auth = _object(
        paths.control_repo / COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_RECOVERY_AUTH,
        "complete reserve additional review set recovery authorization",
    )
    if (
        auth.get("schema_version")
        != "quanthus.stage-complete-reserve-additional-review-set-recovery.v1"
        or auth.get("work_item") != "P3-STAGE-015-B-R1"
        or auth.get("disposition") != "AUTHORIZE_ONE_TECHNICAL_BINDING_RECOVERY"
    ):
        raise StageWorkerError(
            "complete reserve additional review set recovery authorization differs"
        )
    failure = auth.get("failed_job")
    authority = auth.get("authority")
    bindings = auth.get("bindings")
    if not all(isinstance(value, Mapping) for value in (failure, authority, bindings)):
        raise StageWorkerError(
            "complete reserve additional review set recovery authorization incomplete"
        )
    if (
        failure.get("job_id") != failed_job.job_id
        or failure.get("code_revision") != failed_job.code_revision
        or failure.get("input_digest") != failed_job.input_digest
        or failure.get("terminal_state") != "failed"
        or failure.get("error") != "stage_complete_reserve_additional_review_set_process_failed"
        or failure.get("artifact_published") is not False
        or failure.get("runtime_artifact_read_reached") is not False
        or failure.get("semantic_read_reached") is not False
    ):
        raise StageWorkerError(
            "complete reserve additional review set recovery failure binding differs"
        )
    required_true = (
        "same_p015_b_config_required",
        "same_readiness_artifact_required",
        "same_qualified_plan_required",
        "same_p014_review_ledger_required",
        "same_expected_additional_review_count_60_required",
        "same_all_matching_rows_included_required",
        "field_path_repair_only",
    )
    required_false = (
        "resource_change",
        "selection_predicate_change",
        "positive_count_stop",
        "semantic_sorting",
        "new_identity_discovery",
        "market_data_read",
        "semantic_values_read",
        "reference_opinions_read",
        "candidate_output_read",
        "threshold_basis_read",
        "selection_transport_read",
        "candidate_scoring",
        "preserved_2021_market_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    )
    if authority.get("maximum_technical_recovery_jobs") != 1:
        raise StageWorkerError("complete reserve additional review set recovery job budget differs")
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise StageWorkerError("complete reserve additional review set recovery authority differs")
    for key in ("original_p015_b_config", "p014_closeout", "p015_a_result"):
        binding = bindings.get(key)
        if not isinstance(binding, Mapping):
            raise StageWorkerError(
                "complete reserve additional review set recovery binding differs"
            )
        path = paths.control_repo / str(binding.get("path") or "")
        expected = str(binding.get("sha256") or "")
        try:
            observed = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise StageWorkerError(
                "complete reserve additional review set recovery binding unreadable"
            ) from exc
        if expected != observed:
            raise StageWorkerError(
                "complete reserve additional review set recovery binding differs"
            )
    return dict(auth)


def _existing_complete_reserve_additional_review_set_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> tuple[dict[str, Any] | None, StageJob | None]:
    marker_path = paths.complete_reserve_additional_review_set_budget
    if not marker_path.exists():
        return None, None
    marker = _object(marker_path, "complete reserve additional review set budget")
    if (
        marker.get("schema_version") != COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-015-B"
        or marker.get("phase") != "FREEZE_COMPLETE_ADDITIONAL_REVIEW_SET"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("complete reserve additional review set budget marker differs")
    failed_job = StageJob.from_dict(marker["job"])
    if (
        failed_job.kind != STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB
        or marker.get("input_digest") != failed_job.input_digest
    ):
        raise StageWorkerError("complete reserve additional review set budget marker differs")
    if failed_job.code_revision == code_revision:
        return _idempotent_stage_submission(paths, failed_job), None
    observed = read_status(paths, failed_job.job_id)
    if (
        observed.get("state") != "failed"
        or observed.get("error") != "stage_complete_reserve_additional_review_set_process_failed"
        or observed.get("input_digest") != failed_job.input_digest
        or observed.get("code_revision") != failed_job.code_revision
        or observed.get("result_ref") is not None
    ):
        raise StageWorkerError(
            "complete reserve additional review set recovery requires exact failed parent"
        )
    _complete_reserve_additional_review_set_recovery_authorization(paths, failed_job)
    recovery_path = paths.complete_reserve_additional_review_set_recovery_budget
    if not recovery_path.exists():
        return None, failed_job
    recovery = _object(recovery_path, "complete reserve additional review set recovery budget")
    if (
        recovery.get("schema_version")
        != COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_RECOVERY_BUDGET_SCHEMA
        or recovery.get("work_item") != "P3-STAGE-015-B-R1"
        or recovery.get("technical_recovery_of") != failed_job.job_id
        or recovery.get("failed_input_digest") != failed_job.input_digest
        or not isinstance(recovery.get("job"), Mapping)
    ):
        raise StageWorkerError(
            "complete reserve additional review set recovery budget marker differs"
        )
    recovery_job = StageJob.from_dict(recovery["job"])
    if (
        recovery_job.kind != STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB
        or recovery.get("input_digest") != recovery_job.input_digest
    ):
        raise StageWorkerError(
            "complete reserve additional review set recovery budget marker differs"
        )
    if recovery_job.code_revision != code_revision:
        raise StageWorkerError(
            "complete reserve additional review set recovery has no further authority"
        )
    return _idempotent_stage_submission(paths, recovery_job), failed_job


def _complete_reserve_additional_review_prepare_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-015-C1",
        "phase": "PREPARE_EXACT_60_PRIMARY_BLIND_REVIEW",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_complete_reserve_additional_review_prepare_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.complete_reserve_additional_review_prepare_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "complete reserve additional review prepare budget")
    if (
        marker.get("schema_version") != COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-015-C1"
        or marker.get("phase") != "PREPARE_EXACT_60_PRIMARY_BLIND_REVIEW"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("complete reserve additional review prepare budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("complete reserve additional review prepare budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError(
            "complete reserve additional review prepare has no retry or replacement authority"
        )
    return _idempotent_stage_submission(paths, job)


def _combined_atomic_scoring_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": COMBINED_ATOMIC_SCORING_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-016-B",
        "phase": "INDEPENDENT_ATOMIC_SCORING",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_combined_atomic_scoring_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.combined_atomic_scoring_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "combined atomic scoring budget")
    if (
        marker.get("schema_version") != COMBINED_ATOMIC_SCORING_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-016-B"
        or marker.get("phase") != "INDEPENDENT_ATOMIC_SCORING"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("combined atomic scoring budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_COMBINED_ATOMIC_SCORING_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("combined atomic scoring budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError("combined atomic scoring has no retry or replacement authority")
    return _idempotent_stage_submission(paths, job)


def _stratum_normalized_instability_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": STRATUM_NORMALIZED_INSTABILITY_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-017-B",
        "phase": "STRATUM_NORMALIZED_INSTABILITY_DEVELOPMENT_SCORING",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _stratum_normalized_instability_recovery_budget_value(
    failed_job: StageJob, recovery_job: StageJob,
) -> dict[str, Any]:
    return {
        "schema_version": STRATUM_NORMALIZED_INSTABILITY_RECOVERY_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-017-B-R1",
        "technical_recovery_of": failed_job.job_id,
        "failed_input_digest": failed_job.input_digest,
        "job": recovery_job.to_dict(),
        "input_digest": recovery_job.input_digest,
    }


def _stratum_normalized_instability_recovery_authorization(
    paths: StageWorkerPaths, failed_job: StageJob,
) -> dict[str, Any]:
    auth = _object(
        paths.control_repo / STRATUM_NORMALIZED_INSTABILITY_RECOVERY_AUTH,
        "stratum normalized instability recovery authorization",
    )
    if (
        auth.get("schema_version") != "quanthus.stage-stratum-normalized-instability-recovery.v1"
        or auth.get("work_item") != "P3-STAGE-017-B-R1"
        or auth.get("disposition") != "AUTHORIZE_ONE_TECHNICAL_BINDING_RECOVERY"
    ):
        raise StageWorkerError("stratum normalized instability recovery authorization differs")
    failure = auth.get("failed_job")
    authority = auth.get("authority")
    bindings = auth.get("bindings")
    if not all(isinstance(value, Mapping) for value in (failure, authority, bindings)):
        raise StageWorkerError("stratum normalized instability recovery authorization incomplete")
    if (
        failure.get("job_id") != failed_job.job_id
        or failure.get("code_revision") != failed_job.code_revision
        or failure.get("input_digest") != failed_job.input_digest
        or failure.get("terminal_state") != "failed"
        or failure.get("error") != "stage_stratum_normalized_instability_process_failed"
        or failure.get("artifact_published") is not False
        or failure.get("candidate_scoring_reached") is not False
        or failure.get("semantic_result_reached") is not False
    ):
        raise StageWorkerError("stratum normalized instability recovery failure binding differs")
    required_true = (
        "same_p017_config_required",
        "same_252_case_surface_required",
        "same_review_ledgers_required",
        "same_eight_cell_normalization_required",
        "same_3x3_profile_coordinates_required",
        "same_quality_gates_required",
        "field_path_repair_only",
    )
    required_false = (
        "resource_change",
        "reference_change",
        "normalization_change",
        "candidate_surface_change",
        "numeric_gate_change",
        "envelope_reactivation",
        "dwd_market_read",
        "preserved_2021_read",
        "2022_2024_read",
        "2025_read",
        "daily_or_strategy",
        "trading",
    )
    if authority.get("maximum_technical_recovery_jobs") != 1:
        raise StageWorkerError("stratum normalized instability recovery job budget differs")
    if any(authority.get(key) is not True for key in required_true) or any(
        authority.get(key) is not False for key in required_false
    ):
        raise StageWorkerError("stratum normalized instability recovery authority differs")
    for key in ("original_p017_config", "p015_reference_result", "p016_result"):
        binding = bindings.get(key)
        if not isinstance(binding, Mapping):
            raise StageWorkerError("stratum normalized instability recovery binding differs")
        path = paths.control_repo / str(binding.get("path") or "")
        expected = str(binding.get("sha256") or "")
        try:
            observed = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise StageWorkerError(
                "stratum normalized instability recovery binding unreadable"
            ) from exc
        if expected != observed:
            raise StageWorkerError("stratum normalized instability recovery binding differs")
    return dict(auth)


def _existing_stratum_normalized_instability_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> tuple[dict[str, Any] | None, StageJob | None]:
    marker_path = paths.stratum_normalized_instability_budget
    if not marker_path.exists():
        return None, None
    marker = _object(marker_path, "stratum normalized instability budget")
    if (
        marker.get("schema_version") != STRATUM_NORMALIZED_INSTABILITY_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-017-B"
        or marker.get("phase") != "STRATUM_NORMALIZED_INSTABILITY_DEVELOPMENT_SCORING"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("stratum normalized instability budget marker differs")
    failed_job = StageJob.from_dict(marker["job"])
    if (
        failed_job.kind != STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB
        or marker.get("input_digest") != failed_job.input_digest
    ):
        raise StageWorkerError("stratum normalized instability budget marker differs")
    if failed_job.code_revision == code_revision:
        return _idempotent_stage_submission(paths, failed_job), None
    observed = read_status(paths, failed_job.job_id)
    if (
        observed.get("state") != "failed"
        or observed.get("error") != "stage_stratum_normalized_instability_process_failed"
        or observed.get("input_digest") != failed_job.input_digest
        or observed.get("code_revision") != failed_job.code_revision
        or observed.get("result_ref") is not None
    ):
        raise StageWorkerError(
            "stratum normalized instability recovery requires exact failed parent"
        )
    _stratum_normalized_instability_recovery_authorization(paths, failed_job)
    recovery_path = paths.stratum_normalized_instability_recovery_budget
    if not recovery_path.exists():
        return None, failed_job
    recovery = _object(recovery_path, "stratum normalized instability recovery budget")
    if (
        recovery.get("schema_version") != STRATUM_NORMALIZED_INSTABILITY_RECOVERY_BUDGET_SCHEMA
        or recovery.get("work_item") != "P3-STAGE-017-B-R1"
        or recovery.get("technical_recovery_of") != failed_job.job_id
        or recovery.get("failed_input_digest") != failed_job.input_digest
        or not isinstance(recovery.get("job"), Mapping)
    ):
        raise StageWorkerError("stratum normalized instability recovery budget marker differs")
    recovery_job = StageJob.from_dict(recovery["job"])
    if (
        recovery_job.kind != STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB
        or recovery.get("input_digest") != recovery_job.input_digest
    ):
        raise StageWorkerError("stratum normalized instability recovery budget marker differs")
    if recovery_job.code_revision != code_revision:
        raise StageWorkerError("stratum normalized instability recovery has no further authority")
    return _idempotent_stage_submission(paths, recovery_job), failed_job


def _instability_channel_decomposition_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": INSTABILITY_CHANNEL_DECOMPOSITION_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-018-B",
        "phase": "INSTABILITY_CHANNEL_DECOMPOSITION_DIAGNOSTIC",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_instability_channel_decomposition_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.instability_channel_decomposition_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "instability channel decomposition budget")
    if (
        marker.get("schema_version") != INSTABILITY_CHANNEL_DECOMPOSITION_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-018-B"
        or marker.get("phase") != "INSTABILITY_CHANNEL_DECOMPOSITION_DIAGNOSTIC"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("instability channel decomposition budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("instability channel decomposition budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError(
            "instability channel decomposition has no retry or replacement authority"
        )
    return _idempotent_stage_submission(paths, job)


def _downside_lifecycle_resource_plan_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-020-B",
        "phase": "DOWNSIDE_LIFECYCLE_RESOURCE_ADMISSION_AND_PLAN",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _existing_downside_lifecycle_resource_plan_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> dict[str, Any] | None:
    marker_path = paths.downside_lifecycle_resource_plan_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "downside lifecycle resource plan budget")
    if (
        marker.get("schema_version") != DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-020-B"
        or marker.get("phase") != "DOWNSIDE_LIFECYCLE_RESOURCE_ADMISSION_AND_PLAN"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("downside lifecycle resource plan budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("downside lifecycle resource plan budget marker differs")
    if job.code_revision != code_revision:
        raise StageWorkerError(
            "downside lifecycle resource plan has no retry or replacement authority"
        )
    return _idempotent_stage_submission(paths, job)


def _positive_instability_quantitative_d1_budget_value(job: StageJob) -> dict[str, Any]:
    return {
        "schema_version": POSITIVE_INSTABILITY_QUANTITATIVE_D1_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-010-D",
        "phase": "D1_PREPARE_FRESH_2019_DEVELOPMENT_AND_BIND_PRESERVED_2021_PLAN",
        "job": job.to_dict(),
        "input_digest": job.input_digest,
    }


def _positive_instability_quantitative_d1_retry_budget_value(
    failed_job: StageJob, retry_job: StageJob,
) -> dict[str, Any]:
    return {
        "schema_version": POSITIVE_INSTABILITY_QUANTITATIVE_D1_RETRY_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-010-D",
        "technical_retry_of": failed_job.job_id,
        "failed_input_digest": failed_job.input_digest,
        "job": retry_job.to_dict(),
        "input_digest": retry_job.input_digest,
    }


def _positive_instability_quantitative_d1_recovery_budget_value(
    retry_job: StageJob, recovery_job: StageJob,
) -> dict[str, Any]:
    return {
        "schema_version": POSITIVE_INSTABILITY_QUANTITATIVE_D1_RECOVERY_BUDGET_SCHEMA,
        "work_item": "P3-STAGE-010-D1-TRANSPORT-RECOVERY",
        "transport_recovery_of": retry_job.job_id,
        "failed_input_digest": retry_job.input_digest,
        "job": recovery_job.to_dict(),
        "input_digest": recovery_job.input_digest,
    }


def _parse_positive_instability_quantitative_d1_marker(
    paths: StageWorkerPaths,
) -> StageJob | None:
    marker_path = paths.positive_instability_quantitative_d1_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "positive instability quantitative D1 budget")
    if (
        marker.get("schema_version") != POSITIVE_INSTABILITY_QUANTITATIVE_D1_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-010-D"
        or marker.get("phase")
        != "D1_PREPARE_FRESH_2019_DEVELOPMENT_AND_BIND_PRESERVED_2021_PLAN"
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("positive instability quantitative D1 budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("positive instability quantitative D1 budget marker differs")
    return job


def _parse_positive_instability_quantitative_d1_retry_marker(
    paths: StageWorkerPaths, failed_job: StageJob,
) -> StageJob | None:
    marker_path = paths.positive_instability_quantitative_d1_retry_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "positive instability quantitative D1 retry budget")
    if (
        marker.get("schema_version")
        != POSITIVE_INSTABILITY_QUANTITATIVE_D1_RETRY_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-010-D"
        or marker.get("technical_retry_of") != failed_job.job_id
        or marker.get("failed_input_digest") != failed_job.input_digest
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError("positive instability quantitative D1 retry budget marker differs")
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError("positive instability quantitative D1 retry budget marker differs")
    return job


def _parse_positive_instability_quantitative_d1_recovery_marker(
    paths: StageWorkerPaths, retry_job: StageJob,
) -> StageJob | None:
    marker_path = paths.positive_instability_quantitative_d1_recovery_budget
    if not marker_path.exists():
        return None
    marker = _object(marker_path, "positive instability quantitative D1 recovery budget")
    if (
        marker.get("schema_version")
        != POSITIVE_INSTABILITY_QUANTITATIVE_D1_RECOVERY_BUDGET_SCHEMA
        or marker.get("work_item") != "P3-STAGE-010-D1-TRANSPORT-RECOVERY"
        or marker.get("transport_recovery_of") != retry_job.job_id
        or marker.get("failed_input_digest") != retry_job.input_digest
        or not isinstance(marker.get("job"), Mapping)
    ):
        raise StageWorkerError(
            "positive instability quantitative D1 recovery budget marker differs"
        )
    job = StageJob.from_dict(marker["job"])
    if (
        job.kind != STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB
        or marker.get("input_digest") != job.input_digest
    ):
        raise StageWorkerError(
            "positive instability quantitative D1 recovery budget marker differs"
        )
    return job


def _positive_instability_quantitative_d1_retry_authorization(
    paths: StageWorkerPaths, failed_job: StageJob,
) -> None:
    authorization = _object(
        paths.control_repo / POSITIVE_INSTABILITY_QUANTITATIVE_D1_RETRY_AUTH,
        "positive instability quantitative D1 retry authorization",
    )
    if (
        authorization.get("schema_version")
        != "quanthus.stage-positive-instability-quantitative-d1-retry.v1"
        or authorization.get("work_item") != "P3-STAGE-010-D"
        or authorization.get("retry_kind") != "technical_only"
    ):
        raise StageWorkerError("positive instability quantitative D1 retry authorization differs")
    parent = authorization.get("parent_job")
    correction = authorization.get("correction")
    unchanged = authorization.get("unchanged_semantic_contract")
    budget = authorization.get("budget")
    if not all(isinstance(value, Mapping) for value in (parent, correction, unchanged, budget)):
        raise StageWorkerError(
            "positive instability quantitative D1 retry authorization incomplete"
        )
    if (
        parent.get("job_id") != failed_job.job_id
        or parent.get("code_revision") != failed_job.code_revision
        or parent.get("input_digest") != failed_job.input_digest
        or parent.get("terminal_state") != "failed"
        or parent.get("error") != "stage_positive_instability_quantitative_d1_process_failed"
        or parent.get("artifact_published") is not False
    ):
        raise StageWorkerError("positive instability quantitative D1 retry parent differs")
    current = _object(
        paths.control_repo
        / "config/stage-learning/positive-instability-quantitative-development-screen.v1.json",
        "positive instability quantitative development screen",
    )
    execution = _object(
        paths.control_repo
        / "config/stage-learning/positive-instability-quantitative-development-execution.v1.json",
        "positive instability quantitative development execution",
    )
    source_isolation = current.get("source_isolation")
    if not isinstance(source_isolation, Mapping):
        raise StageWorkerError("positive instability quantitative source isolation missing")
    if (
        correction.get("field") != "source_isolation.candidate_cohort_date"
        or correction.get("old_value") != "2018-12-31"
        or correction.get("new_value") != "2018-12-28"
        or source_isolation.get("candidate_cohort_date") != "2018-12-28"
        or _digest(current) != correction.get("new_preregistration_digest")
        or _digest(execution) != correction.get("new_execution_digest")
    ):
        raise StageWorkerError("positive instability quantitative D1 retry correction drift")
    for key in (
        "development_gates_changed", "evaluation_gates_changed", "projection_topology_changed",
        "relocation_guard_retuned", "stage_2_changed", "preserved_2021_evaluation_plan_changed",
        "preserved_2021_evaluation_read", "2022_2024_access", "2025_access",
    ):
        if unchanged.get(key) is not False:
            raise StageWorkerError("positive instability quantitative D1 retry semantic drift")
    if (
        unchanged.get("fresh_pool_size") != 512
        or unchanged.get("development_identity_capacity") != 256
        or unchanged.get("development_case_target") != 160
        or unchanged.get("candidate_count") != 9
        or budget.get("maximum_retry_jobs") != 1
        or budget.get("semantic_retries") != 0
        or budget.get("automatic_supplement_rounds") != 0
        or budget.get("retry_requires_exact_parent_failure") is not True
        or budget.get("retry_requires_exact_corrected_preregistration_digest") is not True
    ):
        raise StageWorkerError("positive instability quantitative D1 retry budget differs")


def _positive_instability_quantitative_d1_recovery_authorization(
    paths: StageWorkerPaths, initial_job: StageJob, retry_job: StageJob,
) -> None:
    authorization = _object(
        paths.control_repo / POSITIVE_INSTABILITY_QUANTITATIVE_D1_RECOVERY_AUTH,
        "positive instability quantitative D1 transport recovery authorization",
    )
    if (
        authorization.get("schema_version")
        != "quanthus.stage-positive-instability-quantitative-d1-transport-recovery.v1"
        or authorization.get("work_item") != "P3-STAGE-010-D1-TRANSPORT-RECOVERY"
        or authorization.get("recovery_kind") != "evidence_transport_only"
    ):
        raise StageWorkerError(
            "positive instability quantitative D1 recovery authorization differs"
        )
    chain = authorization.get("failed_chain")
    defect = authorization.get("implementation_conformance_defect")
    frozen = authorization.get("frozen_parent_contract")
    exposure = authorization.get("observed_result_exposure_before_recovery")
    authority = authorization.get("authority")
    if not all(
        isinstance(value, Mapping)
        for value in (chain, defect, frozen, exposure, authority)
    ):
        raise StageWorkerError(
            "positive instability quantitative D1 recovery authorization incomplete"
        )
    initial = chain.get("initial_job")
    retry = chain.get("technical_retry_job")
    if not isinstance(initial, Mapping) or not isinstance(retry, Mapping):
        raise StageWorkerError("positive instability quantitative D1 recovery chain missing")
    if (
        initial.get("job_id") != initial_job.job_id
        or initial.get("code_revision") != initial_job.code_revision
        or initial.get("input_digest") != initial_job.input_digest
        or initial.get("terminal_state") != "failed"
        or initial.get("error") != "stage_positive_instability_quantitative_d1_process_failed"
        or initial.get("artifact_published") is not False
        or retry.get("job_id") != retry_job.job_id
        or retry.get("code_revision") != retry_job.code_revision
        or retry.get("input_digest") != retry_job.input_digest
        or retry.get("terminal_state") != "failed"
        or retry.get("error") != "stage_positive_instability_quantitative_d1_process_failed"
        or retry.get("artifact_published") is not False
    ):
        raise StageWorkerError("positive instability quantitative D1 recovery chain differs")
    current = _object(
        paths.control_repo
        / "config/stage-learning/positive-instability-quantitative-development-screen.v1.json",
        "positive instability quantitative development screen",
    )
    execution = _object(
        paths.control_repo
        / "config/stage-learning/positive-instability-quantitative-development-execution.v1.json",
        "positive instability quantitative development execution",
    )
    scope_path = str(defect.get("new_scope_path") or "")
    if not scope_path:
        raise StageWorkerError("positive instability quantitative D1 recovery scope missing")
    scope = _object(paths.control_repo / scope_path, "2019 evidence scope")
    if (
        defect.get("old_builder_ignored_declared_time_fields") is not True
        or defect.get("new_scope_digest") != _digest(scope)
        or defect.get("new_scope_history_start") != scope.get("history_start")
        or defect.get("new_scope_case_start") != scope.get("case_start")
        or defect.get("new_scope_development_end") != scope.get("development_end")
        or frozen.get("preregistration_digest") != _digest(current)
        or frozen.get("execution_digest") != _digest(execution)
        or frozen.get("fresh_pool_size") != 512
        or frozen.get("development_identity_capacity") != 256
        or frozen.get("development_case_target") != 160
        or frozen.get("candidate_count") != 9
        or frozen.get("preserved_evaluation_identity_count") != 160
        or frozen.get("preserved_evaluation_plan_digest")
        != "sha256:2800777b5941892a73cf022ddd9375780f2a4f63985f1ce0c1eae9b3668169a7"
    ):
        raise StageWorkerError("positive instability quantitative D1 recovery binding drift")
    for key in (
        "initial_job_artifact",
        "technical_retry_artifact",
        "candidate_selected",
        "threshold_values_exposed_to_reviewer",
        "preserved_2021_evaluation_read",
        "2022_2024_read",
        "2025_read",
    ):
        if exposure.get(key) is not False:
            raise StageWorkerError(
                "positive instability quantitative D1 recovery exposure expanded"
            )
    for key in ("development_reference_opinions", "candidate_scores"):
        if exposure.get(key) != 0:
            raise StageWorkerError(
                "positive instability quantitative D1 recovery exposure expanded"
            )
    for key in (
        "parameter_search",
        "semantic_retry",
        "candidate_replacement",
        "automatic_supplement",
        "preserved_2021_evaluation_read",
        "2022_2024_access",
        "2025_access",
        "official_labels",
        "daily_or_strategy",
        "trading",
    ):
        if authority.get(key) is not False:
            raise StageWorkerError(
                "positive instability quantitative D1 recovery authority expanded"
            )
    if (
        authority.get("maximum_transport_recovery_jobs") != 1
        or authority.get("counts_against_result_bearing_market_data_job_budget") is not False
        or authority.get("same_preregistration_digest_required") is not True
        or authority.get("same_execution_digest_required") is not True
        or authority.get("same_identity_exclusion_required") is not True
        or authority.get("same_sampling_seed_required") is not True
        or authority.get("same_candidate_grid_required") is not True
        or authority.get("same_gates_required") is not True
        or authority.get("same_preserved_evaluation_plan_required") is not True
    ):
        raise StageWorkerError("positive instability quantitative D1 recovery budget differs")


def _existing_positive_instability_quantitative_d1_submission(
    paths: StageWorkerPaths, *, code_revision: str,
) -> tuple[dict[str, Any] | None, StageJob | None, StageJob | None]:
    initial_job = _parse_positive_instability_quantitative_d1_marker(paths)
    if initial_job is None:
        return None, None, None
    if initial_job.code_revision == code_revision:
        return _idempotent_stage_submission(paths, initial_job), None, None

    retry_job = _parse_positive_instability_quantitative_d1_retry_marker(paths, initial_job)
    if retry_job is None:
        observed = read_status(paths, initial_job.job_id)
        if (
            observed.get("state") != "failed"
            or observed.get("error") != "stage_positive_instability_quantitative_d1_process_failed"
            or observed.get("input_digest") != initial_job.input_digest
            or observed.get("code_revision") != initial_job.code_revision
            or observed.get("result_ref") is not None
        ):
            raise StageWorkerError(
                "positive instability quantitative D1 is already bound to another immutable input"
            )
        _positive_instability_quantitative_d1_retry_authorization(paths, initial_job)
        return None, initial_job, None

    if retry_job.code_revision == code_revision:
        result = _idempotent_stage_submission(paths, retry_job)
        result["technical_retry_of"] = initial_job.job_id
        return result, None, None

    recovery_job = _parse_positive_instability_quantitative_d1_recovery_marker(paths, retry_job)
    if recovery_job is not None:
        if recovery_job.code_revision == code_revision:
            result = _idempotent_stage_submission(paths, recovery_job)
            result["transport_recovery_of"] = retry_job.job_id
            result["failed_chain_root"] = initial_job.job_id
            return result, None, None
        raise StageWorkerError(
            "positive instability quantitative D1 transport recovery budget is already consumed"
        )

    observed_retry = read_status(paths, retry_job.job_id)
    if (
        observed_retry.get("state") != "failed"
        or observed_retry.get("error")
        != "stage_positive_instability_quantitative_d1_process_failed"
        or observed_retry.get("input_digest") != retry_job.input_digest
        or observed_retry.get("code_revision") != retry_job.code_revision
        or observed_retry.get("result_ref") is not None
    ):
        raise StageWorkerError(
            "positive instability quantitative D1 retry is not eligible for transport recovery"
        )
    _positive_instability_quantitative_d1_recovery_authorization(
        paths, initial_job, retry_job
    )
    return None, None, retry_job


def _qualification_budget_value(job: StageJob, *, state: str, reserved_at: str) -> dict[str, Any]:
    if state not in {"reserved", "consumed"}:
        raise StageWorkerError("qualification access budget state is invalid")
    return {
        "schema_version": QUALIFICATION_BUDGET_SCHEMA,
        "qualification_id": STAGE_QUALIFICATION_ID,
        "job_id": job.job_id,
        "kind": job.kind,
        "code_revision": job.code_revision,
        "config": job.config,
        "state": state,
        "reserved_at": reserved_at,
        "consumed_at": _utc_now() if state == "consumed" else None,
    }


def _qualification_budget(paths: StageWorkerPaths) -> dict[str, Any]:
    value = _object(paths.qualification_budget, "qualification access budget")
    expected = {
        "schema_version",
        "qualification_id",
        "job_id",
        "kind",
        "code_revision",
        "config",
        "state",
        "reserved_at",
        "consumed_at",
    }
    if (
        set(value) != expected
        or value.get("schema_version") != QUALIFICATION_BUDGET_SCHEMA
        or value.get("qualification_id") != STAGE_QUALIFICATION_ID
        or value.get("kind") != STAGE_QUALIFICATION_JOB
        or value.get("state") not in {"reserved", "consumed"}
    ):
        raise StageWorkerError("qualification access budget marker differs")
    return value


def _reserve_qualification_budget(paths: StageWorkerPaths, job: StageJob) -> None:
    if job.kind != STAGE_QUALIFICATION_JOB:
        return
    reserved_at = _utc_now()
    _exclusive_json(
        paths.qualification_budget,
        _qualification_budget_value(job, state="reserved", reserved_at=reserved_at),
    )


def _consume_qualification_budget(paths: StageWorkerPaths, job: StageJob) -> None:
    if job.kind != STAGE_QUALIFICATION_JOB:
        return
    value = _qualification_budget(paths)
    if any(
        value.get(key) != expected
        for key, expected in (
            ("job_id", job.job_id),
            ("code_revision", job.code_revision),
            ("config", job.config),
        )
    ):
        raise StageWorkerError("qualification access budget identity differs")
    if value.get("state") != "reserved" or value.get("consumed_at") is not None:
        raise StageWorkerError("qualification access budget is already consumed")
    _atomic_json(
        paths.qualification_budget,
        _qualification_budget_value(
            job,
            state="consumed",
            reserved_at=str(value["reserved_at"]),
        ),
    )


def _release_qualification_reservation(paths: StageWorkerPaths, job: StageJob) -> None:
    if job.kind != STAGE_QUALIFICATION_JOB or not paths.qualification_budget.exists():
        return
    value = _qualification_budget(paths)
    if value.get("job_id") != job.job_id:
        raise StageWorkerError("qualification access budget identity differs")
    if value.get("state") == "reserved" and value.get("consumed_at") is None:
        paths.qualification_budget.unlink()


class StageWorker:
    """Single-concurrency node-local stage worker."""

    def __init__(
        self,
        paths: StageWorkerPaths,
        *,
        poll_seconds: float = 2.0,
        wall_seconds: int = 600,
        terminate_seconds: float = 20.0,
    ) -> None:
        if poll_seconds <= 0 or wall_seconds < 1 or terminate_seconds <= 0:
            raise ValueError("worker timing values must be positive")
        self.paths = paths
        self.poll_seconds = poll_seconds
        self.wall_seconds = wall_seconds
        self.terminate_seconds = terminate_seconds
        self.stopping = False
        self.paths.ensure()

    def _git_env(self) -> dict[str, str]:
        env = dict(os.environ)
        known_hosts = self.paths.git_key.parent / "known_hosts"
        env["GIT_SSH_COMMAND"] = (
            f"ssh -F none -i {self.paths.git_key} -o IdentitiesOnly=yes "
            "-o BatchMode=yes -o StrictHostKeyChecking=yes "
            f"-o UserKnownHostsFile={known_hosts} -o GlobalKnownHostsFile=/dev/null"
        )
        return env

    def _git(self, *arguments: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(self.paths.control_repo), *arguments],
                check=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=self._git_env(),
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip().splitlines()
            tail = detail[-1][:400] if detail else f"exit {exc.returncode}"
            action = " ".join(arguments[:2]) or "command"
            raise StageWorkerError(f"git {action} failed: {tail}") from exc

    def _revision_admitted_locally(self, revision: str) -> bool:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self.paths.control_repo),
                "merge-base",
                "--is-ancestor",
                revision,
                MAIN_REF,
            ],
            text=True,
            capture_output=True,
            env=self._git_env(),
            timeout=30,
        )
        return result.returncode == 0

    def _authorize_revision(self, revision: str) -> None:
        # The installer already resolves and pins GitHub main before starting the
        # service.  Reuse that admitted local authority first so the bootstrap
        # job does not immediately race a second fetch against the installer.
        if self._revision_admitted_locally(revision):
            return
        last_error: StageWorkerError | None = None
        for attempt in range(3):
            try:
                self._git(
                    "fetch",
                    "--no-tags",
                    GITHUB_REPOSITORY,
                    f"+refs/heads/main:{MAIN_REF}",
                    timeout=180,
                )
                last_error = None
                break
            except StageWorkerError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        if last_error is not None:
            raise last_error
        if not self._revision_admitted_locally(revision):
            raise StageWorkerError("requested revision is not admitted by GitHub main")

    def _status_path(self, job_id: str) -> Path:
        return self.paths.status / f"{job_id}.json"

    def _write_status(
        self,
        job: StageJob,
        *,
        state: str,
        stage: str,
        started_at: str | None = None,
        completed_at: str | None = None,
        result_ref: Mapping[str, Any] | None = None,
        error: str | None = None,
        exit_code: int | None = None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": STATUS_SCHEMA,
            "job_id": job.job_id,
            "kind": job.kind,
            "code_revision": job.code_revision,
            "input_digest": job.input_digest,
            "state": state,
            "stage": stage,
            "submitted_at": job.submitted_at,
            "started_at": started_at,
            "completed_at": completed_at,
            "result_ref": None if result_ref is None else dict(result_ref),
            "error": error,
            "exit_code": exit_code,
            "updated_at": _utc_now(),
        }
        _atomic_json(self._status_path(job.job_id), value)
        return value

    def _claim_next(self) -> tuple[StageJob, Path] | None:
        for queued in sorted(self.paths.queue.glob("stage_*.json")):
            claimed = self.paths.claimed / queued.name
            try:
                os.replace(queued, claimed)
            except FileNotFoundError:
                continue
            try:
                job = StageJob.from_dict(_object(claimed, "claimed stage job"))
            except Exception:
                claimed.unlink(missing_ok=True)
                raise
            if self._status_path(job.job_id).exists():
                claimed.unlink(missing_ok=True)
                continue
            return job, claimed
        return None

    def _terminate(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=self.terminate_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            process.wait(timeout=self.terminate_seconds)

    def _prepare_worktree(self, job: StageJob) -> Path:
        worktree = self.paths.worktrees / job.job_id
        if worktree.exists():
            shutil.rmtree(worktree)
        self._authorize_revision(job.code_revision)
        self._git("worktree", "prune")
        self._git("worktree", "add", "--detach", str(worktree), job.code_revision, timeout=120)
        observed = subprocess.check_output(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            text=True,
            timeout=10,
        ).strip()
        if observed != job.code_revision:
            raise StageWorkerError("execution worktree revision differs")
        return worktree

    def _remove_worktree(self, worktree: Path) -> None:
        if not worktree.exists():
            return
        try:
            self._git("worktree", "remove", "--force", str(worktree), timeout=60)
        except (subprocess.SubprocessError, OSError):
            shutil.rmtree(worktree, ignore_errors=True)
            with suppress(subprocess.SubprocessError, OSError):
                self._git("worktree", "prune", timeout=30)

    def _verify_result(self, job: StageJob, job_run_root: Path, worktree: Path) -> dict[str, Any]:
        spec = _JOB_SPECS[job.kind]
        artifacts = sorted(
            path for path in job_run_root.glob(spec["artifact_glob"]) if path.is_dir()
        )
        if len(artifacts) != 1:
            raise StageWorkerError("stage job did not publish exactly one governed artifact")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(worktree / "src")
        completed = subprocess.run(
            [
                str(self.paths.python),
                "-m",
                spec["module"],
                "verify",
                "--artifact",
                str(artifacts[0]),
                "--code-revision",
                job.code_revision,
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=60,
            cwd=worktree,
            env=env,
        )
        try:
            verified = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise StageWorkerError("stage job verification output is invalid") from exc
        if not isinstance(verified, dict) or verified.get("verified") is not True:
            raise StageWorkerError("stage job artifact verification did not pass")
        result = {
            "artifact_root": str(artifacts[0]),
            "run_id": verified.get("run_id"),
            "manifest_sha256": verified.get("manifest_sha256"),
            "file_count": verified.get("file_count"),
        }
        if job.kind == STAGE_CAPACITY_CENSUS_JOB:
            result["capacity_census"] = {
                "eligible_population_count": verified.get("eligible_population_count"),
                "runtime_population_size": verified.get("runtime_population_size"),
                "capacity_curve": verified.get("capacity_curve"),
                "first_population_meeting_all_target_caps": verified.get(
                    "first_population_meeting_all_target_caps"
                ),
                "requires_larger_census": verified.get("requires_larger_census"),
            }
        if job.kind == STAGE_SECOND_GROUP_PROFILE_JOB:
            result["second_group_profile"] = {
                "accepted_stage4_episode_count": verified.get("accepted_stage4_episode_count"),
                "path_observation_count": verified.get("path_observation_count"),
                "path_stop_counts": verified.get("path_stop_counts"),
                "scale_profiles": verified.get("scale_profiles"),
            }
        if job.kind == STAGE_SECOND_GROUP_REPAIR_PROFILE_JOB:
            result["second_group_repair_profile"] = {
                "accepted_stage4_episode_count": verified.get("accepted_stage4_episode_count"),
                "repair_observation_count": verified.get("repair_observation_count"),
                "repair_cycle_count": verified.get("repair_cycle_count"),
                "support_observation_count": verified.get("support_observation_count"),
                "path_stop_counts": verified.get("path_stop_counts"),
                "scale_profiles": verified.get("scale_profiles"),
                "preserved_stage_codes": verified.get("preserved_stage_codes"),
                "stage_5_6_merged": verified.get("stage_5_6_merged"),
                "stage_10_11_merged": verified.get("stage_10_11_merged"),
            }
        if job.kind == STAGE_SECOND_GROUP_DEVELOPMENT_JOB:
            result["second_group_development"] = {
                "disposition": verified.get("disposition"),
                "selected_parameters": verified.get("selected_parameters"),
                "reference_summary": verified.get("reference_summary"),
                "candidate_artifact_digest": verified.get("candidate_artifact_digest"),
            }
        if job.kind == STAGE_SECOND_GROUP_DEVELOPMENT_SUPPLEMENT_JOB:
            result["second_group_development_supplement"] = {
                "disposition": verified.get("disposition"),
                "milestone_complete": verified.get("milestone_complete"),
                "selected_parameters": verified.get("selected_parameters"),
                "reference_summary": verified.get("reference_summary"),
                "stage_7_gate_reason_counts": verified.get("stage_7_gate_reason_counts"),
                "candidate_artifact_digest": verified.get("candidate_artifact_digest"),
            }
        if job.kind == STAGE_THIRD_GROUP_PROFILE_JOB:
            result["third_group_profile"] = {
                "profile_observation_count": verified.get("profile_observation_count"),
                "reset_counts": verified.get("reset_counts"),
                "metric_profiles": verified.get("metric_profiles"),
                "joint_diagnostic_counts": verified.get("joint_diagnostic_counts"),
                "diagnostic_slice_counts": verified.get("diagnostic_slice_counts"),
            }
        if job.kind == STAGE_ORDER_PROPOSITION_DEVELOPMENT_JOB:
            result["order_proposition_development"] = {
                "disposition": verified.get("disposition"),
                "selected_parameters": verified.get("selected_parameters"),
                "selected_pair_overlap_count": verified.get("selected_pair_overlap_count"),
                "reference_summary": verified.get("reference_summary"),
                "observation_count": verified.get("observation_count"),
                "candidate_artifact_digest": verified.get("candidate_artifact_digest"),
            }
        if job.kind == STAGE_ORDER_RULE_PREPARE_JOB:
            result["order_rule_prepare"] = {
                "source_isolation_verified": verified.get("source_isolation_verified"),
                "old_cohort_size": verified.get("old_cohort_size"),
                "excluded_economic_identity_count": verified.get(
                    "excluded_economic_identity_count"
                ),
                "candidate_population_count": verified.get("candidate_population_count"),
                "pool_size": verified.get("pool_size"),
                "selected_case_count": verified.get("selected_case_count"),
                "selection_digest": verified.get("selection_digest"),
                "selection_shortfalls": verified.get("selection_shortfalls"),
                "evidence_digest": verified.get("evidence_digest"),
                "evidence_source_row_count": verified.get("evidence_source_row_count"),
                "review_ready": verified.get("review_ready"),
            }
        if job.kind == STAGE_ORDER_RULE_RESOLVE_SCORE_JOB:
            result["order_rule_resolve_score"] = {
                "case_count": verified.get("case_count"),
                "opinion_count": verified.get("opinion_count"),
                "reference_manifest_sha256": verified.get("reference_manifest_sha256"),
                "primary_reference_counts": verified.get("primary_reference_counts"),
                "gate_calculation": verified.get("gate_calculation"),
                "primary_disagreement_count": verified.get("primary_disagreement_count"),
                "joint_primary_unknown_count": verified.get("joint_primary_unknown_count"),
                "recent_disagreement_count": verified.get("recent_disagreement_count"),
                "independent_validation": verified.get("independent_validation"),
                "full_context_blinding_claimed": verified.get("full_context_blinding_claimed"),
                "official_labels_authorized": verified.get("official_labels_authorized"),
                "runtime_activation_authorized": verified.get("runtime_activation_authorized"),
            }
        if job.kind == STAGE_DIRECTIONAL_BOUNDARY_D1_JOB:
            result["directional_boundary_d1"] = {
                "fresh_pool_size": verified.get("fresh_pool_size"),
                "calibration_identity_capacity": verified.get("calibration_identity_capacity"),
                "evaluation_identity_capacity": verified.get("evaluation_identity_capacity"),
                "calibration_case_count": verified.get("calibration_case_count"),
                "calibration_selection_counts": verified.get("calibration_selection_counts"),
                "calibration_selection_shortfalls": verified.get(
                    "calibration_selection_shortfalls"
                ),
                "calibration_selection_digest": verified.get("calibration_selection_digest"),
                "calibration_evidence_digest": verified.get("calibration_evidence_digest"),
                "path_facts_digest": verified.get("path_facts_digest"),
                "evaluation_planned_identity_count": verified.get(
                    "evaluation_planned_identity_count"
                ),
                "evaluation_identity_digest": verified.get("evaluation_identity_digest"),
                "evaluation_plan_digest": verified.get("evaluation_plan_digest"),
                "evaluation_data_read": verified.get("evaluation_data_read"),
                "parameter_search": verified.get("parameter_search"),
                "opinion_count": verified.get("opinion_count"),
                "candidate_frozen": verified.get("candidate_frozen"),
                "review_ready": verified.get("review_ready"),
            }
        if job.kind == STAGE_DIRECTIONAL_BOUNDARY_D3_JOB:
            result["directional_boundary_d3"] = {
                "frozen_candidate_id": verified.get("frozen_candidate_id"),
                "evaluation_identity_count": verified.get("evaluation_identity_count"),
                "evaluation_selected_case_count": verified.get(
                    "evaluation_selected_case_count"
                ),
                "evaluation_shortfall_count": verified.get("evaluation_shortfall_count"),
                "evaluation_selection_digest": verified.get("evaluation_selection_digest"),
                "evaluation_evidence_digest": verified.get("evaluation_evidence_digest"),
                "path_facts_digest": verified.get("path_facts_digest"),
                "opinion_count": verified.get("opinion_count"),
                "candidate_scored": verified.get("candidate_scored"),
                "parameter_search": verified.get("parameter_search"),
                "candidate_replacement": verified.get("candidate_replacement"),
                "review_ready": verified.get("review_ready"),
            }
        if job.kind == STAGE_POSITIVE_INSTABILITY_D1_JOB:
            result["positive_instability_d1"] = {
                "fresh_pool_size": verified.get("fresh_pool_size"),
                "development_identity_capacity": verified.get("development_identity_capacity"),
                "evaluation_identity_capacity": verified.get("evaluation_identity_capacity"),
                "development_selected_case_count": verified.get(
                    "development_selected_case_count"
                ),
                "development_shortfall_count": verified.get("development_shortfall_count"),
                "development_selection_digest": verified.get("development_selection_digest"),
                "development_evidence_digest": verified.get("development_evidence_digest"),
                "threshold_basis_digest": verified.get("threshold_basis_digest"),
                "candidate_grid_digest": verified.get("candidate_grid_digest"),
                "candidate_count": verified.get("candidate_count"),
                "path_facts_digest": verified.get("path_facts_digest"),
                "evaluation_planned_identity_count": verified.get(
                    "evaluation_planned_identity_count"
                ),
                "evaluation_identity_digest": verified.get("evaluation_identity_digest"),
                "evaluation_plan_digest": verified.get("evaluation_plan_digest"),
                "evaluation_data_read": verified.get("evaluation_data_read"),
                "parameter_search": verified.get("parameter_search"),
                "opinion_count": verified.get("opinion_count"),
                "candidate_frozen": verified.get("candidate_frozen"),
                "review_ready": verified.get("review_ready"),
            }
        if job.kind == STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB:
            result["positive_instability_quantitative_d1"] = {
                "fresh_pool_size": verified.get("fresh_pool_size"),
                "development_identity_capacity": verified.get("development_identity_capacity"),
                "development_selected_case_count": verified.get(
                    "development_selected_case_count"
                ),
                "development_shortfall_count": verified.get("development_shortfall_count"),
                "development_selection_digest": verified.get("development_selection_digest"),
                "development_evidence_digest": verified.get("development_evidence_digest"),
                "threshold_basis_digest": verified.get("threshold_basis_digest"),
                "candidate_grid_digest": verified.get("candidate_grid_digest"),
                "candidate_count": verified.get("candidate_count"),
                "path_facts_digest": verified.get("path_facts_digest"),
                "preserved_evaluation_identity_count": verified.get(
                    "preserved_evaluation_identity_count"
                ),
                "preserved_evaluation_identity_digest": verified.get(
                    "preserved_evaluation_identity_digest"
                ),
                "preserved_evaluation_plan_digest": verified.get(
                    "preserved_evaluation_plan_digest"
                ),
                "preserved_evaluation_data_read": verified.get(
                    "preserved_evaluation_data_read"
                ),
                "parameter_search": verified.get("parameter_search"),
                "opinion_count": verified.get("opinion_count"),
                "candidate_frozen": verified.get("candidate_frozen"),
                "review_ready": verified.get("review_ready"),
            }
        if job.kind == STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB:
            result["orderly_consolidation_joint_d1"] = {
                "fresh_pool_size": verified.get("fresh_pool_size"),
                "development_identity_capacity": verified.get("development_identity_capacity"),
                "development_selected_case_count": verified.get(
                    "development_selected_case_count"
                ),
                "development_shortfall_count": verified.get("development_shortfall_count"),
                "development_selection_digest": verified.get("development_selection_digest"),
                "development_evidence_digest": verified.get("development_evidence_digest"),
                "threshold_basis_digest": verified.get("threshold_basis_digest"),
                "candidate_grid_digest": verified.get("candidate_grid_digest"),
                "stage0_candidate_count": verified.get("stage0_candidate_count"),
                "stage2_candidate_count": verified.get("stage2_candidate_count"),
                "candidate_pair_count": verified.get("candidate_pair_count"),
                "path_facts_digest": verified.get("path_facts_digest"),
                "preserved_evaluation_identity_count": verified.get(
                    "preserved_evaluation_identity_count"
                ),
                "preserved_evaluation_identity_digest": verified.get(
                    "preserved_evaluation_identity_digest"
                ),
                "preserved_evaluation_plan_digest": verified.get(
                    "preserved_evaluation_plan_digest"
                ),
                "preserved_evaluation_data_read": verified.get(
                    "preserved_evaluation_data_read"
                ),
                "parameter_search": verified.get("parameter_search"),
                "opinion_count": verified.get("opinion_count"),
                "pair_frozen": verified.get("pair_frozen"),
                "review_ready": verified.get("review_ready"),
            }
        if job.kind == STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB:
            result["development_resource_census"] = {
                "economic_identity_count": verified.get("economic_identity_count"),
                "window_count": verified.get("window_count"),
                "identity_window_capacity": verified.get("identity_window_capacity"),
                "legacy_whole_identity_available_capacity": verified.get(
                    "legacy_whole_identity_available_capacity"
                ),
                "exposure_aware_available_capacity": verified.get(
                    "exposure_aware_available_capacity"
                ),
                "released_capacity_vs_whole_identity": verified.get(
                    "released_capacity_vs_whole_identity"
                ),
                "released_unique_identity_count": verified.get(
                    "released_unique_identity_count"
                ),
                "preserved_2021_identity_count": verified.get(
                    "preserved_2021_identity_count"
                ),
                "capacity_matrix": verified.get("capacity_matrix"),
            }
        if job.kind == STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB:
            result["development_resource_plan"] = {
                "row_count": verified.get("row_count"),
                "primary_count": verified.get("primary_count"),
                "fallback_count": verified.get("fallback_count"),
                "unique_identity_count": verified.get("unique_identity_count"),
                "cell_counts": verified.get("cell_counts"),
                "identity_digest": verified.get("identity_digest"),
                "plan_digest": verified.get("plan_digest"),
                "market_data_read": verified.get("market_data_read"),
            }
        if job.kind == STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB:
            result["development_resource_admission"] = {
                "candidate_window_count": verified.get("candidate_window_count"),
                "candidate_unique_identity_count": verified.get(
                    "candidate_unique_identity_count"
                ),
                "qualified_window_count": verified.get("qualified_window_count"),
                "qualified_unique_identity_count": verified.get(
                    "qualified_unique_identity_count"
                ),
                "cell_summary": verified.get("cell_summary"),
                "all_cells_have_required_qualified_capacity": verified.get(
                    "all_cells_have_required_qualified_capacity"
                ),
                "raw_ohlc_retained": verified.get("raw_ohlc_retained"),
                "semantic_job_authorized": verified.get("semantic_job_authorized"),
            }
        if job.kind == STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB:
            result["development_resource_qualified_plan"] = {
                "row_count": verified.get("row_count"),
                "unique_identity_count": verified.get("unique_identity_count"),
                "primary_count": verified.get("primary_count"),
                "fallback_count": verified.get("fallback_count"),
                "cell_counts": verified.get("cell_counts"),
                "identity_digest": verified.get("identity_digest"),
                "plan_digest": verified.get("plan_digest"),
                "market_data_read": verified.get("market_data_read"),
                "semantic_job_authorized": verified.get("semantic_job_authorized"),
            }
        if job.kind == STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB:
            result["complete_reserve_review_readiness"] = {
                "row_count": verified.get("row_count"),
                "ready_count": verified.get("ready_count"),
                "unready_count": verified.get("unready_count"),
                "ready_by_cell": verified.get("ready_by_cell"),
                "reason_counts": verified.get("reason_counts"),
                "already_reviewed_ready_count": verified.get(
                    "already_reviewed_ready_count"
                ),
                "new_review_required_count": verified.get("new_review_required_count"),
                "ledger_digest": verified.get("ledger_digest"),
                "continuation_pass": verified.get("continuation_pass"),
                "semantic_values_retained": verified.get("semantic_values_retained"),
                "reference_opinions_read": verified.get("reference_opinions_read"),
                "selection_transport_read": verified.get("selection_transport_read"),
                "candidate_output_read": verified.get("candidate_output_read"),
                "preserved_2021_market_read": verified.get("preserved_2021_market_read"),
                "2025_read": verified.get("2025_read"),
            }
        if job.kind == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB:
            result["complete_reserve_additional_review_set"] = {
                "row_count": verified.get("row_count"),
                "cell_counts": verified.get("cell_counts"),
                "identity_digest": verified.get("identity_digest"),
                "set_digest": verified.get("set_digest"),
                "all_matching_rows_included": verified.get("all_matching_rows_included"),
                "positive_count_stop": verified.get("positive_count_stop"),
                "semantic_values_read": verified.get("semantic_values_read"),
                "reference_opinions_read": verified.get("reference_opinions_read"),
                "candidate_output_read": verified.get("candidate_output_read"),
                "threshold_basis_read": verified.get("threshold_basis_read"),
                "selection_transport_read": verified.get("selection_transport_read"),
                "preserved_2021_market_read": verified.get("preserved_2021_market_read"),
                "2025_read": verified.get("2025_read"),
            }
        if job.kind == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB:
            result["complete_reserve_additional_review_prepare"] = {
                "development_selected_case_count": verified.get(
                    "development_selected_case_count"
                ),
                "development_shortfall_count": verified.get("development_shortfall_count"),
                "selected_cell_counts": verified.get("selected_cell_counts"),
                "development_selection_digest": verified.get("development_selection_digest"),
                "development_evidence_digest": verified.get("development_evidence_digest"),
                "path_facts_digest": verified.get("path_facts_digest"),
                "case_strata_digest": verified.get("case_strata_digest"),
                "existing_p014_review_ledger_sha256": verified.get(
                    "existing_p014_review_ledger_sha256"
                ),
                "existing_p014_opinions_read": verified.get("existing_p014_opinions_read"),
                "threshold_basis_computed": verified.get("threshold_basis_computed"),
                "candidate_output_computed": verified.get("candidate_output_computed"),
                "envelope_measurement_computed": verified.get(
                    "envelope_measurement_computed"
                ),
                "selection_transport_read": verified.get("selection_transport_read"),
                "opinion_count": verified.get("opinion_count"),
                "review_ready": verified.get("review_ready"),
                "preserved_2021_market_data_read": verified.get(
                    "preserved_2021_market_data_read"
                ),
                "2025_read": verified.get("2025_read"),
            }
        if job.kind == STAGE_COMBINED_ATOMIC_SCORING_JOB:
            result["combined_atomic_scoring"] = {
                "disposition": verified.get("disposition"),
                "reference_case_count": verified.get("reference_case_count"),
                "atomic_freeze_available": verified.get("atomic_freeze_available"),
                "atomic_freeze_digest": verified.get("atomic_freeze_digest"),
                "instability_trial_count": verified.get("instability_trial_count"),
                "order_trial_count": verified.get("order_trial_count"),
                "relocation_fixed": verified.get("relocation_fixed"),
                "joint_pair_search_count": verified.get("joint_pair_search_count"),
                "projection_performed": verified.get("projection_performed"),
                "dwd_market_read": verified.get("dwd_market_read"),
                "preserved_2021_read": verified.get("preserved_2021_read"),
                "2025_read": verified.get("2025_read"),
            }
        if job.kind == STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB:
            result["stratum_normalized_instability"] = {
                "disposition": verified.get("disposition"),
                "reference_case_count": verified.get("reference_case_count"),
                "cell_count": verified.get("cell_count"),
                "candidate_count": verified.get("candidate_count"),
                "eligible_candidate_count": verified.get("eligible_candidate_count"),
                "selected_profile_id": verified.get("selected_profile_id"),
                "prediction_artifact_available": verified.get("prediction_artifact_available"),
                "basis_digest": verified.get("basis_digest"),
                "profile_grid_digest": verified.get("profile_grid_digest"),
                "dwd_market_read": verified.get("dwd_market_read"),
                "fixed_relocation_scored": verified.get("fixed_relocation_scored"),
                "order_scored": verified.get("order_scored"),
                "projection_performed": verified.get("projection_performed"),
                "preserved_2021_read": verified.get("preserved_2021_read"),
                "2022_2024_read": verified.get("2022_2024_read"),
                "2025_read": verified.get("2025_read"),
            }
        if job.kind == STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB:
            result["instability_channel_decomposition"] = {
                "disposition": verified.get("disposition"),
                "reference_case_count": verified.get("reference_case_count"),
                "cell_count": verified.get("cell_count"),
                "profile_count": verified.get("profile_count"),
                "candidate_selection_performed": verified.get("candidate_selection_performed"),
                "new_semantic_labels_generated": verified.get("new_semantic_labels_generated"),
                "dwd_market_read": verified.get("dwd_market_read"),
                "preserved_2021_read": verified.get("preserved_2021_read"),
                "2022_2024_read": verified.get("2022_2024_read"),
                "2025_read": verified.get("2025_read"),
            }
        if job.kind == STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB:
            result["downside_lifecycle_resource_plan"] = {
                "disposition": verified.get("disposition"),
                "candidate_window_count": verified.get("candidate_window_count"),
                "qualified_window_count": verified.get("qualified_window_count"),
                "cell_summary": verified.get("cell_summary"),
                "all_cells_have_required_qualified_capacity": verified.get(
                    "all_cells_have_required_qualified_capacity"
                ),
                "exact_plan_available": verified.get("exact_plan_available"),
                "exact_plan_row_count": verified.get("exact_plan_row_count"),
                "exact_plan_primary_count": verified.get("exact_plan_primary_count"),
                "exact_plan_fallback_count": verified.get("exact_plan_fallback_count"),
                "exact_plan_digest": verified.get("exact_plan_digest"),
                "ready_for_separate_semantic_review_freeze": verified.get(
                    "ready_for_separate_semantic_review_freeze"
                ),
                "input_readiness_market_read": verified.get("input_readiness_market_read"),
                "preserved_2021_market_read": verified.get("preserved_2021_market_read"),
                "2022_2024_read": verified.get("2022_2024_read"),
                "2025_read": verified.get("2025_read"),
            }
        if job.kind == STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB:
            result["atomic_proposition_successor_d1"] = {
                "fresh_pool_size": verified.get("fresh_pool_size"),
                "development_identity_capacity": verified.get("development_identity_capacity"),
                "development_selected_case_count": verified.get(
                    "development_selected_case_count"
                ),
                "development_shortfall_count": verified.get("development_shortfall_count"),
                "development_selection_digest": verified.get("development_selection_digest"),
                "development_evidence_digest": verified.get("development_evidence_digest"),
                "threshold_basis_digest": verified.get("threshold_basis_digest"),
                "candidate_grid_digest": verified.get("candidate_grid_digest"),
                "envelope_measurements_digest": verified.get("envelope_measurements_digest"),
                "case_strata_digest": verified.get("case_strata_digest"),
                "instability_candidate_count": verified.get("instability_candidate_count"),
                "order_profile_count": verified.get("order_profile_count"),
                "relocation_candidate_count": verified.get("relocation_candidate_count"),
                "joint_pair_search_count": verified.get("joint_pair_search_count"),
                "path_facts_digest": verified.get("path_facts_digest"),
                "preserved_evaluation_identity_count": verified.get(
                    "preserved_evaluation_identity_count"
                ),
                "preserved_evaluation_identity_digest": verified.get(
                    "preserved_evaluation_identity_digest"
                ),
                "preserved_evaluation_plan_digest": verified.get(
                    "preserved_evaluation_plan_digest"
                ),
                "preserved_evaluation_data_read": verified.get(
                    "preserved_evaluation_data_read"
                ),
                "selected_cell_counts": verified.get("selected_cell_counts"),
                "fallback_used_count": verified.get("fallback_used_count"),
                "parameter_search": verified.get("parameter_search"),
                "opinion_count": verified.get("opinion_count"),
                "atomic_freeze": verified.get("atomic_freeze"),
                "review_ready": verified.get("review_ready"),
            }
        if job.kind == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB:
            result["atomic_proposition_admission_qualified_d1"] = {
                "resource_plan_size": verified.get("resource_plan_size"),
                "development_identity_capacity": verified.get("development_identity_capacity"),
                "development_selected_case_count": verified.get(
                    "development_selected_case_count"
                ),
                "development_shortfall_count": verified.get("development_shortfall_count"),
                "development_selection_digest": verified.get("development_selection_digest"),
                "development_evidence_digest": verified.get("development_evidence_digest"),
                "threshold_basis_digest": verified.get("threshold_basis_digest"),
                "candidate_grid_digest": verified.get("candidate_grid_digest"),
                "envelope_measurements_digest": verified.get("envelope_measurements_digest"),
                "case_strata_digest": verified.get("case_strata_digest"),
                "instability_candidate_count": verified.get("instability_candidate_count"),
                "order_profile_count": verified.get("order_profile_count"),
                "relocation_candidate_count": verified.get("relocation_candidate_count"),
                "joint_pair_search_count": verified.get("joint_pair_search_count"),
                "path_facts_digest": verified.get("path_facts_digest"),
                "preserved_evaluation_identity_count": verified.get(
                    "preserved_evaluation_identity_count"
                ),
                "preserved_evaluation_identity_digest": verified.get(
                    "preserved_evaluation_identity_digest"
                ),
                "preserved_evaluation_plan_digest": verified.get(
                    "preserved_evaluation_plan_digest"
                ),
                "preserved_evaluation_data_read": verified.get(
                    "preserved_evaluation_data_read"
                ),
                "selected_cell_counts": verified.get("selected_cell_counts"),
                "fallback_used_count": verified.get("fallback_used_count"),
                "parameter_search": verified.get("parameter_search"),
                "opinion_count": verified.get("opinion_count"),
                "atomic_freeze": verified.get("atomic_freeze"),
                "review_ready": verified.get("review_ready"),
            }
        if job.kind == STAGE_ATOMIC_PROPOSITION_G1_JOB:
            result["atomic_proposition_g1"] = {
                "fresh_pool_size": verified.get("fresh_pool_size"),
                "development_identity_capacity": verified.get("development_identity_capacity"),
                "development_selected_case_count": verified.get(
                    "development_selected_case_count"
                ),
                "development_shortfall_count": verified.get("development_shortfall_count"),
                "development_selection_digest": verified.get("development_selection_digest"),
                "development_evidence_digest": verified.get("development_evidence_digest"),
                "threshold_basis_digest": verified.get("threshold_basis_digest"),
                "candidate_grid_digest": verified.get("candidate_grid_digest"),
                "envelope_measurements_digest": verified.get("envelope_measurements_digest"),
                "instability_candidate_count": verified.get("instability_candidate_count"),
                "order_profile_count": verified.get("order_profile_count"),
                "relocation_candidate_count": verified.get("relocation_candidate_count"),
                "joint_pair_search_count": verified.get("joint_pair_search_count"),
                "path_facts_digest": verified.get("path_facts_digest"),
                "preserved_evaluation_identity_count": verified.get(
                    "preserved_evaluation_identity_count"
                ),
                "preserved_evaluation_identity_digest": verified.get(
                    "preserved_evaluation_identity_digest"
                ),
                "preserved_evaluation_plan_digest": verified.get(
                    "preserved_evaluation_plan_digest"
                ),
                "preserved_evaluation_data_read": verified.get(
                    "preserved_evaluation_data_read"
                ),
                "parameter_search": verified.get("parameter_search"),
                "opinion_count": verified.get("opinion_count"),
                "atomic_freeze": verified.get("atomic_freeze"),
                "review_ready": verified.get("review_ready"),
            }
        return result

    def execute(self, job: StageJob) -> dict[str, Any]:
        started_at = _utc_now()
        self._write_status(job, state="running", stage="preparing", started_at=started_at)
        worktree: Path | None = None
        log_path = self.paths.logs / f"{job.job_id}.log"
        run_root = self.paths.runs / job.job_id
        run_root.mkdir(parents=True, exist_ok=False)
        cancel_path = self.paths.cancel / job.job_id
        qualification_access_started = False
        try:
            worktree = self._prepare_worktree(job)
            if cancel_path.exists() or self.stopping:
                return self._write_status(
                    job,
                    state="canceled",
                    stage="terminal",
                    started_at=started_at,
                    completed_at=_utc_now(),
                    error="cancel_requested_before_execution",
                )
            if job.kind == STAGE_QUALIFICATION_JOB:
                _consume_qualification_budget(self.paths, job)
                qualification_access_started = True
            self._write_status(job, state="running", stage="executing", started_at=started_at)
            env = dict(os.environ)
            env.update(
                {
                    "PYTHONPATH": str(worktree / "src"),
                    "OMP_NUM_THREADS": "8",
                    "OPENBLAS_NUM_THREADS": "8",
                    "MKL_NUM_THREADS": "8",
                    "NUMEXPR_NUM_THREADS": "8",
                }
            )
            spec = _JOB_SPECS[job.kind]
            command = [
                str(self.paths.python),
                "-m",
                spec["module"],
                "run",
                "--config",
                str(worktree / job.config),
                "--training-root",
                str(self.paths.training_root),
                "--output-root",
                str(run_root),
                "--code-revision",
                job.code_revision,
            ]
            with log_path.open("xb") as log:
                process = subprocess.Popen(
                    command,
                    cwd=worktree,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                job_wall_seconds = int(spec.get("wall_seconds", self.wall_seconds))
                deadline = time.monotonic() + job_wall_seconds
                canceled = False
                timed_out = False
                while process.poll() is None:
                    if cancel_path.exists() or self.stopping:
                        canceled = True
                        self._terminate(process)
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        self._terminate(process)
                        break
                    time.sleep(min(self.poll_seconds, 1.0))
                exit_code = process.wait()
            if canceled:
                return self._write_status(
                    job,
                    state="canceled",
                    stage="terminal",
                    started_at=started_at,
                    completed_at=_utc_now(),
                    error="cancel_requested",
                    exit_code=exit_code,
                )
            if timed_out:
                return self._write_status(
                    job,
                    state="failed",
                    stage="terminal",
                    started_at=started_at,
                    completed_at=_utc_now(),
                    error="stage_worker_wall_budget_exceeded",
                    exit_code=exit_code,
                )
            if exit_code != 0:
                return self._write_status(
                    job,
                    state="failed",
                    stage="terminal",
                    started_at=started_at,
                    completed_at=_utc_now(),
                    error=spec["process_error"],
                    exit_code=exit_code,
                )
            self._write_status(job, state="running", stage="verifying", started_at=started_at)
            result_ref = self._verify_result(job, run_root, worktree)
            result_ref["log_path"] = str(log_path)
            return self._write_status(
                job,
                state="succeeded",
                stage="terminal",
                started_at=started_at,
                completed_at=_utc_now(),
                result_ref=result_ref,
                exit_code=0,
            )
        except Exception as exc:
            return self._write_status(
                job,
                state="failed",
                stage="terminal",
                started_at=started_at,
                completed_at=_utc_now(),
                error=f"{type(exc).__name__}:{str(exc)[:500]}",
            )
        finally:
            cancel_path.unlink(missing_ok=True)
            if job.kind == STAGE_QUALIFICATION_JOB and not qualification_access_started:
                with suppress(StageWorkerError):
                    _release_qualification_reservation(self.paths, job)
            if worktree is not None:
                self._remove_worktree(worktree)

    def run_once(self) -> dict[str, Any] | None:
        claimed = self._claim_next()
        if claimed is None:
            return None
        job, claimed_path = claimed
        try:
            return self.execute(job)
        finally:
            claimed_path.unlink(missing_ok=True)

    def serve(self) -> None:
        revision = os.environ.get("QUANTHUS_STAGE_WORKER_CODE_REVISION", "")
        if not _REVISION.fullmatch(revision):
            raise StageWorkerError("worker runtime revision is missing or invalid")
        _atomic_json(
            self.paths.runtime_identity,
            {
                "schema_version": "quanthus.stage-worker-runtime.v1",
                "code_revision": revision,
                "pid": os.getpid(),
                "started_at": _utc_now(),
                "job_kinds": sorted(_JOB_SPECS),
                "concurrency": 1,
            },
        )
        while not self.stopping:
            try:
                result = self.run_once()
            except Exception as exc:
                print(f"stage-worker loop error: {type(exc).__name__}: {exc}", file=sys.stderr)
                result = None
            if result is None:
                time.sleep(self.poll_seconds)


def _paths(args: argparse.Namespace) -> StageWorkerPaths:
    return StageWorkerPaths(
        training_root=Path(args.training_root).expanduser().resolve(),
        control_repo=Path(args.control_repo).expanduser().resolve(),
        git_key=Path(args.git_key).expanduser().resolve(),
    )


def submit_job(
    paths: StageWorkerPaths,
    *,
    code_revision: str,
    kind: str = STAGE_HARNESS_JOB,
) -> dict[str, Any]:
    paths.ensure()
    if not _REVISION.fullmatch(code_revision):
        raise StageWorkerError("submit requires exact 40-character Git revision")
    spec = _JOB_SPECS.get(kind)
    if spec is None:
        raise StageWorkerError("submit kind is not admitted")
    prepare_retry_of: StageJob | None = None
    positive_instability_quantitative_d1_retry_of: StageJob | None = None
    positive_instability_quantitative_d1_recovery_of: StageJob | None = None
    orderly_consolidation_joint_d1_recovery_of: StageJob | None = None
    development_resource_plan_recovery_of: StageJob | None = None
    complete_reserve_additional_review_set_recovery_of: StageJob | None = None
    stratum_normalized_instability_recovery_of: StageJob | None = None
    if kind == STAGE_ORDER_RULE_PREPARE_JOB:
        existing, prepare_retry_of = _existing_order_rule_prepare_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_ORDER_RULE_RESOLVE_SCORE_JOB:
        existing = _existing_order_rule_resolve_score_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_DIRECTIONAL_BOUNDARY_D1_JOB:
        existing = _existing_directional_boundary_d1_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_DIRECTIONAL_BOUNDARY_D3_JOB:
        existing = _existing_directional_boundary_d3_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_POSITIVE_INSTABILITY_D1_JOB:
        existing = _existing_positive_instability_d1_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB:
        (
            existing,
            positive_instability_quantitative_d1_retry_of,
            positive_instability_quantitative_d1_recovery_of,
        ) = _existing_positive_instability_quantitative_d1_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB:
        (
            existing,
            orderly_consolidation_joint_d1_recovery_of,
        ) = _existing_orderly_consolidation_joint_d1_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_ATOMIC_PROPOSITION_G1_JOB:
        existing = _existing_atomic_proposition_g1_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB:
        existing = _existing_development_resource_census_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB:
        existing, development_resource_plan_recovery_of = (
            _existing_development_resource_plan_submission(
                paths, code_revision=code_revision,
            )
        )
        if existing is not None:
            return existing
    if kind == STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB:
        existing = _existing_atomic_proposition_successor_d1_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB:
        existing = _existing_atomic_proposition_admission_qualified_d1_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB:
        existing = _existing_development_resource_admission_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB:
        existing = _existing_development_resource_qualified_plan_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB:
        existing = _existing_complete_reserve_review_readiness_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB:
        existing, complete_reserve_additional_review_set_recovery_of = (
            _existing_complete_reserve_additional_review_set_submission(
                paths, code_revision=code_revision,
            )
        )
        if existing is not None:
            return existing
    if kind == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB:
        existing = _existing_complete_reserve_additional_review_prepare_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_COMBINED_ATOMIC_SCORING_JOB:
        existing = _existing_combined_atomic_scoring_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB:
        existing, stratum_normalized_instability_recovery_of = (
            _existing_stratum_normalized_instability_submission(
                paths, code_revision=code_revision,
            )
        )
        if existing is not None:
            return existing
    if kind == STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB:
        existing = _existing_instability_channel_decomposition_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    if kind == STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB:
        existing = _existing_downside_lifecycle_resource_plan_submission(
            paths, code_revision=code_revision,
        )
        if existing is not None:
            return existing
    job = StageJob(
        job_id="stage_" + uuid.uuid4().hex,
        kind=kind,
        code_revision=code_revision,
        config=spec["config"],
        submitted_at=_utc_now(),
    )
    path = paths.queue / f"{job.job_id}.json"
    qualification_reserved = job.kind == STAGE_QUALIFICATION_JOB
    prepare_reserved = job.kind == STAGE_ORDER_RULE_PREPARE_JOB and prepare_retry_of is None
    prepare_retry_reserved = (
        job.kind == STAGE_ORDER_RULE_PREPARE_JOB and prepare_retry_of is not None
    )
    resolve_score_reserved = job.kind == STAGE_ORDER_RULE_RESOLVE_SCORE_JOB
    directional_d1_reserved = job.kind == STAGE_DIRECTIONAL_BOUNDARY_D1_JOB
    directional_d3_reserved = job.kind == STAGE_DIRECTIONAL_BOUNDARY_D3_JOB
    positive_instability_d1_reserved = job.kind == STAGE_POSITIVE_INSTABILITY_D1_JOB
    positive_instability_quantitative_d1_reserved = (
        job.kind == STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB
        and positive_instability_quantitative_d1_retry_of is None
        and positive_instability_quantitative_d1_recovery_of is None
    )
    positive_instability_quantitative_d1_retry_reserved = (
        job.kind == STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB
        and positive_instability_quantitative_d1_retry_of is not None
    )
    positive_instability_quantitative_d1_recovery_reserved = (
        job.kind == STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB
        and positive_instability_quantitative_d1_recovery_of is not None
    )
    orderly_consolidation_joint_d1_reserved = (
        job.kind == STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB
        and orderly_consolidation_joint_d1_recovery_of is None
    )
    orderly_consolidation_joint_d1_recovery_reserved = (
        job.kind == STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB
        and orderly_consolidation_joint_d1_recovery_of is not None
    )
    atomic_proposition_g1_reserved = job.kind == STAGE_ATOMIC_PROPOSITION_G1_JOB
    development_resource_census_reserved = job.kind == STAGE_DEVELOPMENT_RESOURCE_CENSUS_JOB
    development_resource_plan_reserved = (
        job.kind == STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB
        and development_resource_plan_recovery_of is None
    )
    development_resource_plan_recovery_reserved = (
        job.kind == STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB
        and development_resource_plan_recovery_of is not None
    )
    atomic_proposition_successor_d1_reserved = (
        job.kind == STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB
    )
    atomic_proposition_admission_qualified_d1_reserved = (
        job.kind == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB
    )
    development_resource_admission_reserved = (
        job.kind == STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB
    )
    development_resource_qualified_plan_reserved = (
        job.kind == STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB
    )
    complete_reserve_review_readiness_reserved = (
        job.kind == STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB
    )
    complete_reserve_additional_review_set_reserved = (
        job.kind == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB
        and complete_reserve_additional_review_set_recovery_of is None
    )
    complete_reserve_additional_review_set_recovery_reserved = (
        job.kind == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB
        and complete_reserve_additional_review_set_recovery_of is not None
    )
    complete_reserve_additional_review_prepare_reserved = (
        job.kind == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB
    )
    combined_atomic_scoring_reserved = job.kind == STAGE_COMBINED_ATOMIC_SCORING_JOB
    stratum_normalized_instability_reserved = (
        job.kind == STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB
        and stratum_normalized_instability_recovery_of is None
    )
    stratum_normalized_instability_recovery_reserved = (
        job.kind == STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB
        and stratum_normalized_instability_recovery_of is not None
    )
    instability_channel_decomposition_reserved = (
        job.kind == STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB
    )
    downside_lifecycle_resource_plan_reserved = (
        job.kind == STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB
    )
    if qualification_reserved:
        _reserve_qualification_budget(paths, job)
    if prepare_reserved:
        _exclusive_json(paths.order_rule_prepare_budget, _order_rule_prepare_budget_value(job))
    if prepare_retry_reserved:
        _exclusive_json(
            paths.order_rule_prepare_retry_budget,
            _order_rule_prepare_retry_budget_value(prepare_retry_of, job),
        )
    if resolve_score_reserved:
        _exclusive_json(
            paths.order_rule_resolve_score_budget,
            _order_rule_resolve_score_budget_value(job),
        )
    if directional_d1_reserved:
        _exclusive_json(
            paths.directional_boundary_d1_budget,
            _directional_boundary_d1_budget_value(job),
        )
    if directional_d3_reserved:
        _exclusive_json(
            paths.directional_boundary_d3_budget,
            _directional_boundary_d3_budget_value(job),
        )
    if positive_instability_d1_reserved:
        _exclusive_json(
            paths.positive_instability_d1_budget,
            _positive_instability_d1_budget_value(job),
        )
    if positive_instability_quantitative_d1_reserved:
        _exclusive_json(
            paths.positive_instability_quantitative_d1_budget,
            _positive_instability_quantitative_d1_budget_value(job),
        )
    if positive_instability_quantitative_d1_retry_reserved:
        _exclusive_json(
            paths.positive_instability_quantitative_d1_retry_budget,
            _positive_instability_quantitative_d1_retry_budget_value(
                positive_instability_quantitative_d1_retry_of, job
            ),
        )
    if positive_instability_quantitative_d1_recovery_reserved:
        _exclusive_json(
            paths.positive_instability_quantitative_d1_recovery_budget,
            _positive_instability_quantitative_d1_recovery_budget_value(
                positive_instability_quantitative_d1_recovery_of, job
            ),
        )
    if orderly_consolidation_joint_d1_reserved:
        _exclusive_json(
            paths.orderly_consolidation_joint_d1_budget,
            _orderly_consolidation_joint_d1_budget_value(job),
        )
    if orderly_consolidation_joint_d1_recovery_reserved:
        _exclusive_json(
            paths.orderly_consolidation_joint_d1_recovery_budget,
            _orderly_consolidation_joint_d1_recovery_budget_value(
                orderly_consolidation_joint_d1_recovery_of, job
            ),
        )
    if atomic_proposition_g1_reserved:
        _exclusive_json(
            paths.atomic_proposition_g1_budget,
            _atomic_proposition_g1_budget_value(job),
        )
    if development_resource_census_reserved:
        _exclusive_json(
            paths.development_resource_census_budget,
            _development_resource_census_budget_value(job),
        )
    if development_resource_plan_reserved:
        _exclusive_json(
            paths.development_resource_plan_budget,
            _development_resource_plan_budget_value(job),
        )
    if development_resource_plan_recovery_reserved:
        _exclusive_json(
            paths.development_resource_plan_recovery_budget,
            _development_resource_plan_recovery_budget_value(
                development_resource_plan_recovery_of, job
            ),
        )
    if atomic_proposition_successor_d1_reserved:
        _exclusive_json(
            paths.atomic_proposition_successor_d1_budget,
            _atomic_proposition_successor_d1_budget_value(job),
        )
    if atomic_proposition_admission_qualified_d1_reserved:
        _exclusive_json(
            paths.atomic_proposition_admission_qualified_d1_budget,
            _atomic_proposition_admission_qualified_d1_budget_value(job),
        )
    if development_resource_admission_reserved:
        _exclusive_json(
            paths.development_resource_admission_budget,
            _development_resource_admission_budget_value(job),
        )
    if development_resource_qualified_plan_reserved:
        _exclusive_json(
            paths.development_resource_qualified_plan_budget,
            _development_resource_qualified_plan_budget_value(job),
        )
    if complete_reserve_review_readiness_reserved:
        _exclusive_json(
            paths.complete_reserve_review_readiness_budget,
            _complete_reserve_review_readiness_budget_value(job),
        )
    if complete_reserve_additional_review_set_reserved:
        _exclusive_json(
            paths.complete_reserve_additional_review_set_budget,
            _complete_reserve_additional_review_set_budget_value(job),
        )
    if complete_reserve_additional_review_set_recovery_reserved:
        _exclusive_json(
            paths.complete_reserve_additional_review_set_recovery_budget,
            _complete_reserve_additional_review_set_recovery_budget_value(
                complete_reserve_additional_review_set_recovery_of, job
            ),
        )
    if complete_reserve_additional_review_prepare_reserved:
        _exclusive_json(
            paths.complete_reserve_additional_review_prepare_budget,
            _complete_reserve_additional_review_prepare_budget_value(job),
        )
    if combined_atomic_scoring_reserved:
        _exclusive_json(
            paths.combined_atomic_scoring_budget,
            _combined_atomic_scoring_budget_value(job),
        )
    if stratum_normalized_instability_reserved:
        _exclusive_json(
            paths.stratum_normalized_instability_budget,
            _stratum_normalized_instability_budget_value(job),
        )
    if stratum_normalized_instability_recovery_reserved:
        _exclusive_json(
            paths.stratum_normalized_instability_recovery_budget,
            _stratum_normalized_instability_recovery_budget_value(
                stratum_normalized_instability_recovery_of, job
            ),
        )
    if instability_channel_decomposition_reserved:
        _exclusive_json(
            paths.instability_channel_decomposition_budget,
            _instability_channel_decomposition_budget_value(job),
        )
    if downside_lifecycle_resource_plan_reserved:
        _exclusive_json(
            paths.downside_lifecycle_resource_plan_budget,
            _downside_lifecycle_resource_plan_budget_value(job),
        )
    try:
        with path.open("xb") as stream:
            payload = json.dumps(job.to_dict(), sort_keys=True, indent=2).encode("utf-8") + b"\n"
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        if qualification_reserved:
            with suppress(StageWorkerError):
                _release_qualification_reservation(paths, job)
        if prepare_reserved:
            paths.order_rule_prepare_budget.unlink(missing_ok=True)
        if prepare_retry_reserved:
            paths.order_rule_prepare_retry_budget.unlink(missing_ok=True)
        if resolve_score_reserved:
            paths.order_rule_resolve_score_budget.unlink(missing_ok=True)
        if directional_d1_reserved:
            paths.directional_boundary_d1_budget.unlink(missing_ok=True)
        if directional_d3_reserved:
            paths.directional_boundary_d3_budget.unlink(missing_ok=True)
        if positive_instability_d1_reserved:
            paths.positive_instability_d1_budget.unlink(missing_ok=True)
        if positive_instability_quantitative_d1_reserved:
            paths.positive_instability_quantitative_d1_budget.unlink(missing_ok=True)
        if positive_instability_quantitative_d1_retry_reserved:
            paths.positive_instability_quantitative_d1_retry_budget.unlink(missing_ok=True)
        if positive_instability_quantitative_d1_recovery_reserved:
            paths.positive_instability_quantitative_d1_recovery_budget.unlink(missing_ok=True)
        if orderly_consolidation_joint_d1_reserved:
            paths.orderly_consolidation_joint_d1_budget.unlink(missing_ok=True)
        if orderly_consolidation_joint_d1_recovery_reserved:
            paths.orderly_consolidation_joint_d1_recovery_budget.unlink(missing_ok=True)
        if atomic_proposition_g1_reserved:
            paths.atomic_proposition_g1_budget.unlink(missing_ok=True)
        if development_resource_census_reserved:
            paths.development_resource_census_budget.unlink(missing_ok=True)
        if development_resource_plan_reserved:
            paths.development_resource_plan_budget.unlink(missing_ok=True)
        if development_resource_plan_recovery_reserved:
            paths.development_resource_plan_recovery_budget.unlink(missing_ok=True)
        if atomic_proposition_successor_d1_reserved:
            paths.atomic_proposition_successor_d1_budget.unlink(missing_ok=True)
        if atomic_proposition_admission_qualified_d1_reserved:
            paths.atomic_proposition_admission_qualified_d1_budget.unlink(missing_ok=True)
        if development_resource_admission_reserved:
            paths.development_resource_admission_budget.unlink(missing_ok=True)
        if development_resource_qualified_plan_reserved:
            paths.development_resource_qualified_plan_budget.unlink(missing_ok=True)
        if complete_reserve_review_readiness_reserved:
            paths.complete_reserve_review_readiness_budget.unlink(missing_ok=True)
        if complete_reserve_additional_review_set_reserved:
            paths.complete_reserve_additional_review_set_budget.unlink(missing_ok=True)
        if complete_reserve_additional_review_set_recovery_reserved:
            paths.complete_reserve_additional_review_set_recovery_budget.unlink(missing_ok=True)
        if complete_reserve_additional_review_prepare_reserved:
            paths.complete_reserve_additional_review_prepare_budget.unlink(missing_ok=True)
        if combined_atomic_scoring_reserved:
            paths.combined_atomic_scoring_budget.unlink(missing_ok=True)
        if stratum_normalized_instability_reserved:
            paths.stratum_normalized_instability_budget.unlink(missing_ok=True)
        if stratum_normalized_instability_recovery_reserved:
            paths.stratum_normalized_instability_recovery_budget.unlink(missing_ok=True)
        if instability_channel_decomposition_reserved:
            paths.instability_channel_decomposition_budget.unlink(missing_ok=True)
        if downside_lifecycle_resource_plan_reserved:
            paths.downside_lifecycle_resource_plan_budget.unlink(missing_ok=True)
        raise StageWorkerError("generated duplicate stage job id") from exc
    except Exception:
        if qualification_reserved:
            with suppress(StageWorkerError):
                _release_qualification_reservation(paths, job)
        if prepare_reserved:
            paths.order_rule_prepare_budget.unlink(missing_ok=True)
        if prepare_retry_reserved:
            paths.order_rule_prepare_retry_budget.unlink(missing_ok=True)
        if resolve_score_reserved:
            paths.order_rule_resolve_score_budget.unlink(missing_ok=True)
        if directional_d1_reserved:
            paths.directional_boundary_d1_budget.unlink(missing_ok=True)
        if directional_d3_reserved:
            paths.directional_boundary_d3_budget.unlink(missing_ok=True)
        if positive_instability_d1_reserved:
            paths.positive_instability_d1_budget.unlink(missing_ok=True)
        if positive_instability_quantitative_d1_reserved:
            paths.positive_instability_quantitative_d1_budget.unlink(missing_ok=True)
        if positive_instability_quantitative_d1_retry_reserved:
            paths.positive_instability_quantitative_d1_retry_budget.unlink(missing_ok=True)
        if positive_instability_quantitative_d1_recovery_reserved:
            paths.positive_instability_quantitative_d1_recovery_budget.unlink(missing_ok=True)
        if orderly_consolidation_joint_d1_reserved:
            paths.orderly_consolidation_joint_d1_budget.unlink(missing_ok=True)
        if orderly_consolidation_joint_d1_recovery_reserved:
            paths.orderly_consolidation_joint_d1_recovery_budget.unlink(missing_ok=True)
        if atomic_proposition_g1_reserved:
            paths.atomic_proposition_g1_budget.unlink(missing_ok=True)
        if development_resource_census_reserved:
            paths.development_resource_census_budget.unlink(missing_ok=True)
        if development_resource_plan_reserved:
            paths.development_resource_plan_budget.unlink(missing_ok=True)
        if development_resource_plan_recovery_reserved:
            paths.development_resource_plan_recovery_budget.unlink(missing_ok=True)
        if atomic_proposition_successor_d1_reserved:
            paths.atomic_proposition_successor_d1_budget.unlink(missing_ok=True)
        if atomic_proposition_admission_qualified_d1_reserved:
            paths.atomic_proposition_admission_qualified_d1_budget.unlink(missing_ok=True)
        if development_resource_admission_reserved:
            paths.development_resource_admission_budget.unlink(missing_ok=True)
        if development_resource_qualified_plan_reserved:
            paths.development_resource_qualified_plan_budget.unlink(missing_ok=True)
        if complete_reserve_review_readiness_reserved:
            paths.complete_reserve_review_readiness_budget.unlink(missing_ok=True)
        if complete_reserve_additional_review_set_reserved:
            paths.complete_reserve_additional_review_set_budget.unlink(missing_ok=True)
        if complete_reserve_additional_review_set_recovery_reserved:
            paths.complete_reserve_additional_review_set_recovery_budget.unlink(missing_ok=True)
        if complete_reserve_additional_review_prepare_reserved:
            paths.complete_reserve_additional_review_prepare_budget.unlink(missing_ok=True)
        if combined_atomic_scoring_reserved:
            paths.combined_atomic_scoring_budget.unlink(missing_ok=True)
        if stratum_normalized_instability_reserved:
            paths.stratum_normalized_instability_budget.unlink(missing_ok=True)
        if stratum_normalized_instability_recovery_reserved:
            paths.stratum_normalized_instability_recovery_budget.unlink(missing_ok=True)
        if instability_channel_decomposition_reserved:
            paths.instability_channel_decomposition_budget.unlink(missing_ok=True)
        if downside_lifecycle_resource_plan_reserved:
            paths.downside_lifecycle_resource_plan_budget.unlink(missing_ok=True)
        raise
    result = {**job.to_dict(), "input_digest": job.input_digest, "state": "queued"}
    if prepare_retry_of is not None:
        result["technical_retry_of"] = prepare_retry_of.job_id
    if positive_instability_quantitative_d1_retry_of is not None:
        result["technical_retry_of"] = positive_instability_quantitative_d1_retry_of.job_id
    if positive_instability_quantitative_d1_recovery_of is not None:
        result["transport_recovery_of"] = positive_instability_quantitative_d1_recovery_of.job_id
    if orderly_consolidation_joint_d1_recovery_of is not None:
        result["transport_recovery_of"] = orderly_consolidation_joint_d1_recovery_of.job_id
    if development_resource_plan_recovery_of is not None:
        result["metadata_recovery_of"] = development_resource_plan_recovery_of.job_id
    if complete_reserve_additional_review_set_recovery_of is not None:
        result["technical_recovery_of"] = complete_reserve_additional_review_set_recovery_of.job_id
    if stratum_normalized_instability_recovery_of is not None:
        result["technical_recovery_of"] = stratum_normalized_instability_recovery_of.job_id
    return result


def _bytes_sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


ORDER_RULE_REVIEW_PAGE_SIZE = 8


def _order_rule_review_evidence(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("order-rule review result reference missing")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError("order-rule review artifact outside job root") from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    manifest_sha = _bytes_sha256(manifest_raw)
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("order-rule review manifest binding differs")
    manifest = json.loads(manifest_raw)
    evidence_raw = (artifact_root / "evidence.json").read_bytes()
    evidence_sha = _bytes_sha256(evidence_raw)
    if manifest.get("files", {}).get("evidence.json") != evidence_sha:
        raise StageWorkerError("order-rule review evidence binding differs")
    evidence = json.loads(evidence_raw)
    if (
        not isinstance(evidence.get("source_rows"), dict)
        or not isinstance(evidence.get("cases"), list)
        or not isinstance(evidence.get("protocol", {}).get("profile"), dict)
    ):
        raise StageWorkerError("order-rule review evidence shape differs")
    return manifest_sha, evidence_sha, evidence


def _order_rule_review_alias(parent_job_id: str, page: int) -> str:
    suffix = hashlib.sha256(f"{parent_job_id}|review-page|{page}".encode()).hexdigest()[:32]
    return "stage_" + suffix


def _order_rule_review_index(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_ORDER_RULE_PREPARE_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    manifest_sha, evidence_sha, evidence = _order_rule_review_evidence(paths, observed)
    case_count = len(evidence["cases"])
    pages = (case_count + ORDER_RULE_REVIEW_PAGE_SIZE - 1) // ORDER_RULE_REVIEW_PAGE_SIZE
    return {
        "schema_version": "quanthus.stage-order-review-index.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "case_count": case_count,
        "page_size": ORDER_RULE_REVIEW_PAGE_SIZE,
        "page_count": pages,
        "pages": [
            {
                "page": page,
                "job_id": _order_rule_review_alias(str(observed["job_id"]), page),
                "start": page * ORDER_RULE_REVIEW_PAGE_SIZE,
                "count": min(
                    ORDER_RULE_REVIEW_PAGE_SIZE,
                    case_count - page * ORDER_RULE_REVIEW_PAGE_SIZE,
                ),
            }
            for page in range(pages)
        ],
        "candidate_output_included": False,
        "future_rows_included": False,
    }


def _order_rule_review_page(
    paths: StageWorkerPaths, observed: Mapping[str, Any], page: int,
) -> dict[str, Any]:
    manifest_sha, evidence_sha, evidence = _order_rule_review_evidence(paths, observed)
    source_rows = evidence["source_rows"]
    cases = evidence["cases"]
    profile = evidence["protocol"]["profile"]
    start = page * ORDER_RULE_REVIEW_PAGE_SIZE
    selected = cases[start:start + ORDER_RULE_REVIEW_PAGE_SIZE]
    if not selected:
        raise StageWorkerError("order-rule review page outside denominator")
    compact_cases = []
    for case in selected:
        if not isinstance(case, dict):
            raise StageWorkerError("order-rule review case shape differs")
        case_id = str(case.get("case_id") or "")
        if _digest({key: value for key, value in case.items() if key != "case_id"}) != case_id:
            raise StageWorkerError("order-rule review case digest differs")
        history = []
        for ref in case.get("history_refs", []):
            row = source_rows.get(ref)
            if not isinstance(row, dict) or _digest(row) != ref:
                raise StageWorkerError("order-rule review history binding differs")
            if row.get("market_date") > case.get("market_date"):
                raise StageWorkerError("order-rule review future row")
            history.append([
                ref,
                row.get("market_date"),
                row.get("high"),
                row.get("low"),
                row.get("close"),
                row.get("coordinate_epoch"),
                row.get("reset_reason"),
            ])
        compact_cases.append({
            "case_id": case_id,
            "security_code": case.get("security_code"),
            "security_id": case.get("security_id"),
            "market_date": case.get("market_date"),
            "sampling_stratum": case.get("sampling_stratum"),
            "coordinate_ref": case.get("coordinate_ref"),
            "primary_available": case.get("primary_available"),
            "recent_available": case.get("recent_available"),
            "input_reason": case.get("input_reason"),
            "availability_reasons": case.get("availability_reasons"),
            "primary": case.get("primary"),
            "recent": case.get("recent"),
            "history": history,
        })
    transport = {
        "schema_version": "quanthus.stage-order-review-transport.v1",
        "parent_job_id": observed["job_id"],
        "page": page,
        "page_size": ORDER_RULE_REVIEW_PAGE_SIZE,
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "profile": profile,
        "case_count": len(cases),
        "cases": compact_cases,
        "candidate_output_included": False,
        "future_rows_included": False,
    }
    raw = json.dumps(
        transport, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    if len(encoded) > 180 * 1024:
        raise StageWorkerError("order-rule review page exceeds status budget")
    return {
        "schema_version": "quanthus.stage-order-review-transport-envelope.v1",
        "encoding": "gzip+base64",
        "raw_sha256": _bytes_sha256(raw),
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "encoded_bytes": len(encoded),
        "payload": encoded,
    }


def _order_rule_virtual_review_status(
    paths: StageWorkerPaths, requested_job_id: str,
) -> dict[str, Any] | None:
    for status_path in sorted(paths.status.glob("stage_*.json")):
        observed = _object(status_path, "stage job status")
        if (
            observed.get("kind") != STAGE_ORDER_RULE_PREPARE_JOB
            or observed.get("state") != "succeeded"
            or not isinstance(observed.get("result_ref"), Mapping)
        ):
            continue
        result = observed["result_ref"].get("order_rule_prepare")
        if not isinstance(result, Mapping):
            continue
        case_count = int(result.get("selected_case_count") or 0)
        pages = (case_count + ORDER_RULE_REVIEW_PAGE_SIZE - 1) // ORDER_RULE_REVIEW_PAGE_SIZE
        for page in range(pages):
            if _order_rule_review_alias(str(observed["job_id"]), page) != requested_job_id:
                continue
            return {
                "schema_version": STATUS_SCHEMA,
                "job_id": requested_job_id,
                "kind": "quanthus.stage-order-review-page.v1",
                "code_revision": observed.get("code_revision"),
                "input_digest": observed.get("input_digest"),
                "state": "succeeded",
                "stage": "virtual_read_only",
                "submitted_at": observed.get("submitted_at"),
                "started_at": observed.get("started_at"),
                "completed_at": observed.get("completed_at"),
                "parent_job_id": observed["job_id"],
                "page": page,
                "review_transport": _order_rule_review_page(paths, observed, page),
                "error": None,
                "exit_code": 0,
                "updated_at": observed.get("updated_at"),
            }
    return None


DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE = 8


def _directional_boundary_d1_review_evidence(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> tuple[str, str, str, dict[str, Any], dict[str, Any]]:
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("directional boundary D1 result reference missing")
    detail = result_ref.get("directional_boundary_d1")
    if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
        raise StageWorkerError("directional boundary D1 review is not ready")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError("directional boundary D1 artifact outside job root") from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    manifest_sha = _bytes_sha256(manifest_raw)
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("directional boundary D1 manifest binding differs")
    manifest = json.loads(manifest_raw)
    evidence_raw = (artifact_root / "evidence.json").read_bytes()
    path_facts_raw = (artifact_root / "path-facts.json").read_bytes()
    evidence_sha = _bytes_sha256(evidence_raw)
    path_facts_sha = _bytes_sha256(path_facts_raw)
    files = manifest.get("files", {})
    if (
        files.get("evidence.json") != evidence_sha
        or files.get("path-facts.json") != path_facts_sha
    ):
        raise StageWorkerError("directional boundary D1 review file binding differs")
    evidence = json.loads(evidence_raw)
    path_facts = json.loads(path_facts_raw)
    if (
        not isinstance(evidence.get("source_rows"), dict)
        or not isinstance(evidence.get("cases"), list)
        or not isinstance(path_facts.get("cases"), dict)
    ):
        raise StageWorkerError("directional boundary D1 review evidence shape differs")
    if len(evidence["cases"]) != detail.get("calibration_case_count"):
        raise StageWorkerError("directional boundary D1 review denominator differs")
    return manifest_sha, evidence_sha, path_facts_sha, evidence, path_facts


def _directional_boundary_d1_review_alias(parent_job_id: str, page: int) -> str:
    raw = f"{parent_job_id}|directional-boundary-d1-primary-page|{page}".encode()
    return "stage_" + hashlib.sha256(raw).hexdigest()[:32]


def _directional_boundary_d1_review_index(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_DIRECTIONAL_BOUNDARY_D1_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    manifest_sha, evidence_sha, path_facts_sha, evidence, _path_facts = (
        _directional_boundary_d1_review_evidence(paths, observed)
    )
    case_count = len(evidence["cases"])
    pages = (
        case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
    ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
    return {
        "schema_version": "quanthus.stage-directional-boundary-primary-review-index.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "path_facts_file_sha256": path_facts_sha,
        "case_count": case_count,
        "page_size": DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
        "page_count": pages,
        "pages": [
            {
                "page": page,
                "job_id": _directional_boundary_d1_review_alias(str(observed["job_id"]), page),
                "start": page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                "count": min(
                    DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                    case_count - page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                ),
            }
            for page in range(pages)
        ],
        "explicit_recent_derived_facts_included": False,
        "candidate_output_included": False,
        "future_rows_included": False,
    }


def _directional_boundary_d1_review_page(
    paths: StageWorkerPaths, observed: Mapping[str, Any], page: int,
) -> dict[str, Any]:
    manifest_sha, evidence_sha, path_facts_sha, evidence, path_facts = (
        _directional_boundary_d1_review_evidence(paths, observed)
    )
    cases = evidence["cases"]
    source_rows = evidence["source_rows"]
    path_by_case = path_facts["cases"]
    start = page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
    selected = cases[start:start + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE]
    if not selected:
        raise StageWorkerError("directional boundary D1 review page outside denominator")
    compact_cases = []
    for case in selected:
        if not isinstance(case, dict):
            raise StageWorkerError("directional boundary D1 review case shape differs")
        case_id = str(case.get("case_id") or "")
        if _digest({key: value for key, value in case.items() if key != "case_id"}) != case_id:
            raise StageWorkerError("directional boundary D1 review case digest differs")
        directional = path_by_case.get(case_id)
        if not isinstance(directional, dict) or len(directional) != 2:
            raise StageWorkerError("directional boundary D1 path facts missing")
        refs = case.get("history_refs")
        if not isinstance(refs, list) or len(refs) < 21:
            raise StageWorkerError("directional boundary D1 primary history too short")
        history = []
        for ref in refs[-21:]:
            row = source_rows.get(ref)
            if not isinstance(row, dict) or _digest(row) != ref:
                raise StageWorkerError("directional boundary D1 history binding differs")
            if row.get("market_date") > case.get("market_date"):
                raise StageWorkerError("directional boundary D1 future row")
            history.append([
                ref,
                row.get("market_date"),
                row.get("high"),
                row.get("low"),
                row.get("close"),
                row.get("coordinate_epoch"),
                row.get("reset_reason"),
            ])
        compact_cases.append({
            "case_id": case_id,
            "security_code": case.get("security_code"),
            "security_id": case.get("security_id"),
            "market_date": case.get("market_date"),
            "sampling_stratum": case.get("sampling_stratum"),
            "coordinate_ref": case.get("coordinate_ref"),
            "primary_available": case.get("primary_available"),
            "input_reason": case.get("input_reason"),
            "availability_reasons": case.get("availability_reasons"),
            "primary": case.get("primary"),
            "directional_path_facts": directional,
            "history": history,
        })
    transport = {
        "schema_version": "quanthus.stage-directional-boundary-primary-review-transport.v1",
        "parent_job_id": observed["job_id"],
        "page": page,
        "page_size": DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "path_facts_file_sha256": path_facts_sha,
        "case_count": len(cases),
        "cases": compact_cases,
        "explicit_recent_derived_facts_included": False,
        "candidate_output_included": False,
        "future_rows_included": False,
    }
    raw = json.dumps(
        transport, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    if len(encoded) > 180 * 1024:
        raise StageWorkerError("directional boundary D1 review page exceeds status budget")
    return {
        "schema_version": "quanthus.stage-directional-boundary-primary-review-envelope.v1",
        "encoding": "gzip+base64",
        "raw_sha256": _bytes_sha256(raw),
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "encoded_bytes": len(encoded),
        "payload": encoded,
    }


def _directional_boundary_d1_virtual_review_status(
    paths: StageWorkerPaths, requested_job_id: str,
) -> dict[str, Any] | None:
    for status_path in sorted(paths.status.glob("stage_*.json")):
        observed = _object(status_path, "stage job status")
        if (
            observed.get("kind") != STAGE_DIRECTIONAL_BOUNDARY_D1_JOB
            or observed.get("state") != "succeeded"
        ):
            continue
        result_ref = observed.get("result_ref")
        detail = result_ref.get("directional_boundary_d1") if isinstance(
            result_ref, Mapping
        ) else None
        if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
            continue
        case_count = int(detail.get("calibration_case_count") or 0)
        pages = (
            case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
        ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
        for page in range(pages):
            alias = _directional_boundary_d1_review_alias(str(observed["job_id"]), page)
            if alias != requested_job_id:
                continue
            return {
                "schema_version": STATUS_SCHEMA,
                "job_id": requested_job_id,
                "kind": "quanthus.stage-directional-boundary-primary-review-page.v1",
                "code_revision": observed.get("code_revision"),
                "input_digest": observed.get("input_digest"),
                "state": "succeeded",
                "stage": "virtual_read_only",
                "submitted_at": observed.get("submitted_at"),
                "started_at": observed.get("started_at"),
                "completed_at": observed.get("completed_at"),
                "parent_job_id": observed["job_id"],
                "page": page,
                "review_transport": _directional_boundary_d1_review_page(paths, observed, page),
                "error": None,
                "exit_code": 0,
                "updated_at": observed.get("updated_at"),
            }
    return None


def _directional_boundary_d3_review_evidence(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> tuple[str, str, str, dict[str, Any], dict[str, Any]]:
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("directional boundary D3 result reference missing")
    detail = result_ref.get("directional_boundary_d3")
    if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
        raise StageWorkerError("directional boundary D3 review is not ready")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError("directional boundary D3 artifact outside job root") from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    manifest_sha = _bytes_sha256(manifest_raw)
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("directional boundary D3 manifest binding differs")
    manifest = json.loads(manifest_raw)
    evidence_raw = (artifact_root / "evidence.json").read_bytes()
    path_facts_raw = (artifact_root / "path-facts.json").read_bytes()
    evidence_sha = _bytes_sha256(evidence_raw)
    path_facts_sha = _bytes_sha256(path_facts_raw)
    files = manifest.get("files", {})
    if files.get("evidence.json") != evidence_sha or files.get("path-facts.json") != path_facts_sha:
        raise StageWorkerError("directional boundary D3 review file binding differs")
    evidence = json.loads(evidence_raw)
    path_facts = json.loads(path_facts_raw)
    if (
        not isinstance(evidence.get("source_rows"), dict)
        or not isinstance(evidence.get("cases"), list)
        or not isinstance(path_facts.get("cases"), dict)
    ):
        raise StageWorkerError("directional boundary D3 review evidence shape differs")
    if len(evidence["cases"]) != detail.get("evaluation_selected_case_count"):
        raise StageWorkerError("directional boundary D3 review denominator differs")
    return manifest_sha, evidence_sha, path_facts_sha, evidence, path_facts


def _directional_boundary_d3_review_alias(parent_job_id: str, page: int) -> str:
    raw = f"{parent_job_id}|directional-boundary-d3-primary-page|{page}".encode()
    return "stage_" + hashlib.sha256(raw).hexdigest()[:32]


def _directional_boundary_d3_review_index(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_DIRECTIONAL_BOUNDARY_D3_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    manifest_sha, evidence_sha, path_facts_sha, evidence, _path_facts = (
        _directional_boundary_d3_review_evidence(paths, observed)
    )
    case_count = len(evidence["cases"])
    pages = (
        case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
    ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
    return {
        "schema_version": "quanthus.stage-directional-boundary-primary-review-index.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "path_facts_file_sha256": path_facts_sha,
        "case_count": case_count,
        "page_size": DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
        "page_count": pages,
        "pages": [
            {
                "page": page,
                "job_id": _directional_boundary_d3_review_alias(str(observed["job_id"]), page),
                "start": page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                "count": min(
                    DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                    case_count - page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                ),
            }
            for page in range(pages)
        ],
        "explicit_recent_derived_facts_included": False,
        "candidate_output_included": False,
        "future_rows_included": False,
    }


def _directional_boundary_d3_review_page(
    paths: StageWorkerPaths, observed: Mapping[str, Any], page: int,
) -> dict[str, Any]:
    manifest_sha, evidence_sha, path_facts_sha, evidence, path_facts = (
        _directional_boundary_d3_review_evidence(paths, observed)
    )
    cases = evidence["cases"]
    source_rows = evidence["source_rows"]
    path_by_case = path_facts["cases"]
    start = page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
    selected = cases[start:start + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE]
    if not selected:
        raise StageWorkerError("directional boundary D3 review page outside denominator")
    compact_cases = []
    for case in selected:
        if not isinstance(case, dict):
            raise StageWorkerError("directional boundary D3 review case shape differs")
        case_id = str(case.get("case_id") or "")
        if _digest({key: value for key, value in case.items() if key != "case_id"}) != case_id:
            raise StageWorkerError("directional boundary D3 review case digest differs")
        directional = path_by_case.get(case_id)
        if not isinstance(directional, dict) or len(directional) != 2:
            raise StageWorkerError("directional boundary D3 path facts missing")
        refs = case.get("history_refs")
        if not isinstance(refs, list) or len(refs) < 21:
            raise StageWorkerError("directional boundary D3 primary history too short")
        history = []
        for ref in refs[-21:]:
            row = source_rows.get(ref)
            if not isinstance(row, dict) or _digest(row) != ref:
                raise StageWorkerError("directional boundary D3 history binding differs")
            if row.get("market_date") > case.get("market_date"):
                raise StageWorkerError("directional boundary D3 future row")
            history.append([
                ref, row.get("market_date"), row.get("high"), row.get("low"), row.get("close"),
                row.get("coordinate_epoch"), row.get("reset_reason"),
            ])
        compact_cases.append({
            "case_id": case_id,
            "security_code": case.get("security_code"),
            "security_id": case.get("security_id"),
            "market_date": case.get("market_date"),
            "sampling_stratum": case.get("sampling_stratum"),
            "coordinate_ref": case.get("coordinate_ref"),
            "primary_available": case.get("primary_available"),
            "input_reason": case.get("input_reason"),
            "availability_reasons": case.get("availability_reasons"),
            "primary": case.get("primary"),
            "directional_path_facts": directional,
            "history": history,
        })
    transport = {
        "schema_version": "quanthus.stage-directional-boundary-primary-review-transport.v1",
        "parent_job_id": observed["job_id"],
        "page": page,
        "page_size": DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "path_facts_file_sha256": path_facts_sha,
        "case_count": len(cases),
        "cases": compact_cases,
        "explicit_recent_derived_facts_included": False,
        "candidate_output_included": False,
        "future_rows_included": False,
    }
    raw = json.dumps(
        transport, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    if len(encoded) > 180 * 1024:
        raise StageWorkerError("directional boundary D3 review page exceeds status budget")
    return {
        "schema_version": "quanthus.stage-directional-boundary-primary-review-envelope.v1",
        "encoding": "gzip+base64",
        "raw_sha256": _bytes_sha256(raw),
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "encoded_bytes": len(encoded),
        "payload": encoded,
    }


def _directional_boundary_d3_virtual_review_status(
    paths: StageWorkerPaths, requested_job_id: str,
) -> dict[str, Any] | None:
    for status_path in sorted(paths.status.glob("stage_*.json")):
        observed = _object(status_path, "stage job status")
        if (
            observed.get("kind") != STAGE_DIRECTIONAL_BOUNDARY_D3_JOB
            or observed.get("state") != "succeeded"
        ):
            continue
        result_ref = observed.get("result_ref")
        detail = (
            result_ref.get("directional_boundary_d3")
            if isinstance(result_ref, Mapping)
            else None
        )
        if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
            continue
        case_count = int(detail.get("evaluation_selected_case_count") or 0)
        pages = (
            case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
        ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
        for page in range(pages):
            alias = _directional_boundary_d3_review_alias(str(observed["job_id"]), page)
            if alias != requested_job_id:
                continue
            return {
                "schema_version": STATUS_SCHEMA,
                "job_id": requested_job_id,
                "kind": "quanthus.stage-directional-boundary-primary-review-page.v1",
                "code_revision": observed.get("code_revision"),
                "input_digest": observed.get("input_digest"),
                "state": "succeeded",
                "stage": "virtual_read_only",
                "submitted_at": observed.get("submitted_at"),
                "started_at": observed.get("started_at"),
                "completed_at": observed.get("completed_at"),
                "parent_job_id": observed["job_id"],
                "page": page,
                "review_transport": _directional_boundary_d3_review_page(paths, observed, page),
                "error": None,
                "exit_code": 0,
                "updated_at": observed.get("updated_at"),
            }
    return None


def _positive_instability_d1_review_evidence(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> tuple[str, str, str, dict[str, Any], dict[str, Any]]:
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("positive instability D1 result reference missing")
    if observed.get("kind") == STAGE_POSITIVE_INSTABILITY_D1_JOB:
        detail = result_ref.get("positive_instability_d1")
    elif observed.get("kind") == STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB:
        detail = result_ref.get("positive_instability_quantitative_d1")
    elif observed.get("kind") == STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB:
        detail = result_ref.get("orderly_consolidation_joint_d1")
    elif observed.get("kind") == STAGE_ATOMIC_PROPOSITION_G1_JOB:
        detail = result_ref.get("atomic_proposition_g1")
    elif observed.get("kind") == STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB:
        detail = result_ref.get("atomic_proposition_successor_d1")
    elif observed.get("kind") == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB:
        detail = result_ref.get("atomic_proposition_admission_qualified_d1")
    elif observed.get("kind") == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB:
        detail = result_ref.get("complete_reserve_additional_review_prepare")
    else:
        detail = None
    if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
        raise StageWorkerError("positive instability D1 review is not ready")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError("positive instability D1 artifact outside job root") from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    manifest_sha = _bytes_sha256(manifest_raw)
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("positive instability D1 manifest binding differs")
    manifest = json.loads(manifest_raw)
    evidence_raw = (artifact_root / "evidence.json").read_bytes()
    path_facts_raw = (artifact_root / "path-facts.json").read_bytes()
    evidence_sha = _bytes_sha256(evidence_raw)
    path_facts_sha = _bytes_sha256(path_facts_raw)
    files = manifest.get("files", {})
    if (
        files.get("evidence.json") != evidence_sha
        or files.get("path-facts.json") != path_facts_sha
    ):
        raise StageWorkerError("positive instability D1 review file binding differs")
    evidence = json.loads(evidence_raw)
    path_facts = json.loads(path_facts_raw)
    if (
        not isinstance(evidence.get("source_rows"), dict)
        or not isinstance(evidence.get("cases"), list)
        or not isinstance(path_facts.get("cases"), dict)
    ):
        raise StageWorkerError("positive instability D1 review evidence shape differs")
    if len(evidence["cases"]) != detail.get("development_selected_case_count"):
        raise StageWorkerError("positive instability D1 review denominator differs")
    return manifest_sha, evidence_sha, path_facts_sha, evidence, path_facts


def _positive_instability_d1_review_alias(parent_job_id: str, page: int) -> str:
    raw = f"{parent_job_id}|positive-instability-d1-primary-page|{page}".encode()
    return "stage_" + hashlib.sha256(raw).hexdigest()[:32]


def _positive_instability_d1_review_index(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_POSITIVE_INSTABILITY_D1_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    manifest_sha, evidence_sha, path_facts_sha, evidence, _path_facts = (
        _positive_instability_d1_review_evidence(paths, observed)
    )
    case_count = len(evidence["cases"])
    pages = (
        case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
    ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
    return {
        "schema_version": "quanthus.stage-positive-instability-primary-review-index.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "path_facts_file_sha256": path_facts_sha,
        "case_count": case_count,
        "page_size": DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
        "page_count": pages,
        "pages": [
            {
                "page": page,
                "job_id": _positive_instability_d1_review_alias(
                    str(observed["job_id"]), page
                ),
                "start": page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                "count": min(
                    DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                    case_count - page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                ),
            }
            for page in range(pages)
        ],
        "threshold_basis_included": False,
        "candidate_output_included": False,
        "explicit_recent_derived_facts_included": False,
        "future_rows_included": False,
    }


def _positive_instability_d1_review_page(
    paths: StageWorkerPaths, observed: Mapping[str, Any], page: int,
) -> dict[str, Any]:
    manifest_sha, evidence_sha, path_facts_sha, evidence, path_facts = (
        _positive_instability_d1_review_evidence(paths, observed)
    )
    cases = evidence["cases"]
    source_rows = evidence["source_rows"]
    path_by_case = path_facts["cases"]
    start = page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
    selected = cases[start:start + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE]
    if not selected:
        raise StageWorkerError("positive instability D1 review page outside denominator")
    compact_cases = []
    for case in selected:
        if not isinstance(case, dict):
            raise StageWorkerError("positive instability D1 review case shape differs")
        case_id = str(case.get("case_id") or "")
        if _digest({key: value for key, value in case.items() if key != "case_id"}) != case_id:
            raise StageWorkerError("positive instability D1 review case digest differs")
        directional = path_by_case.get(case_id)
        if not isinstance(directional, dict) or len(directional) != 2:
            raise StageWorkerError("positive instability D1 path facts missing")
        refs = case.get("history_refs")
        if not isinstance(refs, list) or len(refs) < 21:
            raise StageWorkerError("positive instability D1 primary history too short")
        history = []
        for ref in refs[-21:]:
            row = source_rows.get(ref)
            if not isinstance(row, dict) or _digest(row) != ref:
                raise StageWorkerError("positive instability D1 history binding differs")
            if row.get("market_date") > case.get("market_date"):
                raise StageWorkerError("positive instability D1 future row")
            history.append([
                ref,
                row.get("market_date"),
                row.get("high"),
                row.get("low"),
                row.get("close"),
                row.get("coordinate_epoch"),
                row.get("reset_reason"),
            ])
        compact_cases.append({
            "case_id": case_id,
            "security_code": case.get("security_code"),
            "security_id": case.get("security_id"),
            "market_date": case.get("market_date"),
            "sampling_stratum": case.get("sampling_stratum"),
            "coordinate_ref": case.get("coordinate_ref"),
            "primary_available": case.get("primary_available"),
            "input_reason": case.get("input_reason"),
            "availability_reasons": case.get("availability_reasons"),
            "primary": case.get("primary"),
            "directional_path_facts": directional,
            "history": history,
        })
    transport = {
        "schema_version": "quanthus.stage-positive-instability-primary-review-transport.v1",
        "parent_job_id": observed["job_id"],
        "page": page,
        "page_size": DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "path_facts_file_sha256": path_facts_sha,
        "case_count": len(cases),
        "cases": compact_cases,
        "threshold_basis_included": False,
        "candidate_output_included": False,
        "explicit_recent_derived_facts_included": False,
        "future_rows_included": False,
    }
    raw = json.dumps(
        transport, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    if len(encoded) > 180 * 1024:
        raise StageWorkerError("positive instability D1 review page exceeds status budget")
    return {
        "schema_version": "quanthus.stage-positive-instability-primary-review-envelope.v1",
        "encoding": "gzip+base64",
        "raw_sha256": _bytes_sha256(raw),
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "encoded_bytes": len(encoded),
        "payload": encoded,
    }


def _positive_instability_d1_selection_transport(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_POSITIVE_INSTABILITY_D1_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("positive instability D1 selection result reference missing")
    detail = result_ref.get("positive_instability_d1")
    if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
        raise StageWorkerError("positive instability D1 selection is not ready")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError(
            "positive instability D1 selection artifact outside job root"
        ) from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    manifest_sha = _bytes_sha256(manifest_raw)
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("positive instability D1 selection manifest binding differs")
    manifest = json.loads(manifest_raw)
    files = manifest.get("files", {})
    payload = {}
    for name in ("threshold-basis.json", "candidate-grid.json", "evaluation-plan.json"):
        raw = (artifact_root / name).read_bytes()
        if files.get(name) != _bytes_sha256(raw):
            raise StageWorkerError("positive instability D1 selection file binding differs")
        payload[name] = json.loads(raw)
    if (
        _digest(payload["threshold-basis.json"]) != detail.get("threshold_basis_digest")
        or _digest(payload["candidate-grid.json"]) != detail.get("candidate_grid_digest")
        or _digest(payload["evaluation-plan.json"]) != detail.get("evaluation_plan_digest")
        or payload["candidate-grid.json"].get("candidate_count") != 27
        or payload["candidate-grid.json"].get("parameter_search_performed") is not False
        or payload["threshold-basis.json"].get("labels_used") is not False
        or payload["evaluation-plan.json"].get("data_read") is not False
    ):
        raise StageWorkerError("positive instability D1 selection payload differs")
    transport = {
        "schema_version": "quanthus.stage-positive-instability-selection-transport.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "threshold_basis": payload["threshold-basis.json"],
        "candidate_grid": payload["candidate-grid.json"],
        "evaluation_plan_binding": {
            "identity_count": payload["evaluation-plan.json"].get("identity_count"),
            "identity_digest": payload["evaluation-plan.json"].get("identity_digest"),
            "data_read": False,
        },
        "review_ledger_must_be_sealed_before_use": True,
        "evaluation_data_read": False,
        "candidate_output_included": False,
        "parameter_search_performed": False,
    }
    raw = json.dumps(
        transport, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    if len(encoded) > 180 * 1024:
        raise StageWorkerError("positive instability D1 selection transport exceeds status budget")
    return {
        "schema_version": "quanthus.stage-positive-instability-selection-envelope.v1",
        "encoding": "gzip+base64",
        "raw_sha256": _bytes_sha256(raw),
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "encoded_bytes": len(encoded),
        "payload": encoded,
    }


def _positive_instability_d1_virtual_review_status(
    paths: StageWorkerPaths, requested_job_id: str,
) -> dict[str, Any] | None:
    for status_path in sorted(paths.status.glob("stage_*.json")):
        observed = _object(status_path, "stage job status")
        if (
            observed.get("kind") != STAGE_POSITIVE_INSTABILITY_D1_JOB
            or observed.get("state") != "succeeded"
        ):
            continue
        result_ref = observed.get("result_ref")
        detail = (
            result_ref.get("positive_instability_d1")
            if isinstance(result_ref, Mapping)
            else None
        )
        if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
            continue
        case_count = int(detail.get("development_selected_case_count") or 0)
        pages = (
            case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
        ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
        for page in range(pages):
            alias = _positive_instability_d1_review_alias(str(observed["job_id"]), page)
            if alias != requested_job_id:
                continue
            return {
                "schema_version": STATUS_SCHEMA,
                "job_id": requested_job_id,
                "kind": "quanthus.stage-positive-instability-primary-review-page.v1",
                "code_revision": observed.get("code_revision"),
                "input_digest": observed.get("input_digest"),
                "state": "succeeded",
                "stage": "virtual_read_only",
                "submitted_at": observed.get("submitted_at"),
                "started_at": observed.get("started_at"),
                "completed_at": observed.get("completed_at"),
                "parent_job_id": observed["job_id"],
                "page": page,
                "review_transport": _positive_instability_d1_review_page(
                    paths, observed, page
                ),
                "error": None,
                "exit_code": 0,
                "updated_at": observed.get("updated_at"),
            }
    return None


def _positive_instability_quantitative_d1_review_alias(
    parent_job_id: str, page: int
) -> str:
    raw = f"{parent_job_id}|positive-instability-quantitative-d1-primary-page|{page}".encode()
    return "stage_" + hashlib.sha256(raw).hexdigest()[:32]


def _positive_instability_quantitative_d1_review_index(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    manifest_sha, evidence_sha, path_facts_sha, evidence, _path_facts = (
        _positive_instability_d1_review_evidence(paths, observed)
    )
    case_count = len(evidence["cases"])
    pages = (
        case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
    ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
    return {
        "schema_version": "quanthus.stage-positive-instability-primary-review-index.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "path_facts_file_sha256": path_facts_sha,
        "case_count": case_count,
        "page_size": DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
        "page_count": pages,
        "pages": [
            {
                "page": page,
                "job_id": _positive_instability_quantitative_d1_review_alias(
                    str(observed["job_id"]), page
                ),
                "start": page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                "count": min(
                    DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                    case_count - page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                ),
            }
            for page in range(pages)
        ],
        "threshold_basis_included": False,
        "candidate_output_included": False,
        "explicit_recent_derived_facts_included": False,
        "future_rows_included": False,
        "preserved_2021_evaluation_included": False,
    }


def _positive_instability_quantitative_d1_virtual_review_status(
    paths: StageWorkerPaths, requested_job_id: str,
) -> dict[str, Any] | None:
    for status_path in sorted(paths.status.glob("stage_*.json")):
        observed = _object(status_path, "stage job status")
        if (
            observed.get("kind") != STAGE_POSITIVE_INSTABILITY_QUANTITATIVE_D1_JOB
            or observed.get("state") != "succeeded"
        ):
            continue
        result_ref = observed.get("result_ref")
        detail = (
            result_ref.get("positive_instability_quantitative_d1")
            if isinstance(result_ref, Mapping)
            else None
        )
        if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
            continue
        case_count = int(detail.get("development_selected_case_count") or 0)
        pages = (
            case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
        ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
        for page in range(pages):
            alias = _positive_instability_quantitative_d1_review_alias(
                str(observed["job_id"]), page
            )
            if alias != requested_job_id:
                continue
            return {
                "schema_version": STATUS_SCHEMA,
                "job_id": requested_job_id,
                "kind": "quanthus.stage-positive-instability-primary-review-page.v1",
                "code_revision": observed.get("code_revision"),
                "input_digest": observed.get("input_digest"),
                "state": "succeeded",
                "stage": "virtual_read_only",
                "submitted_at": observed.get("submitted_at"),
                "started_at": observed.get("started_at"),
                "completed_at": observed.get("completed_at"),
                "parent_job_id": observed["job_id"],
                "page": page,
                "review_transport": _positive_instability_d1_review_page(
                    paths, observed, page
                ),
                "error": None,
                "exit_code": 0,
                "updated_at": observed.get("updated_at"),
            }
    return None


def _orderly_consolidation_joint_d1_review_alias(parent_job_id: str, page: int) -> str:
    raw = f"{parent_job_id}|orderly-consolidation-joint-d1-primary-page|{page}".encode()
    return "stage_" + hashlib.sha256(raw).hexdigest()[:32]


def _orderly_consolidation_joint_d1_review_index(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    manifest_sha, evidence_sha, path_facts_sha, evidence, _path_facts = (
        _positive_instability_d1_review_evidence(paths, observed)
    )
    case_count = len(evidence["cases"])
    pages = (
        case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
    ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
    return {
        "schema_version": "quanthus.stage-orderly-consolidation-joint-primary-review-index.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "path_facts_file_sha256": path_facts_sha,
        "case_count": case_count,
        "page_size": DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
        "page_count": pages,
        "pages": [
            {
                "page": page,
                "job_id": _orderly_consolidation_joint_d1_review_alias(
                    str(observed["job_id"]), page
                ),
                "start": page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                "count": min(
                    DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                    case_count - page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                ),
            }
            for page in range(pages)
        ],
        "threshold_basis_included": False,
        "candidate_output_included": False,
        "explicit_recent_derived_facts_included": False,
        "future_rows_included": False,
        "preserved_2021_evaluation_included": False,
    }


def _orderly_consolidation_joint_d1_selection_transport(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("orderly consolidation joint D1 selection result reference missing")
    detail = result_ref.get("orderly_consolidation_joint_d1")
    if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
        raise StageWorkerError("orderly consolidation joint D1 selection is not ready")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError(
            "orderly consolidation joint D1 selection artifact outside job root"
        ) from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    manifest_sha = _bytes_sha256(manifest_raw)
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("orderly consolidation joint D1 selection manifest binding differs")
    manifest = json.loads(manifest_raw)
    files = manifest.get("files", {})
    payload = {}
    for name in (
        "threshold-basis.json",
        "candidate-grid.json",
        "preserved-evaluation-plan.json",
    ):
        raw = (artifact_root / name).read_bytes()
        if files.get(name) != _bytes_sha256(raw):
            raise StageWorkerError("orderly consolidation joint D1 selection file binding differs")
        payload[name] = json.loads(raw)
    basis = payload["threshold-basis.json"]
    grid = payload["candidate-grid.json"]
    plan = payload["preserved-evaluation-plan.json"]
    if (
        _digest(basis) != detail.get("threshold_basis_digest")
        or _digest(grid) != detail.get("candidate_grid_digest")
        or _digest(plan) != detail.get("preserved_evaluation_plan_digest")
        or grid.get("stage0_candidate_count") != 9
        or grid.get("stage2_candidate_count") != 3
        or grid.get("pair_count") != 27
        or grid.get("parameter_search_performed") is not False
        or grid.get("grid_expanded_after_result") is not False
        or basis.get("labels_used") is not False
        or basis.get("reference_opinions_used") is not False
        or plan.get("data_read") is not False
    ):
        raise StageWorkerError("orderly consolidation joint D1 selection payload differs")
    transport = {
        "schema_version": "quanthus.stage-orderly-consolidation-joint-selection-transport.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "threshold_basis": basis,
        "candidate_grid": grid,
        "evaluation_plan_binding": {
            "identity_count": plan.get("identity_count"),
            "identity_digest": plan.get("identity_digest"),
            "plan_digest": _digest(plan),
            "data_read": False,
        },
        "review_ledger_must_be_sealed_before_use": True,
        "evaluation_data_read": False,
        "parameter_search_performed": False,
        "candidate_grid_expanded": False,
    }
    raw = json.dumps(
        transport, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    if len(encoded) > 180 * 1024:
        raise StageWorkerError(
            "orderly consolidation joint D1 selection transport exceeds status budget"
        )
    return {
        "schema_version": "quanthus.stage-orderly-consolidation-joint-selection-envelope.v1",
        "encoding": "gzip+base64",
        "raw_sha256": _bytes_sha256(raw),
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "encoded_bytes": len(encoded),
        "payload": encoded,
    }


def _orderly_consolidation_joint_d1_virtual_review_status(
    paths: StageWorkerPaths, requested_job_id: str,
) -> dict[str, Any] | None:
    for status_path in sorted(paths.status.glob("stage_*.json")):
        observed = _object(status_path, "stage job status")
        if (
            observed.get("kind") != STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB
            or observed.get("state") != "succeeded"
        ):
            continue
        result_ref = observed.get("result_ref")
        detail = (
            result_ref.get("orderly_consolidation_joint_d1")
            if isinstance(result_ref, Mapping)
            else None
        )
        if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
            continue
        case_count = int(detail.get("development_selected_case_count") or 0)
        pages = (
            case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
        ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
        for page in range(pages):
            alias = _orderly_consolidation_joint_d1_review_alias(
                str(observed["job_id"]), page
            )
            if alias != requested_job_id:
                continue
            return {
                "schema_version": STATUS_SCHEMA,
                "job_id": requested_job_id,
                "kind": "quanthus.stage-orderly-consolidation-joint-primary-review-page.v1",
                "code_revision": observed.get("code_revision"),
                "input_digest": observed.get("input_digest"),
                "state": "succeeded",
                "stage": "virtual_read_only",
                "submitted_at": observed.get("submitted_at"),
                "started_at": observed.get("started_at"),
                "completed_at": observed.get("completed_at"),
                "parent_job_id": observed["job_id"],
                "page": page,
                "review_transport": _positive_instability_d1_review_page(
                    paths, observed, page
                ),
                "error": None,
                "exit_code": 0,
                "updated_at": observed.get("updated_at"),
            }
    return None


def _atomic_proposition_successor_d1_case_strata(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("atomic proposition successor D1 result reference missing")
    if observed.get("kind") == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB:
        detail_key = "atomic_proposition_admission_qualified_d1"
    elif observed.get("kind") == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB:
        detail_key = "complete_reserve_additional_review_prepare"
    else:
        detail_key = "atomic_proposition_successor_d1"
    detail = result_ref.get(detail_key)
    if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
        raise StageWorkerError("atomic proposition successor D1 review is not ready")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError("atomic proposition successor D1 artifact outside job root") from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    manifest_sha = _bytes_sha256(manifest_raw)
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("atomic proposition successor D1 manifest binding differs")
    manifest = json.loads(manifest_raw)
    strata_raw = (artifact_root / "case-strata.json").read_bytes()
    strata_sha = _bytes_sha256(strata_raw)
    if manifest.get("files", {}).get("case-strata.json") != strata_sha:
        raise StageWorkerError("atomic proposition successor D1 strata binding differs")
    strata = json.loads(strata_raw)
    if (
        not isinstance(strata, dict)
        or _digest(strata) != detail.get("case_strata_digest")
        or len(strata) != detail.get("development_selected_case_count")
    ):
        raise StageWorkerError("atomic proposition successor D1 strata payload differs")
    return strata_sha, strata


def _atomic_proposition_successor_d1_review_alias(parent_job_id: str, page: int) -> str:
    raw = f"{parent_job_id}|atomic-proposition-successor-d1-primary-page|{page}".encode()
    return "stage_" + hashlib.sha256(raw).hexdigest()[:32]


def _atomic_proposition_successor_d1_review_index(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") not in {
            STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB,
            STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB,
            STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB,
        }
        or observed.get("state") != "succeeded"
    ):
        return None
    manifest_sha, evidence_sha, path_facts_sha, evidence, _path_facts = (
        _positive_instability_d1_review_evidence(paths, observed)
    )
    strata_sha, strata = _atomic_proposition_successor_d1_case_strata(paths, observed)
    case_count = len(evidence["cases"])
    if len(strata) != case_count:
        raise StageWorkerError("atomic proposition successor D1 strata denominator differs")
    pages = (
        case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
    ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
    if observed.get("kind") == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB:
        index_schema = (
            "quanthus.stage-atomic-proposition-admission-qualified-d1-primary-review-index.v1"
        )
    elif observed.get("kind") == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB:
        index_schema = "quanthus.stage-complete-reserve-additional-primary-review-index.v1"
    else:
        index_schema = "quanthus.stage-atomic-proposition-successor-d1-primary-review-index.v1"
    return {
        "schema_version": index_schema,
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "path_facts_file_sha256": path_facts_sha,
        "case_strata_file_sha256": strata_sha,
        "case_count": case_count,
        "page_size": DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
        "page_count": pages,
        "pages": [
            {
                "page": page,
                "job_id": _atomic_proposition_successor_d1_review_alias(
                    str(observed["job_id"]), page
                ),
                "start": page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                "count": min(
                    DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                    case_count - page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                ),
            }
            for page in range(pages)
        ],
        "era_board_stratum_included": True,
        "threshold_basis_included": False,
        "candidate_output_included": False,
        "envelope_measurement_included": False,
        "explicit_recent_derived_facts_included": False,
        "future_rows_included": False,
        "preserved_2021_evaluation_included": False,
    }


def _atomic_proposition_successor_d1_review_page(
    paths: StageWorkerPaths, observed: Mapping[str, Any], page: int,
) -> dict[str, Any]:
    envelope = _positive_instability_d1_review_page(paths, observed, page)
    raw = gzip.decompress(base64.b64decode(str(envelope["payload"])))
    transport = json.loads(raw)
    if not isinstance(transport, dict) or not isinstance(transport.get("cases"), list):
        raise StageWorkerError("atomic proposition successor D1 review transport shape differs")
    strata_sha, strata = _atomic_proposition_successor_d1_case_strata(paths, observed)
    for case in transport["cases"]:
        case_id = str(case.get("case_id") or "")
        stratum = strata.get(case_id)
        if not isinstance(stratum, Mapping):
            raise StageWorkerError("atomic proposition successor D1 case stratum missing")
        case["resource_stratum"] = {
            "era": stratum.get("era"),
            "board_family": stratum.get("board_family"),
            "cell": stratum.get("cell"),
            "plan_role": stratum.get("plan_role"),
            "window_id": stratum.get("window_id"),
        }
    if observed.get("kind") == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB:
        transport["schema_version"] = (
            "quanthus.stage-atomic-proposition-admission-qualified-d1-primary-review-transport.v1"
        )
    elif observed.get("kind") == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB:
        transport["schema_version"] = (
            "quanthus.stage-complete-reserve-additional-primary-review-transport.v1"
        )
    else:
        transport["schema_version"] = (
            "quanthus.stage-atomic-proposition-successor-d1-primary-review-transport.v1"
        )
    transport["case_strata_file_sha256"] = strata_sha
    transport["era_board_stratum_included"] = True
    transport["threshold_basis_included"] = False
    transport["candidate_output_included"] = False
    transport["envelope_measurement_included"] = False
    transport["future_rows_included"] = False
    transport["preserved_2021_evaluation_included"] = False
    encoded_raw = json.dumps(
        transport,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    compressed = gzip.compress(encoded_raw, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    if len(encoded) > 200 * 1024:
        raise StageWorkerError("atomic proposition successor D1 review page exceeds status budget")
    if observed.get("kind") == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB:
        envelope_schema = (
            "quanthus.stage-atomic-proposition-admission-qualified-d1-primary-review-envelope.v1"
        )
    elif observed.get("kind") == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB:
        envelope_schema = "quanthus.stage-complete-reserve-additional-primary-review-envelope.v1"
    else:
        envelope_schema = (
            "quanthus.stage-atomic-proposition-successor-d1-primary-review-envelope.v1"
        )
    return {
        "schema_version": envelope_schema,
        "encoding": "gzip+base64",
        "raw_sha256": _bytes_sha256(encoded_raw),
        "raw_bytes": len(encoded_raw),
        "compressed_bytes": len(compressed),
        "encoded_bytes": len(encoded),
        "payload": encoded,
    }


_P014_REVIEW_SCHEMA = "quanthus.stage-atomic-proposition-admission-qualified-review-ledger.v1"
_P014_REVIEW_SUBMISSION_SCHEMA = (
    "quanthus.stage-atomic-proposition-admission-qualified-review-submission.v1"
)
_P014_REVIEW_PROPOSITIONS = (
    "structural_instability_20",
    "coherent_directional_relocation_20",
    "orderly_consolidation_20",
)
_P014_REVIEW_TRUTH = {"TRUE", "FALSE", "UNKNOWN"}
_P015_REVIEW_SCHEMA = "quanthus.stage-complete-reserve-additional-review-ledger.v1"
_P015_REVIEW_SUBMISSION_SCHEMA = "quanthus.stage-complete-reserve-additional-review-submission.v1"


def _p014_review_reference_summary(opinions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from quanthus.stage_learning.order_reference_protocol_v2 import project_atomic_primary

    atomic_counts = {name: Counter() for name in _P014_REVIEW_PROPOSITIONS}
    projected = Counter()
    disagreements = Counter({name: 0 for name in _P014_REVIEW_PROPOSITIONS})
    for row in opinions:
        short = row["short_term"]
        fund = row["fund"]
        for proposition in _P014_REVIEW_PROPOSITIONS:
            left = str(short[proposition])
            right = str(fund[proposition])
            reference = left if left == right else "AMBIGUOUS"
            atomic_counts[proposition][reference] += 1
            disagreements[proposition] += int(left != right)
        short_projection = project_atomic_primary(short, available=True)["status"]
        fund_projection = project_atomic_primary(fund, available=True)["status"]
        projected_status = (
            short_projection if short_projection == fund_projection else "AMBIGUOUS"
        )
        projected[str(projected_status)] += 1
    return {
        "atomic_reference_counts": {
            proposition: dict(sorted(counts.items()))
            for proposition, counts in atomic_counts.items()
        },
        "atomic_disagreement_count": dict(disagreements),
        "projected_reference_counts": dict(sorted(projected.items())),
    }


def _validate_p014_review_submission(
    paths: StageWorkerPaths,
    observed: Mapping[str, Any],
    submission: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        observed.get("kind") != STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB
        or observed.get("state") != "succeeded"
    ):
        raise StageWorkerError("P014 review seal requires the successful exact parent job")
    manifest_sha, evidence_sha, _path_sha, evidence, _path_facts = (
        _positive_instability_d1_review_evidence(paths, observed)
    )
    expected_fields = {
        "schema_version",
        "work_item",
        "parent_job_id",
        "source_manifest_sha256",
        "development_evidence_digest",
        "provenance",
        "candidate_output_seen_during_review",
        "threshold_basis_seen_during_review",
        "envelope_measurement_seen_during_review",
        "selection_transport_seen_during_review",
        "future_rows_seen_during_review",
        "preserved_2021_seen_during_review",
        "opinions",
    }
    if set(submission) != expected_fields:
        raise StageWorkerError("P014 review submission fields differ")
    if (
        submission.get("schema_version") != _P014_REVIEW_SUBMISSION_SCHEMA
        or submission.get("work_item") != "P3-STAGE-014-D"
        or submission.get("parent_job_id") != observed.get("job_id")
        or submission.get("source_manifest_sha256") != manifest_sha
        or submission.get("development_evidence_digest") != _digest(evidence)
    ):
        raise StageWorkerError("P014 review submission binding differs")
    if submission.get("development_evidence_digest") != evidence_sha:
        raise StageWorkerError("P014 review evidence digest differs")
    for key in (
        "candidate_output_seen_during_review",
        "threshold_basis_seen_during_review",
        "envelope_measurement_seen_during_review",
        "selection_transport_seen_during_review",
        "future_rows_seen_during_review",
        "preserved_2021_seen_during_review",
    ):
        if submission.get(key) is not False:
            raise StageWorkerError("P014 review blinding declaration differs")
    provenance = submission.get("provenance")
    if (
        not isinstance(provenance, Mapping)
        or set(provenance) != {"kind", "origin_id", "session_id"}
        or provenance.get("kind") != "self_simulated"
        or not str(provenance.get("origin_id") or "").strip()
        or not str(provenance.get("session_id") or "").strip()
    ):
        raise StageWorkerError("P014 review provenance differs")
    opinions = submission.get("opinions")
    cases = evidence.get("cases")
    if not isinstance(opinions, list) or not isinstance(cases, list) or len(cases) != 192:
        raise StageWorkerError("P014 review denominator differs")
    if len(opinions) != len(cases):
        raise StageWorkerError("P014 review opinion count differs")
    normalized: list[dict[str, Any]] = []
    for index, (raw, case) in enumerate(zip(opinions, cases, strict=True)):
        if not isinstance(raw, Mapping) or not isinstance(case, Mapping):
            raise StageWorkerError("P014 review row shape differs")
        if set(raw) != {
            "index",
            "case_id",
            "short_term",
            "fund",
            "short_reason",
            "fund_reason",
        }:
            raise StageWorkerError("P014 review row fields differ")
        case_id = str(case.get("case_id") or "")
        if raw.get("index") != index or raw.get("case_id") != case_id:
            raise StageWorkerError("P014 review row order differs")
        roles: dict[str, dict[str, str]] = {}
        for role in ("short_term", "fund"):
            values = raw.get(role)
            if not isinstance(values, Mapping) or set(values) != set(_P014_REVIEW_PROPOSITIONS):
                raise StageWorkerError("P014 review atomic proposition shape differs")
            if any(str(value) not in _P014_REVIEW_TRUTH for value in values.values()):
                raise StageWorkerError("P014 review truth value differs")
            roles[role] = {
                proposition: str(values[proposition])
                for proposition in _P014_REVIEW_PROPOSITIONS
            }
        short_reason = str(raw.get("short_reason") or "").strip()
        fund_reason = str(raw.get("fund_reason") or "").strip()
        if not short_reason or not fund_reason or max(len(short_reason), len(fund_reason)) > 2000:
            raise StageWorkerError("P014 review reason differs")
        normalized.append(
            {
                "index": index,
                "case_id": case_id,
                "short_term": roles["short_term"],
                "fund": roles["fund"],
                "short_reason": short_reason,
                "fund_reason": fund_reason,
            }
        )
    summary = _p014_review_reference_summary(normalized)
    return {
        "schema_version": _P014_REVIEW_SCHEMA,
        "work_item": "P3-STAGE-014-D",
        "parent_job_id": observed["job_id"],
        "source_manifest_sha256": manifest_sha,
        "development_evidence_digest": evidence_sha,
        "case_count": len(cases),
        "provenance": dict(provenance),
        "candidate_output_seen_during_review": False,
        "threshold_basis_seen_during_review": False,
        "envelope_measurement_seen_during_review": False,
        "selection_transport_seen_during_review": False,
        "future_rows_seen_during_review": False,
        "preserved_2021_seen_during_review": False,
        "opinions": normalized,
        "reference_summary": summary,
        "submission_digest": _digest(dict(submission)),
    }


def _p014_review_ledger(
    paths: StageWorkerPaths, observed: Mapping[str, Any], *, required: bool = False,
) -> dict[str, Any] | None:
    job_id = str(observed.get("job_id") or "")
    path = paths.atomic_proposition_admission_qualified_review_ledger(job_id)
    if not path.is_file():
        if required:
            raise StageWorkerError("P014 review ledger must be sealed before selection access")
        return None
    ledger = _object(path, "P014 review ledger")
    submission = {
        key: ledger[key]
        for key in (
            "work_item",
            "parent_job_id",
            "source_manifest_sha256",
            "development_evidence_digest",
            "provenance",
            "candidate_output_seen_during_review",
            "threshold_basis_seen_during_review",
            "envelope_measurement_seen_during_review",
            "selection_transport_seen_during_review",
            "future_rows_seen_during_review",
            "preserved_2021_seen_during_review",
            "opinions",
        )
    }
    submission["schema_version"] = _P014_REVIEW_SUBMISSION_SCHEMA
    expected = _validate_p014_review_submission(paths, observed, submission)
    if ledger != expected:
        raise StageWorkerError("P014 review ledger content differs from validated seal")
    return ledger


def seal_p014_review(
    paths: StageWorkerPaths, job_id: str, submission: Mapping[str, Any],
) -> dict[str, Any]:
    if not _JOB_ID.fullmatch(job_id):
        raise StageWorkerError("P014 review seal requires a valid parent job id")
    status_path = paths.status / f"{job_id}.json"
    if not status_path.is_file():
        raise StageWorkerError("P014 review parent status is missing")
    observed = _object(status_path, "P014 review parent status")
    ledger = _validate_p014_review_submission(paths, observed, submission)
    path = paths.atomic_proposition_admission_qualified_review_ledger(job_id)
    if path.exists():
        existing = _object(path, "P014 review ledger")
        if existing != ledger:
            raise StageWorkerError("P014 review ledger is already sealed with different content")
    else:
        _exclusive_json(path, ledger)
    return {
        "schema_version": "quanthus.stage-atomic-proposition-review-seal-receipt.v1",
        "parent_job_id": job_id,
        "sealed": True,
        "ledger_sha256": _file_sha256(path),
        "case_count": ledger["case_count"],
        "reference_summary": ledger["reference_summary"],
        "selection_transport_authorized": True,
    }


def _p015_review_contract(paths: StageWorkerPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    config_path = (
        paths.control_repo
        / "config/stage-learning/stage-complete-reserve-additional-review-prepare.v1.json"
    )
    config = _object(config_path, "P015 review prepare config")
    if (
        config.get("schema_version")
        != "quanthus.stage-complete-reserve-additional-review-prepare.v1"
        or config.get("work_item") != "P3-STAGE-015-C1"
        or config.get("state") != "PREREGISTERED_EXACT_60_PRIMARY_BLIND_REVIEW_PREPARE"
    ):
        raise StageWorkerError("P015 review prepare contract differs")
    binding = config.get("bindings", {}).get("p014_closeout")
    if not isinstance(binding, Mapping):
        raise StageWorkerError("P015 review P014 closeout binding missing")
    closeout_path = paths.control_repo / str(binding.get("path") or "")
    if _file_sha256(closeout_path) != binding.get("file_sha256"):
        raise StageWorkerError("P015 review P014 closeout binding differs")
    closeout = _object(closeout_path, "P014 closeout")
    if (
        closeout.get("review_ledger", {}).get("ledger_sha256")
        != binding.get("review_ledger_sha256")
        or closeout.get("review_ledger", {}).get("case_count") != binding.get("review_case_count")
    ):
        raise StageWorkerError("P015 review frozen P014 ledger binding differs")
    return config, closeout


def _validated_frozen_p014_ledger(
    paths: StageWorkerPaths, closeout: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    job_id = str(closeout.get("job", {}).get("job_id") or "")
    if not _JOB_ID.fullmatch(job_id):
        raise StageWorkerError("P015 review frozen P014 parent job differs")
    path = paths.atomic_proposition_admission_qualified_review_ledger(job_id)
    if not path.is_file():
        raise StageWorkerError("P015 review frozen P014 ledger is missing")
    ledger_sha = _file_sha256(path)
    if ledger_sha != closeout.get("review_ledger", {}).get("ledger_sha256"):
        raise StageWorkerError("P015 review frozen P014 ledger SHA differs")
    ledger = _object(path, "frozen P014 review ledger")
    opinions = ledger.get("opinions")
    if (
        ledger.get("schema_version") != _P014_REVIEW_SCHEMA
        or ledger.get("work_item") != "P3-STAGE-014-D"
        or ledger.get("case_count") != 192
        or not isinstance(opinions, list)
        or len(opinions) != 192
        or ledger.get("reference_summary") != _p014_review_reference_summary(opinions)
    ):
        raise StageWorkerError("P015 review frozen P014 ledger content differs")
    return ledger, ledger_sha


def _validate_p015_review_submission(
    paths: StageWorkerPaths,
    observed: Mapping[str, Any],
    submission: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        observed.get("kind") != STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB
        or observed.get("state") != "succeeded"
    ):
        raise StageWorkerError("P015 review seal requires the successful exact parent job")
    manifest_sha, evidence_sha, _path_sha, evidence, _path_facts = (
        _positive_instability_d1_review_evidence(paths, observed)
    )
    expected_fields = {
        "schema_version",
        "work_item",
        "parent_job_id",
        "source_manifest_sha256",
        "development_evidence_digest",
        "provenance",
        "candidate_output_seen_during_review",
        "threshold_basis_seen_during_review",
        "envelope_measurement_seen_during_review",
        "selection_transport_seen_during_review",
        "existing_p014_opinions_seen_during_review",
        "future_rows_seen_during_review",
        "preserved_2021_seen_during_review",
        "opinions",
    }
    if set(submission) != expected_fields:
        raise StageWorkerError("P015 review submission fields differ")
    if (
        submission.get("schema_version") != _P015_REVIEW_SUBMISSION_SCHEMA
        or submission.get("work_item") != "P3-STAGE-015-C"
        or submission.get("parent_job_id") != observed.get("job_id")
        or submission.get("source_manifest_sha256") != manifest_sha
        or submission.get("development_evidence_digest") != evidence_sha
    ):
        raise StageWorkerError("P015 review submission binding differs")
    for key in (
        "candidate_output_seen_during_review",
        "threshold_basis_seen_during_review",
        "envelope_measurement_seen_during_review",
        "selection_transport_seen_during_review",
        "existing_p014_opinions_seen_during_review",
        "future_rows_seen_during_review",
        "preserved_2021_seen_during_review",
    ):
        if submission.get(key) is not False:
            raise StageWorkerError("P015 review blinding declaration differs")
    provenance = submission.get("provenance")
    if (
        not isinstance(provenance, Mapping)
        or set(provenance) != {"kind", "origin_id", "session_id"}
        or provenance.get("kind") != "self_simulated"
        or not str(provenance.get("origin_id") or "").strip()
        or not str(provenance.get("session_id") or "").strip()
    ):
        raise StageWorkerError("P015 review provenance differs")
    opinions = submission.get("opinions")
    cases = evidence.get("cases")
    if not isinstance(opinions, list) or not isinstance(cases, list) or len(cases) != 60:
        raise StageWorkerError("P015 review denominator differs")
    if len(opinions) != len(cases):
        raise StageWorkerError("P015 review opinion count differs")
    normalized: list[dict[str, Any]] = []
    for index, (raw, case) in enumerate(zip(opinions, cases, strict=True)):
        if not isinstance(raw, Mapping) or not isinstance(case, Mapping):
            raise StageWorkerError("P015 review row shape differs")
        if set(raw) != {
            "index",
            "case_id",
            "short_term",
            "fund",
            "short_reason",
            "fund_reason",
        }:
            raise StageWorkerError("P015 review row fields differ")
        case_id = str(case.get("case_id") or "")
        if raw.get("index") != index or raw.get("case_id") != case_id:
            raise StageWorkerError("P015 review row order differs")
        roles: dict[str, dict[str, str]] = {}
        for role in ("short_term", "fund"):
            values = raw.get(role)
            if not isinstance(values, Mapping) or set(values) != set(_P014_REVIEW_PROPOSITIONS):
                raise StageWorkerError("P015 review atomic proposition shape differs")
            if any(str(value) not in _P014_REVIEW_TRUTH for value in values.values()):
                raise StageWorkerError("P015 review truth value differs")
            roles[role] = {
                proposition: str(values[proposition])
                for proposition in _P014_REVIEW_PROPOSITIONS
            }
        short_reason = str(raw.get("short_reason") or "").strip()
        fund_reason = str(raw.get("fund_reason") or "").strip()
        if not short_reason or not fund_reason or max(len(short_reason), len(fund_reason)) > 2000:
            raise StageWorkerError("P015 review reason differs")
        normalized.append(
            {
                "index": index,
                "case_id": case_id,
                "short_term": roles["short_term"],
                "fund": roles["fund"],
                "short_reason": short_reason,
                "fund_reason": fund_reason,
            }
        )
    return {
        "schema_version": _P015_REVIEW_SCHEMA,
        "work_item": "P3-STAGE-015-C",
        "parent_job_id": observed["job_id"],
        "source_manifest_sha256": manifest_sha,
        "development_evidence_digest": evidence_sha,
        "case_count": 60,
        "provenance": dict(provenance),
        "candidate_output_seen_during_review": False,
        "threshold_basis_seen_during_review": False,
        "envelope_measurement_seen_during_review": False,
        "selection_transport_seen_during_review": False,
        "existing_p014_opinions_seen_during_review": False,
        "future_rows_seen_during_review": False,
        "preserved_2021_seen_during_review": False,
        "opinions": normalized,
        "new_reference_summary": _p014_review_reference_summary(normalized),
        "submission_digest": _digest(dict(submission)),
    }


def _p015_review_ledger(
    paths: StageWorkerPaths, observed: Mapping[str, Any], *, required: bool = False,
) -> dict[str, Any] | None:
    path = paths.complete_reserve_additional_review_ledger(str(observed.get("job_id") or ""))
    if not path.is_file():
        if required:
            raise StageWorkerError("P015 review ledger must be sealed before combined gate access")
        return None
    ledger = _object(path, "P015 additional review ledger")
    submission = {
        key: ledger[key]
        for key in (
            "work_item",
            "parent_job_id",
            "source_manifest_sha256",
            "development_evidence_digest",
            "provenance",
            "candidate_output_seen_during_review",
            "threshold_basis_seen_during_review",
            "envelope_measurement_seen_during_review",
            "selection_transport_seen_during_review",
            "existing_p014_opinions_seen_during_review",
            "future_rows_seen_during_review",
            "preserved_2021_seen_during_review",
            "opinions",
        )
    }
    submission["schema_version"] = _P015_REVIEW_SUBMISSION_SCHEMA
    expected = _validate_p015_review_submission(paths, observed, submission)
    config, closeout = _p015_review_contract(paths)
    old_ledger, old_sha = _validated_frozen_p014_ledger(paths, closeout)
    if old_sha != config["bindings"]["p014_closeout"]["review_ledger_sha256"]:
        raise StageWorkerError("P015 review old ledger contract binding differs")
    old_ids = {str(item.get("case_id") or "") for item in old_ledger["opinions"]}
    new_ids = {str(item.get("case_id") or "") for item in expected["opinions"]}
    if len(new_ids) != 60 or old_ids & new_ids:
        raise StageWorkerError("P015 review old/new case identity overlap")
    combined = _p014_review_reference_summary(
        [*old_ledger["opinions"], *expected["opinions"]]
    )
    expected.update(
        {
            "frozen_p014_review": {
                "parent_job_id": closeout["job"]["job_id"],
                "ledger_sha256": old_sha,
                "case_count": 192,
            },
            "combined_case_count": 252,
            "combined_reference_summary": combined,
        }
    )
    if ledger != expected:
        raise StageWorkerError("P015 review ledger content differs from validated seal")
    return ledger


def seal_p015_review(
    paths: StageWorkerPaths, job_id: str, submission: Mapping[str, Any],
) -> dict[str, Any]:
    if not _JOB_ID.fullmatch(job_id):
        raise StageWorkerError("P015 review seal requires a valid parent job id")
    status_path = paths.status / f"{job_id}.json"
    if not status_path.is_file():
        raise StageWorkerError("P015 review parent status is missing")
    observed = _object(status_path, "P015 review parent status")
    ledger = _validate_p015_review_submission(paths, observed, submission)
    config, closeout = _p015_review_contract(paths)
    old_ledger, old_sha = _validated_frozen_p014_ledger(paths, closeout)
    if old_sha != config["bindings"]["p014_closeout"]["review_ledger_sha256"]:
        raise StageWorkerError("P015 review old ledger contract binding differs")
    old_ids = {str(item.get("case_id") or "") for item in old_ledger["opinions"]}
    new_ids = {str(item.get("case_id") or "") for item in ledger["opinions"]}
    if len(new_ids) != 60 or old_ids & new_ids:
        raise StageWorkerError("P015 review old/new case identity overlap")
    ledger.update(
        {
            "frozen_p014_review": {
                "parent_job_id": closeout["job"]["job_id"],
                "ledger_sha256": old_sha,
                "case_count": 192,
            },
            "combined_case_count": 252,
            "combined_reference_summary": _p014_review_reference_summary(
                [*old_ledger["opinions"], *ledger["opinions"]]
            ),
        }
    )
    path = paths.complete_reserve_additional_review_ledger(job_id)
    if path.exists():
        existing = _object(path, "P015 additional review ledger")
        if existing != ledger:
            raise StageWorkerError("P015 review ledger is already sealed with different content")
    else:
        _exclusive_json(path, ledger)
    return {
        "schema_version": "quanthus.stage-complete-reserve-review-seal-receipt.v1",
        "parent_job_id": job_id,
        "sealed": True,
        "new_review_ledger_sha256": _file_sha256(path),
        "new_case_count": 60,
        "frozen_p014_review_ledger_sha256": old_sha,
        "combined_case_count": 252,
        "combined_reference_summary": ledger["combined_reference_summary"],
        "candidate_scoring_authorized": False,
        "preserved_2021_read_authorized": False,
    }


def _atomic_proposition_g1_review_alias(parent_job_id: str, page: int) -> str:
    raw = f"{parent_job_id}|atomic-proposition-g1-primary-page|{page}".encode()
    return "stage_" + hashlib.sha256(raw).hexdigest()[:32]


def _atomic_proposition_g1_review_index(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_ATOMIC_PROPOSITION_G1_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    manifest_sha, evidence_sha, path_facts_sha, evidence, _path_facts = (
        _positive_instability_d1_review_evidence(paths, observed)
    )
    case_count = len(evidence["cases"])
    pages = (
        case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
    ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
    return {
        "schema_version": "quanthus.stage-atomic-proposition-g1-primary-review-index.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "evidence_file_sha256": evidence_sha,
        "path_facts_file_sha256": path_facts_sha,
        "case_count": case_count,
        "page_size": DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
        "page_count": pages,
        "pages": [
            {
                "page": page,
                "job_id": _atomic_proposition_g1_review_alias(str(observed["job_id"]), page),
                "start": page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                "count": min(
                    DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                    case_count - page * DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE,
                ),
            }
            for page in range(pages)
        ],
        "threshold_basis_included": False,
        "candidate_output_included": False,
        "envelope_measurement_included": False,
        "explicit_recent_derived_facts_included": False,
        "future_rows_included": False,
        "preserved_2021_evaluation_included": False,
    }


def _atomic_proposition_g1_selection_transport(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    kind = observed.get("kind")
    if (
        kind not in {
            STAGE_ATOMIC_PROPOSITION_G1_JOB,
            STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB,
            STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB,
        }
        or observed.get("state") != "succeeded"
    ):
        return None
    if (
        kind == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB
        and _p014_review_ledger(paths, observed) is None
    ):
        return None
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("atomic proposition result reference missing")
    detail_key = {
        STAGE_ATOMIC_PROPOSITION_G1_JOB: "atomic_proposition_g1",
        STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB: "atomic_proposition_successor_d1",
        STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB: (
            "atomic_proposition_admission_qualified_d1"
        ),
    }[str(kind)]
    detail = result_ref.get(detail_key)
    if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
        raise StageWorkerError("atomic proposition G1 selection is not ready")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError("atomic proposition G1 selection artifact outside job root") from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    manifest_sha = _bytes_sha256(manifest_raw)
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("atomic proposition G1 selection manifest binding differs")
    manifest = json.loads(manifest_raw)
    files = manifest.get("files", {})
    payload: dict[str, Any] = {}
    for name in (
        "threshold-basis.json",
        "candidate-grid.json",
        "envelope-measurements.json",
        "preserved-evaluation-plan.json",
    ):
        raw = (artifact_root / name).read_bytes()
        if files.get(name) != _bytes_sha256(raw):
            raise StageWorkerError("atomic proposition G1 selection file binding differs")
        payload[name] = json.loads(raw)
    basis = payload["threshold-basis.json"]
    grid = payload["candidate-grid.json"]
    envelopes = payload["envelope-measurements.json"]
    plan = payload["preserved-evaluation-plan.json"]
    if (
        _digest(basis) != detail.get("threshold_basis_digest")
        or _digest(grid) != detail.get("candidate_grid_digest")
        or _digest(envelopes) != detail.get("envelope_measurements_digest")
        or _digest(plan) != detail.get("preserved_evaluation_plan_digest")
        or grid.get("instability_candidate_count") != 9
        or grid.get("order_profile_count") != 3
        or grid.get("relocation_candidate_count") != 1
        or grid.get("joint_pair_search_count") != 0
        or grid.get("parameter_search_performed") is not False
        or grid.get("grid_expanded_after_result") is not False
        or basis.get("labels_used") is not False
        or basis.get("reference_opinions_used") is not False
        or envelopes.get("review_transport_visible") is not False
        or plan.get("data_read") is not False
    ):
        raise StageWorkerError("atomic proposition G1 selection payload differs")
    selection_schema = {
        STAGE_ATOMIC_PROPOSITION_G1_JOB: (
            "quanthus.stage-atomic-proposition-g1-selection-transport.v1"
        ),
        STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB: (
            "quanthus.stage-atomic-proposition-successor-d1-selection-transport.v1"
        ),
        STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB: (
            "quanthus.stage-atomic-proposition-admission-qualified-d1-selection-transport.v1"
        ),
    }[str(kind)]
    transport = {
        "schema_version": selection_schema,
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "threshold_basis": basis,
        "candidate_grid": grid,
        "envelope_measurements": envelopes,
        "evaluation_plan_binding": {
            "identity_count": plan.get("identity_count"),
            "identity_digest": plan.get("identity_digest"),
            "plan_digest": _digest(plan),
            "data_read": False,
        },
        "review_ledger_must_be_sealed_before_use": True,
        "review_transport_exposed_envelope_measurement": False,
        "evaluation_data_read": False,
        "parameter_search_performed": False,
        "joint_pair_search_count": 0,
    }
    raw = json.dumps(
        transport,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    encoded = base64.b64encode(compressed).decode("ascii")
    if len(encoded) > 300 * 1024:
        raise StageWorkerError("atomic proposition G1 selection transport exceeds status budget")
    envelope_schema = {
        STAGE_ATOMIC_PROPOSITION_G1_JOB: (
            "quanthus.stage-atomic-proposition-g1-selection-envelope.v1"
        ),
        STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB: (
            "quanthus.stage-atomic-proposition-successor-d1-selection-envelope.v1"
        ),
        STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB: (
            "quanthus.stage-atomic-proposition-admission-qualified-d1-selection-envelope.v1"
        ),
    }[str(kind)]
    return {
        "schema_version": envelope_schema,
        "encoding": "gzip+base64",
        "raw_sha256": _bytes_sha256(raw),
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "encoded_bytes": len(encoded),
        "payload": encoded,
    }


def _atomic_proposition_successor_d1_virtual_review_status(
    paths: StageWorkerPaths, requested_job_id: str,
) -> dict[str, Any] | None:
    for status_path in sorted(paths.status.glob("stage_*.json")):
        observed = _object(status_path, "stage job status")
        kind = observed.get("kind")
        if (
            kind not in {
                STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB,
                STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB,
                STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB,
            }
            or observed.get("state") != "succeeded"
        ):
            continue
        result_ref = observed.get("result_ref")
        if kind == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB:
            detail_key = "atomic_proposition_admission_qualified_d1"
        elif kind == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB:
            detail_key = "complete_reserve_additional_review_prepare"
        else:
            detail_key = "atomic_proposition_successor_d1"
        detail = result_ref.get(detail_key) if isinstance(result_ref, Mapping) else None
        if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
            continue
        case_count = int(detail.get("development_selected_case_count") or 0)
        pages = (
            case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
        ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
        for page in range(pages):
            alias = _atomic_proposition_successor_d1_review_alias(
                str(observed["job_id"]), page
            )
            if alias != requested_job_id:
                continue
            if kind == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB:
                virtual_kind = (
                    "quanthus.stage-atomic-proposition-admission-qualified-d1-primary-review-page.v1"
                )
            elif kind == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB:
                virtual_kind = "quanthus.stage-complete-reserve-additional-primary-review-page.v1"
            else:
                virtual_kind = (
                    "quanthus.stage-atomic-proposition-successor-d1-primary-review-page.v1"
                )
            return {
                "schema_version": STATUS_SCHEMA,
                "job_id": requested_job_id,
                "kind": virtual_kind,
                "code_revision": observed.get("code_revision"),
                "input_digest": observed.get("input_digest"),
                "state": "succeeded",
                "stage": "virtual_read_only",
                "submitted_at": observed.get("submitted_at"),
                "started_at": observed.get("started_at"),
                "completed_at": observed.get("completed_at"),
                "parent_job_id": observed["job_id"],
                "page": page,
                "review_transport": _atomic_proposition_successor_d1_review_page(
                    paths, observed, page
                ),
                "error": None,
                "exit_code": 0,
                "updated_at": observed.get("updated_at"),
            }
    return None


def _atomic_proposition_g1_virtual_review_status(
    paths: StageWorkerPaths, requested_job_id: str,
) -> dict[str, Any] | None:
    for status_path in sorted(paths.status.glob("stage_*.json")):
        observed = _object(status_path, "stage job status")
        if (
            observed.get("kind") != STAGE_ATOMIC_PROPOSITION_G1_JOB
            or observed.get("state") != "succeeded"
        ):
            continue
        result_ref = observed.get("result_ref")
        detail = (
            result_ref.get("atomic_proposition_g1")
            if isinstance(result_ref, Mapping)
            else None
        )
        if not isinstance(detail, Mapping) or detail.get("review_ready") is not True:
            continue
        case_count = int(detail.get("development_selected_case_count") or 0)
        pages = (
            case_count + DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE - 1
        ) // DIRECTIONAL_BOUNDARY_PRIMARY_REVIEW_PAGE_SIZE
        for page in range(pages):
            alias = _atomic_proposition_g1_review_alias(str(observed["job_id"]), page)
            if alias != requested_job_id:
                continue
            return {
                "schema_version": STATUS_SCHEMA,
                "job_id": requested_job_id,
                "kind": "quanthus.stage-atomic-proposition-g1-primary-review-page.v1",
                "code_revision": observed.get("code_revision"),
                "input_digest": observed.get("input_digest"),
                "state": "succeeded",
                "stage": "virtual_read_only",
                "submitted_at": observed.get("submitted_at"),
                "started_at": observed.get("started_at"),
                "completed_at": observed.get("completed_at"),
                "parent_job_id": observed["job_id"],
                "page": page,
                "review_transport": _positive_instability_d1_review_page(
                    paths, observed, page
                ),
                "error": None,
                "exit_code": 0,
                "updated_at": observed.get("updated_at"),
            }
    return None


def _complete_reserve_additional_ready_transport(
    paths: StageWorkerPaths, observed: Mapping[str, Any]
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("P015 readiness result reference missing")
    detail = result_ref.get("complete_reserve_review_readiness")
    if not isinstance(detail, Mapping) or detail.get("continuation_pass") is not True:
        raise StageWorkerError("P015 readiness continuation is not authorized")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError("P015 readiness artifact outside job root") from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    manifest_sha = _bytes_sha256(manifest_raw)
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("P015 readiness manifest binding differs")
    manifest = json.loads(manifest_raw)
    readiness_raw = (artifact_root / "review-readiness.json").read_bytes()
    if manifest.get("files", {}).get("review-readiness.json") != _bytes_sha256(readiness_raw):
        raise StageWorkerError("P015 readiness ledger file binding differs")
    ledger = json.loads(readiness_raw)
    rows = ledger.get("rows") if isinstance(ledger, Mapping) else None
    if not isinstance(rows, list) or len(rows) != 256:
        raise StageWorkerError("P015 readiness ledger shape differs")
    if (
        _digest(rows) != detail.get("ledger_digest")
        or ledger.get("semantic_values_retained") is not False
    ):
        raise StageWorkerError("P015 readiness ledger digest differs")
    allowed = {
        "security_id",
        "window_id",
        "era",
        "board_family",
        "cell",
        "review_packet_ready",
        "readiness_reason",
        "source_member_closure_digest",
        "already_reviewed_in_p014_ledger",
        "new_review_required",
    }
    if any(not isinstance(row, Mapping) or set(row) != allowed for row in rows):
        raise StageWorkerError("P015 readiness ledger retention fields differ")
    additional = [
        {
            "security_id": str(row["security_id"]),
            "window_id": str(row["window_id"]),
            "era": str(row["era"]),
            "board_family": str(row["board_family"]),
            "cell": str(row["cell"]),
            "source_member_closure_digest": str(row["source_member_closure_digest"]),
        }
        for row in rows
        if row.get("new_review_required") is True
    ]
    if (
        len(additional) != detail.get("new_review_required_count")
        or len(additional) != 60
        or len({(row["security_id"], row["window_id"]) for row in additional}) != len(additional)
    ):
        raise StageWorkerError("P015 additional review denominator differs")
    return {
        "schema_version": "quanthus.stage-complete-reserve-additional-ready-transport.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "ledger_digest": detail.get("ledger_digest"),
        "ready_count": detail.get("ready_count"),
        "unready_count": detail.get("unready_count"),
        "already_reviewed_ready_count": detail.get("already_reviewed_ready_count"),
        "new_review_required_count": len(additional),
        "rows": additional,
        "semantic_values_included": False,
        "reference_opinions_included": False,
        "candidate_or_threshold_output_included": False,
    }


def _combined_atomic_scoring_diagnostics(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_COMBINED_ATOMIC_SCORING_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("combined atomic scoring result reference missing")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError("combined atomic scoring artifact outside job root") from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    manifest_sha = _bytes_sha256(manifest_raw)
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("combined atomic scoring diagnostic manifest binding differs")
    manifest = json.loads(manifest_raw)
    raw = (artifact_root / "atomic-score.json").read_bytes()
    if manifest.get("files", {}).get("atomic-score.json") != _bytes_sha256(raw):
        raise StageWorkerError("combined atomic scoring diagnostic file binding differs")
    score = json.loads(raw)
    trials = score.get("instability_trials")
    if not isinstance(trials, list) or len(trials) != 9:
        raise StageWorkerError("combined atomic scoring diagnostic trial count differs")

    def compact_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
        pooled = metrics.get("pooled")
        macro = metrics.get("macro")
        per_cell = metrics.get("per_cell")
        if (
            not isinstance(pooled, Mapping)
            or not isinstance(macro, Mapping)
            or not isinstance(per_cell, Mapping)
        ):
            raise StageWorkerError("combined atomic scoring diagnostic metrics differ")

        def compact_cell(cell_metrics: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "definite_positive_references": cell_metrics.get(
                    "definite_positive_references"
                ),
                "definite_negative_references": cell_metrics.get(
                    "definite_negative_references"
                ),
                "predictions": cell_metrics.get("predictions"),
                "true_positive": cell_metrics.get("true_positive"),
                "false_positive": cell_metrics.get("false_positive"),
                "precision": cell_metrics.get("precision"),
                "positive_reference_coverage": cell_metrics.get(
                    "positive_reference_coverage"
                ),
                "definite_negative_false_positive_rate": cell_metrics.get(
                    "definite_negative_false_positive_rate"
                ),
            }

        compact_cells = {}
        for cell, cell_metrics in sorted(per_cell.items()):
            if not isinstance(cell_metrics, Mapping):
                raise StageWorkerError(
                    "combined atomic scoring diagnostic per-cell metrics differ"
                )
            compact_cells[str(cell)] = compact_cell(cell_metrics)
        return {
            "macro_precision": macro.get("precision"),
            "macro_positive_reference_coverage": macro.get("positive_reference_coverage"),
            "macro_definite_negative_false_positive_rate": macro.get(
                "definite_negative_false_positive_rate"
            ),
            "pooled_predictions": pooled.get("predictions"),
            "pooled_true_positive": pooled.get("true_positive"),
            "pooled_false_positive": pooled.get("false_positive"),
            "pooled_precision": pooled.get("precision"),
            "pooled_positive_reference_coverage": pooled.get(
                "positive_reference_coverage"
            ),
            "pooled_definite_negative_false_positive_rate": pooled.get(
                "definite_negative_false_positive_rate"
            ),
            "per_cell": compact_cells,
        }

    compact_trials = []
    for trial in trials:
        if not isinstance(trial, Mapping):
            raise StageWorkerError("combined atomic scoring diagnostic trial shape differs")
        candidate = trial.get("candidate")
        metrics = trial.get("metrics")
        base_metrics = trial.get("base_metrics")
        if not all(isinstance(item, Mapping) for item in (candidate, metrics, base_metrics)):
            raise StageWorkerError("combined atomic scoring diagnostic trial payload differs")
        compact_trials.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "expansion_profile_id": candidate.get("expansion_profile_id"),
                "broad_profile_id": candidate.get("broad_profile_id"),
                "eligible": trial.get("eligible") is True,
                "metrics": compact_metrics(metrics),
                "base_metrics": compact_metrics(base_metrics),
                "incremental_true_positive": trial.get("incremental_true_positive"),
                "incremental_false_positive": trial.get("incremental_false_positive"),
            }
        )
    return {
        "schema_version": "quanthus.stage-combined-atomic-scoring-diagnostics.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "disposition": score.get("disposition"),
        "reference_case_count": score.get("reference_case_count"),
        "instability_trials": compact_trials,
        "candidate_threshold_values_included": False,
        "case_predictions_included": False,
        "reference_rows_included": False,
        "dwd_market_read": False,
    }


def _stratum_normalized_instability_diagnostics(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("stratum normalized instability result reference missing")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError("stratum normalized instability artifact outside job root") from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    manifest_sha = _bytes_sha256(manifest_raw)
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("stratum normalized instability diagnostic manifest binding differs")
    manifest = json.loads(manifest_raw)
    raw = (artifact_root / "instability-score.json").read_bytes()
    if manifest.get("files", {}).get("instability-score.json") != _bytes_sha256(raw):
        raise StageWorkerError("stratum normalized instability diagnostic file binding differs")
    score = json.loads(raw)
    trials = score.get("trials")
    if not isinstance(trials, list) or len(trials) != 9:
        raise StageWorkerError("stratum normalized instability diagnostic trial count differs")

    def compact_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
        pooled = metrics.get("pooled")
        macro = metrics.get("macro")
        per_cell = metrics.get("per_cell")
        if (
            not isinstance(pooled, Mapping)
            or not isinstance(macro, Mapping)
            or not isinstance(per_cell, Mapping)
        ):
            raise StageWorkerError("stratum normalized instability diagnostic metrics differ")
        compact_cells: dict[str, dict[str, Any]] = {}
        for cell, cell_metrics in sorted(per_cell.items()):
            if not isinstance(cell_metrics, Mapping):
                raise StageWorkerError(
                    "stratum normalized instability diagnostic per-cell metrics differ"
                )
            compact_cells[str(cell)] = {
                "definite_positive_references": cell_metrics.get(
                    "definite_positive_references"
                ),
                "definite_negative_references": cell_metrics.get(
                    "definite_negative_references"
                ),
                "predictions": cell_metrics.get("predictions"),
                "true_positive": cell_metrics.get("true_positive"),
                "false_positive": cell_metrics.get("false_positive"),
                "precision": cell_metrics.get("precision"),
                "positive_reference_coverage": cell_metrics.get(
                    "positive_reference_coverage"
                ),
                "definite_negative_false_positive_rate": cell_metrics.get(
                    "definite_negative_false_positive_rate"
                ),
            }
        return {
            "macro_precision": macro.get("precision"),
            "macro_positive_reference_coverage": macro.get("positive_reference_coverage"),
            "macro_definite_negative_false_positive_rate": macro.get(
                "definite_negative_false_positive_rate"
            ),
            "pooled_predictions": pooled.get("predictions"),
            "pooled_true_positive": pooled.get("true_positive"),
            "pooled_false_positive": pooled.get("false_positive"),
            "pooled_precision": pooled.get("precision"),
            "pooled_positive_reference_coverage": pooled.get(
                "positive_reference_coverage"
            ),
            "pooled_definite_negative_false_positive_rate": pooled.get(
                "definite_negative_false_positive_rate"
            ),
            "per_cell": compact_cells,
        }

    compact_trials = []
    for trial in trials:
        if not isinstance(trial, Mapping):
            raise StageWorkerError("stratum normalized instability diagnostic trial shape differs")
        metrics = trial.get("metrics")
        if not isinstance(metrics, Mapping):
            raise StageWorkerError("stratum normalized instability diagnostic trial metrics differ")
        compact_trials.append(
            {
                "profile_id": trial.get("profile_id"),
                "expansion_profile_id": trial.get("expansion_profile_id"),
                "broad_profile_id": trial.get("broad_profile_id"),
                "eligible": trial.get("eligible") is True,
                "metrics": compact_metrics(metrics),
            }
        )
    return {
        "schema_version": "quanthus.stage-stratum-normalized-instability-diagnostics.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "disposition": score.get("disposition"),
        "reference_case_count": score.get("reference_case_count"),
        "eligible_candidate_count": score.get("eligible_candidate_count"),
        "trials": compact_trials,
        "cell_threshold_values_included": False,
        "case_predictions_included": False,
        "reference_rows_included": False,
        "dwd_market_read": False,
        "preserved_2021_read": False,
        "2025_read": False,
    }


def _instability_channel_decomposition_diagnostics(
    paths: StageWorkerPaths, observed: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        observed.get("kind") != STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB
        or observed.get("state") != "succeeded"
    ):
        return None
    result_ref = observed.get("result_ref")
    if not isinstance(result_ref, Mapping):
        raise StageWorkerError("instability channel decomposition result reference missing")
    artifact_root = Path(str(result_ref.get("artifact_root") or "")).resolve()
    job_root = (paths.runs / str(observed["job_id"])).resolve()
    try:
        artifact_root.relative_to(job_root)
    except ValueError as exc:
        raise StageWorkerError(
            "instability channel decomposition artifact outside job root"
        ) from exc
    manifest_raw = (artifact_root / "manifest.json").read_bytes()
    manifest_sha = _bytes_sha256(manifest_raw)
    ready = (artifact_root / "READY").read_text(encoding="ascii").strip()
    if ready != manifest_sha or result_ref.get("manifest_sha256") != manifest_sha:
        raise StageWorkerError("instability channel decomposition manifest binding differs")
    manifest = json.loads(manifest_raw)
    raw = (artifact_root / "channel-decomposition.json").read_bytes()
    if manifest.get("files", {}).get("channel-decomposition.json") != _bytes_sha256(raw):
        raise StageWorkerError("instability channel decomposition diagnostic file binding differs")
    diagnostic = json.loads(raw)
    profiles = diagnostic.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != 9:
        raise StageWorkerError("instability channel decomposition profile count differs")

    def compact_metrics(value: Mapping[str, Any]) -> dict[str, Any]:
        pooled = value.get("pooled")
        macro = value.get("macro")
        per_cell = value.get("per_cell")
        if (
            not isinstance(pooled, Mapping)
            or not isinstance(macro, Mapping)
            or not isinstance(per_cell, Mapping)
        ):
            raise StageWorkerError("instability channel decomposition metrics differ")
        cells: dict[str, dict[str, Any]] = {}
        for cell, metrics in sorted(per_cell.items()):
            if not isinstance(metrics, Mapping):
                raise StageWorkerError("instability channel decomposition cell metrics differ")
            cells[str(cell)] = {
                "predictions": metrics.get("predictions"),
                "true_positive": metrics.get("true_positive"),
                "false_positive": metrics.get("false_positive"),
                "precision": metrics.get("precision"),
                "positive_reference_coverage": metrics.get("positive_reference_coverage"),
                "definite_negative_false_positive_rate": metrics.get(
                    "definite_negative_false_positive_rate"
                ),
            }
        return {
            "macro_precision": macro.get("precision"),
            "macro_positive_reference_coverage": macro.get("positive_reference_coverage"),
            "macro_definite_negative_false_positive_rate": macro.get(
                "definite_negative_false_positive_rate"
            ),
            "pooled_predictions": pooled.get("predictions"),
            "pooled_true_positive": pooled.get("true_positive"),
            "pooled_false_positive": pooled.get("false_positive"),
            "pooled_precision": pooled.get("precision"),
            "pooled_positive_reference_coverage": pooled.get("positive_reference_coverage"),
            "pooled_definite_negative_false_positive_rate": pooled.get(
                "definite_negative_false_positive_rate"
            ),
            "per_cell": cells,
        }

    compact_profiles = []
    for row in profiles:
        if not isinstance(row, Mapping):
            raise StageWorkerError("instability channel decomposition profile shape differs")
        expansion = row.get("expanding_disorder_20")
        broad = row.get("broad_reversal_disorder_20")
        parent_or = row.get("parent_or")
        attribution = row.get("attribution")
        if not all(
            isinstance(item, Mapping)
            for item in (expansion, broad, parent_or, attribution)
        ):
            raise StageWorkerError("instability channel decomposition profile payload differs")
        compact_profiles.append(
            {
                "profile_id": row.get("profile_id"),
                "expansion_profile_id": row.get("expansion_profile_id"),
                "broad_profile_id": row.get("broad_profile_id"),
                "expanding_disorder_20": compact_metrics(expansion),
                "broad_reversal_disorder_20": compact_metrics(broad),
                "parent_or": compact_metrics(parent_or),
                "attribution": dict(attribution),
            }
        )
    return {
        "schema_version": "quanthus.stage-instability-channel-decomposition-diagnostics.v1",
        "parent_job_id": observed["job_id"],
        "manifest_sha256": manifest_sha,
        "disposition": diagnostic.get("disposition"),
        "reference_case_count": diagnostic.get("reference_case_count"),
        "profile_count": diagnostic.get("profile_count"),
        "profiles": compact_profiles,
        "threshold_values_included": False,
        "case_predictions_included": False,
        "reference_rows_included": False,
        "candidate_selection_performed": False,
        "new_semantic_labels_generated": False,
        "dwd_market_read": False,
        "preserved_2021_read": False,
        "2025_read": False,
    }


def read_status(paths: StageWorkerPaths, job_id: str) -> dict[str, Any]:
    if not _JOB_ID.fullmatch(job_id):
        raise StageWorkerError("status requires a valid stage job id")
    status = paths.status / f"{job_id}.json"
    if status.is_file():
        observed = _object(status, "stage job status")
        review_index = _order_rule_review_index(paths, observed)
        if review_index is None:
            review_index = _directional_boundary_d1_review_index(paths, observed)
        if review_index is None:
            review_index = _directional_boundary_d3_review_index(paths, observed)
        if review_index is None:
            review_index = _positive_instability_d1_review_index(paths, observed)
        if review_index is None:
            review_index = _positive_instability_quantitative_d1_review_index(
                paths, observed
            )
        if review_index is None:
            review_index = _orderly_consolidation_joint_d1_review_index(paths, observed)
        if review_index is None:
            review_index = _atomic_proposition_successor_d1_review_index(paths, observed)
        if review_index is None:
            review_index = _atomic_proposition_g1_review_index(paths, observed)
        result = (
            observed
            if review_index is None
            else {**observed, "review_transport_index": review_index}
        )
        if observed.get("kind") == STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB:
            ledger = _p014_review_ledger(paths, observed)
            result = {
                **result,
                "review_ledger_sealed": ledger is not None,
                "review_ledger_sha256": (
                    None
                    if ledger is None
                    else _file_sha256(
                        paths.atomic_proposition_admission_qualified_review_ledger(
                            str(observed["job_id"])
                        )
                    )
                ),
            }
        if observed.get("kind") == STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB:
            ledger = _p015_review_ledger(paths, observed)
            ledger_path = paths.complete_reserve_additional_review_ledger(
                str(observed["job_id"])
            )
            result = {
                **result,
                "review_ledger_sealed": ledger is not None,
                "review_ledger_sha256": (
                    None if ledger is None else _file_sha256(ledger_path)
                ),
                "combined_reference_summary": (
                    None if ledger is None else ledger["combined_reference_summary"]
                ),
                "combined_case_count": None if ledger is None else 252,
            }
        additional_ready_transport = _complete_reserve_additional_ready_transport(
            paths, observed
        )
        if additional_ready_transport is not None:
            result = {**result, "additional_ready_transport": additional_ready_transport}
        scoring_diagnostics = _combined_atomic_scoring_diagnostics(paths, observed)
        if scoring_diagnostics is not None:
            result = {**result, "scoring_diagnostics": scoring_diagnostics}
        stratum_diagnostics = _stratum_normalized_instability_diagnostics(paths, observed)
        if stratum_diagnostics is not None:
            result = {**result, "stratum_normalized_instability_diagnostics": stratum_diagnostics}
        channel_diagnostics = _instability_channel_decomposition_diagnostics(paths, observed)
        if channel_diagnostics is not None:
            result = {
                **result,
                "instability_channel_decomposition_diagnostics": channel_diagnostics,
            }
        selection_transport = _positive_instability_d1_selection_transport(paths, observed)
        if selection_transport is None:
            selection_transport = _orderly_consolidation_joint_d1_selection_transport(
                paths, observed
            )
        if selection_transport is None:
            selection_transport = _atomic_proposition_g1_selection_transport(paths, observed)
        return (
            result
            if selection_transport is None
            else {**result, "selection_transport": selection_transport}
        )
    for root, state in ((paths.queue, "queued"), (paths.claimed, "claimed")):
        candidate = root / f"{job_id}.json"
        if candidate.is_file():
            job = StageJob.from_dict(_object(candidate, "stage job"))
            return {
                "schema_version": STATUS_SCHEMA,
                "job_id": job.job_id,
                "kind": job.kind,
                "code_revision": job.code_revision,
                "input_digest": job.input_digest,
                "state": state,
                "stage": state,
                "submitted_at": job.submitted_at,
            }
    virtual = _order_rule_virtual_review_status(paths, job_id)
    if virtual is not None:
        return virtual
    virtual = _directional_boundary_d1_virtual_review_status(paths, job_id)
    if virtual is not None:
        return virtual
    virtual = _directional_boundary_d3_virtual_review_status(paths, job_id)
    if virtual is not None:
        return virtual
    virtual = _positive_instability_d1_virtual_review_status(paths, job_id)
    if virtual is not None:
        return virtual
    virtual = _positive_instability_quantitative_d1_virtual_review_status(paths, job_id)
    if virtual is not None:
        return virtual
    virtual = _orderly_consolidation_joint_d1_virtual_review_status(paths, job_id)
    if virtual is not None:
        return virtual
    virtual = _atomic_proposition_successor_d1_virtual_review_status(paths, job_id)
    if virtual is not None:
        return virtual
    virtual = _atomic_proposition_g1_virtual_review_status(paths, job_id)
    if virtual is not None:
        return virtual
    raise StageWorkerError("stage job is not known on this node")


def cancel_job(paths: StageWorkerPaths, job_id: str) -> dict[str, Any]:
    observed = read_status(paths, job_id)
    if observed.get("state") in {"succeeded", "failed", "canceled"}:
        raise StageWorkerError("terminal stage job cannot be canceled")
    paths.cancel.mkdir(parents=True, exist_ok=True)
    marker = paths.cancel / job_id
    marker.touch(exist_ok=True)
    if observed.get("state") == "queued":
        queued = paths.queue / f"{job_id}.json"
        if queued.exists():
            job = StageJob.from_dict(_object(queued, "queued stage job"))
            _release_qualification_reservation(paths, job)
            queued.unlink()
            worker = StageWorker(paths)
            worker._write_status(
                job,
                state="canceled",
                stage="terminal",
                completed_at=_utc_now(),
                error="cancel_requested_before_claim",
            )
            marker.unlink(missing_ok=True)
            return read_status(paths, job_id)
    return {**observed, "cancel_requested": True}


def _recent_stage_job_failure(
    paths: StageWorkerPaths, *, kind: str,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for status_path in paths.status.glob("stage_*.json"):
        try:
            observed = _object(status_path, "stage job status")
        except StageWorkerError:
            continue
        if (
            observed.get("kind") == kind
            and observed.get("state") == "failed"
            and _JOB_ID.fullmatch(str(observed.get("job_id") or ""))
            and status_path.stem == observed.get("job_id")
        ):
            candidates.append(observed)
    if not candidates:
        return None
    observed = max(candidates, key=lambda item: str(item.get("updated_at") or ""))
    job_id = str(observed.get("job_id") or "")
    log_path = paths.logs / f"{job_id}.log"
    tail: list[str] = []
    if log_path.is_file() and not log_path.is_symlink():
        try:
            with log_path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 16384))
                raw = stream.read(16384).decode("utf-8", errors="replace")
            tail = raw.splitlines()[-40:]
        except OSError:
            tail = []
    return {
        "job_id": job_id,
        "code_revision": observed.get("code_revision"),
        "input_digest": observed.get("input_digest"),
        "error": observed.get("error"),
        "exit_code": observed.get("exit_code"),
        "completed_at": observed.get("completed_at"),
        "log_tail": tail,
    }


def doctor(paths: StageWorkerPaths) -> dict[str, Any]:
    paths.ensure()
    problems: list[str] = []
    if not (paths.control_repo / ".git").exists():
        problems.append("control_repo_not_git_checkout")
    if not paths.python.is_file() or not os.access(paths.python, os.X_OK):
        problems.append("stage_training_python_missing")
    if not paths.git_key.is_file():
        problems.append("github_deploy_key_missing")
    source = paths.training_root / "sources" / "dwd-v2.1-full-20260908"
    if not source.is_dir():
        problems.append("training_source_missing")
    return {
        "schema_version": "quanthus.stage-worker-doctor.v1",
        "ready": not problems,
        "problems": problems,
        "control_repo": str(paths.control_repo),
        "training_root": str(paths.training_root),
        "python": str(paths.python),
        "job_kinds": tuple(sorted(_JOB_SPECS)),
        "concurrency": 1,
        "recent_p011_d1_failure": _recent_stage_job_failure(
            paths, kind=STAGE_ORDERLY_CONSOLIDATION_JOINT_D1_JOB,
        ),
        "recent_p012_g1_failure": _recent_stage_job_failure(
            paths, kind=STAGE_ATOMIC_PROPOSITION_G1_JOB,
        ),
        "recent_p013_c_failure": _recent_stage_job_failure(
            paths, kind=STAGE_DEVELOPMENT_RESOURCE_PLAN_JOB,
        ),
        "recent_p013_d1_failure": _recent_stage_job_failure(
            paths, kind=STAGE_ATOMIC_PROPOSITION_SUCCESSOR_D1_JOB,
        ),
        "recent_p014_a_failure": _recent_stage_job_failure(
            paths, kind=STAGE_DEVELOPMENT_RESOURCE_ADMISSION_JOB,
        ),
        "recent_p014_b_failure": _recent_stage_job_failure(
            paths, kind=STAGE_DEVELOPMENT_RESOURCE_QUALIFIED_PLAN_JOB,
        ),
        "recent_p014_c_failure": _recent_stage_job_failure(
            paths, kind=STAGE_ATOMIC_PROPOSITION_ADMISSION_QUALIFIED_D1_JOB,
        ),
        "recent_p015_a_failure": _recent_stage_job_failure(
            paths, kind=STAGE_COMPLETE_RESERVE_REVIEW_READINESS_JOB,
        ),
        "recent_p015_b_failure": _recent_stage_job_failure(
            paths, kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_SET_JOB,
        ),
        "recent_p015_c_failure": _recent_stage_job_failure(
            paths, kind=STAGE_COMPLETE_RESERVE_ADDITIONAL_REVIEW_PREPARE_JOB,
        ),
        "recent_p016_b_failure": _recent_stage_job_failure(
            paths, kind=STAGE_COMBINED_ATOMIC_SCORING_JOB,
        ),
        "recent_p017_b_failure": _recent_stage_job_failure(
            paths, kind=STAGE_STRATUM_NORMALIZED_INSTABILITY_JOB,
        ),
        "recent_p018_b_failure": _recent_stage_job_failure(
            paths, kind=STAGE_INSTABILITY_CHANNEL_DECOMPOSITION_JOB,
        ),
        "recent_p020_b_failure": _recent_stage_job_failure(
            paths, kind=STAGE_DOWNSIDE_LIFECYCLE_RESOURCE_PLAN_JOB,
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--training-root",
        default=os.environ.get("QUANTHUS_STAGE_TRAINING_ROOT", "~/quanthus-data/training"),
    )
    parser.add_argument(
        "--control-repo",
        default=os.environ.get("QUANTHUS_STAGE_CONTROL_REPO", "~/quanthus-code/Quanthus"),
    )
    parser.add_argument(
        "--git-key",
        default=os.environ.get("QUANTHUS_STAGE_GIT_KEY", "~/.ssh/quanthus-github-ro"),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--once", action="store_true")
    serve.add_argument("--poll-seconds", type=float, default=2.0)
    serve.add_argument("--wall-seconds", type=int, default=600)
    submit = sub.add_parser("submit")
    submit.add_argument("--code-revision", required=True)
    submit.add_argument("--kind", choices=tuple(sorted(_JOB_SPECS)), default=STAGE_HARNESS_JOB)
    status = sub.add_parser("status")
    status.add_argument("--job-id", required=True)
    seal_review = sub.add_parser("seal-p014-review")
    seal_review.add_argument("--job-id", required=True)
    seal_p015 = sub.add_parser("seal-p015-review")
    seal_p015.add_argument("--job-id", required=True)
    cancel = sub.add_parser("cancel")
    cancel.add_argument("--job-id", required=True)
    sub.add_parser("doctor")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = _paths(args)
    try:
        if args.command == "serve":
            worker = StageWorker(
                paths,
                poll_seconds=args.poll_seconds,
                wall_seconds=args.wall_seconds,
            )
            if args.once:
                result = worker.run_once()
                payload = {"processed": result is not None, "result": result}
                print(json.dumps(payload, sort_keys=True))
            else:
                def stop(_signum: int, _frame: object) -> None:
                    worker.stopping = True

                signal.signal(signal.SIGTERM, stop)
                signal.signal(signal.SIGINT, stop)
                worker.serve()
            return 0
        if args.command == "submit":
            result = submit_job(paths, code_revision=args.code_revision, kind=args.kind)
        elif args.command == "status":
            result = read_status(paths, args.job_id)
        elif args.command == "seal-p014-review":
            try:
                submission = json.load(sys.stdin)
            except (OSError, json.JSONDecodeError) as exc:
                raise StageWorkerError(
                    "P014 review submission must be one JSON object on stdin"
                ) from exc
            if not isinstance(submission, Mapping):
                raise StageWorkerError("P014 review submission must be one JSON object on stdin")
            result = seal_p014_review(paths, args.job_id, submission)
        elif args.command == "seal-p015-review":
            try:
                submission = json.load(sys.stdin)
            except (OSError, json.JSONDecodeError) as exc:
                raise StageWorkerError(
                    "P015 review submission must be one JSON object on stdin"
                ) from exc
            if not isinstance(submission, Mapping):
                raise StageWorkerError("P015 review submission must be one JSON object on stdin")
            result = seal_p015_review(paths, args.job_id, submission)
        elif args.command == "cancel":
            result = cancel_job(paths, args.job_id)
        else:
            result = doctor(paths)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0 if result.get("ready", True) is not False else 2
    except StageWorkerError as exc:
        print(f"stage-worker: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
