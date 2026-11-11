"""Reference snapshots for Daily projection; retained knowledge versions stay immutable."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path


def reference_relation(paths: Sequence[Path], as_of: datetime | None = None) -> str:
    """Select only the reference version actually known at the source artifact cutoff."""
    literals = ",".join("'" + str(p).replace("'", "''") + "'" for p in paths)
    source = f"read_parquet([{literals}])"
    if as_of is None:
        return source
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("DAILY_REFERENCE_CUTOFF_TIMEZONE_REQUIRED")
    cutoff = "'" + as_of.isoformat() + "'::TIMESTAMPTZ"
    return (
        f"(SELECT * FROM {source} WHERE knowledge_from <= {cutoff}"
        f" AND (knowledge_to IS NULL OR {cutoff} < knowledge_to))"
    )
