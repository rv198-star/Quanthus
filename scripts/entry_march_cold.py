#!/usr/bin/env python3
"""Explicit independent construction for the fixed87 March-cutoff experiment.

Original path strings remain semantic provenance. Every actual source read is
resolved to an independently copied, verified file; kernel isolation rejects
original inputs, primary outputs and network access, including native Arrow.
"""

from __future__ import annotations

import argparse
import ast
import ctypes
import ctypes.util
import errno
import importlib.util
import json
import os
import resource
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from entry_march_capital import guard, pin, read, sha, size, verify, write

ROOT = Path(__file__).resolve().parents[1]
VENV = Path(sys.executable).parent.parent


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def prepare(job: Path) -> dict[str, Any]:
    guard(job)
    stage = read(job / "private/capital-stage.frozen.json")
    verify(stage["pins"])
    verify(stage["old_code_pins"])
    frozen = read(job / "inputs.frozen.private.json")
    members = dict(frozen["pins"])
    inventory = read(
        next(p for p in stage["pins"] if p.endswith("runtime-training.freeze.private.json"))
    )
    members.update(inventory["runtime_pins"])
    members.update(read(job / "private/training-members.frozen.json"))
    spec = frozen["spec"]
    members[spec["rubric"]] = pin(Path(spec["rubric"]))
    total = sum(p["bytes"] for p in members.values())
    cold = job / "private/cold-role"
    plan = {
        "kind": "entry-march-independent-copy-plan-v1",
        "UTC": datetime.now(timezone.utc).isoformat(),
        "inputs": members,
        "input_bytes": total,
        "maximum_new_bytes": total + 160 * 2**20,
        "existing_job_bytes": size(job),
        "source_producer_revision": "d1b347a1",
        "fit_revision": "6571e2060e84b9badd7fb6ac960d40a082da0942",
        "old_revision": stage["old_revision"],
        "capital_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "destination": str(cold),
        "copy": "independent files, no hardlinks/reflinks/symlinks",
        "order": [
            "freeze",
            "copy/hash",
            "versioned code snapshots",
            "Facts+fit process",
            "capital process",
            "exact semantic and byte comparison",
        ],
        "workers": 1,
        "retention": "Retain this one cold source and its results through handoff",
        "stops": stage["stops"],
        "snapshot": [
            {
                "path": str(p),
                "device": p.stat().st_dev,
                "available_bytes": shutil.disk_usage(p).free,
                "available_inodes": os.statvfs(p).f_favail,
            }
            for p in (job, Path(stage["stops"]["control"]))
        ],
    }
    if (
        plan["existing_job_bytes"] + plan["maximum_new_bytes"]
        > stage["stops"]["maximum_data_job_logical_bytes"]
    ):
        raise ValueError("ENTRY_COLD_WRITE_BUDGET")
    write(job / "private/cold-copy-plan.frozen.json", plan)
    cold.mkdir()
    mapping = {}
    for number, (name, expected) in enumerate(sorted(members.items())):
        if number % 25 == 0:
            guard(job)
        original = Path(name)
        physical = cold / "fs" / original.relative_to("/")
        physical.parent.mkdir(parents=True, exist_ok=True)
        if original.is_symlink() or not original.is_file():
            raise ValueError("ENTRY_COLD_INPUT_PHYSICAL")
        with original.open("rb") as source, physical.open("xb") as target:
            shutil.copyfileobj(source, target, 4 * 1024 * 1024)
        identity = {
            "bytes": expected["bytes"],
            "sha256": expected["sha256"].removeprefix("sha256:"),
        }
        if (
            pin(original) != identity
            or pin(physical) != identity
            or original.stat().st_ino == physical.stat().st_ino
            or physical.stat().st_nlink != 1
        ):
            raise ValueError("ENTRY_COLD_COPY_IDENTITY")
        mapping[name] = str(physical)
    code = {}
    for name, repository, revision in (
        ("producer", ROOT, "d1b347a1"),
        ("successor", ROOT, plan["capital_revision"]),
        ("retained", Path(stage["old_repository"]), stage["old_revision"]),
    ):
        output = cold / "code" / name
        output.mkdir(parents=True)
        archive = cold / (name + ".tar")
        with archive.open("xb") as stream:
            subprocess.run(
                [
                    "git",
                    "archive",
                    revision,
                    "src",
                    "config",
                    "scripts",
                    "pyproject.toml",
                    "tests/fixtures",
                ],
                cwd=repository,
                stdout=stream,
                check=True,
            )
        with tarfile.open(archive) as handle:
            for item in handle.getmembers():
                if (
                    item.issym()
                    or item.islnk()
                    or Path(item.name).is_absolute()
                    or ".." in Path(item.name).parts
                ):
                    raise ValueError("ENTRY_COLD_CODE_ARCHIVE")
            handle.extractall(output)
        # Archives are successful job-owned transport TMP; content already verified
        # against Git and retained as expanded immutable code, so remove only these.
        archive.unlink()
        code[name] = str(output)
    role = cold / "role"
    for directory in ("private", "reference", "evidence", "tmp"):
        (role / directory).mkdir(parents=True, exist_ok=True)
    for name in (
        "inputs.frozen.private.json",
        "private/training-members.frozen.json",
        "evidence/budget.frozen.json",
        "reference/commit-A.json",
        "reference/commit-B.json",
    ):
        shutil.copyfile(job / name, role / name)
    ledger = {
        "mapping": mapping,
        "input_pins": members,
        "code": code,
        "role": str(role),
        "job": str(job),
        "capital_stage": stage,
        "stops": stage["stops"],
        "copied_members": len(mapping),
        "copied_bytes": total,
        "code_pins": {
            str(p): pin(p) for r in code.values() for p in sorted(Path(r).rglob("*")) if p.is_file()
        },
        "expected": {
            name: sha(job / "private" / name)
            for name in ("facts87.json", "training20.json", "checkpoint.json", "predictions87.json")
        },
        "expected_packet_sha256": sha(job / "reference/packet.json"),
    }
    write(cold / "copy-ledger.frozen.json", ledger)
    return {
        "members": len(mapping),
        "bytes": total,
        "cold": str(cold),
        "ledger_sha256": sha(cold / "copy-ledger.frozen.json"),
    }


def isolation(cold: Path, ledger: dict[str, Any]) -> dict[str, Any]:
    """Reuse the exact retained production isolation function, without its CLI imports."""
    path = (
        Path(ledger["code"]["retained"]) / "src/quanthus/expert_strategy/p3_historical_controls.py"
    )
    tree = ast.parse(path.read_text())
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "isolate_role"
    )
    namespace: dict[str, Any] = {
        "ctypes": ctypes,
        "os": os,
        "errno": errno,
        "socket": socket,
        "Path": Path,
        "Any": Any,
        "require": lambda test, message: (
            None if test else (_ for _ in ()).throw(ValueError(message))
        ),
    }
    exec(compile(ast.Module([function], type_ignores=[]), str(path), "exec"), namespace)
    stage = ledger["capital_stage"]
    source_probe = next(p for p in stage["pins"] if p.endswith("/daily_ohlcv_none.parquet"))
    feature_probe = next(p for p in stage["pins"] if p.endswith("/features.parquet"))
    job = Path(ledger["job"])
    forbidden = [
        Path(source_probe),
        Path(feature_probe),
        job / "private/facts87.json",
        job / "private/checkpoint.json",
        job / "private/predictions87.json",
    ]
    allowed = [
        cold,
        VENV,
        Path("/usr"),
        Path("/lib"),
        Path("/lib64"),
        Path("/etc"),
        Path("/dev"),
        Path("/proc"),
        Path("/sys"),
    ]
    result = namespace["isolate_role"](allowed, forbidden)
    if not isinstance(result, dict):
        raise ValueError("ENTRY_COLD_ISOLATION_RECEIPT")
    return result


def cold_guard(cold: Path, ledger: dict[str, Any]) -> None:
    budget = ledger["stops"]
    if datetime.now(timezone.utc) >= datetime.fromisoformat(budget["deadline_UTC"]):
        raise TimeoutError("ENTRY_COLD_DEADLINE")
    if (
        shutil.disk_usage(cold).free < budget["minimum_available_bytes_per_written_device"]
        or resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        > budget["maximum_process_RSS_bytes"]
    ):
        raise ValueError("ENTRY_COLD_RESOURCE")


def facts(cold: Path) -> dict[str, Any]:
    ledger = read(cold / "copy-ledger.frozen.json")
    verify(ledger["code_pins"])
    for name, target in ledger["mapping"].items():
        expected = ledger["input_pins"][name]
        if (
            pin(Path(target))
            != {"bytes": expected["bytes"], "sha256": expected["sha256"].removeprefix("sha256:")}
            or Path(target).stat().st_nlink != 1
        ):
            raise ValueError("ENTRY_COLD_INPUT_IDENTITY")
    proof = isolation(cold, ledger)
    role = Path(ledger["role"])
    producer_root = Path(ledger["code"]["producer"])
    sys.path.insert(0, str(producer_root / "scripts"))
    producer = load(
        "entry_march_confirmation", producer_root / "scripts/entry_march_confirmation.py"
    )
    mapping = ledger["mapping"]

    def physical(path: Any) -> Any:
        return mapping.get(str(path), path)

    def read_mapped(path: Any) -> Any:
        return read(physical(path))

    def verify_mapped(pins: dict[str, Any]) -> None:
        for name, expected in pins.items():
            path = Path(physical(name))
            if (
                path.is_symlink()
                or path.stat().st_size != expected["bytes"]
                or sha(path) != expected["sha256"].removeprefix("sha256:")
            ):
                raise ValueError("ENTRY_COLD_PIN_CHANGED")

    producer.read = read_mapped
    producer.verify = verify_mapped
    producer.file_sha = lambda path: sha(physical(path))
    producer.guard = lambda _: cold_guard(cold, ledger)
    import duckdb
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    native_read = pq.read_table
    pq.read_table = lambda path, *a, **k: native_read(physical(path), *a, **k)
    native_connect = duckdb.connect

    class Connection:
        def __init__(self, *args: Any, **kwargs: Any):
            self.connection = native_connect(*args, **kwargs)

        def __enter__(self) -> Any:
            self.connection.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self.connection.__exit__(*args)

        def execute(self, query: str, parameters: Any = None) -> Any:
            mapped = (
                None
                if parameters is None
                else [physical(v) if isinstance(v, str) else v for v in parameters]
            )
            return self.connection.execute(query, mapped)

    duckdb.connect = Connection  # type: ignore[assignment]
    if (role / "evidence/qualification.safe.json").exists():
        # Resume after a transport-only failure: the independently constructed
        # immutable Facts must already equal the sealed primary digests.
        for name in ("facts87.json", "training20.json"):
            if sha(role / "private" / name) != ledger["expected"][name]:
                raise ValueError("ENTRY_COLD_PARTIAL_FACTS_CHANGED")
        if sha(role / "reference/packet.json") != ledger["expected_packet_sha256"]:
            raise ValueError("ENTRY_COLD_PARTIAL_PACKET_CHANGED")
        result = read(role / "evidence/qualification.safe.json")
    else:
        result = producer.qualify(role)
    fit_path = Path(ledger["code"]["successor"]) / "scripts/entry_march_fit.py"
    fit = load("entry_march_fit", fit_path)
    fit.file_sha = lambda path: sha(physical(path))
    fit.gate(role)
    fit.fit(role)
    comparisons = {
        name: sha(role / "private" / name) == expected
        for name, expected in ledger["expected"].items()
    }
    comparisons["packet.json"] = (
        sha(role / "reference/packet.json") == ledger["expected_packet_sha256"]
    )
    if not all(comparisons.values()):
        raise ValueError("ENTRY_COLD_FACTS_FIT_MISMATCH:" + json.dumps(comparisons))
    receipt = {
        "kind": "entry-march-cold-facts-fit-v1",
        "byte_equal": comparisons,
        "isolation": proof,
        "rebuilt_facts_rows": result["opportunities"],
        "independent_fit": True,
        "source_requests": 0,
        "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    }
    write(cold / "facts-fit.safe.json", receipt)
    return receipt


def capital(cold: Path, attempt: int = 1, run_number: int = 1) -> dict[str, Any]:
    ledger = read(
        cold
        / ("copy-ledger.frozen.json" if attempt == 1 else f"copy-ledger-v{attempt}.frozen.json")
    )
    verify(ledger["code_pins"])
    if (cold / "metadata-supplement.frozen.json").exists():
        supplement = read(cold / "metadata-supplement.frozen.json")
        verify({supplement["copy"]: supplement["expected"]})
    if not all(read(cold / "facts-fit.safe.json")["byte_equal"].values()):
        raise ValueError("ENTRY_COLD_FACTS_FIT_REQUIRED")
    proof = isolation(cold, ledger)
    RestructuringRuntime = importlib.import_module(
        "quanthus.expert_strategy.p3_restructuring_successor"
    ).RestructuringRuntime

    stage = ledger["capital_stage"]
    source_root = Path(
        "/srv/quanthus-base22-data/jobs/p3-held-source-continuation-20261024T122013Z-859e94b"
    )
    cold_source_root = cold / "fs" / source_root.relative_to("/")

    def move(path: str) -> Path:
        return cold_source_root / Path(path).relative_to(source_root)

    role = stage["role"]
    successor = Path(ledger["code"]["successor"])
    adapter = load(
        "entry_march_adapter", successor / "src/quanthus/expert_strategy/entry_march_capital.py"
    )
    runtime = adapter.runtime_type(RestructuringRuntime)
    suffix = "" if run_number == 1 else f"-run{run_number}"
    root = cold / (("capital" if attempt == 1 else f"capital-v{attempt}") + suffix)
    root.mkdir()
    results = {}
    started = time.monotonic()
    with runtime(
        Path(ledger["code"]["retained"]),
        move(role["source"]),
        move(role["prepared"]),
        read(move(role["admission"])),
        parent_source=move(role["parent_source"]),
        prior_prepared=move(role["prior_prepared"]),
        predictions=read(Path(ledger["role"]) / "private/predictions87.json"),
        successor_request=read(
            successor / "config/research/learning-mvp/entry-march-confirmation.v1.json"
        ),
    ) as engine:
        for arm in stage["arms"]:
            cold_guard(cold, ledger)
            output = root / arm
            output.mkdir()
            _, artifacts = engine.run_variant(arm, output)
            for name, raw in artifacts.items():
                with (output / name).open("xb") as stream:
                    stream.write(raw)
            results[arm] = {name: pin(output / name) for name in sorted(artifacts)}
            print(json.dumps({"arm": arm, "status": "COLD_REBUILT"}), flush=True)
    receipt = {
        "kind": "entry-march-cold-capital-v1",
        "arms": results,
        "isolation": proof,
        "elapsed_seconds": time.monotonic() - started,
        "peak_RSS_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
    }
    write(cold / f"capital-attempt{attempt}{suffix}.safe.json", receipt)
    return receipt


def refresh(job: Path, attempt: int) -> dict[str, Any]:
    """Retain the proven source copy, append a new code/stage identity only."""
    guard(job)
    cold = job / "private/cold-role"
    prior = read(cold / "copy-ledger.frozen.json")
    stage = read(job / "private" / f"capital-stage-v{attempt}.frozen.json")
    verify(stage["pins"])
    verify(stage["old_code_pins"])
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    write(
        job / "private" / f"cold-code-refresh-v{attempt}.frozen.json",
        {
            "kind": "entry-march-cold-code-refresh-v1",
            "UTC": datetime.now(timezone.utc).isoformat(),
            "prior_ledger_sha256": sha(cold / "copy-ledger.frozen.json"),
            "new_stage_sha256": sha(job / "private" / f"capital-stage-v{attempt}.frozen.json"),
            "code_revision": revision,
            "maximum_new_bytes": 40 * 2**20,
            "existing_job_bytes": size(job),
            "remaining_source_copies": 0,
            "available_bytes": shutil.disk_usage(job).free,
            "device": job.stat().st_dev,
            "available_inodes": os.statvfs(job).f_favail,
            "source_identity_reused": "Exact copy ledger; full loader validation retained",
        },
    )
    output = cold / "code" / f"successor-v{attempt}"
    output.mkdir()
    archive = cold / f"successor-v{attempt}.tar"
    with archive.open("xb") as stream:
        subprocess.run(
            [
                "git",
                "archive",
                revision,
                "src",
                "config",
                "scripts",
                "pyproject.toml",
                "tests/fixtures",
            ],
            cwd=ROOT,
            stdout=stream,
            check=True,
        )
    with tarfile.open(archive) as handle:
        for item in handle.getmembers():
            if (
                item.issym()
                or item.islnk()
                or Path(item.name).is_absolute()
                or ".." in Path(item.name).parts
            ):
                raise ValueError("ENTRY_COLD_CODE_ARCHIVE")
        handle.extractall(output)
    archive.unlink()
    ledger = {
        **prior,
        "code": {**prior["code"], "successor": str(output)},
        "capital_stage": stage,
        "code_pins": {
            **prior["code_pins"],
            **{str(p): pin(p) for p in sorted(output.rglob("*")) if p.is_file()},
        },
    }
    write(cold / f"copy-ledger-v{attempt}.frozen.json", ledger)
    return {
        "ledger_sha256": sha(cold / f"copy-ledger-v{attempt}.frozen.json"),
        "source_copy_reused": True,
        "new_code_revision": revision,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "facts", "capital", "refresh"))
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--run-number", type=int, default=1)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.job)
    elif args.command == "refresh":
        result = refresh(args.job, args.attempt)
    else:
        cold = args.job / "private/cold-role"
        ledger = read(cold / "copy-ledger.frozen.json")
        deadline = datetime.fromisoformat(ledger["stops"]["deadline_UTC"])
        signal.signal(
            signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("ENTRY_COLD_DEADLINE"))
        )
        signal.alarm(max(1, int((deadline - datetime.now(timezone.utc)).total_seconds())))
        result = (
            facts(cold) if args.command == "facts" else capital(cold, args.attempt, args.run_number)
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
