"""PostgreSQL-leased worker for bounded P4 Event shard tasks."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from quanthus.event_library.shard_tasks import execute_event_shard_task
from quanthus.platform.persistence import PostgresPlatformStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded Quanthus Event shard worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--renew-seconds", type=float, default=30.0)
    parser.add_argument("--execute-task")
    parser.add_argument("--result")
    return parser


def _execute_file(task_path: Path, result_path: Path) -> int:
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("event shard task must be an object")
    result = execute_event_shard_task(payload)
    result_path.write_text(
        json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


class EventShardWorker:
    def __init__(
        self,
        store: PostgresPlatformStore,
        *,
        worker_id: str,
        run_root: Path,
        lease_seconds: int,
        renew_seconds: float,
    ) -> None:
        self.store = store
        self.worker_id = worker_id
        self.run_root = run_root.resolve()
        self.lease_seconds = lease_seconds
        self.renew_seconds = renew_seconds
        self.stopping = False

    def run_once(self) -> bool:
        shard = self.store.claim_job_shard(self.worker_id, self.lease_seconds)
        if shard is None:
            return False
        claim_root = (
            self.run_root
            / ".shards"
            / shard.parent_job_id
            / "claims"
            / shard.shard_id
            / f"generation-{shard.claim_generation}"
        )
        claim_root.mkdir(parents=True, exist_ok=False)
        task_path = claim_root / "task.json"
        result_path = claim_root / "result.json"
        stderr_path = claim_root / "stderr.log"
        def bind_generation(value: Any) -> Any:
            if isinstance(value, str):
                return value.replace("{claim_generation}", str(shard.claim_generation))
            if isinstance(value, list):
                return [bind_generation(item) for item in value]
            if isinstance(value, dict):
                return {name: bind_generation(item) for name, item in value.items()}
            return value

        task_payload = bind_generation(shard.payload)
        task_path.write_text(
            json.dumps(task_payload, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                (
                    sys.executable,
                    "-m",
                    "quanthus.platform.event_shard_worker",
                    "--execute-task",
                    str(task_path),
                    "--result",
                    str(result_path),
                ),
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )
        last_renewal = time.monotonic()
        lost_claim = False
        while process.poll() is None:
            time.sleep(1)
            if self.stopping:
                process.terminate()
            if time.monotonic() - last_renewal >= self.renew_seconds:
                if not self.store.renew_job_shard_lease(
                    shard.parent_job_id,
                    shard.shard_id,
                    shard.claim_generation,
                    self.worker_id,
                    self.lease_seconds,
                ):
                    lost_claim = True
                    process.terminate()
                last_renewal = time.monotonic()
        if lost_claim:
            return True
        if process.returncode != 0 or not result_path.is_file():
            reason = stderr_path.read_text(encoding="utf-8", errors="replace")[-400:]
            self.store.finish_job_shard(
                shard.parent_job_id,
                shard.shard_id,
                shard.claim_generation,
                state="failed",
                result_ref=None,
                error_reason=f"shard_child_exit_{process.returncode}:{reason}"[:500],
            )
            return True
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise RuntimeError("event shard result must be an object")
        self.store.finish_job_shard(
            shard.parent_job_id,
            shard.shard_id,
            shard.claim_generation,
            state="succeeded",
            result_ref=result,
            error_reason=None,
        )
        return True


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.execute_task:
        if not args.result:
            raise RuntimeError("--result is required with --execute-task")
        return _execute_file(Path(args.execute_task), Path(args.result))
    dsn = os.environ.get("QUANTHUS_PLATFORM_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("QUANTHUS_PLATFORM_DATABASE_URL is required")
    worker = EventShardWorker(
        PostgresPlatformStore(dsn),
        worker_id=os.environ.get("QUANTHUS_WORKER_ID", "").strip() or socket.gethostname(),
        run_root=Path(os.environ.get("QUANTHUS_EVENT_RUN_ROOT", "/data/event-runs")),
        lease_seconds=args.lease_seconds,
        renew_seconds=args.renew_seconds,
    )

    def stop(_signum: int, _frame: Any) -> None:
        worker.stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not worker.stopping:
        worked = worker.run_once()
        if args.once:
            return 0
        if not worked:
            time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
