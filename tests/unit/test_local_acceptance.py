"""A real disposable make/pytest job and adversarial receipt/input boundaries."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from quanthus import local_acceptance as local
from quanthus import local_acceptance_pytest as telemetry

ROOT = Path(__file__).resolve().parents[2]


def commit(root: Path) -> None:
    for command in (
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-qm",
            "synthetic",
        ],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name in ("config", "src/quanthus", "tests", "scripts"):
        (root / name).mkdir(parents=True, exist_ok=True)
    settings = local.read_json(ROOT / local.PROFILE)
    settings["limits"] = {
        "wall_seconds": 30,
        "rss_bytes": 1024**3,
        "job_and_worktree_bytes": 32 * 1024**2,
        "pytest_tmp_bytes": 16 * 1024**2,
        "minimum_free_bytes": 1024**2,
    }
    (root / local.PROFILE).write_bytes(local.encoded(settings))
    (root / "src/quanthus/__init__.py").write_text("")
    shutil.copyfile(
        ROOT / "src/quanthus/local_acceptance_pytest.py",
        root / "src/quanthus/local_acceptance_pytest.py",
    )
    (root / "Makefile").write_text("check:\n\t$(PYTHON) -m pytest -q\n")
    (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n*.ignored\n*.egg-info/\n")
    (root / "tests/test_sample.py").write_text(
        "import pytest\n"
        "def test_pass(tmp_path):\n"
        "    (tmp_path / 'owned').write_text('synthetic')\n"
        "    assert True\n"
        "@pytest.mark.skip(reason='explicit optional integration')\n"
        "def test_skip(): pass\n"
    )
    commit(root)
    monkeypatch.setattr(
        local,
        "runtime_identity",
        lambda: {
            "python": "synthetic",
            "stdlib": {"path": "/synthetic/stdlib"},
            "site_packages": {"path": "/synthetic/site"},
            "identity": "a",
        },
    )
    return root


def repackage(job: Path) -> None:
    (job / "SHA256SUMS").write_text(
        "".join(
            f"{local.digest(job / name)}  {name}\n"
            for name in local.FILES
            if (job / name).is_file()
        )
    )


def test_real_make_job_and_verify_never_reruns(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = tmp_path / "job"
    result = local.run(repository, job)
    assert result["status"] == "PASS_WITH_SKIPS"
    assert result["tests"]["passed"] == 1 and result["tests"]["skipped"] == 1
    assert result["tests"]["successful_function_TMP_reclaimed"] == 1
    assert not list((job / "tmp/pytest").glob("test_pass*"))
    monkeypatch.setattr(local, "execute", lambda *_: pytest.fail("verify must not execute"))
    verified = local.verify(repository, job)
    assert verified["execution"] == "REUSED_NOT_RERUN"
    assert verified["status"] == "PASS_WITH_SKIPS"


@pytest.mark.parametrize("filename", ["checks.log", "pytest.json", "inputs.json", "receipt.json"])
@pytest.mark.parametrize("damage", ["delete", "corrupt"])
def test_missing_or_corrupt_member_is_rejected(
    repository: Path, tmp_path: Path, filename: str, damage: str
) -> None:
    job = tmp_path / "job"
    local.run(repository, job)
    if damage == "delete":
        (job / filename).unlink()
    else:
        with (job / filename).open("ab") as stream:
            stream.write(b"damaged")
    with pytest.raises(local.AcceptanceError):
        local.verify(repository, job)


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "config",
        "test",
        "dependency",
        "commit",
        "runtime",
        "selection",
        "ignored_source",
        "editable_metadata",
    ],
)
def test_semantic_or_commit_changes_reject_reuse(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    job = tmp_path / "job"
    local.run(repository, job)
    if change == "runtime":
        old = local.runtime_identity()
        monkeypatch.setattr(local, "runtime_identity", lambda: old | {"identity": "b"})
    elif change == "commit":
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=synthetic@example.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "new integration commit",
            ],
            cwd=repository,
            check=True,
        )
    else:
        paths = {
            "source": "src/quanthus/change.py",
            "config": "config/change.json",
            "test": "tests/test_other.py",
            "dependency": "requirements.lock",
            "selection": "pytest.ini",
            "ignored_source": "src/extra.ignored",
            "editable_metadata": "src/quanthus.egg-info/PKG-INFO",
        }
        (repository / paths[change]).parent.mkdir(parents=True, exist_ok=True)
        (repository / paths[change]).write_text("# changed\n")
        if change not in {"ignored_source", "editable_metadata"}:
            commit(repository)
    with pytest.raises(local.AcceptanceError):
        local.verify(repository, job)


def test_failure_evidence_retained_and_cannot_reuse(repository: Path, tmp_path: Path) -> None:
    (repository / "tests/test_sample.py").write_text(
        "def test_fail(tmp_path):\n"
        "    (tmp_path / 'evidence').write_text('retained')\n"
        "    assert False\n"
    )
    commit(repository)
    job = tmp_path / "failed"
    result = local.run(repository, job)
    assert result["status"] == "FAILED" and result["tests"]["failed"] == 1
    assert list((job / "tmp/pytest").rglob("evidence"))
    with pytest.raises(local.AcceptanceError, match="RUN_NOT_PASSED"):
        local.verify(repository, job)


def test_planning_failure_after_pytest_is_not_pass(repository: Path, tmp_path: Path) -> None:
    with (repository / "Makefile").open("a") as stream:
        stream.write("\tfalse\n")
    commit(repository)
    result = local.run(repository, tmp_path / "failed")
    assert result["tests"]["passed"] == 1 and result["status"] == "FAILED"


def test_input_changes_during_run_reject_result(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    execute = local.execute

    def mutate(*args: Any) -> Any:
        result = execute(*args)
        (repository / "src/quanthus/__init__.py").write_text("# mutated\n")
        commit(repository)
        return result

    monkeypatch.setattr(local, "execute", mutate)
    result = local.run(repository, tmp_path / "mutated")
    assert result["status"] == "FAILED" and result["inputs_unchanged"] is False


@pytest.mark.parametrize("mode", ["existing", "inside", "symlink"])
def test_output_preflight_never_modifies_existing_bytes(
    repository: Path, tmp_path: Path, mode: str
) -> None:
    source = tmp_path / "keep"
    source.mkdir()
    payload = source / "original"
    payload.write_bytes(b"unchanged")
    job = source
    if mode == "inside":
        job = repository / "job"
    elif mode == "symlink":
        job = tmp_path / "linked"
        job.symlink_to(source, target_is_directory=True)
    with pytest.raises(local.AcceptanceError):
        local.run(repository, job)
    assert payload.read_bytes() == b"unchanged" and list(source.iterdir()) == [payload]
    assert not (repository / "job").exists()


def test_insufficient_space_fails_before_mkdir(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Space:
        f_bavail = 1
        f_frsize = 1

    monkeypatch.setattr(os, "statvfs", lambda _: Space())
    with pytest.raises(local.AcceptanceError, match="INSUFFICIENT_PREFLIGHT_SPACE"):
        local.run(repository, tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


def test_walk_error_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        kwargs["onerror"](PermissionError("deterministic scan failure"))
        return []

    monkeypatch.setattr(os, "walk", broken)
    with pytest.raises(PermissionError):
        local.tree_identity(tmp_path)


def test_repacked_invalid_skip_status_is_rejected(repository: Path, tmp_path: Path) -> None:
    job = tmp_path / "job"
    local.run(repository, job)
    receipt = local.read_json(job / "receipt.json")
    receipt["status"] = "PASS"
    (job / "receipt.json").write_bytes(local.encoded(receipt))
    repackage(job)
    with pytest.raises(local.AcceptanceError, match="INVALID_SKIP_STATUS"):
        local.verify(repository, job)


def test_receipt_symlink_and_unsafe_mode_rejected(repository: Path, tmp_path: Path) -> None:
    job = tmp_path / "job"
    local.run(repository, job)
    target = job / "checks.log"
    saved = tmp_path / "original-log"
    target.rename(saved)
    target.symlink_to(saved)
    with pytest.raises(local.AcceptanceError, match="MISSING_RECEIPT_MEMBER"):
        local.verify(repository, job)
    target.unlink()
    target.write_bytes(saved.read_bytes())
    job.chmod(0o777)
    with pytest.raises(local.AcceptanceError, match="WRITABLE_RECEIPT_DIRECTORY"):
        local.verify(repository, job)


def test_timeout_preserves_attempt_and_rejects_reuse(repository: Path, tmp_path: Path) -> None:
    settings = local.read_json(repository / local.PROFILE)
    settings["limits"]["wall_seconds"] = 1
    (repository / local.PROFILE).write_bytes(local.encoded(settings))
    (repository / "Makefile").write_text("check:\n\t$(PYTHON) -c 'import time;time.sleep(30)'\n")
    commit(repository)
    job = tmp_path / "timed-out"
    result = local.run(repository, job)
    assert result["result"]["stop_reason"] == "TIME_LIMIT" and result["status"] == "FAILED"
    assert (job / "attempt.json").exists()
    with pytest.raises(local.AcceptanceError):
        local.verify(repository, job)


def test_sigterm_stops_child_and_retains_incomplete_attempt(
    repository: Path, tmp_path: Path
) -> None:
    (repository / "Makefile").write_text(
        "check:\n\t$(PYTHON) -c 'import time,os;from pathlib import Path;"
        'Path(os.environ["QUANTHUS_ACCEPTANCE_JOB"]).joinpath("child.pid").write_text('
        "str(os.getpid()));time.sleep(30)'\n"
    )
    commit(repository)
    job = tmp_path / "interrupted"
    harness = (
        "from pathlib import Path;from quanthus import local_acceptance as l;"
        "l.runtime_identity=lambda:{'stdlib':{'path':'/synthetic/stdlib'},"
        "'site_packages':{'path':'/synthetic/site'}};"
        f"print(l.run(Path({str(repository)!r}),Path({str(job)!r})))"
    )
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
    process = subprocess.Popen(
        [sys.executable, "-c", harness], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 15
        while not (job / "child.pid").exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.05)
        assert (job / "child.pid").exists()
        child_pid = int((job / "child.pid").read_text())
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
        assert local.read_json(job / "receipt.json")["status"] == "INCOMPLETE"
        assert (job / "attempt.json").exists()
        proc = Path(f"/proc/{child_pid}/stat")
        assert not proc.exists() or proc.read_text().rsplit(")", 1)[1].split()[0] == "Z"
        with pytest.raises(local.AcceptanceError):
            local.verify(repository, job)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_environment_does_not_inherit_optional_or_secret_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUANTHUS_TEST_POSTGRES_DSN", "synthetic-private-value")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k only_one_test")
    env = local.environment(tmp_path / "root", tmp_path / "job")
    assert "QUANTHUS_TEST_POSTGRES_DSN" not in env
    assert "only_one_test" not in env["PYTEST_ADDOPTS"]
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_runtime_fingerprint_detects_same_version_code_change(tmp_path: Path) -> None:
    file = tmp_path / "package.py"
    file.write_text("VERSION='same';VALUE=1\n")
    first = local.tree_identity(tmp_path)
    file.write_text("VERSION='same';VALUE=2\n")
    assert local.tree_identity(tmp_path)["sha256"] != first["sha256"]


def test_ignored_cache_does_not_change_identity(repository: Path) -> None:
    before = local.repository_identity(repository)
    cache = repository / "src/quanthus/__pycache__"
    cache.mkdir()
    (cache / "synthetic.pyc").write_bytes(b"unused-cache")
    assert local.repository_identity(repository) == before


def test_linked_source_refused_before_output(repository: Path, tmp_path: Path) -> None:
    (repository / "src/linked.py").symlink_to(repository / "src/quanthus/__init__.py")
    commit(repository)
    with pytest.raises(local.AcceptanceError, match="UNSUPPORTED_GIT_MEMBER"):
        local.run(repository, tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


def test_invalid_manifest_member_refused(repository: Path, tmp_path: Path) -> None:
    job = tmp_path / "job"
    local.run(repository, job)
    with (job / "SHA256SUMS").open("a") as stream:
        stream.write("a" * 64 + "  ../not-in-receipt\n")
    with pytest.raises(local.AcceptanceError, match="INVALID_CHECKSUM_MEMBER"):
        local.verify(repository, job)


def test_assume_unchanged_cannot_hide_source_edit(repository: Path) -> None:
    subprocess.run(
        ["git", "update-index", "--assume-unchanged", "src/quanthus/__init__.py"],
        cwd=repository,
        check=True,
    )
    (repository / "src/quanthus/__init__.py").write_text("# hidden edit\n")
    assert not local.git(repository, "status", "--porcelain")
    with pytest.raises(local.AcceptanceError, match="DIRTY_TRACKED_CONTENT"):
        local.repository_identity(repository)


def test_tmp_retention_never_chmods_hardlinked_file(tmp_path: Path) -> None:
    original = tmp_path / "original"
    original.write_bytes(b"synthetic-kept-source")
    original.chmod(0o400)
    owned = tmp_path / "owned"
    nested = owned / "nested"
    nested.mkdir(parents=True)
    (nested / "linked").hardlink_to(original)
    nested.chmod(0o500)
    telemetry.remove_owned(owned)
    assert original.read_bytes() == b"synthetic-kept-source"
    assert original.stat().st_mode & 0o777 == 0o400
    assert not owned.exists()


@pytest.mark.parametrize("error", [FileNotFoundError("retired TMP"), PermissionError("denied")])
def test_allocation_sampling_only_tolerates_retired_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    def raced(*args: Any, **kwargs: Any) -> Any:
        kwargs["onerror"](error)
        return []

    monkeypatch.setattr(os, "walk", raced)
    if isinstance(error, FileNotFoundError):
        assert local.allocated(tmp_path) == 0
    else:
        with pytest.raises(PermissionError):
            local.allocated(tmp_path)


def test_allocation_includes_directory_blocks(tmp_path: Path) -> None:
    for index in range(32):
        (tmp_path / f"directory-{index}").mkdir()
    (tmp_path / "ordinary-file").write_bytes(b"synthetic")
    expected = sum(p.lstat().st_blocks * 512 for p in [tmp_path, *tmp_path.rglob("*")])
    assert local.allocated(tmp_path) == expected


def test_editable_install_metadata_is_bound(repository: Path) -> None:
    metadata = repository / "src/quanthus.egg-info"
    metadata.mkdir()
    names = {
        "src/quanthus.egg-info/" + name
        for name in (
            "PKG-INFO",
            "SOURCES.txt",
            "dependency_links.txt",
            "entry_points.txt",
            "requires.txt",
            "top_level.txt",
        )
    }
    for name in names:
        (repository / name).write_text("synthetic installation metadata\n")
    before = local.repository_identity(repository)
    assert set(before["editable_metadata"]) == names
    assert not local.git(repository, "status", "--porcelain")
    (metadata / "PKG-INFO").write_text("changed metadata\n")
    assert local.repository_identity(repository) != before


@pytest.mark.parametrize("kind", ["unknown", "linked"])
def test_editable_metadata_rejects_unknown_or_linked_files(repository: Path, kind: str) -> None:
    metadata = repository / "src/quanthus.egg-info"
    metadata.mkdir()
    if kind == "unknown":
        (metadata / "untracked.py").write_text("raise RuntimeError('unexpected code')\n")
        reason = "UNTRACKED_SEMANTIC_INPUT"
    else:
        (metadata / "PKG-INFO").symlink_to(repository / "src/quanthus/__init__.py")
        reason = "LINKED_INSTALL_METADATA"
    with pytest.raises(local.AcceptanceError, match=reason):
        local.repository_identity(repository)


def test_teardown_skip_is_not_counted_as_a_fully_passed_test(
    repository: Path, tmp_path: Path
) -> None:
    (repository / "tests/test_sample.py").write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def late_skip():\n"
        "    yield\n"
        "    pytest.skip('teardown dependency unavailable')\n"
        "def test_skipped(late_skip, tmp_path):\n"
        "    (tmp_path / 'retained').write_text('synthetic')\n"
        "def test_pass(): pass\n"
    )
    commit(repository)
    job = tmp_path / "teardown-skipped"
    result = local.run(repository, job)
    assert result["status"] == "PASS_WITH_SKIPS"
    assert result["tests"]["passed"] == 1
    assert result["tests"]["skipped"] == 1
    assert result["tests"]["collected"] == 2
    assert list((job / "tmp/pytest").rglob("retained"))
