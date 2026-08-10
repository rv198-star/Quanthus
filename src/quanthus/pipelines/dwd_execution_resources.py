"""Bounded execution resources shared by DWD builders and audits."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

DWD_THREAD_LIMIT_ENV = "QUANTHUS_DWD_THREAD_LIMIT"
_ALLOWED_THREAD_LIMITS = {"1", "2", "3", "4"}


class DwdExecutionResourceError(ValueError):
    """Raised when a DWD resource setting is outside its governed bounds."""


def dwd_thread_limit(environment: Mapping[str, str] | None = None) -> int:
    """Return the explicitly bounded DWD thread limit, defaulting to one."""

    source = os.environ if environment is None else environment
    raw = source.get(DWD_THREAD_LIMIT_ENV, "1")
    if raw not in _ALLOWED_THREAD_LIMITS:
        raise DwdExecutionResourceError(
            f"{DWD_THREAD_LIMIT_ENV} must be one of {sorted(_ALLOWED_THREAD_LIMITS)}"
        )
    return int(raw)


def configure_duckdb_threads(
    connection: Any,
    environment: Mapping[str, str] | None = None,
    *,
    maximum: int | None = None,
) -> int:
    """Apply and return the governed thread limit on one DuckDB connection."""

    thread_limit = dwd_thread_limit(environment)
    if maximum is not None:
        if isinstance(maximum, bool) or maximum < 1 or maximum > 4:
            raise DwdExecutionResourceError("DuckDB thread maximum must be between 1 and 4")
        thread_limit = min(thread_limit, maximum)
    connection.execute(f"SET threads={thread_limit}")
    return thread_limit


def dwd_duckdb_memory_limit_mb(
    per_thread_mb: int,
    environment: Mapping[str, str] | None = None,
    *,
    minimum_mb: int | None = None,
) -> int:
    """Scale a bounded DuckDB memory allowance with the governed worker count."""

    if isinstance(per_thread_mb, bool) or per_thread_mb < 1 or per_thread_mb > 2048:
        raise DwdExecutionResourceError("DuckDB per-thread memory must be between 1 and 2048 MB")
    if minimum_mb is not None and (
        isinstance(minimum_mb, bool) or minimum_mb < 1 or minimum_mb > 8192
    ):
        raise DwdExecutionResourceError("DuckDB minimum memory must be between 1 and 8192 MB")
    scaled = per_thread_mb * dwd_thread_limit(environment)
    return max(scaled, minimum_mb or 0)
