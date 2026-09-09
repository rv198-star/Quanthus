from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from quanthus.platform import event_worker as worker_module
from quanthus.platform.event_worker import EventWorker, _structure_input, _structure_request_digest
from quanthus.platform.jobs import STRUCTURE_MVP_JOB, DurableJobQueue
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.policy import find_project_root
from quanthus.structure_f9_run import REQUEST_PATH, load_request, write_result
from quanthus.structure_v2_inputs import digest

from .test_structure_f9_run import build
from .test_structure_mvp_worker import _qualification_user

CODE = "a" * 40


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


def queued_worker(tmp_path):
    store = DispatchStore()
    user = _qualification_user()
    store.create_user(user)
    queue = DurableJobQueue(store, {STRUCTURE_MVP_JOB: "event"})
    job, created = queue.submit(
        kind=STRUCTURE_MVP_JOB,
        actor_user_id=user.user_id,
        idempotency_key="f9-fixed-test",
        input_ref={"code_revision": CODE, "run_config": REQUEST_PATH},
    )
    assert created
    worker = EventWorker(
        store,
        worker_id="f9-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )
    return store, worker, job


def publish(root: Path):
    raw, _policy, admissions, readiness, structure, facts, projection, summary, stages = build()
    runtime = {
        "schema_version": "quanthus.structure-f9-runtime.v1",
        "provider_builds": 1,
        "cross_job_cache_hits": 0,
        "provider_lifecycle": "job_local_TMP",
        "stages": stages,
    }
    write_result(
        root,
        raw,
        admissions,
        readiness,
        structure,
        facts,
        projection,
        summary,
        runtime,
    )
    return raw


def test_f9_fixed_request_is_governed_without_opening_arbitrary_config():
    inputs = {"code_revision": CODE, "run_config": REQUEST_PATH}
    assert _structure_input(inputs) == inputs
    assert _structure_request_digest(REQUEST_PATH) == digest(
        load_request(find_project_root() / REQUEST_PATH)
    )
    with pytest.raises(RuntimeError):
        _structure_input({**inputs, "run_config": "/tmp/f9.json"})


def test_event_worker_uses_only_fixed_f9_module_and_deep_verifier(tmp_path, monkeypatch):
    store, worker, job = queued_worker(tmp_path)
    request = build()[0]
    monkeypatch.setattr(worker_module, "_structure_request_digest", lambda _: digest(request))
    commands = []

    class Child:
        returncode = 0

        def __init__(self, command, **kwargs):
            commands.append(command)
            assert kwargs["stdout"] and kwargs["stderr"]
            assert command[1:3] == ("-m", "quanthus.structure_f9_run")
            assert command[command.index("--request") + 1] == REQUEST_PATH
            publish(Path(command[command.index("--output") + 1]))

        def poll(self):
            return 0

    monkeypatch.setattr(worker_module.subprocess, "Popen", Child)
    assert worker.run_once()
    completed = store.get_job(job.job_id)
    assert completed is not None and completed.state == "succeeded", completed.error_reason
    assert completed.result_ref["p2_probe_count"] == 0
    assert completed.result_ref["p2_fill_count"] == 0
    assert completed.result_ref["official_label"] is False
    assert completed.result_ref["support_facts_digest"].startswith("sha256:")
    final = Path(completed.result_ref["artifact_ref"])
    assert final.parent == tmp_path / "structure-runs"
    assert (final / "READY").is_file()
    assert len(commands) == 1 and not worker.run_once()
