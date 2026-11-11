"""Explicit fixed-source clock for the 2020-2021 capital counterfactual only."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

VERSION = "quanthus.capital-retrospective-clock.v1"


@dataclass(frozen=True)
class CapitalReplayClock:
    market_date: date
    knowledge_cutoff: datetime
    data_content_digest: str

    def __post_init__(self) -> None:
        if not date(2020, 1, 1) <= self.market_date <= date(2021, 12, 31):
            raise ValueError("capital clock permits only the frozen 2020-2021 scope")
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise ValueError("capital source cutoff must be aware")
        if self.knowledge_cutoff <= self.simulated_close:
            raise ValueError("capital source cutoff must follow simulated business time")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.data_content_digest):
            raise ValueError("capital source content identity required")

    @property
    def simulated_close(self) -> datetime:
        return datetime.combine(self.market_date, time(15), ZoneInfo("Asia/Shanghai"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": VERSION,
            "request_id": "RESEARCH-CAPITAL-001",
            "market_date": str(self.market_date),
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "simulated_close": self.simulated_close.isoformat(),
            "data_content_digest": self.data_content_digest,
            "historical_pit_claim": False,
            "actual_execution_authorized": False,
            "use": "fixed latest-sealed capital counterfactual with native source clocks",
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> CapitalReplayClock:
        try:
            raw = json.loads(text)
            clock = cls(
                date.fromisoformat(raw["market_date"]),
                datetime.fromisoformat(raw["knowledge_cutoff"]),
                raw["data_content_digest"],
            )
            flags = ("historical_pit_claim", "actual_execution_authorized")
            if raw != clock.to_dict() or any(type(raw[k]) is not bool for k in flags):
                raise ValueError("capital clock identity or authority differs")
            return clock
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError("invalid capital counterfactual clock") from exc


def bind_capital_action_context(action_json: str | None, parent_json: str | None) -> None:
    """An action cannot introduce this context into a default historical run."""
    if action_json is None:
        return
    if parent_json is None:
        raise ValueError("capital action requires matching parent capital context")
    action = CapitalReplayClock.from_json(action_json)
    parent = CapitalReplayClock.from_json(parent_json)
    if (
        action.knowledge_cutoff != parent.knowledge_cutoff
        or action.data_content_digest != parent.data_content_digest
    ):
        raise ValueError("capital action requires matching parent capital context")
