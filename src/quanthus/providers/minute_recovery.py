"""Opt-in, single-attempt recovery policy; never admits cross-attempt reuse."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path


class MinuteBudgetExceeded(RuntimeError):
    """The shared cooperative attempt deadline has expired."""


@dataclass(frozen=True)
class MinuteRecoveryPolicy:
    policy_id: str
    sha256: str
    retry_rounds: int
    retry_delay_seconds: float
    budget_seconds: float


def load_minute_recovery_policy(path: Path) -> MinuteRecoveryPolicy:
    raw = path.read_bytes()
    data = json.loads(raw)
    expected = {
        "schema_version",
        "policy_id",
        "activation",
        "frequency",
        "mode",
        "retry_rounds",
        "retry_delay_seconds",
        "budget_seconds",
    }
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("minute recovery policy fields do not match v1")
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["policy_id"] != "minute-attempt-recovery-v1"
        or data["activation"] != "explicit_opt_in"
        or data["frequency"] != "1m"
        or data["mode"] != "incr"
    ):
        raise ValueError("minute recovery v1 requires explicit opt-in 1m incr")
    rounds = data["retry_rounds"]
    if type(rounds) is not int or not 0 <= rounds <= 3:
        raise ValueError("retry_rounds must be an integer in [0, 3]")
    for key, lower, upper in (
        ("retry_delay_seconds", 0, 60),
        ("budget_seconds", 0, 14400),
    ):
        value = data[key]
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not lower <= value <= upper
            or (key == "budget_seconds" and value == 0)
        ):
            raise ValueError(f"invalid {key} in minute recovery policy")
    return MinuteRecoveryPolicy(
        policy_id=data["policy_id"],
        sha256=hashlib.sha256(raw).hexdigest(),
        retry_rounds=rounds,
        retry_delay_seconds=float(data["retry_delay_seconds"]),
        budget_seconds=float(data["budget_seconds"]),
    )


@dataclass(frozen=True)
class MinuteBudget:
    expires_at: float

    @classmethod
    def start(cls, seconds: float) -> MinuteBudget:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("minute budget must be finite and positive")
        return cls(time.monotonic() + seconds)

    def remaining(self) -> float:
        remaining = self.expires_at - time.monotonic()
        if remaining <= 0:
            raise MinuteBudgetExceeded("minute_attempt_budget_exhausted")
        return remaining

    def sleep(self, seconds: float) -> None:
        # Includes provider backoff and pacing; no round creates a new budget.
        time.sleep(min(seconds, self.remaining()))
        self.remaining()
