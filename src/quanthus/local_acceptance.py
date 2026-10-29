"""Local make-check evidence and deliberately conservative, same-input reuse.

Receipts are integrity records from an explicitly trusted local executor, not signatures.
The profile does not replace independent builds, domain acceptance or optional integration tests.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
import platform
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROFILE = "config/local-acceptance.v1.json"
SCHEMA = "quanthus.local-acceptance-receipt.v1"
PLUGIN = "quanthus.local_acceptance_pytest"
FILES = ("inputs.json", "checks.log", "pytest.json", "receipt.json")
CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


class AcceptanceError(ValueError):
    """An input, receipt or runtime cannot qualify for the declared check."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_new(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(encoded(value))


def read_json(path: Path) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), f"MISSING_OR_LINKED_FILE: {path.name}")
    require(path.stat().st_size <= 16 * 1024**2, f"OVERSIZED_JSON: {path.name}")
    value = json.loads(path.read_bytes())
    require(isinstance(value, dict), f"INVALID_OBJECT: {path.name}")
    return dict(value)


def git(root: Path, *args: str) -> bytes:
    binary = str(shutil.which("git", path=os.defpath))
    return subprocess.check_output([binary, "-C", str(root), *args], stderr=subprocess.PIPE)


def committed_digest(path: Path, blob: str) -> str:
    """Check content even when Git's assume-unchanged/skip-worktree flags hide edits."""
    require(len(blob) == 40, "UNSUPPORTED_GIT_OBJECT_FORMAT")
    content = hashlib.sha256()
    git_blob = hashlib.sha1(b"blob " + str(path.stat().st_size).encode() + b"\0")
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            content.update(block)
            git_blob.update(block)
    require(git_blob.hexdigest() == blob, "DIRTY_TRACKED_CONTENT")
    return content.hexdigest()


def physical(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    require(absolute == absolute.resolve(), "SYMLINK_PATH")
    return absolute


def overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def walked_files(root: Path) -> list[Path]:
    def failed(error: OSError) -> None:
        raise error

    files = []
    for directory, dirs, names in os.walk(root, onerror=failed):
        dirs[:] = sorted(d for d in dirs if d not in CACHE_DIRS)
        for name in dirs:
            require(not (Path(directory) / name).is_symlink(), "LINKED_DIRECTORY")
        for name in sorted(names):
            if not name.endswith((".pyc", ".pyo")):
                files.append(Path(directory) / name)
    return files


def tree_identity(root: Path) -> dict[str, Any]:
    state = hashlib.sha256()
    files = walked_files(root)
    size = 0
    for path in files:
        require(path.is_file(), "RUNTIME_SPECIAL_FILE")
        info = path.stat()
        state.update(
            encoded(
                [
                    str(path.relative_to(root)),
                    stat.S_IMODE(info.st_mode),
                    str(path.readlink()) if path.is_symlink() else None,
                    digest(path),
                ]
            )
        )
        size += info.st_size
    return {"sha256": state.hexdigest(), "files": len(files), "logical_bytes": size}


def runtime_identity() -> dict[str, Any]:
    """Hash installed code/metadata, not just version labels; never export direct URLs."""
    stdlib = Path(sysconfig.get_path("stdlib"))
    site = Path(sysconfig.get_path("purelib"))
    require(stdlib.is_dir() and site.is_dir(), "UNSUPPORTED_RUNTIME_LAYOUT")
    tools = {}
    for name in ("make", "git", "sh"):
        found = shutil.which(name, path=os.defpath)
        require(found, f"MISSING_TOOL: {name}")
        target = Path(str(found)).resolve()
        tools[name] = {"path": str(target), "sha256": digest(target)}
    return {
        "python": sys.version,
        "executable": str(Path(sys.executable).absolute()),
        "executable_sha256": digest(Path(sys.executable)),
        "platform": platform.platform(),
        "os_release_sha256": digest(Path("/etc/os-release")),
        "stdlib": {"path": str(stdlib), **tree_identity(stdlib)},
        "site_packages": {"path": str(site), **tree_identity(site)},
        "distributions": sorted(
            [d.metadata["Name"], d.version] for d in importlib.metadata.distributions()
        ),
        "tools": tools,
    }


def repository_identity(root: Path) -> dict[str, Any]:
    require(
        git(root, "rev-parse", "--show-toplevel").decode().strip() == str(root),
        "NOT_REPOSITORY_ROOT",
    )
    require(not git(root, "status", "--porcelain", "--untracked-files=all"), "DIRTY_WORKTREE")
    members = git(root, "ls-files", "--stage", "-z").split(b"\0")
    files = {}
    for member in filter(None, members):
        metadata, raw = member.split(b"\t", 1)
        mode, blob, stage = metadata.decode().split()
        require(mode in {"100644", "100755"} and stage == "0", "UNSUPPORTED_GIT_MEMBER")
        name = raw.decode()
        path = root / name
        require(path.resolve() == path and path.is_file(), "LINKED_OR_MISSING_GIT_MEMBER")
        executable = bool(path.stat().st_mode & 0o111)
        require(executable == (mode == "100755"), "DIRTY_TRACKED_MODE")
        files[name] = {"sha256": committed_digest(path, blob), "executable": executable}
    for area in ("src", "tests", "config", "scripts"):
        for path in walked_files(root / area):
            require(str(path.relative_to(root)) in files, "UNTRACKED_SEMANTIC_INPUT")
    return {
        "root": str(root),
        "commit": git(root, "rev-parse", "HEAD").decode().strip(),
        "tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
        "files": files,
    }


def profile(root: Path) -> dict[str, Any]:
    value = read_json(root / PROFILE)
    require(value.get("schema_version") == "quanthus.local-acceptance-profile.v1", "PROFILE_SCHEMA")
    require(value.get("target") == "check" and value.get("plugin") == PLUGIN, "PROFILE_COMMAND")
    limits = value.get("limits", {})
    require(
        set(limits)
        == {
            "wall_seconds",
            "rss_bytes",
            "job_and_worktree_bytes",
            "pytest_tmp_bytes",
            "minimum_free_bytes",
        },
        "PROFILE_LIMITS",
    )
    require(all(type(n) is int and n > 0 for n in limits.values()), "PROFILE_LIMIT_VALUES")
    return value


def inputs(root: Path) -> dict[str, Any]:
    return {
        "profile": profile(root),
        "repository": repository_identity(root),
        "runtime": runtime_identity(),
        "environment_profile": "isolated-local-make-check-v1",
        "command_template": ["make", "check", "PYTHON=<current interpreter>"],
        "automatic_pytest_plugins": False,
        "external_integration_environment": "not_inherited",
    }


def environment(root: Path, job: Path) -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "HOME": str(job / "home"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "TMPDIR": str(job / "tmp"),
        "PYTHONPATH": str(root / "src"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONPYCACHEPREFIX": str(job / "tmp/pycache"),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_ADDOPTS": shlex.join(["-p", PLUGIN, "--basetemp=" + str(job / "tmp/pytest")]),
        "QUANTHUS_ACCEPTANCE_JOB": str(job),
    }


def allocated(root: Path) -> int:
    def failed(error: OSError) -> None:
        if not isinstance(error, FileNotFoundError):
            raise error

    if not root.exists():
        return 0  # Optional test TMP is created by pytest after make starts.
    total = 0
    for directory, _, names in os.walk(root, onerror=failed):
        with contextlib.suppress(FileNotFoundError):
            total += Path(directory).lstat().st_blocks * 512
        for name in names:
            with contextlib.suppress(FileNotFoundError):
                total += (Path(directory) / name).lstat().st_blocks * 512
    return total


def group_rss(group: int) -> int:
    total = 0
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            try:
                fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == group:
                    total += int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
    return total


def stop(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def execute(root: Path, job: Path, limits: dict[str, int]) -> dict[str, Any]:
    command = [
        str(shutil.which("make", path=os.defpath)),
        "check",
        "PYTHON=" + shlex.quote(sys.executable),
    ]
    started = time.monotonic()
    measurement = {"peak_RSS_bytes": 0, "peak_job_worktree_bytes": 0, "minimum_free_bytes": 2**64}
    reason = None
    with (job / "checks.log").open("xb") as output:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=environment(root, job),
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                rss = group_rss(process.pid)
                size = allocated(job) + allocated(root)
                space = os.statvfs(job)
                free = space.f_bavail * space.f_frsize
                measurement["peak_RSS_bytes"] = max(measurement["peak_RSS_bytes"], rss)
                measurement["peak_job_worktree_bytes"] = max(
                    measurement["peak_job_worktree_bytes"], size
                )
                measurement["minimum_free_bytes"] = min(measurement["minimum_free_bytes"], free)
                if time.monotonic() - started > limits["wall_seconds"]:
                    reason = "TIME_LIMIT"
                elif rss > limits["rss_bytes"]:
                    reason = "RSS_LIMIT"
                elif size > limits["job_and_worktree_bytes"]:
                    reason = "JOB_WRITE_LIMIT"
                elif allocated(job / "tmp/pytest") > limits["pytest_tmp_bytes"]:
                    reason = "TEST_TMP_LIMIT"
                elif free < limits["minimum_free_bytes"]:
                    reason = "FREE_SPACE_FLOOR"
                if reason:
                    stop(process)
                    break
                time.sleep(0.5)
        except BaseException:
            stop(process)
            raise
        exit_code = process.wait()
    return {
        "command": command,
        "exit_code": exit_code,
        "stop_reason": reason,
        "elapsed_seconds": time.monotonic() - started,
        **measurement,
    }


def run(root: Path, job: Path) -> dict[str, Any]:
    require(job.is_absolute(), "OUTPUT_MUST_BE_ABSOLUTE")
    root, job = physical(root), physical(job)
    require(not overlaps(root, job), "OUTPUT_OVERLAPS_REPOSITORY")
    require(not job.exists() and job.parent.is_dir(), "OUTPUT_MUST_BE_NEW")
    before = inputs(root)
    for scope in (before["runtime"]["stdlib"]["path"], before["runtime"]["site_packages"]["path"]):
        require(not overlaps(Path(scope), job), "OUTPUT_OVERLAPS_RUNTIME")
    limits = before["profile"]["limits"]
    space = os.statvfs(job.parent)
    existing = allocated(root)
    require(existing < limits["job_and_worktree_bytes"], "WORKTREE_ALREADY_EXCEEDS_BUDGET")
    require(
        space.f_bavail * space.f_frsize
        >= limits["minimum_free_bytes"] + limits["job_and_worktree_bytes"] - existing,
        "INSUFFICIENT_PREFLIGHT_SPACE",
    )
    job.mkdir(mode=0o700)
    (job / "tmp").mkdir(mode=0o700)
    (job / "home").mkdir(mode=0o700)
    write_new(job / "inputs.json", before)
    write_new(
        job / "attempt.json",
        {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "kind": "local-acceptance-attempt-v1",
        },
    )

    def terminated(signum: int, frame: Any) -> None:
        raise InterruptedError("LOCAL_ACCEPTANCE_TERMINATED")

    prior_handler = signal.signal(signal.SIGTERM, terminated)
    result = None
    try:
        result = execute(root, job, limits)
        after = inputs(root)
        same = before == after
        tests = read_json(job / "pytest.json") if (job / "pytest.json").exists() else {}
        good = (
            result["exit_code"] == 0
            and result["stop_reason"] is None
            and same
            and tests.get("exit_status") == 0
            and tests.get("passed", 0) > 0
            and tests.get("failed") == 0
            and tests.get("errors") == 0
        )
        status = ("PASS_WITH_SKIPS" if tests.get("skipped", 0) else "PASS") if good else "FAILED"
        receipt = {
            "schema_version": SCHEMA,
            "status": status,
            "execution": "NEW_RUN",
            "input_sha256": digest(job / "inputs.json"),
            "inputs_unchanged": same,
            "tests": tests,
            "result": result,
            "claim_ceiling": "Local engineering only; skips and admission remain separate.",
        }
    except (Exception, KeyboardInterrupt) as error:
        receipt = {
            "schema_version": SCHEMA,
            "status": "INCOMPLETE",
            "execution": "NEW_RUN",
            "error": type(error).__name__,
            "reason": str(error),
            "result": result,
            "input_sha256": digest(job / "inputs.json"),
        }
    finally:
        signal.signal(signal.SIGTERM, prior_handler)
    write_new(job / "receipt.json", receipt)
    members = [name for name in FILES if (job / name).is_file()]
    with (job / "SHA256SUMS").open("x") as stream:
        stream.write("".join(f"{digest(job / name)}  {name}\n" for name in members))
    return receipt


def verify(root: Path, job: Path) -> dict[str, Any]:
    root, job = physical(root), physical(job)
    require(job.is_dir() and job.stat().st_uid == os.geteuid(), "UNTRUSTED_LOCAL_RECEIPT_OWNER")
    require(not job.stat().st_mode & 0o022, "WRITABLE_RECEIPT_DIRECTORY")
    names = {}
    manifest = job / "SHA256SUMS"
    require(manifest.is_file() and not manifest.is_symlink(), "MISSING_CHECKSUMS")
    require(manifest.stat().st_size < 4096, "OVERSIZED_CHECKSUMS")
    for line in manifest.read_text().splitlines():
        expected, name = line.split("  ", 1)
        require(name in FILES and name not in names, "INVALID_CHECKSUM_MEMBER")
        path = job / name
        require(path.is_file() and not path.is_symlink(), "MISSING_RECEIPT_MEMBER")
        require(
            path.stat().st_uid == os.geteuid() and not path.stat().st_mode & 0o022,
            "UNTRUSTED_RECEIPT_MEMBER",
        )
        require(digest(path) == expected, "CHECKSUM_MISMATCH")
        names[name] = expected
    require(set(names) == set(FILES), "INCOMPLETE_RECEIPT_FILES")
    receipt = read_json(job / "receipt.json")
    require(receipt.get("schema_version") == SCHEMA, "RECEIPT_SCHEMA")
    require(
        receipt.get("execution") == "NEW_RUN"
        and receipt.get("status") in {"PASS", "PASS_WITH_SKIPS"},
        "RUN_NOT_PASSED",
    )
    require(
        receipt.get("inputs_unchanged") is True
        and receipt.get("input_sha256") == names["inputs.json"],
        "INVALID_INPUT_PROOF",
    )
    tests = read_json(job / "pytest.json")
    require(
        tests.get("schema_version") == "quanthus.local-pytest-result.v1"
        and all(
            type(tests.get(k)) is int and tests[k] >= 0
            for k in ("passed", "failed", "errors", "skipped", "collected")
        ),
        "INVALID_TEST_COUNTS",
    )
    require(
        receipt.get("tests") == tests
        and tests.get("exit_status") == 0
        and tests.get("passed", 0) > 0
        and tests.get("failed") == 0
        and tests.get("errors") == 0,
        "INVALID_TEST_PROOF",
    )
    require(
        receipt["status"] == ("PASS_WITH_SKIPS" if tests["skipped"] else "PASS"),
        "INVALID_SKIP_STATUS",
    )
    result = receipt.get("result", {})
    require(isinstance(result, dict), "INVALID_COMMAND_RESULT")
    require(result.get("exit_code") == 0 and result.get("stop_reason") is None, "RUN_INTERRUPTED")
    require(read_json(job / "inputs.json") == inputs(root), "INPUT_OR_RUNTIME_CHANGED")
    return {
        "status": receipt["status"],
        "execution": "REUSED_NOT_RERUN",
        "receipt_directory": str(job),
        "receipt_sha256": names["receipt.json"],
        "tests": tests,
        "original_elapsed_seconds": result["elapsed_seconds"],
        "claim_ceiling": receipt["claim_ceiling"],
    }


def summary(receipt: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in receipt.items() if key != "tests"} | {
        "tests": {
            key: value
            for key, value in receipt.get("tests", {}).items()
            if key not in {"skip_reasons", "failure_nodes", "retention_errors"}
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "verify"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = run(args.root, args.job) if args.command == "run" else verify(args.root, args.job)
        counts = receipt.get("tests", {})
        verb = "复用旧结果，未重跑" if args.command == "verify" else "本次执行"
        print(
            f"本地验收：{receipt['status']}；{verb}；"
            f"通过 {counts.get('passed', 0)}，跳过 {counts.get('skipped', 0)}。"
        )
        print(json.dumps(summary(receipt), ensure_ascii=False, indent=2))
        return 0 if receipt["status"] in {"PASS", "PASS_WITH_SKIPS"} else 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "NOT_QUALIFIED", "reason": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
