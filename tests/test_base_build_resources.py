from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

from quanthus.pipelines.securities_warehouse.base22_bounded import BoundedError, Limits
from quanthus.pipelines.securities_warehouse.base22_sources import SourceAssessmentError
from quanthus.pipelines.securities_warehouse.base_build_resources import (
    job_capacity,
    remaining_stage_limits,
)


def test_retained_failed_attempts_count_against_global_next_stage_allowance(tmp_path):
    root = tmp_path / "job"
    failed = root / "stages/ods/attempt-000001/output"
    failed.mkdir(parents=True)
    (failed / "partial.bin").write_bytes(b"x" * 16384)
    limits = Limits(allocated_bytes=1024**2, free_floor_bytes=1)
    usage = job_capacity(root, [], limits)
    assert usage["allocated_bytes"] >= 16384
    assert usage["remaining_allocated_bytes"] == limits.allocated_bytes - usage["allocated_bytes"]
    stage = remaining_stage_limits(root, [], limits)
    assert stage.allocated_bytes == usage["remaining_allocated_bytes"] - limits.allocated_bytes // 8
    with pytest.raises(BoundedError, match="RETAINED_ALLOCATION_LIMIT_EXCEEDED"):
        job_capacity(root, [], replace(limits, allocated_bytes=4096))
    assert (failed / "partial.bin").read_bytes() == b"x" * 16384


def test_preflight_reserves_remaining_capacity_and_rejects_overlapping_job(tmp_path, monkeypatch):
    from quanthus.pipelines.securities_warehouse import base_build as entry

    root = tmp_path / "job"
    root.mkdir()
    (root / "retained.bin").write_bytes(b"x" * 32768)
    limits = Limits(allocated_bytes=65536, free_floor_bytes=1024)
    remaining = job_capacity(root, [], limits)["remaining_allocated_bytes"]
    fs = SimpleNamespace(f_bavail=remaining + 1024, f_frsize=1, f_favail=10000, f_flag=os.ST_RDONLY)
    monkeypatch.setattr(entry.socket, "if_nameindex", lambda: [(1, "lo")])
    monkeypatch.setattr(entry.os, "statvfs", lambda path: fs)
    entry._preflight_paths({}, root, limits, 0)
    fs.f_bavail -= 1
    with pytest.raises(BoundedError, match="SPACE_RESERVE_INSUFFICIENT"):
        entry._preflight_paths({}, root, limits, 0)
    with pytest.raises(SourceAssessmentError, match="SOURCE_OUTPUT_OVERLAP"):
        entry._preflight_paths({"original": str(tmp_path)}, root, limits, 0)


def test_changed_symlink_never_makes_a_retained_job_look_empty(tmp_path):
    root = tmp_path / "job"
    root.mkdir()
    (root / "unowned").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(BoundedError, match="SYMLINK_FORBIDDEN"):
        job_capacity(root, [], Limits())


def test_stage_runner_cannot_publish_success_above_the_total_retained_job_cap(tmp_path):
    from quanthus.pipelines.securities_warehouse.base_build_stages import Stage, StageRunner
    from tests.test_base_build_stages import binding

    root = tmp_path / "job"
    old = root / "stages/ods/attempt-000001/output"
    old.mkdir(parents=True)
    (old / "partial.bin").write_bytes(b"x" * 32768)

    def writer(output, dependencies):
        output.mkdir()
        (output / "new.bin").write_bytes(b"y" * 32768)
        return {"synthetic": True}

    limits = Limits(allocated_bytes=65536, free_floor_bytes=1)
    runner = StageRunner(
        root=root,
        sources=[],
        binding=binding(),
        limits=limits,
        stages=[Stage("ods", (), writer, lambda output, result: None)],
    )
    with pytest.raises(BoundedError, match="RETAINED_ALLOCATION_LIMIT_EXCEEDED"):
        runner.execute()
    failed = root / "stages/ods/attempt-000002"
    assert (failed / "FAILURE.json").exists()
    assert not (failed / "SUCCESS.json").exists()
    assert (old / "partial.bin").exists() and (failed / "output/new.bin").exists()
