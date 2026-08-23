"""Immutable MVP acceptance, lifecycle publication, and forward-Shadow admission."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from quanthus.expert_evaluation.contracts import ArtifactContract, ExpertLifecycleDecision
from quanthus.expert_evaluation.release_verification import verify_formal_attribution_release
from quanthus.expert_evaluation.runner import ExpertEvaluationRun, verify_expert_evaluation_run
from quanthus.expert_evaluation.serialization import (
    ExpertEvaluationError,
    artifact_from_bytes,
    digest_value,
    parse_canonical_object,
)
from quanthus.platform.jobs import DurableJobQueue
from quanthus.platform.models import JobRecord
from quanthus.platform.persistence import PlatformStore

ACCEPTANCE_PACKET_SCHEMA = "quanthus.expert-mvp-acceptance-packet.v1"
SHADOW_SCHEDULE_SCHEMA = "quanthus.expert-forward-shadow-schedule.v1"
FORWARD_SHADOW_JOB_KIND = "expert-forward-shadow"


class ExpertLifecycleError(RuntimeError):
    """Typed fail-closed lifecycle or Shadow handoff error."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}")


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExpertLifecycleError("lifecycle_schema_invalid", f"{field} must be an object")
    return value


def _array(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ExpertLifecycleError("lifecycle_schema_invalid", f"{field} must be an array")
    return value


def _exact(value: Mapping[str, Any], fields: set[str], field: str) -> None:
    if set(value) != fields:
        raise ExpertLifecycleError("lifecycle_schema_invalid", f"{field} fields differ")


def _read_canonical(path: Path, reason_code: str) -> dict[str, Any]:
    try:
        return parse_canonical_object(path.read_bytes())
    except (OSError, ExpertEvaluationError) as exc:
        raise ExpertLifecycleError(reason_code, f"{path.name} is unreadable or invalid") from exc


def acceptance_packet_digest(packet: Mapping[str, Any]) -> str:
    semantic = dict(packet)
    semantic.pop("digest", None)
    return digest_value(semantic)


def verify_acceptance_packet(path: Path, artifact_root: Path) -> dict[str, Any]:
    """Verify one deterministic packet and both independent artifact publications."""

    packet = _read_canonical(path, "acceptance_packet_unreadable")
    _exact(
        packet,
        {
            "schema_version",
            "packet_id",
            "strategy",
            "development_partition",
            "dwd",
            "artifacts",
            "reproductions",
            "read_audit",
            "resource_audit",
            "stop_condition_disposition",
            "rollback",
            "non_authority",
            "digest",
        },
        "acceptance packet",
    )
    if (
        packet["schema_version"] != ACCEPTANCE_PACKET_SCHEMA
        or packet["digest"] != acceptance_packet_digest(packet)
    ):
        raise ExpertLifecycleError("acceptance_packet_digest_mismatch", "packet digest differs")

    strategy = _object(packet["strategy"], "strategy")
    _exact(strategy, {"strategy_id", "strategy_version", "strategy_digest"}, "strategy")
    partition = _object(packet["development_partition"], "development_partition")
    _exact(
        partition,
        {"partition_id", "partition_version", "partition_digest", "start", "end"},
        "development_partition",
    )
    if partition["partition_id"] != "partition.development-2020-2021":
        raise ExpertLifecycleError("sealed_partition_access", "packet is not development-only")

    audits = _array(packet["read_audit"], "read_audit")
    if not audits:
        raise ExpertLifecycleError("read_audit_incomplete", "read audit is empty")
    for raw in audits:
        audit = _object(raw, "read_audit[]")
        _exact(
            audit,
            {"operation", "partition_id", "first_date", "last_date", "sealed_access"},
            "read_audit[]",
        )
        if (
            audit["partition_id"] != partition["partition_id"]
            or audit["first_date"] < partition["start"]
            or audit["last_date"] > partition["end"]
            or audit["sealed_access"] is not False
        ):
            raise ExpertLifecycleError("sealed_partition_access", "read audit crosses development")

    non_authority = _object(packet["non_authority"], "non_authority")
    _exact(
        non_authority,
        {"execution_authorized", "promotion", "notification", "order", "fill"},
        "non_authority",
    )
    if any(value is not False for value in non_authority.values()):
        raise ExpertLifecycleError("execution_authority_present", "packet adds forbidden authority")

    artifacts = _object(packet["artifacts"], "artifacts")
    _exact(
        artifacts,
        {
            "evaluation_run_id",
            "evaluation_manifest_digest",
            "formal_scorecard_digest",
            "assessment_run_id",
            "assessment_manifest_digest",
            "assessment_scorecard_digest",
            "attribution_release_id",
            "attribution_release_digest",
            "pipeline_evaluation_digest",
            "topology_digest",
            "source_product_package_digest",
        },
        "artifacts",
    )
    reproductions = _object(packet["reproductions"], "reproductions")
    _exact(
        reproductions,
        {"formal_root", "assessment_root", "semantic_match"},
        "reproductions",
    )
    if reproductions["semantic_match"] is not True:
        raise ExpertLifecycleError("reproduction_mismatch", "semantic match is not asserted")

    primary_run = _verify_run(artifact_root, str(artifacts["evaluation_run_id"]))
    assessment_run = _verify_run(artifact_root, str(artifacts["assessment_run_id"]))
    formal_reproduction = _verify_run(
        artifact_root / str(reproductions["formal_root"]),
        str(artifacts["evaluation_run_id"]),
    )
    assessment_reproduction = _verify_run(
        artifact_root / str(reproductions["assessment_root"]),
        str(artifacts["assessment_run_id"]),
    )
    for run, manifest_field, scorecard_field in (
        (primary_run, "evaluation_manifest_digest", "formal_scorecard_digest"),
        (assessment_run, "assessment_manifest_digest", "assessment_scorecard_digest"),
        (formal_reproduction, "evaluation_manifest_digest", "formal_scorecard_digest"),
        (assessment_reproduction, "assessment_manifest_digest", "assessment_scorecard_digest"),
    ):
        if run.manifest_digest != artifacts[manifest_field]:
            raise ExpertLifecycleError("reproduction_mismatch", "Run manifest digest differs")
        run_partition = _object(run.manifest["job_input"], "job_input")["partition"]
        if (
            run_partition["artifact_id"] != partition["partition_id"]
            or run_partition["digest"] != partition["partition_digest"]
            or run_partition["decision_start"] != partition["start"]
            or run_partition["decision_end"] > partition["end"]
        ):
            raise ExpertLifecycleError("sealed_partition_access", "Run partition differs")
        scorecard_bytes = (run.root / "scorecard.json").read_bytes()
        scorecard = json.loads(scorecard_bytes)
        if "digest" in scorecard:
            try:
                scorecard_artifact = artifact_from_bytes(scorecard_bytes)
                if not isinstance(scorecard_artifact, ArtifactContract):
                    raise ExpertEvaluationError(
                        "unsupported_schema", "scorecard is not an artifact contract"
                    )
                observed_scorecard_digest = scorecard_artifact.digest
            except ExpertEvaluationError as exc:
                raise ExpertLifecycleError(
                    "reproduction_mismatch", "assessment scorecard is invalid"
                ) from exc
        else:
            observed_scorecard_digest = digest_value(scorecard)
        if observed_scorecard_digest != artifacts[scorecard_field]:
            raise ExpertLifecycleError("reproduction_mismatch", "scorecard digest differs")
        if (
            "sealed_partition_access" in scorecard
            and scorecard["sealed_partition_access"] is not False
        ):
            raise ExpertLifecycleError("sealed_partition_access", "scorecard accessed sealed data")

    primary_release = _verify_release(artifact_root, str(artifacts["attribution_release_id"]))
    reproduced_release = _verify_release(
        artifact_root / str(reproductions["formal_root"]),
        str(artifacts["attribution_release_id"]),
    )
    for release in (primary_release, reproduced_release):
        if (
            release["release_digest"] != artifacts["attribution_release_digest"]
            or release["evaluation_bundle_digest"] != artifacts["pipeline_evaluation_digest"]
            or release.get("sealed_partition_access") is not False
        ):
            raise ExpertLifecycleError("reproduction_mismatch", "attribution release differs")
    return packet


def _verify_run(root: Path, run_id: str) -> ExpertEvaluationRun:
    try:
        return verify_expert_evaluation_run(root / "runs" / run_id, expected_run_id=run_id)
    except ExpertEvaluationError as exc:
        raise ExpertLifecycleError(exc.reason_code, "expert evaluation Run differs") from exc


def _verify_release(root: Path, release_id: str) -> dict[str, Any]:
    try:
        return verify_formal_attribution_release(root / "attribution-releases" / release_id)
    except ExpertEvaluationError as exc:
        raise ExpertLifecycleError(exc.reason_code, "formal attribution release differs") from exc


def publish_lifecycle_decision(path: Path, decision: ExpertLifecycleDecision) -> bool:
    """Append one canonical decision; exact retries are idempotent and conflicts fail."""

    payload = decision.canonical_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        if path.read_bytes() == payload:
            return False
        raise ExpertLifecycleError(
            "lifecycle_append_conflict", "decision path already contains different bytes"
        ) from None
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    return True


def load_shadow_schedule(path: Path) -> dict[str, Any]:
    schedule = _read_canonical(path, "shadow_schedule_unreadable")
    _exact(
        schedule,
        {"schema_version", "config_version", "bindings", "disable_receipts", "digest"},
        "shadow schedule",
    )
    semantic = dict(schedule)
    observed_digest = semantic.pop("digest")
    if schedule["schema_version"] != SHADOW_SCHEDULE_SCHEMA or observed_digest != digest_value(
        semantic
    ):
        raise ExpertLifecycleError("shadow_schedule_digest_mismatch", "schedule digest differs")
    identities: set[tuple[str, str]] = set()
    for raw in _array(schedule["bindings"], "bindings"):
        binding = _object(raw, "bindings[]")
        _exact(
            binding,
            {
                "strategy_id",
                "strategy_version",
                "decision_digest",
                "operations",
                "observation_budget",
            },
            "bindings[]",
        )
        identity = (binding["strategy_id"], binding["strategy_version"])
        if identity in identities or binding["observation_budget"] <= 0:
            raise ExpertLifecycleError("shadow_schedule_invalid", "enabled binding differs")
        identities.add(identity)
    for raw in _array(schedule["disable_receipts"], "disable_receipts"):
        receipt = _object(raw, "disable_receipts[]")
        _exact(
            receipt,
            {"strategy_id", "strategy_version", "decision_digest", "reason_code"},
            "disable_receipts[]",
        )
        if (receipt["strategy_id"], receipt["strategy_version"]) in identities:
            raise ExpertLifecycleError(
                "shadow_schedule_invalid", "identity is enabled and disabled"
            )
    return schedule


def submit_forward_shadow(
    *,
    schedule_path: Path,
    store: PlatformStore,
    strategy_id: str,
    strategy_version: str,
    operation: str,
    observation_date: str,
    dwd_release_digest: str,
    actor_user_id: str,
) -> tuple[JobRecord, bool]:
    """Admit an enabled identity before the only PostgreSQL insertion point."""

    schedule = load_shadow_schedule(schedule_path)
    binding = next(
        (
            item
            for item in schedule["bindings"]
            if item["strategy_id"] == strategy_id
            and item["strategy_version"] == strategy_version
        ),
        None,
    )
    if binding is None:
        raise ExpertLifecycleError(
            "shadow_binding_disabled", "strategy version is absent from the enabled schedule"
        )
    if operation not in binding["operations"]:
        raise ExpertLifecycleError("shadow_operation_not_admitted", "operation is not admitted")
    input_ref = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "decision_digest": binding["decision_digest"],
        "operation": operation,
        "observation_date": observation_date,
        "dwd_release_digest": dwd_release_digest,
    }
    key = digest_value(input_ref)[7:]
    queue = DurableJobQueue(store, {FORWARD_SHADOW_JOB_KIND: "expert"})
    return queue.submit(
        kind=FORWARD_SHADOW_JOB_KIND,
        actor_user_id=actor_user_id,
        idempotency_key=key,
        input_ref=input_ref,
        concurrency_key=f"{strategy_id}@{strategy_version}:{operation}:{observation_date}",
    )


def advance_product_binding(
    config_path: Path, *, package: Mapping[str, Any], expected_previous_digest: str
) -> str:
    """Append a package and atomically advance only its exact candidate binding."""

    config = json.loads(config_path.read_bytes())
    candidate = _object(package.get("candidate"), "package.candidate")
    package_digest = package.get("package_digest")
    semantic = dict(package)
    semantic.pop("package_digest", None)
    if package_digest != digest_value(semantic):
        raise ExpertLifecycleError("expert_evidence_package_digest_mismatch", "package differs")
    binding = next(
        (
            item
            for item in config["bindings"]
            if item["strategy_id"] == candidate["strategy_id"]
            and item["strategy_version"] == candidate["strategy_version"]
        ),
        None,
    )
    if binding is None or binding["package_digest"] != expected_previous_digest:
        raise ExpertLifecycleError("serving_advance_conflict", "previous binding differs")
    if any(item["package_id"] == package["package_id"] for item in config["packages"]):
        raise ExpertLifecycleError("serving_advance_conflict", "package id already exists")
    config["packages"].append(dict(package))
    binding.update(
        package_id=package["package_id"],
        package_digest=package_digest,
        missing_reason_codes=[],
    )
    payload = json.dumps(config, ensure_ascii=True, indent=2).encode("utf-8") + b"\n"
    with tempfile.NamedTemporaryFile(dir=config_path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, config_path)
    return str(package_digest)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit one admitted expert forward-Shadow Job")
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--strategy-version", required=True)
    parser.add_argument(
        "--operation", choices=("forward_observation", "mature_settlement"), required=True
    )
    parser.add_argument("--observation-date", required=True)
    parser.add_argument("--dwd-release-digest", required=True)
    parser.add_argument("--actor-user-id", default="node-local-controller")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        schedule = load_shadow_schedule(args.schedule)
        enabled = any(
            item["strategy_id"] == args.strategy_id
            and item["strategy_version"] == args.strategy_version
            for item in schedule["bindings"]
        )
        if not enabled:
            raise ExpertLifecycleError(
                "shadow_binding_disabled", "strategy version is absent from the enabled schedule"
            )
        dsn = os.environ.get("QUANTHUS_PLATFORM_DATABASE_URL")
        if not dsn:
            raise ExpertLifecycleError("postgres_dsn_missing", "database URL is required")
        from quanthus.platform.persistence import PostgresPlatformStore

        job, created = submit_forward_shadow(
            schedule_path=args.schedule,
            store=PostgresPlatformStore(dsn),
            strategy_id=args.strategy_id,
            strategy_version=args.strategy_version,
            operation=args.operation,
            observation_date=args.observation_date,
            dwd_release_digest=args.dwd_release_digest,
            actor_user_id=args.actor_user_id,
        )
    except ExpertLifecycleError as exc:
        print(json.dumps({"state": "rejected", "reason_code": exc.reason_code}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {"state": "queued", "job_id": job.job_id, "created": created}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
