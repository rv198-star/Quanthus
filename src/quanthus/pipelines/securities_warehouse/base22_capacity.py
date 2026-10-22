"""Execute an exact preregistered bounded experiment, never a full Base build.

Only quality statistics/hashes are emitted. Input rows remain sealed; no market
values, event text, research results or provider calls enter the report.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import resource
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import base22_bounded as engine
from .base22 import canonical, file_sha
from .base22_sources import canonical_daily_basis, physical_path


def _write(path: Path, obj: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical(obj) + b"\n")


def guarded_child(
    command: list[str], cwd: Path, budget: engine.Budget
) -> subprocess.CompletedProcess[str]:
    """Stop this owned child on a budget breach, including while it emits no output."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                budget.check()
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                try:
                    child_rss = int(Path(f"/proc/{process.pid}/statm").read_text().split()[1])
                except FileNotFoundError:
                    child_rss = 0
                budget.check(extra_rss=child_rss * os.sysconf("SC_PAGE_SIZE"))
    except BaseException:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
        raise


def cold_scan(selection: Path, root: Path) -> dict[str, Any]:
    """New process; local-only paths, source reads denied by audit hook and netns."""
    pq = importlib.import_module("pyarrow.parquet")

    plan = json.loads(selection.read_bytes())
    forbidden = plan["forbidden_roots"]
    attempts = []

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "open" and isinstance(args[0], (str, bytes)):
            path = os.path.abspath(os.fsdecode(args[0]))
            if any(path == p or path.startswith(p.rstrip("/") + "/") for p in forbidden):
                attempts.append("forbidden_source")
                raise engine.BoundedError("COLD_SOURCE_READ_FORBIDDEN")
        if event in {"socket.connect", "socket.getaddrinfo"}:
            attempts.append("network")
            raise engine.BoundedError("COLD_NETWORK_FORBIDDEN")

    sys.addaudithook(audit)
    start = time.monotonic()
    result = {}
    for sample in plan["samples"]:
        path = physical_path(root / sample["relative_path"])
        if root not in path.parents or path.stat().st_nlink != 1:
            raise engine.BoundedError("COLD_LOCAL_FILE_REQUIRED")
        if file_sha(path) != sample["sha256"]:
            raise engine.BoundedError("COLD_FILE_HASH_MISMATCH")
        schema = pq.ParquetFile(path).schema_arrow
        actual = engine.semantic_scan([path], schema, plan["batch_rows"])
        if actual != sample["semantic"]:
            raise engine.BoundedError("COLD_SCHEMA_ROW_SEMANTIC_MISMATCH")
        result[sample["label"]] = actual
    return {
        "status": "PASS",
        "samples": result,
        "elapsed_seconds": time.monotonic() - start,
        "source_or_network_attempts": attempts,
        "max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "pid": os.getpid(),
        "network_isolation": "unshare-netns required by parent",
    }


def run_experiment(selection: Path, expected_sha: str, report: Path) -> dict[str, Any]:
    pq = importlib.import_module("pyarrow.parquet")

    if file_sha(selection) != expected_sha:
        raise engine.BoundedError("EXPERIMENT_SELECTION_CHANGED")
    frozen = json.loads(selection.read_bytes())
    if frozen.get("purpose") != "p10_base22_preregistered_bounded_capacity_experiment":
        raise engine.BoundedError("EXPERIMENT_PURPOSE_INVALID")
    source = physical_path(Path(frozen["source_root"]))
    if file_sha(source / "manifest.json") != frozen["source_manifest_sha256"]:
        raise engine.BoundedError("EXPERIMENT_SOURCE_MANIFEST_CHANGED")
    limits = engine.Limits(
        batch_rows=frozen["batch_rows"],
        memory_bytes=frozen["duckdb_memory_bytes"],
        temp_bytes=frozen["max_temp_bytes"],
        allocated_bytes=frozen["max_total_allocated_write_bytes"],
        free_floor_bytes=frozen["minimum_available_bytes"],
        rss_bytes=frozen["maximum_peak_rss_bytes"],
        wall_seconds=frozen["sample_wall_limit_seconds"],
    )
    root = Path(frozen["paths"]["output"]).parent
    result: dict[str, Any] = {
        "kind": "preregistered_capacity_samples_not_image_acceptance",
        "selection_sha256": expected_sha,
        "limits": asdict(limits),
        "evaluator_sha256": file_sha(Path(__file__)),
        "engine_sha256": file_sha(Path(engine.__file__)),
        "samples": [],
        "status": "RUNNING",
    }
    forbidden_roots = [
        str(source),
        "/srv/agentdock/AgentDock/data/quanthus-ods-onedrive-20261003",
        "/srv/agentdock/AgentDock/data/quanthus-daily-sync-20261003",
    ]
    with engine.Budget(root, [*(Path(p) for p in forbidden_roots), selection], limits) as budget:
        try:
            scan_plan: dict[str, Any] = {
                "batch_rows": limits.batch_rows,
                "forbidden_roots": forbidden_roots,
                "samples": [],
            }
            for sample in frozen["samples"]:
                budget.begin_stage(sample["label"])
                start = time.monotonic()
                members = [
                    engine.Member(source / x["path"], x["sha256"], x["size_bytes"], x["rows"])
                    for x in sample["members"]
                ]
                output = Path(frozen["paths"]["output"]) / (sample["label"] + ".parquet")
                if sample["mode"] == "copy":
                    if len(members) != 1:
                        raise engine.BoundedError("COPY_SAMPLE_NOT_SINGLE_FILE")
                    disposition = engine.independent_copy(members[0], output, budget)
                else:
                    transform = (
                        (lambda batch: canonical_daily_basis(batch)[0])
                        if sample["table"] == "dwd_security_bar"
                        else None
                    )
                    disposition = engine.rewrite_partition(members, output, budget, transform)
                schema = pq.ParquetFile(output).schema_arrow
                original = engine.semantic_scan(
                    [m.path for m in members], schema, limits.batch_rows, budget.check
                )
                actual = engine.semantic_scan([output], schema, limits.batch_rows, budget.check)
                if original != actual:
                    raise engine.BoundedError("SAMPLE_REAL_SERIALIZATION_CHANGED")
                metric = {
                    "label": sample["label"],
                    "table": sample["table"],
                    "partition": sample["partition"],
                    "input_rows": sample["rows"],
                    "input_compressed_bytes": sample["compressed_bytes"],
                    "output": disposition,
                    "semantic": actual,
                    "storage": engine.storage_census(root),
                    "peak": dict(budget.peak),
                    "elapsed_seconds": time.monotonic() - start,
                }
                if sample["mode"] == "rewrite":
                    model = json.loads(
                        (
                            Path(__file__).parents[4]
                            / "config/pipelines/base22-independent-image.v1.json"
                        ).read_bytes()
                    )
                    rule = model["tables"][sample["table"]]
                    with engine.GlobalScan(budget) as global_scan:
                        global_scan.add(sample["table"], [output])
                        global_scan.keys(sample["table"], rule["business_key"], rule["version_key"])
                    metric["global_partition_key_check"] = "PASS"
                    metric["global_key_index"] = global_scan.key_metrics
                metric["elapsed_seconds"] = time.monotonic() - start
                metric["peak"] = dict(budget.peak)
                result["samples"].append(metric)
                scan_plan["samples"].append(
                    {
                        "label": sample["label"],
                        "relative_path": output.name,
                        "sha256": file_sha(output),
                        "semantic": actual,
                    }
                )
                _write(root / (sample["label"] + "-measurement.json"), metric)
                print(
                    json.dumps(
                        {
                            "sample": sample["label"],
                            "status": "PASS",
                            "elapsed_seconds": metric["elapsed_seconds"],
                        }
                    ),
                    flush=True,
                )
            # Same-device copy-on-write probe only on a disposable clone of an output.
            budget.begin_stage("copy-on-write-probes")
            first = scan_plan["samples"][0]
            original_path = Path(frozen["paths"]["output"]) / first["relative_path"]
            member = engine.Member(
                original_path,
                first["sha256"],
                original_path.stat().st_size,
                first["semantic"]["rows"],
            )
            cow = budget.root / "scratch/cow-probe.parquet"
            copy = engine.independent_copy(member, cow, budget)
            before = engine.storage_census(root)
            with cow.open("r+b") as stream:
                stream.write(b"probe destination only")
            after = engine.storage_census(root)
            if file_sha(original_path) != member.sha256:
                raise engine.BoundedError("COW_SOURCE_CHANGED")
            result["cow_probe"] = {
                "copy": copy,
                "source_unchanged": True,
                "before": before,
                "after": after,
            }
            # Explicit cross-device fallback with a tiny synthetic file; no real rows in tmpfs.
            probe = Path(frozen["paths"]["cross_device_probe"])
            with engine.Budget(probe, [], engine.Limits(free_floor_bytes=1)) as cross_budget:
                out = probe / "probe.bin"
                local = root / "scratch/cross-device-synthetic.bin"
                with local.open("xb") as stream:
                    stream.write(b"disposable CoW probe" * 4096)
                member = engine.Member(local, file_sha(local), local.stat().st_size, 0)
                result["cross_device_probe"] = engine.independent_copy(member, out, cross_budget)
                original_bytes = local.read_bytes()
                with out.open("r+b") as stream:
                    stream.write(b"destination change")
                result["cross_device_probe"]["source_unchanged"] = (
                    local.read_bytes() == original_bytes
                )
            # Two copies retained at once. Neither shares an inode with source/output.
            cold_plan = root / "cold-selection.json"
            _write(cold_plan, scan_plan)
            result["cold_copies"] = []
            for n in (1, 2):
                budget.begin_stage("independent-cold-copy-" + str(n))
                dest = Path(frozen["paths"]["cold_copy_" + str(n)])
                copies = []
                for sample in scan_plan["samples"]:
                    p = Path(frozen["paths"]["output"]) / sample["relative_path"]
                    m = engine.Member(
                        p, sample["sha256"], p.stat().st_size, sample["semantic"]["rows"]
                    )
                    copies.append(
                        engine.independent_copy(m, dest / sample["relative_path"], budget)
                    )
                cmd = [
                    "unshare",
                    "-n",
                    sys.executable,
                    "-I",
                    "-B",
                    "-m",
                    __package__ + ".base22_capacity",
                    "--cold-selection",
                    str(cold_plan),
                    "--cold-root",
                    str(dest),
                ]
                ran = guarded_child(cmd, dest, budget)
                # Errors are reduced to reason codes; never retain arbitrary exception row values.
                if ran.returncode:
                    raise engine.BoundedError("COLD_CHILD_FAILED")
                response = json.loads(ran.stdout)
                result["cold_copies"].append(
                    {"number": n, "paths": str(dest), "copies": copies, "scan": response}
                )
                _write(root / ("cold-" + str(n) + "-result.json"), result["cold_copies"][-1])
                budget.check()
            result["peak"] = dict(budget.peak)
            result["storage"] = engine.storage_census(root)
            result["status"] = "PASS_SAMPLES_ONLY"
        except Exception as exc:
            result["status"] = "FAIL"
            result["reason"] = budget.error or (
                str(exc) if isinstance(exc, engine.BoundedError) else type(exc).__name__
            )
            result["peak"] = dict(budget.peak)
            result["storage"] = engine.storage_census(root)
            _write(report, result)
            raise
    _write(report, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--expected-sha")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--cold-selection", type=Path)
    parser.add_argument("--cold-root", type=Path)
    args = parser.parse_args()
    if args.cold_selection:
        print(json.dumps(cold_scan(args.cold_selection, args.cold_root)))
    else:
        print(json.dumps(run_experiment(args.selection, args.expected_sha, args.report)))


if __name__ == "__main__":
    main()
