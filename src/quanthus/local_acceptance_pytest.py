"""Fixed local-acceptance pytest telemetry and PASS-only function TMP retention."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

_passed: set[str] = set()
_failed: set[str] = set()
_skipped: dict[str, str] = {}
_errors: set[str] = set()
_reclaimed = 0
_retention_errors: list[str] = []


def pytest_runtest_logreport(report: Any) -> None:
    if report.failed:
        (_failed if report.when == "call" else _errors).add(report.nodeid)
    if report.passed and report.when == "call":
        _passed.add(report.nodeid)
    if report.skipped:
        _skipped[report.nodeid] = str(report.longrepr)


def pytest_collectreport(report: Any) -> None:
    if report.failed:
        _errors.add(report.nodeid)
    elif report.skipped:
        _skipped[report.nodeid] = str(report.longrepr)


def remove_owned(path: Path) -> None:
    def writable(function: Any, name: str, error: Any) -> None:
        target = Path(name)
        if target != path and path not in target.parents:
            raise ValueError("TMP_OUTSIDE_OWNED_ROOT")
        for item in (target.parent, target):
            if (item == path or path in item.parents) and not item.is_symlink():
                if item.stat().st_uid != os.geteuid():
                    raise ValueError("TMP_OWNER_CHANGED")
                item.chmod(item.stat().st_mode | (0o700 if item.is_dir() else 0o600))
        function(name)

    shutil.rmtree(path, onerror=writable)


@pytest.hookimpl(hookwrapper=True)  # type: ignore[untyped-decorator]
def pytest_runtest_makereport(item: Any, call: Any) -> Any:
    global _reclaimed
    outcome = yield
    report = outcome.get_result()
    results = getattr(item, "_acceptance_phases", {})
    results[report.when] = report.passed
    item._acceptance_phases = results
    if report.when == "call":
        fixture = getattr(getattr(item, "_request", None), "_fixture_defs", {}).get("tmp_path")
        if fixture is not None and fixture.scope == "function":
            item._acceptance_tmp = item.funcargs.get("tmp_path")
    if report.when == "teardown" and all(results.get(k) for k in ("setup", "call", "teardown")):
        path = getattr(item, "_acceptance_tmp", None)
        if path is not None and path.exists():
            base = Path(os.environ["QUANTHUS_ACCEPTANCE_JOB"]) / "tmp/pytest"
            if path.is_symlink() or base.resolve() not in path.resolve().parents:
                raise ValueError("UNOWNED_TEST_TMP")
            try:
                remove_owned(path)
                _reclaimed += 1
            except OSError as error:
                _retention_errors.append(type(error).__name__ + ":" + item.nodeid)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    value = {
        "schema_version": "quanthus.local-pytest-result.v1",
        "exit_status": int(exitstatus),
        "collected": session.testscollected,
        "passed": len(_passed - _failed - _errors),
        "failed": len(_failed),
        "errors": len(_errors),
        "skipped": len(_skipped),
        "skip_reasons": _skipped,
        "failure_nodes": sorted(_failed | _errors),
        "successful_function_TMP_reclaimed": _reclaimed,
        "failed_and_shared_TMP_retained": True,
        "retention_errors": _retention_errors,
    }
    path = Path(os.environ["QUANTHUS_ACCEPTANCE_JOB"]) / "pytest.json"
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
