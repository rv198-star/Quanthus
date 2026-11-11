"""Pure P10 1m operation-admission cutoff for a future isolated T/T+1 controller.

The sixty-minute budget decides whether a NEW bounded atomic operation may
start. It is NOT a kill switch for an already accepted operation. This pure
helper has no scheduling, network or publication side effects; an eventual
runtime must persist cumulative seconds/operation identity and hold a lock.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

POLICY_PATH = (
    Path(__file__).resolve().parents[3]
    / "config/pipelines/minute-daily-tplus1-budget.v2.json"
)
PHASES = ("T", "T_plus_1")


@dataclass(frozen=True)
class OperationAdmission:
    phase: str
    elapsed_before_start: float
    cutoff_seconds: float
    operation_timeout_seconds: float

    def close(self, *, duration_seconds: float) -> dict[str, float | bool]:
        """Account an admitted operation's natural completion, even after cutoff."""
        _finite_nonnegative(duration_seconds, "operation duration")
        if duration_seconds > self.operation_timeout_seconds:
            raise ValueError("operation exceeded its own scoped timeout")
        total = self.elapsed_before_start + duration_seconds
        return {
            "phase_consumed_seconds": total,
            "operation_completed_after_cutoff": total >= self.cutoff_seconds,
            "post_cutoff_drain_seconds": max(
                0.0,
                total - max(self.elapsed_before_start, self.cutoff_seconds),
            ),
        }


def _finite_nonnegative(value: float, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"invalid {label}")


def new_operation(
    *,
    phase: str,
    cumulative_phase_seconds: float,
    operation_timeout_seconds: float,
    another_operation_running: bool = False,
    policy_path: Path = POLICY_PATH,
) -> OperationAdmission | None:
    """None means no new operation. Caller may still finalize a previously admitted one.

    The caller supplies the persisted across-restart elapsed execution seconds
    of THIS market-date/phase (including prior operations and bounded tails).
    No reservations or queued operations can bypass this admission check.
    """
    if phase not in PHASES:
        raise ValueError("unsupported trading-session phase")
    _finite_nonnegative(cumulative_phase_seconds, "cumulative phase seconds")
    _finite_nonnegative(operation_timeout_seconds, "atomic operation timeout")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if (
        policy.get("schema_version") != 2
        or policy.get("policy_id") != "p10-1m-daily-and-tplus1-bounded-ops-v2"
    ):
        raise ValueError("operation admission is bound to P10 v2 quality-time policy")
    cutoff = policy["per_target_market_date"][phase]["new_operation_admission_cutoff_seconds"]
    maximum = policy["atomic_operation"]["max_operation_timeout_seconds"]
    if operation_timeout_seconds <= 0 or operation_timeout_seconds > maximum:
        raise ValueError("operation must be bounded by its independent timeout")
    if another_operation_running or cumulative_phase_seconds >= cutoff:
        return None
    return OperationAdmission(
        phase=phase,
        elapsed_before_start=float(cumulative_phase_seconds),
        cutoff_seconds=float(cutoff),
        operation_timeout_seconds=float(operation_timeout_seconds),
    )
