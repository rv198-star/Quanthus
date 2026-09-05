from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from quanthus.platform.event_worker import EventWorker, _structure_input
from quanthus.platform.jobs import STRUCTURE_MVP_JOB, input_digest
from quanthus.platform.models import JobRecord

STRUCTURE_INPUT = {
    "code_revision": "a" * 40,
    "run_config": "config/structure-mvp-run.development.v1.json",
}


def _job(kind: str = STRUCTURE_MVP_JOB) -> JobRecord:
    now = datetime.now(timezone.utc)
    return JobRecord(
        job_id="job_0123456789abcdef0123456789abcdef",
        kind=kind,
        actor_user_id="usr_structure_mvp",
        idempotency_key="structure-mvp-development-v1",
        input_ref=dict(STRUCTURE_INPUT),
        input_digest=input_digest(STRUCTURE_INPUT),
        queue="event",
        concurrency_key="structure-mvp:development-v1",
        state="running",
        stage="starting",
        progress=1,
        cancel_requested=False,
        attempt=1,
        result_ref=None,
        error_reason=None,
        created_at=now,
        updated_at=now,
        claimed_by="event-test",
        claim_generation=2,
    )


class _Store:
    def __init__(self, job: JobRecord) -> None:
        self.job = job
        self.finished: list[dict[str, object]] = []

    def claim_job(self, queue: str, worker_id: str, lease_seconds: int) -> JobRecord:
        self.claim_args = (queue, worker_id, lease_seconds)
        return self.job

    def finish_claimed_job(
        self,
        job_id: str,
        generation: int,
        *,
        state: str,
        result_ref: object,
        error_reason: object,
    ) -> None:
        self.finished.append(
            {
                "job_id": job_id,
                "generation": generation,
                "state": state,
                "result_ref": result_ref,
                "error_reason": error_reason,
            }
        )


class StructureMvpWorkerTests(unittest.TestCase):
    def test_structure_input_is_exact_and_cannot_substitute_config(self) -> None:
        self.assertEqual(_structure_input(STRUCTURE_INPUT), STRUCTURE_INPUT)
        with self.assertRaisesRegex(RuntimeError, "run_config_not_governed"):
            _structure_input({**STRUCTURE_INPUT, "run_config": "/tmp/anything.json"})
        with self.assertRaisesRegex(RuntimeError, "input_contract_mismatch"):
            _structure_input({**STRUCTURE_INPUT, "command": "bash"})

    def test_event_worker_dispatches_structure_job_only_to_structure_executor(self) -> None:
        job = _job()
        store = _Store(job)
        with tempfile.TemporaryDirectory() as temporary:
            worker = EventWorker(
                store,  # type: ignore[arg-type]
                worker_id="event-test",
                run_root=Path(temporary),
                lease_seconds=300,
                renew_seconds=30.0,
                terminate_seconds=5.0,
            )
            with patch.object(worker, "_execute_structure") as execute_structure, patch.object(
                worker, "_execute"
            ) as execute_event:
                self.assertTrue(worker.run_once())
        execute_structure.assert_called_once_with(
            job.job_id,
            job.claim_generation,
            job.input_ref,
            job.input_digest,
        )
        execute_event.assert_not_called()
        self.assertEqual(store.claim_args, ("event", "event-test", 300))
        self.assertEqual(store.finished, [])

    def test_event_worker_still_rejects_unadmitted_event_queue_kind(self) -> None:
        job = _job("quanthus.unrelated.v1")
        store = _Store(job)
        with tempfile.TemporaryDirectory() as temporary:
            worker = EventWorker(
                store,  # type: ignore[arg-type]
                worker_id="event-test",
                run_root=Path(temporary),
                lease_seconds=300,
                renew_seconds=30.0,
                terminate_seconds=5.0,
            )
            self.assertTrue(worker.run_once())
        self.assertEqual(len(store.finished), 1)
        self.assertEqual(store.finished[0]["state"], "failed")
        self.assertEqual(store.finished[0]["error_reason"], "job_kind_not_admitted")


if __name__ == "__main__":
    unittest.main()
