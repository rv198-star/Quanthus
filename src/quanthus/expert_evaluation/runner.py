"""Deterministic expert-evaluation Job input and immutable Run publication."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from quanthus.expert_evaluation.contracts import ExpertEvaluationSpec
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    canonical_json_bytes,
    decode_artifact,
    digest_value,
    parse_date,
    require_array,
    require_fields,
    require_object,
)

JOB_INPUT_SCHEMA = "quanthus.expert-evaluation-job-input.v1"
RUN_SCHEMA = "quanthus.expert-evaluation-run.v1"
OPERATIONS = {
    "development_evaluation",
    "forward_observation",
    "mature_settlement",
    "bounded_intervention",
}
SKIP_REASONS = {"not_requested", "not_mature", "insufficient_sample", "not_applicable"}
RUN_ARTIFACTS = (
    "evaluation-specs.json",
    "observations.parquet",
    "settlements.parquet",
    "sample-ledger.json",
    "scorecard.json",
)
STAGES = (
    "binding-inputs",
    "building-shared-projection",
    "producing-observations",
    "settling-outcomes",
    "building-scorecard",
    "running-interventions",
    "verifying-artifacts",
    "publishing-run",
)


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ExpertEvaluationError("field_mismatch", f"{field_name} must be non-empty text")
    return value


def _digest(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if len(text) != 71 or not text.startswith("sha256:"):
        raise ExpertEvaluationError("invalid_identity", f"{field_name} must be a sha256 digest")
    try:
        int(text[7:], 16)
    except ValueError as exc:
        raise ExpertEvaluationError(
            "invalid_identity", f"{field_name} must be a sha256 digest"
        ) from exc
    if text.lower() != text:
        raise ExpertEvaluationError("invalid_identity", f"{field_name} must be lowercase")
    return text


def _registry_key(value: object, field_name: str) -> str:
    key = _text(value, field_name)
    path = PurePosixPath(key)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or "\\" in key:
        raise ExpertEvaluationError("path_escape", f"{field_name} is not a registry key")
    return key


@dataclass(frozen=True)
class RegistryArtifactRef:
    artifact_id: str
    version: str
    digest: str
    registry_key: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.artifact_id, self.version, self.digest)

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "version": self.version,
            "digest": self.digest,
            "registry_key": self.registry_key,
        }

    @classmethod
    def from_dict(cls, value: object, field_name: str) -> RegistryArtifactRef:
        raw = require_object(value, field_name)
        require_fields(raw, {"artifact_id", "version", "digest", "registry_key"}, field_name)
        return cls(
            _text(raw["artifact_id"], f"{field_name}.artifact_id"),
            _text(raw["version"], f"{field_name}.version"),
            _digest(raw["digest"], f"{field_name}.digest"),
            _registry_key(raw["registry_key"], f"{field_name}.registry_key"),
        )


@dataclass(frozen=True)
class DwdIdentity:
    base: tuple[str, str, str]
    corrections: tuple[tuple[str, str, str], ...]
    increments: tuple[tuple[str, str, str], ...]
    effective_view_digest: str
    through_date: date

    @staticmethod
    def _component(value: object, field_name: str) -> tuple[str, str, str]:
        raw = require_object(value, field_name)
        require_fields(raw, {"artifact_id", "version", "digest"}, field_name)
        return (
            _text(raw["artifact_id"], f"{field_name}.artifact_id"),
            _text(raw["version"], f"{field_name}.version"),
            _digest(raw["digest"], f"{field_name}.digest"),
        )

    @staticmethod
    def _component_dict(value: tuple[str, str, str]) -> dict[str, str]:
        return {"artifact_id": value[0], "version": value[1], "digest": value[2]}

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self._component_dict(self.base),
            "corrections": [self._component_dict(item) for item in self.corrections],
            "increments": [self._component_dict(item) for item in self.increments],
            "effective_view_digest": self.effective_view_digest,
            "through_date": self.through_date.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: object) -> DwdIdentity:
        raw = require_object(value, "dwd")
        require_fields(
            raw,
            {"base", "corrections", "increments", "effective_view_digest", "through_date"},
            "dwd",
        )
        corrections = tuple(
            cls._component(item, "dwd.corrections[]")
            for item in require_array(raw["corrections"], "dwd.corrections")
        )
        increments = tuple(
            cls._component(item, "dwd.increments[]")
            for item in require_array(raw["increments"], "dwd.increments")
        )
        for name, items in (("corrections", corrections), ("increments", increments)):
            if len(items) != len(set(items)):
                raise ExpertEvaluationError("duplicate_identity", f"dwd.{name} contains duplicates")
        return cls(
            cls._component(raw["base"], "dwd.base"),
            corrections,
            increments,
            _digest(raw["effective_view_digest"], "dwd.effective_view_digest"),
            parse_date(raw["through_date"], "dwd.through_date"),
        )


@dataclass(frozen=True)
class PartitionBinding:
    artifact_id: str
    version: str
    digest: str
    decision_start: date
    decision_end: date

    def __post_init__(self) -> None:
        if self.decision_end < self.decision_start:
            raise ExpertEvaluationError(
                "invalid_partition_bounds", "partition decision_end precedes decision_start"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "version": self.version,
            "digest": self.digest,
            "decision_start": self.decision_start.isoformat(),
            "decision_end": self.decision_end.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: object) -> PartitionBinding:
        raw = require_object(value, "partition")
        require_fields(
            raw,
            {"artifact_id", "version", "digest", "decision_start", "decision_end"},
            "partition",
        )
        return cls(
            _text(raw["artifact_id"], "partition.artifact_id"),
            _text(raw["version"], "partition.version"),
            _digest(raw["digest"], "partition.digest"),
            parse_date(raw["decision_start"], "partition.decision_start"),
            parse_date(raw["decision_end"], "partition.decision_end"),
        )


@dataclass(frozen=True)
class FormalStrategyRef:
    strategy_id: str
    strategy_version: str
    card_digest: str
    attribution_plan_digest: str
    registry_key: str

    def to_dict(self) -> dict[str, str]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "card_digest": self.card_digest,
            "attribution_plan_digest": self.attribution_plan_digest,
            "registry_key": self.registry_key,
        }

    @classmethod
    def from_dict(cls, value: object) -> FormalStrategyRef:
        raw = require_object(value, "formal_strategy")
        require_fields(
            raw,
            {
                "strategy_id",
                "strategy_version",
                "card_digest",
                "attribution_plan_digest",
                "registry_key",
            },
            "formal_strategy",
        )
        return cls(
            _text(raw["strategy_id"], "formal_strategy.strategy_id"),
            _text(raw["strategy_version"], "formal_strategy.strategy_version"),
            _digest(raw["card_digest"], "formal_strategy.card_digest"),
            _digest(raw["attribution_plan_digest"], "formal_strategy.attribution_plan_digest"),
            _registry_key(raw["registry_key"], "formal_strategy.registry_key"),
        )


@dataclass(frozen=True)
class ProducerBinding:
    producer_id: str
    producer_version: str
    configuration_digest: str

    def to_dict(self) -> dict[str, str]:
        return {
            "producer_id": self.producer_id,
            "producer_version": self.producer_version,
            "configuration_digest": self.configuration_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> ProducerBinding:
        raw = require_object(value, "producer")
        require_fields(raw, {"producer_id", "producer_version", "configuration_digest"}, "producer")
        return cls(
            _text(raw["producer_id"], "producer.producer_id"),
            _text(raw["producer_version"], "producer.producer_version"),
            _digest(raw["configuration_digest"], "producer.configuration_digest"),
        )


@dataclass(frozen=True)
class ExpertEvaluationJobInput:
    operation: str
    evaluation_specs: tuple[RegistryArtifactRef, ...]
    formal_strategy: FormalStrategyRef | None
    dwd: DwdIdentity
    partition: PartitionBinding
    requested_interventions: tuple[str, ...]
    expected_parent_run_digests: tuple[str, ...]
    producer: ProducerBinding

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS:
            raise ExpertEvaluationError("unsupported_operation", "operation is not admitted")
        if not self.evaluation_specs:
            raise ExpertEvaluationError("empty_spec_set", "evaluation_specs must not be empty")
        ordered = tuple(sorted(self.evaluation_specs, key=lambda item: item.identity))
        if ordered != self.evaluation_specs:
            raise ExpertEvaluationError("non_canonical_order", "evaluation_specs must be sorted")
        if len({item.identity for item in ordered}) != len(ordered):
            raise ExpertEvaluationError(
                "duplicate_identity", "evaluation_specs contains duplicates"
            )
        if len(set(self.requested_interventions)) != len(self.requested_interventions):
            raise ExpertEvaluationError(
                "duplicate_identity", "requested_interventions has duplicates"
            )
        if len(set(self.expected_parent_run_digests)) != len(self.expected_parent_run_digests):
            raise ExpertEvaluationError(
                "duplicate_identity", "expected parent Runs contain duplicates"
            )
        if self.partition.decision_end > self.dwd.through_date:
            raise ExpertEvaluationError(
                "partition_outside_dwd", "partition decision_end exceeds DWD through_date"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": JOB_INPUT_SCHEMA,
            "operation": self.operation,
            "evaluation_specs": [item.to_dict() for item in self.evaluation_specs],
            "formal_strategy": self.formal_strategy.to_dict() if self.formal_strategy else None,
            "dwd": self.dwd.to_dict(),
            "partition": self.partition.to_dict(),
            "requested_interventions": list(self.requested_interventions),
            "expected_parent_run_digests": list(self.expected_parent_run_digests),
            "producer": self.producer.to_dict(),
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @property
    def input_digest(self) -> str:
        return digest_value(self.to_dict())

    @property
    def idempotency_key(self) -> str:
        return f"expert-evaluation:{self.input_digest[7:]}"

    @classmethod
    def from_dict(cls, value: object) -> ExpertEvaluationJobInput:
        raw = require_object(value, "expert evaluation Job input")
        require_fields(
            raw,
            {
                "schema_version",
                "operation",
                "evaluation_specs",
                "formal_strategy",
                "dwd",
                "partition",
                "requested_interventions",
                "expected_parent_run_digests",
                "producer",
            },
            "expert evaluation Job input",
        )
        if raw["schema_version"] != JOB_INPUT_SCHEMA:
            raise ExpertEvaluationError("unsupported_schema", "Job input schema is not admitted")
        specs = tuple(
            RegistryArtifactRef.from_dict(item, "evaluation_specs[]")
            for item in require_array(raw["evaluation_specs"], "evaluation_specs")
        )
        interventions = tuple(
            _text(item, "requested_interventions[]")
            for item in require_array(raw["requested_interventions"], "requested_interventions")
        )
        parents = tuple(
            _digest(item, "expected_parent_run_digests[]")
            for item in require_array(
                raw["expected_parent_run_digests"], "expected_parent_run_digests"
            )
        )
        formal = (
            None
            if raw["formal_strategy"] is None
            else FormalStrategyRef.from_dict(raw["formal_strategy"])
        )
        return cls(
            _text(raw["operation"], "operation"),
            specs,
            formal,
            DwdIdentity.from_dict(raw["dwd"]),
            PartitionBinding.from_dict(raw["partition"]),
            interventions,
            parents,
            ProducerBinding.from_dict(raw["producer"]),
        )


@dataclass(frozen=True)
class ProducedArtifact:
    path: str
    payload: bytes | None
    row_count: int | None = None
    skip_reason: str | None = None

    def __post_init__(self) -> None:
        _registry_key(self.path, "produced artifact path")
        if (self.payload is None) == (self.skip_reason is None):
            raise ExpertEvaluationError(
                "invalid_artifact_state", "artifact must be available or have one skip reason"
            )
        if self.skip_reason is not None and self.skip_reason not in SKIP_REASONS:
            raise ExpertEvaluationError("invalid_skip_reason", "artifact skip reason is invalid")
        if self.row_count is not None and self.row_count < 0:
            raise ExpertEvaluationError("invalid_row_count", "row_count must be non-negative")


class OperationProducer(Protocol):
    def produce(
        self,
        job_input: ExpertEvaluationJobInput,
        specs: tuple[ExpertEvaluationSpec, ...],
        progress: Callable[[str, Mapping[str, Any]], None],
        canceled: Callable[[], bool],
    ) -> tuple[ProducedArtifact, ...]: ...


class FormalStrategyResolver(Protocol):
    def expected_bindings(
        self, strategy: FormalStrategyRef, registry_root: Path
    ) -> tuple[tuple[tuple[str, str, str], ...], tuple[str, ...]]: ...


def resolve_registry_path(root: Path, registry_key: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / _registry_key(registry_key, "registry_key")).resolve()
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise ExpertEvaluationError("path_escape", "registry key escapes configured root")
    if not candidate.is_file():
        raise ExpertEvaluationError("registry_artifact_missing", "registry artifact is unavailable")
    return candidate


def bind_evaluation_specs(
    job_input: ExpertEvaluationJobInput,
    registry_root: Path,
    formal_strategy_resolver: FormalStrategyResolver | None = None,
) -> tuple[ExpertEvaluationSpec, ...]:
    specs: list[ExpertEvaluationSpec] = []
    for reference in job_input.evaluation_specs:
        payload = resolve_registry_path(registry_root, reference.registry_key).read_bytes()
        spec = decode_artifact(payload, ExpertEvaluationSpec)
        if (
            spec.assessment_id,
            spec.assessment_version,
            spec.digest,
        ) != reference.identity:
            raise ExpertEvaluationError("registry_drift", "evaluation spec identity differs")
        partition = spec.partition
        if (partition.artifact_id, partition.version, partition.digest) != (
            job_input.partition.artifact_id,
            job_input.partition.version,
            job_input.partition.digest,
        ):
            raise ExpertEvaluationError("partition_mismatch", "evaluation spec partition differs")
        specs.append(spec)
    if job_input.formal_strategy is not None:
        if formal_strategy_resolver is None:
            raise ExpertEvaluationError(
                "formal_strategy_resolver_unavailable",
                "formal strategy closure cannot be verified by this deployment",
            )
        expected_specs, admitted_interventions = formal_strategy_resolver.expected_bindings(
            job_input.formal_strategy, registry_root
        )
        observed = tuple(reference.identity for reference in job_input.evaluation_specs)
        if observed != expected_specs:
            raise ExpertEvaluationError(
                "formal_strategy_spec_mismatch", "formal strategy spec closure differs"
            )
        if any(item not in admitted_interventions for item in job_input.requested_interventions):
            raise ExpertEvaluationError(
                "intervention_not_admitted", "requested intervention is not in strategy card"
            )
    elif job_input.requested_interventions:
        raise ExpertEvaluationError(
            "intervention_not_admitted", "interventions require a formal strategy card"
        )
    return tuple(specs)


@dataclass(frozen=True)
class ExpertEvaluationRun:
    run_id: str
    root: Path
    manifest_digest: str
    manifest: dict[str, Any]

    @property
    def summary(self) -> dict[str, Any]:
        value = self.manifest["summary"]
        assert isinstance(value, dict)
        return dict(value)


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _artifact_entry(artifact: ProducedArtifact) -> dict[str, Any]:
    if artifact.payload is None:
        return {
            "path": artifact.path,
            "state": "skipped",
            "skip_reason": artifact.skip_reason,
            "sha256": None,
            "size_bytes": None,
            "row_count": artifact.row_count,
        }
    return {
        "path": artifact.path,
        "state": "available",
        "skip_reason": None,
        "sha256": _sha256(artifact.payload),
        "size_bytes": len(artifact.payload),
        "row_count": artifact.row_count,
    }


def _stage_entry(stage: str, artifacts: Sequence[ProducedArtifact]) -> dict[str, Any]:
    if stage == "running-interventions" and not artifacts:
        return {"stage": stage, "state": "skipped", "skip_reason": "not_requested"}
    available = any(item.payload is not None for item in artifacts)
    reasons = {item.skip_reason for item in artifacts if item.skip_reason is not None}
    return {
        "stage": stage,
        "state": "completed" if available or not artifacts else "skipped",
        "skip_reason": None if available or not artifacts else sorted(reasons)[0],
    }


def run_evaluation(
    job_input: ExpertEvaluationJobInput,
    *,
    registry_root: Path,
    staging: Path,
    run_root: Path,
    producer: OperationProducer,
    progress: Callable[[str, Mapping[str, Any]], None] = lambda _stage, _detail: None,
    canceled: Callable[[], bool] = lambda: False,
    formal_strategy_resolver: FormalStrategyResolver | None = None,
) -> ExpertEvaluationRun:
    def emit(stage: str, detail: Mapping[str, Any]) -> None:
        progress(stage, detail)
        if canceled():
            raise ExpertEvaluationError("canceled", "evaluation was canceled")

    emit("binding-inputs", {})
    specs = bind_evaluation_specs(job_input, registry_root, formal_strategy_resolver)
    emit("building-shared-projection", {"spec_count": len(specs)})
    produced = producer.produce(job_input, specs, emit, canceled)
    by_path = {item.path: item for item in produced}
    if len(by_path) != len(produced):
        raise ExpertEvaluationError("duplicate_artifact", "producer returned duplicate paths")
    expected = set(RUN_ARTIFACTS[1:])
    admitted = expected.union(
        {f"interventions/{item}.json" for item in job_input.requested_interventions}
    )
    if set(by_path).difference(admitted):
        raise ExpertEvaluationError(
            "unexpected_artifact", "producer returned an undeclared artifact"
        )
    for path in RUN_ARTIFACTS[1:]:
        if path not in by_path:
            raise ExpertEvaluationError("missing_artifact_state", f"producer omitted {path}")
    expected_interventions = {
        f"interventions/{item}.json" for item in job_input.requested_interventions
    }
    if {path for path in by_path if path.startswith("interventions/")} != expected_interventions:
        raise ExpertEvaluationError("intervention_set_mismatch", "intervention artifacts differ")
    if canceled():
        raise ExpertEvaluationError("canceled", "evaluation was canceled")
    if staging.exists():
        if any(staging.iterdir()):
            raise ExpertEvaluationError("staging_not_empty", "staging directory is not empty")
    else:
        staging.mkdir(parents=True)
    spec_payload = canonical_json_bytes([spec.to_dict() for spec in specs])
    complete = (ProducedArtifact("evaluation-specs.json", spec_payload, len(specs)), *produced)
    for artifact in complete:
        if artifact.payload is not None:
            _write(staging / artifact.path, artifact.payload)
    emit("verifying-artifacts", {"artifact_count": len(complete)})
    stage_artifacts = {
        "binding-inputs": [complete[0]],
        "building-shared-projection": [],
        "producing-observations": [by_path["observations.parquet"]],
        "settling-outcomes": [by_path["settlements.parquet"]],
        "building-scorecard": [by_path["sample-ledger.json"], by_path["scorecard.json"]],
        "running-interventions": [
            item for item in produced if item.path.startswith("interventions/")
        ],
        "verifying-artifacts": [],
        "publishing-run": [],
    }
    manifest = {
        "schema_version": RUN_SCHEMA,
        "job_input": job_input.to_dict(),
        "evaluation_identities": [
            {
                "assessment_id": spec.assessment_id,
                "assessment_version": spec.assessment_version,
                "digest": spec.digest,
            }
            for spec in specs
        ],
        "strategy_identity": (
            {
                "strategy_id": job_input.formal_strategy.strategy_id,
                "strategy_version": job_input.formal_strategy.strategy_version,
                "card_digest": job_input.formal_strategy.card_digest,
                "attribution_plan_digest": job_input.formal_strategy.attribution_plan_digest,
            }
            if job_input.formal_strategy
            else None
        ),
        "stages": [_stage_entry(stage, stage_artifacts[stage]) for stage in STAGES],
        "artifacts": [_artifact_entry(item) for item in complete],
        "summary": {
            "operation": job_input.operation,
            "spec_count": len(specs),
            "available_artifact_count": sum(item.payload is not None for item in complete),
            "skipped_artifact_count": sum(item.payload is None for item in complete),
            "intervention_count": len(job_input.requested_interventions),
        },
    }
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_digest = _sha256(manifest_bytes)
    run_id = f"expert_run_{manifest_digest[7:]}"
    _write(staging / "run.json", manifest_bytes)
    checksum_paths = sorted(
        item.path for item in complete if item.payload is not None
    ) + ["run.json"]
    checksum_payload = "".join(
        f"{_sha256((staging / path).read_bytes())}  {path}\n" for path in sorted(checksum_paths)
    ).encode("ascii")
    _write(staging / "CHECKSUMS.sha256", checksum_payload)
    verify_expert_evaluation_run(staging, expected_run_id=run_id, require_ready=False)
    if canceled():
        raise ExpertEvaluationError("canceled", "evaluation was canceled")
    _write(staging / "READY", (manifest_digest + "\n").encode("ascii"))
    emit("publishing-run", {"run_id": run_id})
    final = run_root.resolve() / run_id
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        existing = verify_expert_evaluation_run(final, expected_run_id=run_id)
        shutil.rmtree(staging)
        return existing
    try:
        os.replace(staging, final)
    except OSError:
        if not final.exists():
            raise
        existing = verify_expert_evaluation_run(final, expected_run_id=run_id)
        shutil.rmtree(staging)
        return existing
    return verify_expert_evaluation_run(final, expected_run_id=run_id)


def verify_expert_evaluation_run(
    root: Path, *, expected_run_id: str | None = None, require_ready: bool = True
) -> ExpertEvaluationRun:
    if root.is_symlink():
        raise ExpertEvaluationError("path_escape", "Run directory must not be a symlink")
    resolved = root.resolve()
    if not resolved.is_dir():
        raise ExpertEvaluationError("sealed_run_unreadable", "Run directory is unavailable")
    try:
        manifest_bytes = (resolved / "run.json").read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExpertEvaluationError("sealed_run_unreadable", "run.json is unreadable") from exc
    if canonical_json_bytes(manifest) != manifest_bytes or not isinstance(manifest, dict):
        raise ExpertEvaluationError("non_canonical_encoding", "run.json is not canonical")
    require_fields(
        manifest,
        {
            "schema_version",
            "job_input",
            "evaluation_identities",
            "strategy_identity",
            "stages",
            "artifacts",
            "summary",
        },
        "run.json",
    )
    if manifest["schema_version"] != RUN_SCHEMA:
        raise ExpertEvaluationError("unsupported_schema", "Run schema is not admitted")
    job_input = ExpertEvaluationJobInput.from_dict(manifest["job_input"])
    del job_input
    manifest_digest = _sha256(manifest_bytes)
    run_id = f"expert_run_{manifest_digest[7:]}"
    if expected_run_id is not None and expected_run_id != run_id:
        raise ExpertEvaluationError("sealed_run_manifest_mismatch", "Run id differs")
    stages = require_array(manifest["stages"], "run.json.stages")
    if [item.get("stage") for item in stages if isinstance(item, dict)] != list(STAGES):
        raise ExpertEvaluationError("stage_manifest_mismatch", "Run stages differ")
    artifacts = require_array(manifest["artifacts"], "run.json.artifacts")
    available: dict[str, str] = {}
    declared_paths: set[str] = set()
    for value in artifacts:
        item = require_object(value, "run.json.artifacts[]")
        require_fields(
            item,
            {"path", "state", "skip_reason", "sha256", "size_bytes", "row_count"},
            "run.json.artifacts[]",
        )
        path_text = _registry_key(item["path"], "run.json.artifacts[].path")
        if path_text in declared_paths:
            raise ExpertEvaluationError("duplicate_artifact", "Run artifact path is duplicated")
        declared_paths.add(path_text)
        raw_artifact_path = resolved / path_text
        artifact_path = raw_artifact_path.resolve()
        if resolved not in artifact_path.parents or raw_artifact_path.is_symlink():
            raise ExpertEvaluationError("path_escape", "Run artifact escapes its directory")
        if item["state"] == "available":
            if item["skip_reason"] is not None:
                raise ExpertEvaluationError("invalid_artifact_state", "available artifact has skip")
            payload = artifact_path.read_bytes()
            observed = _sha256(payload)
            if observed != item["sha256"] or len(payload) != item["size_bytes"]:
                raise ExpertEvaluationError("artifact_checksum_mismatch", "Run artifact differs")
            if path_text == "evaluation-specs.json":
                try:
                    spec_count = len(json.loads(payload))
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
                    raise ExpertEvaluationError(
                        "artifact_row_count_mismatch", "evaluation specs are unreadable"
                    ) from exc
                if spec_count != item["row_count"]:
                    raise ExpertEvaluationError(
                        "artifact_row_count_mismatch", "evaluation spec count differs"
                    )
            elif path_text.endswith(".parquet"):
                try:
                    import pyarrow.parquet as pq  # type: ignore[import-untyped]

                    parquet_rows = pq.read_metadata(artifact_path).num_rows
                except Exception as exc:
                    raise ExpertEvaluationError(
                        "artifact_row_count_mismatch", "Parquet metadata is unreadable"
                    ) from exc
                if parquet_rows != item["row_count"]:
                    raise ExpertEvaluationError(
                        "artifact_row_count_mismatch", "Parquet row count differs"
                    )
            available[path_text] = observed
        elif item["state"] == "skipped":
            if item["skip_reason"] not in SKIP_REASONS or artifact_path.exists():
                raise ExpertEvaluationError("invalid_artifact_state", "skipped artifact is invalid")
        else:
            raise ExpertEvaluationError("invalid_artifact_state", "artifact state is invalid")
    if not set(RUN_ARTIFACTS).issubset(declared_paths):
        raise ExpertEvaluationError("missing_artifact_state", "Run artifact states are incomplete")
    available["run.json"] = manifest_digest
    try:
        checksum_lines = (resolved / "CHECKSUMS.sha256").read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise ExpertEvaluationError(
            "checksum_inventory_mismatch", "checksums are unreadable"
        ) from exc
    expected_lines = [f"{digest}  {path}" for path, digest in sorted(available.items())]
    if checksum_lines != expected_lines:
        raise ExpertEvaluationError("checksum_inventory_mismatch", "checksums differ")
    physical = {
        path.relative_to(resolved).as_posix()
        for path in resolved.rglob("*")
        if path.is_file()
    }
    expected_physical = set(available).union({"CHECKSUMS.sha256"})
    if require_ready:
        try:
            ready = (resolved / "READY").read_text(encoding="ascii")
        except OSError as exc:
            raise ExpertEvaluationError("run_not_ready", "READY is unavailable") from exc
        if ready != manifest_digest + "\n":
            raise ExpertEvaluationError("run_not_ready", "READY does not bind run.json")
        expected_physical.add("READY")
    if physical != expected_physical:
        raise ExpertEvaluationError("artifact_inventory_mismatch", "physical Run files differ")
    return ExpertEvaluationRun(run_id, resolved, manifest_digest, manifest)


class ProtocolFixtureProducer:
    """Deterministic infrastructure fixture; it is never formal strategy evidence."""

    PRODUCER_ID = "fixture.expert-evaluation-protocol"
    PRODUCER_VERSION = "1.0.0"
    CONFIGURATION_DIGEST = digest_value({"fixture": PRODUCER_ID, "version": PRODUCER_VERSION})

    @staticmethod
    def _parquet(columns: Mapping[str, Sequence[str]]) -> bytes:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ExpertEvaluationError(
                "producer_dependency_unavailable", "protocol fixture requires pyarrow"
            ) from exc
        table = pa.table(dict(columns))
        sink = pa.BufferOutputStream()
        pq.write_table(
            table,
            sink,
            compression="NONE",
            use_dictionary=False,
            write_statistics=False,
            data_page_version="1.0",
        )
        return bytes(sink.getvalue().to_pybytes())

    def produce(
        self,
        job_input: ExpertEvaluationJobInput,
        specs: tuple[ExpertEvaluationSpec, ...],
        progress: Callable[[str, Mapping[str, Any]], None],
        canceled: Callable[[], bool],
    ) -> tuple[ProducedArtifact, ...]:
        if job_input.producer != ProducerBinding(
            self.PRODUCER_ID, self.PRODUCER_VERSION, self.CONFIGURATION_DIGEST
        ):
            raise ExpertEvaluationError("producer_binding_mismatch", "fixture producer differs")
        if canceled():
            raise ExpertEvaluationError("canceled", "evaluation was canceled")
        progress("producing-observations", {"fixture": True})
        observations = self._parquet(
            {
                "spec_digest": [spec.digest for spec in specs],
                "fixture_state": ["observed"] * len(specs),
            }
        )
        progress("settling-outcomes", {"fixture": True})
        settlements = self._parquet(
            {
                "spec_digest": [spec.digest for spec in specs],
                "fixture_state": ["settled"] * len(specs),
            }
        )
        progress("building-scorecard", {"fixture": True})
        ledger = canonical_json_bytes(
            {
                "schema_version": "quanthus.expert-sample-ledger-fixture.v1",
                "sample_count": len(specs),
            }
        )
        scorecard = canonical_json_bytes(
            {
                "schema_version": "quanthus.expert-scorecard-fixture.v1",
                "formal_strategy_evidence": False,
                "spec_count": len(specs),
            }
        )
        progress(
            "running-interventions",
            {"state": "not_requested" if not job_input.requested_interventions else "fixture"},
        )
        interventions = tuple(
            ProducedArtifact(
                f"interventions/{item}.json",
                canonical_json_bytes(
                    {"schema_version": "quanthus.expert-intervention-fixture.v1", "id": item}
                ),
                1,
            )
            for item in job_input.requested_interventions
        )
        return (
            ProducedArtifact("observations.parquet", observations, len(specs)),
            ProducedArtifact("settlements.parquet", settlements, len(specs)),
            ProducedArtifact("sample-ledger.json", ledger, len(specs)),
            ProducedArtifact("scorecard.json", scorecard, len(specs)),
            *interventions,
        )


def fixture_producer_for(job_input: ExpertEvaluationJobInput) -> ProtocolFixtureProducer:
    producer = ProtocolFixtureProducer()
    if job_input.producer.producer_id != producer.PRODUCER_ID:
        raise ExpertEvaluationError("producer_not_admitted", "operation producer is not admitted")
    return producer
