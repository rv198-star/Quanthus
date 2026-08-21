from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from quanthus.expert_evaluation.production import (
    PRODUCER_ID,
    production_dwd_identity,
    production_job_input,
    production_producer_for,
)
from quanthus.expert_evaluation.runner import (
    DwdIdentity,
    ExpertEvaluationJobInput,
    FormalStrategyRef,
    PartitionBinding,
    ProducerBinding,
    ProtocolFixtureProducer,
    RegistryArtifactRef,
    bind_evaluation_specs,
    run_evaluation,
    verify_expert_evaluation_run,
)
from quanthus.expert_evaluation.serialization import ExpertEvaluationError
from quanthus.platform.jobs import (
    EXPERT_EVALUATION_JOB,
    expert_evaluation_submission,
    input_digest,
)
from quanthus.platform.models import JobRecord
from quanthus.platform.worker import ReplayWorker
from tests.expert_evaluation_fixtures import build_evaluation_graph


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _fixture_input(registry: Path) -> ExpertEvaluationJobInput:
    fixture = build_evaluation_graph()
    spec_path = registry / "specs" / "trend-stability-1.0.0.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_bytes(fixture.spec.canonical_bytes())
    producer = ProtocolFixtureProducer()
    return ExpertEvaluationJobInput(
        operation="development_evaluation",
        evaluation_specs=(
            RegistryArtifactRef(
                fixture.spec.assessment_id,
                fixture.spec.assessment_version,
                fixture.spec.digest,
                "specs/trend-stability-1.0.0.json",
            ),
        ),
        formal_strategy=None,
        dwd=DwdIdentity(
            ("dwd.base", "2.1.0", _digest("dwd-base")),
            (("dwd.correction", "1.0.0", _digest("correction")),),
            (),
            _digest("effective-view"),
            date(2021, 12, 31),
        ),
        partition=PartitionBinding(
            fixture.spec.partition.artifact_id,
            fixture.spec.partition.version,
            fixture.spec.partition.digest,
            date(2012, 1, 4),
            date(2021, 12, 30),
        ),
        requested_interventions=(),
        expected_parent_run_digests=(),
        producer=ProducerBinding(
            producer.PRODUCER_ID,
            producer.PRODUCER_VERSION,
            producer.CONFIGURATION_DIGEST,
        ),
    )


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _running_job(
    job_input: ExpertEvaluationJobInput,
    *,
    job_id: str = "job_expert_fixture",
    generation: int = 3,
) -> JobRecord:
    now = datetime.now(timezone.utc)
    return JobRecord(
        job_id=job_id,
        kind=EXPERT_EVALUATION_JOB,
        actor_user_id="usr_test",
        idempotency_key=job_input.idempotency_key,
        input_ref=job_input.to_dict(),
        state="running",
        progress=1,
        cancel_requested=False,
        attempt=1,
        result_ref=None,
        error_reason=None,
        created_at=now,
        updated_at=now,
        queue="replay",
        input_digest=job_input.input_digest,
        concurrency_key=job_input.idempotency_key,
        available_at=now,
        stage="starting",
        claimed_by="worker-test",
        claim_generation=generation,
    )


def test_job_input_is_exact_canonical_and_derives_transport_keys(tmp_path: Path) -> None:
    job_input = _fixture_input(tmp_path / "registry")

    decoded = ExpertEvaluationJobInput.from_dict(job_input.to_dict())
    canonical, idempotency_key, concurrency_key = expert_evaluation_submission(
        job_input.to_dict()
    )

    assert decoded == job_input
    assert canonical == job_input.to_dict()
    assert idempotency_key == job_input.idempotency_key
    assert concurrency_key == idempotency_key
    assert input_digest(canonical) == job_input.input_digest


def test_first_production_job_constructor_freezes_scope_dwd_parent_and_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = "sha256:" + "e" * 64
    job_input = production_job_input(parent)
    monkeypatch.setenv("QUANTHUS_EVENT_RUN_ROOT", str(tmp_path / "event-runs"))

    producer = production_producer_for(job_input, tmp_path / "config/expert-evaluations")

    assert job_input.producer.producer_id == PRODUCER_ID
    assert job_input.dwd == production_dwd_identity()
    assert job_input.partition.decision_start == date(2020, 1, 1)
    assert job_input.partition.decision_end == date(2021, 12, 31)
    assert job_input.expected_parent_run_digests == (parent,)
    assert producer.event_run_root == (tmp_path / "event-runs").resolve()
    invalid = job_input.to_dict()
    invalid["job_id"] = "transport-field-must-not-enter-semantics"
    with pytest.raises(ExpertEvaluationError, match="field_mismatch"):
        ExpertEvaluationJobInput.from_dict(invalid)


def test_registry_binding_rejects_path_escape_checksum_drift_and_partition_drift(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry"
    job_input = _fixture_input(registry)
    assert len(bind_evaluation_specs(job_input, registry)) == 1

    escaped = job_input.to_dict()
    escaped["evaluation_specs"][0]["registry_key"] = "../outside.json"
    with pytest.raises(ExpertEvaluationError, match="path_escape"):
        ExpertEvaluationJobInput.from_dict(escaped)

    spec_path = registry / job_input.evaluation_specs[0].registry_key
    spec_path.write_bytes(spec_path.read_bytes() + b"\n")
    with pytest.raises(ExpertEvaluationError, match="non_canonical_encoding"):
        bind_evaluation_specs(job_input, registry)

    _fixture_input(registry)
    mismatched = job_input.to_dict()
    mismatched["partition"]["digest"] = _digest("another-partition")
    with pytest.raises(ExpertEvaluationError, match="partition_mismatch"):
        bind_evaluation_specs(ExpertEvaluationJobInput.from_dict(mismatched), registry)


def test_formal_strategy_requires_exact_spec_closure_and_admitted_interventions(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "registry"
    job_input = _fixture_input(registry)
    formal = FormalStrategyRef(
        "strategy.fixture",
        "1.0.0",
        _digest("strategy-card"),
        _digest("attribution-plan"),
        "strategies/fixture.json",
    )
    formal_input = replace(job_input, formal_strategy=formal)
    expected = tuple(item.identity for item in formal_input.evaluation_specs)

    class Resolver:
        def __init__(
            self,
            specs: tuple[tuple[str, str, str], ...],
            interventions: tuple[str, ...] = (),
        ) -> None:
            self.specs = specs
            self.interventions = interventions

        def expected_bindings(
            self, strategy: FormalStrategyRef, registry_root: Path
        ) -> tuple[tuple[tuple[str, str, str], ...], tuple[str, ...]]:
            assert strategy == formal and registry_root == registry
            return self.specs, self.interventions

    assert len(bind_evaluation_specs(formal_input, registry, Resolver(expected))) == 1
    with pytest.raises(ExpertEvaluationError, match="formal_strategy_spec_mismatch"):
        bind_evaluation_specs(formal_input, registry, Resolver(()))
    intervention_input = replace(formal_input, requested_interventions=("p2.fixture",))
    with pytest.raises(ExpertEvaluationError, match="intervention_not_admitted"):
        bind_evaluation_specs(intervention_input, registry, Resolver(expected))
    assert len(
        bind_evaluation_specs(
            intervention_input, registry, Resolver(expected, ("p2.fixture",))
        )
    ) == 1


def test_identical_semantics_publish_byte_identical_content_runs(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    job_input = _fixture_input(registry)
    producer = ProtocolFixtureProducer()
    progress_a: list[str] = []
    progress_b: list[str] = []

    first = run_evaluation(
        job_input,
        registry_root=registry,
        staging=tmp_path / "attempt-job-a-generation-1",
        run_root=tmp_path / "runs-a",
        producer=producer,
        progress=lambda stage, _detail: progress_a.append(stage),
    )
    second = run_evaluation(
        job_input,
        registry_root=registry,
        staging=tmp_path / "attempt-job-b-generation-9",
        run_root=tmp_path / "runs-b",
        producer=producer,
        progress=lambda stage, _detail: progress_b.append(stage),
    )

    assert first.run_id == second.run_id
    assert first.manifest_digest == second.manifest_digest
    assert _files(first.root) == _files(second.root)
    assert progress_a == progress_b
    manifest_text = (first.root / "run.json").read_text(encoding="utf-8")
    for forbidden in ("job_id", "claim_generation", "worker_id", "created_at", "retry"):
        assert forbidden not in manifest_text
    assert verify_expert_evaluation_run(first.root).run_id == first.run_id
    intervention_stage = next(
        item for item in first.manifest["stages"] if item["stage"] == "running-interventions"
    )
    assert intervention_stage == {
        "stage": "running-interventions",
        "state": "skipped",
        "skip_reason": "not_requested",
    }


def test_deep_verification_rejects_tamper_partial_and_path_inventory(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    job_input = _fixture_input(registry)
    run = run_evaluation(
        job_input,
        registry_root=registry,
        staging=tmp_path / "attempt",
        run_root=tmp_path / "runs",
        producer=ProtocolFixtureProducer(),
    )
    (run.root / "scorecard.json").write_text("tampered\n", encoding="ascii")
    with pytest.raises(ExpertEvaluationError, match="artifact_checksum_mismatch"):
        verify_expert_evaluation_run(run.root)

    another = run_evaluation(
        job_input,
        registry_root=registry,
        staging=tmp_path / "attempt-another",
        run_root=tmp_path / "another-runs",
        producer=ProtocolFixtureProducer(),
    )
    (another.root / "READY").unlink()
    with pytest.raises(ExpertEvaluationError, match="run_not_ready"):
        verify_expert_evaluation_run(another.root)


@pytest.mark.parametrize(
    "cancel_stage",
    (
        "binding-inputs",
        "building-shared-projection",
        "producing-observations",
        "settling-outcomes",
        "building-scorecard",
        "running-interventions",
        "verifying-artifacts",
        "publishing-run",
    ),
)
def test_cancellation_at_every_stage_boundary_never_publishes(
    tmp_path: Path, cancel_stage: str
) -> None:
    registry = tmp_path / cancel_stage / "registry"
    job_input = _fixture_input(registry)
    last_stage = ""

    def progress(stage: str, _detail: object) -> None:
        nonlocal last_stage
        last_stage = stage

    with pytest.raises(ExpertEvaluationError, match="canceled"):
        run_evaluation(
            job_input,
            registry_root=registry,
            staging=tmp_path / cancel_stage / "attempt",
            run_root=tmp_path / cancel_stage / "runs",
            producer=ProtocolFixtureProducer(),
            progress=progress,
            canceled=lambda: last_stage == cancel_stage,
        )
    run_root = tmp_path / cancel_stage / "runs"
    assert not run_root.exists() or not any(run_root.iterdir())


def test_replay_worker_dispatches_expert_job_to_isolated_child(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    job_input = _fixture_input(registry)
    job = _running_job(job_input)

    class Store:
        published: dict[str, Any] | None = None
        updates: list[tuple[str, int]]

        def __init__(self) -> None:
            self.updates = []

        def claim_job(self, queue: str, worker_id: str, lease_seconds: int) -> JobRecord:
            assert (queue, worker_id, lease_seconds) == ("replay", "worker-test", 300)
            return job

        def get_job(self, job_id: str) -> JobRecord:
            assert job_id == job.job_id
            return job

        def expert_evaluation_run_for_input(self, digest: str) -> None:
            assert digest == job.input_digest
            return None

        def update_claimed_job(
            self,
            job_id: str,
            generation: int,
            *,
            stage: str,
            progress: int,
            progress_detail: dict[str, Any],
        ) -> JobRecord:
            del progress_detail
            assert job_id == job.job_id and generation == 3
            self.updates.append((stage, progress))
            return job

        def publish_expert_evaluation_run(
            self, job_id: str, generation: int, **values: Any
        ) -> tuple[object, JobRecord]:
            assert job_id == job.job_id and generation == 3
            self.published = values
            return object(), job

        def finish_claimed_job(self, *_args: object, **_kwargs: object) -> JobRecord:
            pytest.fail("successful child must publish atomically")

        def renew_job_lease(self, *_args: object) -> bool:
            return True

    store = Store()
    worker = ReplayWorker(
        store,  # type: ignore[arg-type]
        worker_id="worker-test",
        run_root=tmp_path / "candidate-runs",
        expert_run_root=tmp_path / "expert-runs",
        expert_registry_root=registry,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )

    assert worker.run_once() is True
    assert store.published is not None
    assert str(store.published["run_id"]).startswith("expert_run_")
    assert (Path(str(store.published["artifact_ref"])) / "READY").is_file()
    assert store.updates[-1][0] == "publishing-run"


def test_worker_adopts_orphaned_sealed_run_without_rerunning_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "registry"
    job_input = _fixture_input(registry)
    expert_root = tmp_path / "expert-runs"
    orphan = run_evaluation(
        job_input,
        registry_root=registry,
        staging=tmp_path / "orphan-attempt",
        run_root=expert_root / "runs",
        producer=ProtocolFixtureProducer(),
    )
    job = _running_job(job_input, job_id="job_adopt_orphan", generation=7)

    class Store:
        published: dict[str, Any] | None = None
        recovered_stage: str | None = None

        def get_job(self, job_id: str) -> JobRecord:
            assert job_id == job.job_id
            return job

        def expert_evaluation_run_for_input(self, digest: str) -> None:
            assert digest == job.input_digest
            return None

        def update_claimed_job(
            self,
            _job_id: str,
            _generation: int,
            *,
            stage: str,
            progress: int,
            progress_detail: dict[str, Any],
        ) -> JobRecord:
            assert progress == 98 and progress_detail == {"run_id": orphan.run_id}
            self.recovered_stage = stage
            return job

        def publish_expert_evaluation_run(
            self, _job_id: str, _generation: int, **values: Any
        ) -> tuple[object, JobRecord]:
            self.published = values
            return object(), job

    monkeypatch.setattr(
        "quanthus.platform.worker.subprocess.Popen",
        lambda *_args, **_kwargs: pytest.fail("orphan adoption must not rerun the child"),
    )
    store = Store()
    worker = ReplayWorker(
        store,  # type: ignore[arg-type]
        worker_id="worker-test",
        run_root=tmp_path / "candidate-runs",
        expert_run_root=expert_root,
        expert_registry_root=registry,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )

    worker._execute_expert(job.job_id, 7, job.input_ref)

    assert store.recovered_stage == "recovering-sealed-run"
    assert store.published is not None
    assert store.published["run_id"] == orphan.run_id
