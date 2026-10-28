#!/usr/bin/env python3
"""Run the fixed March-cutoff successor on the retained accounting engine.

This is orchestration only. The versioned adapter owns the entry projection;
the retained runtime owns allocation, costs, actions, exits and reconciliation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import resource
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "config/research/learning-mvp/entry-march-confirmation.v1.json"
ADAPTER = "src/quanthus/expert_strategy/entry_march_capital.py"


def read(path: Path | str) -> Any:
    return json.loads(Path(path).read_bytes())


def write(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def sha(path: Path | str) -> str:
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def size(root: Path) -> int:
    total = 0
    for current, _, names in os.walk(root, onerror=lambda error: (_ for _ in ()).throw(error)):
        for name in names:
            try:
                total += (Path(current) / name).stat().st_size
            except FileNotFoundError:
                # Concurrently removed job-owned temporary files consume no space.
                continue
    return total


def guard(job: Path) -> None:
    budget = read(job / "evidence/budget.frozen.json")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(budget["deadline_UTC"]):
        raise ValueError("ENTRY_DEADLINE")
    for root, key in (
        (job, "maximum_data_job_logical_bytes"),
        (Path(budget["control"]), "maximum_system_job_logical_bytes"),
    ):
        if (
            shutil.disk_usage(root).free < budget["minimum_available_bytes_per_written_device"]
            or size(root) > budget[key]
        ):
            raise ValueError("ENTRY_CAPACITY")
    if (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        > budget["maximum_process_RSS_bytes"]
    ):
        raise ValueError("ENTRY_RSS")


def pin(path: Path) -> dict[str, Any]:
    return {"bytes": path.stat().st_size, "sha256": sha(path)}


def verify(pins: dict[str, Any]) -> None:
    for name, expected in pins.items():
        path = Path(name)
        if path.is_symlink() or pin(path) != expected:
            raise ValueError("ENTRY_CAPITAL_INPUT_CHANGED:" + name)


def stage_path(job: Path, attempt: int) -> Path:
    if attempt < 1 or attempt > 3:
        raise ValueError("ENTRY_CAPITAL_ATTEMPT")
    return (
        job
        / "private"
        / ("capital-stage" + ("" if attempt == 1 else f"-v{attempt}") + ".frozen.json")
    )


def freeze(job: Path, inventory: Path, old_repository: Path, attempt: int = 1) -> dict[str, Any]:
    guard(job)
    old = read(inventory)
    baseline = read(old["baseline_freeze"])
    role = {
        k: baseline["roles"]["primary"][k]
        for k in ("source", "parent_source", "prepared", "prior_prepared", "admission")
    }
    pins = {
        name: {"bytes": p["bytes"], "sha256": p["sha256"].removeprefix("sha256:")}
        for name, p in old["runtime_pins"].items()
    }
    verify(pins)
    for path in (
        job / "private/predictions87.json",
        job / "private/checkpoint.json",
        ROOT / CONTRACT,
        ROOT / ADAPTER,
        Path(__file__),
        inventory,
    ):
        pins[str(path)] = pin(path)
    code = {
        str(p): pin(p)
        for directory in ("src", "config")
        for p in sorted((old_repository / directory).rglob("*"))
        if p.is_file() and p.suffix in {".py", ".json"}
    }
    now = datetime.now(timezone.utc).isoformat()
    result = {
        "kind": "entry-march-complete-capital-freeze-v1",
        "UTC": now,
        "old_repository": str(old_repository),
        "old_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=old_repository, text=True
        ).strip(),
        "successor_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "pins": pins,
        "old_code_pins": code,
        "role": role,
        "adapter": str(ROOT / ADAPTER),
        "contract": str(ROOT / CONTRACT),
        "arms": ["C0", "C1", "C2"],
        "attempt": attempt,
        "primary_output": str(
            job / "private" / ("capital-primary" if attempt == 1 else f"capital-primary-v{attempt}")
        ),
        "cold_output": str(job / "private/capital-independent"),
        "preexisting_job_bytes": size(job),
        "planned_maximum_additional_bytes": 2300000000 if attempt == 1 else 100 * 2**20,
        "simultaneously_live": [
            "existing shared Facts/ref/fit",
            "three primary arms",
            "one independent physical source/code copy",
            "three cold arms",
        ],
        "temporary": str(job / "tmp/capital"),
        "workers": 1,
        "snapshot": [
            {
                "path": str(p),
                "device": p.stat().st_dev,
                "available_bytes": shutil.disk_usage(p).free,
                "available_inodes": os.statvfs(p).f_favail,
                "UTC": now,
            }
            for p in (job, Path(read(job / "evidence/budget.frozen.json")["control"]))
        ],
        "stops": read(job / "evidence/budget.frozen.json"),
        "research_only": True,
        "no_source_acquisition": True,
    }
    if (
        result["preexisting_job_bytes"] + result["planned_maximum_additional_bytes"]
        > result["stops"]["maximum_data_job_logical_bytes"]
    ):
        raise ValueError("ENTRY_CAPITAL_WRITE_BUDGET")
    write(stage_path(job, attempt), result)
    return {
        "frozen_sha256": sha(stage_path(job, attempt)),
        "runtime_members": len(old["runtime_pins"]),
        "planned_new_bytes": result["planned_maximum_additional_bytes"],
    }


def run(job: Path, attempt: int = 1) -> dict[str, Any]:
    guard(job)
    frozen = read(stage_path(job, attempt))
    verify(frozen["pins"])
    verify(frozen["old_code_pins"])
    diagnostic = read(job / "evidence/fit-diagnostic.safe.json")
    if (
        diagnostic["known_c1_c2_differences"] == 0
        or diagnostic["difference_dates"] < 2
        or diagnostic["difference_issuers"] < 2
    ):
        raise ValueError("ENTRY_CAPITAL_FUTILITY")
    deadline = datetime.fromisoformat(frozen["stops"]["deadline_UTC"])
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("ENTRY_DEADLINE")))
    signal.alarm(max(1, int((deadline - datetime.now(timezone.utc)).total_seconds())))
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(5):
            try:
                guard(job)
            except Exception as error:
                write(
                    job / "evidence" / f"capital-watch-attempt{attempt}-failure.safe.json",
                    {"reason": str(error), "UTC": datetime.now(timezone.utc).isoformat()},
                )
                os._exit(75)

    threading.Thread(target=watch, daemon=True).start()
    RestructuringRuntime = importlib.import_module(
        "quanthus.expert_strategy.p3_restructuring_successor"
    ).RestructuringRuntime

    adapter_spec = importlib.util.spec_from_file_location("entry_march_adapter", frozen["adapter"])
    assert adapter_spec is not None and adapter_spec.loader is not None
    adapter = importlib.util.module_from_spec(adapter_spec)
    adapter_spec.loader.exec_module(adapter)
    runtime = adapter.runtime_type(RestructuringRuntime)
    role = frozen["role"]
    root = Path(frozen["primary_output"])
    root.mkdir()
    started = time.monotonic()
    result: dict[str, Any] = {"arms": {}, "consumer_admission": "NOT_GRANTED"}
    try:
        with runtime(
            Path(frozen["old_repository"]),
            Path(role["source"]),
            Path(role["prepared"]),
            read(role["admission"]),
            parent_source=Path(role["parent_source"]),
            prior_prepared=Path(role["prior_prepared"]),
            predictions=read(job / "private/predictions87.json"),
            successor_request=read(frozen["contract"]),
        ) as engine:
            result["frames"] = len(engine.frames)
            result["opportunities"] = len(engine.events)
            for arm in frozen["arms"]:
                guard(job)
                arm_start = time.monotonic()
                target = root / arm
                target.mkdir()
                _, artifacts = engine.run_variant(arm, target)
                for name, raw in artifacts.items():
                    with (target / name).open("xb") as stream:
                        stream.write(raw)
                reconciliation = read(target / "reconciliation.json")
                if not reconciliation["reconciled"] or reconciliation["sealed_partition_access"]:
                    raise ValueError("ENTRY_CAPITAL_RECONCILIATION")
                result["arms"][arm] = {
                    "files": {name: pin(target / name) for name in sorted(artifacts)},
                    "reconciliation": reconciliation,
                    "elapsed_seconds": time.monotonic() - arm_start,
                }
                print(
                    json.dumps(
                        {"arm": arm, "status": "RECONCILED", "fills": reconciliation["fill_count"]}
                    ),
                    flush=True,
                )
        result.update(
            {
                "elapsed_seconds": time.monotonic() - started,
                "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                "UTC": datetime.now(timezone.utc).isoformat(),
            }
        )
        write(job / "evidence" / f"capital-primary-attempt{attempt}.safe.json", result)
        return result
    finally:
        stop.set()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("freeze", "run"))
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--old-repository", type=Path)
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    value = (
        freeze(args.job, args.inventory, args.old_repository, args.attempt)
        if args.command == "freeze"
        else run(args.job, args.attempt)
    )
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
