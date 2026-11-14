"""Explicit fixed-source clock for the 2020-2021 capital counterfactual only."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

VERSION = "quanthus.capital-retrospective-clock.v1"
RECOVERY_VERSION = "quanthus.capital-retrospective-clock.v2"
CONTROLLED_VERSION = "quanthus.r10-controlled-clock.v1"


@dataclass(frozen=True)
class CapitalReplayClock:
    market_date: date
    knowledge_cutoff: datetime
    data_content_digest: str
    request_id: str = "RESEARCH-CAPITAL-001"
    task_id: str | None = None
    attempt_id: str | None = None
    input_identity_json: str | None = None

    def __post_init__(self) -> None:
        if not date(2020, 1, 1) <= self.market_date <= date(2021, 12, 31):
            raise ValueError("capital clock permits only the frozen 2020-2021 scope")
        if self.knowledge_cutoff.tzinfo is None or self.knowledge_cutoff.utcoffset() is None:
            raise ValueError("capital source cutoff must be aware")
        if self.knowledge_cutoff <= self.simulated_close:
            raise ValueError("capital source cutoff must follow simulated business time")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.data_content_digest):
            raise ValueError("capital source content identity required")
        if self.input_identity_json is None:
            if (
                self.request_id != "RESEARCH-CAPITAL-001"
                or self.task_id is not None
                or self.attempt_id is not None
            ):
                raise ValueError("legacy capital identity is fixed")
        else:
            controlled = self.request_id == "RESEARCH-R10-001"
            if not controlled and not re.fullmatch(r"RESEARCH-CAPITAL-\d{3}", self.request_id):
                raise ValueError("recovery request identity required")
            if not re.fullmatch(
                re.escape(self.request_id) + r"-T[1-9]\d*", self.task_id or ""
            ) or not re.fullmatch(r"A[1-9]\d*", self.attempt_id or ""):
                raise ValueError("recovery task/attempt identity required")
            raw = json.loads(self.input_identity_json)
            if set(raw) != {
                "native_content_digest",
                "purpose_sha256",
                "consumer_closure_sha256",
                "native_knowledge_cutoff",
                "purpose_qualified_at",
            }:
                raise ValueError("exact recovery input identity required")
            for name in ("native_content_digest", "purpose_sha256", "consumer_closure_sha256"):
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", raw[name]):
                    raise ValueError("complete recovery input identity required")
            for name in ("native_knowledge_cutoff", "purpose_qualified_at"):
                clock = datetime.fromisoformat(raw[name])
                if clock.tzinfo is None or (
                    clock > self.knowledge_cutoff
                    and not (controlled and name == "purpose_qualified_at")
                ):
                    raise ValueError("recovery material knowledge after cutoff")
            if controlled and (
                raw["native_content_digest"] != self.data_content_digest
                or datetime.fromisoformat(raw["native_knowledge_cutoff"]) != self.knowledge_cutoff
                or raw["purpose_sha256"] != raw["consumer_closure_sha256"]
            ):
                raise ValueError("controlled source or purpose binding differs")

    def validate_run(self, run_id: str) -> None:
        if self.input_identity_json is not None and not run_id.startswith(
            str(self.task_id) + "-" + str(self.attempt_id) + "-"
        ):
            raise ValueError("capital run task/attempt differs from its clock")

    @property
    def simulated_close(self) -> datetime:
        return datetime.combine(self.market_date, time(15), ZoneInfo("Asia/Shanghai"))

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": (
                CONTROLLED_VERSION
                if self.request_id == "RESEARCH-R10-001"
                else RECOVERY_VERSION
                if self.input_identity_json is not None
                else VERSION
            ),
            "request_id": self.request_id,
            "market_date": str(self.market_date),
            "knowledge_cutoff": self.knowledge_cutoff.isoformat(),
            "simulated_close": self.simulated_close.isoformat(),
            "data_content_digest": self.data_content_digest,
            "historical_pit_claim": False,
            "actual_execution_authorized": False,
            "use": "fixed latest-sealed capital counterfactual with native source clocks",
        }
        if self.input_identity_json is not None:
            value.update(
                task_id=self.task_id,
                attempt_id=self.attempt_id,
                input_identity=json.loads(self.input_identity_json),
            )
        return value

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> CapitalReplayClock:
        try:
            raw = json.loads(text)
            recovery = raw.get("schema_version") in {RECOVERY_VERSION, CONTROLLED_VERSION}
            clock = cls(
                date.fromisoformat(raw["market_date"]),
                datetime.fromisoformat(raw["knowledge_cutoff"]),
                raw["data_content_digest"],
                raw["request_id"],
                raw.get("task_id") if recovery else None,
                raw.get("attempt_id") if recovery else None,
                json.dumps(raw["input_identity"], sort_keys=True, separators=(",", ":"))
                if recovery
                else None,
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
        or action.request_id != parent.request_id
        or action.task_id != parent.task_id
        or action.attempt_id != parent.attempt_id
        or action.input_identity_json != parent.input_identity_json
    ):
        raise ValueError("capital action requires matching parent capital context")
