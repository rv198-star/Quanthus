"""Read a pinned Base 2.4 table closure through CN01's existing native reader.

This verifies selected physical references for a job, not the full image or Daily
admission. Callers reuse the returned descriptor inside their job. Historical
source_root strings are archival; this reader never dereferences them.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .base24_delivery import INTERVALS, ONE_MINUTE_DISPOSITION, PURPOSE
from .base24_image import KIND
from .native_base23_reader import (
    PARENT_TABLES,
    NativeBase23,
    NativeBase23Error,
    require_daily_core_parent_admission,
)
from .native_base23_reader import _read_native_tables as _read

NativeBase24Error = NativeBase23Error
NativeBase24 = NativeBase23


def read_native_tables(
    root: Path, config: Mapping[str, Any], tables: tuple[str, ...]
) -> NativeBase24:
    """Verify exact 2.4 metadata and only the explicit, independent table files."""
    if config.get("through_date") != "2026-09-30":
        raise NativeBase24Error("native Base 2.4 business cutoff differs")
    return _read(
        root,
        config,
        tables,
        profile=(KIND, "2.4", PURPOSE, "native_base24"),
        independent_files=True,
        default_intervals=INTERVALS,
        one_minute_disposition=ONE_MINUTE_DISPOSITION,
    )


def read_native_parent_references(root: Path, config: Mapping[str, Any]) -> NativeBase24:
    """Verify once the five parent tables required by the existing Daily engine."""
    return read_native_tables(root, config, PARENT_TABLES)


def require_daily_core_governance(config: Mapping[str, Any]) -> None:
    """Admit only an explicit isolated 1d job; this never enables execution."""
    if (
        config.get("schema_version") != "quanthus.base24-daily-governance.v1"
        or config.get("purpose") != "isolated_daily_governance"
        or config.get("consumer_admission") != "NOT_GRANTED"
        or config.get("execution_authorized") is not False
    ):
        raise NativeBase24Error("native Base 2.4 Daily governance contract differs")
    require_daily_core_parent_admission(config)
