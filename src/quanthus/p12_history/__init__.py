"""P12 bounded history reader (issue #749).

Reads the raw facts the P11 runner needs for the 22/61/120 floors from
the pinned sources - parent Base 2.5.1 (read-only) plus the fixed Daily
core days 2026-11-01..T - and hands them over as a neutral
``p11.raw-history-input.v1`` envelope the P11 strict adapter consumes.

The reader never computes the three floors, never builds a
CandidateDailyInput, never repairs or back-fills data.  Coverage that
is absent or unverifiable is handed over UNKNOWN per member - the rule
decides which securities drop.
"""

from quanthus.p12_history.reader import HistoryResult, read_history
from quanthus.p12_history.source_pins import SourceMap

__all__ = ["HistoryResult", "SourceMap", "read_history"]
