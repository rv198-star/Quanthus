"""Cumulative retained-job admission, including failed attempts and control bytes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .base22_bounded import BoundedError, Limits, storage_census
from .base22_sources import disjoint_output


def job_capacity(
    root: Path, sources: list[Path], limits: Limits, *, reserve: int = 0
) -> dict[str, int]:
    """A retained job consumes its allowance; recovery reserves only the remainder."""
    root = disjoint_output(root, sources)
    if root.exists() and not root.is_dir():
        raise BoundedError("BUILD_JOB_DIRECTORY_REQUIRED")
    usage = (
        storage_census(root)
        if root.exists()
        else {"logical_bytes": 0, "allocated_bytes": 0, "files": 0}
    )
    remaining = limits.allocated_bytes - usage["allocated_bytes"]
    if reserve < 0 or remaining < reserve:
        raise BoundedError("BUILD_JOB_RETAINED_ALLOCATION_LIMIT_EXCEEDED")
    return {**usage, "remaining_allocated_bytes": remaining}


def remaining_stage_limits(root: Path, sources: list[Path], limits: Limits) -> Limits:
    usage = job_capacity(root, sources, limits)
    # Keep room for immutable inventories, failure receipts and events. The exact
    # completed receipt is independently admitted before StageRunner publishes it.
    control_reserve = min(128 * 1024**2, limits.allocated_bytes // 8)
    remaining = usage["remaining_allocated_bytes"] - control_reserve
    if remaining <= 0:
        raise BoundedError("BUILD_JOB_NO_CAPACITY_FOR_NEXT_STAGE")
    return replace(limits, allocated_bytes=remaining, temp_bytes=min(limits.temp_bytes, remaining))
