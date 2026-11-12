"""Two clocks for counterfactual mechanism tests, never historical PIT evidence.

The knowledge clock is the actual later snapshot. Only the simulation clock
visits past market sessions. Neither source timestamps nor official gates move.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from quanthus.backtest.capital_clock import CapitalReplayClock

VERSION = "quanthus.p6-fixed-snapshot-clock.v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class SnapshotReplayClock:
    market_date: date
    knowledge_cutoff: datetime

    def __post_init__(self) -> None:
        if not date(2026, 1, 1) <= self.market_date <= date(2026, 12, 31):
            raise ValueError("snapshot mechanism clock permits only the authorized 2026 scope")
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise ValueError("snapshot mechanism knowledge clock must be timezone-aware")
        if self.knowledge_cutoff <= self.simulated_close:
            raise ValueError("fixed snapshot must actually follow its simulated market session")

    @property
    def simulated_close(self) -> datetime:
        return datetime.combine(self.market_date, time(15), SHANGHAI)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": VERSION,
            "market_date": str(self.market_date),
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "simulated_close": self.simulated_close.isoformat(),
            "session_clock_basis": "versioned_regular_session_simulation_not_source_evidence",
            "historical_pit_claim": False,
            "economic_validation_claim": False,
            "prospective_observation": False,
            "actual_execution_authorized": False,
        }

    @classmethod
    def from_json(cls, value: str) -> SnapshotReplayClock:
        try:
            raw = json.loads(value)
            clock = cls(
                date.fromisoformat(raw["market_date"]),
                datetime.fromisoformat(raw["knowledge_cutoff"]),
            )
            flags = (
                "historical_pit_claim",
                "economic_validation_claim",
                "prospective_observation",
                "actual_execution_authorized",
            )
            if raw != clock.to_dict() or any(type(raw[name]) is not bool for name in flags):
                raise ValueError("snapshot mechanism clock fields or authority differ")
            return clock
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError("invalid snapshot mechanism clock") from exc

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def parse_replay_clock(value: str) -> SnapshotReplayClock | CapitalReplayClock:
    """Dispatch exact versioned research contexts; legacy scope stays unchanged."""
    from quanthus.backtest.capital_clock import RECOVERY_VERSION, CapitalReplayClock
    from quanthus.backtest.capital_clock import VERSION as CAPITAL_VERSION

    raw = json.loads(value)
    if isinstance(raw, dict) and raw.get("schema_version") in {CAPITAL_VERSION, RECOVERY_VERSION}:
        return CapitalReplayClock.from_json(value)
    return SnapshotReplayClock.from_json(value)
