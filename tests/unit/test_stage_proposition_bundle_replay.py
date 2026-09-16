from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from quanthus.platform import event_worker as worker_module
from quanthus.platform.event_worker import EventWorker, _structure_input, _structure_request_digest
from quanthus.platform.jobs import STRUCTURE_MVP_JOB, DurableJobQueue
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.policy import find_project_root
from quanthus.stage_proposition_bundle_replay import load_replay_request
from quanthus.structure_v2_inputs import digest

from .test_structure_mvp_worker import _qualification_user

CODE = "b" * 40
REQUEST_PATH = "config/stage-learning/stage-proposition-bundle-real-data-replay.v1.json"


class DispatchStore(InMemoryPlatformStore):
    def claim_job(self, queue, worker_id, lease_seconds):
        for job in self.list_jobs():
            if job.queue == queue and job.state == "queued":
                claimed = replace(
                    job,
                    state="running",
                    claim_generation=1,
                    claimed_by=worker_id,
                    attempt=1,
                )
                self.update_job(claimed)
                return claimed
        return None

    def update_claimed_job(self, job_id, generation, **changes):
        job = self.get_job(job_id)
        assert job is not None and job.claim_generation == generation
        self.update_job(replace(job, **changes))

    def finish_claimed_job(self, job_id, generation, **changes):
        job = self.get_job(job_id)
        assert job is not None and job.claim_generation == generation
        self.finish_job(replace(job, **changes))


def _queued_worker(tmp_path):
    store = DispatchStore()
    user = _qualification_user()
    store.create_user(user)
    queue = DurableJobQueue(store, {STRUCTURE_MVP_JOB: "event"})
    job, created = queue.submit(
        kind=STRUCTURE_MVP_JOB,
        actor_user_id=user.user_id,
        idempotency_key="p029-stage-bundle-replay-fixed-test",
        input_ref={"code_revision": CODE, "run_config": REQUEST_PATH},
    )
    assert created
    worker = EventWorker(
        store,
        worker_id="p029-replay-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )
    return store, worker, job


def test_p029_request_freezes_retrospective_real_history_without_pit_claim() -> None:
    request = load_replay_request(find_project_root() / REQUEST_PATH)
    assert request["work_item"] == "P3-STAGE-029"
    assert request["source_f9"]["knowledge_view"] == "retrospective"
    assert request["population"]["expected_row_count"] == 10
    assert request["knowledge_boundary"]["strict_point_in_time_claim_authorized"] is False
    assert request["knowledge_boundary"]["strict_pit_gate"] == (
        "BLOCKED_BY_RETROSPECTIVE_SOURCE_RECORD_VIEW"
    )
    assert request["authority"]["new_market_data_read_authorized"] is False
    assert request["authority"]["dwd_provider_open_authorized"] is False
    assert request["authority"]["development_shadow_consumption_authorized"] is True
    assert request["authority"]["production_expert_consumption_authorized"] is False
    assert (
        request["views"]["counterfactual_single_code"]["historical_or_production_policy_claimed"]
        is False
    )


def test_p029_fixed_request_is_governed_without_arbitrary_config() -> None:
    inputs = {"code_revision": CODE, "run_config": REQUEST_PATH}
    assert _structure_input(inputs) == inputs
    request = load_replay_request(find_project_root() / REQUEST_PATH)
    assert _structure_request_digest(REQUEST_PATH) == digest(request)
    with pytest.raises(RuntimeError):
        _structure_input({**inputs, "run_config": "/tmp/p029.json"})


def test_event_worker_dispatches_only_fixed_p029_replay(tmp_path, monkeypatch) -> None:
    store, worker, job = _queued_worker(tmp_path)
    request = load_replay_request(find_project_root() / REQUEST_PATH)
    monkeypatch.setattr(worker_module, "_structure_request_digest", lambda _: digest(request))
    commands = []

    def fake_verify(root, *, code_revision, request_digest):
        assert code_revision == CODE and request_digest == digest(request)
        return {
            "artifact_ref": str(root),
            "manifest_sha256": "sha256:" + "1" * 64,
            "result_digest": "sha256:" + "2" * 64,
            "row_count": 10,
            "p2_probe_count": 0,
            "p2_fill_count": 0,
            "nested_10_11_count": 6,
            "context_only_count": 4,
            "single_code_parent_elision_count": 6,
            "layered_parent_elision_count": 0,
            "layered_retained_count": 16,
            "knowledge_view": "retrospective",
            "strict_pit_claim": False,
            "market_data_reads": 0,
            "dwd_provider_opens": 0,
            "interpretation_result": (
                "PASS_LAYERED_REPRESENTATION_PRESERVES_EXPLICIT_PARENT_AND_NESTED_SEMANTICS"
            ),
            "execution_authorized": False,
        }

    class Child:
        returncode = 0

        def __init__(self, command, **kwargs):
            commands.append(command)
            assert kwargs["stdout"] and kwargs["stderr"]
            assert command[1:3] == ("-m", "quanthus.stage_proposition_bundle_replay")
            assert command[command.index("--request") + 1] == REQUEST_PATH
            Path(command[command.index("--output") + 1]).mkdir(parents=True)

        def poll(self):
            return 0

    monkeypatch.setattr(
        worker_module,
        "verify_stage_proposition_bundle_replay_artifact",
        fake_verify,
    )
    monkeypatch.setattr(worker_module.subprocess, "Popen", Child)
    assert worker.run_once()
    completed = store.get_job(job.job_id)
    assert completed is not None and completed.state == "succeeded", completed.error_reason
    assert completed.result_ref["row_count"] == 10
    assert completed.result_ref["single_code_parent_elision_count"] == 6
    assert completed.result_ref["layered_parent_elision_count"] == 0
    assert completed.result_ref["strict_pit_claim"] is False
    assert completed.result_ref["market_data_reads"] == 0
    assert completed.result_ref["dwd_provider_opens"] == 0
    assert len(commands) == 1 and not worker.run_once()
