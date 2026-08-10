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
) -> int:
    """Apply and return the governed thread limit on one DuckDB connection."""

    thread_limit = dwd_thread_limit(environment)
    connection.execute(f"SET threads={thread_limit}")
    return thread_limit
