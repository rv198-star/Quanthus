import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from quanthus.strategy_attribution import AttributionError, digest
from quanthus.strategy_attribution_demo import (
    SyntheticAttributionExecutor,
    SyntheticStrategyAttributionAdapter,
    synthetic_run_request,
)
from quanthus.strategy_attribution_pipeline import (
    AttributionPipeline,
    DeterministicAttributionEvaluator,
    DeterministicAttributionNodeVerifier,
    DeterministicAttributionPlanCompiler,
    EvaluationBundle,
    ImmutableRunStore,
    PipelineRunState,
    PipelineStageReceipt,
    RawExecutionResult,
)
from quanthus.strategy_attribution_planning import (
    AttributionRunRequest,
    PlanNodeKind,
)


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _failure_files(run_dir: Path) -> dict[str, bytes]:
    directory = run_dir / "receipts" / "failures"
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.json"))}


class _LegacyResultsForbiddenAdapter(SyntheticStrategyAttributionAdapter):
    def artifact_resolver(self):
        raise AssertionError("pipeline must not ask the semantic adapter for results")

    def baseline(self, question):
        raise AssertionError("pipeline must not ask the semantic adapter for baseline results")

    def intervention(self, question):
        raise AssertionError("pipeline must not ask the semantic adapter for interventions")

    def condition_comparisons(self, question):
        raise AssertionError("pipeline must not ask the semantic adapter for condition results")

    def interaction_runs(self, question):
        raise AssertionError("pipeline must not ask the semantic adapter for interaction results")

    def analysis_checks(self, question):
        raise AssertionError("pipeline must not ask the semantic adapter for check results")


class _WrongNodeExecutor(SyntheticAttributionExecutor):
    def execute(self, work):
        result = super().execute(work)
        return replace(result, planned_experiment_digest=digest("foreign-planned-node"))


class _InvalidOutputExecutor(SyntheticAttributionExecutor):
    def execute(self, work):
        self._calls.append(work.experiment.experiment_id)
        return {"claim": "executor-forged-claim"}


class _ImpersonatingExecutor:
    def __init__(self) -> None:
        self.inner = SyntheticAttributionExecutor()
        self.execute_calls = 0

    def binding(self):
        return self.inner.binding()

    def execute(self, work):
        self.execute_calls += 1
        result = self.inner.execute(work)
        return replace(
            result,
            metrics={
                key: value + (Decimal("1") if key == "total_return" else Decimal("0"))
                for key, value in result.metrics.items()
            },
        )


def test_plan_is_durable_before_first_execute_and_tamper_blocks_resume(tmp_path) -> None:
    request = synthetic_run_request()
    pipeline = AttributionPipeline(tmp_path / "durable")
    planned = pipeline.plan(request)

    assert all(
        (planned.run_dir / item).is_file()
        for item in ("request.json", "plan.json", "receipts/planned.json")
    )
    receipt = PipelineStageReceipt.from_bytes(
        (planned.run_dir / "receipts" / "planned.json").read_bytes()
    )
    assert receipt.output_digests == (planned.plan.digest,)

    result = pipeline.resume(request, SyntheticAttributionExecutor())
    assert result.state is PipelineRunState.EVALUATED

    tampered_pipeline = AttributionPipeline(tmp_path / "tampered")
    planned = tampered_pipeline.plan(request)
    (planned.run_dir / "plan.json").write_bytes(b"{}")
    blocked_executor = SyntheticAttributionExecutor()

    with pytest.raises(AttributionError) as captured:
        tampered_pipeline.resume(request, blocked_executor)

    assert captured.value.reason_code == "immutable_stage_conflict"
    assert blocked_executor.calls == ()
    assert not (planned.run_dir / "claims").exists()


def test_clean_run_is_byte_deterministic_and_same_root_is_idempotent(tmp_path) -> None:
    request = synthetic_run_request()
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_executor = SyntheticAttributionExecutor()
    second_executor = SyntheticAttributionExecutor()

    first = AttributionPipeline(first_root).run(request, first_executor)
    second = AttributionPipeline(second_root).run(request, second_executor)

    assert len(first.plan.experiments) == 9
    assert len(first_executor.calls) == len(set(first_executor.calls)) == 9
    assert first.claims == second.claims
    assert _files(first_root) == _files(second_root)

    before = _files(first_root)
    mtimes = {path: path.stat().st_mtime_ns for path in first_root.rglob("*") if path.is_file()}
    retry_executor = SyntheticAttributionExecutor()
    retry = AttributionPipeline(first_root).run(request, retry_executor)

    assert retry_executor.calls == ()
    assert retry.claims == first.claims
    assert _files(first_root) == before
    assert all(path.stat().st_mtime_ns == value for path, value in mtimes.items())


def test_clean_resume_loads_verified_projection_without_reverification(tmp_path) -> None:
    request = synthetic_run_request()
    verifier = DeterministicAttributionNodeVerifier()
    pipeline = AttributionPipeline(tmp_path, verifier=verifier)

    first = pipeline.run(request, SyntheticAttributionExecutor())
    retry_executor = SyntheticAttributionExecutor()
    second = pipeline.resume(request, retry_executor)

    assert verifier.calls == len(first.plan.experiments) == 9
    assert retry_executor.calls == ()
    assert second.claims == first.claims
    assert second.publishable_claims == ()


def test_concurrent_resume_executes_each_physical_node_once(tmp_path) -> None:
    request = synthetic_run_request()
    AttributionPipeline(tmp_path).plan(request)
    executors = (SyntheticAttributionExecutor(), SyntheticAttributionExecutor())

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                lambda executor: AttributionPipeline(tmp_path).resume(request, executor),
                executors,
            )
        )

    assert all(item.state is PipelineRunState.EVALUATED for item in results)
    assert sum(len(item.calls) for item in executors) == 9


def test_shared_physical_nodes_execute_once_and_project_per_question(tmp_path) -> None:
    request = synthetic_run_request()
    result = AttributionPipeline(tmp_path).run(request, SyntheticAttributionExecutor())

    baseline = next(
        item for item in result.plan.experiments if item.kind is PlanNodeKind.BASELINE_REPLAY
    )
    router = next(
        item
        for item in result.plan.experiments
        if item.kind is PlanNodeKind.SINGLE_INTERVENTION
        and item.component_ids == ("router.market_state",)
    )
    baseline_record = json.loads(
        (
            result.run_dir / "verification" / f"{baseline.digest.removeprefix('sha256:')}.json"
        ).read_bytes()
    )
    router_record = json.loads(
        (
            result.run_dir / "verification" / f"{router.digest.removeprefix('sha256:')}.json"
        ).read_bytes()
    )

    assert len(baseline_record["experiment_manifest_digests"]) == 5
    assert len(router_record["experiment_manifest_digests"]) == 2
    assert len(result.experiments) == 12
    assert len({item.raw_execution_digest for item in result.experiments}) == 7


def test_pipeline_uses_only_semantic_adapter_and_raw_executor_ports(tmp_path) -> None:
    adapter = _LegacyResultsForbiddenAdapter()
    request = synthetic_run_request(adapter)
    executor = SyntheticAttributionExecutor(adapter)

    result = AttributionPipeline(tmp_path).run(request, executor)

    assert len(result.claims) == 5
    forbidden = {
        "claim_id",
        "classification",
        "next_action_code",
        "validation_flags",
        "verification_receipts",
        "passed",
        "reproduced",
        "reconciled",
    }
    for path in result.run_dir.joinpath("raw").glob("*.json"):
        assert forbidden.isdisjoint(json.loads(path.read_bytes()))


def test_recursive_claim_fields_are_rejected_from_raw_trace(tmp_path) -> None:
    result = AttributionPipeline(tmp_path).run(
        synthetic_run_request(), SyntheticAttributionExecutor()
    )
    raw_path = next(result.run_dir.joinpath("raw").glob("*.json"))
    raw = RawExecutionResult.from_bytes(raw_path.read_bytes())

    with pytest.raises(AttributionError) as captured:
        replace(
            raw,
            trace_payload={"outer": [{"evidence": {"claim_id": "forged"}}]},
        )

    assert captured.value.reason_code == "reserved_trace_field"


def test_concrete_executor_registration_rejects_identity_impersonation(tmp_path) -> None:
    executor = _ImpersonatingExecutor()

    with pytest.raises(AttributionError) as captured:
        AttributionPipeline(tmp_path).run(synthetic_run_request(), executor)

    assert captured.value.reason_code == "unregistered_executor_implementation"
    assert executor.execute_calls == 0
    assert executor.inner.calls == ()


def test_executor_failure_resumes_without_replaying_completed_nodes(tmp_path) -> None:
    request = synthetic_run_request()
    pipeline = AttributionPipeline(tmp_path)
    failing = SyntheticAttributionExecutor(fail_on_calls=(4,))

    with pytest.raises(AttributionError) as captured:
        pipeline.run(request, failing)

    assert captured.value.reason_code == "synthetic_executor_injected_failure"
    failed_status = pipeline.status(request)
    assert failed_status.state is PipelineRunState.FAILED
    assert failed_status.executed_nodes == 3

    healthy = SyntheticAttributionExecutor()
    result = AttributionPipeline(tmp_path).resume(request, healthy)

    assert result.state is PipelineRunState.EVALUATED
    assert len(healthy.calls) == 6
    assert pipeline.status(request).verified_nodes == 9


def test_verification_failure_is_terminal_and_non_directional(tmp_path) -> None:
    request = synthetic_run_request()
    pipeline = AttributionPipeline(tmp_path)
    corrupt = SyntheticAttributionExecutor(corrupt_on_calls=(3,))

    with pytest.raises(AttributionError) as captured:
        pipeline.run(request, corrupt)

    assert captured.value.reason_code == "node_verification_failed"
    status = pipeline.status(request)
    assert status.state is PipelineRunState.VERIFICATION_FAILED
    assert status.executed_nodes == 3
    assert not (status.run_dir / "claims").exists()

    healthy = SyntheticAttributionExecutor()
    with pytest.raises(AttributionError):
        AttributionPipeline(tmp_path).resume(request, healthy)
    assert healthy.calls == ()


@pytest.mark.parametrize(
    "binding_mutation",
    (
        {"source_kind": "synthetic_fixture_not_strategy_evidence_extra"},
        {"executor_id": "foreign.synthetic-looking-executor"},
        {"producer_id": "foreign.synthetic-looking-producer"},
        {"synthetic_fixture": False},
    ),
)
def test_prototype_source_gate_is_exact_and_pre_execution(tmp_path, binding_mutation) -> None:
    request = synthetic_run_request()

    class MutatedBindingExecutor(SyntheticAttributionExecutor):
        def binding(self):
            return replace(super().binding(), **binding_mutation)

    executor = MutatedBindingExecutor()
    pipeline = AttributionPipeline(tmp_path)

    with pytest.raises(AttributionError):
        pipeline.run(request, executor)

    assert executor.calls == ()
    status = pipeline.status(request)
    assert status.state is PipelineRunState.FAILED
    assert not (status.run_dir / "claims").exists()


def test_formal_adapter_source_remains_blocked(tmp_path) -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    formal_manifest = replace(
        adapter.capability_manifest(), evidence_source_kind="formal_strategy_evidence"
    )
    request = AttributionRunRequest.create(
        adapter_manifest=formal_manifest,
        questions=adapter.questions(),
        context_bindings=synthetic_run_request().context_bindings,
        period_start=synthetic_run_request().period_start,
        period_end=synthetic_run_request().period_end,
        metric_calculator_id=synthetic_run_request().metric_calculator_id,
        metric_calculator_version=synthetic_run_request().metric_calculator_version,
    )
    executor = SyntheticAttributionExecutor()

    with pytest.raises(AttributionError) as captured:
        AttributionPipeline(tmp_path).run(request, executor)

    assert captured.value.reason_code == "formal_strategy_source_blocked"
    assert executor.calls == ()


@pytest.mark.parametrize("executor_type", (_WrongNodeExecutor, _InvalidOutputExecutor))
def test_foreign_or_claim_bearing_executor_output_cannot_reach_claims(
    tmp_path, executor_type
) -> None:
    request = synthetic_run_request()
    pipeline = AttributionPipeline(tmp_path)
    executor = executor_type()

    with pytest.raises(AttributionError):
        pipeline.run(request, executor)

    status = pipeline.status(request)
    assert status.state in {
        PipelineRunState.FAILED,
        PipelineRunState.VERIFICATION_FAILED,
    }
    assert not (status.run_dir / "claims").exists()


def test_planner_failure_receipt_ignores_dynamic_exception_text(tmp_path) -> None:
    request = synthetic_run_request()
    compiler = DeterministicAttributionPlanCompiler(fail_on_calls=(1, 2))
    pipeline = AttributionPipeline(tmp_path, plan_compiler=compiler)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            pipeline.plan(request)
    status = pipeline.status(request)

    assert status.state is PipelineRunState.FAILED
    assert status.reason_codes == ("planner_failed",)
    assert len(_failure_files(status.run_dir)) == 1
    assert not (status.run_dir / "plan.json").exists()


def test_executor_failure_receipt_is_stable_across_retries(tmp_path) -> None:
    request = synthetic_run_request()
    pipeline = AttributionPipeline(tmp_path)
    executor = SyntheticAttributionExecutor(fail_on_calls=(1, 2))

    with pytest.raises(AttributionError):
        pipeline.run(request, executor)
    run_dir = pipeline.status(request).run_dir
    first = _failure_files(run_dir)
    with pytest.raises(AttributionError):
        pipeline.resume(request, executor)

    assert _failure_files(run_dir) == first
    assert len(first) == 1
    assert b"deterministic failure injection" not in next(iter(first.values()))


def test_verifier_failure_receipt_is_stable_and_resume_skips_executor(tmp_path) -> None:
    request = synthetic_run_request()
    verifier = DeterministicAttributionNodeVerifier(fail_on_calls=(1, 2))
    pipeline = AttributionPipeline(tmp_path, verifier=verifier)
    first_executor = SyntheticAttributionExecutor()

    with pytest.raises(RuntimeError):
        pipeline.run(request, first_executor)
    run_dir = pipeline.status(request).run_dir
    first = _failure_files(run_dir)
    retry_executor = SyntheticAttributionExecutor()
    with pytest.raises(RuntimeError):
        pipeline.resume(request, retry_executor)

    assert retry_executor.calls == ()
    assert _failure_files(run_dir) == first
    assert b"injected verifier failure" not in next(iter(first.values()))


def test_verifier_rebinds_observation_digests_to_raw_metric_content(tmp_path) -> None:
    request = synthetic_run_request()
    failing_verifier = DeterministicAttributionNodeVerifier(fail_on_calls=(1,))
    pipeline = AttributionPipeline(tmp_path, verifier=failing_verifier)

    with pytest.raises(RuntimeError):
        pipeline.run(request, SyntheticAttributionExecutor())
    run_dir = pipeline.status(request).run_dir
    raw_path = next(run_dir.joinpath("raw").glob("*.json"))
    raw = RawExecutionResult.from_bytes(raw_path.read_bytes())
    tampered = replace(
        raw,
        metrics={
            key: value + (Decimal("1") if key == "total_return" else Decimal("0"))
            for key, value in raw.metrics.items()
        },
    )
    raw_path.write_bytes(tampered.canonical_bytes())
    (run_dir / "receipts" / "execution" / raw_path.name).unlink()
    retry_executor = SyntheticAttributionExecutor()

    with pytest.raises(AttributionError) as captured:
        AttributionPipeline(tmp_path).resume(request, retry_executor)

    assert captured.value.reason_code == "node_verification_failed"
    assert retry_executor.calls == ()
    assert not (run_dir / "claims").exists()


def test_evaluator_failure_is_stable_and_never_persists_partial_claims(tmp_path) -> None:
    request = synthetic_run_request()
    evaluator = DeterministicAttributionEvaluator(fail_on_calls=(1, 2))
    pipeline = AttributionPipeline(tmp_path, evaluator=evaluator)

    with pytest.raises(RuntimeError):
        pipeline.run(request, SyntheticAttributionExecutor())
    status = pipeline.status(request)
    first = _failure_files(status.run_dir)
    retry_executor = SyntheticAttributionExecutor()
    with pytest.raises(RuntimeError):
        pipeline.resume(request, retry_executor)

    assert status.state is PipelineRunState.FAILED
    assert retry_executor.calls == ()
    assert _failure_files(status.run_dir) == first
    assert not (status.run_dir / "claims").exists()


def test_evaluation_bundle_conflict_never_exposes_directional_claim_files(tmp_path) -> None:
    request = synthetic_run_request()
    evaluator = DeterministicAttributionEvaluator(fail_on_calls=(1,))
    pipeline = AttributionPipeline(tmp_path, evaluator=evaluator)

    with pytest.raises(RuntimeError):
        pipeline.run(request, SyntheticAttributionExecutor())
    run_dir = pipeline.status(request).run_dir
    bundle_path = run_dir / "evaluation" / "bundle.json"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_bytes(b'{"foreign":true}')
    retry_executor = SyntheticAttributionExecutor()

    with pytest.raises(AttributionError) as captured:
        AttributionPipeline(tmp_path).resume(request, retry_executor)

    assert captured.value.reason_code == "immutable_stage_conflict"
    assert retry_executor.calls == ()
    assert not (run_dir / "claims").exists()
    assert not (run_dir / "receipts" / "evaluated.json").exists()


def test_immutable_store_accepts_same_bytes_and_rejects_conflict(tmp_path) -> None:
    store = ImmutableRunStore(tmp_path, "attribution-run-test")
    target = store.write("stage/result.json", b"first")
    initial_mtime = target.stat().st_mtime_ns

    store.write("stage/result.json", b"first")
    with pytest.raises(AttributionError) as captured:
        store.write("stage/result.json", b"different")

    assert captured.value.reason_code == "immutable_stage_conflict"
    assert target.read_bytes() == b"first"
    assert target.stat().st_mtime_ns == initial_mtime


def test_immutable_store_rejects_parent_symlink_escape(tmp_path) -> None:
    store = ImmutableRunStore(tmp_path / "runs", "attribution-run-test")
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "raw").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AttributionError) as captured:
        store.write("raw/escaped.json", b"forbidden")

    assert captured.value.reason_code == "invalid_store_path"
    assert not (outside / "escaped.json").exists()


def test_status_is_read_only_for_an_unknown_run(tmp_path) -> None:
    pipeline = AttributionPipeline(tmp_path)

    with pytest.raises(AttributionError) as captured:
        pipeline.status(synthetic_run_request())

    assert captured.value.reason_code == "attribution_run_not_found"
    assert tuple(tmp_path.iterdir()) == ()


def test_status_rejects_forged_evaluated_receipt_without_verified_dag(tmp_path) -> None:
    request = synthetic_run_request()
    planned = AttributionPipeline(tmp_path).plan(request)
    forged = PipelineStageReceipt(
        run_id="foreign-run",
        state=PipelineRunState.PLANNED,
        stage="forged-stage",
        subject_id="forged-subject",
    )
    target = planned.run_dir / "receipts" / "evaluated.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(forged.canonical_bytes())

    with pytest.raises(AttributionError) as captured:
        AttributionPipeline(tmp_path).status(request)

    assert captured.value.reason_code == "evaluation_lineage_incomplete"


def test_status_rejects_foreign_failure_receipt(tmp_path) -> None:
    request = synthetic_run_request()
    planned = AttributionPipeline(tmp_path).plan(request)
    forged = PipelineStageReceipt(
        run_id="foreign-run",
        state=PipelineRunState.FAILED,
        stage="executor_binding",
        subject_id=planned.plan.plan_id,
        parent_digests=(planned.planned_receipt.digest,),
        reason_codes=("foreign_failure",),
    )
    token = AttributionPipeline._failure_token(
        forged.stage,
        forged.subject_id,
        forged.reason_codes[0],
    )
    target = planned.run_dir / "receipts" / "failures" / f"{forged.stage}-{token}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(forged.canonical_bytes())

    with pytest.raises(AttributionError) as captured:
        AttributionPipeline(tmp_path).status(request)

    assert captured.value.reason_code == "failure_receipt_run_mismatch"


def test_status_rebuilds_kernel_output_and_rejects_self_consistent_forgery(
    tmp_path,
) -> None:
    request = synthetic_run_request()
    result = AttributionPipeline(tmp_path).run(request, SyntheticAttributionExecutor())
    bundle_path = result.run_dir / "evaluation" / "bundle.json"
    receipt_path = result.run_dir / "receipts" / "evaluated.json"
    bundle = EvaluationBundle.from_bytes(bundle_path.read_bytes())
    forged_bundle = replace(
        bundle,
        claims=(
            {
                "claim_id": "forged-directional-claim",
                "status": "valid",
                "classification": "beneficial",
                "next_action_code": "publish_now",
            },
        ),
        kernel_receipts=(),
    )
    forged_receipt = replace(
        PipelineStageReceipt.from_bytes(receipt_path.read_bytes()),
        output_digests=(forged_bundle.digest,),
    )
    bundle_path.write_bytes(forged_bundle.canonical_bytes())
    receipt_path.write_bytes(forged_receipt.canonical_bytes())

    with pytest.raises(AttributionError) as captured:
        AttributionPipeline(tmp_path).status(request)

    assert captured.value.reason_code == "evaluation_bundle_rebuild_mismatch"


def test_stage_receipts_are_timestamp_free_and_follow_the_state_chain(tmp_path) -> None:
    request = synthetic_run_request()
    result = AttributionPipeline(tmp_path).run(request, SyntheticAttributionExecutor())
    receipts = [
        json.loads(path.read_bytes())
        for path in sorted(result.run_dir.joinpath("receipts").rglob("*.json"))
    ]
    states = {item["state"] for item in receipts}
    serialized = json.dumps(receipts, sort_keys=True)

    assert {
        PipelineRunState.PLANNED.value,
        PipelineRunState.RUNNING.value,
        PipelineRunState.EXECUTED.value,
        PipelineRunState.EVALUATED.value,
    }.issubset(states)
    assert "timestamp" not in serialized
    assert str(tmp_path) not in serialized
    assert "traceback" not in serialized


def test_open_component_kind_does_not_change_pipeline_or_executor_routing(tmp_path) -> None:
    adapter = SyntheticStrategyAttributionAdapter()
    adapter._manifest = replace(
        adapter.capability_manifest(),
        components=tuple(
            replace(item, kind=f"future_open_kind:{index}")
            for index, item in enumerate(adapter.capability_manifest().components)
        ),
    )
    request = synthetic_run_request(adapter)

    result = AttributionPipeline(tmp_path).run(request, SyntheticAttributionExecutor(adapter))

    assert len(result.claims) == 5
    assert all(item.status.value == "valid" for item in result.claims)
