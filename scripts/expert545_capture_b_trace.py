#!/usr/bin/env python3
"""One instrumented, unchanged B reproduction from the exact accepted producer.

Run with the pinned producer's src on PYTHONPATH. Contexts are private job-local
TMP, never a new strategy, shared dataset, or market-data Git asset.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import resource
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

PRODUCER = "4199786e5a0a0fdde55ed84271b3ed703f0aa3fc"


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            value.update(block)
    return value.hexdigest()


def encode(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(type(value).__name__)


def launch_identity(repository: Path, freeze: Path) -> dict[str, Any]:
    """Snapshot launcher and the producer's whole Python/config import closure.

    This CLI runs with the old producer on PYTHONPATH, so it deliberately uses
    stdlib only until the launch record is sealed. No current-package import is
    inserted into that producer's namespace.
    """
    pins = {}

    def scan_error(error: OSError) -> None:
        raise error

    for directory in (repository / "src/quanthus", repository / "config"):
        if directory.resolve(strict=True) != directory:
            raise ValueError("TRACE_CODE_SYMLINK")
        for parent, dirs, files in os.walk(directory, onerror=scan_error, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            if any((Path(parent) / d).is_symlink() for d in dirs):
                raise ValueError("TRACE_CODE_SYMLINK")
            for name in sorted(files):
                path = Path(parent) / name
                if directory.name != "config" and path.suffix != ".py":
                    continue
                if path.resolve(strict=True) != path or not path.is_file():
                    raise ValueError("TRACE_CODE_SYMLINK")
                pins[path.relative_to(repository).as_posix()] = sha(path)
    for path in repository.iterdir():
        if path.is_file() and (
            path.name in {"pyproject.toml", "uv.lock", "poetry.lock"}
            or path.name.startswith("requirements")
            and path.suffix == ".txt"
        ):
            pins[path.name] = sha(path)
    return {
        "kind": "expert545-capture-launch-v1",
        "observed_at": datetime.now().astimezone().isoformat(),
        "producer_revision": PRODUCER,
        "producer_root": str(repository),
        "producer_code_config_sha256": pins,
        "launcher": str(Path(__file__).resolve()),
        "launcher_sha256": sha(Path(__file__)),
        "source_freeze_sha256": sha(freeze),
        "source_freeze": str(freeze),
        "python": sys.version,
        "executable": sys.executable,
        "historical_capture_backfill": False,
    }


def verify_launch(identity: dict[str, Any], repository: Path) -> None:
    current = launch_identity(repository, Path(identity["source_freeze"]))
    for key in ("producer_code_config_sha256", "launcher_sha256", "source_freeze_sha256"):
        if current[key] != identity[key]:
            raise ValueError("TRACE_LAUNCH_CHANGED:" + key)
    for name, module in tuple(sys.modules.items()):
        if name != "quanthus" and not name.startswith("quanthus."):
            continue
        origin = getattr(module, "__file__", None)
        path = Path(origin).resolve() if origin is not None else None
        if path is None or not path.is_relative_to(repository / "src"):
            raise ValueError("TRACE_IMPORTED_CODE_ORIGIN:" + name)
        key = path.relative_to(repository).as_posix()
        if identity["producer_code_config_sha256"].get(key) != sha(path):
            raise ValueError("TRACE_IMPORTED_CODE_ORIGIN:" + name)


def capture(freeze: Path, output: Path) -> dict[str, Any]:
    start = time.monotonic()
    frozen = json.loads(freeze.read_bytes())
    repository = Path(frozen["repository"])
    actual = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    if actual != PRODUCER or output.exists() or output.is_symlink():
        raise ValueError("TRACE_PRODUCER_OR_OUTPUT")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "src", "config"],
        cwd=repository,
        text=True,
    )
    if dirty.strip():
        raise ValueError("TRACE_PRODUCER_CODE_DIRTY")
    if shutil.disk_usage(output.parent).free < 8 * 1024**3 + 512 * 1024**2:
        raise ValueError("TRACE_FREE_SPACE")
    launch = launch_identity(repository, freeze)
    record = frozen["roles"]["primary"]
    for name, identity in record["bindings"].items():
        path = Path(name)
        if path.stat().st_size != identity["bytes"] or "sha256:" + sha(path) != identity["sha256"]:
            raise ValueError("TRACE_SELECTED_MEMBER_CHANGED:" + name)
    print(
        json.dumps(
            {
                "stage": "bindings_verified",
                "members": len(record["bindings"]),
                "elapsed_seconds": time.monotonic() - start,
            }
        ),
        flush=True,
    )
    # Seal before importing or invoking any producer. Old captures are not amended.
    verify_launch(launch, repository)
    output.mkdir()
    launch_path = output / "launch.frozen.json"
    with launch_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(launch, indent=2, sort_keys=True) + "\n")
    # No network source fallback may be introduced by the instrumented run.
    connect = socket.socket.connect

    def no_network(sock: socket.socket, address: Any) -> Any:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise ValueError("TRACE_NETWORK_FORBIDDEN")
        return connect(sock, address)

    socket.socket.connect = no_network  # type: ignore[method-assign]
    try:
        return _capture_runtime(repository, record, output, launch, start, freeze)
    finally:
        socket.socket.connect = connect  # type: ignore[method-assign]


def _capture_runtime(
    repository: Path,
    record: dict[str, Any],
    output: Path,
    launch: dict[str, Any],
    start: float,
    freeze: Path,
) -> dict[str, Any]:
    from quanthus.expert_strategy.p3_restructuring_successor import RestructuringRuntime

    from quanthus.expert_strategy.formal_p2_replay import FormalP2ReplayRuntime
    from quanthus.expert_strategy.runner import FormalStrategyRunner

    verify_launch(launch, repository)
    current: dict[str, Any] = {}
    reviews = FormalP2ReplayRuntime._review_positions
    evaluate = FormalStrategyRunner.evaluate_held
    trace_count = 0
    with gzip.open(output / "held-contexts.private.jsonl.gz", "wt", encoding="utf-8") as trace:

        def review(self: Any, frame_index: Any, frame: Any, runtimes: Any, *args: Any) -> Any:
            current.update(frame=frame, runtimes=runtimes)
            return reviews(self, frame_index, frame, runtimes, *args)

        def observed(self: Any, **kwargs: Any) -> Any:
            nonlocal trace_count
            result = evaluate(self, **kwargs)
            facts = kwargs["position_facts"]
            unit_id = facts.evidence.evidence_id.removeprefix("position-facts:")
            runtime = current["runtimes"][unit_id]
            row = {
                "market_date": current["frame"].snapshot.market_date,
                "unit_id": unit_id,
                "security_code": runtime.panel_security_id,
                "context": asdict(kwargs["context"]),
                "position_facts": asdict(facts),
                "event_id": kwargs["event_id"],
                "base_required_action": kwargs["base_required_action"],
                "base_reason_code": kwargs["base_reason_code"],
                "intervention_id": kwargs["intervention_id"],
                "quantity": runtime.ledger.settled_quantity,
                "settled_quantity": runtime.ledger.settled_quantity,
                "target_notional": runtime.ledger.target_notional,
                "last_mark_price": runtime.ledger.last_mark_price,
                "baseline_decision_digest": result.digest,
                "baseline_action": result.action.final_action.value,
            }
            trace.write(json.dumps(row, default=encode, sort_keys=True) + "\n")
            trace_count += 1
            return result

        FormalP2ReplayRuntime._review_positions = review  # type: ignore[method-assign]
        FormalStrategyRunner.evaluate_held = observed  # type: ignore[method-assign]
        try:
            admission = json.loads(Path(record["admission"]).read_bytes())
            with RestructuringRuntime(
                repository,
                Path(record["source"]),
                Path(record["prepared"]),
                admission,
                parent_source=Path(record["parent_source"]),
                prior_prepared=Path(record["prior_prepared"]),
            ) as runtime:
                # Explicit effective dataset paths, not guessed legacy roots.
                members = {
                    d.dataset: [
                        str(p) for p in runtime.bundle.historical_bundle.partition_paths(d.dataset)
                    ]
                    for d in runtime.bundle.historical_bundle.datasets
                }
                (output / "effective-members.json").write_text(json.dumps(members, indent=2) + "\n")
                print(
                    json.dumps(
                        {"stage": "runtime_ready", "elapsed_seconds": time.monotonic() - start}
                    ),
                    flush=True,
                )
                _, artifacts = runtime.run_variant("B", output)
                comparisons = {}
                for name in ("decisions.json", "engine-events.json", "manifest.json"):
                    before = Path(record["reference_work"]) / "replays/variant.B" / name
                    actual_sha = hashlib.sha256(artifacts[name]).hexdigest()
                    comparisons[name] = {
                        "before_sha256": sha(before),
                        "after_sha256": actual_sha,
                        "equal": actual_sha == sha(before),
                    }
                if not all(item["equal"] for item in comparisons.values()) or trace_count != 8393:
                    raise ValueError("TRACE_BASELINE_REPRODUCTION_MISMATCH")
        finally:
            FormalP2ReplayRuntime._review_positions = reviews  # type: ignore[method-assign]
            FormalStrategyRunner.evaluate_held = evaluate  # type: ignore[method-assign]
    verify_launch(launch, repository)
    result = {
        "kind": "expert545-unchanged-B-trace-v1",
        "producer_revision": PRODUCER,
        "source_freeze_sha256": sha(freeze),
        "capture_launch_sha256": sha(output / "launch.frozen.json"),
        "rows": trace_count,
        "comparisons": comparisons,
        "trace_sha256": sha(output / "held-contexts.private.jsonl.gz"),
        "elapsed_seconds": time.monotonic() - start,
        "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "new_scientific_result": False,
        "network_allowed": False,
    }
    (output / "receipt.safe.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(capture(arguments.freeze, arguments.output), indent=2))
