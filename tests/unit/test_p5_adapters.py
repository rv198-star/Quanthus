from __future__ import annotations

import hashlib
import json
import shlex
import sys
from dataclasses import replace
from datetime import date, timedelta

import pytest

from quanthus.policy import find_project_root
from quanthus.stage_learning.p5_adapters import (
    BaselineAdapter,
    TrainingExample,
    base_rate_receipt,
    rule_surface,
)
from quanthus.stage_learning.p5_assessment import (
    FeatureCell,
    FeatureWindow,
    P5AssessmentError,
    TemporalEncoderConfig,
    masked_binary_cross_entropy,
)
from quanthus.stage_learning.p5_contracts import (
    PROPOSITIONS,
    REQUEST_DIGEST,
    TARGET_DIGEST,
    CalibrationBinding,
    ModelIdentity,
    ParentEvidence,
    SampleIdentity,
    SeedPrediction,
    aggregate_predictions,
    head_set_digest,
    project_output,
    supervision_mask,
    validate_dependency_mask,
    validate_reference_use,
)
from quanthus.stage_learning.p5_synthetic_runner import CONFIG, run, verify
from quanthus.stage_learning.worker import (
    P5_SYNTHETIC_JOB,
    StageJob,
    StageWorker,
    StageWorkerPaths,
    submit_job,
)
from quanthus.stage_proposition_bundle import (
    build_bundle,
    load_stage_proposition_contract,
    make_assertion,
)

ROOT = find_project_root()
DIGEST = "sha256:" + "a" * 64
SAMPLE = SampleIdentity("sample", "synthetic-security", date(2015, 8, 10), "episode")


def identity(kind="L", heads=(2,)):
    return ModelIdentity(
        kind,
        REQUEST_DIGEST,
        TARGET_DIGEST,
        DIGEST,
        DIGEST,
        head_set_digest(heads),
        DIGEST,
        DIGEST,
        DIGEST,
        DIGEST,
        DIGEST,
        DIGEST,
    )


def row(index=0):
    days = tuple(SAMPLE.anchor_date - timedelta(days=119 - i) for i in range(120))
    return TrainingExample(
        replace(SAMPLE, sample_id=f"sample-{index}", episode_id=f"episode-{index}"),
        FeatureWindow(
            days, tuple((FeatureCell((i + index) / 120),) for i in range(120)), SAMPLE.anchor_date
        ),
        tuple(index % 2 if code == 2 else None for code in range(1, 13)),
        (True,) * 12,
        {},
    )


def test_exact_frozen_identities_and_propositions():
    root = ROOT / "config/research/p5-stage-001"
    for name, expected in (
        ("request.v1.json", REQUEST_DIGEST),
        ("stage-targets.v1.json", TARGET_DIGEST),
    ):
        assert "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
    assert (
        tuple(
            card["proposition_id"]
            for card in json.loads((root / "stage-targets.v1.json").read_text())["cards"]
        )
        == PROPOSITIONS
    )


def test_conditional_mask_requires_same_parent_episode_and_no_future():
    parent = ParentEvidence(SAMPLE, 10, "episode", True, DIGEST, SAMPLE.anchor_date)
    args = dict(
        sample=SAMPLE,
        labels=(1,) * 12,
        applicability=(True,) * 12,
        trainable_heads=(11,),
        parents={11: parent},
    )
    assert supervision_mask(**args) == (False,) * 10 + (True, False)
    for changed in (
        replace(parent, episode_id="other"),
        replace(parent, knowledge_cutoff=date(2016, 1, 1)),
        replace(parent, sample=replace(SAMPLE, sample_id="other")),
    ):
        with pytest.raises(P5AssessmentError):
            supervision_mask(**{**args, "parents": {11: changed}})
    with pytest.raises(P5AssessmentError):
        supervision_mask(**{**args, "parents": {}})


def test_loss_cannot_train_nonadmitted_or_frozen_heads():
    scores = [[0.5] * 12]
    assert masked_binary_cross_entropy(
        scores, [[1] * 12], [[True] * 12], trainable_heads=(2,)
    ) == pytest.approx(0.69314718056)
    with pytest.raises(P5AssessmentError):
        masked_binary_cross_entropy(scores, [[1] * 12], [[True] * 12], trainable_heads=(7,))
    with pytest.raises(P5AssessmentError):
        masked_binary_cross_entropy(scores, [[1] * 12], [[True] * 12], trainable_heads=())


def test_seed_aggregation_binds_sample_input_split_and_headset():
    p = SeedPrediction(SAMPLE, DIGEST, DIGEST, head_set_digest((2,)), DIGEST, DIGEST, (0.6,) * 12)
    values = {
        seed: replace(
            p,
            seed=seed,
            trained_heads=(2,),
            window_digest=DIGEST,
            checkpoint_digest="sha256:" + hashlib.sha256(str(seed).encode()).hexdigest(),
        )
        for seed in (17, 29, 43)
    }
    p = values[43]
    assert aggregate_predictions(values, trainable_heads=(2,)) == (None, 0.6) + (None,) * 10
    for changed in (
        replace(p, sample=replace(SAMPLE, sample_id="other")),
        replace(p, input_digest="sha256:" + "b" * 64),
        replace(p, head_set_digest=head_set_digest((3,))),
    ):
        with pytest.raises(P5AssessmentError):
            aggregate_predictions({**values, 43: changed}, trainable_heads=(2,))
    with pytest.raises(P5AssessmentError):
        aggregate_predictions({17: p}, trainable_heads=(2,))
    with pytest.raises(P5AssessmentError):
        aggregate_predictions({seed: p for seed in (17, 29, 43)}, trainable_heads=(2,))


def test_probability_requires_correct_calibrator_and_partition():
    ident = replace(
        identity(),
        calibrator_digest=DIGEST,
        calibrator_fit_partition="calibration",
        trained_heads=(2,),
    )
    binding = CalibrationBinding(
        DIGEST,
        DIGEST,
        DIGEST,
        DIGEST,
        DIGEST,
        head_set_digest((2,)),
        "calibration",
        (2,),
        "raw_score",
    )
    args = dict(
        sample=SAMPLE,
        historical_stage_code=2,
        proposition_id=PROPOSITIONS[1],
        identity=ident,
        trainable_heads=(2,),
        applicability=True,
        raw_score=0.4,
        calibrated_probability=0.8,
    )
    with pytest.raises(P5AssessmentError):
        project_output(**args)
    assert project_output(**args, calibration=binding)["decision_state"] == "ASSERT_POSITIVE"
    for changed in (
        replace(binding, fit_partition="evaluation"),
        replace(binding, split_digest="sha256:" + "b" * 64),
    ):
        with pytest.raises(P5AssessmentError):
            project_output(**args, calibration=changed)


@pytest.mark.parametrize(
    "grade", ["rule_silver", "model_suggestion", "same_origin_simulation", "historical_expert"]
)
def test_reference_does_not_become_independent_truth(grade):
    with pytest.raises(P5AssessmentError):
        validate_reference_use(
            grade=grade,
            partition="evaluation",
            exposed_to_tested_output=False,
            previously_used_for_development=False,
        )


def test_dependency_mask_rejects_same_and_adjacent_session_leak():
    dependencies = {
        "bar:T": frozenset(),
        "return:T": frozenset({"bar:T"}),
        "lagged:T+1": frozenset({"return:T"}),
    }
    for mask in (frozenset({"bar:T"}), frozenset({"bar:T", "return:T"})):
        with pytest.raises(P5AssessmentError, match="leaks"):
            validate_dependency_mask(dependencies, artificial_mask=mask, actual_missing=frozenset())
    validate_dependency_mask(
        dependencies, artificial_mask=frozenset(dependencies), actual_missing=frozenset()
    )


@pytest.mark.parametrize("kind", ["L", "G"])
def test_real_baseline_adapter_suppresses_untrained_and_rejects_refit_failure(kind):
    pytest.importorskip("sklearn")
    adapter = BaselineAdapter(identity(kind), (2,), TemporalEncoderConfig(input_channels=1))
    rows = [row(i) for i in range(24)]
    adapter.fit(rows, partition="train")
    result = adapter.predict(rows[0].sample, rows[0].window)
    first_state = adapter.identity.checkpoint_digest
    first_rows = adapter.identity.training_rows_digest
    assert result.raw_scores[1] is not None
    assert all(value is None for index, value in enumerate(result.raw_scores) if index != 1)
    assert result.window_digest is not None
    changed = [
        replace(
            item, labels=tuple(1 - label if label is not None else None for label in item.labels)
        )
        for item in rows
    ]
    adapter.fit(changed, partition="train")
    assert adapter.identity.training_rows_digest != first_rows
    if kind == "L":
        assert adapter.identity.checkpoint_digest != first_state
    with pytest.raises(P5AssessmentError):
        adapter.fit(rows, partition="evaluation")
    with pytest.raises(P5AssessmentError, match="not fitted"):
        adapter.predict(rows[0].sample, rows[0].window)
    receipt = base_rate_receipt(rows=rows, heads=(2,), train_manifest_digest=DIGEST)
    assert receipt["base_rates"][1] == 0.5
    assert receipt["positive_counts"][1] == 12
    assert receipt["definite_applicable_counts"][1] == 24


def test_rule_adapter_preserves_existing_verdict_without_probability():
    contract = load_stage_proposition_contract(
        ROOT / "config/stage-learning/stage-proposition-bundle.v1.json"
    )
    assertion = make_assertion(
        contract,
        proposition_id=contract.by_code[10].proposition_id,
        verdict="TRUE",
        reason_codes=["synthetic"],
        fact_refs=["fact:synthetic"],
        provenance_refs=["stage10_confirmation:synthetic"],
    )
    bundle = build_bundle(
        contract,
        security_id=SAMPLE.security_id,
        knowledge_cutoff="2015-08-10T15:00:00+08:00",
        control_state="ACTIVE",
        epistemic_status="ASSERTED",
        assertions=[assertion],
    )
    result = rule_surface(sample=SAMPLE, contract=contract, bundle=bundle)
    assert result["bundle"]["context_assertions"][0]["verdict"] == "TRUE"
    assert result["calibrated_probability"] is None
    with pytest.raises(P5AssessmentError):
        rule_surface(sample=replace(SAMPLE, security_id="other"), contract=contract, bundle=bundle)
    with pytest.raises(P5AssessmentError):
        rule_surface(
            sample=SAMPLE,
            contract=contract,
            bundle=replace(bundle, knowledge_cutoff="2015-08-10T16:00:00+08:00"),
        )


def worker_paths(tmp_path):
    repo = tmp_path / "repo"
    python = repo / ".venv-stage-training/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o755)
    key = tmp_path / "key"
    key.write_text("fixture")
    return StageWorkerPaths(tmp_path / "training", repo, key)


def test_worker_submission_is_idempotent_before_and_after_claim(tmp_path):
    paths = worker_paths(tmp_path)
    first = submit_job(paths, code_revision="a" * 40, kind=P5_SYNTHETIC_JOB)
    second = submit_job(paths, code_revision="a" * 40, kind=P5_SYNTHETIC_JOB)
    assert first["job_id"] == second["job_id"]
    assert second["idempotent_reuse"] is True
    assert second["formal_seed_budget_consumed"] == 0
    queue = paths.queue / (first["job_id"] + ".json")
    queue.rename(paths.claimed / queue.name)
    submit_job(paths, code_revision="a" * 40, kind=P5_SYNTHETIC_JOB)
    assert not list(paths.queue.iterdir())


def test_worker_recovers_marker_before_queue_publication(tmp_path, monkeypatch):
    from quanthus.stage_learning import worker

    paths = worker_paths(tmp_path)
    original = worker._atomic_json

    def fail_queue(path, value):
        if path.parent == paths.queue:
            raise OSError("simulated crash")
        original(path, value)

    monkeypatch.setattr(worker, "_atomic_json", fail_queue)
    with pytest.raises(OSError):
        submit_job(paths, code_revision="a" * 40, kind=P5_SYNTHETIC_JOB)
    monkeypatch.setattr(worker, "_atomic_json", original)
    result = submit_job(paths, code_revision="a" * 40, kind=P5_SYNTHETIC_JOB)
    assert result["idempotent_reuse"] is True
    assert len(list(paths.queue.iterdir())) == 1


def test_synthetic_worker_executes_verifies_and_never_inspects_source(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    pytest.importorskip("sklearn")
    paths = worker_paths(tmp_path)
    queued = submit_job(paths, code_revision="a" * 40, kind=P5_SYNTHETIC_JOB)
    job = StageJob.from_dict(json.loads((paths.queue / (queued["job_id"] + ".json")).read_text()))
    worker = StageWorker(paths, poll_seconds=0.01)
    monkeypatch.setattr(worker, "_prepare_worktree", lambda job: ROOT)
    monkeypatch.setattr(worker, "_remove_worktree", lambda path: None)
    result = worker.run_once()
    assert result["state"] == "succeeded", result
    artifact = paths.runs / job.job_id / "p5-synthetic-v1"
    assert verify(artifact, "a" * 40)["verified"] is True
    summary = json.loads((artifact / "summary.json").read_text())
    assert summary["market_rows_read"] == summary["formal_seed_budget_consumed"] == 0
    assert summary["projected_output"]["decision_state"] == "UNAVAILABLE"
    assert summary["projected_output"]["raw_score"] is None
    assert summary["raw_scores"]["T0"] == [None] * 12
    for kind in ("L", "G"):
        receipt = summary["baseline_receipts"][kind]
        assert receipt["model_identity"]["training_rows_digest"].startswith("sha256:")
        assert (
            receipt["prediction"]["checkpoint_digest"]
            == receipt["model_identity"]["checkpoint_digest"]
        )
        assert receipt["prediction"]["window_digest"].startswith("sha256:")
    submit_job(paths, code_revision="a" * 40, kind=P5_SYNTHETIC_JOB)
    assert worker.run_once() is None
    (artifact / "summary.json").write_text("{}")
    with pytest.raises(P5AssessmentError):
        verify(artifact, "a" * 40)


def test_runner_rejects_nonfixed_config_without_training(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({**CONFIG, "market_row_read": True}))
    with pytest.raises(P5AssessmentError):
        run(config, tmp_path / "out", "a" * 40)
    assert not (tmp_path / "out").exists()
