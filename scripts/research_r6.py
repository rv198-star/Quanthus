#!/usr/bin/env python3
"""One frozen R6 diagnostic, using existing HF inputs and a separately guarded reference."""

import argparse
import fcntl
import gzip
import hashlib
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from quanthus.hf_dwd_research import (
    clean_environment,
    digest,
    directory_bytes,
    download_file,
    fetch,
    file_digest,
    load_bundle,
    now,
    read,
    require,
    verify_members,
    verify_remote,
    write,
)
from quanthus.rank1_response import compare, formal

ROOT = Path(__file__).resolve().parents[1]
REQUEST = "config/research/research-r6/request.v1.json"
FILES = (
    REQUEST,
    "src/quanthus/rank1_response.py",
    "scripts/research_r6.py",
    "scripts/research_r6_independent.py",
    "scripts/research_r5_independent_audit.py",
    "scripts/research_r2_independent_audit.py",
    "scripts/research_r1_v2_independent_audit.py",
)


def configuration(execution_git):
    q = read(ROOT / REQUEST)
    require(q["schema_version"] == "quanthus.r6-rank1-request.v1", "R6 request schema")
    require(
        file_digest(ROOT / q["input_request"]["path"]) == q["input_request"]["sha256"],
        "input request identity",
    )
    inputs, members, source_identity = load_bundle(ROOT / q["input_request"]["path"])
    require(
        source_identity["git"] == execution_git and re.fullmatch(r"[a-f0-9]{40}", execution_git),
        "execution Git identity",
    )
    require(
        not subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
        ).strip(),
        "tracked execution tree must be clean",
    )
    tracked = subprocess.check_output(["git", "ls-files", *FILES], cwd=ROOT, text=True).splitlines()
    require(set(tracked) == set(FILES), "all execution files must be Git-tracked")
    identity = {
        "schema": "quanthus.r6-identity.v1",
        "git": execution_git,
        "request_sha256": file_digest(ROOT / REQUEST),
        "request_digest": digest(q),
        "input_identity": source_identity,
        "files": {p: file_digest(ROOT / p) for p in FILES},
    }
    return q, inputs, members, identity


def old_results(job, q):
    results = []
    for key in ("old_r4", "old_r5"):
        spec, path = q[key], job / (key + ".payload")
        require(
            path.stat().st_size == spec["size_bytes"] and file_digest(path) == spec["sha256"],
            "old immutable file identity",
        )
        raw = gzip.decompress(path.read_bytes()) if key == "old_r4" else path.read_bytes()
        if key == "old_r4":
            require(
                len(raw) == spec["raw_size_bytes"]
                and "sha256:" + hashlib.sha256(raw).hexdigest() == spec["raw_sha256"],
                "old raw identity",
            )
        value = json.loads(raw)
        require(
            digest({k: v for k, v in value.items() if k != "digest"}) == value["digest"],
            "old result semantic digest",
        )
        if key == "old_r4":
            require(
                len(value["nominal_choices"]) == spec["nominal_rows"]
                and digest(value["nominal_choices"]) == spec["nominal_sha256"],
                "old nominal identity",
            )
        results.append(value)
    return results


def prepare(args, q, inputs, members, identity):
    fetch(inputs, members, identity, args.job, args.source_cache)
    counter = {"downloaded": read(args.job / "fetch.json")["network_bytes"]}
    for key, cache in (("old_r4", args.old_r4), ("old_r5", args.old_r5)):
        spec, path = q[key], args.job / (key + ".payload")
        if cache:
            require(file_digest(cache) == spec["sha256"], "old cache identity")
            shutil.copyfile(cache, path)
        else:
            url = (
                "https://huggingface.co/datasets/"
                + spec["dataset"]
                + "/resolve/"
                + spec["revision"]
                + "/"
                + urllib.parse.quote(spec["member"])
            )
            download_file(
                url,
                path,
                spec["sha256"],
                spec["size_bytes"],
                counter,
                q["budget"]["max_download_bytes"],
            )
    old_results(args.job, q)
    write(
        args.job / "prepare.json",
        {
            "identity": identity,
            "input_member_bytes": inputs["closure"]["exact_bytes"],
            "downloaded_bytes": counter["downloaded"],
            "recorded_at": now(),
        },
    )


def compute(args, q, inputs, members, identity):
    require(
        not any(os.environ.get(k) for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")),
        "compute credentials present",
    )
    require(read(args.job / "prepare.json")["identity"] == identity, "prepared identity differs")
    verify_members(args.job / "source", inputs, members)
    old_r4, old_r5 = old_results(args.job, q)
    if args.phase == "formal":
        import duckdb

        from quanthus.hf_dwd_research_sql import TABLES, calculate

        db = duckdb.connect()
        db.execute("SET threads=2")
        db.execute("SET memory_limit='3GB'")
        db.execute(f"SET temp_directory='{args.job / 'duckdb-tmp'}'")
        paths = {
            t: [
                str(args.job / "source/DWD" / m["path"])
                for m in members
                if m["path"].startswith("payload/" + t + "/")
            ]
            for t in TABLES
        }
        population = calculate(db, inputs, paths)
        checked = compare(old_r5["metrics"], population)
        metrics = formal(db, old_r4, population, q["analysis"])
        db.close()
        result = {"identity": identity, "metrics": metrics, "r5_control_nodes_checked": checked}
        result["digest"] = digest(result)
        write(args.job / "result.json", result)
    else:
        from research_r6_independent import run

        require(
            run(args.job, q, inputs, members, old_r4, old_r5) == 0,
            "independent population control mismatch",
        )
        rebuilt = read(args.job / "independent/metrics.json")
        original = read(args.job / "result.json")
        require(
            original["identity"] == identity
            and digest({k: v for k, v in original.items() if k != "digest"}) == original["digest"],
            "formal result identity",
        )
        nodes = compare(original["metrics"], rebuilt)
        write(
            args.job / "independent/receipt.json",
            {
                "identity": identity,
                "formal_digest": original["digest"],
                "rebuilt_digest": digest(rebuilt),
                "comparison_nodes": nodes,
                "passed": True,
            },
        )
    verify_members(args.job / "source", inputs, members)


def package(args, q, identity):
    """Freeze accepted bytes once; retrying transport cannot rebuild or alter this package."""
    receipt = read(args.job / "independent/receipt.json")
    require(receipt["passed"] is True and receipt["identity"] == identity, "independent receipt")
    original = read(args.job / "result.json")
    rebuilt = read(args.job / "independent/metrics.json")
    require(original["identity"] == identity, "formal identity")
    require(
        digest({k: v for k, v in original.items() if k != "digest"}) == original["digest"],
        "formal payload digest",
    )
    require(
        receipt["formal_digest"] == original["digest"]
        and receipt["rebuilt_digest"] == digest(rebuilt),
        "independent result binding",
    )
    compare(original["metrics"], rebuilt)
    review = read(args.job / "review.json")
    require(
        review["accepted_identity"] == identity
        and review["formal_digest"] == original["digest"]
        and review["rebuilt_digest"] == digest(rebuilt)
        and review["status"] == "passed",
        "independent role acceptance identity",
    )
    target = args.job / "package"
    names = {
        "request": ROOT / REQUEST,
        "identity": args.job / "identity.json",
        "formal": args.job / "result.json",
        "reference": args.job / "independent/metrics.json",
        "independent": args.job / "independent/receipt.json",
        "review": args.job / "review.json",
    }
    prefix = q["task_id"] + "-" + q["attempt_id"]
    target.mkdir(exist_ok=True)
    expected = {}
    for key, source in names.items():
        name = prefix + "-" + key + ".json"
        if (target / name).exists():
            require(file_digest(target / name) == file_digest(source), "package overwrite conflict")
        else:
            shutil.copyfile(source, target / name)
        expected[name] = {"sha256": file_digest(source), "size_bytes": source.stat().st_size}
    manifest = {"schema": "quanthus.r6-result-package.v1", "identity": identity, "files": expected}
    manifest["digest"] = digest(manifest)
    mp = target / (prefix + "-manifest.json")
    if mp.exists():
        require(read(mp) == manifest, "immutable package manifest")
    else:
        write(mp, manifest)
    ready = target / (prefix + "-READY")
    if not ready.exists():
        partial = ready.with_suffix(".partial")
        partial.write_text(file_digest(mp) + "\n")
        partial.replace(ready)
    require(ready.read_text().strip() == file_digest(mp), "package READY")
    require(
        {p.name for p in target.iterdir()} == set(expected) | {mp.name, prefix + "-READY"},
        "package inventory",
    )
    require(directory_bytes(target) <= q["budget"]["max_upload_bytes"], "upload byte budget")
    return target


def publish(args, q, identity):
    from huggingface_hub import CommitOperationAdd, HfApi

    target = package(args, q, identity)
    api = HfApi(token=os.environ.get("HF_TOKEN"))
    repo, prefix = q["publish"]["dataset"], q["publish"]["path_prefix"]
    info = api.dataset_info(repo)
    require(
        info.private is True and re.fullmatch(r"[a-f0-9]{40}", info.sha),
        "private immutable repository",
    )
    existing = [
        p
        for p in api.list_repo_files(repo, repo_type="dataset", revision=info.sha)
        if p.startswith(prefix + "/")
    ]
    if existing:
        revision = info.sha
    else:
        revision = api.create_commit(
            repo_id=repo,
            repo_type="dataset",
            parent_commit=info.sha,
            operations=[
                CommitOperationAdd(path_in_repo=prefix + "/" + p.name, path_or_fileobj=str(p))
                for p in sorted(target.iterdir())
            ],
            commit_message=q["task_id"] + " " + q["attempt_id"] + " immutable result",
        ).oid
    verify_remote(api, q, target, revision)
    write(
        args.job / "publish.json",
        {
            "identity": identity,
            "dataset": repo,
            "revision": revision,
            "path_prefix": prefix,
            "all_files_read_back": True,
            "bytes": directory_bytes(target),
            "recorded_at": now(),
        },
    )


def terminal_failure(elapsed, peak, scratch, cpu, limit, budget):
    for bad, reason in (
        (elapsed > limit, "wall_exceeded"),
        (peak > budget["max_rss_bytes"], "rss_exceeded"),
        (scratch > budget["max_scratch_bytes"], "scratch_exceeded"),
        (cpu > budget["max_cpu_seconds"], "cpu_exceeded"),
    ):
        if bad:
            return reason
    return None


def guarded(args, q, identity):
    job, phase, budget = args.job, args.phase, q["budget"]
    job.mkdir(parents=True, exist_ok=True)
    with (job / ".writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (job / "identity.json").exists():
            require(read(job / "identity.json") == identity, "job identity conflict")
        else:
            write(job / "identity.json", identity)
        attempts = read(job / "attempts.json") if (job / "attempts.json").exists() else []
        count = sum(x["phase"] == phase for x in attempts)
        require(count < budget[phase + "_attempts"], "attempt budget exhausted")
        if phase != "prepare":
            require(
                any(x["phase"] == "prepare" and x["status"] == "succeeded" for x in attempts),
                "prepare not accepted",
            )
        if phase in ("reference", "publish"):
            require(
                any(x["phase"] == "formal" and x["status"] == "succeeded" for x in attempts),
                "formal not accepted",
            )
        if phase == "publish":
            require(
                any(x["phase"] == "reference" and x["status"] == "succeeded" for x in attempts),
                "reference not accepted",
            )
        attempt = {"phase": phase, "attempt": count + 1, "status": "running", "started_at": now()}
        attempts.append(attempt)
        write(job / "attempts.json", attempts)
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            phase,
            "--job",
            str(job),
            "--execution-git",
            args.execution_git,
            "--child",
        ]
        for name in ("source_cache", "old_r4", "old_r5"):
            if getattr(args, name):
                command.extend(["--" + name.replace("_", "-"), str(getattr(args, name))])
        limit = budget[
            "prepare_seconds"
            if phase == "prepare"
            else "publish_seconds"
            if phase == "publish"
            else "compute_seconds"
        ]
        started, peak, scratch, failure = time.monotonic(), 0, 0, None
        with (job / f"{phase}-{count + 1}.log").open("wb") as log:
            process = subprocess.Popen(
                command,
                env=clean_environment(
                    "fetch" if phase == "prepare" else "publish" if phase == "publish" else "screen"
                ),
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            while process.poll() is None:
                try:
                    found = re.search(
                        r"VmHWM:\s+(\d+) kB", Path(f"/proc/{process.pid}/status").read_text()
                    )
                    if found:
                        peak = max(peak, int(found[1]) * 1024)
                except FileNotFoundError:
                    pass
                scratch = max(scratch, directory_bytes(job))
                failure = terminal_failure(
                    time.monotonic() - started, peak, scratch, 0, limit, budget
                )
                if failure:
                    os.killpg(process.pid, signal.SIGKILL)
                    break
                time.sleep(0.2)
            code = process.wait()
        elapsed = time.monotonic() - started
        terminal = job / f"{phase}-terminal.json"
        measured = read(terminal) if terminal.exists() else {}
        peak = max(peak, measured.get("peak_rss_bytes", 0))
        scratch = max(scratch, directory_bytes(job))
        failure = failure or terminal_failure(
            elapsed, peak, scratch, measured.get("cpu_seconds", 0), limit, budget
        )
        if code == 0 and not measured:
            failure = failure or "missing_terminal_receipt"
        attempt.update(
            status="succeeded" if code == 0 and not failure else "failed",
            exit_code=code,
            failure=failure,
            wall_seconds=elapsed,
            peak_rss_bytes=peak,
            peak_scratch_bytes=scratch,
            cpu_seconds=measured.get("cpu_seconds"),
            completed_at=now(),
        )
        write(job / "attempts.json", attempts)
        print(json.dumps(attempt))
        return 0 if attempt["status"] == "succeeded" else 2


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=("prepare", "formal", "reference", "publish"))
    p.add_argument("--job", type=Path, required=True)
    p.add_argument("--execution-git", required=True)
    p.add_argument("--source-cache", type=Path)
    p.add_argument("--old-r4", type=Path)
    p.add_argument("--old-r5", type=Path)
    p.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    args.job = args.job.resolve()
    q, inputs, members, identity = configuration(args.execution_git)
    if not args.child:
        return guarded(args, q, identity)
    resource.setrlimit(resource.RLIMIT_CPU, (q["budget"]["max_cpu_seconds"],) * 2)
    try:
        if args.phase == "prepare":
            prepare(args, q, inputs, members, identity)
        elif args.phase == "publish":
            publish(args, q, identity)
        else:
            compute(args, q, inputs, members, identity)
    finally:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        write(
            args.job / f"{args.phase}-terminal.json",
            {
                "identity": identity,
                "peak_rss_bytes": usage.ru_maxrss * 1024,
                "cpu_seconds": usage.ru_utime + usage.ru_stime,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
