"""Synthetic regressions for invalid native originals and processing-only resume."""

from __future__ import annotations

import gzip
import json
import os
import runpy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from quanthus.pipelines import p10_complete_daily_cycle as cycle
from quanthus.pipelines import p10_native_minute_delivery as minute
from quanthus.pipelines.p10_delivery_common import atomic, digest, encoded
from tests.contract.test_p10_complete_daily import (
    DAY,
    REVISION,
    capture,
    raw_m1,
    raw_m5,
    synthetic_reference,
)


def invalid_m5() -> bytes:
    value = json.loads(raw_m5())
    value["rows"][0][2] = "sz.999999"
    return encoded(value)


def test_valid_target_repair_of_invalid_original_is_not_parser_failure(tmp_path: Path) -> None:
    ref = synthetic_reference(tmp_path)
    old = capture(tmp_path / "old", "5m", invalid_m5())
    new = capture(tmp_path / "new", "5m", raw_m5())
    original = (old / "shard-000/raw/000001.json.gz").read_bytes()
    result = minute.build(
        reference_file=ref,
        capture_root=new,
        prior_capture_root=old,
        interval="5m",
        product_root=tmp_path / "products",
        code_revision=REVISION,
    )
    assert result["research_ready"]
    assert result["quality_grades"] == {"R2": 1, "SUSPENDED": 4991}
    assert (old / "shard-000/raw/000001.json.gz").read_bytes() == original
    admitted = Path(result["package_path"]) / "ods/raw/000001.json.gz"
    assert gzip.decompress(admitted.read_bytes()) == raw_m5()


@pytest.mark.parametrize(
    "before,after,changed",
    [
        (invalid_m5(), raw_m5(), True),
        (raw_m5(), invalid_m5(), False),
        (invalid_m5(), invalid_m5(), False),
        (raw_m5(), raw_m5(), False),
    ],
)
def test_invalid_new_source_never_replaces_valid_or_triggers_repoll(
    before: bytes,
    after: bytes,
    changed: bool,
) -> None:
    assert minute.target_source_changed(before, after, "000001", DAY, "5m") is changed


def test_empty_new_source_does_not_replace_real_old_observation() -> None:
    obj = json.loads(raw_m5())
    obj["rows"] = []
    assert not minute.target_source_changed(raw_m5(), encoded(obj), "000001", DAY, "5m")
    m1 = json.loads(raw_m1())
    m1["outside_target"] = "rolling_vendor_metadata"
    assert not minute.target_source_changed(raw_m1(), encoded(m1), "000001", DAY, "1m")


def test_repair_cannot_hide_tampered_prior_raw_sha(tmp_path: Path) -> None:
    ref = synthetic_reference(tmp_path)
    old = capture(tmp_path / "old", "5m", invalid_m5())
    new = capture(tmp_path / "new", "5m", raw_m5())
    (old / "shard-000/raw/000001.json.gz").write_bytes(gzip.compress(b"{}"))
    with pytest.raises(ValueError, match="original SHA"):
        minute.build(
            reference_file=ref,
            capture_root=new,
            prior_capture_root=old,
            interval="5m",
            product_root=tmp_path / "products",
            code_revision=REVISION,
        )


@pytest.mark.parametrize("capture_authorized", [True, False])
def test_failed_repair_grade_resumes_existing_capture_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_authorized: bool,
) -> None:
    job = tmp_path / "job"
    journal = job / "5m"
    journal.mkdir(parents=True)
    atomic(
        journal / f"{DAY}-T_plus_1.json",
        {
            "operations": [
                {"operation_id": "repair-grade", "exit_code": 1, "outcome": "failed"},
            ],
            "cumulative_processing_seconds": 1960.5,
        },
    )
    ledger_before = (journal / f"{DAY}-T_plus_1.json").read_bytes()
    monkeypatch.setattr(cycle, "reference", lambda _: {"securities": {}})
    monkeypatch.setattr(cycle, "repair_capture_digest", lambda _: "sha256:" + "a" * 64)
    old = tmp_path / "old"
    new = tmp_path / "new"
    monkeypatch.setattr(cycle, "_package", Mock(side_effect=[old, new]))
    monkeypatch.setattr(
        cycle,
        "verify",
        Mock(
            side_effect=[
                {"research_ready": False},
                {"research_ready": True, "package_path": str(new)},
            ]
        ),
    )
    operation = Mock(return_value={"status": "success", "receipt": {"exit_code": 0}})
    monkeypatch.setattr(cycle, "_operation", operation)
    result = cycle.run_interval(
        interval="5m",
        target=cycle.date.fromisoformat(DAY),
        job=job,
        capture=tmp_path / "capture",
        product=tmp_path / "products",
        revision=REVISION,
        capture_authorized=capture_authorized,
    )
    assert result["research_ready"]
    kwargs = operation.call_args.kwargs
    assert kwargs["operation"] == "repair-grade-retry-001"
    assert "build" in kwargs["command"] and "capture" not in kwargs["command"]
    assert "--prior-capture-root" in kwargs["command"]
    assert (journal / f"{DAY}-T_plus_1.json").read_bytes() == ledger_before


def test_failed_grading_retry_limit_never_restarts_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cycle, "_operation", Mock())
    result = cycle.retry_existing_repair_grade(
        journal=tmp_path,
        calendar=tmp_path / "calendar",
        target=cycle.date.fromisoformat(DAY),
        ref_file=tmp_path / "reference",
        root=tmp_path / "repair",
        original=tmp_path / "original",
        product=tmp_path / "5m",
        revision=REVISION,
        attempts=3,
    )
    assert result["status"] == "repair_grade_retry_limit"
    assert not cycle._operation.called


def resume_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    job = tmp_path / "job"
    job.mkdir()
    synthetic_reference(job).rename(job / "daily-reference.json")
    atomic(job / "code.json", {"code_revision": REVISION, "policy_id": cycle.POLICY_ID})
    captures = tmp_path / "capture"
    root = capture(captures / DAY / "5m", "repair-v1", raw_m5())
    old = capture(captures / DAY / "5m", "initial-v1", invalid_m5())
    failed = {"operation_id": "repair-grade", "exit_code": 1, "outcome": "failed"}
    atomic(
        job / "5m" / f"{DAY}-T_plus_1.json",
        {
            "operations": [failed],
            "market_date": DAY,
            "phase": "T_plus_1",
            "policy_id": cycle.POLICY_ID,
            "inflight_operation": None,
            "cumulative_processing_seconds": 1960.5,
        },
    )
    revision = "d" * 40
    obj = {
        "kind": "p10-complete-daily-processing-resume-v1",
        "market_date": DAY,
        "from_code_revision": REVISION,
        "to_code_revision": revision,
        "policy_id": cycle.POLICY_ID,
        "interval": "5m",
        "failed_operation_digest": digest(encoded(failed)),
        "original_capture_digest": cycle.repair_capture_digest(old),
        "reference_digest": minute.reference(job / "daily-reference.json")["reference_digest"],
        "repair_capture_digest": cycle.repair_capture_digest(root),
        "processing_only": True,
    }
    authorization = tmp_path / "resume.json"
    atomic(authorization, obj)
    monkeypatch.setenv("QUANTHUS_COMPLETE_DAILY_RESUME_AUTHORIZATION", str(authorization))
    return job, captures, revision


def test_explicit_code_resume_keeps_original_identity_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, captures, revision = resume_setup(tmp_path, monkeypatch)
    old = (job / "code.json").read_bytes()
    assert cycle.reconcile_job_code(job, captures, cycle.date.fromisoformat(DAY), revision)
    assert cycle.reconcile_job_code(job, captures, cycle.date.fromisoformat(DAY), revision)
    assert (job / "code.json").read_bytes() == old
    receipt = json.loads((job / "code-resumes" / f"{revision}.json").read_text())
    assert receipt["processing_only"] and receipt["recorded_at"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("market_date", "2026-10-09"),
        ("reference_digest", "sha256:" + "f" * 64),
        ("processing_only", False),
    ],
)
def test_resume_rejects_wrong_identity_or_source_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    job, captures, revision = resume_setup(tmp_path, monkeypatch)
    path = Path(os.environ["QUANTHUS_COMPLETE_DAILY_RESUME_AUTHORIZATION"])
    obj = json.loads(path.read_text())
    obj[field] = value
    atomic(path, obj)
    with pytest.raises(ValueError, match="resume identity"):
        cycle.reconcile_job_code(job, captures, cycle.date.fromisoformat(DAY), revision)
    assert not (job / "code-resumes").exists()


def test_pinned_entrypoint_propagates_release_path_to_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    monkeypatch.setenv("PYTHONPATH", "/stale/daily-core/src:/baostock-dependency")
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setattr(sys, "argv", ["synthetic-entrypoint"])
    monkeypatch.setattr(cycle, "main", lambda: 0)
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda args, **kwargs: REVISION + "\n" if "rev-parse" in args else b"",
    )
    root = Path(__file__).resolve().parents[2]
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(root / "ops/bin/run-complete-daily-cycle"), run_name="__main__")
    assert result.value.code == 0
    assert os.environ["PYTHONPATH"].split(os.pathsep)[0] == str(root / "src")
    assert "/baostock-dependency" in os.environ["PYTHONPATH"]
