"""Bounded, versioned R1 worker. Designed for one statically assigned Linux node."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CODE_FILES = (
    "ops/bin/hf-dwd-research",
    "src/quanthus/hf_dwd_research.py",
    "src/quanthus/hf_dwd_research_sql.py",
)
REQUEST_SCHEMA = "quanthus.hf-dwd-research-request.v2"
REASONS = ("fetch", "screen", "publish")


class GuardError(ValueError):
    """A declared identity, scope or resource contract was not met."""


def digest(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
    )


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def read(path: Path) -> Any:
    def invalid(text: str) -> None:
        raise GuardError("nonfinite JSON value")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid)


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require(ok: Any, message: str) -> None:
    if not ok:
        raise GuardError(message)


def safe_path(root: Path, relative: str) -> Path:
    p = Path(relative)
    require(not p.is_absolute() and ".." not in p.parts and bool(p.parts), "unsafe member path")
    result = root / p
    require(result.resolve().is_relative_to(root.resolve()), "member escapes root")
    require(not result.is_symlink(), "symlink member refused")
    return result


def validate_request(request: dict[str, Any]) -> None:
    require(request.get("schema_version") == REQUEST_SCHEMA, "request schema")
    for key in ("request_id", "task_id", "attempt_id"):
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", request.get(key, "")), key)
    a, s, b = request["authority"], request["scope"], request["budget"]
    require(re.fullmatch(r"[0-9a-f]{40}", a["revision"]), "source revision must be immutable")
    require(a["dataset"] == "rv192/quanthus-base25-sealed", "source dataset")
    for key in ("manifest_sha256", "content_digest"):
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", a[key]), key)
    dates = {
        key: date.fromisoformat(s[key])
        for key in (
            "calendar_start",
            "read_start",
            "decision_start",
            "decision_end",
            "execution_end",
        )
    }
    require(date(2012, 1, 1) <= dates["calendar_start"] <= dates["read_start"], "calendar range")
    require(
        date(2020, 1, 1)
        <= dates["read_start"]
        <= dates["decision_start"]
        <= dates["decision_end"]
        < dates["execution_end"]
        < date(2022, 1, 1),
        "protected or invalid market scope",
    )
    require(
        s["partition"] == "development" and s["forbidden_after"] == "2022-01-01",
        "development-only partition",
    )
    require(s["knowledge_view"] == "latest_sealed_retrospective", "knowledge purpose")
    for key in (
        "threads",
        "duckdb_memory_bytes",
        "max_rss_bytes",
        "max_scratch_bytes",
        "max_download_bytes",
        "max_upload_bytes",
        "max_job_wall_seconds",
        "max_cpu_seconds",
        "max_compute_attempts",
        "max_publish_attempts",
    ):
        require(type(b[key]) is int and b[key] > 0, f"positive integer budget: {key}")
    require(b["threads"] <= 4 and b["duckdb_memory_bytes"] < b["max_rss_bytes"], "memory/threads")
    require(b["max_compute_attempts"] <= 2 and b["max_publish_attempts"] <= 3, "retry caps")
    for phase in REASONS:
        require(
            type(b["max_stage_wall_seconds"][phase]) is int
            and 0 < b["max_stage_wall_seconds"][phase] <= b["max_job_wall_seconds"],
            "stage wall budget",
        )
    for key in (
        "min_listing_sessions",
        "min_avg_turnover_prior20",
        "max_unadjusted_close_return_vol20",
        "max_abs_unadjusted_close_return",
    ):
        value = request["screen"][key]
        require(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0,
            "screen numeric parameter",
        )
    band = request["screen"]["unadjusted_momentum20_band"]
    require(
        len(band) == 2 and all(math.isfinite(x) for x in band) and band[0] < band[1],
        "momentum band",
    )
    expected = "runs/" + "/".join(request[k] for k in ("request_id", "task_id", "attempt_id"))
    require(request["publish"]["path_prefix"] == expected, "task/attempt result path")
    require(request["publish"]["private"] is True, "private result repository")
    require(request["publish"]["dataset"] == "rv192/quanthus-research-r1", "result dataset")
    require(bool(request["publish"]["cleanup_owner"]), "retention owner")
    date.fromisoformat(request["publish"]["ttl_review_after"])


def load_bundle(request_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    request = read(request_path)
    validate_request(request)
    closure_path = safe_path(ROOT, request["closure"]["path"])
    lock_path = safe_path(ROOT, request["environment"]["lock_path"])
    require(file_digest(closure_path) == request["closure"]["sha256"], "closure file identity")
    require(
        file_digest(lock_path) == request["environment"]["lock_sha256"], "environment lock identity"
    )
    members = read(closure_path)["members"]
    require(
        len(members) == request["closure"]["exact_members"]
        and sum(x["size_bytes"] for x in members) == request["closure"]["exact_bytes"],
        "closure cardinality",
    )
    seen = set()
    for entry in members:
        name = entry["path"]
        safe_path(Path("/nonexistent/declared-root"), name)
        require(name not in seen, "duplicate member")
        seen.add(name)
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", entry["sha256"]), "member hash")
        require(type(entry["size_bytes"]) is int and entry["size_bytes"] > 0, "member bytes")
        if name.startswith("payload/"):
            require(
                name.split("/")[1]
                in {
                    "dwd_security_bar",
                    "dwd_trading_calendar",
                    "dwd_tradability_state",
                    "dwd_listing",
                    "dwd_security",
                },
                "unexpected table",
            )
            years = re.findall(r"(?:observation_start_year|trade_date_year)=(\d{4})/", name)
            if years:
                allowed = (
                    {"2019", "2020", "2021"}
                    if "/dwd_trading_calendar/" in name
                    else {"2020", "2021"}
                )
                require(set(years) <= allowed, "protected member partition")
        else:
            require(name == "contract/base-build-delivery.v1.json", "unexpected contract")
    require(sys.platform == request["environment"]["platform"], "qualified worker platform")
    require(
        ".".join(map(str, sys.version_info[:3])) == request["environment"]["python"],
        "Python version",
    )
    versions = {}
    for line in lock_path.read_text().splitlines():
        name, version = line.split("==", 1)
        actual = importlib.metadata.version(name)
        require(actual == version, "dependency version: " + name)
        versions[name] = actual
    git = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    code = {name: file_digest(ROOT / name) for name in CODE_FILES}
    protected = [
        *CODE_FILES,
        str(request_path.resolve().relative_to(ROOT)),
        request["closure"]["path"],
        request["environment"]["lock_path"],
    ]
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--", *protected], cwd=ROOT, text=True
    )
    require(not dirty.strip(), "execution code/config must be clean tracked Git state")
    identity = {
        "git": git,
        "code": code,
        "request_digest": digest(request),
        "closure_digest": request["closure"]["sha256"],
        "environment_digest": digest(
            {"python": request["environment"]["python"], "packages": versions}
        ),
    }
    identity["identity_digest"] = digest(identity)
    return request, members, identity


def verify_members(root: Path, request: dict[str, Any], members: list[dict[str, Any]]) -> None:
    manifest = root / "DWD/manifest.json"
    require(
        file_digest(manifest) == request["authority"]["manifest_sha256"], "source manifest differs"
    )
    source = read(manifest)
    require(
        source["content_digest"] == request["authority"]["content_digest"], "source content digest"
    )
    require(
        (root / "DWD/READY").read_text().strip() == file_digest(manifest), "source READY binding"
    )
    declared = {x["path"]: x for x in source["content"]["files"]}
    expected = {"DWD/manifest.json", "DWD/READY"}
    for entry in members:
        require(declared.get(entry["path"]) == entry, "member differs from sealed manifest")
        path = safe_path(root, "DWD/" + entry["path"])
        expected.add("DWD/" + entry["path"])
        require(path.is_file() and stat.S_ISREG(path.stat().st_mode), "missing regular member")
        require(
            path.stat().st_size == entry["size_bytes"] and file_digest(path) == entry["sha256"],
            "member content differs: " + entry["path"],
        )
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    require(actual == expected, "undeclared source files or incomplete closure")


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result and urllib.parse.urlsplit(newurl).netloc != "huggingface.co":
            result.remove_header("Authorization")
        return result


def download_file(
    url: str,
    destination: Path,
    expected_hash: str | None,
    expected_bytes: int | None,
    counter: dict[str, int],
    maximum: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    headers = {}
    token = os.environ.get("HF_TOKEN", "")
    if token:
        headers["Authorization"] = "Bearer " + token
    opener = urllib.request.build_opener(SafeRedirect())
    with (
        opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response,
        partial.open("wb") as target,
    ):
        while chunk := response.read(1024 * 1024):
            counter["downloaded"] += len(chunk)
            require(counter["downloaded"] <= maximum, "actual network byte budget exceeded")
            target.write(chunk)
    require(
        expected_bytes is None or partial.stat().st_size == expected_bytes, "download size differs"
    )
    require(expected_hash is None or file_digest(partial) == expected_hash, "download hash differs")
    partial.replace(destination)


def seal(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "digest": digest(value)}


def fetch(
    request: dict[str, Any],
    members: list[dict[str, Any]],
    identity: dict[str, Any],
    job: Path,
    cache: Path | None,
) -> None:
    source = job / "source"
    source.mkdir(exist_ok=True)
    if (job / "fetch.json").exists():
        bound_read(job, "fetch.json", identity)
        verify_members(source, request, members)
        return
    authority = request["authority"]
    counter = {"downloaded": 0, "copied": 0}
    maximum = request["budget"]["max_download_bytes"]
    require(request["closure"]["exact_bytes"] <= maximum, "declared download budget exceeded")
    entries = [
        {"path": "manifest.json", "sha256": authority["manifest_sha256"], "size_bytes": None},
        {"path": "READY", "sha256": None, "size_bytes": None},
        *members,
    ]
    for entry in entries:
        relative = "DWD/" + entry["path"]
        target = safe_path(source, relative)
        valid = (
            target.is_file()
            and (entry["sha256"] is None or file_digest(target) == entry["sha256"])
            and (entry["size_bytes"] is None or target.stat().st_size == entry["size_bytes"])
        )
        if valid:
            continue
        cached = safe_path(cache, relative) if cache else None
        if (
            cached
            and cached.is_file()
            and (entry["sha256"] is None or file_digest(cached) == entry["sha256"])
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".partial")
            shutil.copyfile(cached, temporary)
            require(
                entry["size_bytes"] is None or temporary.stat().st_size == entry["size_bytes"],
                "cached size differs",
            )
            temporary.replace(target)
            counter["copied"] += target.stat().st_size
        else:
            url = (
                "https://huggingface.co/datasets/"
                + authority["dataset"]
                + "/resolve/"
                + authority["revision"]
                + "/"
                + urllib.parse.quote(relative)
            )
            download_file(url, target, entry["sha256"], entry["size_bytes"], counter, maximum)
    verify_members(source, request, members)
    write(
        job / "fetch.json",
        seal(
            {
                "schema": "quanthus.research-fetch.v2",
                "identity": identity,
                "members": members,
                "member_bytes": request["closure"]["exact_bytes"],
                "network_bytes": counter["downloaded"],
                "cache_copied_bytes": counter["copied"],
                "recorded_at": now(),
            }
        ),
    )


def bound_read(job: Path, name: str, identity: dict[str, Any]) -> dict[str, Any]:
    data = read(job / name)
    schemas = {
        "fetch.json": "quanthus.research-fetch.v2",
        "run.json": "quanthus.research-run.v2",
        "result.json": "quanthus.research-result.v2",
    }
    if name in schemas:
        require(data.get("schema") == schemas[name], "stage receipt kind differs")
        require("digest" in data, "stage receipt digest missing")
    require(data["identity"] == identity, "stage identity differs: " + name)
    if "digest" in data:
        require(
            data["digest"] == digest({k: v for k, v in data.items() if k != "digest"}),
            "stage content differs: " + name,
        )
    return data


def screen(
    request: dict[str, Any], members: list[dict[str, Any]], identity: dict[str, Any], job: Path
) -> None:
    import duckdb

    from quanthus.hf_dwd_research_sql import TABLES, calculate

    fetched = bound_read(job, "fetch.json", identity)
    verified_stage_success(job, "fetch", identity, {"fetch.json": fetched["digest"]})
    verify_members(job / "source", request, members)
    if (job / "result.json").is_file():
        existing = bound_read(job, "result.json", identity)
        previous = bound_read(job, "run.json", identity)
        require(previous["result_digest"] == existing["digest"], "existing run/result conflict")
        return
    paths = {
        table: [
            str(safe_path(job / "source", "DWD/" + x["path"]))
            for x in members
            if x["path"].startswith("payload/" + table + "/")
        ]
        for table in TABLES
    }
    connection = duckdb.connect()
    connection.execute(f"SET threads={request['budget']['threads']}")
    connection.execute(f"SET memory_limit='{request['budget']['duckdb_memory_bytes']}B'")
    connection.execute(f"SET temp_directory='{job / 'duckdb-tmp'}'")
    started = time.monotonic()
    result = {
        "schema": "quanthus.research-result.v2",
        "identity": identity,
        "scope": request["scope"],
        "screen": request["screen"],
        "metrics": calculate(connection, request, paths),
    }
    connection.close()
    result["digest"] = digest(result)
    write(job / "result.json", result)
    write(
        job / "run.json",
        seal(
            {
                "schema": "quanthus.research-run.v2",
                "identity": identity,
                "result_digest": result["digest"],
                "wall_seconds": time.monotonic() - started,
                "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "credential_environment_present": False,
                "recorded_at": now(),
            }
        ),
    )


def artifact_name(request: dict[str, Any], kind: str) -> str:
    return f"{request['task_id']}-{request['attempt_id']}-{kind}.json"


def stage_output_digests(job: Path, phase: str, identity: dict[str, Any]) -> dict[str, str]:
    names = ("fetch.json",) if phase == "fetch" else ("result.json", "run.json")
    receipts = {name: bound_read(job, name, identity) for name in names}
    if phase == "screen":
        require(
            receipts["run.json"]["result_digest"] == receipts["result.json"]["digest"],
            "run/result identity differs",
        )
    return {name: receipt["digest"] for name, receipt in receipts.items()}


def verified_stage_success(
    job: Path, phase: str, identity: dict[str, Any], outputs: dict[str, str]
) -> dict[str, Any]:
    require(
        (job / "resources.json").is_file() and (job / "attempts.json").is_file(),
        "Guardian completion missing",
    )
    resources, attempts = read(job / "resources.json"), read(job / "attempts.json")
    require(isinstance(resources, dict) and isinstance(attempts, list), "Guardian record shape")
    entry = resources.get(phase)
    require(isinstance(entry, dict), "Guardian completion missing: " + phase)
    require(
        entry.get("schema") == "quanthus.research-stage-completion.v1"
        and entry.get("identity") == identity
        and entry.get("phase") == phase,
        "Guardian completion identity/phase",
    )
    require(
        entry.get("digest") == digest({k: v for k, v in entry.items() if k != "digest"}),
        "Guardian completion digest",
    )
    require(
        entry.get("status") == "succeeded"
        and type(entry.get("exit_code")) is int
        and entry.get("exit_code") == 0
        and entry.get("failure") is None,
        "Guardian stage has not succeeded: " + phase,
    )
    history = [x for x in attempts if isinstance(x, dict) and x.get("phase") == phase]
    require(
        bool(history)
        and history[-1] == entry
        and type(entry.get("attempt")) is int
        and entry.get("attempt") == len(history),
        "Guardian completion is not the latest attempt",
    )
    require(entry.get("output_digests") == outputs, "Guardian completion output identity")
    return entry


def verify_package(package: Path) -> dict[str, Any]:
    matches = list(package.glob("*-manifest.json"))
    require(len(matches) == 1, "package manifest cardinality")
    manifest_path = matches[0]
    manifest = read(manifest_path)
    ready_name = f"{manifest['task_id']}-{manifest['attempt_id']}-READY"
    claimed = manifest["digest"]
    require(
        claimed == digest({k: v for k, v in manifest.items() if k != "digest"}),
        "package manifest digest",
    )
    require(
        (package / ready_name).read_text().strip() == file_digest(manifest_path), "package READY"
    )
    expected = {manifest_path.name, ready_name, *manifest["artifacts"]}
    require({x.name for x in package.iterdir()} == expected, "package extra or missing member")
    for name, entry in manifest["artifacts"].items():
        p = safe_path(package, name)
        require(
            p.stat().st_size == entry["size_bytes"] and file_digest(p) == entry["sha256"],
            "package member differs",
        )
    return manifest


def build_package(
    request: dict[str, Any], members: list[dict[str, Any]], identity: dict[str, Any], job: Path
) -> Path:
    fetched = bound_read(job, "fetch.json", identity)
    result = bound_read(job, "result.json", identity)
    run = bound_read(job, "run.json", identity)
    require(run["result_digest"] == result["digest"], "run/result identity differs")
    completions = {
        "fetch": verified_stage_success(job, "fetch", identity, {"fetch.json": fetched["digest"]}),
        "screen": verified_stage_success(
            job, "screen", identity, {"result.json": result["digest"], "run.json": run["digest"]}
        ),
    }
    verify_members(job / "source", request, members)
    package = job / "package"
    staging = job / "package.staging"
    values = {
        "request": request,
        "closure": {"members": members},
        "identity": identity,
        "fetch-receipt": fetched,
        "result": result,
        "run-receipt": run,
        "resource-receipts": completions,
        "stage-attempts": [
            x for x in read(job / "attempts.json") if x.get("phase") in {"fetch", "screen"}
        ],
    }

    def verified_existing(path: Path) -> None:
        require(path.is_dir() and not path.is_symlink(), "invalid package directory")
        require(
            sum(x.stat().st_size for x in path.iterdir()) <= request["budget"]["max_upload_bytes"],
            "package upload budget exceeded",
        )
        manifest = verify_package(path)
        require(manifest["identity"] == identity, "existing package identity")
        for kind, value in values.items():
            # Operational publish attempts are tracked outside the once-frozen package.
            if kind == "stage-attempts":
                continue
            require(
                read(path / artifact_name(request, kind)) == value,
                "existing package content conflict",
            )

    def quarantine_partial(path: Path) -> None:
        require(path.is_dir() and not path.is_symlink(), "invalid partial package directory")
        retained = job / "package-quarantine"
        retained.mkdir(exist_ok=True)
        target = retained / f"{path.name}-{time.time_ns()}"
        path.rename(target)
        log = job / "package-recovery.json"
        previous = read(log) if log.exists() else []
        write(
            log,
            [
                *previous,
                {
                    "from": path.name,
                    "retained": str(target.relative_to(job)),
                    "reason": "incomplete_without_READY",
                    "recorded_at": now(),
                },
            ],
        )

    if package.exists():
        if list(package.glob("*-READY")):
            verified_existing(package)
            return package
        quarantine_partial(package)
    if staging.exists():
        if list(staging.glob("*-READY")):
            verified_existing(staging)
            staging.rename(package)
            return package
        quarantine_partial(staging)
    staging.mkdir()
    for kind, value in values.items():
        write(staging / artifact_name(request, kind), value)
    artifacts = {
        p.name: {"sha256": file_digest(p), "size_bytes": p.stat().st_size}
        for p in staging.iterdir()
    }
    manifest = {
        "schema": "quanthus.research-package.v2",
        "identity": identity,
        "request_id": request["request_id"],
        "task_id": request["task_id"],
        "attempt_id": request["attempt_id"],
        "artifacts": artifacts,
        "retention": request["publish"],
        "created_at": now(),
    }
    manifest["digest"] = digest(manifest)
    manifest_path = staging / artifact_name(request, "manifest")
    write(manifest_path, manifest)
    ready = staging / f"{request['task_id']}-{request['attempt_id']}-READY"
    ready_partial = ready.with_name(ready.name + ".partial")
    ready_partial.write_text(file_digest(manifest_path) + "\n")
    ready_partial.replace(ready)
    verified_existing(staging)
    staging.rename(package)
    return package


def verify_remote(api: Any, request: dict[str, Any], package: Path, revision: str) -> None:
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "immutable result commit")
    prefix = request["publish"]["path_prefix"] + "/"
    remote = {
        x[len(prefix) :]
        for x in api.list_repo_files(
            repo_id=request["publish"]["dataset"], repo_type="dataset", revision=revision
        )
        if x.startswith(prefix)
    }
    require(remote == {p.name for p in package.iterdir()}, "remote package inventory differs")
    for path in package.iterdir():
        received = api.hf_hub_download(
            repo_id=request["publish"]["dataset"],
            repo_type="dataset",
            filename=request["publish"]["path_prefix"] + "/" + path.name,
            revision=revision,
            force_download=True,
            cache_dir=str(package.parent / "hf-readback-cache"),
        )
        require(Path(received).read_bytes() == path.read_bytes(), "remote read-back differs")


def publish(
    request: dict[str, Any], members: list[dict[str, Any]], identity: dict[str, Any], job: Path
) -> None:
    from huggingface_hub import CommitOperationAdd, HfApi

    package = build_package(request, members, identity, job)
    api = HfApi(token=os.environ.get("HF_TOKEN"))
    repo = request["publish"]["dataset"]
    prefix = request["publish"]["path_prefix"]
    info = api.dataset_info(repo)
    require(info.private is True, "result repository must be private")
    head = info.sha
    exists = api.file_exists(
        repo_id=repo,
        repo_type="dataset",
        filename=prefix + "/" + artifact_name(request, "manifest"),
        revision=head,
    )
    remote_names = api.list_repo_files(repo_id=repo, repo_type="dataset", revision=head)
    require(
        exists or not any(x.startswith(prefix + "/") for x in remote_names),
        "incomplete remote attempt conflict",
    )
    if exists:
        verify_remote(api, request, package, head)
        revision = head
        disposition = "already_delivered"
    else:
        revision = api.create_commit(
            repo_id=repo,
            repo_type="dataset",
            parent_commit=head,
            operations=[
                CommitOperationAdd(path_in_repo=prefix + "/" + p.name, path_or_fileobj=str(p))
                for p in sorted(package.iterdir())
            ],
            commit_message=f"{request['task_id']} {request['attempt_id']} immutable v2 result",
        ).oid
        verify_remote(api, request, package, revision)
        disposition = "created"
    write(
        job / "publish.json",
        {
            "schema": "quanthus.research-publish.v2",
            "identity": identity,
            "dataset": repo,
            "revision": revision,
            "path_prefix": prefix,
            "disposition": disposition,
            "package_digest": verify_package(package)["digest"],
            "read_back_all_files": True,
            "recorded_at": now(),
        },
    )


def clean_environment(phase: str) -> dict[str, str]:
    if phase != "screen":
        return dict(os.environ)
    allowed = {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TZ",
        "PYTHONPATH",
        "LD_LIBRARY_PATH",
        "SYSTEMROOT",
        "TMPDIR",
    }
    return {k: v for k, v in os.environ.items() if k in allowed}


def directory_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def guard_job(job: Path, identity: dict[str, Any]) -> dict[str, Any]:
    if (job / "job.json").exists():
        old = read(job / "job.json")
        require(old["identity"] == identity, "existing Job identity differs")
        return old
    value = {"identity": identity, "created_epoch": time.time()}
    write(job / "job.json", value)
    return value


def launch_guarded(phase: str, request_path: Path, job: Path, cache: Path | None) -> int:
    request, members, identity = load_bundle(request_path)
    job.mkdir(parents=True, exist_ok=True)
    with (job / ".writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GuardError("another Job writer is active") from exc
        state = guard_job(job, identity)
        b = request["budget"]
        require(
            time.time() - state["created_epoch"] < b["max_job_wall_seconds"],
            "Job wall budget expired",
        )
        attempts = read(job / "attempts.json") if (job / "attempts.json").exists() else []
        previous = [x for x in attempts if x["phase"] == phase]
        cap = b["max_publish_attempts"] if phase == "publish" else b["max_compute_attempts"]
        require(len(previous) < cap, "stage retry budget exhausted")
        if previous and phase == "screen":
            require(
                (job / "retry-authorization.json").is_file(),
                "located cause and retry authorization required",
            )
            auth = read(job / "retry-authorization.json")
            require(
                auth.get("identity_digest") == identity["identity_digest"]
                and bool(auth.get("cause")),
                "retry authorization identity/cause",
            )
        index = len(previous) + 1
        started = time.monotonic()
        limit = b["max_stage_wall_seconds"][phase]
        command = [
            sys.executable,
            str(ROOT / "ops/bin/hf-dwd-research"),
            "_worker",
            "--phase",
            phase,
            "--request",
            str(request_path.resolve()),
            "--work",
            str(job.resolve()),
        ]
        if cache:
            command.extend(["--source-cache", str(cache.resolve())])
        logfile = job / f"{phase}-{index}.log"
        failure = None
        peak = 0
        scratch = 0
        running = seal(
            {
                "schema": "quanthus.research-stage-completion.v1",
                "identity": identity,
                "phase": phase,
                "attempt": index,
                "status": "running",
                "exit_code": None,
                "failure": None,
                "output_digests": {},
                "log": logfile.name,
                "recorded_at": now(),
            }
        )
        attempts.append(running)
        write(job / "attempts.json", attempts)
        resources = read(job / "resources.json") if (job / "resources.json").is_file() else {}
        if phase != "publish":
            resources[phase] = running
            write(job / "resources.json", resources)
        with logfile.open("wb") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=log,
                env=clean_environment(phase),
                start_new_session=True,
            )
            while process.poll() is None:
                try:
                    fields = Path(f"/proc/{process.pid}/status").read_text()
                    measured = re.search(r"VmHWM:\s+(\d+) kB", fields)
                    if measured:
                        peak = max(peak, int(measured.group(1)) * 1024)
                except FileNotFoundError:
                    pass
                scratch = max(scratch, directory_bytes(job))
                if time.monotonic() - started > limit:
                    failure = "stage_wall_exceeded"
                elif time.time() - state["created_epoch"] > b["max_job_wall_seconds"]:
                    failure = "job_wall_exceeded"
                elif peak > b["max_rss_bytes"]:
                    failure = "rss_exceeded"
                elif scratch > b["max_scratch_bytes"]:
                    failure = "scratch_exceeded"
                if failure:
                    os.killpg(process.pid, signal.SIGKILL)
                    break
                time.sleep(0.2)
            code = process.wait()
        elapsed = time.monotonic() - started
        outputs = {}
        if failure is None and code == 0 and phase != "publish":
            try:
                outputs = stage_output_digests(job, phase, identity)
            except (GuardError, OSError, KeyError, TypeError, ValueError):
                failure = "stage_receipt_invalid"
        if failure is None and elapsed > limit:
            failure = "stage_wall_exceeded"
        if failure is None and time.time() - state["created_epoch"] > b["max_job_wall_seconds"]:
            failure = "job_wall_exceeded"
        attempt = seal(
            {
                "schema": "quanthus.research-stage-completion.v1",
                "identity": identity,
                "phase": phase,
                "attempt": index,
                "status": "succeeded" if code == 0 and failure is None else "failed",
                "exit_code": code,
                "failure": failure,
                "output_digests": outputs,
                "wall_seconds": elapsed,
                "peak_rss_bytes": peak,
                "peak_scratch_bytes": scratch,
                "log": logfile.name,
                "recorded_at": now(),
            }
        )
        attempts[-1] = attempt
        write(job / "attempts.json", attempts)
        # Immutable package keeps resources before publish; upload records remain separate.
        if phase != "publish":
            resources[phase] = attempt
            write(job / "resources.json", resources)
        print(json.dumps(attempt))
        return 0 if code == 0 and failure is None else 1


def worker(phase: str, request_path: Path, job: Path, cache: Path | None) -> int:
    request, members, identity = load_bundle(request_path)
    resource.setrlimit(resource.RLIMIT_CPU, (request["budget"]["max_cpu_seconds"],) * 2)
    if phase == "screen":
        require(
            not any(re.search(r"TOKEN|PASSWORD|SECRET|API_KEY", k, re.I) for k in os.environ),
            "calculation credential isolation",
        )
        screen(request, members, identity, job)
    elif phase == "fetch":
        fetch(request, members, identity, job, cache)
    else:
        publish(request, members, identity, job)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=[*REASONS, "_worker"])
    p.add_argument("--request", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--source-cache", type=Path)
    p.add_argument("--phase", choices=REASONS)
    a = p.parse_args(argv)
    try:
        if a.command == "_worker":
            require(a.phase in REASONS, "worker phase")
            return worker(a.phase, a.request, a.work, a.source_cache)
        return launch_guarded(a.command, a.request, a.work, a.source_cache)
    except GuardError as exc:
        print("GUARD_FAILED: " + str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        # Do not echo authenticated URLs, HTTP bodies or credential-bearing environment.
        print("STAGE_FAILED: " + type(exc).__name__, file=sys.stderr)
        return 3
