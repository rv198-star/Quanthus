from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from quanthus.platform import event_worker as worker_module
from quanthus.platform.event_worker import EventWorker, _structure_input, _structure_request_digest
from quanthus.platform.jobs import STRUCTURE_MVP_JOB, DurableJobQueue
from quanthus.platform.persistence import InMemoryPlatformStore
from quanthus.policy import find_project_root
from quanthus.structure_order_artifact import load_structure_order_qualification
from quanthus.structure_v2_inputs import digest

from .test_structure_mvp_worker import _qualification_user

CODE = "a" * 40
REQUEST_PATH = "config/structure-order-qualification.v1.json"


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
        idempotency_key="structure-order-fixed-test",
        input_ref={"code_revision": CODE, "run_config": REQUEST_PATH},
    )
    assert created
    worker = EventWorker(
        store,
        worker_id="structure-order-test",
        run_root=tmp_path,
        lease_seconds=300,
        renew_seconds=30,
        terminate_seconds=1,
    )
    return store, worker, job


def test_structure_order_fixed_request_is_governed_without_arbitrary_config():
    inputs = {"code_revision": CODE, "run_config": REQUEST_PATH}
    assert _structure_input(inputs) == inputs
    request = load_structure_order_qualification(find_project_root() / REQUEST_PATH)
    assert _structure_request_digest(REQUEST_PATH) == digest(request)
    with pytest.raises(RuntimeError):
        _structure_input({**inputs, "run_config": "/tmp/structure-order.json"})


def test_event_worker_dispatches_only_fixed_structure_order_qualification(tmp_path, monkeypatch):
    store, worker, job = _queued_worker(tmp_path)
    request = load_structure_order_qualification(find_project_root() / REQUEST_PATH)
    monkeypatch.setattr(worker_module, "_structure_request_digest", lambda _: digest(request))
    commands = []

    def fake_verify(root, *, code_revision, request_digest):
        assert code_revision == CODE and request_digest == digest(request)
        return {
            "artifact_ref": str(root),
            "manifest_sha256": "sha256:" + "1" * 64,
            "fact_contract_digest": "sha256:" + "2" * 64,
            "structure_order_digest": "sha256:" + "3" * 64,
            "source_inputs_digest": "sha256:" + "4" * 64,
            "result_digest": "sha256:" + "5" * 64,
            "source_run_count": 2,
            "row_count": 100,
            "counts": {"available_observation_count": 100},
            "review_sample": [],
            "market_data_reads": 0,
            "dwd_provider_opens": 0,
            "stage_projection_authorized": False,
            "official_label_card_authorized": False,
            "p2_probe_count": 0,
            "p2_fill_count": 0,
            "execution_authorized": False,
        }

    class Child:
        returncode = 0

        def __init__(self, command, **kwargs):
            commands.append(command)
            assert kwargs["stdout"] and kwargs["stderr"]
            assert command[1:3] == ("-m", "quanthus.structure_order_qualification")
            assert command[command.index("--request") + 1] == REQUEST_PATH
            Path(command[command.index("--output") + 1]).mkdir(parents=True)

        def poll(self):
            return 0

    monkeypatch.setattr(worker_module, "verify_structure_order_artifact", fake_verify)
    monkeypatch.setattr(worker_module.subprocess, "Popen", Child)
    assert worker.run_once()
    completed = store.get_job(job.job_id)
    assert completed is not None and completed.state == "succeeded", completed.error_reason
    assert completed.result_ref["source_run_count"] == 2
    assert completed.result_ref["market_data_reads"] == 0
    assert completed.result_ref["dwd_provider_opens"] == 0
    assert completed.result_ref["stage_projection_authorized"] is False
    assert completed.result_ref["p2_probe_count"] == completed.result_ref["p2_fill_count"] == 0
    final = Path(completed.result_ref["artifact_ref"])
    assert final.parent == tmp_path / "structure-runs"
    assert len(commands) == 1 and not worker.run_once()
